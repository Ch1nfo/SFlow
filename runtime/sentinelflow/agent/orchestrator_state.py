from __future__ import annotations

import operator
from typing import Annotated, Any

try:
    from langgraph.graph.message import add_messages
except ModuleNotFoundError:  # pragma: no cover
    def add_messages(existing: list, new: list) -> list:  # type: ignore[override]
        return list(existing or []) + list(new or [])

from typing_extensions import NotRequired, TypedDict


class OrchestratorState(TypedDict):
    """
    Top-level state for the Supervisor + SubGraph orchestrator graph.

    The Supervisor's ReAct loop lives in `messages`.
    Each Worker SubGraph runs in total isolation — only its final_response
    surfaces back as a ToolMessage in the Supervisor's messages.
    """

    # ── Input context ────────────────────────────────────────────────────────
    alert_data: dict[str, Any]           # Original alert payload or command
    action_hint: str                     # "triage_close" | "triage_dispose" | ""
    entry_type: str                      # "conversation" | "alert"

    # ── Supervisor ReAct message history ─────────────────────────────────────
    # Uses LangGraph's add_messages reducer so new messages are appended.
    messages: Annotated[list, add_messages]

    # ── Seed: original user conversation history ──────────────────────────────
    conversation_history: list[dict]     # [{role, content}, ...] — seeded once

    # ── Worker result accumulator ─────────────────────────────────────────────
    # Appended by a post-processing step from ToolMessages after graph finishes.
    worker_results: Annotated[list, operator.add]

    # ── Supervisor system prompt ──────────────────────────────────────────────
    system_prompt_override: NotRequired[str]

    # ── Cancellation signal ───────────────────────────────────────────────────
    cancel_event: NotRequired[Any]
    readable_skills: NotRequired[list[str]]
    executable_skills: NotRequired[list[str]]
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
    case_context: NotRequired[dict[str, Any]]
