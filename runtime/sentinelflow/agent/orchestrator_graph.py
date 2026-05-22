"""
SentinelFlow Multi-Agent Orchestrator Graph
=========================================

Implements a LangGraph **Supervisor + Worker SubGraph** pattern:

  START → supervisor_node (LLM bound with worker tools)
            ↓ tool_calls?
          tools_node  (ToolNode — each worker is a compiled SubGraph wrapped as @tool)
            ↓
          supervisor_node  (re-evaluates results, decides next step)
            ↓ no more tool_calls
          END

Each Worker SubGraph is the same single-agent ReAct graph built by
`build_agent_graph()`, wrapped in an async @tool function so the Supervisor
can invoke it just like any other tool.  The Worker's internal messages are
fully isolated — only `final_response` surfaces back to the Supervisor as a
ToolMessage.
"""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from sentinelflow.agent.checkpoint_state import serialize_graph_state
from sentinelflow.agent.context_utils import (
    build_context_manifest,
    compact_prior_step_payload,
    compact_worker_result_for_llm,
    extract_key_facts,
    format_context_manifest_header,
    prepare_messages_for_llm,
    summarize_tool_calls,
)
from sentinelflow.agent.graph import build_agent_graph
from sentinelflow.agent.message_trace import count_react_turns
from sentinelflow.agent.run_log_tracer import get_active_tracer, make_logged_tool_node, scope_label
from sentinelflow.agent.orchestrator_state import OrchestratorState
from sentinelflow.agent.policy import can_agent_execute_skill, can_agent_read_skill
from sentinelflow.agent.tools import build_agent_tools
from sentinelflow.config.runtime import build_llm_client_kwargs
from sentinelflow.services.skill_approval_service import SkillApprovalService
from sentinelflow.skills.adapters import SentinelFlowSkillRuntime
from sentinelflow.workflows.agent_workflow_registry import load_agent_workflow, list_agent_workflows

try:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
    from langchain_core.tools import tool
    from langchain_openai import ChatOpenAI
    from langgraph.graph import END, START, StateGraph
    from langgraph.prebuilt import InjectedState, ToolNode
except ModuleNotFoundError as _exc:  # pragma: no cover
    raise ModuleNotFoundError(
        "缺少 Agent 运行依赖，请安装 langgraph、langchain-openai 和 langchain-core。"
    ) from _exc


# ── Helpers ───────────────────────────────────────────────────────────────────

def _worker_tool_name(worker_name: str) -> str:
    safe_name = str(worker_name or "").replace("-", "_").replace(" ", "_").strip("_")
    return f"call_{safe_name or 'worker'}"


def _resolve_worker_permissions(
    worker_agent_def,
    skill_runtime: SentinelFlowSkillRuntime,
) -> tuple[list[str], list[str]]:
    readable: list[str] = []
    executable: list[str] = []
    for skill in skill_runtime.loader.list_skills():
        if can_agent_read_skill(worker_agent_def, skill):
            readable.append(skill.spec.name)
        if can_agent_execute_skill(worker_agent_def, skill):
            executable.append(skill.spec.name)
    return readable, executable


def _resolve_current_tool_call_id(
    state: OrchestratorState,
    tool_name: str,
    *,
    expected_args: dict[str, str] | None = None,
) -> str:
    completed_call_ids = {
        str(getattr(msg, "tool_call_id", "")).strip()
        for msg in list(state.get("messages", []))
        if isinstance(msg, ToolMessage) and str(getattr(msg, "tool_call_id", "")).strip()
    }
    for msg in reversed(list(state.get("messages", []))):
        if not isinstance(msg, AIMessage):
            continue
        for call in reversed(list(getattr(msg, "tool_calls", None) or [])):
            if not isinstance(call, dict):
                continue
            if str(call.get("name", "")).strip() != tool_name:
                continue
            call_id = str(call.get("id", "")).strip()
            if call_id and call_id in completed_call_ids:
                continue
            if expected_args:
                args = call.get("args", {})
                if not isinstance(args, dict):
                    continue
                matched = True
                for key, value in expected_args.items():
                    if str(args.get(key, "")) != value:
                        matched = False
                        break
                if not matched:
                    continue
            return call_id
    return ""


def _extract_prior_facts_from_messages(messages: list[Any]) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        content = getattr(msg, "content", "")
        if not isinstance(content, str):
            continue
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            continue
        facts = extract_key_facts(facts, payload.get("key_facts", {}), payload.get("tool_calls_summary", []), payload)
    return facts


def _context_warnings_from_manifest(manifest: dict[str, Any]) -> list[str]:
    warnings = manifest.get("context_warnings", []) if isinstance(manifest, dict) else []
    return [str(item) for item in warnings if str(item).strip()] if isinstance(warnings, list) else []


def _tool_payloads_from_messages(messages: list[Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        content = getattr(msg, "content", "")
        if not isinstance(content, str) or not content.strip().startswith("{"):
            continue
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            payloads.append(compact_prior_step_payload(parsed))
    return payloads


# ── Worker SubGraph Tool Builder ──────────────────────────────────────────────

def _build_worker_subgraph_tool(
    worker_agent_def,
    project_root: Path,
    skill_runtime: SentinelFlowSkillRuntime,
    approval_service: SkillApprovalService,
    runtime_config,
    *,
    alert_data: dict[str, Any],
    cancel_event: Any = None,
    step_counter: list[int],
):
    """
    Compile a Worker SubGraph and wrap it as a LangChain @tool.

    The Supervisor calls this tool with a plain-text `task_prompt`.
    Internally it runs a full ReAct SubGraph and returns a JSON summary
    of the worker's final response and skills used.
    """
    readable_skills, executable_skills = _resolve_worker_permissions(worker_agent_def, skill_runtime)

    async def _execute_worker_subgraph(task_prompt: str, state: OrchestratorState, step_idx: int) -> dict[str, Any]:
        child_checkpoint_thread_id = f"{str(state.get('checkpoint_thread_id', '')).strip() or uuid4().hex}:worker:{worker_agent_def.name}:{step_idx}"
        prior_facts = _extract_prior_facts_from_messages(list(state.get("messages", [])))
        context_manifest = build_context_manifest(
            current_goal=task_prompt,
            entry_type=str(state.get("entry_type", "")).strip(),
            current_step={"step": step_idx, "worker": worker_agent_def.name},
            original_input=alert_data,
            current_task_prompt=task_prompt,
            prior_step_results=_tool_payloads_from_messages(list(state.get("messages", []))),
            model_summary=prior_facts,
        )
        child_state = {
            "alert_data": {
                **copy.deepcopy(alert_data),
                "delegated_task_prompt": task_prompt,
                "prior_facts": prior_facts,
                "context_manifest": context_manifest,
                "context_warnings": _context_warnings_from_manifest(context_manifest),
            },
            "messages": [],
            "event_id_ref": str(alert_data.get("eventIds", "")).strip(),
            "input_seeded": False,
            "cancel_event": cancel_event,
            "readable_skills": readable_skills,
            "executable_skills": executable_skills,
            "system_prompt_override": worker_agent_def.prompt or "",
            "agent_name": worker_agent_def.name,
            "run_id": str(state.get("run_id", "")).strip(),
            "execution_entry": str(state.get("execution_entry", "")).strip(),
            "scope_type": str(state.get("scope_type", "")).strip(),
            "scope_ref": str(state.get("scope_ref", "")).strip(),
            "checkpoint_thread_id": child_checkpoint_thread_id,
            "graph_checkpoint_ns": "agent_graph",
            "parent_checkpoint_thread_id": str(state.get("checkpoint_thread_id", "")).strip(),
            "parent_checkpoint_ns": str(state.get("graph_checkpoint_ns", state.get("checkpoint_ns", "orchestrator_graph"))).strip(),
            "parent_tool_call_id": str(state.get("parent_tool_call_id", "")).strip(),
            "approved_fingerprints": list(state.get("approved_fingerprints") or []),
            "rejected_fingerprints": list(state.get("rejected_fingerprints") or []),
            "executed_skill_cache": dict(state.get("executed_skill_cache", {}) or {}),
            "case_context": copy.deepcopy(state.get("case_context", {}) or {}),
        }
        tracer = get_active_tracer()
        if tracer is not None:
            tracer.log_worker_boundary(
                worker=worker_agent_def.name,
                step=step_idx,
                event_type="worker_started",
                title=f"子 Agent {worker_agent_def.name} · 开始执行",
                extra={"task_prompt": task_prompt},
            )

        worker_config = worker_agent_def.resolve_runtime_config(runtime_config)
        subgraph = build_agent_graph(
            project_root,
            skill_runtime,
            approval_service,
            worker_config,
            enable_read_skill_document=True,
            enable_execute_skill=True,
            executable_skill_names=list(executable_skills or []),
        )
        try:
            worker_state = await subgraph.ainvoke(child_state)
        except Exception as exc:
            if tracer is not None:
                tracer.log_worker_boundary(
                    worker=worker_agent_def.name,
                    step=step_idx,
                    event_type="worker_failed",
                    title=f"子 Agent {worker_agent_def.name} · 执行异常",
                    extra={"error": str(exc)},
                )
            return {
                "step": step_idx,
                "worker": worker_agent_def.name,
                "task_prompt": task_prompt,
                "final_response": "",
                "skills_used": [],
                "tool_calls_summary": [],
                "key_facts": {},
                "context_manifest": context_manifest,
                "context_warnings": _context_warnings_from_manifest(context_manifest),
                "success": False,
                "error": str(exc),
            }

        final_text = ""
        skills_used: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        tool_result_facts: dict[str, Any] = {}
        for msg in worker_state.get("messages", []):
            msg_type = getattr(msg, "type", "")
            if msg_type == "ai" and getattr(msg, "content", ""):
                final_text = msg.content
            if getattr(msg, "tool_calls", None):
                tool_calls.extend(msg.tool_calls)
            for tc in (getattr(msg, "tool_calls", None) or []):
                if isinstance(tc, dict) and tc.get("name"):
                    skills_used.append(tc["name"])
            if msg_type == "tool":
                content = getattr(msg, "content", "")
                if isinstance(content, str):
                    try:
                        parsed_content = json.loads(content)
                    except json.JSONDecodeError:
                        parsed_content = content
                    tool_result_facts = extract_key_facts(tool_result_facts, parsed_content)

        tool_calls_summary = summarize_tool_calls(
            tool_calls,
            tool_messages=list(worker_state.get("messages", [])),
        )
        if tracer is not None:
            tracer.log_worker_boundary(
                worker=worker_agent_def.name,
                step=step_idx,
                event_type="worker_finished",
                title=f"子 Agent {worker_agent_def.name} · 执行完成",
                extra={
                    "success": bool(final_text),
                    "skills_used": skills_used,
                    "final_response": final_text,
                    "approval_pending": bool(worker_state.get("approval_pending")),
                },
            )
        result = {
            "step": step_idx,
            "worker": worker_agent_def.name,
            "task_prompt": task_prompt,
            "final_response": final_text,
            "skills_used": skills_used,
            "tool_calls_summary": tool_calls_summary,
            "key_facts": extract_key_facts(prior_facts, alert_data, task_prompt, final_text, tool_calls_summary, tool_result_facts),
            "context_manifest": context_manifest,
            "context_warnings": _context_warnings_from_manifest(context_manifest),
            "success": bool(final_text),
            "approval_pending": bool(worker_state.get("approval_pending")),
            "approval_request": worker_state.get("approval_request", {}),
            "error": None if final_text else "子 Agent 未返回有效结果。",
        }
        if worker_state.get("approval_pending"):
            approval_request = worker_state.get("approval_request", {})
            pending_tool_call_id = ""
            if isinstance(approval_request, dict):
                for msg in reversed(list(worker_state.get("messages", []))):
                    if getattr(msg, "type", "") != "tool":
                        continue
                    content = getattr(msg, "content", "")
                    if not isinstance(content, str):
                        continue
                    try:
                        payload = json.loads(content)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict) and payload.get("approval_pending"):
                        pending_tool_call_id = str(getattr(msg, "tool_call_id", "")).strip()
                        break
                approval_service.save_checkpoint(
                    checkpoint_thread_id=child_checkpoint_thread_id,
                    checkpoint_ns="agent_graph",
                    checkpoint_kind="agent_graph",
                    run_id=str(child_state.get("run_id", "")).strip(),
                    scope_type=str(child_state.get("scope_type", "")).strip(),
                    scope_ref=str(child_state.get("scope_ref", "")).strip(),
                    agent_name=worker_agent_def.name,
                    execution_entry=str(child_state.get("execution_entry", "")).strip(),
                    action_hint="",
                    state_payload=serialize_graph_state(worker_state),
                )
                record = approval_service.create_or_reuse_pending(
                    run_id=str(child_state.get("run_id", "")).strip(),
                    scope_type=str(child_state.get("scope_type", "")).strip(),
                    scope_ref=str(child_state.get("scope_ref", "")).strip(),
                    skill_name=str(approval_request.get("skill_name", "")).strip(),
                    arguments=approval_request.get("arguments", {}) if isinstance(approval_request.get("arguments"), dict) else {},
                    approval_required=True,
                    checkpoint_thread_id=child_checkpoint_thread_id,
                    checkpoint_ns="agent_graph",
                    tool_call_id=pending_tool_call_id or str(approval_request.get("tool_call_id", "")).strip(),
                    parent_checkpoint_thread_id=str(child_state.get("parent_checkpoint_thread_id", "")).strip(),
                    parent_checkpoint_ns=str(child_state.get("parent_checkpoint_ns", "")).strip(),
                    parent_tool_call_id=str(child_state.get("parent_tool_call_id", "")).strip(),
                    message=str(approval_request.get("message", "")).strip(),
                )
                result["approval_request"] = approval_service.serialize_approval(record)
                result["error"] = "子 Agent 等待技能审批。"
                result["success"] = False
        return compact_worker_result_for_llm(result)

    tool_name = _worker_tool_name(worker_agent_def.name)
    worker_desc = (worker_agent_def.description or worker_agent_def.name).strip()
    tool_description = (
        f"委托子 Agent【{worker_agent_def.name}】执行指定任务。"
        f"该 Agent 的专项能力：{worker_desc}。"
        f"参数 task_prompt 必须是具体、可操作的中文任务描述，包含当前步骤需要完成的具体工作内容。"
    )

    async def _invoke(
        task_prompt: str,
        state: Annotated[OrchestratorState, InjectedState()],  # type: ignore[misc]
    ) -> str:
        """委托当前子 Agent 执行一个具体任务，并返回 JSON 结果。"""
        step_counter[0] += 1
        step_idx = step_counter[0]
        worker_state = dict(state)
        worker_state["parent_tool_call_id"] = _resolve_current_tool_call_id(
            state,
            tool_name,
            expected_args={"task_prompt": str(task_prompt or "")},
        )
        result = await _execute_worker_subgraph(task_prompt, worker_state, step_idx)
        return json.dumps(result, ensure_ascii=False)
    worker_tool = tool(tool_name, description=tool_description)(_invoke)
    return worker_tool, _execute_worker_subgraph


# ── Routing ───────────────────────────────────────────────────────────────────

def _should_orchestrate_continue(
    state: OrchestratorState,
) -> Literal["tools", "__end__"]:
    """Route: delegate to a worker tool, or finish the orchestration."""
    messages = state.get("messages", [])
    if not messages:
        return "__end__"
    last_msg = messages[-1]
    if isinstance(last_msg, AIMessage) and getattr(last_msg, "tool_calls", None):
        return "tools"
    return "__end__"


# ── Orchestrator Graph Builder ────────────────────────────────────────────────

def build_orchestrator_graph(
    primary_agent,
    workers: list,
    project_root: Path,
    skill_runtime: SentinelFlowSkillRuntime,
    approval_service: SkillApprovalService,
    runtime_config,
    *,
    alert_data: dict[str, Any],
    cancel_event: Any = None,
    workflow_root: Path | None = None,
    workflow_runner: Any = None,
):
    """
    Build and return the compiled Supervisor + Worker SubGraph orchestrator.

    Args:
        primary_agent: The primary agent definition (role="primary").
        workers:       List of worker agent definitions (role="worker").
        project_root:  Absolute path to the project root.
        skill_runtime: Shared SentinelFlowSkillRuntime instance.
        runtime_config: Global LLM runtime configuration.
        alert_data:    The initial alert payload or command dict.
        cancel_event:  Optional threading.Event for cancellation.

    Returns:
        A compiled LangGraph StateGraph ready for `ainvoke()`.
    """
    # ── Build Worker tools ────────────────────────────────────────────────────
    step_counter: list[int] = [0]
    worker_entries = [
        _build_worker_subgraph_tool(
            w,
            project_root,
            skill_runtime,
            approval_service,
            runtime_config,
            alert_data=alert_data,
            cancel_event=cancel_event,
            step_counter=step_counter,
        )
        for w in workers
    ]
    worker_tools = [entry[0] for entry in worker_entries]
    worker_runners = {worker.name: entry[1] for worker, entry in zip(workers, worker_entries)}
    readable_skills = list(alert_data.get("_primary_readable_skills", []) or [])
    executable_skills = list(alert_data.get("_primary_executable_skills", []) or [])
    parallel_limit = max(1, int(alert_data.get("_primary_worker_parallel_limit", 3) or 3))
    primary_skill_tools = build_agent_tools(
        skill_runtime,
        approval_service,
        enable_read_skill_document=bool(readable_skills),
        enable_execute_skill=bool(executable_skills),
        executable_skill_names=executable_skills,
    )
    workflow_catalog = {
        workflow.id: workflow
        for workflow in (list_agent_workflows(workflow_root) if workflow_root is not None else [])
        if workflow.enabled
    }

    @tool
    async def run_workflow(
        workflow_id: str,
        task_prompt: str,
        state: Annotated[OrchestratorState, InjectedState()],  # type: ignore[misc]
    ) -> str:
        """读取一个已配置的 Agent Workflow 执行计划。该工具只返回固定步骤顺序；每一步仍必须由主 Agent 亲自调用对应子 Agent。"""
        cancel = state.get("cancel_event")
        if cancel is not None and getattr(cancel, "is_set", lambda: False)():
            return json.dumps({"success": False, "workflow_id": workflow_id, "error": "用户已停止当前任务。"}, ensure_ascii=False)
        workflow_id = str(workflow_id or "").strip()
        if not workflow_id:
            return json.dumps({"success": False, "workflow_id": "", "error": "必须提供 workflow_id。"}, ensure_ascii=False)
        if workflow_root is None:
            return json.dumps({"success": False, "workflow_id": workflow_id, "error": "当前未启用 Workflow 运行时。"}, ensure_ascii=False)
        workflow = workflow_catalog.get(workflow_id)
        if workflow is None:
            try:
                workflow = load_agent_workflow(workflow_root, workflow_id)
            except Exception as exc:
                return json.dumps({"success": False, "workflow_id": workflow_id, "error": f"Workflow 不存在或无法加载：{exc}"}, ensure_ascii=False)
        if not workflow.enabled:
            return json.dumps({"success": False, "workflow_id": workflow_id, "error": "该 Workflow 当前未启用。"}, ensure_ascii=False)
        steps: list[dict[str, Any]] = []
        for index, step in enumerate(workflow.steps, start=1):
            agent_name = str(step.agent or "").strip()
            tool_name = _worker_tool_name(agent_name)
            steps.append(
                {
                    "index": index,
                    "id": step.id,
                    "name": step.name,
                    "agent": agent_name,
                    "tool_name": tool_name,
                    "agent_available": agent_name in worker_runners,
                    "task_prompt": step.task_prompt,
                    "step_goal": step.task_prompt or f"请根据 Workflow 目标完成第 {index} 步《{step.name}》。",
                    "depends_on": [item.id for item in workflow.steps[: index - 1]],
                    "visible_prior_steps": [item.id for item in workflow.steps[: index - 1]],
                }
            )
        context_manifest = build_context_manifest(
            current_goal=str(task_prompt or workflow.description or workflow.name),
            entry_type=str(state.get("entry_type", "")).strip(),
            current_step={"workflow_id": workflow.id, "workflow_name": workflow.name},
            original_input=alert_data,
            current_task_prompt=str(task_prompt or ""),
            workflow_definition={
                "id": workflow.id,
                "name": workflow.name,
                "description": workflow.description,
                "steps": steps,
            },
            conversation_history=state.get("conversation_history", []),
        )
        result = {
            "success": True,
            "workflow_id": workflow.id,
            "workflow_name": workflow.name,
            "workflow_description": workflow.description,
            "execution_mode": "supervisor_guided_workflow",
            "execution_status": "plan_ready",
            "requires_supervisor_execution": True,
            "task_prompt": str(task_prompt or ""),
            "steps": steps,
            "context_manifest": context_manifest,
            "context_warnings": _context_warnings_from_manifest(context_manifest),
            "summary": f"Workflow《{workflow.name}》计划已载入，等待主 Agent 按步骤调用子 Agent。",
            "instructions": [
                "run_workflow 只返回固定步骤计划，没有执行任何子 Agent。",
                "你必须按 steps 顺序逐步调用对应 tool_name。",
                "每次调用子 Agent 时，由你结合原始任务、workflow_description、当前 step_goal、已完成步骤结果和必要查询结果生成完整 task_prompt。",
                "如果当前步骤缺少动态对象，例如 IP 归属人或通知对象，可以先调用合适的查询能力获得对象，再继续当前步骤。",
                "强依赖前后顺序的 Workflow 步骤不要并行执行。",
            ],
            "next_step": steps[0] if steps else {},
            "used_agent_workflow": True,
        }
        return json.dumps(result, ensure_ascii=False)

    @tool
    async def delegate_parallel(
        tasks: list[dict[str, str]],
        state: Annotated[OrchestratorState, InjectedState()],  # type: ignore[misc]
    ) -> str:
        """并行委托多个子 Agent 执行彼此独立的子任务，并返回全部结果。"""
        cancel = state.get("cancel_event")
        if cancel is not None and getattr(cancel, "is_set", lambda: False)():
            return json.dumps({"success": False, "mode": "parallel", "results": [], "error": "用户已停止当前任务。"}, ensure_ascii=False)

        normalized_tasks: list[dict[str, str]] = []
        for item in tasks or []:
            if not isinstance(item, dict):
                continue
            worker_name = str(item.get("worker", "")).strip()
            task_prompt = str(item.get("task_prompt", ""))
            if not worker_name or not task_prompt.strip():
                continue
            if worker_name not in worker_runners:
                normalized_tasks.append(
                    {
                        "worker": worker_name,
                        "task_prompt": task_prompt,
                        "error": f"子 Agent {worker_name} 未注册或未配置给当前主 Agent。",
                    }
                )
                continue
            normalized_tasks.append({"worker": worker_name, "task_prompt": task_prompt})

        if not normalized_tasks:
            return json.dumps(
                {"success": False, "mode": "parallel", "results": [], "error": "没有提供有效的并行委派任务。"},
                ensure_ascii=False,
            )

        run_coroutines: list[Any] = []
        coroutine_meta: list[dict[str, Any]] = []
        invalid_results: list[dict[str, Any]] = []
        for task_spec in normalized_tasks[:parallel_limit]:
            worker_name = str(task_spec.get("worker", "")).strip()
            task_prompt = str(task_spec.get("task_prompt", ""))
            invalid_reason = str(task_spec.get("error", "")).strip()
            if invalid_reason:
                step_counter[0] += 1
                invalid_results.append(
                    {
                        "step": step_counter[0],
                        "worker": worker_name,
                        "task_prompt": task_prompt,
                        "final_response": "",
                        "skills_used": [],
                        "success": False,
                        "error": invalid_reason,
                    }
                )
                continue
            step_counter[0] += 1
            step_idx = step_counter[0]
            context_manifest = build_context_manifest(
                current_goal=task_prompt,
                entry_type=str(state.get("entry_type", "")).strip(),
                current_step={"step": step_idx, "worker": worker_name, "mode": "parallel"},
                original_input=alert_data,
                current_task_prompt=task_prompt,
                model_summary=_extract_prior_facts_from_messages(list(state.get("messages", []))),
            )
            coroutine_meta.append(
                {
                    "step": step_idx,
                    "worker": worker_name,
                    "task_prompt": task_prompt,
                    "context_manifest": context_manifest,
                }
            )
            run_coroutines.append(worker_runners[worker_name](task_prompt, state, step_idx))

        gathered = await asyncio.gather(*run_coroutines, return_exceptions=True) if run_coroutines else []
        results: list[dict[str, Any]] = list(invalid_results)
        for meta, item in zip(coroutine_meta, gathered):
            if isinstance(item, Exception):
                results.append(
                    {
                        "step": meta["step"],
                        "worker": meta["worker"],
                        "task_prompt": meta["task_prompt"],
                        "final_response": "",
                        "skills_used": [],
                        "context_manifest": meta.get("context_manifest", {}),
                        "context_warnings": _context_warnings_from_manifest(meta.get("context_manifest", {})),
                        "success": False,
                        "error": f"子 Agent 执行异常：{item}",
                    }
                )
            else:
                results.append(item)

        pending_result = next(
            (
                item
                for item in results
                if isinstance(item, dict)
                and item.get("approval_pending")
                and isinstance(item.get("approval_request"), dict)
                and item.get("approval_request")
            ),
            None,
        )

        return json.dumps(
            {
                "success": False if pending_result else any(bool(item.get("success")) for item in results if isinstance(item, dict)),
                "mode": "parallel",
                "results": results,
                "approval_pending": bool(pending_result),
                "approval_request": dict(pending_result.get("approval_request", {})) if isinstance(pending_result, dict) else {},
                "error": "并行子 Agent 等待技能审批。" if pending_result else None,
            },
            ensure_ascii=False,
        )

    supervisor_tools = worker_tools + [run_workflow, delegate_parallel] + primary_skill_tools

    # ── Build Supervisor LLM (bound with worker + primary skill tools) ───────
    supervisor_config = primary_agent.resolve_runtime_config(runtime_config)
    supervisor_llm = ChatOpenAI(**build_llm_client_kwargs(supervisor_config)).bind_tools(supervisor_tools)

    # ── Supervisor node ───────────────────────────────────────────────────────
    async def _supervisor_node(state: OrchestratorState) -> dict:
        cancel = state.get("cancel_event")
        if cancel is not None and getattr(cancel, "is_set", lambda: False)():
            raise RuntimeError("用户已停止当前任务。")

        system_prompt = str(state.get("system_prompt_override", "")).strip()
        system_msg = SystemMessage(content=system_prompt)
        current_messages = list(state.get("messages", []))

        if not current_messages:
            # ── First invocation: seed with conversation history + initial task ──
            seed_messages: list = []
            context_messages: list = []
            for item in (state.get("conversation_history") or []):
                role = str(item.get("role", "")).strip().lower()
                content = str(item.get("content", ""))
                if not content.strip():
                    continue
                if role == "user":
                    seed_messages.append(HumanMessage(content=content))
                elif role == "assistant":
                    seed_messages.append(AIMessage(content=content))

            # Build human message from alert_data / command payload
            ad = state.get("alert_data", {})
            entry_type = state.get("entry_type", "conversation")
            if entry_type == "conversation" or ad.get("alert_source") == "human_command":
                payload = str(ad.get("payload", ""))
                human_content = f"请处理以下人工指令：{payload}" if payload else "请开始处理当前任务。"
            else:
                alert_json = json.dumps(ad, ensure_ascii=False, indent=2)
                action_hint = state.get("action_hint", "")
                hint_text = f"\n\n处置意图：{action_hint}" if action_hint else ""
                human_content = f"请分析并处置以下告警：\n\n```json\n{alert_json}\n```{hint_text}"
            forced_workflow_id = str(ad.get("_forced_workflow_id", "")).strip()
            if forced_workflow_id:
                forced_workflow_name = str(ad.get("_forced_workflow_name", "")).strip()
                forced_workflow_description = str(ad.get("_forced_workflow_description", "")).strip()
                human_content += (
                    "\n\nWorkflow 约束："
                    f"\n- 必须先调用 run_workflow 读取 workflow_id={forced_workflow_id} 的固定步骤计划。"
                    "\n- run_workflow 返回后，由你按步骤亲自调用对应子 Agent，并为每一步生成完整 task_prompt。"
                    "\n- 如果某一步缺少动态对象，先查询对象，再继续当前步骤。"
                )
                if forced_workflow_name:
                    human_content += f"\n- Workflow 名称：{forced_workflow_name}"
                if forced_workflow_description:
                    human_content += f"\n- Workflow 描述：{forced_workflow_description}"

            context_manifest = build_context_manifest(
                current_goal=str(ad.get("payload", "") or state.get("action_hint", "") or "处理当前告警/任务"),
                entry_type=str(entry_type),
                current_step={"agent": str(getattr(primary_agent, "name", "")).strip(), "role": "primary"},
                original_input=ad,
                current_task_prompt=str(ad.get("payload", "") or state.get("action_hint", "")),
                conversation_history=state.get("conversation_history") or [],
            )
            context_messages.append(HumanMessage(content=format_context_manifest_header(context_manifest)))

            initial_msg = HumanMessage(content=human_content)
            messages_to_send = [system_msg] + seed_messages + context_messages + [initial_msg]
            # Persist seed + initial into state so subsequent loops see them
            new_messages = seed_messages + context_messages + [initial_msg]
        else:
            # ── Subsequent invocation: full message history already in state ──
            messages_to_send = [system_msg] + current_messages
            new_messages = []

        primary_name = str(getattr(primary_agent, "name", "")).strip()
        primary_scope = scope_label(primary_name, default="主 Agent")
        tracer = get_active_tracer()
        if tracer is not None and not current_messages:
            tracer.log_system_prompt(
                scope=primary_scope,
                graph="orchestrator",
                agent_name=primary_name,
                content=system_prompt,
                node="supervisor",
            )

        turn = count_react_turns(current_messages) + 1
        alert_data = state.get("alert_data", {}) if isinstance(state.get("alert_data", {}), dict) else {}
        prompt_goal = str(alert_data.get("payload", "") or state.get("action_hint", "") or "处理当前告警/任务")
        prompt_messages, prompt_window_info, case_context = prepare_messages_for_llm(
            system_msg=system_msg,
            messages=messages_to_send[1:],
            alert_data=alert_data,
            current_goal=prompt_goal,
            case_context=state.get("case_context", {}) if isinstance(state.get("case_context", {}), dict) else {},
            scope="supervisor",
        )
        if tracer is not None:
            tracer.log_llm_request(
                scope=primary_scope,
                graph="orchestrator",
                agent_name=primary_name,
                turn=turn,
                messages=prompt_messages,
                node="supervisor",
                alert_data=alert_data,
                window_info=prompt_window_info,
            )

        response = await supervisor_llm.ainvoke(prompt_messages)

        if cancel is not None and getattr(cancel, "is_set", lambda: False)():
            raise RuntimeError("用户已停止当前任务。")

        if tracer is not None:
            tracer.log_llm_response(
                scope=primary_scope,
                graph="orchestrator",
                agent_name=primary_name,
                turn=turn,
                message=response,
                node="supervisor",
                request_messages=prompt_messages,
                alert_data=alert_data,
            )

        return {"messages": new_messages + [response], "case_context": case_context}

    def _extract_approval_request(state: OrchestratorState) -> dict | None:
        for msg in reversed(list(state.get("messages", []))):
            if not isinstance(msg, ToolMessage):
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
            return request if isinstance(request, dict) else None
        return None

    def _approval_gate(state: OrchestratorState) -> dict:
        request = _extract_approval_request(state)
        if not request:
            return {"approval_pending": False, "approval_request": {}}
        return {"approval_pending": True, "approval_request": request}

    def _route_after_tools(state: OrchestratorState) -> str:
        return "__end__" if state.get("approval_pending") else "supervisor"

    # ── Assemble the graph ────────────────────────────────────────────────────
    builder: StateGraph = StateGraph(OrchestratorState)
    builder.add_node("supervisor", _supervisor_node)
    builder.add_node(
        "tools",
        make_logged_tool_node(
            supervisor_tools,
            scope="主 Agent",
            graph="orchestrator",
            agent_name=str(getattr(primary_agent, "name", "")).strip(),
            node="supervisor_tools",
            tool_node_cls=ToolNode,
        ),
    )
    builder.add_node("approval_gate", _approval_gate)
    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        _should_orchestrate_continue,
        {"tools": "tools", "__end__": END},
    )
    builder.add_edge("tools", "approval_gate")
    builder.add_conditional_edges("approval_gate", _route_after_tools, {"supervisor": "supervisor", "__end__": END})
    return builder.compile()
