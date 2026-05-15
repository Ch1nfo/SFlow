from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sentinelflow.domain.enums import AlertDisposition, SkillRuntimeMode, SkillType


@dataclass(slots=True)
class SentinelFlowAlert:
    event_ids: str
    alert_name: str = ""
    sip: str = ""
    dip: str = ""
    payload: str = ""
    response_body: str = ""
    alert_time: str = ""
    alert_source: str = ""
    current_judgment: str = ""
    history_judgment: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SkillCompletionPolicy:
    enabled: bool = False
    action_kind: str = "other"
    completion_effect: str = "none"


@dataclass(slots=True)
class SkillSpec:
    name: str
    type: SkillType
    description: str
    base_dir: Path
    doc_path: Path
    category: str = "other"
    entry: str | None = None
    mode: SkillRuntimeMode | None = None
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    execute_enabled: bool = False
    approval_required: bool = False
    audit_enabled: bool = True
    completion_policy: SkillCompletionPolicy = field(default_factory=SkillCompletionPolicy)


@dataclass(slots=True)
class SkillExecutionRequest:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SkillExecutionResult:
    success: bool
    skill: str
    data: Any = None
    error: str | None = None
    audit_id: str | None = None


@dataclass(slots=True)
class SkillReadResult:
    name: str
    type: SkillType
    description: str
    markdown: str
    executable: bool
    category: str = "other"
    approval_required: bool = False
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    entry: str | None = None
    mode: SkillRuntimeMode | None = None
    completion_policy: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SkillApprovalRecord:
    approval_id: str
    run_id: str
    scope_type: str
    scope_ref: str
    status: str
    skill_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    arguments_fingerprint: str = ""
    approval_required: bool = False
    checkpoint_thread_id: str = ""
    checkpoint_ns: str = ""
    parent_checkpoint_thread_id: str = ""
    parent_checkpoint_ns: str = ""
    tool_call_id: str = ""
    parent_tool_call_id: str = ""
    message: str = ""
    created_at: str = ""
    decided_at: str = ""


@dataclass(slots=True)
class SkillApprovalDecisionResult:
    success: bool
    approval: SkillApprovalRecord | None = None
    route: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass(slots=True)
class JudgmentResult:
    disposition: AlertDisposition
    summary: str
    evidence: list[str] = field(default_factory=list)
    suggested_actions: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AlertHandlingTask:
    task_id: str
    event_ids: str
    workflow_name: str
    title: str
    description: str
    source_id: str = "default"
    source_name: str = "默认告警源"
    alert_time: str = ""
    updated_at: str = ""
    status: str = "queued"
    retry_count: int = 0
    last_action: str = ""
    last_result_success: bool | None = None
    last_result_error: str | None = None
    last_result_data: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PollingDispatchResult:
    fetched_count: int = 0
    queued_count: int = 0
    updated_count: int = 0
    completed_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    snapshot_complete: bool = False
    auto_execute_enabled: bool = False
    auto_execute_running: bool = False
    tasks: list[AlertHandlingTask] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AlertTriageCloseResult:
    event_ids: str
    disposition: str
    summary: str
    memo: str
    detail_msg: str
    closure_status: str
    enrichment: dict[str, Any] = field(default_factory=dict)
    closure_result: dict[str, Any] = field(default_factory=dict)
    success: bool = False


@dataclass(slots=True)
class AlertTriageDisposeResult:
    event_ids: str
    disposition: str
    summary: str
    memo: str
    detail_msg: str
    closure_status: str
    reason: str
    actions: dict[str, Any] = field(default_factory=dict)
    enrichment: dict[str, Any] = field(default_factory=dict)
    closure_result: dict[str, Any] = field(default_factory=dict)
    success: bool = False


@dataclass(slots=True)
class CommandDispatchResult:
    command_text: str
    route: str
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
