from __future__ import annotations

import json
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

IPV4_PATTERN = re.compile(r"(?<![\d.])(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\d.])")
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


async def build_conversation_context_plan_with_llm(
    command_text: str,
    history: list[dict[str, str]] | None = None,
    *,
    llm_kwargs: dict[str, Any] | None = None,
) -> ConversationContextPlan:
    raw_history = _normalize_history(history or [])
    extracted = _conversation_extraction(command_text, raw_history)
    fallback = _build_plan_from_decision(extracted, raw_history, classifier_decision={}, classifier_source="deterministic_fallback")
    if not llm_kwargs:
        return fallback
    try:
        decision = await _classify_context_with_llm(command_text, raw_history, extracted, llm_kwargs)
    except Exception as exc:
        fallback.context_policy["classifier_source"] = "llm_failed_fallback"
        fallback.context_policy["classifier_error"] = str(exc)[:500]
        return fallback
    return _build_plan_from_decision(extracted, raw_history, classifier_decision=decision, classifier_source="llm")


def build_conversation_context_plan(command_text: str, history: list[dict[str, str]] | None = None) -> ConversationContextPlan:
    raw_history = _normalize_history(history or [])
    extracted = _conversation_extraction(command_text, raw_history)
    return _build_plan_from_decision(extracted, raw_history, classifier_decision={}, classifier_source="deterministic")


def _conversation_extraction(command_text: str, raw_history: list[dict[str, str]]) -> dict[str, Any]:
    text = str(command_text or "").strip()
    current_objects = _extract_objects(text)
    history_objects = _extract_history_objects(_last_turns(raw_history, 6))
    return {
        "text": text,
        "current_objects": current_objects,
        "history_objects": history_objects,
        "has_followup": _contains_any(text, FOLLOWUP_MARKERS),
        "has_report": _contains_any(text, REPORT_MARKERS),
        "has_action": _contains_any(text, ACTION_MARKERS),
        "has_alert": _contains_any(text, ALERT_MARKERS),
        "has_query": _contains_any(text, QUERY_MARKERS),
        "has_current_object": _has_any_object(current_objects),
        "has_history_object": _has_any_object(history_objects),
    }


def _build_plan_from_decision(
    extracted: dict[str, Any],
    raw_history: list[dict[str, str]],
    *,
    classifier_decision: dict[str, Any],
    classifier_source: str,
) -> ConversationContextPlan:
    text = str(extracted.get("text") or "")
    current_objects = dict(extracted.get("current_objects") or {})
    history_objects = dict(extracted.get("history_objects") or {})
    has_followup = bool(extracted.get("has_followup"))
    has_report = bool(extracted.get("has_report"))
    has_action = bool(extracted.get("has_action"))
    has_alert = bool(extracted.get("has_alert"))
    has_query = bool(extracted.get("has_query"))
    has_current_object = bool(extracted.get("has_current_object"))
    has_history_object = bool(extracted.get("has_history_object"))

    llm_goal_type = _normalize_goal_type(classifier_decision.get("goal_type"))

    if has_alert and not has_current_object and _contains_any(text, AMBIGUOUS_OBJECT_MARKERS):
        return _with_classifier_metadata(_plan(
            goal_type="ambiguous",
            allowed_history_use="none",
            authoritative_inputs=current_objects,
            history_messages=[],
            raw_history=raw_history,
            reason="ambiguous_alert_object_requires_clarification",
        ), classifier_decision, classifier_source)

    if has_current_object:
        conflict = _objects_conflict(current_objects, history_objects)
        if has_alert or has_action:
            goal_type: GoalType = "alert_task" if has_alert else "standalone_action"
        elif llm_goal_type in {"alert_task", "standalone_action"}:
            goal_type = llm_goal_type
        else:
            goal_type = "standalone_query" if has_query else "standalone_action"
        return _with_classifier_metadata(_plan(
            goal_type=goal_type,
            allowed_history_use="none",
            authoritative_inputs=current_objects,
            history_messages=[],
            raw_history=raw_history,
            reason="history_conflict_current_object_wins" if conflict else "current_command_has_authoritative_object",
            conflict=conflict,
        ), classifier_decision, classifier_source)

    if llm_goal_type == "report_task" or has_report:
        selected = _last_turns(raw_history, 6)
        return _with_classifier_metadata(_plan(
            goal_type="report_task",
            allowed_history_use="recent_relevant_turns",
            authoritative_inputs=current_objects,
            history_messages=selected,
            raw_history=raw_history,
            reason="report_or_summary_request_uses_recent_history",
        ), classifier_decision, classifier_source)

    if llm_goal_type == "followup" or has_followup:
        if has_history_object:
            selected = _last_turns(raw_history, 3)
            inputs = dict(current_objects)
            inputs["resolved_from_history"] = history_objects
            return _with_classifier_metadata(_plan(
                goal_type="followup",
                allowed_history_use="object_resolution_only",
                authoritative_inputs=inputs,
                history_messages=selected,
                raw_history=raw_history,
                reason="followup_resolved_from_recent_history",
            ), classifier_decision, classifier_source)
        return _with_classifier_metadata(_plan(
            goal_type="ambiguous",
            allowed_history_use="none",
            authoritative_inputs=current_objects,
            history_messages=[],
            raw_history=raw_history,
            reason="followup_without_resolvable_history_object",
        ), classifier_decision, classifier_source)

    if llm_goal_type == "ambiguous":
        return _with_classifier_metadata(_plan(
            goal_type="ambiguous",
            allowed_history_use="none",
            authoritative_inputs=current_objects,
            history_messages=[],
            raw_history=raw_history,
            reason="llm_classifier_marked_ambiguous",
        ), classifier_decision, classifier_source)

    if llm_goal_type == "standalone_action" or has_action:
        return _with_classifier_metadata(_plan(
            goal_type="standalone_action",
            allowed_history_use="none",
            authoritative_inputs=current_objects,
            history_messages=[],
            raw_history=raw_history,
            reason="action_without_explicit_followup_does_not_use_history",
        ), classifier_decision, classifier_source)

    if llm_goal_type == "standalone_query" or has_query:
        return _with_classifier_metadata(_plan(
            goal_type="standalone_query",
            allowed_history_use="none",
            authoritative_inputs=current_objects,
            history_messages=[],
            raw_history=raw_history,
            reason="query_without_explicit_followup_does_not_use_history",
        ), classifier_decision, classifier_source)

    return _with_classifier_metadata(_plan(
        goal_type="standalone_query",
        allowed_history_use="none",
        authoritative_inputs=current_objects,
        history_messages=[],
        raw_history=raw_history,
        reason="default_isolated_conversation_turn",
    ), classifier_decision, classifier_source)


async def _classify_context_with_llm(
    command_text: str,
    history: list[dict[str, str]],
    extracted: dict[str, Any],
    llm_kwargs: dict[str, Any],
) -> dict[str, Any]:
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

    history_preview = _last_turns(history, 6)
    payload = {
        "current_command": command_text,
        "deterministic_extraction": {
            "current_objects": extracted.get("current_objects", {}),
            "history_objects": extracted.get("history_objects", {}),
            "has_action_marker": extracted.get("has_action", False),
            "has_alert_marker": extracted.get("has_alert", False),
            "has_query_marker": extracted.get("has_query", False),
            "has_report_marker": extracted.get("has_report", False),
            "has_followup_marker": extracted.get("has_followup", False),
        },
        "recent_history": history_preview,
    }
    llm = ChatOpenAI(**llm_kwargs)
    response = await llm.ainvoke(
        [
            SystemMessage(
                content=(
                    "你是 SentinelFlow 对话上下文分类器。只判断当前用户指令是否需要使用历史上下文，"
                    "不要执行安全处置或业务查询。必须只输出 JSON。"
                )
            ),
            HumanMessage(
                content=(
                    "请分类 current_command 的上下文使用策略。\n"
                    "goal_type 只能是 standalone_query、standalone_action、followup、alert_task、report_task、ambiguous。\n"
                    "判断原则：当前命令明确给出 IP/告警号/对象时通常是 standalone；"
                    "只有“它、这个、继续、上一个”等对象缺失追问才是 followup；"
                    "总结整个刚才过程/本会话才是 report_task；对象不明确且涉及告警/处置为 ambiguous。\n"
                    "输出 JSON 字段：goal_type, needs_history(boolean), history_use, reason。\n\n"
                    f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"
                )
            ),
        ]
    )
    content = getattr(response, "content", response)
    if isinstance(content, list):
        content = "\n".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in content)
    return _parse_classifier_json(str(content or ""))


def _parse_classifier_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            payload = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return payload if isinstance(payload, dict) else {}


def _with_classifier_metadata(
    plan: ConversationContextPlan,
    classifier_decision: dict[str, Any],
    classifier_source: str,
) -> ConversationContextPlan:
    plan.context_policy["classifier_source"] = classifier_source
    goal_type = _normalize_goal_type(classifier_decision.get("goal_type"))
    if goal_type:
        plan.context_policy["classifier_goal_type"] = goal_type
    if "needs_history" in classifier_decision:
        plan.context_policy["classifier_needs_history"] = bool(classifier_decision.get("needs_history"))
    history_use = str(classifier_decision.get("history_use", "") or "").strip()
    if history_use:
        plan.context_policy["classifier_history_use"] = history_use[:120]
    reason = str(classifier_decision.get("reason", "") or "").strip()
    if reason:
        plan.context_policy["classifier_reason"] = reason[:300]
    return plan


def _normalize_goal_type(value: Any) -> GoalType | None:
    candidate = str(value or "").strip()
    allowed = {"standalone_query", "standalone_action", "followup", "alert_task", "report_task", "ambiguous"}
    return candidate if candidate in allowed else None  # type: ignore[return-value]


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
    if history_messages:
        policy["history_context"] = _compact_history_context(history_messages)
    return ConversationContextPlan(
        goal_type=goal_type,
        allowed_history_use=allowed_history_use,
        authoritative_inputs=authoritative_inputs,
        history_messages=history_messages,
        context_policy=policy,
    )


def _compact_history_context(history_messages: list[dict[str, str]]) -> list[dict[str, str]]:
    compacted: list[dict[str, str]] = []
    for item in history_messages[-6:]:
        role = str(item.get("role", "")).strip().lower()
        content = re.sub(r"\s+", " ", str(item.get("content", "")).strip())
        if not role or not content:
            continue
        compacted.append({"role": role, "content": content[:300]})
    return compacted


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
        has_event_hint = bool(re.search(r"(?:告警|事件|工单|event|alert|warn)", item, flags=re.IGNORECASE))
        digit_count = sum(1 for ch in item if ch.isdigit())
        if has_event_hint or digit_count >= 6:
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
