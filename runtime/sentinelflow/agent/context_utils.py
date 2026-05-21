from __future__ import annotations

import json
import re
from typing import Any

try:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
except ModuleNotFoundError:  # pragma: no cover
    AIMessage = HumanMessage = SystemMessage = ToolMessage = object  # type: ignore[assignment]


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
DEFAULT_SAFE_GOAL_MAX_CHARS = 500
DEFAULT_SUPERVISOR_PROMPT_TOKEN_BUDGET = 32000
DEFAULT_WORKER_PROMPT_TOKEN_BUDGET = 16000
DEFAULT_RECENT_REACT_MESSAGES = 8
DEFAULT_TOOL_SUMMARY_CHARS = 800
DEFAULT_TOOL_PREVIEW_CHARS = 500

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


def _clip_goal_text(value: Any, limit: int = DEFAULT_SAFE_GOAL_MAX_CHARS) -> tuple[str, dict[str, Any]]:
    raw = str(value or "")
    compacted = compact_text(raw, limit)
    return compacted, {
        "truncated": len(re.sub(r"\s+", " ", raw.strip())) > len(compacted),
        "original_chars": len(raw),
    }


def _payload_noise_reason(text: str) -> str:
    if not text:
        return ""
    if len(text) > 1000:
        return "large_payload"
    lowered = text.lower()
    if "http/1." in lowered or "content-length:" in lowered or "\r\n\r\n" in text or "\n\n" in text:
        return "http_payload"
    if len(re.findall(r"%[0-9a-fA-F]{2}", text)) >= 12:
        return "url_encoded_payload"
    if text:
        control_count = sum(1 for char in text if ord(char) < 32 and char not in "\r\n\t")
        if control_count / max(len(text), 1) >= 0.02:
            return "binary_or_control_payload"
    if re.search(r"(?:[A-Za-z0-9+/]{80,}={0,2}|[A-Fa-f0-9]{120,}|A{120,})", text):
        return "encoded_or_padding_payload"
    return ""


def _alert_summary(alert_data: dict[str, Any]) -> str:
    parts: list[str] = []
    event_id = str(alert_data.get("eventIds") or alert_data.get("event_id") or alert_data.get("alert_id") or "").strip()
    alert_name = str(alert_data.get("alert_name") or alert_data.get("name") or "").strip()
    if event_id or alert_name:
        parts.append("告警")
        if event_id:
            parts.append(event_id)
        if alert_name:
            parts.append(alert_name)
    for field in ("sip", "dip", "sport", "dport"):
        value = str(alert_data.get(field) or "").strip()
        if value:
            parts.append(f"{field}={value}")
    return " ".join(parts).strip()


def build_safe_current_goal(
    *,
    alert_data: dict[str, Any] | None = None,
    arguments: dict[str, Any] | None = None,
    skill_name: str = "",
    max_chars: int = DEFAULT_SAFE_GOAL_MAX_CHARS,
) -> tuple[str, dict[str, Any]]:
    """Build a short navigation goal for validation failures without mutating inputs."""
    data = alert_data if isinstance(alert_data, dict) else {}
    _ = arguments if isinstance(arguments, dict) else {}
    omitted_fields: list[str] = []

    delegated = str(data.get("delegated_task_prompt") or "").strip()
    if delegated:
        reason = _payload_noise_reason(delegated)
        if not reason:
            goal, clip_meta = _clip_goal_text(delegated, max_chars)
            return goal or f"执行 Skill {skill_name}", {
                "source": "delegated_task_prompt",
                **clip_meta,
                "omitted_fields": omitted_fields,
            }
        omitted_fields.append("delegated_task_prompt")

    summary = _alert_summary(data)
    if summary:
        return summary, {
            "source": "alert_summary",
            "truncated": False,
            "original_chars": len(summary),
            "omitted_fields": omitted_fields,
        }

    payload = str(data.get("payload") or "").strip()
    alert_source = str(data.get("alert_source") or "").strip()
    if payload and (alert_source == "human_command" or str(data.get("entry_type") or "").strip() == "conversation"):
        reason = _payload_noise_reason(payload)
        if not reason:
            goal, clip_meta = _clip_goal_text(payload, max_chars)
            return goal or f"执行 Skill {skill_name}", {
                "source": "human_command",
                **clip_meta,
                "omitted_fields": omitted_fields,
            }

    if payload:
        reason = _payload_noise_reason(payload)
        if reason:
            omitted_fields.append("payload")
            return f"payload 摘要：{reason}，长度 {len(payload)} 字符，内容已省略", {
                "source": "payload_summary",
                "truncated": True,
                "original_chars": len(payload),
                "omitted_fields": omitted_fields,
                "reason": reason,
            }
        goal, clip_meta = _clip_goal_text(payload, max_chars)
        return goal or f"执行 Skill {skill_name}", {
            "source": "payload",
            **clip_meta,
            "omitted_fields": omitted_fields,
        }

    return f"执行 Skill {skill_name}", {
        "source": "fallback",
        "truncated": False,
        "original_chars": 0,
        "omitted_fields": omitted_fields,
    }


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


def _message_type(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("type") or message.get("role") or "").strip().lower()
    return str(getattr(message, "type", "") or message.__class__.__name__).strip().lower()


def _message_content(message: Any) -> Any:
    if isinstance(message, dict):
        return message.get("content", "")
    return getattr(message, "content", "")


def _message_tool_call_id(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("tool_call_id", "") or "").strip()
    return str(getattr(message, "tool_call_id", "") or "").strip()


def _message_tool_name(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("name", "") or "").strip()
    return str(getattr(message, "name", "") or "").strip()


def _message_tool_calls(message: Any) -> list[Any]:
    if isinstance(message, dict):
        calls = message.get("tool_calls", [])
    else:
        calls = getattr(message, "tool_calls", [])
    return calls if isinstance(calls, list) else []


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


def _is_missing_schema_value(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _schema_type_matches(value: Any, expected_type: str) -> bool:
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    return True


def validate_skill_input_schema(
    *,
    skill_name: str,
    arguments: dict[str, Any],
    input_schema: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate the small JSON Schema subset used by Skill frontmatter."""
    if not isinstance(input_schema, dict) or not input_schema:
        return {"valid": True, "input_contract": {}, "missing_required_inputs": [], "invalid_inputs": []}

    schema_type = str(input_schema.get("type") or "object").strip() or "object"
    required = input_schema.get("required", [])
    properties = input_schema.get("properties", {})
    additional_properties = input_schema.get("additionalProperties", True)
    if not isinstance(required, list):
        required = []
    if not isinstance(properties, dict):
        properties = {}

    contract = {
        "skill_name": skill_name,
        "action_type": "schema",
        "required": [str(field) for field in required],
        "schema_type": schema_type,
        "additionalProperties": additional_properties if isinstance(additional_properties, bool) else True,
    }
    missing: list[dict[str, str]] = []
    invalid: list[dict[str, str]] = []

    if schema_type != "object":
        invalid.append(
            {
                "field": "$",
                "source": "input_schema",
                "reason": f"当前仅支持 object 类型 Skill input_schema，实际为 {schema_type}。",
            }
        )
        return {
            "valid": False,
            "input_contract": contract,
            "missing_required_inputs": missing,
            "invalid_inputs": invalid,
        }

    for field in contract["required"]:
        if _is_missing_schema_value(arguments.get(field)):
            missing.append(
                {
                    "field": field,
                    "source": "arguments",
                    "reason": f"Skill「{skill_name}」执行前必须提供非空字段 {field}。",
                }
            )

    for field, field_schema in properties.items():
        if field not in arguments or _is_missing_schema_value(arguments.get(field)):
            continue
        if not isinstance(field_schema, dict):
            continue
        expected_type = field_schema.get("type")
        if not isinstance(expected_type, str) or not expected_type.strip():
            continue
        if not _schema_type_matches(arguments[field], expected_type.strip()):
            invalid.append(
                {
                    "field": str(field),
                    "source": "arguments",
                    "reason": f"字段 {field} 类型不符合 input_schema，期望 {expected_type}。",
                }
            )

    allow_additional = additional_properties if isinstance(additional_properties, bool) else True
    if not allow_additional:
        allowed = {str(field) for field in properties.keys()}
        for field in arguments.keys():
            if str(field) not in allowed:
                invalid.append(
                    {
                        "field": str(field),
                        "source": "arguments",
                        "reason": f"字段 {field} 未在 input_schema.properties 中声明。",
                    }
                )

    return {
        "valid": not missing and not invalid,
        "input_contract": contract,
        "missing_required_inputs": missing,
        "invalid_inputs": invalid,
    }


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
    current_goal_meta: dict[str, Any] | None = None,
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
    manifest = {
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
    if current_goal_meta:
        manifest["current_goal_meta"] = _json_safe(current_goal_meta)
    return manifest


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


def compact_tool_payload_for_llm(
    payload: Any,
    *,
    tool_name: str = "",
    tool_call_id: str = "",
    summary_chars: int = DEFAULT_TOOL_SUMMARY_CHARS,
) -> dict[str, Any]:
    parsed = payload if isinstance(payload, dict) else _parse_tool_message_payload(payload)
    parsed_dict = parsed if isinstance(parsed, dict) else {"result": parsed}
    data = parsed_dict.get("data") if isinstance(parsed_dict, dict) else None
    success = parsed_dict.get("success")
    error = parsed_dict.get("error")
    key_facts = extract_key_facts(parsed_dict)
    summary_source = (
        parsed_dict.get("summary")
        or parsed_dict.get("display_summary")
        or parsed_dict.get("short_summary")
        or parsed_dict.get("final_response")
        or data
        or parsed_dict
    )
    compact: dict[str, Any] = {
        "tool": str(tool_name or parsed_dict.get("tool") or parsed_dict.get("worker") or parsed_dict.get("name") or "").strip(),
        "success": bool(success) if success is not None else not bool(error),
        "summary": compact_text(summary_source, summary_chars),
        "key_facts": key_facts,
        "missing_required_inputs": list(parsed_dict.get("missing_required_inputs", []) or [])
        if isinstance(parsed_dict.get("missing_required_inputs", []), list)
        else [],
        "approval_pending": bool(parsed_dict.get("approval_pending")),
        "error": error,
        "result_ref": str(tool_call_id or parsed_dict.get("result_ref") or parsed_dict.get("id") or "").strip(),
    }
    actions = parsed_dict.get("actions_taken") or parsed_dict.get("actions_summary")
    if isinstance(actions, list) and actions:
        compact["actions_taken"] = _json_safe(actions, max_depth=4)
    if parsed_dict.get("approval_request"):
        compact["approval_request"] = _json_safe(parsed_dict.get("approval_request"), max_depth=6)
    return {key: value for key, value in compact.items() if value not in ("", None, [], {})}


def compact_tool_message_for_llm(message: Any, *, summary_chars: int = DEFAULT_TOOL_SUMMARY_CHARS) -> dict[str, Any]:
    return compact_tool_payload_for_llm(
        _message_content(message),
        tool_name=_message_tool_name(message),
        tool_call_id=_message_tool_call_id(message),
        summary_chars=summary_chars,
    )


def compact_prior_step_payload(payload: dict[str, Any]) -> dict[str, Any]:
    tool_calls_summary = payload.get("tool_calls_summary", [])
    if not isinstance(tool_calls_summary, list):
        tool_calls_summary = []
    compact_calls = [
        {
            "name": str(item.get("name", "")).strip(),
            "args": _json_safe(item.get("args", {}), max_depth=4),
            "key_facts": _json_safe(item.get("key_facts", {}), max_depth=4),
            "result_ref": str(item.get("id", "")).strip(),
        }
        for item in tool_calls_summary
        if isinstance(item, dict)
    ]
    final_response = str(payload.get("final_response") or payload.get("display_summary") or payload.get("summary") or "")
    compact: dict[str, Any] = {
        "step": payload.get("step"),
        "worker": str(payload.get("worker") or payload.get("worker_agent") or "").strip(),
        "task_prompt": compact_text(payload.get("task_prompt", ""), 500),
        "summary": compact_text(final_response, DEFAULT_TOOL_SUMMARY_CHARS),
        "success": bool(payload.get("success")),
        "error": payload.get("error"),
        "key_facts": extract_key_facts(payload.get("key_facts", {}), compact_calls),
        "tool_calls_summary": compact_calls[:8],
        "missing_required_inputs": list(payload.get("missing_required_inputs", []) or [])
        if isinstance(payload.get("missing_required_inputs", []), list)
        else [],
        "approval_pending": bool(payload.get("approval_pending")),
    }
    return {key: value for key, value in compact.items() if value not in ("", None, [], {})}


def build_case_context(
    *,
    goal: str = "",
    alert_data: Any = None,
    messages: list[Any] | None = None,
    prior_step_results: Any = None,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    case_context: dict[str, Any] = _json_safe(existing or {}, max_depth=8) if isinstance(existing, dict) else {}
    if goal:
        case_context["goal"] = compact_text(goal, 800)
    alert = alert_data if isinstance(alert_data, dict) else {}
    alert_refs = extract_key_facts(alert)
    if alert_refs:
        merged_refs = case_context.get("alert_refs", {}) if isinstance(case_context.get("alert_refs"), dict) else {}
        case_context["alert_refs"] = {**merged_refs, **alert_refs}

    facts = case_context.get("facts", {}) if isinstance(case_context.get("facts"), dict) else {}
    actions_taken = list(case_context.get("actions_taken", []) or []) if isinstance(case_context.get("actions_taken", []), list) else []
    missing_inputs = list(case_context.get("missing_inputs", []) or []) if isinstance(case_context.get("missing_inputs", []), list) else []
    pending_approvals = list(case_context.get("pending_approvals", []) or []) if isinstance(case_context.get("pending_approvals", []), list) else []
    completed_steps = list(case_context.get("completed_steps", []) or []) if isinstance(case_context.get("completed_steps", []), list) else []
    do_not_repeat = list(case_context.get("do_not_repeat", []) or []) if isinstance(case_context.get("do_not_repeat", []), list) else []

    payload_sources: list[Any] = []
    if isinstance(prior_step_results, list):
        payload_sources.extend(prior_step_results)
    for message in messages or []:
        if _message_type(message) not in {"tool", "toolmessage"}:
            continue
        payload_sources.append(_parse_tool_message_payload(_message_content(message)))

    for index, payload in enumerate(payload_sources, start=1):
        if not isinstance(payload, dict):
            continue
        facts = {**facts, **extract_key_facts(payload)}
        if payload.get("success") is True:
            completed_steps.append(
                {
                    "step": payload.get("step") or index,
                    "worker": payload.get("worker") or payload.get("worker_agent") or payload.get("tool"),
                    "summary": compact_text(payload.get("summary") or payload.get("display_summary") or payload.get("final_response") or payload, 400),
                }
            )
        if payload.get("actions_taken") and isinstance(payload.get("actions_taken"), list):
            actions_taken.extend(_json_safe(payload.get("actions_taken"), max_depth=4))
        elif payload.get("tool_calls_summary") and isinstance(payload.get("tool_calls_summary"), list):
            for call in payload.get("tool_calls_summary", [])[:8]:
                if isinstance(call, dict):
                    actions_taken.append(
                        {
                            "tool": call.get("name"),
                            "args": _json_safe(call.get("args", {}), max_depth=4),
                            "key_facts": _json_safe(call.get("key_facts", {}), max_depth=4),
                        }
                    )
        if payload.get("missing_required_inputs") and isinstance(payload.get("missing_required_inputs"), list):
            missing_inputs.extend(_json_safe(payload.get("missing_required_inputs"), max_depth=4))
        if payload.get("approval_pending"):
            pending_approvals.append(_json_safe(payload.get("approval_request", payload), max_depth=5))
        if payload.get("success") is False or payload.get("error"):
            do_not_repeat.append(
                {
                    "step": payload.get("step") or index,
                    "worker": payload.get("worker") or payload.get("worker_agent") or payload.get("tool"),
                    "error": compact_text(payload.get("error") or payload, 400),
                }
            )

    def _dedupe_list(items: list[Any], limit: int) -> list[Any]:
        deduped: list[Any] = []
        seen: set[str] = set()
        for item in items:
            safe = _json_safe(item, max_depth=6)
            marker = json.dumps(safe, ensure_ascii=False, sort_keys=True)
            if marker in seen:
                continue
            seen.add(marker)
            deduped.append(safe)
        return deduped[-limit:]

    if facts:
        case_context["facts"] = _json_safe(facts, max_depth=8)
    if actions_taken:
        case_context["actions_taken"] = _dedupe_list(actions_taken, 20)
    if missing_inputs:
        case_context["missing_inputs"] = _dedupe_list(missing_inputs, 20)
    if pending_approvals:
        case_context["pending_approvals"] = _dedupe_list(pending_approvals, 10)
    if completed_steps:
        case_context["completed_steps"] = _dedupe_list(completed_steps, 20)
    if do_not_repeat:
        case_context["do_not_repeat"] = _dedupe_list(do_not_repeat, 20)
    return case_context


def format_case_context_for_llm(case_context: dict[str, Any]) -> str:
    return (
        "本次任务 case_context（仅本次 run 内有效；完整审计数据保留在 runtime state/run log）：\n"
        f"```json\n{json.dumps(_json_safe(case_context, max_depth=8), ensure_ascii=False, indent=2)}\n```\n"
    )


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
    include_tool_payload: bool = False,
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
            if include_tool_payload:
                item["tool_payload"] = tool_payload
            data = tool_payload.get("data", {})
            item["result_summary"] = compact_tool_payload_for_llm(
                tool_payload,
                tool_name=str(item.get("name", "")),
                tool_call_id=str(item.get("id", "")),
            )
            item["key_facts"] = extract_key_facts(item.get("key_facts", {}), tool_payload)
            if include_tool_payload:
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
    actions_taken = worker_result.get("actions_taken") or worker_result.get("actions_summary") or []
    compact: dict[str, Any] = {
        "step": worker_result.get("step", 0),
        "worker": str(worker_result.get("worker", worker_result.get("worker_agent", ""))).strip(),
        "task_prompt": str(worker_result.get("task_prompt", "")),
        "summary": compact_text(worker_result.get("summary") or worker_result.get("display_summary") or final_response, 800),
        "display_summary": compact_text(worker_result.get("display_summary") or final_response, 800),
        "skills_used": list(worker_result.get("skills_used", []) or []),
        "tool_calls_summary": [
            {
                "name": str(item.get("name", "")).strip(),
                "args": _json_safe(item.get("args", {}), max_depth=4),
                "key_facts": _json_safe(item.get("key_facts", {}), max_depth=4),
                "result_summary": _json_safe(item.get("result_summary", {}), max_depth=4),
                "id": str(item.get("id", "")).strip(),
                "type": str(item.get("type", "")).strip(),
            }
            for item in tool_calls_summary
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        ],
        "key_facts": key_facts,
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
        "result_ref": str(worker_result.get("result_ref") or worker_result.get("tool_call_id") or "").strip(),
    }
    if isinstance(actions_taken, list) and actions_taken:
        compact["actions_taken"] = _json_safe(actions_taken, max_depth=5)
    if worker_result.get("approval_pending"):
        compact["approval_pending"] = True
        compact["approval_request"] = worker_result.get("approval_request", {})
    return {key: value for key, value in compact.items() if value not in ("", None, [], {})}


def _select_recent_messages(messages: list[Any], *, max_messages: int = DEFAULT_RECENT_REACT_MESSAGES) -> tuple[list[Any], int]:
    if not messages:
        return [], 0
    start = max(0, len(messages) - max_messages)
    while start > 0 and _message_type(messages[start]) in {"tool", "toolmessage"}:
        start -= 1
    return list(messages[start:]), start


def _summarize_older_messages(messages: list[Any]) -> tuple[list[dict[str, Any]], int]:
    summaries: list[dict[str, Any]] = []
    trimmed_tool_messages = 0
    pending_ai: dict[str, dict[str, Any]] = {}
    for index, message in enumerate(messages):
        msg_type = _message_type(message)
        if msg_type in {"ai", "aimessage"}:
            for call in _message_tool_calls(message):
                if isinstance(call, dict):
                    call_id = str(call.get("id", "") or "").strip()
                    if call_id:
                        pending_ai[call_id] = {
                            "tool": str(call.get("name", "") or "").strip(),
                            "args": _json_safe(call.get("args", {}), max_depth=4),
                        }
            continue
        if msg_type in {"tool", "toolmessage"}:
            compact = compact_tool_message_for_llm(message)
            call_id = _message_tool_call_id(message)
            if call_id and call_id in pending_ai:
                compact = {**pending_ai.get(call_id, {}), **compact}
            compact["message_index"] = index
            summaries.append(compact)
            trimmed_tool_messages += 1
            continue
        content = compact_text(_message_content(message), 500)
        if content:
            summaries.append({"role": msg_type or "message", "summary": content, "message_index": index})
    return summaries[-30:], trimmed_tool_messages


def prepare_messages_for_llm(
    *,
    system_msg: Any,
    messages: list[Any],
    alert_data: Any = None,
    current_goal: str = "",
    case_context: dict[str, Any] | None = None,
    scope: str = "worker",
    token_budget: int | None = None,
    recent_message_count: int = DEFAULT_RECENT_REACT_MESSAGES,
) -> tuple[list[Any], dict[str, Any], dict[str, Any]]:
    """Create a bounded prompt view for inference without mutating graph state."""
    all_messages = list(messages or [])
    budget = token_budget or (
        DEFAULT_SUPERVISOR_PROMPT_TOKEN_BUDGET
        if str(scope).strip().lower() in {"supervisor", "orchestrator"}
        else DEFAULT_WORKER_PROMPT_TOKEN_BUDGET
    )
    recent_messages, recent_start = _select_recent_messages(all_messages, max_messages=recent_message_count)
    older_messages = all_messages[:recent_start]
    older_summaries, trimmed_tool_messages = _summarize_older_messages(older_messages)
    before_size = estimate_context_size([_message_content(system_msg), [_message_content(item) for item in all_messages]])
    if before_size.get("estimated_tokens", 0) <= budget and trimmed_tool_messages == 0:
        recent_messages = all_messages
        recent_start = 0
        older_messages = []
        older_summaries = []
    merged_case_context = build_case_context(
        goal=current_goal,
        alert_data=alert_data,
        messages=all_messages,
        existing=case_context,
    )
    control_payload = {
        "current_goal": compact_text(current_goal, 1000),
        "case_context": merged_case_context,
        "older_history_compact": older_summaries,
        "context_window_policy": {
            "state_retention": "完整 messages/checkpoint/run_log 保留在 runtime；本消息仅为 LLM 推理视图。",
            "recent_raw_messages": len(recent_messages),
            "older_tool_results": "旧 ToolMessage 已替换为 compact record；如需完整数据依据 run log/checkpoint 追溯。",
        },
    }
    control_message = HumanMessage(
        content=(
            "LLM 推理窗口控制器（本消息为自动生成的上下文视图）：\n"
            f"```json\n{json.dumps(_json_safe(control_payload, max_depth=10), ensure_ascii=False, indent=2)}\n```"
        )
    )
    prompt_view = [system_msg, control_message] + recent_messages
    after_size = estimate_context_size([_message_content(item) for item in prompt_view])

    # If the compact view still exceeds budget, reduce older summaries first, then recent tail.
    strategy = "case_context+older_summary+recent_raw"
    while after_size.get("estimated_tokens", 0) > budget and older_summaries:
        older_summaries = older_summaries[len(older_summaries) // 2 :]
        control_payload["older_history_compact"] = older_summaries
        control_message = HumanMessage(
            content=(
                "LLM 推理窗口控制器（本消息为自动生成的上下文视图）：\n"
                f"```json\n{json.dumps(_json_safe(control_payload, max_depth=10), ensure_ascii=False, indent=2)}\n```"
            )
        )
        prompt_view = [system_msg, control_message] + recent_messages
        after_size = estimate_context_size([_message_content(item) for item in prompt_view])
        strategy = "case_context+reduced_older_summary+recent_raw"

    while after_size.get("estimated_tokens", 0) > budget and len(recent_messages) > 2:
        recent_messages = recent_messages[2:]
        while recent_messages and _message_type(recent_messages[0]) in {"tool", "toolmessage"}:
            recent_messages = recent_messages[1:]
        prompt_view = [system_msg, control_message] + recent_messages
        after_size = estimate_context_size([_message_content(item) for item in prompt_view])
        strategy = "case_context+reduced_older_summary+reduced_recent_raw"

    window_info = {
        "estimated_tokens_before": before_size.get("estimated_tokens", 0),
        "estimated_tokens_after": after_size.get("estimated_tokens", 0),
        "chars_before": before_size.get("chars", 0),
        "chars_after": after_size.get("chars", 0),
        "token_budget": budget,
        "trimmed_tool_messages": trimmed_tool_messages,
        "kept_recent_messages": len(recent_messages),
        "kept_recent_turns": max(1, len(recent_messages) // 2) if recent_messages else 0,
        "older_summary_count": len(older_summaries),
        "strategy": strategy,
        "scope": scope,
    }
    return prompt_view, window_info, merged_case_context


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
