from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal


GoalType = Literal[
    "standalone_query",
    "standalone_action",
    "followup",
    "alert_task",
    "report_task",
    "ambiguous",
]
AllowedHistoryUse = Literal[
    "none",
    "object_resolution_only",
    "recent_relevant_turns",
    "task_snapshot",
]

IPV4_PATTERN = re.compile(r"\b(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b")
EVENT_ID_PATTERN = re.compile(r"\b(?:EVT|ALERT|WARN|事件|告警)?[-_:]?[A-Za-z0-9]{8,}\b", re.IGNORECASE)
USER_PATTERN = re.compile(r"\b[a-zA-Z][a-zA-Z0-9_.-]{2,31}\b")

QUERY_MARKERS = ("查", "查询", "看", "信息", "资产", "归属", "使用人", "负责人", "登录", "cmdb", "CMDB", "搜索")
ACTION_MARKERS = ("封禁", "拉黑", "阻断", "处置", "结单", "通知", "转处置中", "重试", "重新处置")
ALERT_MARKERS = ("告警", "事件", "研判", "结单", "处置当前", "处理告警")
REPORT_MARKERS = ("总结", "复盘", "报告", "整理", "回顾", "刚才过程", "本会话")
FOLLOWUP_MARKERS = ("继续", "上一个", "上一条", "刚才", "这个", "它", "该", "再按", "同上", "其", "该IP", "这个IP")
AMBIGUOUS_OBJECT_MARKERS = ("这个告警", "该告警", "这个事件", "该事件", "它的", "这个IP", "该IP")


@dataclass
class ConversationContextPlan:
    goal_type: GoalType
    allowed_history_use: AllowedHistoryUse
    authoritative_inputs: dict[str, Any] = field(default_factory=dict)
    history_messages: list[dict[str, str]] = field(default_factory=list)
    context_policy: dict[str, Any] = field(default_factory=dict)


def build_conversation_context_plan(command_text: str, history: list[dict[str, str]] | None = None) -> ConversationContextPlan:
    text = str(command_text or "").strip()
    raw_history = _normalize_history(history or [])
    current_objects = _extract_objects(text)
    history_objects = _extract_history_objects(raw_history[:6])
    has_followup = _contains_any(text, FOLLOWUP_MARKERS)
    has_report = _contains_any(text, REPORT_MARKERS)
    has_action = _contains_any(text, ACTION_MARKERS)
    has_alert = _contains_any(text, ALERT_MARKERS)
    has_query = _contains_any(text, QUERY_MARKERS)
    has_current_object = _has_any_object(current_objects)
    has_history_object = _has_any_object(history_objects)

    if has_alert and not has_current_object and _contains_any(text, AMBIGUOUS_OBJECT_MARKERS):
        return _plan(
            goal_type="ambiguous",
            allowed_history_use="none",
            authoritative_inputs=current_objects,
            history_messages=[],
            raw_history=raw_history,
            reason="ambiguous_alert_object_requires_clarification",
        )

    if has_report:
        selected = _last_turns(raw_history, 6)
        return _plan(
            goal_type="report_task",
            allowed_history_use="recent_relevant_turns",
            authoritative_inputs=current_objects,
            history_messages=selected,
            raw_history=raw_history,
            reason="report_or_summary_request_uses_recent_history",
        )

    if has_followup and not has_current_object:
        if has_history_object:
            selected = _last_turns(raw_history, 3)
            inputs = dict(current_objects)
            inputs["resolved_from_history"] = history_objects
            return _plan(
                goal_type="followup",
                allowed_history_use="object_resolution_only",
                authoritative_inputs=inputs,
                history_messages=selected,
                raw_history=raw_history,
                reason="followup_resolved_from_recent_history",
            )
        return _plan(
            goal_type="ambiguous",
            allowed_history_use="none",
            authoritative_inputs=current_objects,
            history_messages=[],
            raw_history=raw_history,
            reason="followup_without_resolvable_history_object",
        )

    if has_current_object:
        conflict = _objects_conflict(current_objects, history_objects)
        if has_alert or has_action:
            goal_type: GoalType = "alert_task" if has_alert else "standalone_action"
        else:
            goal_type = "standalone_query" if has_query else "standalone_action"
        return _plan(
            goal_type=goal_type,
            allowed_history_use="none",
            authoritative_inputs=current_objects,
            history_messages=[],
            raw_history=raw_history,
            reason="history_conflict_current_object_wins" if conflict else "current_command_has_authoritative_object",
            conflict=conflict,
        )

    if has_action:
        return _plan(
            goal_type="standalone_action",
            allowed_history_use="none",
            authoritative_inputs=current_objects,
            history_messages=[],
            raw_history=raw_history,
            reason="action_without_explicit_followup_does_not_use_history",
        )

    if has_query:
        return _plan(
            goal_type="standalone_query",
            allowed_history_use="none",
            authoritative_inputs=current_objects,
            history_messages=[],
            raw_history=raw_history,
            reason="query_without_explicit_followup_does_not_use_history",
        )

    return _plan(
        goal_type="standalone_query",
        allowed_history_use="none",
        authoritative_inputs=current_objects,
        history_messages=[],
        raw_history=raw_history,
        reason="default_isolated_conversation_turn",
    )


def _plan(
    *,
    goal_type: GoalType,
    allowed_history_use: AllowedHistoryUse,
    authoritative_inputs: dict[str, Any],
    history_messages: list[dict[str, str]],
    raw_history: list[dict[str, str]],
    reason: str,
    conflict: bool = False,
) -> ConversationContextPlan:
    policy = {
        "goal_type": goal_type,
        "allowed_history_use": allowed_history_use,
        "authoritative_inputs": authoritative_inputs,
        "history_used_count": len(history_messages),
        "history_dropped_count": max(0, len(raw_history) - len(history_messages)),
        "reason": reason,
    }
    if conflict:
        policy["conflict"] = "history_object_differs_from_current_object"
        policy["conflict_rule"] = "use_current_command_object"
    return ConversationContextPlan(
        goal_type=goal_type,
        allowed_history_use=allowed_history_use,
        authoritative_inputs=authoritative_inputs,
        history_messages=history_messages,
        context_policy=policy,
    )


def _normalize_history(history: list[dict[str, str]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip().lower()
        content = str(item.get("content", "")).strip()
        if role not in {"user", "assistant"} or not content:
            continue
        normalized.append({"role": role, "content": content[:1200]})
    return normalized


def _last_turns(history: list[dict[str, str]], max_messages: int) -> list[dict[str, str]]:
    return history[-max_messages:] if len(history) > max_messages else list(history)


def _extract_objects(text: str) -> dict[str, Any]:
    ips = _dedupe(IPV4_PATTERN.findall(text))
    event_ids = _extract_event_ids(text)
    users = _extract_users(text)
    result: dict[str, Any] = {}
    if ips:
        result["ips"] = ips
    if event_ids:
        result["event_ids"] = event_ids
    if users:
        result["users"] = users
    return result


def _extract_history_objects(history: list[dict[str, str]]) -> dict[str, Any]:
    combined = "\n".join(str(item.get("content", "")) for item in history)
    return _extract_objects(combined)


def _extract_event_ids(text: str) -> list[str]:
    candidates = []
    for match in EVENT_ID_PATTERN.findall(text):
        item = str(match).strip("-_:")
        if not item or IPV4_PATTERN.fullmatch(item):
            continue
        if item.lower() in {"cmdb", "sentinelflow"}:
            continue
        if any(ch.isdigit() for ch in item) and len(item) >= 8:
            candidates.append(item)
    return _dedupe(candidates)


def _extract_users(text: str) -> list[str]:
    if not any(marker in text for marker in ("用户", "使用人", "负责人", "通知", "@")):
        return []
    blocked = {"CMDB", "IP", "HTTP", "SOC", "Agent", "SentinelFlow"}
    return _dedupe([item for item in USER_PATTERN.findall(text) if item not in blocked and not item.lower().startswith("http")])


def _has_any_object(objects: dict[str, Any]) -> bool:
    return any(bool(value) for value in objects.values())


def _objects_conflict(current: dict[str, Any], previous: dict[str, Any]) -> bool:
    for key in ("ips", "event_ids", "users"):
        current_values = set(current.get(key) or [])
        previous_values = set(previous.get(key) or [])
        if current_values and previous_values and not current_values.issubset(previous_values):
            return True
    return False


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        value = str(item).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
