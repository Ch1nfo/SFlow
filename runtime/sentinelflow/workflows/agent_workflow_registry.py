from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class AgentWorkflowStepDefinition:
    id: str
    name: str
    agent: str
    task_prompt: str = ""
@dataclass(frozen=True, slots=True)
class AgentWorkflowDefinition:
    id: str
    name: str
    description: str
    enabled: bool
    scenarios: list[str] = field(default_factory=list)
    selection_keywords: list[str] = field(default_factory=list)
    allowed_alert_source_ids: list[str] = field(default_factory=list)
    steps: list[AgentWorkflowStepDefinition] = field(default_factory=list)
    location: str = ""


def _coerce_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _coerce_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def is_workflow_allowed_for_alert_source(workflow: AgentWorkflowDefinition, alert: dict[str, Any] | None) -> bool:
    allowed_source_ids = {item.strip() for item in workflow.allowed_alert_source_ids if item.strip()}
    if not allowed_source_ids:
        return True
    if not isinstance(alert, dict) or str(alert.get("alert_source", "")).strip() == "human_command":
        return True
    source_id = str(alert.get("alert_source_id") or alert.get("source_id") or "").strip()
    if not source_id:
        return True
    return source_id in allowed_source_ids


def _parse_workflow_file(workflow_dir: Path) -> AgentWorkflowDefinition:
    workflow_json = workflow_dir / "workflow.json"
    if not workflow_json.is_file():
        raise FileNotFoundError(f"Agent workflow not found: {workflow_dir.name}")

    raw = json.loads(workflow_json.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"workflow.json must contain an object: {workflow_json}")

    steps: list[AgentWorkflowStepDefinition] = []
    for index, item in enumerate(raw.get("steps") or [], start=1):
        if not isinstance(item, dict):
            continue
        agent_name = str(item.get("agent", "")).strip()
        task_prompt = str(item.get("task_prompt", "")).strip()
        if not agent_name:
            continue
        step_id = str(item.get("id", "")).strip() or f"step-{index}"
        step_name = str(item.get("name", "")).strip() or step_id
        steps.append(
            AgentWorkflowStepDefinition(
                id=step_id,
                name=step_name,
                agent=agent_name,
                task_prompt=task_prompt,
            )
        )

    return AgentWorkflowDefinition(
        id=workflow_dir.name,
        name=str(raw.get("name") or workflow_dir.name).strip(),
        description=str(raw.get("description") or "").strip(),
        enabled=_coerce_bool(raw.get("enabled"), True),
        scenarios=_coerce_list(raw.get("scenarios")),
        selection_keywords=_coerce_list(raw.get("selection_keywords")),
        allowed_alert_source_ids=_coerce_list(raw.get("allowed_alert_source_ids") or raw.get("allowed_alert_sources")),
        steps=steps,
        location=str(workflow_dir),
    )


def list_agent_workflows(workflow_root: Path) -> list[AgentWorkflowDefinition]:
    if not workflow_root.is_dir():
        return []
    workflows: list[AgentWorkflowDefinition] = []
    for workflow_json in sorted(workflow_root.glob("*/workflow.json")):
        workflows.append(_parse_workflow_file(workflow_json.parent))
    return workflows


def load_agent_workflow(workflow_root: Path, workflow_id: str) -> AgentWorkflowDefinition:
    return _parse_workflow_file(workflow_root / workflow_id)


def serialize_agent_workflow_summary(workflow: AgentWorkflowDefinition) -> dict[str, Any]:
    return {
        "id": workflow.id,
        "name": workflow.name,
        "description": workflow.description,
        "enabled": workflow.enabled,
        "scenarios": workflow.scenarios,
        "allowed_alert_source_ids": workflow.allowed_alert_source_ids,
        "steps_count": len(workflow.steps),
        "step_agents": [step.agent for step in workflow.steps],
        "location": workflow.location,
    }


def serialize_agent_workflow_detail(workflow: AgentWorkflowDefinition) -> dict[str, Any]:
    has_steps = bool(workflow.steps)
    return {
        **serialize_agent_workflow_summary(workflow),
        "selection_keywords": workflow.selection_keywords,
        "steps": [
            {
                "id": step.id,
                "name": step.name,
                "agent": step.agent,
                "task_prompt": step.task_prompt,
            }
            for step in workflow.steps
        ],
        "validation": {
            "valid": has_steps,
            "errors": [] if has_steps else ["Workflow 必须至少包含一个启用步骤。"],
        },
    }
