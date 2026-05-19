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


def extract_reasoning(message: Any) -> str:
    additional = getattr(message, "additional_kwargs", None)
    if not isinstance(additional, dict):
        additional = {}
    for key in _REASONING_KEYS:
        value = additional.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    response_metadata = getattr(message, "response_metadata", None)
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


def parse_tool_payload(content: Any) -> Any:
    if not isinstance(content, str) or not content.strip():
        return content
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return content
