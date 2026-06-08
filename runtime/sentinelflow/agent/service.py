from __future__ import annotations

import copy
from dataclasses import replace
import json
import logging
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from sentinelflow.agent.checkpoint_state import deserialize_graph_state, serialize_graph_state
from sentinelflow.agent.catalog import load_skill_catalog
from sentinelflow.agent.conversation_context import build_conversation_context_plan_with_llm
from sentinelflow.agent.context_utils import (
    build_compact_final_summary_context,
    build_context_manifest,
    compact_worker_result_for_llm,
    extract_explicit_event_id_from_text,
    extract_key_facts,
    is_reusable_final_response_markdown,
    summarize_tool_calls,
)
from sentinelflow.agent.graph import build_agent_graph
from sentinelflow.agent.policy import can_agent_delegate_to_worker, can_agent_execute_skill, can_agent_read_skill
from sentinelflow.agent.prompt_builder import PromptBuildContext, build_prompt
from sentinelflow.agent.prompts import (
    PRIMARY_ALERT_ORCHESTRATION_APPENDIX,
    PRIMARY_COMMAND_ORCHESTRATION_APPENDIX,
)
from sentinelflow.agent.registry import list_agent_definitions, resolve_default_agent
from sentinelflow.agent.skill_run_analyzer import SkillRunAnalyzerMixin
from sentinelflow.agent.text_extractor import (
    TextExtractorMixin,
    clean_model_text as _clean_model_text,
    infer_explicit_disposition_from_text,
    normalize_markdown_line as _normalize_markdown_line,
)
from sentinelflow.config.runtime import build_llm_client_kwargs, load_runtime_config
from sentinelflow.services.skill_approval_service import SkillApprovalService
from sentinelflow.services.triage_service import TriageService
from sentinelflow.skills.adapters import SentinelFlowSkillRuntime
from sentinelflow.workflows.agent_workflow_registry import is_workflow_allowed_for_alert_source, list_agent_workflows


LOGGER = logging.getLogger(__name__)


def _result_text_for_display(result: dict[str, Any], *, limit: int | None = None) -> str:
    if not isinstance(result, dict):
        return ""
    for field in ("summary", "display_summary", "short_summary", "final_response", "reason", "error"):
        text = str(result.get(field, "") or "").strip()
        if text:
            return text[:limit] if limit is not None else text
    return ""


class SentinelFlowAgentService(SkillRunAnalyzerMixin, TextExtractorMixin):
    def __init__(
        self,
        project_root: Path,
        skill_runtime: SentinelFlowSkillRuntime,
        approval_service: SkillApprovalService,
        audit_service: Any | None = None,
    ) -> None:
        self.project_root = project_root
        self.skill_runtime = skill_runtime
        self.approval_service = approval_service
        self.audit_service = audit_service
        self.triage_service = TriageService()
        self.agent_root = project_root / ".sentinelflow" / "plugins" / "agents"
        self.workflow_root = project_root / ".sentinelflow" / "plugins" / "workflows"
        self.workflow_runner = None

    def attach_workflow_runner(self, workflow_runner) -> None:
        self.workflow_runner = workflow_runner

    def is_configured(self, agent_name: str | None = None) -> bool:
        config = load_runtime_config()
        if not config.agent_enabled:
            return False
        agent_definition = resolve_default_agent(self.agent_root, agent_name)
        effective_config = agent_definition.resolve_runtime_config(config) if agent_definition else config
        return bool(
            effective_config.llm_model
            and effective_config.llm_api_key
            and effective_config.llm_api_base_url
        )

    def is_available(self) -> tuple[bool, str | None]:
        try:
            import langgraph  # noqa: F401
            import langchain_openai  # noqa: F401
            import langchain_core  # noqa: F401
        except ModuleNotFoundError as exc:
            return False, str(exc)
        return True, None

    def _record_audit(self, event_type: str, message: str, payload: dict[str, Any] | None = None) -> None:
        if self.audit_service is None:
            return
        try:
            self.audit_service.record(event_type, message, payload or {})
        except Exception:
            LOGGER.exception("Failed to record SentinelFlow agent audit event.")

    def _resolve_skill_permissions(self, agent_definition) -> tuple[list[str], list[str]]:
        if agent_definition is None:
            return self.skill_runtime.list_skills(), self.skill_runtime.list_skills()
        readable_skills: list[str] = []
        executable_skills: list[str] = []
        for skill in self.skill_runtime.loader.list_skills():
            if can_agent_read_skill(agent_definition, skill):
                readable_skills.append(skill.spec.name)
            if can_agent_execute_skill(agent_definition, skill):
                executable_skills.append(skill.spec.name)
        return readable_skills, executable_skills

    def _resolve_worker_candidates(self, primary_agent, entry_type: str = "conversation") -> list:
        if primary_agent is None or primary_agent.role != "primary" or not primary_agent.enabled:
            return []
        # Allow multi-agent routing for both conversation and alert entry types
        workers = [agent for agent in list_agent_definitions(self.agent_root) if agent.role == "worker" and agent.enabled]
        return [agent for agent in workers if can_agent_delegate_to_worker(primary_agent, agent.name, entry_type=entry_type)]

    async def _invoke_agent_graph_state(
        self,
        agent_definition,
        alert_data: dict[str, Any],
        history: list[dict[str, str]] | None = None,
        cancel_event=None,
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        config = load_runtime_config()
        effective_config = agent_definition.resolve_runtime_config(config) if agent_definition else config
        readable_skills, executable_skills = self._resolve_skill_permissions(agent_definition)
        prompt_mode = "agent_command" if alert_data.get("alert_source") == "human_command" else "agent_alert"
        alert_data = dict(alert_data)
        if not isinstance(alert_data.get("context_manifest"), dict):
            current_goal = str(alert_data.get("delegated_task_prompt") or alert_data.get("payload") or alert_data.get("handling_intent") or alert_data.get("eventIds") or "")
            context_manifest = build_context_manifest(
                current_goal=current_goal,
                entry_type=str((execution_context or {}).get("execution_entry", "")).strip() or prompt_mode,
                current_step={"agent": agent_definition.name if agent_definition else "", "mode": prompt_mode},
                original_input=alert_data,
                current_task_prompt=current_goal,
                conversation_history=list(history or []),
                current_goal_meta={
                    "conversation_context_policy": (
                        alert_data.get("conversation_context_policy", {})
                        if isinstance(alert_data.get("conversation_context_policy", {}), dict)
                        else {}
                    )
                },
            )
            alert_data["context_manifest"] = context_manifest
            alert_data["context_warnings"] = list(context_manifest.get("context_warnings", []) or [])
        graph = build_agent_graph(
            self.project_root,
            self.skill_runtime,
            self.approval_service,
            effective_config,
            enable_read_skill_document=bool(readable_skills),
            enable_execute_skill=bool(executable_skills),
            executable_skill_names=executable_skills,
        )
        state = await graph.ainvoke(
            {
                "alert_data": alert_data,
                "messages": self._build_history_messages(history or []),
                "event_id_ref": str(alert_data.get("eventIds", "")).strip(),
                "input_seeded": False,
                "cancel_event": cancel_event,
                "readable_skills": readable_skills,
                "executable_skills": executable_skills,
                "system_prompt_override": agent_definition.prompt_for_mode(prompt_mode) if agent_definition else "",
                "agent_name": agent_definition.name if agent_definition else "",
                "run_id": str((execution_context or {}).get("run_id", "")).strip(),
                "execution_entry": str((execution_context or {}).get("execution_entry", "")).strip(),
                "scope_type": str((execution_context or {}).get("scope_type", "")).strip(),
                "scope_ref": str((execution_context or {}).get("scope_ref", "")).strip(),
                "checkpoint_thread_id": str((execution_context or {}).get("checkpoint_thread_id", "")).strip(),
                "graph_checkpoint_ns": str((execution_context or {}).get("checkpoint_ns", "agent_graph")).strip(),
                "parent_checkpoint_thread_id": str((execution_context or {}).get("parent_checkpoint_thread_id", "")).strip(),
                "parent_checkpoint_ns": str((execution_context or {}).get("parent_checkpoint_ns", "")).strip(),
                "parent_tool_call_id": str((execution_context or {}).get("parent_tool_call_id", "")).strip(),
                "approved_fingerprints": list((execution_context or {}).get("approved_fingerprints", []) or []),
                "rejected_fingerprints": list((execution_context or {}).get("rejected_fingerprints", []) or []),
                "executed_skill_cache": dict((execution_context or {}).get("executed_skill_cache", {}) or {}),
            },
            {"recursion_limit": self._resolve_agent_recursion_limit(agent_definition)},
        )
        return state

    async def _run_agent_graph(
        self,
        agent_definition,
        alert_data: dict[str, Any],
        history: list[dict[str, str]] | None = None,
        cancel_event=None,
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = await self._invoke_agent_graph_state(
            agent_definition,
            alert_data,
            history=history,
            cancel_event=cancel_event,
            execution_context=execution_context,
        )
        serialized = self._serialize_graph_result(
            str(alert_data.get("payload") or alert_data.get("eventIds") or "").strip(),
            state,
            agent_definition.name if agent_definition else "",
        )
        if state.get("approval_pending"):
            serialized["approval_pending"] = True
            approval_request = self._persist_pending_state(
                state=state,
                checkpoint_kind="agent_graph",
                agent_name=agent_definition.name if agent_definition else "",
                action_hint=str(alert_data.get("handling_intent", "")).strip(),
            )
            serialized["approval_request"] = approval_request or {}
            serialized["route"] = "approval_required"
        return serialized

    async def _run_planner_graph(
        self,
        agent_definition,
        alert_data: dict[str, Any],
        history: list[dict[str, str]] | None = None,
        cancel_event=None,
    ) -> dict[str, Any]:
        from pydantic import BaseModel, Field
        from langchain_openai import ChatOpenAI
        from sentinelflow.agent.prompts import DEFAULT_COMMAND_SYSTEM_PROMPT, DEFAULT_ALERT_SYSTEM_PROMPT
        from langchain_core.messages import SystemMessage, HumanMessage

        class PlannerResult(BaseModel):
            strategy: str = Field(description="The strategy to handle the task: 'self_handle', 'finish', 'self_execute', 'delegate', 'workflow', or 'direct'")
            response: str = Field(description="Direct response to the user, if strategy is self_handle or finish", default="")
            worker: str = Field(description="Target sub-agent to delegate to, if strategy is delegate", default="")
            task_prompt: str = Field(description="The instructions to send to the worker, if strategy is delegate", default="")
            reason: str = Field(description="The internal reasoning for this decision.", default="")
            workflow_id: str = Field(description="Target workflow id if strategy is workflow", default="")

        config = load_runtime_config()
        effective_config = agent_definition.resolve_runtime_config(config) if agent_definition else config
        try:
            llm_instance = ChatOpenAI(**build_llm_client_kwargs(effective_config))
            llm = llm_instance.with_structured_output(PlannerResult)
        except Exception as exc:
            LOGGER.exception("Failed to initialize planner LLM.")
            raise RuntimeError("Planner 模型初始化失败，请检查模型配置并确认当前模型支持结构化输出。") from exc

        custom_prompt = agent_definition.prompt_for_mode(
            "agent_command" if alert_data.get("alert_source") == "human_command" else "agent_alert"
        ) if agent_definition else ""
        system_msg = SystemMessage(content=custom_prompt)
        
        is_human_command = alert_data.get("alert_source") == "human_command"
        if is_human_command:
            payload = str(alert_data.get("payload", ""))
            initial_msg = HumanMessage(content=f"请执行以下人工指令：{payload}")
        else:
            alert_json = json.dumps(alert_data, ensure_ascii=False, indent=2)
            initial_msg = HumanMessage(content=f"请分析并调度以下告警：\n\n```json\n{alert_json}\n```")

        messages = [system_msg] + self._build_history_messages(history or []) + [initial_msg]
        
        try:
            response_obj = await llm.ainvoke(messages)
            if hasattr(response_obj, "model_dump_json"):
                final_response = response_obj.model_dump_json()
            else:
                final_response = json.dumps(dict(response_obj), ensure_ascii=False)
        except Exception as exc:
            LOGGER.exception("Planner structured output invocation failed.")
            raise RuntimeError("Planner 结构化输出失败，请检查模型能力或输出格式配置。") from exc

        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            raise RuntimeError("用户已停止当前任务。")

        return {
            "final_response": final_response,
            "success": True,
            "route": "structured_planner",
            "messages": []
        }

    def _build_worker_catalog(self, workers: list) -> str:
        if not workers:
            return "（当前没有可用子 Agent）"
        items: list[str] = []
        for worker in workers:
            items.append(f"- name: {worker.name}\n  description: {worker.description or worker.name}")
        return "\n".join(items)

    def _with_alert_source_prompt(self, primary_agent, alert: dict[str, Any]):
        if primary_agent is None or getattr(primary_agent, "role", "") != "primary":
            return primary_agent
        if alert.get("alert_source") == "human_command":
            return primary_agent
        config = load_runtime_config()
        sources = list(getattr(config, "alert_sources", []) or [])
        if len(sources) <= 1:
            return primary_agent
        source_id = str(alert.get("alert_source_id", "")).strip()
        source_index = next((index for index, source in enumerate(sources) if source.id == source_id), 0)
        if source_index == 0:
            return primary_agent
        source_prompt = str(getattr(sources[source_index], "analysis_prompt", "")).strip()
        if not source_prompt:
            return primary_agent
        return replace(primary_agent, prompt_alert=source_prompt)

    def _build_primary_prompt(self, primary_agent, appendix_template: str, workers: list, alert_data: dict[str, Any] | None = None) -> str:
        mode_map = {
            PRIMARY_COMMAND_ORCHESTRATION_APPENDIX: "primary_orchestrate_command",
            PRIMARY_ALERT_ORCHESTRATION_APPENDIX: "primary_orchestrate_alert",
        }
        readable_skills, _ = self._resolve_skill_permissions(primary_agent)
        skill_catalog = load_skill_catalog(self.project_root / ".sentinelflow" / "plugins" / "skills", readable_skills)
        workflows = [
            workflow
            for workflow in list_agent_workflows(self.workflow_root)
            if workflow.enabled and is_workflow_allowed_for_alert_source(workflow, alert_data)
        ]
        return build_prompt(
            PromptBuildContext(
                base_prompt=primary_agent.prompt_for_mode(mode_map.get(appendix_template, "primary_orchestrate_alert")).strip() if primary_agent else "",
                mode=mode_map.get(appendix_template, "primary_orchestrate_alert"),
                skill_catalog=skill_catalog,
                worker_catalog=self._build_worker_catalog(workers),
                workflow_catalog=self._build_workflow_catalog(workflows),
            )
        )

    def _build_workflow_catalog(self, workflows: list) -> str:
        if not workflows:
            return "（当前没有可用 Agent Workflow）"
        items: list[str] = []
        for workflow in workflows:
            items.append(
                "\n".join(
                    [
                        f"- id: {workflow.id}",
                        f"  name: {workflow.name}",
                        f"  description: {workflow.description or workflow.name}",
                        f"  scenarios: {', '.join(workflow.scenarios) if workflow.scenarios else '未设置'}",
                        f"  selection_keywords: {', '.join(workflow.selection_keywords) if workflow.selection_keywords else '未设置'}",
                        f"  allowed_alert_sources: {', '.join(workflow.allowed_alert_source_ids) if workflow.allowed_alert_source_ids else '全部告警源'}",
                        f"  step_agents: {', '.join(step.agent for step in workflow.steps) if workflow.steps else '无步骤'}",
                    ]
                )
            )
        return "\n".join(items)

    def _build_command_planner_payload(self, command_text: str, step_results: list[dict[str, Any]]) -> dict[str, Any]:
        command_ref = f"PLAN-{uuid4().hex[:12].upper()}"
        return {
            "eventIds": extract_explicit_event_id_from_text(command_text),
            "command_ref": command_ref,
            "alert_name": "主 Agent 调度",
            "payload": json.dumps(
                {
                    "original_command": command_text,
                    "completed_steps": step_results,
                },
                ensure_ascii=False,
            ),
            "alert_source": "human_command",
        }

    def _build_command_self_execute_payload(self, command_text: str, step_results: list[dict[str, Any]]) -> dict[str, Any]:
        if not step_results:
            payload = command_text
        else:
            payload = json.dumps(
                {
                    "original_command": command_text,
                    "completed_steps": step_results,
                    "instruction": "请结合上面的原始任务和已完成步骤结果，必要时继续使用你自己的技能，并直接给用户最终回复。",
                },
                ensure_ascii=False,
            )
        command_ref = f"CMD-{uuid4().hex[:12].upper()}"
        return {
            "eventIds": extract_explicit_event_id_from_text(command_text),
            "command_ref": command_ref,
            "alert_name": "人工指令",
            "payload": payload,
            "alert_source": "human_command",
        }

    def _build_alert_planner_payload(self, alert: dict[str, Any], action_hint: str | None, step_results: list[dict[str, Any]]) -> dict[str, Any]:
        planner_alert = dict(alert)
        planner_alert["handling_intent"] = action_hint or ""
        planner_alert["payload"] = json.dumps(
            {
                "original_alert": alert,
                "handling_intent": action_hint or "",
                "completed_steps": step_results,
            },
            ensure_ascii=False,
        )
        return planner_alert

    def _build_alert_self_execute_payload(
        self,
        alert: dict[str, Any],
        action_hint: str | None,
        step_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not step_results:
            delegated_alert = dict(alert)
            if action_hint:
                delegated_alert["handling_intent"] = action_hint
            return delegated_alert
        delegated_alert = dict(alert)
        delegated_alert["handling_intent"] = action_hint or ""
        delegated_alert["payload"] = json.dumps(
            {
                "original_alert": alert,
                "handling_intent": action_hint or "",
                "completed_steps": step_results,
                "instruction": "请结合上面的原始告警和已完成步骤结果，必要时继续使用你自己的技能，并直接给出最终值班结论。",
            },
            ensure_ascii=False,
        )
        return delegated_alert

    def _compact_worker_result(
        self,
        worker_name: str,
        task_prompt: str,
        reason: str,
        worker_result: dict[str, Any],
        step_index: int,
    ) -> dict[str, Any]:
        return {
            "step": step_index,
            "worker_agent": worker_name,
            "task_prompt": task_prompt,
            "delegation_reason": reason,
            "route": worker_result.get("route", ""),
            "final_response": worker_result.get("final_response", ""),
            "tool_calls": worker_result.get("tool_calls", []),
            "messages": worker_result.get("messages", []),
            "success": worker_result.get("success", True),
            "disposition": worker_result.get("disposition", ""),
            "reason": worker_result.get("reason", ""),
            "evidence": worker_result.get("evidence", []),
            "actions": worker_result.get("actions", {}),
        }

    def _should_use_orchestrator(self, primary_agent, workers: list) -> bool:
        if primary_agent is None or primary_agent.role != "primary":
            return False
        if workers:
            return True
        if self.workflow_runner is None:
            return False
        return any(workflow.enabled for workflow in list_agent_workflows(self.workflow_root))

    def _resolve_worker_max_steps(self, primary_agent) -> int:
        raw_value = getattr(primary_agent, "worker_max_steps", 3)
        if not isinstance(raw_value, int):
            return 3
        return max(1, raw_value)

    def _resolve_worker_parallel_limit(self, primary_agent) -> int:
        raw_value = getattr(primary_agent, "worker_parallel_limit", 3)
        if not isinstance(raw_value, int):
            return 3
        return max(1, raw_value)

    def _resolve_agent_recursion_limit(self, agent_definition) -> int:
        return max(10, self._resolve_worker_max_steps(agent_definition) * 4 + 4)

    def _build_execution_context(
        self,
        *,
        execution_entry: str,
        scope_type: str,
        scope_ref: str,
        run_id: str | None = None,
        checkpoint_thread_id: str | None = None,
        checkpoint_ns: str = "agent_graph",
        parent_checkpoint_thread_id: str | None = None,
        parent_checkpoint_ns: str | None = None,
        parent_tool_call_id: str | None = None,
        approved_fingerprints: list[str] | None = None,
        rejected_fingerprints: list[str] | None = None,
        executed_skill_cache: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "run_id": (run_id or uuid4().hex).strip(),
            "execution_entry": execution_entry.strip(),
            "scope_type": scope_type.strip(),
            "scope_ref": scope_ref.strip(),
            "checkpoint_thread_id": (checkpoint_thread_id or uuid4().hex).strip(),
            "checkpoint_ns": checkpoint_ns.strip(),
            "parent_checkpoint_thread_id": (parent_checkpoint_thread_id or "").strip(),
            "parent_checkpoint_ns": (parent_checkpoint_ns or "").strip(),
            "parent_tool_call_id": (parent_tool_call_id or "").strip(),
            "approved_fingerprints": list(approved_fingerprints or []),
            "rejected_fingerprints": list(rejected_fingerprints or []),
            "executed_skill_cache": dict(executed_skill_cache or {}),
        }

    def evaluate_worker_result(self, worker_result: dict[str, Any]) -> tuple[bool, str | None]:
        if bool(worker_result.get("approval_pending")):
            return False, "子 Agent 等待技能审批。"

        final_response = str(worker_result.get("final_response", "")).strip()
        tool_calls = worker_result.get("tool_calls", [])
        messages = worker_result.get("messages", [])
        has_tool_error = False
        has_successful_tool_result = False
        has_successful_completion = False
        if isinstance(messages, list):
            for message in messages:
                if not isinstance(message, dict) or str(message.get("type", "")).strip() != "tool":
                    continue
                content = str(message.get("content", "")).strip()
                try:
                    parsed = json.loads(content)
                except Exception:
                    has_tool_error = True
                    break
                if self._is_resolved_approval_pending_tool_payload(parsed):
                    continue
                if self._tool_payload_is_successful(parsed):
                    has_successful_tool_result = True
                if self._tool_payload_has_successful_completion(parsed):
                    has_successful_completion = True
                if isinstance(parsed, dict) and (not parsed.get("success", True) or parsed.get("error")):
                    has_tool_error = True

        if has_successful_completion:
            return True, None

        has_action = bool(final_response or tool_calls or has_successful_tool_result)
        if not has_action:
            return False, "子 Agent 未返回有效结果。"
        if has_tool_error:
            return False, "子 Agent 执行过程中存在失败的工具调用。"
        return True, None

    def _is_resolved_approval_pending_tool_payload(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        if payload.get("approval_pending") is not True:
            return False
        error = str(payload.get("error", "") or "").strip()
        return "需要审批" in error or "审批" in error

    def _tool_payload_is_successful(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        return payload.get("success") is True and not bool(payload.get("error"))

    def _tool_payload_has_successful_completion(self, payload: Any) -> bool:
        if not self._tool_payload_is_successful(payload):
            return False
        data = payload.get("data", {})
        data = data if isinstance(data, dict) else {}
        status = str(data.get("status", payload.get("status", ""))).strip()
        return status in {"4", "6"}

    def _approval_payload_has_successful_completion(self, payload: dict[str, Any]) -> bool:
        if bool(payload.get("success")):
            return True
        final_facts = payload.get("final_facts", {})
        if isinstance(final_facts, dict):
            task_outcome = final_facts.get("task_outcome", {})
            if isinstance(task_outcome, dict) and task_outcome.get("success") is True:
                return True
        for key in ("effective_closure_step", "closure_step"):
            closure_step = payload.get(key, {})
            if isinstance(closure_step, dict) and closure_step.get("attempted") is True and closure_step.get("success") is True:
                return True
        if self._payload_has_successful_closure_summary(payload):
            return True
        return False

    def _payload_has_successful_closure_summary(self, payload: dict[str, Any]) -> bool:
        for item in payload.get("tool_calls_summary", []) if isinstance(payload.get("tool_calls_summary", []), list) else []:
            if self._tool_summary_has_successful_closure(item):
                return True
        for worker_result in payload.get("worker_results", []) if isinstance(payload.get("worker_results", []), list) else []:
            if isinstance(worker_result, dict) and self._payload_has_successful_closure_summary(worker_result):
                return True
        for workflow_run in payload.get("workflow_runs", []) if isinstance(payload.get("workflow_runs", []), list) else []:
            if isinstance(workflow_run, dict) and self._payload_has_successful_closure_summary(workflow_run):
                return True
        return False

    def _tool_summary_has_successful_closure(self, item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        name = str(item.get("name", "")).strip()
        if name not in {"execute_skill", "execute_skill_no_args"}:
            return False
        args = item.get("args", {})
        args = args if isinstance(args, dict) else {}
        skill_name = str(args.get("skill_name", "")).strip()
        if not skill_name or not (self._is_closure_skill_name(skill_name) or self._completion_policy_marks_closure(skill_name)):
            return False
        result_summary = item.get("result_summary", {})
        result_summary = result_summary if isinstance(result_summary, dict) else {}
        if result_summary.get("success") is False:
            return False
        data = result_summary.get("data", {})
        data = data if isinstance(data, dict) else {}
        status = str(data.get("status", result_summary.get("status", ""))).strip()
        return status in {"4", "6"} and result_summary.get("error") in (None, "")

    def _normalize_graph_state_keys(self, state: dict[str, Any]) -> dict[str, Any]:
        if "graph_checkpoint_ns" not in state and "checkpoint_ns" in state:
            state["graph_checkpoint_ns"] = state.get("checkpoint_ns")
        return state

    def _extract_pending_tool_message(self, state: dict[str, Any]) -> tuple[dict[str, Any], str] | tuple[None, str]:
        messages = list(state.get("messages", []))
        for msg in reversed(messages):
            if getattr(msg, "type", "") != "tool":
                continue
            content = getattr(msg, "content", "")
            if not isinstance(content, str):
                continue
            try:
                payload = json.loads(content)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict) or not payload.get("approval_pending"):
                continue
            request = payload.get("approval_request", {})
            if not isinstance(request, dict):
                continue
            return request, str(getattr(msg, "tool_call_id", "")).strip()
        return None, ""

    def _copy_tool_message_with_content(self, msg: Any, content: str) -> Any:
        if isinstance(msg, dict):
            return {**msg, "content": content}
        if hasattr(msg, "model_copy"):
            try:
                return msg.model_copy(update={"content": content})
            except Exception:
                pass
        if hasattr(msg, "copy"):
            try:
                return msg.copy(update={"content": content})
            except Exception:
                pass
        try:
            cloned = copy.deepcopy(msg)
            setattr(cloned, "content", content)
            return cloned
        except Exception:
            pass
        try:
            cloned = copy.deepcopy(msg)
            object.__setattr__(cloned, "content", content)
            return cloned
        except Exception:
            pass
        try:
            from langchain_core.messages import ToolMessage
            return ToolMessage(
                content=content,
                tool_call_id=str(getattr(msg, "tool_call_id", "")).strip() or "tool-call",
            )
        except ModuleNotFoundError:
            return msg

    def _replace_tool_message_content(self, state: dict[str, Any], tool_call_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        serialized = json.dumps(payload, ensure_ascii=False)
        messages = list(state.get("messages", []))
        normalized_tool_call_id = str(tool_call_id or "").strip()
        replaced = False
        for index in range(len(messages) - 1, -1, -1):
            msg = messages[index]
            msg_type = str(msg.get("type", "") if isinstance(msg, dict) else getattr(msg, "type", "")).strip()
            if msg_type != "tool":
                continue
            if normalized_tool_call_id:
                current_tool_call_id = str(
                    msg.get("tool_call_id", "") if isinstance(msg, dict) else getattr(msg, "tool_call_id", "")
                ).strip()
                if current_tool_call_id != normalized_tool_call_id:
                    continue
            else:
                content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
                if isinstance(content, str):
                    try:
                        decoded = json.loads(content)
                    except json.JSONDecodeError:
                        decoded = None
                    if not (isinstance(decoded, dict) and decoded.get("approval_pending")):
                        continue
                else:
                    continue
            messages[index] = self._copy_tool_message_with_content(msg, serialized)
            replaced = True
            break
        if not replaced and not normalized_tool_call_id:
            for index in range(len(messages) - 1, -1, -1):
                msg = messages[index]
                msg_type = str(msg.get("type", "") if isinstance(msg, dict) else getattr(msg, "type", "")).strip()
                if msg_type != "tool":
                    continue
                messages[index] = self._copy_tool_message_with_content(msg, serialized)
                break
        state["messages"] = messages
        state["approval_pending"] = False
        state["approval_request"] = {}
        return state

    def _replace_parent_tool_result(
        self,
        state: dict[str, Any],
        tool_call_id: str,
        approval_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        messages = list(state.get("messages", []))
        for index in range(len(messages) - 1, -1, -1):
            msg = messages[index]
            msg_type = str(msg.get("type", "") if isinstance(msg, dict) else getattr(msg, "type", "")).strip()
            if msg_type != "tool":
                continue
            current_tool_call_id = str(
                msg.get("tool_call_id", "") if isinstance(msg, dict) else getattr(msg, "tool_call_id", "")
            ).strip()
            if tool_call_id and current_tool_call_id != tool_call_id:
                continue
            content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
            if not isinstance(content, str):
                break
            try:
                decoded = json.loads(content)
            except json.JSONDecodeError:
                break
            if not isinstance(decoded, dict) or decoded.get("mode") != "parallel":
                break
            results = list(decoded.get("results", []) or [])
            replaced = False
            for result_index, item in enumerate(results):
                if not isinstance(item, dict):
                    continue
                approval_request = item.get("approval_request", {})
                if isinstance(approval_request, dict) and str(approval_request.get("approval_id", "")).strip() == approval_id:
                    results[result_index] = payload
                    replaced = True
                    break
            if not replaced:
                for result_index in range(len(results) - 1, -1, -1):
                    item = results[result_index]
                    if isinstance(item, dict) and item.get("approval_pending"):
                        results[result_index] = payload
                        replaced = True
                        break
            if not replaced:
                break
            decoded["results"] = results
            next_pending: dict[str, Any] = {}
            for item in results:
                if not isinstance(item, dict):
                    continue
                approval_request = item.get("approval_request", {})
                if item.get("approval_pending") and isinstance(approval_request, dict) and approval_request:
                    next_pending = approval_request
                    break
            decoded["approval_pending"] = bool(next_pending)
            decoded["approval_request"] = next_pending
            decoded["success"] = False if next_pending else any(bool(item.get("success")) for item in results if isinstance(item, dict))
            decoded["error"] = "并行子 Agent 等待技能审批。" if next_pending else None
            messages[index] = self._copy_tool_message_with_content(msg, json.dumps(decoded, ensure_ascii=False))
            state["messages"] = messages
            state["approval_pending"] = bool(next_pending)
            state["approval_request"] = next_pending
            return state
        return self._replace_tool_message_content(state, tool_call_id, payload)

    def _persist_pending_state(
        self,
        *,
        state: dict[str, Any],
        checkpoint_kind: str,
        agent_name: str,
        action_hint: str,
    ) -> dict[str, Any] | None:
        request, tool_call_id = self._extract_pending_tool_message(state)
        if request is None:
            return None
        run_id = str(state.get("run_id", request.get("run_id", ""))).strip()
        scope_type = str(state.get("scope_type", request.get("scope_type", ""))).strip()
        scope_ref = str(state.get("scope_ref", request.get("scope_ref", ""))).strip()
        checkpoint_thread_id = str(state.get("checkpoint_thread_id", request.get("checkpoint_thread_id", ""))).strip() or uuid4().hex
        checkpoint_ns = str(state.get("graph_checkpoint_ns", state.get("checkpoint_ns", request.get("checkpoint_ns", checkpoint_kind)))).strip() or checkpoint_kind

        self.approval_service.save_checkpoint(
            checkpoint_thread_id=checkpoint_thread_id,
            checkpoint_ns=checkpoint_ns,
            checkpoint_kind=checkpoint_kind,
            run_id=run_id,
            scope_type=scope_type,
            scope_ref=scope_ref,
            agent_name=agent_name,
            execution_entry=str(state.get("execution_entry", "")).strip(),
            action_hint=action_hint,
            state_payload=serialize_graph_state(state),
        )
        approval_id = str(request.get("approval_id", "")).strip()
        if approval_id:
            record = self.approval_service.get_by_id(approval_id)
            if record is not None and record.status == "pending":
                if not record.parent_checkpoint_thread_id and str(state.get("checkpoint_thread_id", "")).strip():
                    updated = self.approval_service.update_parent_context(
                        approval_id,
                        parent_checkpoint_thread_id=str(state.get("checkpoint_thread_id", "")).strip(),
                        parent_checkpoint_ns=str(state.get("graph_checkpoint_ns", state.get("checkpoint_ns", checkpoint_kind))).strip(),
                        parent_tool_call_id=tool_call_id,
                    )
                    if updated is not None:
                        record = updated
                request["approval_id"] = record.approval_id
                return self.approval_service.serialize_approval(record)
        record = self.approval_service.create_or_reuse_pending(
            run_id=run_id,
            scope_type=scope_type,
            scope_ref=scope_ref,
            skill_name=str(request.get("skill_name", "")).strip(),
            arguments=request.get("arguments", {}) if isinstance(request.get("arguments"), dict) else {},
            approval_required=True,
            checkpoint_thread_id=checkpoint_thread_id,
            checkpoint_ns=checkpoint_ns,
            tool_call_id=tool_call_id,
            parent_checkpoint_thread_id=str(state.get("parent_checkpoint_thread_id", "")).strip(),
            parent_checkpoint_ns=str(state.get("parent_checkpoint_ns", "")).strip(),
            parent_tool_call_id=str(state.get("parent_tool_call_id", "")).strip(),
            message=str(request.get("message", "")).strip(),
        )
        return self.approval_service.serialize_approval(record)

    def _approved_tool_payload(self, approval, state: dict[str, Any]) -> dict[str, Any]:
        context = {
            "event_id_ref": state.get("event_id_ref", ""),
            "alert_data": state.get("alert_data", {}),
        }
        try:
            result = self.skill_runtime.execute_skill(approval.skill_name, approval.arguments, context)
        except Exception as exc:
            return {
                "success": False,
                "data": {},
                "error": f"审批通过后执行 Skill 失败：{exc}",
            }
        payload = result.data if isinstance(result.data, dict) else {"result": result.data}
        return {
            "success": result.success,
            "data": payload,
            "error": result.error,
        }

    def _rejected_tool_payload(self, approval) -> dict[str, Any]:
        return {
            "success": False,
            "data": {
                "approval_rejected": True,
                "skill_name": approval.skill_name,
                "arguments": approval.arguments,
            },
            "error": "用户拒绝执行需要审批的 Skill。",
        }

    def _extract_tool_calls_from_messages(self, messages: list[Any]) -> list[dict[str, Any]]:
        tool_calls: list[dict[str, Any]] = []
        for msg in messages:
            candidates = []
            if isinstance(msg, dict):
                candidates = list(msg.get("tool_calls", []) or [])
                data = msg.get("data", {})
                if isinstance(data, dict):
                    candidates.extend(list(data.get("tool_calls", []) or []))
            else:
                candidates = list(getattr(msg, "tool_calls", None) or [])
            for call in candidates:
                if isinstance(call, dict):
                    tool_calls.append(call)
        return tool_calls

    def _dedupe_tool_calls(self, tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for call in tool_calls:
            marker = str(call.get("id", "")).strip()
            if not marker:
                try:
                    marker = json.dumps(call, ensure_ascii=False, sort_keys=True, default=str)
                except TypeError:
                    marker = str(call)
            if marker in seen:
                continue
            seen.add(marker)
            deduped.append(call)
        return deduped

    def _build_worker_wrapped_result(self, checkpoint: dict[str, Any], state: dict[str, Any], graph_result: dict[str, Any]) -> dict[str, Any]:
        delegated_task_prompt = str((state.get("alert_data", {}) or {}).get("delegated_task_prompt", ""))
        graph_messages = list(graph_result.get("messages", []) or [])
        checkpoint_messages = list(state.get("messages", []) or [])
        tool_calls = self._dedupe_tool_calls(
            [item for item in graph_result.get("tool_calls", []) if isinstance(item, dict)]
            + self._extract_tool_calls_from_messages(checkpoint_messages)
        )
        skills_used = [
            str(item.get("name", "")).strip()
            for item in tool_calls
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        ]
        step_idx = 0
        checkpoint_thread_id = str(checkpoint.get("checkpoint_thread_id", "")).strip()
        if ":worker:" in checkpoint_thread_id:
            try:
                step_idx = int(checkpoint_thread_id.rsplit(":", 1)[1])
            except (ValueError, IndexError):
                step_idx = 0
        final_text = str(graph_result.get("final_response", ""))
        success, error = self.evaluate_worker_result(graph_result)
        alert_data = state.get("alert_data", {}) if isinstance(state.get("alert_data", {}), dict) else {}
        context_manifest = graph_result.get("context_manifest") if isinstance(graph_result.get("context_manifest"), dict) else alert_data.get("context_manifest", {})
        if not isinstance(context_manifest, dict):
            context_manifest = build_context_manifest(
                current_goal=delegated_task_prompt,
                entry_type=str(state.get("execution_entry", "")).strip(),
                original_input=alert_data,
                current_task_prompt=delegated_task_prompt,
                model_summary=graph_result.get("key_facts", {}),
            )
        tool_result_facts: dict[str, Any] = {}
        source_messages = graph_messages + checkpoint_messages
        for message in source_messages:
            if isinstance(message, dict):
                if str(message.get("type", "")).strip() != "tool":
                    continue
                content = str(message.get("content", "")).strip()
            else:
                if str(getattr(message, "type", "")).strip() != "tool":
                    continue
                content = str(getattr(message, "content", "")).strip()
            try:
                parsed_content = json.loads(content)
            except json.JSONDecodeError:
                parsed_content = content
            tool_result_facts = extract_key_facts(tool_result_facts, parsed_content)
        tool_calls_summary = summarize_tool_calls(
            tool_calls,
            tool_messages=source_messages,
        )
        wrapped = {
            "step": step_idx or 1,
            "worker": str(checkpoint.get("agent_name", "")).strip() or str(graph_result.get("agent_name", "")).strip(),
            "task_prompt": delegated_task_prompt,
            "final_response": final_text,
            "skills_used": skills_used,
            "tool_calls_summary": tool_calls_summary,
            "key_facts": extract_key_facts(graph_result.get("key_facts", {}), tool_calls, final_text, tool_result_facts),
            "context_manifest": context_manifest,
            "context_warnings": list(context_manifest.get("context_warnings", []) or []),
            "success": success,
            "error": error,
        }
        if graph_result.get("approval_pending"):
            wrapped["approval_pending"] = True
            wrapped["approval_request"] = graph_result.get("approval_request", {})
        return compact_worker_result_for_llm(wrapped)

    def _approval_resume_failed_result(self, error: str, approval) -> dict[str, Any]:
        return {
            "success": False,
            "route": "approval_resume_failed",
            "error": error,
            "data": {
                "approval": self.approval_service.serialize_approval(approval),
            },
        }

    def _reload_checkpoint_for_resume(
        self,
        checkpoint_thread_id: str,
        *,
        approval,
        decision: str,
        stage: str,
        error_message: str,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        checkpoint = self.approval_service.load_checkpoint(checkpoint_thread_id)
        if checkpoint is not None:
            return checkpoint, None
        self._record_audit(
            "approval_resume_failed",
            error_message,
            {
                "approval_id": str(getattr(approval, "approval_id", "")).strip(),
                "checkpoint_thread_id": checkpoint_thread_id,
                "decision": decision,
                "stage": stage,
            },
        )
        return None, self._approval_resume_failed_result(error_message, approval)

    async def _resume_workflow_checkpoint(
        self,
        checkpoint: dict[str, Any],
        step_result: dict[str, Any],
        approval,
    ) -> dict[str, Any]:
        if self.workflow_runner is None:
            return {
                "success": False,
                "route": "approval_resume_failed",
                "error": "Workflow 运行时未初始化，无法恢复审批后的 Workflow。",
                "data": {},
            }
        return await self.workflow_runner.resume_checkpoint(checkpoint, step_result, approval)

    def _build_approval_resolution_result(self, result: dict[str, Any], approval_record) -> dict[str, Any]:
        payload = dict(result) if isinstance(result, dict) else {}
        resolved_approval = self.approval_service.serialize_approval(approval_record)
        approval_pending = bool(payload.get("approval_pending"))
        successful_completion = self._approval_payload_has_successful_completion(payload)
        route = str(payload.get("route", "")).strip() or ("approval_required" if approval_pending else "approval_resolved")
        data = {
            **payload,
            "resolved_approval": resolved_approval,
        }
        if not approval_pending:
            data["approval"] = resolved_approval
        return {
            "success": not approval_pending and (successful_completion or bool(payload.get("success", True))),
            "route": route,
            "data": data,
            "error": None if approval_pending or successful_completion else payload.get("error"),
        }

    async def _resume_saved_checkpoint(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        state = self._normalize_graph_state_keys(deserialize_graph_state(checkpoint.get("state", {})))
        checkpoint_kind = str(checkpoint.get("checkpoint_kind", "")).strip()
        agent_name = str(checkpoint.get("agent_name", "")).strip() or None
        agent_definition = resolve_default_agent(self.agent_root, agent_name)
        config = load_runtime_config()
        effective_config = agent_definition.resolve_runtime_config(config) if agent_definition else config

        if checkpoint_kind == "agent_graph":
            readable_skills, executable_skills = self._resolve_skill_permissions(agent_definition)
            graph = build_agent_graph(
                self.project_root,
                self.skill_runtime,
                self.approval_service,
                effective_config,
                enable_read_skill_document=bool(readable_skills),
                enable_execute_skill=bool(executable_skills),
                executable_skill_names=executable_skills,
            )
            resumed_state = await graph.ainvoke(
                state,
                {"recursion_limit": self._resolve_agent_recursion_limit(agent_definition)},
            )
            graph_result = self._serialize_graph_result(
                str((state.get("alert_data", {}) or {}).get("payload") or (state.get("alert_data", {}) or {}).get("eventIds") or "").strip(),
                resumed_state,
                agent_definition.name if agent_definition else "",
            )
            if resumed_state.get("approval_pending"):
                graph_result["approval_pending"] = True
                graph_result["approval_request"] = self._persist_pending_state(
                    state=resumed_state,
                    checkpoint_kind="agent_graph",
                    agent_name=agent_definition.name if agent_definition else "",
                    action_hint=str(checkpoint.get("action_hint", "")).strip(),
                ) or {}
                graph_result["route"] = "approval_required"
                return graph_result
            if str(state.get("parent_checkpoint_thread_id", "")).strip():
                return self._build_worker_wrapped_result(checkpoint, state, graph_result)
            if str(checkpoint.get("execution_entry", "")).strip() == "manual_alert":
                return await self._serialize_alert_result(
                    state.get("alert_data", {}) or {},
                    graph_result,
                    str(checkpoint.get("action_hint", "")).strip() or None,
                    agent_definition=agent_definition,
                    effective_config=effective_config,
                )
            return graph_result

        if checkpoint_kind == "orchestrator_graph":
            from sentinelflow.agent.orchestrator_graph import build_orchestrator_graph

            workers = self._resolve_worker_candidates(agent_definition, entry_type="conversation" if str(checkpoint.get("execution_entry", "")).strip() == "conversation" else "alert")
            orchestrator = build_orchestrator_graph(
                agent_definition,
                workers,
                self.project_root,
                self.skill_runtime,
                self.approval_service,
                effective_config,
                alert_data=state.get("alert_data", {}) or {},
                cancel_event=None,
                workflow_root=self.workflow_root,
                workflow_runner=self.workflow_runner,
            )
            resumed_state = await orchestrator.ainvoke(
                state,
                {"recursion_limit": max(18, self._resolve_worker_max_steps(agent_definition) * 6 + 10)},
            )
            graph_result = self._serialize_orchestrator_result(
                resumed_state,
                state.get("alert_data", {}) or {},
                agent_definition,
                str(checkpoint.get("action_hint", "")).strip() or None,
            )
            if resumed_state.get("approval_pending"):
                graph_result["approval_pending"] = True
                graph_result["approval_request"] = self._persist_pending_state(
                    state=resumed_state,
                    checkpoint_kind="orchestrator_graph",
                    agent_name=agent_definition.name if agent_definition else "",
                    action_hint=str(checkpoint.get("action_hint", "")).strip(),
                ) or {}
                graph_result["route"] = "approval_required"
                return graph_result
            if str(checkpoint.get("execution_entry", "")).strip() == "manual_alert":
                return await self._serialize_alert_result(
                    state.get("alert_data", {}) or {},
                    graph_result,
                    str(checkpoint.get("action_hint", "")).strip() or None,
                    agent_definition=agent_definition,
                    effective_config=effective_config,
                )
            return graph_result

        return {
            "success": False,
            "route": "approval_resume_failed",
            "error": f"不支持的 checkpoint 类型：{checkpoint_kind}",
            "data": {},
        }

    async def resolve_skill_approval(
        self,
        approval_id: str,
        decision: str,
        *,
        status_callback: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        approval = self.approval_service.get_by_id(approval_id)
        if approval is None:
            return {"success": False, "route": "approval_not_found", "error": "找不到待审批记录。", "data": {}}
        if approval.status != "pending":
            return {"success": False, "route": "approval_not_pending", "error": "该审批已处理。", "data": {"approval": self.approval_service.serialize_approval(approval)}}
        if status_callback:
            status_callback("正在读取审批断点...")
        checkpoint = self.approval_service.load_checkpoint(approval.checkpoint_thread_id)
        if checkpoint is None:
            return {"success": False, "route": "approval_checkpoint_missing", "error": "审批断点不存在或已丢失。", "data": {}}

        decision_status = "approved" if decision == "approve" else "rejected" if decision == "reject" else ""
        if not decision_status:
            return {"success": False, "route": "approval_invalid_decision", "error": f"不支持的审批动作：{decision}", "data": {}}
        approval_record = self.approval_service.consume_pending_decision(approval_id, decision_status)
        if approval_record is None:
            latest = self.approval_service.get_by_id(approval_id)
            return {
                "success": False,
                "route": "approval_not_pending",
                "error": "该审批已处理。",
                "data": {"approval": self.approval_service.serialize_approval(latest or approval)},
            }

        state = deserialize_graph_state(checkpoint.get("state", {}))
        if decision == "approve":
            if status_callback:
                status_callback(f"已批准 Skill「{approval.skill_name}」，正在执行...")
            tool_payload = self._approved_tool_payload(approval, state)
            approved = set(state.get("approved_fingerprints", []) or [])
            approved.add(approval.arguments_fingerprint)
            state["approved_fingerprints"] = list(approved)
        elif decision == "reject":
            if status_callback:
                status_callback(f"已拒绝 Skill「{approval.skill_name}」，正在恢复推理...")
            tool_payload = self._rejected_tool_payload(approval)
            rejected = set(state.get("rejected_fingerprints", []) or [])
            rejected.add(
                SkillApprovalService.build_skill_arguments_key(
                    approval.skill_name,
                    approval.arguments_fingerprint,
                )
            )
            state["rejected_fingerprints"] = list(rejected)
        else:
            return {"success": False, "route": "approval_invalid_decision", "error": f"不支持的审批动作：{decision}", "data": {}}

        state = self._replace_tool_message_content(state, approval.tool_call_id, tool_payload)
        if status_callback:
            status_callback("正在写入审批结果并恢复执行...")
        self.approval_service.save_checkpoint(
            checkpoint_thread_id=approval.checkpoint_thread_id,
            checkpoint_ns=checkpoint.get("checkpoint_ns", ""),
            checkpoint_kind=checkpoint.get("checkpoint_kind", ""),
            run_id=checkpoint.get("run_id", ""),
            scope_type=checkpoint.get("scope_type", ""),
            scope_ref=checkpoint.get("scope_ref", ""),
            agent_name=checkpoint.get("agent_name", ""),
            execution_entry=checkpoint.get("execution_entry", ""),
            action_hint=checkpoint.get("action_hint", ""),
            state_payload=serialize_graph_state(state),
        )
        current_checkpoint, resume_error = self._reload_checkpoint_for_resume(
            approval.checkpoint_thread_id,
            approval=approval_record,
            decision=decision,
            stage="child_checkpoint_reload",
            error_message="审批结果已写入，但重新加载 Agent 断点失败，无法继续恢复执行。",
        )
        if resume_error is not None:
            return resume_error
        if status_callback:
            status_callback("正在恢复 Agent 图状态...")
        result = await self._resume_saved_checkpoint(current_checkpoint)

        parent_checkpoint_id = approval.parent_checkpoint_thread_id.strip()
        if parent_checkpoint_id and not result.get("approval_pending"):
            parent_checkpoint, resume_error = self._reload_checkpoint_for_resume(
                parent_checkpoint_id,
                approval=approval_record,
                decision=decision,
                stage="parent_checkpoint_load",
                error_message="子 Agent 已恢复，但重新加载上层断点失败，无法继续恢复编排。",
            )
            if resume_error is not None:
                return resume_error
            if status_callback:
                status_callback("正在恢复上层编排状态...")
            parent_kind = str(parent_checkpoint.get("checkpoint_kind", "")).strip()
            if parent_kind == "workflow_runner":
                result = await self._resume_workflow_checkpoint(parent_checkpoint, result, approval)
                workflow_state = parent_checkpoint.get("state", {}) if isinstance(parent_checkpoint.get("state", {}), dict) else {}
                workflow_parent_id = str(workflow_state.get("parent_checkpoint_thread_id", "")).strip()
                if workflow_parent_id and not result.get("approval_pending"):
                    workflow_parent, resume_error = self._reload_checkpoint_for_resume(
                        workflow_parent_id,
                        approval=approval_record,
                        decision=decision,
                        stage="workflow_parent_checkpoint_load",
                        error_message="Workflow 已恢复，但重新加载 Workflow 上层断点失败，无法继续恢复编排。",
                    )
                    if resume_error is not None:
                        return resume_error
                    workflow_parent_state = deserialize_graph_state(workflow_parent.get("state", {}))
                    workflow_parent_state = self._replace_parent_tool_result(
                        workflow_parent_state,
                        str(workflow_state.get("parent_tool_call_id", "")).strip(),
                        approval.approval_id,
                        result,
                    )
                    self.approval_service.save_checkpoint(
                        checkpoint_thread_id=workflow_parent_id,
                        checkpoint_ns=workflow_parent.get("checkpoint_ns", ""),
                        checkpoint_kind=workflow_parent.get("checkpoint_kind", ""),
                        run_id=workflow_parent.get("run_id", ""),
                        scope_type=workflow_parent.get("scope_type", ""),
                        scope_ref=workflow_parent.get("scope_ref", ""),
                        agent_name=workflow_parent.get("agent_name", ""),
                        execution_entry=workflow_parent.get("execution_entry", ""),
                        action_hint=workflow_parent.get("action_hint", ""),
                        state_payload=serialize_graph_state(workflow_parent_state),
                    )
                    workflow_parent_checkpoint, resume_error = self._reload_checkpoint_for_resume(
                        workflow_parent_id,
                        approval=approval_record,
                        decision=decision,
                        stage="workflow_parent_checkpoint_reload",
                        error_message="Workflow 上层断点已更新，但重新加载失败，无法继续恢复编排。",
                    )
                    if resume_error is not None:
                        return resume_error
                    result = await self._resume_saved_checkpoint(workflow_parent_checkpoint)
                return self._build_approval_resolution_result(result, approval_record)
            parent_state = deserialize_graph_state(parent_checkpoint.get("state", {}))
            wrapped_result = result
            if current_checkpoint.get("checkpoint_kind") == "agent_graph":
                wrapped_result = self._build_worker_wrapped_result(current_checkpoint, state, result)
            approved = set(parent_state.get("approved_fingerprints", []) or [])
            rejected = set(parent_state.get("rejected_fingerprints", []) or [])
            if decision == "approve":
                approved.add(approval.arguments_fingerprint)
            else:
                rejected.add(
                    SkillApprovalService.build_skill_arguments_key(
                        approval.skill_name,
                        approval.arguments_fingerprint,
                    )
                )
            parent_state["approved_fingerprints"] = list(approved)
            parent_state["rejected_fingerprints"] = list(rejected)
            parent_state = self._replace_parent_tool_result(parent_state, approval.parent_tool_call_id, approval.approval_id, wrapped_result)
            self.approval_service.save_checkpoint(
                checkpoint_thread_id=parent_checkpoint_id,
                checkpoint_ns=parent_checkpoint.get("checkpoint_ns", ""),
                checkpoint_kind=parent_checkpoint.get("checkpoint_kind", ""),
                run_id=parent_checkpoint.get("run_id", ""),
                scope_type=parent_checkpoint.get("scope_type", ""),
                scope_ref=parent_checkpoint.get("scope_ref", ""),
                agent_name=parent_checkpoint.get("agent_name", ""),
                execution_entry=parent_checkpoint.get("execution_entry", ""),
                action_hint=parent_checkpoint.get("action_hint", ""),
                state_payload=serialize_graph_state(parent_state),
            )
            parent_resume_checkpoint, resume_error = self._reload_checkpoint_for_resume(
                parent_checkpoint_id,
                approval=approval_record,
                decision=decision,
                stage="parent_checkpoint_reload",
                error_message="上层断点已更新，但重新加载失败，无法继续恢复编排。",
            )
            if resume_error is not None:
                return resume_error
            result = await self._resume_saved_checkpoint(parent_resume_checkpoint)

        if status_callback:
            status_callback("正在整理审批恢复结果...")
        return self._build_approval_resolution_result(result, approval_record)

    def _serialize_orchestrator_result(
        self,
        final_state: dict[str, Any],
        alert_data: dict[str, Any],
        primary_agent,
        action_hint: str | None,
    ) -> dict[str, Any]:
        """Deserialize the completed OrchestratorState into a result dict."""
        messages = final_state.get("messages", [])

        # Final supervisor response = last AI message with non-empty content
        from sentinelflow.agent.schemas import stringify_llm_content

        final_text = ""
        for msg in reversed(messages):
            msg_type = getattr(msg, "type", "")
            content = stringify_llm_content(getattr(msg, "content", ""))
            if msg_type == "ai" and content.strip():
                final_text = _clean_model_text(content)
                break

        # Worker results surfaced from ToolMessages
        worker_results: list[dict[str, Any]] = []
        workflow_runs: list[dict[str, Any]] = []
        for msg_index, msg in enumerate(messages):
            try:
                from langchain_core.messages import ToolMessage
                if not isinstance(msg, ToolMessage):
                    continue
            except ModuleNotFoundError:
                pass
            content = getattr(msg, "content", "")
            if not isinstance(content, str):
                continue
            try:
                result = json.loads(content)
                if isinstance(result, dict) and "worker" in result:
                    worker_results.append({**result, "_message_index": msg_index})
                elif isinstance(result, dict) and result.get("workflow_id"):
                    workflow_runs.append({**result, "_message_index": msg_index})
                elif isinstance(result, dict) and result.get("mode") == "parallel" and isinstance(result.get("results"), list):
                    for item in result["results"]:
                        if isinstance(item, dict) and "worker" in item:
                            worker_results.append({**item, "_message_index": msg_index})
            except (json.JSONDecodeError, TypeError):
                pass

        # Tool calls summary for upstream compatibility
        tool_calls: list[dict[str, Any]] = []
        for msg in messages:
            for tc in (getattr(msg, "tool_calls", None) or []):
                if isinstance(tc, dict):
                    tool_calls.append(tc)

        # Serialized message list
        serialized_messages: list[dict[str, Any]] = []
        for msg in messages:
            msg_type = getattr(msg, "type", msg.__class__.__name__.lower())
            content = getattr(msg, "content", "")
            if isinstance(content, str):
                content = _clean_model_text(content)
            item: dict[str, Any] = {"type": msg_type, "content": content}
            if getattr(msg, "tool_calls", None):
                item["tool_calls"] = msg.tool_calls
            if getattr(msg, "name", None):
                item["name"] = msg.name
            serialized_messages.append(item)

        return {
            "source": str(alert_data.get("payload") or alert_data.get("eventIds") or "").strip(),
            "agent_name": primary_agent.name if primary_agent else "",
            "final_response": final_text,
            "messages": serialized_messages,
            "tool_calls": tool_calls,
            "event_id_ref": str(alert_data.get("eventIds", "")).strip(),
            "orchestrated": True,
            "orchestration_strategy": "subgraph_supervisor",
            "primary_agent": primary_agent.name if primary_agent else "",
            "worker_results": worker_results,
            "workflow_runs": workflow_runs,
            "worker_agent": worker_results[-1]["worker"] if worker_results else "",
            "context_manifest": alert_data.get("context_manifest", {}) if isinstance(alert_data.get("context_manifest", {}), dict) else {},
            "context_warnings": list(alert_data.get("context_warnings", []) or []),
            "conversation_context_policy": alert_data.get("conversation_context_policy", {}) if isinstance(alert_data.get("conversation_context_policy", {}), dict) else {},
            "success": bool(final_text),
        }

    async def _orchestrate_command(
        self,
        primary_agent,
        workers: list,
        command_text: str,
        history: list[dict[str, str]] | None,
        context_policy: dict[str, Any] | None = None,
        cancel_event=None,
        status_callback: Callable[[str], None] | None = None,
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from sentinelflow.agent.orchestrator_graph import build_orchestrator_graph

        config = load_runtime_config()
        effective_config = primary_agent.resolve_runtime_config(config)
        max_steps = self._resolve_worker_max_steps(primary_agent)
        parallel_limit = self._resolve_worker_parallel_limit(primary_agent)
        readable_skills, executable_skills = self._resolve_skill_permissions(primary_agent)
        command_ref = f"CMD-{uuid4().hex[:12].upper()}"
        alert_data = {
            "eventIds": extract_explicit_event_id_from_text(command_text),
            "command_ref": command_ref,
            "alert_name": "人工指令",
            "payload": command_text,
            "alert_source": "human_command",
            "_primary_readable_skills": readable_skills,
            "_primary_executable_skills": executable_skills,
            "_primary_worker_parallel_limit": parallel_limit,
            "conversation_context_policy": dict(context_policy or {}),
        }
        context_manifest = build_context_manifest(
            current_goal=command_text,
            entry_type="conversation",
            current_step={"agent": primary_agent.name, "role": "primary"},
            original_input=alert_data,
            current_task_prompt=command_text,
            conversation_history=list(history or []),
            current_goal_meta={"conversation_context_policy": dict(context_policy or {})},
        )
        alert_data["context_manifest"] = context_manifest
        alert_data["context_warnings"] = list(context_manifest.get("context_warnings", []) or [])
        system_prompt = self._build_primary_prompt(
            primary_agent, PRIMARY_COMMAND_ORCHESTRATION_APPENDIX, workers
        )
        if status_callback:
            status_callback("正在构建多 Agent 编排图...")

        orchestrator = build_orchestrator_graph(
            primary_agent,
            workers,
            self.project_root,
            self.skill_runtime,
            self.approval_service,
            effective_config,
            alert_data=alert_data,
            cancel_event=cancel_event,
            workflow_root=self.workflow_root,
            workflow_runner=self.workflow_runner,
        )
        initial_state = {
            "alert_data": alert_data,
            "action_hint": "",
            "entry_type": "conversation",
            "messages": [],
            "conversation_history": list(history or []),
            "worker_results": [],
            "system_prompt_override": system_prompt,
            "cancel_event": cancel_event,
            "readable_skills": readable_skills,
            "executable_skills": executable_skills,
            "run_id": str((execution_context or {}).get("run_id", "")).strip(),
            "execution_entry": str((execution_context or {}).get("execution_entry", "")).strip(),
            "scope_type": str((execution_context or {}).get("scope_type", "")).strip(),
            "scope_ref": str((execution_context or {}).get("scope_ref", "")).strip(),
            "checkpoint_thread_id": str((execution_context or {}).get("checkpoint_thread_id", "")).strip(),
            "graph_checkpoint_ns": "orchestrator_graph",
            "parent_checkpoint_thread_id": str((execution_context or {}).get("parent_checkpoint_thread_id", "")).strip(),
            "parent_checkpoint_ns": str((execution_context or {}).get("parent_checkpoint_ns", "")).strip(),
            "parent_tool_call_id": str((execution_context or {}).get("parent_tool_call_id", "")).strip(),
            "approved_fingerprints": list((execution_context or {}).get("approved_fingerprints", []) or []),
            "rejected_fingerprints": list((execution_context or {}).get("rejected_fingerprints", []) or []),
            "executed_skill_cache": dict((execution_context or {}).get("executed_skill_cache", {}) or {}),
        }
        if status_callback:
            status_callback("主 Agent 正在分析任务并调度子 Agent...")
        final_state = await orchestrator.ainvoke(
            initial_state,
            {"recursion_limit": max(18, max_steps * 6 + 10)},
        )
        serialized = self._serialize_orchestrator_result(final_state, alert_data, primary_agent, action_hint=None)
        if final_state.get("approval_pending"):
            serialized["approval_pending"] = True
            approval_request = self._persist_pending_state(
                state=final_state,
                checkpoint_kind="orchestrator_graph",
                agent_name=primary_agent.name if primary_agent else "",
                action_hint="",
            )
            serialized["approval_request"] = approval_request or {}
            serialized["route"] = "approval_required"
        return serialized

    async def _orchestrate_alert(
        self,
        primary_agent,
        workers: list,
        alert: dict[str, Any],
        action_hint: str | None,
        cancel_event=None,
        status_callback: Callable[[str], None] | None = None,
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from sentinelflow.agent.orchestrator_graph import build_orchestrator_graph

        config = load_runtime_config()
        effective_config = primary_agent.resolve_runtime_config(config)
        max_steps = self._resolve_worker_max_steps(primary_agent)
        parallel_limit = self._resolve_worker_parallel_limit(primary_agent)
        readable_skills, executable_skills = self._resolve_skill_permissions(primary_agent)

        alert_data = dict(alert)
        if action_hint:
            alert_data["handling_intent"] = action_hint
        alert_data["_primary_readable_skills"] = readable_skills
        alert_data["_primary_executable_skills"] = executable_skills
        alert_data["_primary_worker_parallel_limit"] = parallel_limit
        context_manifest = build_context_manifest(
            current_goal=str(action_hint or alert_data.get("alert_name", "") or alert_data.get("eventIds", "")),
            entry_type="alert",
            current_step={"agent": primary_agent.name, "role": "primary", "action_hint": action_hint or ""},
            original_input=alert_data,
            current_task_prompt=str(action_hint or ""),
        )
        alert_data["context_manifest"] = context_manifest
        alert_data["context_warnings"] = list(context_manifest.get("context_warnings", []) or [])

        system_prompt = self._build_primary_prompt(
            primary_agent, PRIMARY_ALERT_ORCHESTRATION_APPENDIX, workers, alert_data=alert_data
        )
        orchestrator = build_orchestrator_graph(
            primary_agent,
            workers,
            self.project_root,
            self.skill_runtime,
            self.approval_service,
            effective_config,
            alert_data=alert_data,
            cancel_event=cancel_event,
            workflow_root=self.workflow_root,
            workflow_runner=self.workflow_runner,
        )
        initial_state = {
            "alert_data": alert_data,
            "action_hint": action_hint or "",
            "entry_type": "alert",
            "messages": [],
            "conversation_history": [],
            "worker_results": [],
            "system_prompt_override": system_prompt,
            "cancel_event": cancel_event,
            "readable_skills": readable_skills,
            "executable_skills": executable_skills,
            "run_id": str((execution_context or {}).get("run_id", "")).strip(),
            "execution_entry": str((execution_context or {}).get("execution_entry", "")).strip(),
            "scope_type": str((execution_context or {}).get("scope_type", "")).strip(),
            "scope_ref": str((execution_context or {}).get("scope_ref", "")).strip(),
            "checkpoint_thread_id": str((execution_context or {}).get("checkpoint_thread_id", "")).strip(),
            "graph_checkpoint_ns": "orchestrator_graph",
            "parent_checkpoint_thread_id": str((execution_context or {}).get("parent_checkpoint_thread_id", "")).strip(),
            "parent_checkpoint_ns": str((execution_context or {}).get("parent_checkpoint_ns", "")).strip(),
            "parent_tool_call_id": str((execution_context or {}).get("parent_tool_call_id", "")).strip(),
            "approved_fingerprints": list((execution_context or {}).get("approved_fingerprints", []) or []),
            "rejected_fingerprints": list((execution_context or {}).get("rejected_fingerprints", []) or []),
            "executed_skill_cache": dict((execution_context or {}).get("executed_skill_cache", {}) or {}),
        }
        if status_callback:
            status_callback("主 Agent 正在分析告警并调度子 Agent...")
        final_state = await orchestrator.ainvoke(
            initial_state,
            {"recursion_limit": max(18, max_steps * 6 + 10)},
        )
        graph_result = self._serialize_orchestrator_result(final_state, alert, primary_agent, action_hint)
        if final_state.get("approval_pending"):
            graph_result["approval_pending"] = True
            approval_request = self._persist_pending_state(
                state=final_state,
                checkpoint_kind="orchestrator_graph",
                agent_name=primary_agent.name if primary_agent else "",
                action_hint=action_hint or "",
            )
            graph_result["approval_request"] = approval_request or {}
            graph_result["route"] = "approval_required"
            return graph_result
        # Orchestrator graph has no synthesis_node; run synthesis in Python layer when needed.
        if not graph_result.get("structured_judgment"):
            if self._should_skip_orchestrator_synthesis(graph_result, action_hint):
                LOGGER.debug(
                    "Skipping orchestrator _run_synthesis: closure succeeded and "
                    "final_response contains explicit disposition keywords."
                )
            else:
                graph_result["structured_judgment"] = await self._run_synthesis(
                    graph_result, effective_config=effective_config
                )
        return await self._serialize_alert_result(
            alert,
            graph_result,
            action_hint,
            agent_definition=primary_agent,
            effective_config=effective_config,
        )

    def _should_skip_orchestrator_synthesis(
        self,
        graph_result: dict[str, Any],
        action_hint: str | None,
    ) -> bool:
        """
        Skip _run_synthesis when closure already succeeded and the primary Agent
        final_response clearly states disposition via keyword matching.

        Keep synthesis for: no closure, failed closure, vague final_response, or
        disposition that would fall back to unknown/heuristic parsing.
        """
        final_text = str(graph_result.get("final_response", "")).strip()
        if not infer_explicit_disposition_from_text(final_text):
            return False
        skill_runs = self._extract_skill_runs(graph_result)
        closure_run = self._select_closure_run(skill_runs, action_hint)
        if closure_run is None:
            return False
        return self._is_successful_closure_run(closure_run)

    async def _run_synthesis(
        self,
        graph_result: dict[str, Any],
        effective_config=None,
    ) -> dict[str, Any] | None:
        """
        Run a structured-output LLM call to extract AlertJudgment from a graph result.

        Used by the orchestrator path, which has no synthesis_node inside its graph.
        Returns None on any error; _serialize_alert_result will then fall back to text parsing.
        """
        try:
            from langchain_openai import ChatOpenAI
            from langchain_core.messages import HumanMessage, SystemMessage
            from sentinelflow.agent.prompts import SYNTHESIS_SYSTEM_PROMPT
            from sentinelflow.agent.schemas import parse_alert_judgment_content, stringify_llm_content
        except ModuleNotFoundError:
            return None

        try:
            config = effective_config or load_runtime_config()
            llm = ChatOpenAI(**build_llm_client_kwargs(config, temperature=0))

            final_response = str(graph_result.get("final_response", "")).strip()
            worker_parts: list[str] = []
            for wr in (graph_result.get("worker_results") or [])[:5]:
                if not isinstance(wr, dict):
                    continue
                worker_name = str(wr.get("worker") or wr.get("worker_agent") or "worker").strip()
                resp = _result_text_for_display(wr, limit=400)
                if resp:
                    worker_parts.append(f"子Agent [{worker_name}]: {resp}")

            conversation_text = f"主Agent最终结论：{final_response}"
            if worker_parts:
                conversation_text += "\n\n子Agent执行结果：\n" + "\n".join(worker_parts)

            messages = [
                SystemMessage(
                    content=(
                        f"{SYNTHESIS_SYSTEM_PROMPT}\n\n"
                        "输出要求：只返回一个 JSON 对象，不要使用 Markdown 代码块或额外说明。"
                    )
                ),
                HumanMessage(content=conversation_text),
            ]
            response = await llm.ainvoke(messages)
            content = stringify_llm_content(getattr(response, "content", response))
            result = parse_alert_judgment_content(content)
            return result.model_dump()
        except Exception:
            LOGGER.exception(
                "_run_synthesis: structured output failed for orchestrator path, "
                "_serialize_alert_result will use text parsing fallback."
            )
            return None

    def _resolve_prompt_synthesize_agent(self, agent_definition, graph_result: dict[str, Any]):
        if agent_definition is not None:
            return agent_definition
        agent_name = str(graph_result.get("primary_agent") or graph_result.get("agent_name") or "").strip() or None
        try:
            return resolve_default_agent(self.agent_root, agent_name)
        except Exception:
            return None

    def _trace_with_inserted_step(self, trace: list[dict[str, Any]], step: dict[str, Any]) -> list[dict[str, Any]]:
        if not trace:
            return [step]
        for index in range(len(trace) - 1, -1, -1):
            item = trace[index]
            if isinstance(item, dict) and str(item.get("phase", "")).strip() == "final_status":
                return [*trace[:index], step, *trace[index:]]
        return [*trace, step]

    def _build_final_summary_context(
        self,
        *,
        alert: dict[str, Any],
        action_hint: str | None,
        graph_result: dict[str, Any],
        disposition: str,
        summary: str,
        reason: str,
        evidence: list[str],
        skill_runs: list[dict[str, Any]],
        action_steps: list[dict[str, Any]],
        closure_step: dict[str, Any],
        closure_result: dict[str, Any],
        actions: dict[str, Any],
        workflow_runs: list[dict[str, Any]],
        final_facts: dict[str, Any],
        execution_trace: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del skill_runs, actions
        return build_compact_final_summary_context(
            alert=alert,
            action_hint=action_hint,
            graph_result=graph_result,
            disposition=disposition,
            summary=summary,
            reason=reason,
            evidence=evidence,
            action_steps=action_steps,
            closure_step=closure_step,
            closure_result=closure_result,
            workflow_runs=workflow_runs,
            final_facts=final_facts,
            execution_trace=execution_trace,
        )

    def _build_report_context_snapshot(
        self,
        *,
        alert: dict[str, Any],
        action_hint: str | None,
        graph_result: dict[str, Any],
        disposition: str,
        summary: str,
        reason: str,
        evidence: list[str],
        skill_runs: list[dict[str, Any]],
        action_steps: list[dict[str, Any]],
        closure_step: dict[str, Any],
        closure_result: dict[str, Any],
        actions: dict[str, Any],
        workflow_runs: list[dict[str, Any]],
        final_facts: dict[str, Any],
        execution_trace: list[dict[str, Any]],
    ) -> dict[str, Any]:
        snapshot = self._build_final_summary_context(
            alert=alert,
            action_hint=action_hint,
            graph_result=graph_result,
            disposition=disposition,
            summary=summary,
            reason=reason,
            evidence=evidence,
            skill_runs=skill_runs,
            action_steps=action_steps,
            closure_step=closure_step,
            closure_result=closure_result,
            actions=actions,
            workflow_runs=workflow_runs,
            final_facts=final_facts,
            execution_trace=execution_trace,
        )
        compact_skill_runs: list[dict[str, Any]] = []
        for run in skill_runs or []:
            if not isinstance(run, dict):
                continue
            compact_skill_runs.append(
                {
                    "skill_name": str(run.get("skill_name", "")).strip(),
                    "tool_name": str(run.get("tool_name", "")).strip(),
                    "success": run.get("success"),
                    "arguments": run.get("arguments", {}),
                    "payload": run.get("payload", {}),
                    "tool_error": run.get("tool_error"),
                }
            )
            if len(compact_skill_runs) >= 30:
                break
        return {
            **snapshot,
            "skill_execution": {
                **(snapshot.get("skill_execution", {}) if isinstance(snapshot.get("skill_execution"), dict) else {}),
                "skill_runs": compact_skill_runs,
            },
            "snapshot_meta": {
                "source": "alert_task_result",
                "purpose": "full_report_generation",
            },
        }

    async def _run_prompt_synthesize_final_summary(
        self,
        *,
        alert: dict[str, Any],
        action_hint: str | None,
        graph_result: dict[str, Any],
        disposition: str,
        summary: str,
        reason: str,
        evidence: list[str],
        skill_runs: list[dict[str, Any]],
        action_steps: list[dict[str, Any]],
        closure_step: dict[str, Any],
        closure_result: dict[str, Any],
        actions: dict[str, Any],
        workflow_runs: list[dict[str, Any]],
        final_facts: dict[str, Any],
        execution_trace: list[dict[str, Any]],
        agent_definition=None,
        effective_config=None,
    ) -> tuple[str, dict[str, Any] | None]:
        prompt_agent = self._resolve_prompt_synthesize_agent(agent_definition, graph_result)
        agent_name = str(getattr(prompt_agent, "name", "") or graph_result.get("agent_name") or "").strip()
        final_response = str(graph_result.get("final_response", "") or "").strip()
        # Reuse primary-agent markdown even when external closure is still pending
        # (e.g. disposal done + calling status updated, but 结单 skill not run).
        if is_reusable_final_response_markdown(final_response):
            markdown = _clean_model_text(final_response).strip()
            if markdown:
                LOGGER.debug(
                    "prompt_synthesize: reusing final_response markdown (%d chars), skipping LLM call.",
                    len(markdown),
                )
                return markdown, {
                    "success": True,
                    "source": "final_response_reuse",
                    "agent_name": agent_name,
                }

        task_outcome = final_facts.get("task_outcome", {}) if isinstance(final_facts, dict) else {}
        status = str(task_outcome.get("status", "")).strip()
        if task_outcome.get("success") is not True or status not in {"succeeded", "completed"}:
            return "", None

        prompt = str(getattr(prompt_agent, "prompt_synthesize", "") or "").strip()
        if not prompt:
            return "", None

        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            from langchain_openai import ChatOpenAI
        except ModuleNotFoundError as exc:
            return "", {
                "phase": "final_judgment_synthesis",
                "title": "最终研判总结",
                "summary": "prompt_synthesize 总结失败，已回退到结构化最终研判。",
                "success": False,
                "data": {"error": str(exc), "source": "prompt_synthesize"},
            }

        try:
            config = effective_config
            if config is None:
                runtime_config = load_runtime_config()
                config = prompt_agent.resolve_runtime_config(runtime_config) if prompt_agent is not None else runtime_config
            context = self._build_final_summary_context(
                alert=alert,
                action_hint=action_hint,
                graph_result=graph_result,
                disposition=disposition,
                summary=summary,
                reason=reason,
                evidence=evidence,
                skill_runs=skill_runs,
                action_steps=action_steps,
                closure_step=closure_step,
                closure_result=closure_result,
                actions=actions,
                workflow_runs=workflow_runs,
                final_facts=final_facts,
                execution_trace=execution_trace,
            )
            context_json = json.dumps(context, ensure_ascii=False, indent=2, default=str)
            LOGGER.debug(
                "prompt_synthesize: compact context size=%d chars, estimated_tokens~=%d",
                len(context_json),
                max(1, len(context_json) // 4),
            )
            llm = ChatOpenAI(
                **build_llm_client_kwargs(
                    config,
                    temperature=config.llm_temperature if config.llm_temperature is not None else 0,
                )
            )
            response = await llm.ainvoke(
                [
                    SystemMessage(content=prompt),
                    HumanMessage(
                        content=(
                            "以下 JSON 是本次告警处置完成后的压缩上下文（已去除完整对话、重复 Skill 原始返回和冗余 payload）。"
                            "请只按照 system prompt 的要求生成最终研判展示内容。\n\n"
                            f"```json\n{context_json}\n```"
                        )
                    ),
                ]
            )
            content = getattr(response, "content", response)
            if isinstance(content, list):
                content = "\n".join(
                    str(item.get("text", item)) if isinstance(item, dict) else str(item)
                    for item in content
                )
            markdown = _clean_model_text(str(content or "")).strip()
            if not markdown:
                return "", {
                    "phase": "final_judgment_synthesis",
                    "title": "最终研判总结",
                    "summary": "prompt_synthesize 未返回有效内容，已回退到结构化最终研判。",
                    "success": False,
                    "data": {"error": "empty_response", "source": "prompt_synthesize"},
                }
            return markdown, {
                "success": True,
                "source": "prompt_synthesize",
                "agent_name": agent_name,
            }
        except Exception as exc:
            LOGGER.warning("prompt_synthesize final summary failed; falling back to structured judgment.", exc_info=True)
            return "", {
                "phase": "final_judgment_synthesis",
                "title": "最终研判总结",
                "summary": "prompt_synthesize 总结失败，已回退到结构化最终研判。",
                "success": False,
                "data": {"error": str(exc), "source": "prompt_synthesize"},
            }


    async def run_command(
        self,
        command_text: str,
        history: list[dict[str, str]] | None = None,
        cancel_event=None,
        agent_name: str | None = None,
        status_callback: Callable[[str], None] | None = None,
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        config = load_runtime_config()
        agent_definition = resolve_default_agent(self.agent_root, agent_name)
        context_plan = await build_conversation_context_plan_with_llm(
            command_text,
            history or [],
            llm_kwargs=build_llm_client_kwargs(config, temperature=0),
        )
        effective_history = list(context_plan.history_messages)
        context_policy = dict(context_plan.context_policy)
        workers = self._resolve_worker_candidates(agent_definition, entry_type="conversation")
        if self._should_use_orchestrator(agent_definition, workers):
            result = await self._orchestrate_command(
                agent_definition,
                workers,
                command_text,
                effective_history,
                context_policy,
                cancel_event,
                status_callback=status_callback,
                execution_context=execution_context,
            )
            result["conversation_context_policy"] = context_policy
            return result
        command_ref = f"CMD-{uuid4().hex[:12].upper()}"
        alert = {
            "eventIds": extract_explicit_event_id_from_text(command_text),
            "command_ref": command_ref,
            "alert_name": "人工指令",
            "payload": command_text,
            "alert_source": "human_command",
            "conversation_context_policy": context_policy,
        }
        result = await self._run_agent_graph(agent_definition, alert, history=[], cancel_event=cancel_event, execution_context=execution_context)
        result["conversation_context_policy"] = context_policy
        return result

    async def run_alert(
        self,
        alert: dict[str, Any],
        action_hint: str | None = None,
        cancel_event=None,
        agent_name: str | None = None,
        status_callback: Callable[[str], None] | None = None,
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        agent_definition = resolve_default_agent(self.agent_root, agent_name)
        agent_definition = self._with_alert_source_prompt(agent_definition, alert)
        workers = self._resolve_worker_candidates(agent_definition, entry_type="alert")
        if self._should_use_orchestrator(agent_definition, workers):
            return await self._orchestrate_alert(agent_definition, workers, alert, action_hint, cancel_event, status_callback=status_callback, execution_context=execution_context)
        alert_payload = dict(alert)
        if action_hint:
            alert_payload["handling_intent"] = action_hint
        serialized = await self._run_agent_graph(agent_definition, alert_payload, history=[], cancel_event=cancel_event, execution_context=execution_context)
        if serialized.get("approval_pending"):
            return serialized
        return await self._serialize_alert_result(alert, serialized, action_hint, agent_definition=agent_definition)


    def _serialize_graph_result(self, source: str, state: dict[str, Any], agent_name: str = "") -> dict[str, Any]:
        messages = state.get("messages", [])
        final_text = ""
        serialized_messages: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []

        from sentinelflow.agent.schemas import stringify_llm_content

        for msg in messages:
            msg_type = getattr(msg, "type", msg.__class__.__name__.lower())
            raw_content = getattr(msg, "content", "")
            content = _clean_model_text(stringify_llm_content(raw_content))
            item: dict[str, Any] = {"type": msg_type, "content": content}
            if getattr(msg, "tool_calls", None):
                item["tool_calls"] = msg.tool_calls
                tool_calls.extend(msg.tool_calls)
            if getattr(msg, "name", None):
                item["name"] = msg.name
            if getattr(msg, "tool_call_id", None):
                item["tool_call_id"] = msg.tool_call_id
            serialized_messages.append(item)
            if msg_type == "ai" and content:
                final_text = content

        return {
            "source": source,
            "agent_name": agent_name,
            "final_response": final_text,
            "messages": serialized_messages,
            "tool_calls": tool_calls,
            "event_id_ref": state.get("event_id_ref", ""),
            # Pass through structured judgment produced by synthesis_node (may be None)
            "structured_judgment": state.get("structured_judgment"),
            "context_manifest": (
                (state.get("alert_data", {}) or {}).get("context_manifest", {})
                if isinstance(state.get("alert_data", {}), dict)
                else {}
            ),
            "context_warnings": (
                list((state.get("alert_data", {}) or {}).get("context_warnings", []) or [])
                if isinstance(state.get("alert_data", {}), dict)
                else []
            ),
            "conversation_context_policy": (
                (state.get("alert_data", {}) or {}).get("conversation_context_policy", {})
                if isinstance(state.get("alert_data", {}), dict)
                else {}
            ),
        }

    async def _serialize_alert_result(
        self,
        alert: dict[str, Any],
        graph_result: dict[str, Any],
        action_hint: str | None,
        *,
        agent_definition=None,
        effective_config=None,
    ) -> dict[str, Any]:
        skill_runs = self._extract_skill_runs(graph_result)
        fallback_judgment = self.triage_service.analyze_alert(alert)
        final_text = str(graph_result.get("final_response", "")).strip()

        # ── Prefer structured judgment from synthesis_node; fallback to text parsing ──
        structured = graph_result.get("structured_judgment") or {}
        if isinstance(structured, dict) and structured.get("disposition"):
            LOGGER.debug("_serialize_alert_result: using structured_judgment (synthesis path)")
            disposition = str(structured["disposition"]).strip() or fallback_judgment.disposition.value
            summary = str(structured.get("summary") or "").strip() or fallback_judgment.summary
            reason = str(structured.get("reason") or "").strip() or fallback_judgment.summary
            evidence = [str(e).strip() for e in (structured.get("evidence") or []) if str(e).strip()]
        else:
            LOGGER.debug("_serialize_alert_result: structured_judgment absent, using text parsing fallback")
            disposition = self._infer_disposition(final_text, fallback_judgment.disposition.value)
            summary = self._infer_summary(final_text, fallback_judgment.summary)
            reason = self._infer_reason(final_text, alert, fallback_judgment)
            evidence = self._infer_evidence(final_text, alert, fallback_judgment)
        # ─────────────────────────────────────────────────────────────────────────────

        if not summary or summary in {"--", "-", "—"}:
            summary = reason or fallback_judgment.summary
        analysis_step = self._build_analysis_step(graph_result, disposition, summary, reason, evidence)
        enrichment = self._first_enrichment_payload(skill_runs)
        closure_run = self._select_closure_run(skill_runs, action_hint)
        closure_result = self._first_closure_payload(skill_runs, closure_run)
        actions = self._build_actions(skill_runs, closure_run)
        action_steps = self._build_action_steps(skill_runs, closure_run)
        closure_step = self._build_closure_step(skill_runs, closure_run)
        primary_actions = dict(actions)
        primary_action_steps = list(action_steps)
        primary_closure_step = dict(closure_step)
        workflow_runs = graph_result.get("workflow_runs", [])
        if not isinstance(workflow_runs, list):
            workflow_runs = []
        worker_results = graph_result.get("worker_results", [])
        if not isinstance(worker_results, list):
            worker_results = []
        workflow_runs, workflow_execution_issues = self._annotate_guided_workflow_execution(
            workflow_runs=workflow_runs,
            worker_results=worker_results,
        )
        aggregated_action_steps, aggregated_actions = self._aggregate_action_side_effects(
            action_hint=action_hint,
            primary_action_steps=action_steps,
            primary_actions=actions,
            worker_results=worker_results,
            workflow_runs=workflow_runs,
        )
        aggregated_closure_steps = self._aggregate_closure_steps(
            action_hint=action_hint,
            primary_closure_step=closure_step,
            worker_results=worker_results,
            workflow_runs=workflow_runs,
        )
        effective_closure_step = self._resolve_effective_closure_step(
            skill_runs=skill_runs,
            action_hint=action_hint,
            primary_closure_step=closure_step,
            aggregated_closure_steps=aggregated_closure_steps,
            worker_results=worker_results,
            workflow_runs=workflow_runs,
        )
        effective_closure_result = effective_closure_step.get("result", {})
        if not isinstance(effective_closure_result, dict):
            effective_closure_result = {}
        if effective_closure_result:
            closure_result = effective_closure_result
        actions = aggregated_actions
        action_steps = aggregated_action_steps
        closure_step = effective_closure_step
        workflow_selection = self._extract_workflow_selection(alert, graph_result)
        final_facts = self._build_final_facts(
            structured_disposition=disposition,
            closure_step=closure_step,
            closure_result=closure_result,
            action_steps=action_steps,
            workflow_runs=workflow_runs,
            workflow_execution_issues=workflow_execution_issues,
            success=False,
        )
        task_outcome = final_facts.get("task_outcome", {}) if isinstance(final_facts, dict) else {}
        success = bool(task_outcome.get("success"))
        execution_trace = self._build_execution_trace(
            alert=alert,
            graph_result=graph_result,
            workflow_selection=workflow_selection,
            workflow_runs=workflow_runs,
            analysis_step=analysis_step,
            disposition=disposition,
            summary=summary,
            reason=reason,
            evidence=evidence,
            enrichment=enrichment,
            action_steps=action_steps,
            closure_step=closure_step,
            closure_result=closure_result,
            actions=actions,
            skill_runs=skill_runs,
            success=success,
        )
        if execution_trace:
            finalizer_step = {
                "phase": "final_facts",
                "title": "最终事实收敛",
                "summary": (
                    "已按执行事实优先完成最终结果收敛。"
                    if bool(((final_facts.get("consistency", {}) if isinstance(final_facts, dict) else {}).get("consistent", True)))
                    else "检测到结果冲突，已按执行事实优先完成最终结果收敛。"
                ),
                "success": bool(success),
                "data": final_facts,
            }
            if isinstance(execution_trace[-1], dict) and str(execution_trace[-1].get("phase", "")).strip() == "final_status":
                execution_trace = execution_trace[:-1] + [finalizer_step, execution_trace[-1]]
            else:
                execution_trace.append(finalizer_step)
        final_disposition = str(
            ((final_facts.get("judgment", {}) if isinstance(final_facts, dict) else {}).get("disposition", disposition))
        ).strip() or disposition
        final_judgment_markdown, final_judgment_synthesis = await self._run_prompt_synthesize_final_summary(
            alert=alert,
            action_hint=action_hint,
            graph_result=graph_result,
            disposition=final_disposition,
            summary=summary,
            reason=reason,
            evidence=evidence,
            skill_runs=skill_runs,
            action_steps=action_steps,
            closure_step=closure_step,
            closure_result=closure_result,
            actions=actions,
            workflow_runs=workflow_runs,
            final_facts=final_facts,
            execution_trace=execution_trace,
            agent_definition=agent_definition,
            effective_config=effective_config,
        )
        report_context_snapshot = self._build_report_context_snapshot(
            alert=alert,
            action_hint=action_hint,
            graph_result=graph_result,
            disposition=final_disposition,
            summary=summary,
            reason=reason,
            evidence=evidence,
            skill_runs=skill_runs,
            action_steps=action_steps,
            closure_step=closure_step,
            closure_result=closure_result,
            actions=actions,
            workflow_runs=workflow_runs,
            final_facts=final_facts,
            execution_trace=execution_trace,
        )
        if final_judgment_synthesis and not final_judgment_markdown:
            execution_trace = self._trace_with_inserted_step(execution_trace, final_judgment_synthesis)

        result_payload = {
            **graph_result,
            "event_ids": str(alert.get("eventIds", "")).strip(),
            "disposition": final_disposition,
            "summary": summary,
            "reason": reason,
            "evidence": evidence,
            "analysis_step": analysis_step,
            "memo": str(
                closure_result.get("memo")
                or self._infer_closure_field(skill_runs, "memo", self.triage_service.build_memo(summary))
            ).strip(),
            "detail_msg": str(
                closure_result.get("detailMsg")
                or closure_result.get("detail_msg")
                or self._infer_closure_field(skill_runs, "detailMsg", self._default_detail_msg(disposition))
            ).strip(),
            "closure_status": str(
                closure_result.get("status")
                or self._infer_closure_field(skill_runs, "status", self._default_closure_status(disposition))
            ).strip(),
            "enrichment": enrichment,
            "workflow_selection": workflow_selection,
            "workflow_runs": workflow_runs,
            "workflow_execution_issues": workflow_execution_issues,
            "primary_action_steps": primary_action_steps,
            "primary_closure_step": primary_closure_step,
            "aggregated_action_steps": aggregated_action_steps,
            "aggregated_actions": aggregated_actions,
            "aggregated_closure_steps": aggregated_closure_steps,
            "effective_closure_step": effective_closure_step,
            "action_steps": action_steps,
            "closure_step": closure_step,
            "closure_result": closure_result,
            "actions": actions,
            "success": success,
            "execution_mode": "agent",
            "execution_trace": execution_trace,
            "final_facts": final_facts,
            "report_context_snapshot": report_context_snapshot,
            "used_agent": True,
            "has_close_action": bool(closure_step.get("attempted")),
            "has_disposal_action": bool(actions),
        }
        if final_judgment_markdown:
            result_payload["final_judgment_markdown"] = final_judgment_markdown
            result_payload["final_judgment_synthesis"] = final_judgment_synthesis or {
                "success": True,
                "source": "prompt_synthesize",
            }
        elif final_judgment_synthesis:
            result_payload["final_judgment_synthesis"] = final_judgment_synthesis
        return result_payload

    def _extract_workflow_selection(
        self,
        alert: dict[str, Any],
        graph_result: dict[str, Any],
    ) -> dict[str, Any]:
        direct = graph_result.get("workflow_selection")
        if isinstance(direct, dict):
            return dict(direct)
        payload = alert.get("_workflow_selection")
        if isinstance(payload, dict):
            return dict(payload)
        raw_payload = alert.get("payload")
        if isinstance(raw_payload, str) and raw_payload.strip().startswith("{"):
            try:
                decoded = json.loads(raw_payload)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict):
                candidate = decoded.get("workflow_selection")
                if isinstance(candidate, dict):
                    return dict(candidate)
        return {}

    def _collect_executed_worker_names(self, worker_results: list[dict[str, Any]]) -> list[str]:
        names: list[str] = []
        for worker_result in worker_results:
            if not isinstance(worker_result, dict):
                continue
            worker_name = str(worker_result.get("worker") or worker_result.get("worker_agent") or "").strip()
            if worker_name:
                names.append(worker_name)
            nested_worker_results = worker_result.get("worker_results", [])
            if isinstance(nested_worker_results, list):
                names.extend(self._collect_executed_worker_names(nested_worker_results))
            parallel_results = worker_result.get("results", [])
            if isinstance(parallel_results, list):
                names.extend(self._collect_executed_worker_names(parallel_results))
        return names

    def _annotate_guided_workflow_execution(
        self,
        *,
        workflow_runs: list[dict[str, Any]],
        worker_results: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if not workflow_runs:
            return workflow_runs, []

        from collections import Counter

        def message_index(item: dict[str, Any]) -> int | None:
            try:
                return int(item.get("_message_index"))
            except (TypeError, ValueError):
                return None

        annotated: list[dict[str, Any]] = []
        all_issues: list[dict[str, Any]] = []
        workflow_indexes = [
            idx
            for item in workflow_runs
            if isinstance(item, dict)
            for idx in [message_index(item)]
            if idx is not None
        ]
        for workflow_run in workflow_runs:
            if not isinstance(workflow_run, dict):
                continue
            run = dict(workflow_run)
            is_guided = (
                str(run.get("execution_mode", "")).strip() == "supervisor_guided_workflow"
                or bool(run.get("requires_supervisor_execution"))
            )
            if not is_guided:
                annotated.append(run)
                continue

            # Prefer the inline worker_results embedded in the workflow_run by
            # the orchestrator — these are scoped to this specific workflow
            # invocation.  Fall back to the caller-supplied global list only
            # when no inline results are present (e.g. legacy paths).
            # Using the global list risks counting workers invoked by the
            # supervisor *before* run_workflow (e.g. IP queries) as if they
            # were workflow step executions, producing false "step executed"
            # verdicts.
            inline_worker_results = run.get("worker_results")
            if isinstance(inline_worker_results, list) and inline_worker_results:
                scoped_workers = inline_worker_results
            else:
                workflow_message_index = message_index(run)
                if workflow_message_index is None:
                    scoped_workers = worker_results
                else:
                    next_workflow_index = min(
                        (idx for idx in workflow_indexes if idx > workflow_message_index),
                        default=None,
                    )
                    scoped_workers = [
                        item
                        for item in worker_results
                        if isinstance(item, dict)
                        and (message_index(item) is None or message_index(item) > workflow_message_index)
                        and (next_workflow_index is None or message_index(item) is None or message_index(item) < next_workflow_index)
                    ]

            executed_workers = Counter(
                name.strip().lower()
                for name in self._collect_executed_worker_names(scoped_workers)
                if name.strip()
            )
            remaining_workers = Counter(executed_workers)
            issues: list[dict[str, Any]] = []
            for step in run.get("steps", []) if isinstance(run.get("steps", []), list) else []:
                if not isinstance(step, dict):
                    continue
                agent_name = str(step.get("agent", "")).strip()
                if not agent_name:
                    continue
                marker = agent_name.lower()
                if remaining_workers[marker] > 0:
                    remaining_workers[marker] -= 1
                    continue
                issue = {
                    "workflow_id": str(run.get("workflow_id", "")).strip(),
                    "step_index": step.get("index"),
                    "step_id": step.get("id", ""),
                    "step_name": step.get("name", ""),
                    "agent": agent_name,
                    "issue": "missing_worker_execution",
                }
                issues.append(issue)
                all_issues.append(issue)

            run["execution_confirmed"] = not bool(issues)
            run["workflow_execution_issues"] = issues
            if issues:
                run["success"] = False
                run["execution_status"] = "incomplete"
            elif str(run.get("execution_status", "")).strip() == "plan_ready":
                run["execution_status"] = "executed"
            annotated.append(run)
        return annotated, all_issues

    def _build_execution_trace(
        self,
        *,
        alert: dict[str, Any],
        graph_result: dict[str, Any],
        workflow_selection: dict[str, Any],
        workflow_runs: list[dict[str, Any]],
        analysis_step: dict[str, Any],
        disposition: str,
        summary: str,
        reason: str,
        evidence: list[str],
        enrichment: dict[str, Any],
        action_steps: list[dict[str, Any]],
        closure_step: dict[str, Any],
        closure_result: dict[str, Any],
        actions: dict[str, Any],
        skill_runs: list[dict[str, Any]],
        success: bool,
    ) -> list[dict[str, Any]]:
        trace: list[dict[str, Any]] = []
        trace.append(
            {
                "phase": "alert_received",
                "title": "接收告警",
                "summary": "已接收任务告警上下文。",
                "success": True,
                "data": {
                    "eventIds": str(alert.get("eventIds", "")).strip(),
                    "alert_name": str(alert.get("alert_name", alert.get("alert_name") or alert.get("alertName", ""))).strip(),
                    "sip": alert.get("sip", ""),
                    "dip": alert.get("dip", ""),
                    "alert_time": alert.get("alert_time", ""),
                    "alert_source": alert.get("alert_source", ""),
                    "current_judgment": alert.get("current_judgment", ""),
                    "history_judgment": alert.get("history_judgment", ""),
                    "payload": alert.get("payload", ""),
                },
            }
        )
        if workflow_selection:
            trace.append(
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
                }
            )
        for workflow_run in workflow_runs:
            if not isinstance(workflow_run, dict):
                continue
            trace.append(
                {
                    "phase": "workflow_run",
                    "title": f"调用 Workflow：{str(workflow_run.get('workflow_name', workflow_run.get('workflow_id', '未命名流程'))).strip()}",
                    "summary": str(workflow_run.get("summary", "")).strip() or "主 Agent 调用了一个固定多步骤 Workflow。",
                    "success": bool(workflow_run.get("success")),
                    "data": workflow_run,
                }
            )
            nested_trace = workflow_run.get("execution_trace", [])
            if isinstance(nested_trace, list):
                for nested_item in nested_trace:
                    if not isinstance(nested_item, dict):
                        continue
                    trace.append(
                        {
                            "phase": f"workflow_nested_{str(nested_item.get('phase', '')).strip() or 'step'}",
                            "title": f"Workflow 内部：{str(nested_item.get('title', '')).strip() or '步骤'}",
                            "summary": str(nested_item.get("summary", "")).strip(),
                            "success": nested_item.get("success"),
                            "data": nested_item.get("data", nested_item),
                        }
                    )
        workflow_execution_issues = [
            issue
            for workflow_run in workflow_runs
            if isinstance(workflow_run, dict)
            for issue in (workflow_run.get("workflow_execution_issues", []) if isinstance(workflow_run.get("workflow_execution_issues", []), list) else [])
            if isinstance(issue, dict)
        ]
        if workflow_execution_issues:
            trace.append(
                {
                    "phase": "workflow_execution_check",
                    "title": "Workflow 执行校验",
                    "summary": f"检测到 {len(workflow_execution_issues)} 个 Workflow 步骤未确认执行。",
                    "success": False,
                    "data": {
                        "issues": workflow_execution_issues,
                    },
                }
            )
        context_records: list[dict[str, Any]] = []
        root_manifest = graph_result.get("context_manifest", {})
        if isinstance(root_manifest, dict) and root_manifest:
            context_records.append({"source_type": "primary", "source_name": "primary", "context_manifest": root_manifest})
        for worker_result in graph_result.get("worker_results", []) or []:
            if not isinstance(worker_result, dict):
                continue
            manifest = worker_result.get("context_manifest", {})
            if isinstance(manifest, dict) and manifest:
                context_records.append(
                    {
                        "source_type": "worker",
                        "source_name": str(worker_result.get("worker", worker_result.get("worker_agent", ""))).strip(),
                        "step": worker_result.get("step"),
                        "context_manifest": manifest,
                    }
                )
        for workflow_run in workflow_runs:
            if not isinstance(workflow_run, dict):
                continue
            manifest = workflow_run.get("context_manifest", {})
            if isinstance(manifest, dict) and manifest:
                context_records.append(
                    {
                        "source_type": "workflow",
                        "source_name": str(workflow_run.get("workflow_name", workflow_run.get("workflow_id", ""))).strip(),
                        "context_manifest": manifest,
                    }
                )
        if context_records:
            warnings = [
                warning
                for record in context_records
                for warning in (
                    (record.get("context_manifest", {}) or {}).get("context_warnings", [])
                    if isinstance(record.get("context_manifest", {}), dict)
                    else []
                )
                if str(warning).strip()
            ]
            missing = [
                item
                for record in context_records
                for item in (
                    (record.get("context_manifest", {}) or {}).get("missing_required_inputs", [])
                    if isinstance(record.get("context_manifest", {}), dict)
                    else []
                )
                if isinstance(item, dict)
            ]
            trace.append(
                {
                    "phase": "context_control",
                    "title": "执行上下文控制",
                    "summary": "已记录当前任务目标、权威事实来源和上下文健康信息。",
                    "success": not missing,
                    "data": {
                        "records": context_records,
                        "context_warnings": warnings,
                        "missing_required_inputs": missing,
                    },
                }
            )
        trace.append(
            {
                "phase": "agent_analysis",
                "title": "主 Agent 研判",
                "summary": summary or reason or "主 Agent 已输出研判结论。",
                "success": bool(analysis_step.get("success", True)),
                "data": analysis_step,
            }
        )
        if enrichment:
            trace.append(
                {
                    "phase": "enrichment",
                    "title": "情报补充",
                    "summary": "已产生额外情报或上下文补充结果。",
                    "success": not bool(enrichment.get("error")),
                    "data": enrichment,
                }
            )
        if skill_runs:
            trace.append(
                {
                    "phase": "skill_runs",
                    "title": "技能调用记录",
                    "summary": f"共调用 {len(skill_runs)} 个技能。",
                    "success": all(bool(run.get("success")) for run in skill_runs),
                    "data": {
                        "runs": skill_runs,
                    },
                }
            )
        if actions:
            trace.append(
                {
                    "phase": "actions",
                    "title": "处置动作",
                    "summary": f"共执行 {len(actions)} 类处置动作。",
                    "success": all(bool(step.get("success")) for step in action_steps) if action_steps else all(not bool(payload.get("error")) for payload in actions.values() if isinstance(payload, dict)),
                    "data": {
                        "steps": action_steps,
                        "actions": actions,
                    },
                }
            )
        if closure_step.get("attempted") or closure_result:
            trace.append(
                {
                    "phase": "closure",
                    "title": "结单结果",
                    "summary": str(
                        closure_step.get("summary")
                        or closure_result.get("detailMsg")
                        or closure_result.get("detail_msg")
                        or closure_result.get("result")
                        or "已执行结单。"
                    ).strip(),
                    "success": bool(closure_step.get("success")),
                    "data": {
                        **closure_step,
                        "closure_result": closure_result,
                    },
                }
            )
        trace.append(
            {
                "phase": "final_status",
                "title": "最终执行状态",
                "summary": (
                    "任务成功完成。"
                    if success
                    else (
                        "未执行结单，任务未完成。"
                        if not bool(closure_step.get("attempted"))
                        else "结单未成功，任务未完成。"
                    )
                ),
                "success": success,
                "data": {
                    "success": success,
                    "has_close_action": bool(closure_step.get("attempted")),
                    "has_disposal_action": bool(action_steps) or bool(actions),
                    "tool_calls": graph_result.get("tool_calls", []),
                    "messages": graph_result.get("messages", []),
                },
            }
        )
        return trace

    def _build_analysis_step(
        self,
        graph_result: dict[str, Any],
        disposition: str,
        summary: str,
        reason: str,
        evidence: list[str],
    ) -> dict[str, Any]:
        return {
            "attempted": True,
            "success": True,
            "agent_name": graph_result.get("agent_name", ""),
            "used_agent": True,
            "execution_mode": "agent",
            "disposition": disposition,
            "summary": summary,
            "reason": reason,
            "evidence": evidence,
            "final_response": graph_result.get("final_response", ""),
        }

    def _build_action_steps(
        self,
        skill_runs: list[dict[str, Any]],
        closure_run: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        steps: list[dict[str, Any]] = []
        for run in skill_runs:
            skill_name = str(run.get("skill_name", "")).strip()
            if not skill_name or self._is_same_skill_run(run, closure_run) or self._is_enrichment_run(run):
                continue
            payload = run.get("payload", {})
            completion_policy = self._completion_policy_for_skill(skill_name)
            steps.append(
                {
                    "attempted": True,
                    "success": bool(run.get("success")),
                    "skill_name": skill_name,
                    "tool_name": run.get("tool_name", ""),
                    "tool_call_id": run.get("tool_call_id", ""),
                    "tool_success": run.get("tool_success"),
                    "tool_error": run.get("tool_error"),
                    "arguments": run.get("arguments", {}) if isinstance(run.get("arguments"), dict) else {},
                    "result": payload if isinstance(payload, dict) else {},
                    "error": payload.get("error") if isinstance(payload, dict) else None,
                    "completion_policy": completion_policy,
                    "completion_policy_enabled": bool(completion_policy.get("enabled")),
                    "completion_effect": str(completion_policy.get("completion_effect", "none")).strip() or "none",
                    "configured_action_kind": str(completion_policy.get("action_kind", "")).strip(),
                }
            )
        return steps

    def _resolve_closure_disposition(self, status_value: str, memo: str, detail_msg: str) -> str:
        normalized = str(status_value or "").strip()
        if normalized == "4":
            return "false_positive"
        if normalized != "6":
            return ""
        normalized_text = _clean_model_text(detail_msg).replace(" ", "")
        if any(keyword in normalized_text for keyword in ("真实攻击", "恶意攻击", "确认攻击", "高危攻击")):
            return "true_attack"
        if any(keyword in normalized_text for keyword in ("规则误报", "误报")):
            return "false_positive"
        if any(keyword in normalized_text for keyword in ("测试/业务触发", "业务触发", "测试触发", "正常业务", "业务测试")):
            return "business_trigger"
        return ""

    def _extract_action_target(self, payload: dict[str, Any], arguments: dict[str, Any]) -> str:
        for candidate in ("ban_ip", "banned_ip", "blocked_ip", "ip", "source_ip", "sip", "target", "target_ip", "name", "dip"):
            value = str(payload.get(candidate) or arguments.get(candidate) or "").strip()
            if value:
                return value
        return ""

    def _classify_action_kind(self, skill_name: str, payload: dict[str, Any], arguments: dict[str, Any]) -> str:
        normalized_name = str(skill_name or "").strip().lower()
        combined = " ".join(
            [
                normalized_name,
                str(payload.get("action", "")).strip().lower(),
                str(payload.get("result", "")).strip().lower(),
                str(payload.get("message", "")).strip().lower(),
            ]
        )
        if "ban" in normalized_name or "封禁" in combined:
            return "ban_ip"
        if "notify" in normalized_name or "通知" in combined:
            return "notify"
        if "isolate" in normalized_name or "隔离" in combined:
            return "isolate_host"
        if "query" in normalized_name or "info" in normalized_name or "lookup" in normalized_name or "查询" in combined:
            return "collect_context"
        return "other"

    def _is_substantive_disposal_action(self, action: dict[str, Any]) -> bool:
        action_kind = str(action.get("kind", "")).strip()
        completion_effect = str(action.get("completion_effect", "none")).strip() or "none"
        if bool(action.get("completion_policy_enabled")) and completion_effect != "none":
            return True
        return action_kind in {"ban_ip", "notify", "isolate_host", "closure"}

    def _completion_policy_for_skill(self, skill_name: str) -> dict[str, Any]:
        cache_key = str(skill_name or "").strip()
        cache = getattr(self, "_completion_policy_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, "_completion_policy_cache", cache)
        if cache_key in cache:
            return dict(cache[cache_key])
        try:
            policy = self.skill_runtime.read_skill(cache_key).completion_policy
        except Exception:
            policy = {}
        if not isinstance(policy, dict):
            result = {"enabled": False, "action_kind": "other", "completion_effect": "none"}
            cache[cache_key] = result
            return dict(result)
        result = {
            "enabled": bool(policy.get("enabled")),
            "action_kind": str(policy.get("action_kind", "other")).strip() or "other",
            "completion_effect": str(policy.get("completion_effect", "none")).strip() or "none",
        }
        cache[cache_key] = result
        return dict(result)

    def _build_final_facts(
        self,
        *,
        structured_disposition: str,
        closure_step: dict[str, Any],
        closure_result: dict[str, Any],
        action_steps: list[dict[str, Any]],
        workflow_runs: list[dict[str, Any]],
        workflow_execution_issues: list[dict[str, Any]] | None = None,
        success: bool = False,
    ) -> dict[str, Any]:
        closure_step = closure_step if isinstance(closure_step, dict) else {}
        closure_result = closure_result if isinstance(closure_result, dict) else {}
        closure_attempted = bool(closure_step.get("attempted"))
        closure_success = bool(closure_step.get("success"))
        closure_status = str(
            closure_result.get("status")
            or ((closure_step.get("result") or {}) if isinstance(closure_step.get("result"), dict) else {}).get("status")
            or ((closure_step.get("arguments") or {}) if isinstance(closure_step.get("arguments"), dict) else {}).get("status")
            or ""
        ).strip()
        closure_memo = str(closure_result.get("memo") or "").strip()
        closure_detail = str(closure_result.get("detailMsg") or closure_result.get("detail_msg") or "").strip()
        mapped_disposition = self._resolve_closure_disposition(closure_status, closure_memo, closure_detail) if closure_success else ""
        structured_disposition = str(structured_disposition or "").strip()
        if mapped_disposition:
            final_disposition = mapped_disposition
            judgment_source = "closure_result_mapping"
            judgment_confidence = "high"
        elif closure_success and structured_disposition:
            final_disposition = structured_disposition
            judgment_source = "structured_analysis_after_closure"
            judgment_confidence = "medium"
        else:
            final_disposition = structured_disposition or "unknown"
            judgment_source = "structured_analysis"
            judgment_confidence = "medium" if structured_disposition else "low"

        disposal_actions: list[dict[str, Any]] = []
        for step in action_steps:
            if not isinstance(step, dict):
                continue
            payload = step.get("result", {})
            payload = payload if isinstance(payload, dict) else {}
            arguments = step.get("arguments", {})
            arguments = arguments if isinstance(arguments, dict) else {}
            step_success = bool(step.get("success"))
            configured_action_kind = str(step.get("configured_action_kind", "")).strip()
            policy_enabled = bool(step.get("completion_policy_enabled"))
            action_kind = configured_action_kind if policy_enabled and configured_action_kind else self._classify_action_kind(str(step.get("skill_name", "")).strip(), payload, arguments)
            completion_effect = str(step.get("completion_effect", "none")).strip() or "none"
            target = self._extract_action_target(payload, arguments)
            disposal_actions.append(
                {
                    "kind": action_kind,
                    "skill_name": str(step.get("skill_name", "")).strip(),
                    "target": target,
                    "success": step_success,
                    "completion_policy_enabled": policy_enabled,
                    "completion_effect": completion_effect,
                    "source_type": str(step.get("source_type", "primary") or "primary").strip(),
                    "source_name": str(step.get("source_name", "primary") or "primary").strip(),
                }
            )

        substantive_disposal_actions = [
            item
            for item in disposal_actions
            if isinstance(item, dict) and self._is_substantive_disposal_action(item)
        ]
        successful_disposal_actions = [item for item in substantive_disposal_actions if bool(item.get("success"))]
        successful_terminal_notification = any(
            bool(item.get("success"))
            and bool(item.get("completion_policy_enabled"))
            and str(item.get("completion_effect", "")).strip() == "notification"
            for item in disposal_actions
        )
        terminal_action_complete = (
            not closure_attempted
            and successful_terminal_notification
        )
        consistency_issues: list[str] = []
        workflow_execution_issues = [item for item in (workflow_execution_issues or []) if isinstance(item, dict)]
        if workflow_execution_issues:
            consistency_issues.append("workflow_steps_missing_execution")
        if successful_disposal_actions and not closure_attempted and not terminal_action_complete:
            consistency_issues.append("disposal_executed_but_closure_not_attempted")
        elif closure_attempted and not closure_success:
            consistency_issues.append("closure_attempted_but_not_successful")

        if workflow_execution_issues:
            outcome_status = "workflow_incomplete"
            outcome_success = False
        elif closure_success:
            outcome_status = "succeeded"
            outcome_success = True
        elif closure_attempted:
            outcome_status = "failed"
            outcome_success = False
        elif terminal_action_complete:
            outcome_status = "pending_manual_closure"
            outcome_success = True
        else:
            outcome_status = "pending_closure"
            outcome_success = False

        return {
            "judgment": {
                "disposition": final_disposition,
                "source": judgment_source,
                "confidence": judgment_confidence,
            },
            "closure": {
                "attempted": closure_attempted,
                "success": closure_success,
                "status": closure_status,
                "memo": closure_memo,
                "detail_msg": closure_detail,
                "source_type": str(closure_step.get("source_type", "")).strip(),
                "source_name": str(closure_step.get("source_name", "")).strip(),
            },
            "disposal": {
                "attempted": bool(substantive_disposal_actions),
                "success": bool(successful_disposal_actions),
                "actions": disposal_actions,
            },
            "workflow": {
                "used": bool(workflow_runs),
                "count": len(workflow_runs) if isinstance(workflow_runs, list) else 0,
                "workflow_ids": [
                    str(item.get("workflow_id", "")).strip()
                    for item in workflow_runs
                    if isinstance(item, dict) and str(item.get("workflow_id", "")).strip()
                ] if isinstance(workflow_runs, list) else [],
                "execution_issues": workflow_execution_issues,
            },
            "task_outcome": {
                "success": outcome_success,
                "status": outcome_status,
                "source": "terminal_action_policy" if terminal_action_complete else "finalizer",
            },
            "consistency": {
                "consistent": not consistency_issues,
                "issues": consistency_issues,
            },
        }

    # NOTE: _extract_skill_runs … _default_closure_status are provided by
    # SkillRunAnalyzerMixin and TextExtractorMixin (see agent/skill_run_analyzer.py
    # and agent/text_extractor.py).
    def _build_history_messages(self, history: list[dict[str, str]]) -> list[Any]:
        try:
            from langchain_core.messages import AIMessage, HumanMessage
        except ModuleNotFoundError:
            return []

        messages: list[Any] = []
        for item in history:
            role = str(item.get("role", "")).strip().lower()
            content = str(item.get("content", ""))
            if not content.strip():
                continue
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))
        return messages
