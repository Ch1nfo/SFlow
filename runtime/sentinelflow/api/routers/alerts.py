import asyncio
import json
import logging
import re
import queue
import threading
import time
from dataclasses import asdict
from typing import Any
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from sentinelflow.agent.registry import list_agent_definitions
from sentinelflow.agent.run_log_tracer import activate_run_log_tracer, deactivate_run_log_tracer
from sentinelflow.api.schemas import CommandDispatchRequest, AlertActionRequest, ApprovalDecisionRequest
from sentinelflow.api.deps import agent_service, dispatch_service, audit_service, polling_service, skill_runtime, _serialize, agent_run_log_service, auto_execution_service, task_runner_service, skill_approval_service, WORKFLOW_ROOT, AGENT_ROOT
from sentinelflow.api.utils import _extract_alert_payload, _resolve_task
from sentinelflow.config.runtime import build_llm_client_kwargs, load_runtime_config, save_runtime_config

router = APIRouter(prefix="/api/sentinelflow")
logger = logging.getLogger(__name__)

active_command_cancellations: dict[str, threading.Event] = {}
active_command_lock = threading.Lock()
active_full_report_tasks: dict[str, asyncio.Task] = {}
active_full_report_lock = threading.Lock()


def _alert_sources_payload() -> list[dict[str, Any]]:
    return [
        {
            "id": source.id,
            "name": source.name,
            "enabled": source.alert_source_enabled,
            "auto_execute_enabled": source.auto_execute_enabled,
        }
        for source in load_runtime_config().alert_sources
    ]


def _default_source_id() -> str:
    sources = load_runtime_config().alert_sources
    return sources[0].id if sources else "default"


def _stream_timeout_seconds() -> int:
    try:
        return max(30, int(load_runtime_config().llm_timeout or 60) + 60)
    except Exception:
        return 120


def _resolve_source_id(value: str | None = None) -> str:
    candidate = str(value or "").strip()
    if candidate == "all":
        return "all"
    sources = load_runtime_config().alert_sources
    if candidate and any(source.id == candidate for source in sources):
        return candidate
    return sources[0].id if sources else (candidate or "default")


def _all_source_ids() -> list[str]:
    return [source.id for source in load_runtime_config().alert_sources] or ["default"]


def _compact_report_run_log(task) -> dict[str, Any]:
    if task is None or agent_run_log_service is None:
        return {}
    alert_data = task.payload.get("alert_data", {}) if isinstance(task.payload, dict) and isinstance(task.payload.get("alert_data"), dict) else {}
    ref = agent_run_log_service.derive_ref(
        task_id=task.task_id,
        event_ids=task.event_ids,
        alert_data=alert_data,
    )
    detail = agent_run_log_service.read_log(ref.date, ref.log_id, limit=200, tail=True)
    events = detail.get("events", []) if isinstance(detail, dict) else []
    if not isinstance(events, list) or not events:
        return {}
    selected: list[dict[str, Any]] = []
    useful_phases = {"workflow", "execution_trace", "final_facts", "final_response", "run_summary", "task_finished"}
    for event in events:
        if not isinstance(event, dict):
            continue
        phase = str(event.get("phase", "")).strip()
        if phase not in useful_phases:
            continue
        data = event.get("data", {})
        summary = ""
        if isinstance(data, dict):
            summary = str(data.get("summary") or data.get("final_response") or data.get("error") or "").strip()
        elif isinstance(data, str):
            summary = data.strip()
        selected.append(
            {
                "seq": event.get("seq"),
                "phase": phase,
                "title": str(event.get("title", "")).strip(),
                "summary": summary[:500],
            }
        )
        if len(selected) >= 30:
            break
    return {
        "available": True,
        "date": detail.get("date", ""),
        "log_id": detail.get("log_id", ""),
        "total_events": detail.get("total_events", 0),
        "selected_events": selected,
    }


def _fallback_report_context(task) -> dict[str, Any]:
    result = task.last_result_data if task is not None and isinstance(task.last_result_data, dict) else {}
    payload = task.payload if task is not None and isinstance(task.payload, dict) else {}
    return {
        "original_alert": payload.get("alert_data", {}),
        "current_structured_judgment": {
            "disposition": result.get("disposition", ""),
            "summary": result.get("summary", ""),
            "reason": result.get("reason", ""),
            "evidence": result.get("evidence", []),
        },
        "agent_result": {
            "workflow_runs": result.get("workflow_runs", []),
            "final_judgment_markdown": result.get("final_judgment_markdown", ""),
        },
        "skill_execution": {
            "action_steps": result.get("action_steps", []),
            "closure": result.get("effective_closure_step") or result.get("closure_step") or {},
        },
        "final_facts": result.get("final_facts", {}),
        "execution_trace": result.get("execution_trace", []),
    }


async def _generate_full_report_markdown(task) -> dict[str, Any]:
    config = load_runtime_config()
    skill_name = str(getattr(config, "full_report_format_skill", "") or "output-report").strip() or "output-report"
    try:
        skill_doc = skill_runtime.read_skill(skill_name)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"读取完整报告格式 Skill 失败：{exc}") from exc
    format_markdown = str(getattr(skill_doc, "markdown", "") or "").strip()
    if not format_markdown:
        raise HTTPException(status_code=400, detail=f"完整报告格式 Skill「{skill_name}」内容为空。")

    result = task.last_result_data if isinstance(task.last_result_data, dict) else {}
    context = result.get("report_context_snapshot")
    if not isinstance(context, dict) or not context:
        context = _fallback_report_context(task)
    run_log_context = _compact_report_run_log(task)
    report_input = {
        "format_skill": skill_name,
        "format_markdown": format_markdown[:8000],
        "report_context_snapshot": context,
        "run_log_supplement": run_log_context,
    }
    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_openai import ChatOpenAI
    except ModuleNotFoundError as exc:
        raise HTTPException(status_code=500, detail=f"缺少完整报告生成依赖：{exc}") from exc

    llm = ChatOpenAI(
        **build_llm_client_kwargs(
            config,
            temperature=config.llm_temperature if config.llm_temperature is not None else 0,
        )
    )
    response = await llm.ainvoke(
        [
            SystemMessage(
                content=(
                    "你是安全告警完整研判报告生成器。只基于用户提供的已保存任务事实生成报告；"
                    "不得编造未出现的证据，不得声称重新执行了任何处置动作。"
                )
            ),
            HumanMessage(
                content=(
                    "请按 format_markdown 中的格式要求，基于 report_context_snapshot 生成完整中文研判报告。"
                    "run_log_supplement 仅作细节补充；若与任务结果冲突，以 report_context_snapshot 和 final_facts 为准。\n\n"
                    f"```json\n{json.dumps(report_input, ensure_ascii=False, indent=2, default=str)}\n```"
                )
            ),
        ]
    )
    content = getattr(response, "content", response)
    if isinstance(content, list):
        content = "\n".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in content)
    markdown = str(content or "").strip()
    if not markdown:
        raise HTTPException(status_code=500, detail="完整研判报告生成失败：模型未返回有效内容。")
    return {
        "markdown": markdown,
        "format_skill": skill_name,
        "run_log_used": bool(run_log_context),
    }


def _full_report_generation_payload(
    *,
    status: str,
    source: str = "manual_button",
    format_skill: str = "",
    run_log_used: bool | None = None,
    error: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "source": source,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    if format_skill:
        payload["format_skill"] = format_skill
    if run_log_used is not None:
        payload["run_log_used"] = run_log_used
    if error:
        payload["error"] = error[:1000]
    return payload


async def _run_full_report_generation(task_id: str) -> None:
    try:
        task = dispatch_service.get_task(task_id)
        if task is None:
            return
        result_data = dict(task.last_result_data) if isinstance(task.last_result_data, dict) else {}
        if str(result_data.get("full_report_markdown") or "").strip():
            return

        generated = await _generate_full_report_markdown(task)
        latest_task = dispatch_service.get_task(task_id)
        if latest_task is None:
            return
        latest_result_data = dict(latest_task.last_result_data) if isinstance(latest_task.last_result_data, dict) else {}
        if str(latest_result_data.get("full_report_markdown") or "").strip():
            return
        latest_result_data["full_report_markdown"] = generated["markdown"]
        latest_result_data["full_report_generation"] = _full_report_generation_payload(
            status="success",
            format_skill=generated["format_skill"],
            run_log_used=generated["run_log_used"],
        )
        dispatch_service.update_task_result_data(task_id, latest_result_data)
    except Exception as exc:
        logger.warning("full report background generation failed for task %s", task_id, exc_info=True)
        latest_task = dispatch_service.get_task(task_id)
        if latest_task is not None:
            latest_result_data = dict(latest_task.last_result_data) if isinstance(latest_task.last_result_data, dict) else {}
            existing_generation = latest_result_data.get("full_report_generation")
            format_skill = ""
            if isinstance(existing_generation, dict):
                format_skill = str(existing_generation.get("format_skill") or "").strip()
            latest_result_data["full_report_generation"] = _full_report_generation_payload(
                status="failed",
                format_skill=format_skill,
                error=str(exc),
            )
            dispatch_service.update_task_result_data(task_id, latest_result_data)
    finally:
        with active_full_report_lock:
            active_full_report_tasks.pop(task_id, None)


def _start_full_report_generation(task_id: str) -> None:
    with active_full_report_lock:
        existing = active_full_report_tasks.get(task_id)
        if existing is not None and not existing.done():
            return
        active_full_report_tasks[task_id] = asyncio.create_task(_run_full_report_generation(task_id))


def _save_source_auto_execute(source_id: str, enabled: bool) -> None:
    config = load_runtime_config()
    target_ids = set(_all_source_ids() if source_id == "all" else [source_id])
    next_sources = []
    for source in config.alert_sources:
        data = asdict(source) if hasattr(source, "__dataclass_fields__") else dict(source)
        if data.get("id") in target_ids:
            data["auto_execute_enabled"] = enabled
        next_sources.append(data)
    save_runtime_config({"alert_sources": next_sources})


def _state_tasks_payload(
    source_id: str | None,
    *,
    limit: int,
    offset: int,
    since: str | None = None,
    cursor_sort_time: str | None = None,
    cursor_task_id: str | None = None,
) -> tuple[list[dict[str, Any]], int, dict[str, str] | None]:
    started = time.monotonic()
    tasks, total, next_cursor = dispatch_service.list_task_rows(
        None if source_id in {None, "", "all"} else source_id,
        limit=limit,
        offset=offset,
        since=since,
        cursor_sort_time=cursor_sort_time,
        cursor_task_id=cursor_task_id,
    )
    logger.info(
        "alerts_state_tasks duration_ms=%s row_count=%s total=%s source_id=%s limit=%s offset=%s since=%s cursor=%s",
        round((time.monotonic() - started) * 1000, 2),
        len(tasks),
        total,
        source_id or "all",
        limit,
        offset,
        since or "",
        cursor_sort_time or "",
    )
    return tasks, total, next_cursor


def _task_status_counts_payload(source_id: str | None) -> dict[str, int]:
    return dispatch_service.task_status_counts(None if source_id in {None, "", "all"} else source_id)


def _all_alerts_state(
    *,
    limit: int = 120,
    offset: int = 0,
    since: str | None = None,
    cursor_sort_time: str | None = None,
    cursor_task_id: str | None = None,
) -> dict[str, Any]:
    source_ids = _all_source_ids()
    tasks, total_tasks, next_cursor = _state_tasks_payload(
        None,
        limit=limit,
        offset=offset,
        since=since,
        cursor_sort_time=cursor_sort_time,
        cursor_task_id=cursor_task_id,
    )
    status_counts = _task_status_counts_payload(None)
    latest_results = [polling_service.get_latest_result(source_id, include_tasks=False) for source_id in source_ids]
    auto_states = [auto_execution_service.state(source_id) for source_id in source_ids]
    return {
        "source_id": "all",
        "alert_sources": _alert_sources_payload(),
        "fetched_count": sum(result.fetched_count for result in latest_results),
        "queued_count": status_counts.get("queued", 0),
        "updated_count": sum(result.updated_count for result in latest_results),
        "completed_count": status_counts.get("completed", 0),
        "skipped_count": sum(result.skipped_count for result in latest_results),
        "failed_count": status_counts.get("failed", 0),
        "snapshot_complete": all(result.snapshot_complete for result in latest_results) if latest_results else False,
        "auto_execute_enabled": any(state.get("enabled", False) for state in auto_states),
        "auto_execute_running": any(state.get("running", False) for state in auto_states),
        "tasks": tasks,
        "tasks_total": total_tasks,
        "tasks_limit": limit,
        "tasks_offset": offset,
        "tasks_since": since or "",
        "tasks_cursor": next_cursor,
        "errors": [error for result in latest_results for error in result.errors],
    }


def _run_coroutine_in_new_loop(coro):
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        asyncio.set_event_loop(None)
        loop.close()


def _is_successful_ban_action(action_name: str, payload: dict[str, Any]) -> bool:
    normalized_name = action_name.lower().strip()
    combined_text = " ".join(
        [
            normalized_name,
            str(payload.get("action", "")).strip().lower(),
            str(payload.get("result", "")).strip().lower(),
            str(payload.get("message", "")).strip().lower(),
        ]
    )
    if "ban" not in normalized_name and "block" not in normalized_name and "封禁" not in combined_text and "阻断" not in combined_text:
        return False
    if bool(payload.get("error")):
        return False
    success_value = payload.get("success")
    if isinstance(success_value, bool):
        return success_value
    status_value = str(payload.get("status", "")).strip().lower()
    if status_value in {"fail", "failed", "error"}:
        return False
    return True


BAN_IP_FIELDS = {"ban_ip", "banned_ip", "blocked_ip", "ip", "source_ip", "sip", "target", "target_ip", "name"}


def _split_ip_values(value: Any) -> set[str]:
    if isinstance(value, list):
        values: set[str] = set()
        for item in value:
            values.update(_split_ip_values(item))
        return values
    text = str(value or "").strip()
    if not text:
        return set()
    return {item.strip() for item in re.split(r"[,，;；\s]+", text) if item.strip()}


def _extract_ban_ip(payload: dict[str, Any]) -> str:
    for candidate in ("ban_ip", "banned_ip", "blocked_ip", "ip", "source_ip", "sip", "target", "target_ip", "name"):
        values = _split_ip_values(payload.get(candidate, ""))
        if values:
            return ",".join(sorted(values))
    return ""


def _collect_ip_values(payload: Any) -> set[str]:
    values: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key).strip() in BAN_IP_FIELDS:
                values.update(_split_ip_values(value))
            elif isinstance(value, (dict, list)):
                values.update(_collect_ip_values(value))
    elif isinstance(payload, list):
        for item in payload:
            values.update(_collect_ip_values(item))
    return values


def _collect_banned_ips_from_tool_summaries(tool_summaries: Any, *, step_success: bool = True) -> set[str]:
    banned_ips: set[str] = set()
    if not step_success or not isinstance(tool_summaries, list):
        return banned_ips
    for item in tool_summaries:
        if not isinstance(item, dict):
            continue
        args = item.get("args", {})
        if not isinstance(args, dict):
            args = {}
        skill_name = str(args.get("skill_name") or item.get("skill_name") or item.get("name") or "").strip()
        arguments = args.get("arguments", {})
        if not isinstance(arguments, dict):
            arguments = {}
        payload: dict[str, Any] = {
            **arguments,
            **(item.get("key_facts", {}) if isinstance(item.get("key_facts"), dict) else {}),
        }
        if not _is_successful_ban_action(skill_name, payload):
            continue
        banned_ips.update(_collect_ip_values(payload))
    return banned_ips


def _collect_banned_ips_from_workflow_tool_runs(tool_runs: Any) -> set[str]:
    banned_ips: set[str] = set()
    if not isinstance(tool_runs, list):
        return banned_ips
    for item in tool_runs:
        if not isinstance(item, dict):
            continue
        banned_ips.update(
            _collect_banned_ips_from_tool_summaries(
                item.get("tool_calls_summary", []),
                step_success=bool(item.get("success", True)),
            )
        )
    return banned_ips


def _collect_banned_ips_from_result(result: dict[str, Any]) -> set[str]:
    banned_ips: set[str] = set()
    final_facts = result.get("final_facts")
    if isinstance(final_facts, dict):
        disposal = final_facts.get("disposal", {})
        if isinstance(disposal, dict):
            actions = disposal.get("actions", [])
            if isinstance(actions, list):
                for action in actions:
                    if not isinstance(action, dict):
                        continue
                    if str(action.get("kind", "")).strip() != "ban_ip" or not bool(action.get("success")):
                        continue
                    banned_ip = str(action.get("target", "")).strip()
                    if banned_ip:
                        banned_ips.add(banned_ip)
            if banned_ips:
                return banned_ips
    aggregated_action_steps = result.get("aggregated_action_steps")
    if isinstance(aggregated_action_steps, list):
        for step in aggregated_action_steps:
            if not isinstance(step, dict):
                continue
            skill_name = str(step.get("skill_name", "")).strip()
            payload = step.get("result", {})
            if not isinstance(payload, dict):
                continue
            if not _is_successful_ban_action(skill_name, payload):
                continue
            banned_ip = _extract_ban_ip(payload)
            if banned_ip:
                banned_ips.add(banned_ip)
        if banned_ips:
            return banned_ips
    actions = result.get("actions")
    if isinstance(actions, dict):
        for action_name, item in actions.items():
            if action_name == "tool_runs":
                banned_ips.update(_collect_banned_ips_from_workflow_tool_runs(item))
                continue
            if not isinstance(item, dict):
                continue
            if not _is_successful_ban_action(str(action_name), item):
                continue
            banned_ip = _extract_ban_ip(item)
            if banned_ip:
                banned_ips.add(banned_ip)
        if banned_ips:
            return banned_ips
    worker_results = result.get("worker_results")
    if isinstance(worker_results, list):
        for worker_result in worker_results:
            if not isinstance(worker_result, dict):
                continue
            banned_ips.update(
                _collect_banned_ips_from_tool_summaries(
                    worker_result.get("tool_calls_summary", []),
                    step_success=bool(worker_result.get("success", True)),
                )
            )
        if banned_ips:
            return banned_ips
    workflow_runs = result.get("workflow_runs")
    if isinstance(workflow_runs, list):
        for workflow_run in workflow_runs:
            if not isinstance(workflow_run, dict):
                continue
            banned_ips.update(_collect_banned_ips_from_result(workflow_run))
    return banned_ips


def _resolve_result_disposition(result: dict[str, Any]) -> str:
    final_facts = result.get("final_facts")
    if isinstance(final_facts, dict):
        judgment = final_facts.get("judgment", {})
        if isinstance(judgment, dict):
            value = str(judgment.get("disposition", "")).strip()
            if value:
                return value
    return str(result.get("disposition", "")).strip() or "unknown"


def _resolve_task_outcome_status(task, result: dict[str, Any]) -> str:
    final_facts = result.get("final_facts")
    if isinstance(final_facts, dict):
        outcome = final_facts.get("task_outcome", {})
        if isinstance(outcome, dict):
            value = str(outcome.get("status", "")).strip()
            if value:
                return value
    return str(task.status or "").strip()


def _dashboard_summary() -> dict[str, Any]:
    aggregates = dispatch_service.dashboard_aggregates()
    agents = [
        agent
        for agent in list_agent_definitions(AGENT_ROOT, include_system_primary=True)
        if agent.enabled and agent.role == "worker"
    ]
    status_counts = aggregates["status_counts"]
    dispositions = aggregates["dispositions"]

    return {
        "totals": {
            "tasks": aggregates["total_tasks"],
            "queued": status_counts.get("queued", 0),
            "running": status_counts.get("running", 0),
            "awaiting_approval": status_counts.get("awaiting_approval", 0),
            "succeeded": status_counts.get("succeeded", 0),
            "failed": status_counts.get("failed", 0),
            "audit_events": len(audit_service.list_events()),
            "skills": len(skill_runtime.list_skills()),
            "workflows": len(list(WORKFLOW_ROOT.glob("*/workflow.json"))) if WORKFLOW_ROOT.is_dir() else 0,
            "agents": len(agents),
        },
        "judgment": dispositions,
        "operations": {
            "closed_success": aggregates["closed_success"],
            "disposed_success": aggregates["disposed_success"],
            "manual_completed": aggregates["manual_completed"],
            "banned_ip_count": len(aggregates["banned_ips"]),
            "banned_ips": aggregates["banned_ips"],
        },
        "recent_results": aggregates["recent_results"],
    }


@router.get("/dashboard/summary")
def dashboard_summary() -> dict[str, Any]:
    summary = _dashboard_summary()
    summary["automation"] = auto_execution_service.state()
    return summary


@router.get("/alerts/poll")
async def poll_alerts(
    sourceId: str | None = None,
    limit: int = Query(120, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    source_id = _resolve_source_id(sourceId)
    if source_id == "all":
        for current_source_id in _all_source_ids():
            await polling_service.poll_once(current_source_id)
        return _all_alerts_state(limit=limit, offset=offset)
    result = await polling_service.poll_once(source_id)
    auto_state = auto_execution_service.state(source_id)
    tasks, total_tasks, next_cursor = _state_tasks_payload(source_id, limit=limit, offset=offset)
    status_counts = _task_status_counts_payload(source_id)
    payload = _serialize(result)
    payload["tasks"] = tasks
    payload["tasks_total"] = total_tasks
    payload["tasks_limit"] = limit
    payload["tasks_offset"] = offset
    payload["tasks_cursor"] = next_cursor
    payload["queued_count"] = status_counts.get("queued", 0)
    payload["completed_count"] = status_counts.get("completed", 0)
    payload["failed_count"] = status_counts.get("failed", 0)
    payload["auto_execute_enabled"] = auto_state["enabled"]
    payload["auto_execute_running"] = auto_state["running"]
    payload["source_id"] = source_id
    payload["alert_sources"] = _alert_sources_payload()
    return payload


@router.get("/alerts/state")
def alerts_state(
    sourceId: str | None = None,
    limit: int = Query(120, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    since: str | None = None,
    cursorSortTime: str | None = None,
    cursorTaskId: str | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    source_id = _resolve_source_id(sourceId)
    if source_id == "all":
        payload = _all_alerts_state(
            limit=limit,
            offset=offset,
            since=since,
            cursor_sort_time=cursorSortTime,
            cursor_task_id=cursorTaskId,
        )
    else:
        result = polling_service.get_latest_result(source_id, include_tasks=False)
        auto_state = auto_execution_service.state(source_id)
        tasks, total_tasks, next_cursor = _state_tasks_payload(
            source_id,
            limit=limit,
            offset=offset,
            since=since,
            cursor_sort_time=cursorSortTime,
            cursor_task_id=cursorTaskId,
        )
        status_counts = _task_status_counts_payload(source_id)
        payload = _serialize(result)
        payload["tasks"] = tasks
        payload["tasks_total"] = total_tasks
        payload["tasks_limit"] = limit
        payload["tasks_offset"] = offset
        payload["tasks_since"] = since or ""
        payload["tasks_cursor"] = next_cursor
        payload["queued_count"] = status_counts.get("queued", 0)
        payload["completed_count"] = status_counts.get("completed", 0)
        payload["failed_count"] = status_counts.get("failed", 0)
        payload["auto_execute_enabled"] = auto_state["enabled"]
        payload["auto_execute_running"] = auto_state["running"]
        payload["source_id"] = source_id
        payload["alert_sources"] = _alert_sources_payload()
    logger.info(
        "alerts_state duration_ms=%s source_id=%s limit=%s offset=%s since=%s tasks=%s",
        round((time.monotonic() - started) * 1000, 2),
        source_id,
        limit,
        offset,
        since or "",
        len(payload.get("tasks") or []),
    )
    return payload


@router.get("/alerts/summary/period")
def alerts_period_summary(
    since: str = Query(..., min_length=1),
    sourceId: str | None = None,
) -> dict[str, Any]:
    source_id = _resolve_source_id(sourceId)
    effective_source_id = None if source_id == "all" else source_id
    return dispatch_service.period_aggregates(since=since, source_id=effective_source_id)


@router.get("/alerts/state/headlines")
def alerts_state_headlines(
    sourceId: str | None = None,
    since: str | None = None,
    limit: int = Query(200, ge=1, le=500),
) -> dict[str, Any]:
    source_id = _resolve_source_id(sourceId)
    effective_source_id = None if source_id in {"all"} else source_id
    return dispatch_service.list_task_headlines(since=since, source_id=effective_source_id, limit=limit)


@router.get("/alerts/tasks/{task_id}")
def alert_task_detail(task_id: str) -> dict[str, Any]:
    task = dispatch_service.get_task(task_id)
    if task is None:
        return {"task": None}
    return {"task": _serialize(task)}


@router.post("/alerts/tasks/{task_id}/full-report")
async def generate_alert_task_full_report(task_id: str) -> dict[str, Any]:
    task = dispatch_service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在。")
    result_data = dict(task.last_result_data) if isinstance(task.last_result_data, dict) else {}
    existing = str(result_data.get("full_report_markdown") or "").strip()
    if existing:
        return {
            "success": True,
            "task": _serialize(task),
            "markdown": existing,
            "cached": True,
            "format_skill": str((result_data.get("full_report_generation") or {}).get("format_skill", "")).strip(),
        }

    generation = result_data.get("full_report_generation")
    if isinstance(generation, dict) and str(generation.get("status") or "").strip() == "running":
        _start_full_report_generation(task_id)
        return {
            "success": True,
            "task": _serialize(task),
            "markdown": "",
            "cached": False,
            "pending": True,
            "format_skill": str(generation.get("format_skill") or "").strip(),
        }

    result_data["full_report_generation"] = _full_report_generation_payload(
        status="running",
        format_skill=str(getattr(load_runtime_config(), "full_report_format_skill", "") or "output-report").strip() or "output-report",
    )
    updated_task = dispatch_service.update_task_result_data(task_id, result_data)
    if updated_task is None:
        raise HTTPException(status_code=409, detail="完整报告生成启动失败：任务状态已变化。")
    _start_full_report_generation(task_id)
    return {
        "success": True,
        "task": _serialize(updated_task),
        "markdown": "",
        "cached": False,
        "pending": True,
        "format_skill": str(result_data["full_report_generation"].get("format_skill") or "").strip(),
    }



@router.post("/alerts/handle")
async def handle_alert(payload: AlertActionRequest) -> dict[str, Any]:
    alert = _extract_alert_payload(payload)
    event_ids = str(alert.get("eventIds", "")).strip()
    task = _resolve_task(payload)
    task_id = task.task_id if task else ""
    source_id = _resolve_source_id(payload.source_id or (task.source_id if task else None) or str(alert.get("alert_source_id", "")).strip())

    if payload.action == "refresh_poll":
        result = await polling_service.poll_once(source_id)
        return {
            "action": payload.action,
            "success": not result.errors,
            "task_id": task_id,
            "event_ids": event_ids,
            "data": _serialize(result),
            "error": result.errors[0] if result.errors else None,
        }

    if payload.action == "auto_run_pending":
        config = load_runtime_config()
        target_ids = _all_source_ids() if source_id == "all" else [source_id]
        queued_count = 0
        retry_candidate_count = 0
        for current_source_id in target_ids:
            source_config = next((source for source in config.alert_sources if source.id == current_source_id), None)
            retry_interval_seconds = max(int(getattr(source_config, "failed_retry_interval_seconds", 0) or 0), 0)
            queued_count += dispatch_service.task_status_counts(current_source_id).get("queued", 0)
            retry_candidate_count += len(dispatch_service.list_failed_retry_candidates(retry_interval_seconds, max_retry_count=3, source_id=current_source_id))
            auto_execution_service.request_run_once(current_source_id)
        executor_state = _all_alerts_state() if source_id == "all" else auto_execution_service.state(source_id)
        return {
            "action": payload.action,
            "success": True,
            "task_id": "",
            "event_ids": "",
            "data": {
                "background_started": True,
                "queued_count": queued_count,
                "retry_candidate_count": retry_candidate_count,
                "executor_enabled": executor_state.get("auto_execute_enabled", executor_state.get("enabled", False)),
                "executor_running": executor_state.get("auto_execute_running", executor_state.get("running", False)),
                "source_id": source_id,
            },
            "task": None,
            "error": None,
        }

    if payload.action == "auto_execute_start":
        _save_source_auto_execute(source_id, True)
        for current_source_id in (_all_source_ids() if source_id == "all" else [source_id]):
            auto_execution_service.enable(current_source_id)
        return {
            "action": payload.action,
            "success": True,
            "task_id": "",
            "event_ids": "",
            "data": {"auto_execution": _all_alerts_state() if source_id == "all" else auto_execution_service.state(source_id), "source_id": source_id},
            "task": None,
            "error": None,
        }

    if payload.action == "auto_execute_stop":
        _save_source_auto_execute(source_id, False)
        for current_source_id in (_all_source_ids() if source_id == "all" else [source_id]):
            auto_execution_service.disable(current_source_id)
        return {
            "action": payload.action,
            "success": True,
            "task_id": "",
            "event_ids": "",
            "data": {"auto_execution": _all_alerts_state() if source_id == "all" else auto_execution_service.state(source_id), "source_id": source_id},
            "task": None,
            "error": None,
        }

    if payload.action == "retry_task":
        if not task:
            return {
                "action": payload.action,
                "success": False,
                "error": "未找到待重试任务。",
            }
        prepared = dispatch_service.prepare_retry(task.task_id)
        if not prepared:
            return {
                "action": payload.action,
                "success": False,
                "error": "任务重试准备失败。",
            }
        return _serialize(await task_runner_service.run_task(prepared, execution_entry="manual_alert"))

    if payload.action == "force_restart_task":
        if not task:
            return {
                "action": payload.action,
                "success": False,
                "error": "未找到待重新处置任务。",
            }
        prepared = dispatch_service.force_restart_task(task.task_id)
        if not prepared:
            return {
                "action": payload.action,
                "success": False,
                "error": "任务重新处置准备失败。",
            }
        return _serialize(await task_runner_service.run_task(prepared, execution_entry="manual_alert"))

    if not alert:
        return {"action": payload.action, "success": False, "error": "未提供可处理的告警上下文。"}

    if task:
        return _serialize(await task_runner_service.run_task(task, payload.action, execution_entry="manual_alert"))

    return {"action": payload.action, "success": False, "error": "当前动作需要绑定任务上下文。"}

async def _dispatch_command_internal(
    payload: CommandDispatchRequest,
    cancel_event: threading.Event | None = None,
    status_callback=None,
) -> dict[str, Any]:
    if cancel_event is not None and cancel_event.is_set():
        return {"command_text": payload.command_text, "route": "stopped", "success": False, "data": {"interrupted": True}, "error": "已停止当前任务"}
    if not agent_service.is_configured(payload.agent_name):
        return {"command_text": payload.command_text, "route": "agent_not_configured", "success": False, "error": "当前未完成系统主 Agent 配置。"}
    available, reason = agent_service.is_available()
    if not available:
        return {"command_text": payload.command_text, "route": "agent_runtime_unavailable", "success": False, "error": f"当前 Agent Runtime 不可用：{reason}"}

    try:
        execution_context = agent_service._build_execution_context(
            execution_entry="conversation",
            scope_type="conversation",
            scope_ref=uuid4().hex,
        )
        agent_data = await agent_service.run_command(
            payload.command_text,
            payload.history or [],
            cancel_event=cancel_event,
            agent_name=payload.agent_name,
            status_callback=status_callback,
            execution_context=execution_context,
        )
        approval_pending = bool(agent_data.get("approval_pending"))
        route = "approval_required" if approval_pending else "agent_dispatch"
        error = str(agent_data.get("error", "")).strip() or None
        return {
            "command_text": payload.command_text,
            "route": route,
            "success": False if approval_pending else bool(agent_data.get("success", True)),
            "data": agent_data,
            "error": error,
        }
    except Exception as exc:
        if cancel_event is not None and cancel_event.is_set():
            return {"command_text": payload.command_text, "route": "stopped", "success": False, "error": "已停止当前任务"}
        return {"command_text": payload.command_text, "route": "agent_dispatch_failed", "success": False, "error": f"主 Agent 执行失败：{exc}"}


def _build_stream_text(response: dict[str, Any]) -> str:
    data = response.get("data")
    data = data if isinstance(data, dict) else {}
    final_response = str(data.get("final_response", "")).strip()
    if final_response:
        return re.sub(r"\n{3,}", "\n\n", re.sub(r"<think\b[^>]*>.*?</think>", "", final_response, flags=re.IGNORECASE | re.DOTALL)).strip()
    approval_request = data.get("approval_request", {})
    has_approval_request = isinstance(approval_request, dict) and bool(str(approval_request.get("approval_id", "")).strip())
    if bool(data.get("approval_pending")) or has_approval_request:
        return "当前命中了需要审批的 Skill，请在下方确认后继续。"
    error = str(response.get("error", "")).strip()
    if error: return error
    return "命令已执行完成。"

def _stream_command_response(payload: CommandDispatchRequest):
    request_id = uuid4().hex
    cancel_event = threading.Event()
    with active_command_lock:
        active_command_cancellations[request_id] = cancel_event
    result_queue: queue.Queue[tuple[str, Any]] = queue.Queue()

    def run_dispatch() -> None:
        try:
            response = _run_coroutine_in_new_loop(
                _dispatch_command_internal(
                    payload,
                    cancel_event=cancel_event,
                    status_callback=lambda text: result_queue.put(("status", text)),
                )
            )
            result_queue.put(("response", response))
        except Exception as error:
            result_queue.put(("error", error))
        finally:
            with active_command_lock:
                active_command_cancellations.pop(request_id, None)

    worker = threading.Thread(target=run_dispatch, daemon=True)
    worker.start()

    yield f"data: {json.dumps({'type': 'request', 'payload': {'request_id': request_id}}, ensure_ascii=False)}\n\n"
    yield f"data: {json.dumps({'type': 'status', 'payload': {'text': '正在建立会话...'}}, ensure_ascii=False)}\n\n"

    status_messages = [
        "正在分析输入内容...",
        "正在规划处理路径...",
        "正在调用所需能力...",
    ]
    status_index = 0
    last_status_at = 0.0
    last_custom_status_at = 0.0
    started_at = time.monotonic()
    timeout_seconds = _stream_timeout_seconds()

    while True:
        if cancel_event.is_set():
            response = {"command_text": payload.command_text, "route": "stopped", "success": False, "error": "已停止当前任务"}
            break
        try:
            event_type, payload_data = result_queue.get(timeout=0.2)
            if event_type == "status":
                yield f"data: {json.dumps({'type': 'status', 'payload': {'text': str(payload_data)}}, ensure_ascii=False)}\n\n"
                last_custom_status_at = time.monotonic()
                continue
            if event_type == "error":
                raise payload_data
            response = payload_data
            break
        except queue.Empty:
            now = time.monotonic()
            if now - started_at >= timeout_seconds:
                cancel_event.set()
                response = {
                    "command_text": payload.command_text,
                    "route": "agent_dispatch_timeout",
                    "success": False,
                    "error": f"主 Agent 执行超过 {timeout_seconds} 秒，已停止等待。",
                }
                break
            if now - last_custom_status_at < 1.5:
                continue
            if now - last_status_at >= 0.6:
                yield f"data: {json.dumps({'type': 'status', 'payload': {'text': status_messages[min(status_index, len(status_messages) - 1)]}}, ensure_ascii=False)}\n\n"
                last_status_at = now
                if status_index < len(status_messages) - 1:
                    status_index += 1


    stream_text = _build_stream_text(response)
    yield f"data: {json.dumps({'type': 'meta', 'payload': {'route': response.get('route', ''), 'success': response.get('success', False)}}, ensure_ascii=False)}\n\n"
    chunk_size = 18
    for index in range(0, len(stream_text), chunk_size):
        yield f"data: {json.dumps({'type': 'delta', 'payload': {'text': stream_text[index:index + chunk_size]}}, ensure_ascii=False)}\n\n"
        time.sleep(0.03)
    yield f"data: {json.dumps({'type': 'done', 'payload': response}, ensure_ascii=False)}\n\n"


def _build_approval_resolution_response(
    *,
    success: bool,
    route: str,
    approval: dict[str, Any] | None,
    data: dict[str, Any] | None = None,
    task: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "success": success,
        "route": route,
        "approval": approval,
        "data": data if isinstance(data, dict) else {},
        "task": task,
        "error": error,
    }


def _select_current_approval(payload: dict[str, Any], fallback_approval: dict[str, Any]) -> dict[str, Any]:
    approval_request = payload.get("approval_request", {})
    if isinstance(approval_request, dict):
        status = str(approval_request.get("status", "")).strip()
        approval_id = str(approval_request.get("approval_id", "")).strip()
        if approval_id and (status == "pending" or bool(payload.get("approval_pending"))):
            return approval_request

    scope_type = str(payload.get("scope_type", "")).strip()
    scope_ref = str(payload.get("scope_ref", "")).strip()
    if not scope_ref:
        task_payload = payload.get("task")
        if isinstance(task_payload, dict):
            scope_ref = str(task_payload.get("task_id", "")).strip()
    if not scope_type and scope_ref:
        scope_type = "alert_task"
    if scope_type and scope_ref and bool(payload.get("approval_pending")):
        latest = skill_approval_service.get_latest_pending_for_scope(scope_type, scope_ref)
        if latest is not None:
            return skill_approval_service.serialize_approval(latest)
    return fallback_approval


async def _resolve_approval_json(
    approval_id: str,
    decision: str,
    *,
    status_callback=None,
) -> dict[str, Any]:
    approval = skill_approval_service.get_by_id(approval_id)
    if approval is None:
        return _build_approval_resolution_response(
            success=False,
            route="approval_not_found",
            approval=None,
            data={},
            task=None,
            error="找不到待审批记录。",
        )
    if approval.status != "pending":
        latest = skill_approval_service.get_latest_pending_for_scope(approval.scope_type, approval.scope_ref)
        serialized = skill_approval_service.serialize_approval(skill_approval_service.get_by_id(approval_id) or approval)
        if latest is not None:
            return _build_approval_resolution_response(
                success=False,
                route="approval_not_pending",
                approval=skill_approval_service.serialize_approval(latest),
                data={"current_approval": skill_approval_service.serialize_approval(latest)},
                task=_serialize(dispatch_service.get_task(approval.scope_ref)) if approval.scope_type == "alert_task" else None,
                error=f"该审批已处理，请使用最新待审批记录（{latest.approval_id}）。",
            )
        return _build_approval_resolution_response(
            success=False,
            route="approval_not_pending",
            approval=serialized,
            data={"approval": serialized},
            task=None,
            error="该审批已处理。",
        )
    prepared_task = None
    run_log_ref = None
    prepared_selected_action = ""
    if approval.scope_type == "alert_task":
        prepared = task_runner_service.prepare_approval_resume(
            approval.scope_ref,
            approval_id=approval_id,
            decision=decision,
            skill_name=approval.skill_name,
        )
        if not prepared.get("ok"):
            return _build_approval_resolution_response(
                success=False,
                route="approval_resume_state_conflict",
                approval=skill_approval_service.serialize_approval(skill_approval_service.get_by_id(approval_id) or approval),
                data={
                    "current_status": prepared.get("current_status", ""),
                    "task": _serialize(prepared.get("task")),
                },
                task=_serialize(prepared.get("task")),
                error=prepared.get("error") or "审批恢复中止：任务状态已变化。",
            )
        prepared_task = prepared["task"]
        run_log_ref = prepared["run_log_ref"]
        prepared_selected_action = prepared["selected_action"]
    tracer_activated = activate_run_log_tracer(agent_run_log_service, run_log_ref) is not None
    if tracer_activated and run_log_ref is not None:
        agent_run_log_service.append(
            run_log_ref,
            "approval_resolution",
            f"审批决定：{decision}",
            {"approval_id": approval_id, "decision": decision, "skill_name": approval.skill_name},
        )
    try:
        try:
            result = await agent_service.resolve_skill_approval(
                approval_id,
                decision,
                status_callback=status_callback,
            )
        except Exception as exc:
            finalized = None
            if tracer_activated and run_log_ref is not None:
                agent_run_log_service.append(
                    run_log_ref,
                    "agent_exception",
                    "审批恢复执行异常",
                    {"error": str(exc), "approval_id": approval_id},
                    level="error",
                )
            if approval.scope_type == "alert_task":
                finalized = task_runner_service.fail_after_approval_resume(
                    approval.scope_ref,
                    f"审批恢复执行失败：{exc}",
                    prepared_task=prepared_task,
                    run_log_ref=run_log_ref,
                    selected_action=prepared_selected_action,
                )
            return _build_approval_resolution_response(
                success=False,
                route="approval_resolution_failed",
                approval=skill_approval_service.serialize_approval(skill_approval_service.get_by_id(approval_id) or approval),
                data=finalized.get("data", {}) if finalized else {},
                task=_serialize(finalized.get("task")) if finalized else None,
                error=f"审批恢复执行失败：{exc}",
            )
        if approval.scope_type == "alert_task" and not bool(result.get("success", False)):
            payload = result.get("data", {})
            payload = payload if isinstance(payload, dict) else {}
            if not bool(payload.get("approval_pending")):
                raw_error = result.get("error")
                error = str(raw_error).strip() if raw_error is not None and str(raw_error).strip() not in {"", "None"} else "审批恢复执行失败。"
                if tracer_activated and run_log_ref is not None:
                    agent_run_log_service.append(
                        run_log_ref,
                        "approval_resolution_failed",
                        "审批恢复执行失败",
                        {"error": error, "approval_id": approval_id},
                        level="error",
                    )
                finalized = task_runner_service.fail_after_approval_resume(
                    approval.scope_ref,
                    error,
                    prepared_task=prepared_task,
                    run_log_ref=run_log_ref,
                    selected_action=prepared_selected_action,
                )
                return _build_approval_resolution_response(
                    success=False,
                    route=str(result.get("route", "")).strip() or "approval_resolution_failed",
                    approval=skill_approval_service.serialize_approval(skill_approval_service.get_by_id(approval_id) or approval),
                    data=finalized.get("data", {}),
                    task=_serialize(finalized.get("task")),
                    error=error,
                )
        payload = result.get("data", {})
        payload = payload if isinstance(payload, dict) else {}
        serialized_approval = skill_approval_service.serialize_approval(skill_approval_service.get_by_id(approval_id) or approval)
        current_approval = _select_current_approval(payload, serialized_approval)
        if approval.scope_type == "alert_task":
            finalized = task_runner_service.finalize_after_approval(
                approval.scope_ref,
                payload,
                prepared_task=prepared_task,
                run_log_ref=run_log_ref,
                selected_action=prepared_selected_action,
            )
            return _build_approval_resolution_response(
                success=bool(finalized.get("success", False)),
                route=str(result.get("route", "")).strip(),
                approval=current_approval,
                data=finalized.get("data", {}),
                task=_serialize(finalized.get("task")),
                error=None if bool(payload.get("approval_pending")) else finalized.get("error"),
            )
    finally:
        if tracer_activated:
            deactivate_run_log_tracer()
    return _build_approval_resolution_response(
        success=bool(result.get("success", False)),
        route=str(result.get("route", "")).strip(),
        approval=current_approval,
        data=payload,
        task=None,
        error=result.get("error"),
    )


def _stream_approval_resolution(approval_id: str, decision: str):
    result_queue: queue.Queue[tuple[str, Any]] = queue.Queue()

    def run_resolution() -> None:
        try:
            response = _run_coroutine_in_new_loop(
                _resolve_approval_json(
                    approval_id,
                    decision,
                    status_callback=lambda text: result_queue.put(("status", text)),
                )
            )
            result_queue.put(("response", response))
        except Exception as error:
            result_queue.put(("error", error))

    worker = threading.Thread(target=run_resolution, daemon=True)
    worker.start()

    yield f"data: {json.dumps({'type': 'request', 'payload': {'request_id': approval_id}}, ensure_ascii=False)}\n\n"
    initial_status = "正在批准并继续执行..." if decision == "approve" else "正在拒绝并继续推理..."
    yield f"data: {json.dumps({'type': 'status', 'payload': {'text': initial_status}}, ensure_ascii=False)}\n\n"
    status_messages = (
        [
            "正在执行已批准的 Skill...",
            "正在恢复 Agent 推理...",
            "正在整理执行结果...",
        ]
        if decision == "approve"
        else [
            "正在写入拒绝决定...",
            "正在恢复 Agent 推理...",
            "正在整理执行结果...",
        ]
    )
    status_index = 0
    last_status_at = time.monotonic()

    while True:
        try:
            event_type, payload_data = result_queue.get(timeout=0.2)
            if event_type == "status":
                yield f"data: {json.dumps({'type': 'status', 'payload': {'text': str(payload_data)}}, ensure_ascii=False)}\n\n"
                last_status_at = time.monotonic()
                continue
            if event_type == "error":
                response = {
                    "route": "approval_resolution_failed",
                    "success": False,
                    "data": {},
                    "approval": None,
                    "task": None,
                    "error": f"审批恢复执行失败：{payload_data}",
                }
                break
            response = payload_data
            break
        except queue.Empty:
            now = time.monotonic()
            if now - last_status_at >= 1.0:
                yield f"data: {json.dumps({'type': 'status', 'payload': {'text': status_messages[status_index]}}, ensure_ascii=False)}\n\n"
                last_status_at = now
                status_index = (status_index + 1) % len(status_messages)
            continue

    stream_text = _build_stream_text({"data": response.get("data", {}), "error": response.get("error")})
    yield f"data: {json.dumps({'type': 'meta', 'payload': {'route': response.get('route', ''), 'success': response.get('success', False)}}, ensure_ascii=False)}\n\n"
    chunk_size = 18
    for index in range(0, len(stream_text), chunk_size):
        yield f"data: {json.dumps({'type': 'delta', 'payload': {'text': stream_text[index:index + chunk_size]}}, ensure_ascii=False)}\n\n"
        time.sleep(0.03)
    done_payload = {
        "command_text": "",
        "route": response.get("route", ""),
        "success": response.get("success", False),
        "data": response.get("data", {}),
        "approval": response.get("approval"),
        "task": response.get("task"),
        "error": response.get("error"),
    }
    yield f"data: {json.dumps({'type': 'done', 'payload': done_payload}, ensure_ascii=False)}\n\n"

@router.post("/commands/stop")
def stop_command(payload: dict[str, Any]) -> dict[str, Any]:
    request_id = str(payload.get("request_id", "")).strip()
    if not request_id:
        return {"stopped": False, "error": "缺少 request_id"}
    with active_command_lock:
        cancel_event = active_command_cancellations.get(request_id)
    if cancel_event is None:
        return {"stopped": False, "error": "当前任务不存在或已结束"}
    cancel_event.set()
    return {"stopped": True, "request_id": request_id}

@router.post("/commands/dispatch")
async def dispatch_command(payload: CommandDispatchRequest) -> dict[str, Any]:
    return await _dispatch_command_internal(payload)

@router.post("/commands/stream")
def stream_command(payload: CommandDispatchRequest):
    return StreamingResponse(_stream_command_response(payload), media_type="text/event-stream")


@router.get("/approvals/pending")
def list_pending_approvals() -> dict[str, Any]:
    approvals = [skill_approval_service.serialize_approval(item) for item in skill_approval_service.list_pending()]
    return {"approvals": approvals}


@router.post("/approvals/{approval_id}/approve")
async def approve_skill_approval(approval_id: str, payload: ApprovalDecisionRequest) -> Any:
    if payload.stream:
        return StreamingResponse(_stream_approval_resolution(approval_id, "approve"), media_type="text/event-stream")
    return await _resolve_approval_json(approval_id, "approve")


@router.post("/approvals/{approval_id}/reject")
async def reject_skill_approval(approval_id: str, payload: ApprovalDecisionRequest) -> Any:
    if payload.stream:
        return StreamingResponse(_stream_approval_resolution(approval_id, "reject"), media_type="text/event-stream")
    return await _resolve_approval_json(approval_id, "reject")
