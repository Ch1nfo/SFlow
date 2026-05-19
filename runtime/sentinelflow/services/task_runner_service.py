from __future__ import annotations

from pathlib import Path
from typing import Any

from sentinelflow.agent.service import SentinelFlowAgentService
from sentinelflow.services.audit_service import AuditService
from sentinelflow.agent.run_log_tracer import activate_run_log_tracer, deactivate_run_log_tracer
from sentinelflow.services.agent_run_log_service import AgentRunLogService, RunLogRef
from sentinelflow.services.dispatch_service import AlertDispatchService
from sentinelflow.workflows.agent_workflow_registry import load_agent_workflow
from sentinelflow.workflows.agent_workflow_runner import SentinelFlowAgentWorkflowRunner


class AlertTaskRunnerService:
    def __init__(
        self,
        dispatch_service: AlertDispatchService,
        audit_service: AuditService,
        agent_service: SentinelFlowAgentService,
        agent_workflow_runner: SentinelFlowAgentWorkflowRunner,
        workflow_root: Path,
        run_log_service: AgentRunLogService | None = None,
    ) -> None:
        self.dispatch_service = dispatch_service
        self.audit_service = audit_service
        self.agent_service = agent_service
        self.agent_workflow_runner = agent_workflow_runner
        self.workflow_root = workflow_root
        self.run_log_service = run_log_service

    def _log_event(self, run_log_ref: RunLogRef | dict[str, Any] | None, phase: str, title: str, data: Any, *, level: str = "info") -> None:
        if self.run_log_service is None:
            return
        self.run_log_service.append(run_log_ref, phase, title, data, level=level)

    def _record_agent_result(self, run_log_ref: RunLogRef | dict[str, Any] | None, agent_result: dict[str, Any]) -> None:
        if self.run_log_service is None:
            return
        self.run_log_service.record_agent_result(run_log_ref, agent_result)

    def resolve_run_log_ref(self, task) -> RunLogRef | None:
        if self.run_log_service is None or task is None:
            return None
        alert_data: dict[str, Any] = {}
        if isinstance(task.payload, dict):
            payload_alert = task.payload.get("alert_data")
            if isinstance(payload_alert, dict):
                alert_data = payload_alert
        try:
            return self.run_log_service.derive_ref(
                task_id=task.task_id,
                event_ids=task.event_ids or str(alert_data.get("eventIds") or ""),
                alert_data=alert_data,
            )
        except Exception:
            return None

    def _agent_result_is_success(self, agent_result: dict[str, Any], selected_action: str) -> bool:
        final_facts = agent_result.get("final_facts", {})
        if isinstance(final_facts, dict):
            task_outcome = final_facts.get("task_outcome", {})
            if isinstance(task_outcome, dict) and isinstance(task_outcome.get("success"), bool):
                return bool(task_outcome.get("success"))
        closure_step = agent_result.get("effective_closure_step", agent_result.get("closure_step", {}))
        return isinstance(closure_step, dict) and bool(closure_step.get("attempted")) and bool(closure_step.get("success"))

    def _agent_result_failure_reason(self, agent_result: dict[str, Any], selected_action: str) -> str:
        final_facts = agent_result.get("final_facts", {})
        if isinstance(final_facts, dict):
            task_outcome = final_facts.get("task_outcome", {})
            if isinstance(task_outcome, dict):
                status = str(task_outcome.get("status", "")).strip()
                if status == "pending_manual_closure":
                    return "自动处置已完成，等待人工结单。"
        closure_step = agent_result.get("effective_closure_step", agent_result.get("closure_step", {}))
        if isinstance(closure_step, dict):
            if not bool(closure_step.get("attempted")):
                return "未执行结单，任务未完成。"
            if not bool(closure_step.get("success")):
                return "结单执行失败，任务未完成。"
        return "主 Agent 未返回成功结果。"

    def _finalize_success(self, task, selected_action: str, result_data: dict[str, Any]) -> dict[str, Any]:
        result_payload = self._normalize_success_result_payload(result_data)
        if isinstance(task.payload, dict):
            workflow_selection = task.payload.get("workflow_selection")
            if isinstance(workflow_selection, dict) and "workflow_selection" not in result_payload:
                result_payload["workflow_selection"] = workflow_selection
        task = self.dispatch_service.finalize_task(task.task_id, selected_action, True, result_payload, None)
        return {
            "action": selected_action,
            "success": True,
            "task_id": task.task_id if task else "",
            "event_ids": str(result_payload.get("event_ids", "")).strip(),
            "data": result_payload,
            "task": task,
            "error": None,
        }

    def _normalize_success_result_payload(self, result_data: dict[str, Any]) -> dict[str, Any]:
        result_payload = dict(result_data)
        closure_step = result_payload.get("effective_closure_step", result_payload.get("closure_step", {}))
        if not isinstance(closure_step, dict) or not bool(closure_step.get("attempted")) or not bool(closure_step.get("success")):
            return result_payload

        final_facts = result_payload.get("final_facts")
        if not isinstance(final_facts, dict):
            final_facts = {}
        task_outcome = final_facts.get("task_outcome")
        if not isinstance(task_outcome, dict):
            task_outcome = {}

        normalized_status = str(task_outcome.get("status", "")).strip()
        if normalized_status == "pending_manual_closure":
            return result_payload
        if task_outcome.get("success") is True and normalized_status in {"succeeded", "completed"}:
            return result_payload

        final_facts["task_outcome"] = {
            **task_outcome,
            "success": True,
            "status": "succeeded",
            "source": str(task_outcome.get("source", "")).strip() or "task_runner_finalize",
        }
        result_payload["final_facts"] = final_facts
        result_payload["success"] = True
        return result_payload

    def _finalize_failure(self, task, selected_action: str, error: str, result_data: dict[str, Any] | None = None) -> dict[str, Any]:
        workflow_selection = {}
        alert_data = {}
        if isinstance(task.payload, dict):
            workflow_selection = task.payload.get("workflow_selection", {}) if isinstance(task.payload.get("workflow_selection"), dict) else {}
            alert_data = task.payload.get("alert_data", {}) if isinstance(task.payload.get("alert_data"), dict) else {}
        failure_payload = dict(result_data or {})
        if "workflow_selection" not in failure_payload:
            failure_payload["workflow_selection"] = workflow_selection
        existing_trace = failure_payload.get("execution_trace", [])
        if not isinstance(existing_trace, list) or not existing_trace:
            existing_trace = [
                {
                    "phase": "alert_received",
                    "title": "接收告警",
                    "summary": "已接收任务告警上下文。",
                    "success": True,
                    "data": {
                        "eventIds": task.event_ids,
                        "alert_name": str(alert_data.get("alert_name", task.title)).strip(),
                        "sip": alert_data.get("sip", ""),
                        "dip": alert_data.get("dip", ""),
                        "alert_time": alert_data.get("alert_time", getattr(task, "alert_time", "")),
                        "payload": alert_data.get("payload", ""),
                    },
                },
                {
                    "phase": "workflow_selection",
                    "title": "Workflow 记录",
                    "summary": (
                        f"历史流程记录：{workflow_selection.get('workflow_id')}"
                        if workflow_selection.get("workflow_id")
                        else str(workflow_selection.get("reason", "")).strip() or "存在历史 Workflow 记录。"
                    ),
                    "success": True,
                    "data": workflow_selection,
                } if workflow_selection else None,
            ]
        failure_payload["execution_trace"] = [
            item
            for item in existing_trace
            if item is not None and not (isinstance(item, dict) and str(item.get("phase", "")).strip() == "final_status")
        ]
        failure_payload["execution_trace"].append(
            {
                "phase": "final_status",
                "title": "最终执行状态",
                "summary": error,
                "success": False,
                "data": {
                    "success": False,
                    "error": error,
                    "action": selected_action,
                },
            }
        )
        failure_payload["success"] = False
        task = self.dispatch_service.finalize_task(task.task_id, selected_action, False, failure_payload, error)
        return {
            "action": selected_action,
            "success": False,
            "task_id": task.task_id if task else "",
            "event_ids": str((task.event_ids if task else "")),
            "data": failure_payload,
            "task": task,
            "error": error,
        }

    def _finish_agent_result(self, task, selected_action: str, agent_result: dict[str, Any], run_log_ref: RunLogRef | dict[str, Any] | None) -> dict[str, Any]:
        self._record_agent_result(run_log_ref, agent_result)
        if agent_result.get("approval_pending"):
            pending_payload = dict(agent_result)
            task = self.dispatch_service.mark_task_awaiting_approval(task.task_id, selected_action, pending_payload, "任务等待技能审批。")
            result = {
                "action": selected_action,
                "success": False,
                "task_id": task.task_id if task else "",
                "event_ids": task.event_ids if task else "",
                "data": pending_payload,
                "task": task,
                "error": "任务等待技能审批。",
            }
            self._log_event(run_log_ref, "task_finished", "任务等待技能审批", result, level="warn")
            return result

        if self._agent_result_is_success(agent_result, selected_action):
            result = self._finalize_success(task, selected_action, agent_result)
            self._log_event(run_log_ref, "task_finished", "任务成功完成", {"success": True, "result": result})
            return result
        error = self._agent_result_failure_reason(agent_result, selected_action)
        result = self._finalize_failure(task, selected_action, error, agent_result)
        self._log_event(run_log_ref, "task_finished", "任务未完成", {"success": False, "error": error, "result": result}, level="error")
        return result

    async def _run_agent_react(self, task, alert: dict[str, Any], selected_action: str, execution_context: dict[str, Any], run_log_ref: RunLogRef | dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            agent_result = await self.agent_service.run_alert(alert, selected_action, execution_context=execution_context)
        except Exception as exc:
            self.audit_service.record("agent_react_task_failed", "Agent ReAct runtime failed.", {"error": str(exc)})
            self._log_event(run_log_ref, "agent_exception", "主 Agent 执行异常", {"error": str(exc)}, level="error")
            return self._finalize_failure(task, selected_action, f"主 Agent 执行失败：{exc}")

        return self._finish_agent_result(task, selected_action, agent_result, run_log_ref)

    async def _run_workflow_or_fallback(self, task, alert: dict[str, Any], selected_action: str, execution_context: dict[str, Any], run_log_ref: RunLogRef | dict[str, Any] | None = None) -> dict[str, Any]:
        if selected_action not in {"triage_close", "triage_dispose"}:
            return self._finalize_failure(task, selected_action, "当前动作不支持工作流执行。")
        if not self.agent_service.is_configured():
            return self._finalize_failure(task, selected_action, "当前未完成系统主 Agent 配置。")

        try:
            workflow_definition = load_agent_workflow(self.workflow_root, task.workflow_name)
        except FileNotFoundError:
            try:
                agent_result = await self.agent_service.run_alert(alert, selected_action, execution_context=execution_context)
            except Exception as exc:
                self.audit_service.record("agent_task_failed", f"Agent runtime failed during {selected_action}.", {"error": str(exc)})
                self._log_event(run_log_ref, "agent_exception", "主 Agent 执行异常", {"error": str(exc)}, level="error")
                return self._finalize_failure(task, selected_action, f"主 Agent 执行失败：{exc}")
            return self._finish_agent_result(task, selected_action, agent_result, run_log_ref)
        except Exception as exc:
            self.audit_service.record("agent_workflow_task_failed", f"Workflow failed during {selected_action}.", {"error": str(exc)})
            self._log_event(run_log_ref, "workflow_exception", "Workflow 加载异常", {"error": str(exc)}, level="error")
            return self._finalize_failure(task, selected_action, f"Workflow 加载失败：{exc}")

        guided_alert = dict(alert)
        guided_alert["_forced_workflow_id"] = workflow_definition.id
        guided_alert["_forced_workflow_name"] = workflow_definition.name
        guided_alert["_forced_workflow_description"] = workflow_definition.description
        try:
            agent_result = await self.agent_service.run_alert(
                guided_alert,
                selected_action,
                execution_context=execution_context,
            )
        except Exception as exc:
            self.audit_service.record("agent_workflow_task_failed", f"Guided workflow failed during {selected_action}.", {"error": str(exc)})
            self._log_event(run_log_ref, "agent_exception", "主 Agent 引导式 Workflow 执行异常", {"error": str(exc)}, level="error")
            return self._finalize_failure(task, selected_action, f"主 Agent 引导式 Workflow 执行失败：{exc}")

        return self._finish_agent_result(task, selected_action, agent_result, run_log_ref)

    async def run_task(self, task, action: str | None = None, execution_entry: str = "manual_alert") -> dict[str, Any]:
        alert = {}
        if isinstance(task.payload, dict):
            payload_alert = task.payload.get("alert_data")
            if isinstance(payload_alert, dict):
                alert = payload_alert

        if not alert:
            task = self.dispatch_service.finalize_task(task.task_id, action or "unknown", False, {}, "任务缺少告警上下文。")
            return {
                "action": action or "unknown",
                "success": False,
                "task_id": task.task_id if task else "",
                "event_ids": "",
                "data": {},
                "task": task,
                "error": "任务缺少告警上下文。",
            }

        selected_action = action
        if not selected_action:
            selected_action = "triage_close"

        running_task = self.dispatch_service.mark_task_running(task.task_id, selected_action)
        if not running_task:
            latest_task = self.dispatch_service.get_task(task.task_id)
            return {
                "action": selected_action,
                "success": False,
                "task_id": latest_task.task_id if latest_task else task.task_id,
                "event_ids": latest_task.event_ids if latest_task else task.event_ids,
                "data": {},
                "task": latest_task,
                "error": "任务状态已变化，当前无法进入运行态。",
            }
        task = running_task
        execution_context = self.agent_service._build_execution_context(
            execution_entry=execution_entry,
            scope_type="alert_task",
            scope_ref=task.task_id,
        )
        run_log_ref = None
        if self.run_log_service is not None:
            run_log_ref = self.run_log_service.start_run(
                task_id=task.task_id,
                event_ids=task.event_ids,
                alert_data=alert,
                selected_action=selected_action,
                execution_entry=execution_entry,
                task_title=task.title,
            )
            execution_context["run_log_ref"] = run_log_ref.to_dict()
            self._log_event(run_log_ref, "task_running", "任务进入运行态", {"task": task, "execution_context": execution_context})

        agent_available, _agent_error = self.agent_service.is_available()
        if not agent_available:
            self._log_event(run_log_ref, "task_finished", "Agent Runtime 不可用", {"error": _agent_error or "unknown"}, level="error")
            return self._finalize_failure(task, selected_action, f"当前 Agent Runtime 不可用：{_agent_error or 'unknown'}")

        activate_run_log_tracer(self.run_log_service, run_log_ref)
        try:
            if task.workflow_name == "agent_react":
                if not self.agent_service.is_configured():
                    self._log_event(run_log_ref, "task_finished", "主 Agent 未配置", {}, level="error")
                    return self._finalize_failure(task, selected_action, "当前未完成系统主 Agent 配置。")
                return await self._run_agent_react(task, alert, selected_action, execution_context, run_log_ref)
            return await self._run_workflow_or_fallback(task, alert, selected_action, execution_context, run_log_ref)
        finally:
            deactivate_run_log_tracer()

    def prepare_approval_resume(
        self,
        task_id: str,
        *,
        approval_id: str = "",
        decision: str = "",
        skill_name: str = "",
    ) -> dict[str, Any]:
        """Switch task to running and log task_running BEFORE replaying the graph.

        Returns ``{"ok": bool, "task", "run_log_ref", "selected_action", "error",
        "current_status"}``. Callers MUST abort the resume (do not call
        ``resolve_skill_approval``) when ``ok`` is ``False`` to avoid
        "log says resumed, DB still awaiting_approval" inconsistency.
        """
        task = self.dispatch_service.get_task(task_id)
        if task is None:
            return {
                "ok": False,
                "task": None,
                "run_log_ref": None,
                "selected_action": "",
                "error": "待恢复任务不存在。",
                "current_status": "",
            }
        selected_action = task.last_action or "triage_close"
        prior_status = task.status
        resumed = self.dispatch_service.mark_task_running_from_approval(task_id, selected_action)
        if resumed is None:
            run_log_ref = self.resolve_run_log_ref(task)
            self._log_event(
                run_log_ref,
                "task_finished",
                "审批恢复中止：任务状态已变化",
                {
                    "task_id": task_id,
                    "approval_id": approval_id,
                    "decision": decision,
                    "skill_name": skill_name,
                    "expected_status": "awaiting_approval",
                    "current_status": prior_status,
                    "selected_action": selected_action,
                },
                level="error",
            )
            return {
                "ok": False,
                "task": task,
                "run_log_ref": run_log_ref,
                "selected_action": selected_action,
                "error": f"任务状态已变化（当前 {prior_status}），无法恢复执行。",
                "current_status": prior_status,
            }
        task = resumed
        run_log_ref = self.resolve_run_log_ref(task)
        if run_log_ref is not None:
            self._log_event(
                run_log_ref,
                "task_running",
                "审批已处理，任务恢复运行",
                {
                    "task_id": task.task_id,
                    "selected_action": selected_action,
                    "approval_id": approval_id,
                    "decision": decision,
                    "skill_name": skill_name,
                },
            )
        return {
            "ok": True,
            "task": task,
            "run_log_ref": run_log_ref,
            "selected_action": selected_action,
            "error": None,
            "current_status": "running",
        }

    def finalize_after_approval(
        self,
        task_id: str,
        agent_result: dict[str, Any],
        *,
        prepared_task: Any = None,
        run_log_ref: RunLogRef | None = None,
        selected_action: str = "",
    ) -> dict[str, Any]:
        task = prepared_task if prepared_task is not None else self.dispatch_service.get_task(task_id)
        if not task:
            return {
                "action": "approval",
                "success": False,
                "task_id": task_id,
                "event_ids": "",
                "data": agent_result,
                "task": None,
                "error": "待恢复任务不存在。",
            }
        if prepared_task is None:
            resumed = self.dispatch_service.mark_task_running_from_approval(task_id, task.last_action or "triage_close")
            task = resumed or task
        if not selected_action:
            selected_action = task.last_action or "triage_close"
        if run_log_ref is None:
            run_log_ref = self.resolve_run_log_ref(task)
        if prepared_task is None and run_log_ref is not None:
            self._log_event(
                run_log_ref,
                "task_running",
                "审批已处理，任务恢复运行",
                {"task_id": task.task_id, "selected_action": selected_action},
            )
        if agent_result.get("approval_pending"):
            pending_payload = dict(agent_result)
            task = self.dispatch_service.mark_task_awaiting_approval(task.task_id, selected_action, pending_payload, "任务等待技能审批。")
            result = {
                "action": selected_action,
                "success": False,
                "task_id": task.task_id if task else task_id,
                "event_ids": task.event_ids if task else "",
                "data": pending_payload,
                "task": task,
                "error": "任务等待技能审批。",
            }
            self._log_event(run_log_ref, "task_finished", "任务再次等待技能审批", result, level="warn")
            return result
        return self._finish_agent_result(task, selected_action, agent_result, run_log_ref)
