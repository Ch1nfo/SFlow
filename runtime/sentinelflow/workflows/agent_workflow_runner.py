from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from sentinelflow.agent.context_utils import (
    build_context_envelope,
    build_context_manifest,
    build_case_context,
    compact_text,
    extract_key_facts,
    format_context_manifest_header,
    summarize_tool_calls,
)
from sentinelflow.services.skill_approval_service import SkillApprovalService
from sentinelflow.workflows.agent_workflow_registry import AgentWorkflowDefinition


class SentinelFlowAgentWorkflowRunner:
    def __init__(self, agent_service, audit_service) -> None:
        self.agent_service = agent_service
        self.audit_service = audit_service

    def _serialize_workflow(self, workflow: AgentWorkflowDefinition) -> dict[str, Any]:
        return {
            "id": workflow.id,
            "name": workflow.name,
            "description": workflow.description,
            "enabled": workflow.enabled,
            "steps": [
                {
                    "id": step.id,
                    "name": step.name,
                    "agent": step.agent,
                    "task_prompt": step.task_prompt,
                }
                for step in workflow.steps
            ],
        }

    def _deserialize_workflow(self, payload: dict[str, Any]) -> AgentWorkflowDefinition:
        steps = [
            SimpleNamespace(
                id=str(item.get("id", "")).strip() or f"step-{index}",
                name=str(item.get("name", "")).strip() or str(item.get("id", "")).strip() or f"step-{index}",
                agent=str(item.get("agent", "")).strip(),
                task_prompt=str(item.get("task_prompt", "")).strip(),
            )
            for index, item in enumerate(list(payload.get("steps", []) or []), start=1)
            if isinstance(item, dict) and str(item.get("agent", "")).strip()
        ]
        return SimpleNamespace(
            id=str(payload.get("id", "")).strip(),
            name=str(payload.get("name", "")).strip(),
            description=str(payload.get("description", "")).strip(),
            enabled=bool(payload.get("enabled", True)),
            steps=steps,
        )

    def _workflow_checkpoint_id(self, workflow: AgentWorkflowDefinition, execution_context: dict[str, Any] | None) -> str:
        explicit = str((execution_context or {}).get("checkpoint_thread_id", "")).strip()
        if explicit:
            return explicit
        return f"{uuid4().hex}:workflow:{workflow.id}"

    def _workflow_parent_context(self, execution_context: dict[str, Any] | None) -> dict[str, str]:
        return {
            "parent_checkpoint_thread_id": str((execution_context or {}).get("parent_checkpoint_thread_id", "")).strip(),
            "parent_checkpoint_ns": str((execution_context or {}).get("parent_checkpoint_ns", "")).strip(),
            "parent_tool_call_id": str((execution_context or {}).get("parent_tool_call_id", "")).strip(),
        }

    def _build_step_execution_context(
        self,
        workflow: AgentWorkflowDefinition,
        step_index: int,
        execution_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        workflow_checkpoint_id = self._workflow_checkpoint_id(workflow, execution_context)
        return self.agent_service._build_execution_context(
            execution_entry=str((execution_context or {}).get("execution_entry", "")).strip(),
            scope_type=str((execution_context or {}).get("scope_type", "")).strip(),
            scope_ref=str((execution_context or {}).get("scope_ref", "")).strip(),
            run_id=str((execution_context or {}).get("run_id", "")).strip() or None,
            checkpoint_thread_id=f"{workflow_checkpoint_id}:step:{step_index}",
            checkpoint_ns="agent_graph",
            parent_checkpoint_thread_id=workflow_checkpoint_id,
            parent_checkpoint_ns="workflow_runner",
            parent_tool_call_id=f"workflow-step:{step_index}",
            approved_fingerprints=list((execution_context or {}).get("approved_fingerprints", []) or []),
            rejected_fingerprints=list((execution_context or {}).get("rejected_fingerprints", []) or []),
        )

    def _compact_step_result(
        self,
        *,
        step_index: int,
        step,
        task_prompt: str,
        worker_result: dict[str, Any],
    ) -> dict[str, Any]:
        messages = worker_result.get("messages", [])
        final_response = str(worker_result.get("final_response", ""))
        final_response_for_check = final_response.strip()
        tool_result_facts: dict[str, Any] = {}
        for message in messages:
            if not isinstance(message, dict) or str(message.get("type", "")).strip() != "tool":
                continue
            content = str(message.get("content", "")).strip()
            try:
                parsed = json.loads(content)
            except Exception:
                continue
            tool_result_facts = extract_key_facts(tool_result_facts, parsed)
        success_evaluator = getattr(self.agent_service, "evaluate_worker_result", None)
        if callable(success_evaluator):
            success, _error = success_evaluator(worker_result)
        else:
            has_error = False
            for message in messages:
                if not isinstance(message, dict) or str(message.get("type", "")).strip() != "tool":
                    continue
                content = str(message.get("content", "")).strip()
                try:
                    parsed = json.loads(content)
                except Exception:
                    has_error = True
                    break
                if isinstance(parsed, dict) and (not parsed.get("success", True) or parsed.get("error")):
                    has_error = True
                    break
            has_action = bool(final_response_for_check or worker_result.get("tool_calls"))
            success = has_action and not has_error
        tool_calls_summary = list(worker_result.get("tool_calls_summary", []) or [])
        if not tool_calls_summary:
            tool_calls_summary = summarize_tool_calls(worker_result.get("tool_calls", []))
        key_facts = extract_key_facts(worker_result.get("key_facts", {}), tool_calls_summary, final_response, tool_result_facts)
        context_manifest = worker_result.get("context_manifest", {})
        if not isinstance(context_manifest, dict):
            context_manifest = {}
        return {
            "step": step_index,
            "step_id": step.id,
            "step_name": step.name,
            "worker_agent": step.agent,
            "task_prompt": step.task_prompt,
            "workflow_task_prompt": task_prompt,
            "final_response": final_response,
            "display_summary": compact_text(final_response, 1800),
            "short_summary": compact_text(final_response, 500),
            "tool_calls": worker_result.get("tool_calls", []),
            "tool_calls_summary": tool_calls_summary,
            "actions_summary": [
                {
                    "skill": str(item.get("name", "")).strip(),
                    "args": item.get("args", {}) if isinstance(item.get("args", {}), dict) else {},
                    "key_facts": item.get("key_facts", {}),
                }
                for item in tool_calls_summary
                if isinstance(item, dict) and str(item.get("name", "")).strip()
            ],
            "key_facts": key_facts,
            "context_manifest": context_manifest,
            "context_warnings": list(worker_result.get("context_warnings", []) or []),
            "authority_trace": context_manifest.get("authority_trace", []),
            "missing_required_inputs": list(worker_result.get("missing_required_inputs", []) or []),
            "error": worker_result.get("error"),
            "success": success,
        }

    def _save_workflow_checkpoint(
        self,
        *,
        workflow: AgentWorkflowDefinition,
        workflow_input: dict[str, Any],
        task_prompt: str,
        step_results: list[dict[str, Any]],
        pending_step_index: int,
        execution_context: dict[str, Any] | None,
    ) -> str:
        checkpoint_thread_id = self._workflow_checkpoint_id(workflow, execution_context)
        parent_context = self._workflow_parent_context(execution_context)
        self.agent_service.approval_service.save_checkpoint(
            checkpoint_thread_id=checkpoint_thread_id,
            checkpoint_ns="workflow_runner",
            checkpoint_kind="workflow_runner",
            run_id=str((execution_context or {}).get("run_id", "")).strip(),
            scope_type=str((execution_context or {}).get("scope_type", "")).strip(),
            scope_ref=str((execution_context or {}).get("scope_ref", "")).strip(),
            agent_name=workflow.id,
            execution_entry=str((execution_context or {}).get("execution_entry", "")).strip(),
            action_hint=task_prompt,
            state_payload={
                "workflow": self._serialize_workflow(workflow),
                "workflow_input": workflow_input,
                "task_prompt": task_prompt,
                "step_results": step_results,
                "pending_step_index": pending_step_index,
                "execution_context": dict(execution_context or {}),
                **parent_context,
            },
        )
        return checkpoint_thread_id

    def _build_workflow_pending_result(
        self,
        *,
        workflow: AgentWorkflowDefinition,
        workflow_input: dict[str, Any],
        task_prompt: str,
        step_results: list[dict[str, Any]],
        approval_request: dict[str, Any],
    ) -> dict[str, Any]:
        execution_trace = self._build_workflow_execution_trace(
            workflow=workflow,
            workflow_input=workflow_input,
            task_prompt=task_prompt,
            step_results=step_results,
            success=False,
            error="Workflow 步骤等待技能审批。",
        )
        return {
            "success": False,
            "workflow_id": workflow.id,
            "workflow_name": workflow.name,
            "workflow_description": workflow.description,
            "execution_mode": "agent_workflow",
            "event_ids": str(workflow_input.get("eventIds", "")).strip(),
            "task_prompt": task_prompt,
            "worker_results": step_results,
            "summary": f"Workflow《{workflow.name}》等待技能审批。",
            "reason": f"Workflow《{workflow.name}》中的某个步骤需要人工审批后继续执行。",
            "evidence": self._build_workflow_evidence(step_results),
            "actions": self._build_workflow_actions(step_results),
            "execution_trace": execution_trace,
            "approval_pending": True,
            "approval_request": approval_request,
            "error": "Workflow 步骤等待技能审批。",
        }

    async def _continue_workflow(
        self,
        workflow: AgentWorkflowDefinition,
        workflow_input: dict[str, Any],
        *,
        task_prompt: str = "",
        step_results: list[dict[str, Any]] | None = None,
        start_index: int = 1,
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        completed_results = list(step_results or [])
        for step_index, step in enumerate(workflow.steps[start_index - 1 :], start=start_index):
            worker_prompt = self._build_worker_prompt(
                workflow=workflow,
                workflow_input=workflow_input,
                step_index=step_index,
                step_name=step.name,
                task_prompt=step.task_prompt,
                delegated_task_prompt=task_prompt,
                step_results=completed_results,
            )
            worker_result = await self.agent_service.run_command(
                worker_prompt,
                history=[],
                agent_name=step.agent,
                execution_context=self._build_step_execution_context(workflow, step_index, execution_context),
            )
            if worker_result.get("approval_pending"):
                self._save_workflow_checkpoint(
                    workflow=workflow,
                    workflow_input=workflow_input,
                    task_prompt=task_prompt,
                    step_results=completed_results,
                    pending_step_index=step_index,
                    execution_context=execution_context,
                )
                approval_request = worker_result.get("approval_request", {})
                if not isinstance(approval_request, dict):
                    approval_request = {}
                return self._build_workflow_pending_result(
                    workflow=workflow,
                    workflow_input=workflow_input,
                    task_prompt=task_prompt,
                    step_results=completed_results,
                    approval_request=approval_request,
                )

            compact = self._compact_step_result(
                step_index=step_index,
                step=step,
                task_prompt=task_prompt,
                worker_result=worker_result,
            )
            completed_results.append(compact)
            self.audit_service.record(
                "agent_workflow_step_finished",
                f"Agent workflow {workflow.id} finished step {step.id}.",
                {
                    "workflowId": workflow.id,
                    "stepId": step.id,
                    "stepName": step.name,
                    "workerAgent": step.agent,
                    "eventIds": str(workflow_input.get("eventIds", "")).strip(),
                    "success": compact["success"],
                },
            )
            if not compact["success"]:
                execution_trace = self._build_workflow_execution_trace(
                    workflow=workflow,
                    workflow_input=workflow_input,
                    task_prompt=task_prompt,
                    step_results=completed_results,
                    success=False,
                    error=f"步骤 {step.name} 未返回有效结果。",
                )
                return {
                    "success": False,
                    "workflow_id": workflow.id,
                    "workflow_name": workflow.name,
                    "workflow_description": workflow.description,
                    "execution_mode": "agent_workflow",
                    "event_ids": str(workflow_input.get("eventIds", "")).strip(),
                    "task_prompt": task_prompt,
                    "worker_results": completed_results,
                    "summary": f"Workflow《{workflow.name}》在步骤《{step.name}》未返回有效结果。",
                    "reason": f"Workflow《{workflow.name}》执行失败，失败步骤：{step.name}。",
                    "evidence": self._build_workflow_evidence(completed_results),
                    "error": f"步骤 {step.name} 未返回有效结果。",
                    "actions": self._build_workflow_actions(completed_results),
                    "execution_trace": execution_trace,
                }

        latest = completed_results[-1] if completed_results else {}
        final_response = str(latest.get("final_response", "")).strip()
        execution_trace = self._build_workflow_execution_trace(
            workflow=workflow,
            workflow_input=workflow_input,
            task_prompt=task_prompt,
            step_results=completed_results,
            success=bool(latest.get("success")),
            error="",
        )
        return {
            "success": bool(latest.get("success")),
            "workflow_id": workflow.id,
            "workflow_name": workflow.name,
            "workflow_description": workflow.description,
            "execution_mode": "agent_workflow",
            "event_ids": str(workflow_input.get("eventIds", "")).strip(),
            "task_prompt": task_prompt,
            "summary": final_response or f"Workflow《{workflow.name}》已完成。",
            "reason": final_response or f"Workflow《{workflow.name}》已按既定步骤完成执行。",
            "evidence": self._build_workflow_evidence(completed_results),
            "final_response": final_response,
            "worker_results": completed_results,
            "workflow_steps": [
                {
                    "id": step.id,
                    "name": step.name,
                    "agent": step.agent,
                }
                for step in workflow.steps
            ],
            "used_agent_workflow": True,
            "actions": self._build_workflow_actions(completed_results),
            "closure_step": {"attempted": False, "success": False},
            "execution_trace": execution_trace,
        }

    async def execute_workflow(
        self,
        workflow: AgentWorkflowDefinition,
        workflow_input: dict[str, Any],
        *,
        task_prompt: str = "",
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._continue_workflow(
            workflow,
            workflow_input,
            task_prompt=task_prompt,
            step_results=[],
            start_index=1,
            execution_context=execution_context,
        )

    async def resume_checkpoint(
        self,
        checkpoint: dict[str, Any],
        step_result: dict[str, Any],
        approval,
    ) -> dict[str, Any]:
        state = checkpoint.get("state", {}) if isinstance(checkpoint.get("state", {}), dict) else {}
        workflow_payload = state.get("workflow", {})
        if not isinstance(workflow_payload, dict):
            return {
                "success": False,
                "route": "approval_resume_failed",
                "error": "Workflow 断点数据损坏，缺少 workflow 定义。",
                "data": {},
            }
        workflow = self._deserialize_workflow(workflow_payload)
        workflow_input = state.get("workflow_input", {})
        workflow_input = workflow_input if isinstance(workflow_input, dict) else {}
        task_prompt = str(state.get("task_prompt", ""))
        step_results = list(state.get("step_results", []) or [])
        pending_step_index = int(state.get("pending_step_index", 1) or 1)
        execution_context = dict(state.get("execution_context", {}) or {})
        approved_fingerprints = set(execution_context.get("approved_fingerprints", []) or [])
        rejected_fingerprints = set(execution_context.get("rejected_fingerprints", []) or [])
        if approval.status == "approved":
            approved_fingerprints.add(approval.arguments_fingerprint)
        elif approval.status == "rejected":
            rejected_fingerprints.add(
                SkillApprovalService.build_skill_arguments_key(
                    approval.skill_name,
                    approval.arguments_fingerprint,
                )
            )
        execution_context["approved_fingerprints"] = list(approved_fingerprints)
        execution_context["rejected_fingerprints"] = list(rejected_fingerprints)
        if pending_step_index < 1 or pending_step_index > len(workflow.steps):
            return {
                "success": False,
                "route": "approval_resume_failed",
                "error": "Workflow 断点中的 pending_step_index 无效。",
                "data": {},
            }
        current_step = workflow.steps[pending_step_index - 1]
        compact = self._compact_step_result(
            step_index=pending_step_index,
            step=current_step,
            task_prompt=task_prompt,
            worker_result=step_result,
        )
        step_results.append(compact)
        self.audit_service.record(
            "agent_workflow_step_finished",
            f"Agent workflow {workflow.id} resumed step {current_step.id} after approval.",
            {
                "workflowId": workflow.id,
                "stepId": current_step.id,
                "stepName": current_step.name,
                "workerAgent": current_step.agent,
                "eventIds": str(workflow_input.get("eventIds", "")).strip(),
                "success": compact["success"],
                "approvalResumed": True,
            },
        )
        if not compact["success"]:
            execution_trace = self._build_workflow_execution_trace(
                workflow=workflow,
                workflow_input=workflow_input,
                task_prompt=task_prompt,
                step_results=step_results,
                success=False,
                error=f"步骤 {current_step.name} 未返回有效结果。",
            )
            return {
                "success": False,
                "workflow_id": workflow.id,
                "workflow_name": workflow.name,
                "workflow_description": workflow.description,
                "execution_mode": "agent_workflow",
                "event_ids": str(workflow_input.get("eventIds", "")).strip(),
                "task_prompt": task_prompt,
                "worker_results": step_results,
                "summary": f"Workflow《{workflow.name}》在步骤《{current_step.name}》未返回有效结果。",
                "reason": f"Workflow《{workflow.name}》执行失败，失败步骤：{current_step.name}。",
                "evidence": self._build_workflow_evidence(step_results),
                "error": f"步骤 {current_step.name} 未返回有效结果。",
                "actions": self._build_workflow_actions(step_results),
                "execution_trace": execution_trace,
            }
        return await self._continue_workflow(
            workflow,
            workflow_input,
            task_prompt=task_prompt,
            step_results=step_results,
            start_index=pending_step_index + 1,
            execution_context=execution_context,
        )

    def _build_worker_prompt(
        self,
        workflow: AgentWorkflowDefinition,
        workflow_input: dict[str, Any],
        step_index: int,
        step_name: str,
        task_prompt: str,
        delegated_task_prompt: str,
        step_results: list[dict[str, Any]],
    ) -> str:
        effective_task_prompt = task_prompt if task_prompt.strip() else f"请作为流程中的子 Agent 完成第 {step_index} 步《{step_name}》需要承担的工作，并输出你的阶段性结果。"
        workflow_definition = {
            "id": workflow.id,
            "name": workflow.name,
            "description": workflow.description,
            "steps": [
                {
                    "id": item.id,
                    "name": item.name,
                    "agent": item.agent,
                    "task_prompt": item.task_prompt,
                }
                for item in workflow.steps
            ],
        }
        prior_step_results = [item for item in step_results if isinstance(item, dict)]
        prior_step_summaries = [
            {
                "step": item.get("step"),
                "step_name": item.get("step_name"),
                "worker_agent": item.get("worker_agent"),
                "success": bool(item.get("success")),
                "summary": item.get("short_summary") or compact_text(item.get("final_response", ""), 500),
                "key_facts": item.get("key_facts", {}),
            }
            for item in prior_step_results
        ]
        prior_facts = extract_key_facts(
            workflow_input,
            workflow_definition,
            delegated_task_prompt,
            effective_task_prompt,
            prior_step_summaries,
            *[
                item.get("key_facts", {})
                for item in prior_step_results
            ]
        )
        case_context = build_case_context(
            goal=effective_task_prompt,
            alert_data=workflow_input,
            prior_step_results=prior_step_results,
        )
        context_manifest = build_context_manifest(
            current_goal=effective_task_prompt,
            entry_type="workflow_step",
            current_step={
                "workflow_id": workflow.id,
                "workflow_name": workflow.name,
                "step_index": step_index,
                "step_name": step_name,
            },
            original_input=workflow_input,
            current_task_prompt=effective_task_prompt,
            workflow_definition=workflow_definition,
            prior_step_results=prior_step_summaries,
            model_summary=prior_facts,
        )
        envelope = build_context_envelope(
            original_input=workflow_input,
            delegated_task=delegated_task_prompt,
            workflow_step={
                "workflow_definition": workflow_definition,
                "workflow_name": workflow.name,
                "workflow_description": workflow.description,
                "step_index": step_index,
                "steps_count": len(workflow.steps),
                "step_name": step_name,
                "task_prompt": effective_task_prompt,
                "prior_step_summaries": prior_step_summaries,
                "prior_steps": prior_step_summaries,
                "case_context": case_context,
                "context_manifest": context_manifest,
            },
            prior_facts=prior_facts,
            authoritative_inputs={
                "workflow_input": workflow_input,
                "workflow_task_prompt": delegated_task_prompt,
                "current_step_task_prompt": effective_task_prompt,
            },
            constraints=[
                "当前任务以 workflow_step.task_prompt 为准，workflow_definition.description 是固定流程目标和对象依据。",
                "不得从历史噪声中猜测发送对象、处置对象或结单对象。",
                "关键对象只能来自当前步骤、workflow_definition、authoritative_inputs、原始输入中的明确字段或 prior_facts。",
                "如果关键对象缺失，必须说明缺失，不要编造。",
            ],
        )
        return (
            f"你当前处于 Agent Workflow《{workflow.name}》的第 {step_index}/{len(workflow.steps)} 步：{step_name}\n\n"
            f"{format_context_manifest_header(context_manifest)}\n"
            "请只依据以下上下文执行当前步骤；workflow_definition.description 是固定流程目标和对象依据，prior_step_summaries 与 case_context 是前置步骤执行视图，完整前置结果仅保留在运行日志：\n"
            f"```json\n{json.dumps(envelope, ensure_ascii=False, indent=2)}\n```\n\n"
            f"本步骤固定任务：\n{effective_task_prompt}\n\n"
            "要求：\n"
            "- 只完成当前步骤，不要尝试规划整个流程\n"
            "- 不要假设自己能调度其他 Agent\n"
            "- 发送对象、处置对象、结单对象只能来自当前步骤、workflow_definition、authoritative_inputs、prior_step_summaries、case_context、原始上下文或 prior_facts\n"
            "- 如果关键对象缺失，明确说明缺失，不要编造\n"
            "- 输出简洁中文结果，必要时调用你已授权的 Skill\n"
        )

    def _build_workflow_evidence(self, step_results: list[dict[str, Any]]) -> list[str]:
        evidence: list[str] = []
        for step in step_results:
            if not isinstance(step, dict):
                continue
            step_name = str(step.get("step_name", "")).strip()
            final_response = str(step.get("final_response", "")).strip()
            if not step_name or not final_response:
                continue
            snippet = final_response[:120].strip()
            evidence.append(f"{step_name}: {snippet}")
        return evidence[:5]

    def _build_workflow_actions(self, step_results: list[dict[str, Any]]) -> dict[str, Any]:
        action_items: list[dict[str, Any]] = []
        for step in step_results:
            if not isinstance(step, dict):
                continue
            tool_calls_summary = step.get("tool_calls_summary", [])
            if not isinstance(tool_calls_summary, list) or not tool_calls_summary:
                continue
            action_items.append(
                {
                    "step_id": step.get("step_id", ""),
                    "step_name": step.get("step_name", ""),
                    "worker_agent": step.get("worker_agent", ""),
                    "tool_calls": step.get("tool_calls", []),
                    "tool_calls_summary": tool_calls_summary,
                    "key_facts": step.get("key_facts", {}),
                }
            )
        return {
            "workers_used": [str(step.get("worker_agent", "")).strip() for step in step_results if isinstance(step, dict) and str(step.get("worker_agent", "")).strip()],
            "tool_runs": action_items,
        }

    def _build_workflow_execution_trace(
        self,
        *,
        workflow: AgentWorkflowDefinition,
        workflow_input: dict[str, Any],
        task_prompt: str,
        step_results: list[dict[str, Any]],
        success: bool,
        error: str,
    ) -> list[dict[str, Any]]:
        trace: list[dict[str, Any]] = [
            {
                "phase": "workflow_received",
                "title": "接收 Workflow 调用",
                "summary": f"主 Agent 调用了 Workflow《{workflow.name}》。",
                "success": True,
                "data": {
                    "workflow_id": workflow.id,
                    "workflow_name": workflow.name,
                    "task_prompt": task_prompt,
                    "eventIds": str(workflow_input.get("eventIds", "")).strip(),
                    "alert_name": str(workflow_input.get("alert_name", "")).strip(),
                },
            }
        ]
        for step in step_results:
            if not isinstance(step, dict):
                continue
            trace.append(
                {
                    "phase": "workflow_step",
                    "title": f"步骤：{str(step.get('step_name', '')).strip() or str(step.get('step_id', '')).strip() or '未命名步骤'}",
                    "summary": str(step.get("display_summary") or step.get("final_response", "")).strip() or "该步骤已执行完成。",
                    "success": bool(step.get("success")),
                    "data": step,
                }
            )
        trace.append(
            {
                "phase": "workflow_final_status",
                "title": "Workflow 执行结果",
                "summary": "Workflow 已完成执行。" if success else (error or "Workflow 执行失败。"),
                "success": success,
                "data": {
                    "success": success,
                    "error": error,
                    "steps_count": len(step_results),
                    "workflow_id": workflow.id,
                },
            }
        )
        return trace
