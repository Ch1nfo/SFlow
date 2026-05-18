from __future__ import annotations

from typing import Annotated, Any

try:
    from langgraph.graph.message import add_messages
except ModuleNotFoundError:  # pragma: no cover
    def add_messages(existing: list, new: list) -> list:  # type: ignore[override]
        return list(existing or []) + list(new or [])

from typing_extensions import NotRequired, TypedDict


class SentinelFlowAgentState(TypedDict):
    alert_data: dict[str, Any]
    messages: Annotated[list, add_messages]
    event_id_ref: NotRequired[str]
    input_seeded: NotRequired[bool]
    cancel_event: NotRequired[Any]
    readable_skills: NotRequired[list[str]]
    executable_skills: NotRequired[list[str]]
    system_prompt_override: NotRequired[str]
    agent_name: NotRequired[str]
    approval_pending: NotRequired[bool]
    approval_request: NotRequired[dict[str, Any]]
    run_id: NotRequired[str]
    execution_entry: NotRequired[str]
    scope_type: NotRequired[str]
    scope_ref: NotRequired[str]
    checkpoint_thread_id: NotRequired[str]
    graph_checkpoint_ns: NotRequired[str]
    parent_checkpoint_thread_id: NotRequired[str]
    parent_checkpoint_ns: NotRequired[str]
    parent_tool_call_id: NotRequired[str]
    approved_fingerprints: NotRequired[list[str]]
    rejected_fingerprints: NotRequired[list[str]]
    executed_skill_cache: NotRequired[dict[str, Any]]
    structured_judgment: NotRequired[dict[str, Any] | None]  # AlertJudgment.model_dump() or None
