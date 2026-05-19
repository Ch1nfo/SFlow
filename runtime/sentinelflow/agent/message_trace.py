from __future__ import annotations

import json
from typing import Any


_REASONING_KEYS = (
    "reasoning",
    "reasoning_content",
    "thinking",
    "thought",
    "reasoning_text",
)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _stringify_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
                continue
            if isinstance(block, dict):
                text = block.get("text") or block.get("content")
                if isinstance(text, str) and text.strip():
                    parts.append(text)
        return "\n".join(parts)
    return str(content)


def _reasoning_from_content_blocks(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type", "")).strip().lower()
        if block_type not in {"thinking", "reasoning", "thought"}:
            continue
        for key in ("thinking", "reasoning", "thought", "text", "content"):
            value = block.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
                break
    return "\n\n".join(parts)


def extract_reasoning(message: Any) -> str:
    content = getattr(message, "content", None) if not isinstance(message, dict) else message.get("content")
    from_blocks = _reasoning_from_content_blocks(content)
    if from_blocks:
        return from_blocks
    additional = getattr(message, "additional_kwargs", None)
    if isinstance(message, dict):
        additional = message.get("additional_kwargs", additional)
    if not isinstance(additional, dict):
        additional = {}
    for key in _REASONING_KEYS:
        value = additional.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    response_metadata = getattr(message, "response_metadata", None)
    if isinstance(message, dict):
        response_metadata = message.get("response_metadata", response_metadata)
    if isinstance(response_metadata, dict):
        for key in _REASONING_KEYS:
            value = response_metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def serialize_tool_call(call: Any) -> dict[str, Any]:
    if not isinstance(call, dict):
        return {"raw": _json_safe(call)}
    item = {
        "name": str(call.get("name", "")).strip(),
        "args": _json_safe(call.get("args", {})),
    }
    if call.get("id"):
        item["id"] = str(call.get("id", "")).strip()
    if call.get("type"):
        item["type"] = str(call.get("type", "")).strip()
    return item


def serialize_message(message: Any) -> dict[str, Any]:
    if isinstance(message, dict):
        msg_type = str(message.get("type", "")).strip() or "unknown"
        content = _stringify_content(message.get("content"))
        item: dict[str, Any] = {
            "type": msg_type,
            "content": content,
            "reasoning": str(message.get("reasoning", "")).strip(),
        }
        if message.get("name"):
            item["name"] = str(message.get("name", "")).strip()
        if message.get("tool_call_id"):
            item["tool_call_id"] = str(message.get("tool_call_id", "")).strip()
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            item["tool_calls"] = [serialize_tool_call(call) for call in tool_calls]
        additional = message.get("additional_kwargs")
        if isinstance(additional, dict) and additional:
            item["additional_kwargs"] = _json_safe(additional)
        return item

    msg_type = str(getattr(message, "type", "") or message.__class__.__name__.lower()).strip() or "unknown"
    content = _stringify_content(getattr(message, "content", ""))
    reasoning = extract_reasoning(message)
    item = {
        "type": msg_type,
        "content": content,
        "reasoning": reasoning,
    }
    name = getattr(message, "name", None)
    if name:
        item["name"] = str(name).strip()
    tool_call_id = getattr(message, "tool_call_id", None)
    if tool_call_id:
        item["tool_call_id"] = str(tool_call_id).strip()
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        item["tool_calls"] = [serialize_tool_call(call) for call in tool_calls]
    additional = getattr(message, "additional_kwargs", None)
    if isinstance(additional, dict) and additional:
        item["additional_kwargs"] = _json_safe(additional)
    response_metadata = getattr(message, "response_metadata", None)
    if isinstance(response_metadata, dict) and response_metadata:
        item["response_metadata"] = _json_safe(response_metadata)
    return item


def count_react_turns(messages: list[Any]) -> int:
    count = 0
    for message in messages:
        msg_type = ""
        if isinstance(message, dict):
            msg_type = str(message.get("type", "")).strip().lower()
        else:
            msg_type = str(getattr(message, "type", "") or "").strip().lower()
        if msg_type in {"ai", "aimessage"}:
            count += 1
    return count


_PROMPT_AUDIT_LIMITS = {
    "system": 200_000,
    "human": 80_000,
    "tool": 30_000,
    "ai": 16_000,
}


def _message_role_key(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("type", "")).strip().lower()
    return str(getattr(message, "type", "") or message.__class__.__name__.lower()).strip().lower()


def _truncate_content(content: str, limit: int) -> tuple[str, bool]:
    if len(content) <= limit:
        return content, False
    return f"{content[:limit].rstrip()}...(truncated, total_chars={len(content)})", True


def serialize_messages_for_trace(
    messages: list[Any],
    *,
    max_messages: int = 40,
    max_chars_per_message: int = 6000,
) -> list[dict[str, Any]]:
    serialized, _window = serialize_messages_for_prompt_audit(
        messages,
        max_messages=max_messages,
        per_role_limits={
            "system": max_chars_per_message,
            "human": max_chars_per_message,
            "tool": max_chars_per_message,
            "ai": max_chars_per_message,
        },
    )
    return serialized


_AUDIT_STRIP_KEYS = ("additional_kwargs", "response_metadata")


def compute_prompt_digest(serialized_messages: list[dict[str, Any]]) -> str:
    """Stable digest over the audit-shaped prompt messages for cross-event linking."""
    import hashlib

    hasher = hashlib.sha256()
    for item in serialized_messages:
        role = str(item.get("role") or item.get("type") or "")
        content = str(item.get("content") or "")
        hasher.update(role.encode("utf-8", errors="replace"))
        hasher.update(b"\x1f")
        hasher.update(content.encode("utf-8", errors="replace"))
        hasher.update(b"\x1e")
    return hasher.hexdigest()[:32]


def serialize_message_for_audit(
    message: Any,
    *,
    include_response_metadata: bool = False,
) -> dict[str, Any]:
    """Serialize a message specifically for prompt audit.

    Unlike :func:`serialize_message`, by default this drops ``additional_kwargs``
    and ``response_metadata``: those are *response-side* artifacts (e.g. provider
    reasoning_content) that were NOT part of what was sent to the model. Mixing
    them into a "prompt audit" view would lie about what the model actually saw.
    """
    item = serialize_message(message)
    if not include_response_metadata:
        for key in _AUDIT_STRIP_KEYS:
            item.pop(key, None)
    return item


def serialize_messages_for_prompt_audit(
    messages: list[Any],
    *,
    max_messages: int = 200,
    per_role_limits: dict[str, int] | None = None,
    include_response_metadata: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Serialize full prompt context for audit.

    Returns ``(serialized_messages, window_info)``. ``window_info`` exposes
    whether a message-window cut was applied so callers MUST NOT advertise the
    result as "complete" silently.
    """
    limits = dict(_PROMPT_AUDIT_LIMITS)
    if per_role_limits:
        limits.update(per_role_limits)
    total = len(messages)
    selected = messages[-max_messages:] if total > max_messages else list(messages)
    serialized: list[dict[str, Any]] = []
    offset = total - len(selected)
    for index, message in enumerate(selected):
        item = serialize_message_for_audit(
            message,
            include_response_metadata=include_response_metadata,
        )
        role = _message_role_key(message)
        if role in {"aimessage", "humanmessage", "toolmessage", "systemmessage"}:
            role = role.replace("message", "")
        limit = limits.get(role, limits.get("human", 80_000))
        content = str(item.get("content", "") or "")
        clipped, truncated = _truncate_content(content, limit)
        item["content"] = clipped
        item["content_chars"] = len(content)
        item["content_truncated"] = truncated
        item["role"] = role or str(item.get("type", "")).strip().lower()
        item["message_index"] = offset + index
        serialized.append(item)
    window_info = {
        "total_message_count": total,
        "included_message_count": len(serialized),
        "dropped_message_count": total - len(serialized),
        "max_messages": max_messages,
        "window_truncated": total > len(serialized),
        "drop_strategy": "keep_last" if total > len(serialized) else "none",
        "first_included_message_index": offset if serialized else None,
        "last_included_message_index": offset + len(serialized) - 1 if serialized else None,
    }
    return serialized, window_info


def extract_system_prompt_text(messages: list[Any]) -> dict[str, Any]:
    for index, message in enumerate(messages):
        role = _message_role_key(message)
        if role not in {"system", "systemmessage"}:
            continue
        content = _stringify_content(
            message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
        )
        clipped, truncated = _truncate_content(content, _PROMPT_AUDIT_LIMITS["system"])
        return {
            "message_index": index,
            "content": clipped,
            "content_chars": len(content),
            "content_truncated": truncated,
        }
    return {"message_index": None, "content": "", "content_chars": 0, "content_truncated": False}


def summarize_prompt_stats(
    messages: list[Any],
    serialized_messages: list[dict[str, Any]],
    *,
    window_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    role_chars: dict[str, int] = {}
    truncated_roles: list[str] = []
    for item in serialized_messages:
        role = str(item.get("role", "unknown")).strip() or "unknown"
        role_chars[role] = role_chars.get(role, 0) + int(item.get("content_chars", 0) or 0)
        if item.get("content_truncated"):
            truncated_roles.append(role)
    stats = {
        "message_count": len(messages),
        "logged_message_count": len(serialized_messages),
        "dropped_message_count": (
            int(window_info.get("dropped_message_count", 0))
            if isinstance(window_info, dict)
            else max(0, len(messages) - len(serialized_messages))
        ),
        "window_truncated": (
            bool(window_info.get("window_truncated"))
            if isinstance(window_info, dict)
            else len(messages) > len(serialized_messages)
        ),
        "total_content_chars": sum(role_chars.values()),
        "chars_by_role": role_chars,
        "truncated_roles": sorted(set(truncated_roles)),
        "content_truncated_any": bool(truncated_roles),
    }
    if isinstance(window_info, dict):
        stats["window"] = {
            key: window_info[key]
            for key in (
                "total_message_count",
                "included_message_count",
                "dropped_message_count",
                "max_messages",
                "window_truncated",
                "drop_strategy",
                "first_included_message_index",
                "last_included_message_index",
            )
            if key in window_info
        }
    return stats


def _flatten_string_values(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            hits.extend(_flatten_string_values(item, path))
        return hits
    if isinstance(value, list):
        for index, item in enumerate(value):
            path = f"{prefix}[{index}]" if prefix else f"[{index}]"
            hits.extend(_flatten_string_values(item, path))
        return hits
    if value in (None, "", [], {}):
        return hits
    text = str(value).strip()
    if text:
        hits.append((prefix or "$", text))
    return hits


def _collect_source_index(
    label: str,
    payload: Any,
    *,
    value: str,
    sources: list[dict[str, Any]],
    max_hits: int = 6,
) -> None:
    if len(sources) >= max_hits or not value:
        return
    for path, candidate in _flatten_string_values(payload):
        if candidate != value:
            continue
        sources.append({"kind": label, "path": path, "value": candidate})
        if len(sources) >= max_hits:
            return


def _normalize_tool_calls(tool_calls: Any) -> list[dict[str, Any]]:
    if not isinstance(tool_calls, list):
        return []
    normalized: list[dict[str, Any]] = []
    for call in tool_calls:
        if isinstance(call, dict):
            normalized.append(call)
    return normalized


def extract_skill_tool_calls(tool_calls: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for call in _normalize_tool_calls(tool_calls):
        name = str(call.get("name", "")).strip()
        if name not in {"execute_skill", "execute_skill_no_args"}:
            continue
        args = call.get("args", {})
        if not isinstance(args, dict):
            args = {}
        calls.append(
            {
                "tool_name": name,
                "tool_call_id": str(call.get("id", "")).strip(),
                "skill_name": str(args.get("skill_name", "")).strip(),
                "arguments": _json_safe(args.get("arguments", {}) if isinstance(args.get("arguments"), dict) else args),
                "raw_args": _json_safe(args),
            }
        )
    return calls


def _leaf_argument_paths(arguments: Any, prefix: str = "") -> list[tuple[str, Any]]:
    if not isinstance(arguments, dict):
        return [(prefix or "arguments", arguments)] if arguments not in (None, "", [], {}) else []
    leaves: list[tuple[str, Any]] = []
    for key, value in arguments.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            leaves.extend(_leaf_argument_paths(value, path))
        else:
            leaves.append((path, value))
    return leaves


def infer_tool_argument_sources(
    tool_calls: Any,
    request_messages: list[Any] | None = None,
    alert_data: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Heuristic: where each tool argument value likely came from in prior context."""
    results: list[dict[str, Any]] = []
    for call in _normalize_tool_calls(tool_calls):
        tool_name = str(call.get("name", "")).strip()
        args = call.get("args", {})
        if not isinstance(args, dict):
            args = {}
        skill_name = str(args.get("skill_name", "")).strip() if tool_name in {"execute_skill", "execute_skill_no_args"} else tool_name
        skill_arguments = args.get("arguments", {}) if isinstance(args.get("arguments"), dict) else {}
        argument_paths = _leaf_argument_paths(skill_arguments, "arguments") if skill_arguments else _leaf_argument_paths(args, "args")
        argument_sources: list[dict[str, Any]] = []
        for argument_path, raw_value in argument_paths:
            if raw_value in (None, "", [], {}):
                continue
            value = str(raw_value).strip()
            if not value:
                continue
            sources: list[dict[str, Any]] = []
            if isinstance(alert_data, dict):
                _collect_source_index("alert_data", alert_data, value=value, sources=sources)
            for message_index, message in enumerate(request_messages or []):
                serialized = serialize_message(message)
                msg_type = str(serialized.get("type", "")).strip().lower()
                if msg_type in {"human", "humanmessage", "system", "systemmessage"}:
                    if value in str(serialized.get("content", "")):
                        sources.append(
                            {
                                "kind": "request_message",
                                "message_index": message_index,
                                "role": msg_type,
                                "match": "content_contains",
                            }
                        )
                if msg_type not in {"tool", "toolmessage"}:
                    continue
                parsed = parse_tool_payload(serialized.get("content"))
                if not isinstance(parsed, (dict, list)):
                    continue
                for path, candidate in _flatten_string_values(parsed):
                    if candidate != value:
                        continue
                    sources.append(
                        {
                            "kind": "prior_tool_result",
                            "message_index": message_index,
                            "path": path,
                            "value": candidate,
                        }
                    )
                    if len(sources) >= 8:
                        break
            argument_sources.append(
                {
                    "argument_path": argument_path,
                    "value": _json_safe(raw_value),
                    "sources": sources[:8],
                    "source_found": bool(sources),
                }
            )
        results.append(
            {
                "tool_call_id": str(call.get("id", "")).strip(),
                "tool_name": tool_name,
                "skill_name": skill_name,
                "argument_sources": argument_sources,
            }
        )
    return results


def _summarize_validation_failures(validation: dict[str, Any] | None) -> list[str]:
    if not isinstance(validation, dict):
        return []
    reasons: list[str] = []
    for item in validation.get("missing_required_inputs", []) or []:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field", "")).strip() or "?"
        reason = str(item.get("reason", "")).strip()
        reasons.append(f"missing_required:{field}" + (f" ({reason})" if reason else ""))
    for item in validation.get("invalid_inputs", []) or []:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field", "")).strip() or "?"
        reason = str(item.get("reason", "")).strip()
        reasons.append(f"invalid:{field}" + (f" ({reason})" if reason else ""))
    return reasons


def build_skill_input_audit_record(
    *,
    skill_name: str,
    arguments: dict[str, Any] | None,
    outcome: str,
    compliant: bool,
    error: str = "",
    validation: dict[str, Any] | None = None,
    input_schema: dict[str, Any] | None = None,
    suggested_arguments: dict[str, Any] | None = None,
    execution_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Structured audit payload for Skill input_schema compliance review."""
    normalized_args = arguments if isinstance(arguments, dict) else {}
    validation = validation if isinstance(validation, dict) else {}
    record: dict[str, Any] = {
        "skill_name": str(skill_name or "").strip(),
        "outcome": str(outcome or "").strip(),
        "compliant": bool(compliant),
        "error": str(error or "").strip(),
        "submitted_arguments": _json_safe(normalized_args),
        "input_contract": _json_safe(validation.get("input_contract", {})),
        "missing_required_inputs": _json_safe(validation.get("missing_required_inputs", [])),
        "invalid_inputs": _json_safe(validation.get("invalid_inputs", [])),
        "failure_reasons": _summarize_validation_failures(validation),
    }
    if isinstance(input_schema, dict) and input_schema:
        record["input_schema"] = _json_safe(input_schema)
    if isinstance(suggested_arguments, dict) and suggested_arguments:
        record["suggested_arguments"] = _json_safe(suggested_arguments)
    if isinstance(execution_result, dict) and execution_result:
        record["execution_result"] = _json_safe(execution_result)
    return record


def parse_tool_payload(content: Any) -> Any:
    if not isinstance(content, str) or not content.strip():
        return content
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return content
