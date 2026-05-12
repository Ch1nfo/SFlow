from __future__ import annotations

import json
import re
from typing import Any


KEY_FACT_FIELDS = {
    "to",
    "notify_to",
    "recipient",
    "recipients",
    "recipient_id",
    "receiver",
    "receiver_id",
    "target",
    "target_ip",
    "ip",
    "sip",
    "dip",
    "alert_id",
    "event_id",
    "eventIds",
    "action_object",
    "notification_channel",
    "channel",
    "user",
    "user_id",
    "username",
    "account",
    "chat_id",
    "group_id",
    "mobile",
    "phone",
    "email",
    "webhook",
}

KEY_FACT_ALIASES = {
    "to": ("to", "recipient"),
    "notify_to": ("notify_to", "to", "recipient"),
    "recipient": ("recipient", "to"),
    "receiver": ("receiver", "recipient", "to"),
    "recipient_id": ("recipient_id", "recipient", "to"),
    "receiver_id": ("receiver_id", "recipient", "to"),
}

DEFAULT_KEY_FACT_MAX_DEPTH = 20
DEFAULT_CONTEXT_WARNING_TOKEN_THRESHOLD = 24000

AUTHORITY_PRIORITY = [
    "current_skill_args",
    "current_task_prompt",
    "current_workflow_step",
    "workflow_definition",
    "prior_step_results",
    "original_input",
    "conversation_history",
    "model_summary",
]


def compact_text(value: Any, limit: int = 800) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _json_safe(value: Any, *, max_depth: int | None = None, _depth: int = 0) -> Any:
    if max_depth is not None and _depth >= max_depth:
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item, max_depth=max_depth, _depth=_depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, max_depth=max_depth, _depth=_depth + 1) for item in value]
    return str(value)


def _merge_fact(facts: dict[str, Any], key: str, value: Any) -> None:
    if value in ("", None, [], {}):
        return
    safe_value = _json_safe(value, max_depth=DEFAULT_KEY_FACT_MAX_DEPTH)
    if key not in facts:
        facts[key] = safe_value
        return
    current = facts[key]
    if current == safe_value:
        return
    if not isinstance(current, list):
        current = [current]
    values = current + ([safe_value] if not isinstance(safe_value, list) else safe_value)
    deduped: list[Any] = []
    seen: set[str] = set()
    for item in values:
        safe_item = _json_safe(item, max_depth=DEFAULT_KEY_FACT_MAX_DEPTH)
        marker = json.dumps(safe_item, ensure_ascii=False, sort_keys=True)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(safe_item)
    facts[key] = deduped


def _collect_key_facts(
    value: Any,
    facts: dict[str, Any],
    *,
    depth: int = 0,
    max_depth: int = DEFAULT_KEY_FACT_MAX_DEPTH,
    seen_containers: set[int] | None = None,
) -> None:
    if depth > max_depth:
        return
    if seen_containers is None:
        seen_containers = set()
    if isinstance(value, dict):
        marker = id(value)
        if marker in seen_containers:
            return
        seen_containers.add(marker)
        for key, item in value.items():
            normalized_key = str(key).strip()
            if normalized_key in KEY_FACT_FIELDS:
                for fact_key in KEY_FACT_ALIASES.get(normalized_key, (normalized_key,)):
                    _merge_fact(facts, fact_key, item)
            if isinstance(item, (dict, list, tuple)):
                _collect_key_facts(
                    item,
                    facts,
                    depth=depth + 1,
                    max_depth=max_depth,
                    seen_containers=seen_containers,
                )
    elif isinstance(value, (list, tuple)):
        marker = id(value)
        if marker in seen_containers:
            return
        seen_containers.add(marker)
        for item in value:
            _collect_key_facts(
                item,
                facts,
                depth=depth + 1,
                max_depth=max_depth,
                seen_containers=seen_containers,
            )
    elif isinstance(value, str):
        ips = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", value)
        emails = re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", value)
        if ips:
            _merge_fact(facts, "ip", ips)
        if emails:
            _merge_fact(facts, "email", emails)


def extract_key_facts(*values: Any, max_depth: int = DEFAULT_KEY_FACT_MAX_DEPTH) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    for value in values:
        _collect_key_facts(value, facts, max_depth=max_depth)
    return facts


def estimate_context_size(value: Any) -> dict[str, int]:
    """Return a small, deterministic size estimate for observability only."""
    try:
        text = json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True)
    except Exception:
        text = str(value)
    chars = len(text)
    return {
        "chars": chars,
        "estimated_tokens": max(1, chars // 4) if chars else 0,
    }


def _fact_values(value: Any) -> list[Any]:
    if value in ("", None, [], {}):
        return []
    return value if isinstance(value, list) else [value]


def resolve_authoritative_facts(**sources: Any) -> dict[str, Any]:
    """Build a fact index with source priority without replacing raw inputs."""
    ordered_sources: list[tuple[str, Any]] = []
    for name in AUTHORITY_PRIORITY:
        if name in sources:
            ordered_sources.append((name, sources.get(name)))
    for name, value in sources.items():
        if name not in AUTHORITY_PRIORITY:
            ordered_sources.append((name, value))

    facts: dict[str, Any] = {}
    trace: list[dict[str, Any]] = []
    conflicts: dict[str, list[dict[str, Any]]] = {}
    for priority, (source_name, source_value) in enumerate(ordered_sources, start=1):
        source_facts = extract_key_facts(source_value)
        if not source_facts:
            continue
        for key, value in source_facts.items():
            values = _fact_values(value)
            if not values:
                continue
            if key not in facts:
                facts[key] = value
                trace.append({"fact": key, "source": source_name, "priority": priority})
                continue
            existing_values = {
                json.dumps(_json_safe(item), ensure_ascii=False, sort_keys=True)
                for item in _fact_values(facts[key])
            }
            new_values = {
                json.dumps(_json_safe(item), ensure_ascii=False, sort_keys=True)
                for item in values
            }
            if new_values - existing_values:
                conflicts.setdefault(key, []).append(
                    {"source": source_name, "priority": priority, "value": _json_safe(value)}
                )
    return {
        "facts": facts,
        "authority_trace": trace,
        "conflicts": conflicts,
        "priority_order": AUTHORITY_PRIORITY,
    }


def _has_any(data: dict[str, Any], keys: tuple[str, ...]) -> bool:
    for key in keys:
        value = data.get(key)
        if value not in ("", None, [], {}):
            return True
    return False


def _missing_field(field: str, reason: str, source: str = "arguments") -> dict[str, str]:
    return {"field": field, "source": source, "reason": reason}


def validate_execution_inputs(
    *,
    skill_name: str = "",
    arguments: dict[str, Any] | None = None,
    task_prompt: str = "",
) -> dict[str, Any]:
    """Check only hard execution parameters; never mutate or infer args."""
    normalized_name = str(skill_name or "").strip().lower()
    compact_name = normalized_name.replace("-", "").replace("_", "").replace(" ", "")
    args = arguments if isinstance(arguments, dict) else {}
    prompt_text = str(task_prompt or "")
    missing: list[dict[str, str]] = []
    contract = {"skill_name": skill_name, "action_type": "generic", "required": []}

    is_contact = any(marker in compact_name for marker in ("contact", "hiklink", "sendhiklink"))
    is_closure_like = compact_name in {"exec", "calling", "close", "soccalling"} or any(
        marker in compact_name for marker in ("close", "closure", "ticketclose", "结单", "闭环")
    )

    if is_contact:
        contract = {"skill_name": skill_name, "action_type": "contact", "required": ["to", "body"]}
        if not _has_any(args, ("to",)):
            missing.append(_missing_field("to", "联系/通知类 Skill 执行前必须有明确收信人。"))
        if not _has_any(args, ("body",)):
            missing.append(_missing_field("body", "联系/通知类 Skill 执行前必须有明确消息内容。"))
    elif is_closure_like:
        contract = {"skill_name": skill_name, "action_type": "closure_or_status_update", "required": ["eventIds", "status"]}
        if not _has_any(args, ("eventIds", "event_id", "alert_id")):
            missing.append(_missing_field("eventIds", "告警状态更新/结单类 Skill 执行前必须有明确告警 ID。"))
        if not _has_any(args, ("status", "closeStatus", "close_status")):
            missing.append(_missing_field("status", "告警状态更新/结单类 Skill 执行前必须有明确目标状态。"))

    if missing and prompt_text:
        contract["task_prompt_size"] = estimate_context_size(prompt_text)
    return {
        "valid": not missing,
        "input_contract": contract,
        "missing_required_inputs": missing,
    }


def build_context_manifest(
    *,
    current_goal: str = "",
    entry_type: str = "",
    current_step: Any = None,
    original_input: Any = None,
    current_task_prompt: str = "",
    current_skill_args: dict[str, Any] | None = None,
    workflow_definition: Any = None,
    prior_step_results: Any = None,
    conversation_history: Any = None,
    model_summary: Any = None,
    input_contract: dict[str, Any] | None = None,
    missing_required_inputs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    authority = resolve_authoritative_facts(
        current_skill_args=current_skill_args or {},
        current_task_prompt=current_task_prompt,
        current_workflow_step=current_step or {},
        workflow_definition=workflow_definition or {},
        prior_step_results=prior_step_results or [],
        original_input=original_input or {},
        conversation_history=conversation_history or [],
        model_summary=model_summary or "",
    )
    payload_for_size = {
        "current_goal": current_goal,
        "current_step": current_step,
        "original_input": original_input,
        "current_task_prompt": current_task_prompt,
        "current_skill_args": current_skill_args or {},
        "workflow_definition": workflow_definition or {},
        "prior_step_results": prior_step_results or [],
        "conversation_history": conversation_history or [],
        "model_summary": model_summary or "",
    }
    size = estimate_context_size(payload_for_size)
    warnings: list[str] = []
    if size.get("estimated_tokens", 0) >= DEFAULT_CONTEXT_WARNING_TOKEN_THRESHOLD:
        warnings.append("context_size_large")
    conflicts = authority.get("conflicts", {})
    if isinstance(conflicts, dict) and conflicts:
        warnings.append("authority_fact_conflict")
    return {
        "current_goal": str(current_goal or current_task_prompt or "").strip(),
        "entry_type": str(entry_type or "").strip(),
        "current_step": _json_safe(current_step or {}),
        "required_objects": list((input_contract or {}).get("required", []) or []),
        "available_facts": authority.get("facts", {}),
        "authoritative_sources": AUTHORITY_PRIORITY,
        "authority_trace": authority.get("authority_trace", []),
        "fact_conflicts": conflicts,
        "input_contract": input_contract or {},
        "missing_required_inputs": missing_required_inputs or [],
        "context_size": size,
        "context_warnings": warnings,
    }


def format_context_manifest_header(manifest: dict[str, Any]) -> str:
    return (
        "SOC 执行上下文控制器（导航信息，不替代原始执行数据）：\n"
        f"```json\n{json.dumps(_json_safe(manifest), ensure_ascii=False, indent=2)}\n```\n\n"
        "执行依据优先级：当前 skill args > 当前子 Agent task_prompt > 当前 workflow step > "
        "workflow description/task > 前置步骤真实结果 > 告警原始字段 > 对话历史 > 模型摘要。\n"
        "如果对象冲突，使用最高优先级来源；如果关键对象缺失，先查询或明确说明缺失，不要编造。\n"
    )


def _parse_tool_message_payload(content: Any) -> Any:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"raw": content}
    if isinstance(content, dict):
        return content
    return {"result": content}


def _tool_payloads_by_id(tool_messages: Any) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    if not isinstance(tool_messages, list):
        return payloads
    for message in tool_messages:
        if isinstance(message, dict):
            msg_type = str(message.get("type", "")).strip()
            tool_call_id = str(message.get("tool_call_id", "")).strip()
            content = message.get("content", "")
        else:
            msg_type = str(getattr(message, "type", "")).strip()
            tool_call_id = str(getattr(message, "tool_call_id", "")).strip()
            content = getattr(message, "content", "")
        if msg_type != "tool" or not tool_call_id:
            continue
        payload = _parse_tool_message_payload(content)
        if isinstance(payload, dict):
            payloads[tool_call_id] = _json_safe(payload)
    return payloads


def summarize_tool_calls(
    tool_calls: Any,
    *,
    limit: int | None = None,
    tool_messages: Any = None,
) -> list[dict[str, Any]]:
    if not isinstance(tool_calls, list):
        return []
    summaries: list[dict[str, Any]] = []
    selected_calls = tool_calls if limit is None else tool_calls[:limit]
    payloads_by_id = _tool_payloads_by_id(tool_messages)
    for call in selected_calls:
        if not isinstance(call, dict):
            continue
        args = call.get("args", {})
        if not isinstance(args, dict):
            args = {}
        item = {
            "name": str(call.get("name", "")).strip(),
            "args": _json_safe(args),
            "key_facts": extract_key_facts(args),
        }
        if call.get("id"):
            item["id"] = str(call.get("id", "")).strip()
        if call.get("type"):
            item["type"] = str(call.get("type", "")).strip()
        tool_payload = payloads_by_id.get(str(call.get("id", "")).strip())
        if isinstance(tool_payload, dict):
            item["tool_payload"] = tool_payload
            data = tool_payload.get("data", {})
            if isinstance(data, dict):
                item["payload"] = data
            elif data not in (None, ""):
                item["payload"] = {"result": data}
        summaries.append(item)
    return [item for item in summaries if item.get("name")]


def compact_worker_result_for_llm(worker_result: dict[str, Any]) -> dict[str, Any]:
    tool_calls_summary = worker_result.get("tool_calls_summary", [])
    if not isinstance(tool_calls_summary, list) or not tool_calls_summary:
        tool_calls_summary = summarize_tool_calls(worker_result.get("tool_calls", []))
    key_facts = extract_key_facts(
        worker_result.get("key_facts", {}),
        tool_calls_summary,
        worker_result.get("final_response", ""),
    )
    final_response = str(worker_result.get("final_response", ""))
    error = worker_result.get("error")
    compact: dict[str, Any] = {
        "step": worker_result.get("step", 0),
        "worker": str(worker_result.get("worker", worker_result.get("worker_agent", ""))).strip(),
        "task_prompt": str(worker_result.get("task_prompt", "")),
        "final_response": final_response,
        "display_summary": compact_text(final_response, 1600),
        "skills_used": list(worker_result.get("skills_used", []) or []),
        "tool_calls_summary": tool_calls_summary,
        "key_facts": key_facts,
        "context_manifest": worker_result.get("context_manifest", {}),
        "context_warnings": list(worker_result.get("context_warnings", []) or []),
        "input_contract": worker_result.get("input_contract", {}),
        "missing_required_inputs": list(worker_result.get("missing_required_inputs", []) or []),
        "authority_trace": (
            (worker_result.get("context_manifest", {}) or {}).get("authority_trace", [])
            if isinstance(worker_result.get("context_manifest", {}), dict)
            else []
        ),
        "success": bool(worker_result.get("success")),
        "error": error,
    }
    if worker_result.get("approval_pending"):
        compact["approval_pending"] = True
        compact["approval_request"] = worker_result.get("approval_request", {})
    return compact


def build_context_envelope(
    *,
    original_input: Any,
    delegated_task: str = "",
    workflow_step: dict[str, Any] | None = None,
    prior_facts: dict[str, Any] | None = None,
    authoritative_inputs: dict[str, Any] | None = None,
    constraints: list[str] | None = None,
) -> dict[str, Any]:
    default_constraints = [
        "当前任务以 delegated_task / workflow_step 为准，原始输入只作为背景。",
        "不得从历史噪声中猜测发送对象、处置对象或结单对象。",
        "关键对象只能来自当前任务、authoritative_inputs、原始输入中的明确字段或 prior_facts。",
        "如果关键对象缺失，必须说明缺失，不要编造。",
    ]
    return {
        "original_input": _json_safe(original_input),
        "delegated_task": delegated_task,
        "workflow_step": workflow_step or {},
        "prior_facts": prior_facts or {},
        "authoritative_inputs": _json_safe(authoritative_inputs or {}),
        "constraints": list(constraints or default_constraints),
    }
