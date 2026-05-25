"""
Text extraction and inference utilities for SentinelFlow agent results.

Extracted from agent/service.py to keep SentinelFlowAgentService focused on
orchestration logic. All methods are pure text/data transformations with no
I/O side effects and no dependency on service state.
"""
from __future__ import annotations

import re
from typing import Any


# ── Module-level text helpers ─────────────────────────────────────────────────

THINK_BLOCK_PATTERN = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)


def clean_model_text(text: str) -> str:
    cleaned = THINK_BLOCK_PATTERN.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def normalize_markdown_line(text: str) -> str:
    cleaned = text.strip()
    cleaned = cleaned.strip("|").strip()
    cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"__(.*?)__", r"\1", cleaned)
    cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
    cleaned = cleaned.lstrip("-*#>").strip()
    parts = [part.strip() for part in cleaned.split("|") if part.strip()]
    if parts:
        cleaned = " ".join(parts)
    return cleaned


def extract_json_object(text: str) -> dict[str, Any] | None:
    import json

    cleaned = clean_model_text(text)
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()
    try:
        decoded = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return None
        try:
            decoded = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return decoded if isinstance(decoded, dict) else None


def infer_explicit_disposition_from_text(final_text: str) -> str | None:
    """
    Return disposition only when final_text contains explicit judgment keywords.

    Unlike _infer_disposition(), this returns None when no keyword matched,
    indicating a vague conclusion that should not skip orchestrator synthesis.
    """
    cleaned = clean_model_text(final_text)
    if not cleaned:
        return None
    normalized = cleaned.replace(" ", "")
    lowered = cleaned.lower()
    if any(keyword in normalized for keyword in ("非真实攻击", "不是真实攻击", "并非真实攻击", "不是攻击")):
        if "误报" in normalized:
            return "false_positive"
        return "business_trigger"
    if any(keyword in normalized for keyword in ("规则误报", "误报")):
        return "false_positive"
    if any(keyword in normalized for keyword in ("业务触发", "测试触发", "正常业务", "测试流量", "业务流量", "业务测试")):
        return "business_trigger"
    if any(keyword in normalized for keyword in ("真实攻击", "恶意攻击", "确认攻击", "高危攻击")):
        return "true_attack"
    if any(keyword in lowered for keyword in ("false positive", "false-positive", "false_positive")):
        return "false_positive"
    if any(keyword in lowered for keyword in ("business activity", "benign business", "benign traffic", "business traffic", "test traffic", "business_trigger")):
        return "business_trigger"
    if any(keyword in lowered for keyword in ("true attack", "confirmed attack", "real attack", "malicious attack", "true_attack")):
        return "true_attack"
    return None


# ── Mixin class ───────────────────────────────────────────────────────────────

class TextExtractorMixin:
    """
    Mixin providing text inference methods for SentinelFlowAgentService.

    All methods operate purely on string/dict inputs and are stateless
    with respect to service infrastructure (no DB, no LLM calls).
    """

    def _infer_disposition(self, final_text: str, fallback: str) -> str:
        explicit = infer_explicit_disposition_from_text(final_text)
        if explicit:
            return explicit
        return fallback or "unknown"

    def _infer_summary(self, final_text: str, fallback: str) -> str:
        for line in final_text.splitlines():
            stripped = normalize_markdown_line(line)
            if not stripped:
                continue
            if any(marker in stripped for marker in ("最终分类", "简短理由", "关键依据", "执行结果")):
                continue
            if stripped in {"--", "-", "—"}:
                continue
            if stripped:
                return stripped[:120]
        return fallback

    def _infer_reason(self, final_text: str, alert: dict[str, Any], fallback_judgment) -> str:
        for raw_line in final_text.splitlines():
            normalized = normalize_markdown_line(raw_line)
            if not normalized or normalized in {"--", "-", "—"}:
                continue
            lowered = normalized.lower()
            if any(marker in lowered for marker in ("简短理由", "原因", "理由")):
                parts = re.split(r"[:：]", normalized, maxsplit=1)
                candidate = parts[1].strip() if len(parts) > 1 else normalized
                candidate = candidate.replace("简短理由", "").replace("理由", "").replace("原因", "").strip("：: ").strip()
                if candidate and candidate not in {"--", "-", "—"}:
                    return candidate[:120]

        current = str(alert.get("current_judgment", "")).strip()
        history = str(alert.get("history_judgment", "")).strip()
        alert_name = str(alert.get("alert_name", "")).strip() or "该告警"
        if current:
            return f"{alert_name} 的当前研判信息显示：{current[:90]}"
        if history:
            return f"{alert_name} 的历史处置记录显示：{history[:90]}"
        return fallback_judgment.summary

    def _infer_evidence(self, final_text: str, alert: dict[str, Any], fallback_judgment) -> list[str]:
        evidence: list[str] = []
        capture = False
        for raw_line in final_text.splitlines():
            normalized = normalize_markdown_line(raw_line)
            if not normalized:
                if capture and evidence:
                    break
                continue
            lowered = normalized.lower()
            if any(marker in lowered for marker in ("关键依据", "依据", "证据")):
                capture = True
                parts = re.split(r"[:：]", normalized, maxsplit=1)
                trailing = parts[1].strip() if len(parts) > 1 else ""
                trailing = trailing.replace("关键依据", "").replace("依据", "").replace("证据", "").strip("：: ").strip()
                if trailing and trailing not in {"--", "-", "—"}:
                    evidence.append(trailing[:160])
                continue
            if capture:
                if any(marker in normalized for marker in ("执行结果", "最终分类", "简短理由")):
                    break
                if normalized in {"--", "-", "—"}:
                    continue
                evidence.append(normalized[:160])
                if len(evidence) >= 3:
                    break

        if evidence:
            return evidence[:3]

        fallback = list(getattr(fallback_judgment, "evidence", []) or [])
        if fallback:
            return [str(item).strip()[:160] for item in fallback if str(item).strip()][:3]

        current = str(alert.get("current_judgment", "")).strip()
        history = str(alert.get("history_judgment", "")).strip()
        result: list[str] = []
        if current:
            result.append(f"当前研判：{current[:140]}")
        if history:
            result.append(f"历史处置：{history[:140]}")
        return result[:3]

    def _infer_closure_field(self, skill_runs: list[dict[str, Any]], field_name: str, fallback: str) -> str:
        closure_run = self._select_closure_run(skill_runs, None)  # type: ignore[attr-defined]
        if closure_run is None:
            closure_run = next((run for run in skill_runs if isinstance(run, dict) and bool(run.get("is_closure"))), None)
        if closure_run is None:
            return fallback
        payload = closure_run.get("payload", {})
        if isinstance(payload, dict):
            value = str(payload.get(field_name, "")).strip()
            if value:
                return value
        arguments = closure_run.get("arguments", {})
        if isinstance(arguments, dict):
            value = str(arguments.get(field_name, "")).strip()
            if value:
                return value
        return fallback

    def _default_detail_msg(self, disposition: str) -> str:
        if disposition == "false_positive":
            return "规则误报"
        return "测试/业务触发" if disposition == "business_trigger" else "真实攻击"

    def _default_closure_status(self, disposition: str) -> str:
        return "4" if disposition == "false_positive" else "6"
