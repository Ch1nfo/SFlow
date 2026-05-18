"""
Pydantic schemas for SentinelFlow structured LLM outputs.

These schemas are used with LangChain's with_structured_output() to replace
regex/keyword-based text parsing in _serialize_alert_result.
"""
from __future__ import annotations

from typing import Any
from typing import Literal

from pydantic import BaseModel, Field

from sentinelflow.agent.text_extractor import extract_json_object


class AlertJudgment(BaseModel):
    """Structured judgment produced by the synthesis LLM call after Agent execution."""

    disposition: Literal["true_attack", "false_positive", "business_trigger", "unknown"] = Field(
        description=(
            "告警最终判定类型：true_attack（真实攻击）/ false_positive（规则误报）/ "
            "business_trigger（业务触发）/ unknown（无法判断）"
        )
    )
    summary: str = Field(
        default="",
        description="一句话简要结论，不超过 100 字，便于值班人员快速阅读",
    )
    reason: str = Field(
        default="",
        description="判定理由，一句话说明为什么这样判定",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="1-3 条最关键的原始依据，直接从告警数据或 Skill 返回值中取，不要推断",
    )
    execution_result: str = Field(
        default="",
        description="已执行的动作摘要，例如：已查询 IP 信息、已执行结单。未执行任何动作时为空字符串",
    )


def stringify_llm_content(content: Any) -> str:
    """Normalize LangChain/OpenAI message content variants into plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
            elif item is not None:
                parts.append(str(item))
        return "\n".join(parts)
    return "" if content is None else str(content)


def parse_alert_judgment_content(content: str) -> AlertJudgment:
    """Parse raw or fenced JSON LLM content and validate it as AlertJudgment."""
    payload = extract_json_object(content)
    if payload is None:
        raise ValueError("LLM synthesis response did not contain a JSON object.")
    return AlertJudgment.model_validate(payload)
