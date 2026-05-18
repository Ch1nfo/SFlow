import re
import shutil
import json
from pathlib import Path
from typing import Any
from fastapi import HTTPException
from sentinelflow.api.schemas import AlertActionRequest, AgentCreateRequest
from sentinelflow.api.deps import dispatch_service, SKILL_ROOT, AGENT_ROOT, WORKFLOW_ROOT, PROJECT_ROOT, PLATFORM_ROOT, branding, agent_service
from sentinelflow.agent.registry import SYSTEM_PRIMARY_AGENT_NAME, list_agent_definitions, load_agent_definition

VISIBLE_RUNTIME_OVERRIDE_KEYS = {
    "poll_interval_seconds", "failed_retry_interval_seconds", "agent_enabled", "llm_api_base_url", "llm_api_key",
    "llm_model", "llm_temperature", "llm_timeout", "weekly_alert_cleanup_enabled", "run_log_retention_days", "alert_source_enabled",
    "alert_source_type", "alert_source_url", "alert_source_method",
    "alert_source_headers", "alert_source_query", "alert_source_body",
    "alert_source_timeout", "alert_source_sample_payload", "alert_parser_rule",
    "alert_script_code", "alert_script_timeout", "alert_sources",
    "rag_enabled", "rag_knowledge_id", "rag_api_key", "rag_top_k",
    "rag_similarity_threshold", "rag_retrieve_strategy", "rag_enable_rerank_model", "rag_rerank_model",
}


def _resolve_task(payload: AlertActionRequest):
    if not isinstance(payload.task, dict):
        return None
    task_id = str(payload.task.get("task_id", "")).strip()
    if task_id:
        task = dispatch_service.get_task(task_id)
        if task:
            return task
    event_ids = str(payload.task.get("event_ids", "")).strip()
    if event_ids:
        source_id = str(payload.task.get("source_id") or payload.task.get("sourceId") or "").strip()
        return dispatch_service.get_task_by_event_id(event_ids, source_id=source_id or None)
    return None


def _extract_alert_payload(payload: AlertActionRequest) -> dict[str, Any]:
    if isinstance(payload.alert, dict):
        return payload.alert
    if isinstance(payload.task, dict):
        task_payload = payload.task.get("payload")
        if isinstance(task_payload, dict):
            alert_data = task_payload.get("alert_data")
            if isinstance(alert_data, dict):
                return alert_data
    return {}


def _slugify(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return text or "sentinelflow-item"


def _normalize_workflow_id(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-_")
    return text or "sentinelflow-workflow"


def _normalized_display_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).lower()


def _read_skill_name_from_md(skill_dir: Path) -> str:
    """Read the skill display name from SKILL.md frontmatter (name field)."""
    doc = skill_dir / "SKILL.md"
    if not doc.is_file():
        return skill_dir.name
    text = doc.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return skill_dir.name
    for line in lines[1:]:
        if line.strip() == "---":
            break
        match = re.match(r"^name:\s*(.+?)\s*$", line)
        if match:
            return match.group(1).strip()
    return skill_dir.name


def _iter_existing_plugin_entries() -> list[tuple[str, str, str]]:
    import json
    entries: list[tuple[str, str, str]] = []
    if SKILL_ROOT.is_dir():
        for path in SKILL_ROOT.iterdir():
            if not path.is_dir():
                continue
            display_name = _read_skill_name_from_md(path)
            entries.append(("skill", path.name.strip().lower(), _normalized_display_name(display_name)))
    if AGENT_ROOT.is_dir():
        for path in AGENT_ROOT.glob("*/agent.yaml"):
            agent_dir = path.parent
            display_name = agent_dir.name
            match = re.search(r"^name:\s*(.+?)\s*$", path.read_text(encoding="utf-8"), re.MULTILINE)
            if match:
                display_name = match.group(1).strip()
            entries.append(("agent", agent_dir.name.strip().lower(), _normalized_display_name(display_name)))
    else:
        entries.append(("agent", SYSTEM_PRIMARY_AGENT_NAME, _normalized_display_name(SYSTEM_PRIMARY_AGENT_NAME)))
    if WORKFLOW_ROOT.is_dir():
        for path in WORKFLOW_ROOT.glob("*/workflow.json"):
            workflow_dir = path.parent
            display_name = workflow_dir.name
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                raw = {}
            if isinstance(raw, dict) and raw.get("name"):
                display_name = str(raw.get("name")).strip()
            entries.append(("workflow", workflow_dir.name.strip().lower(), _normalized_display_name(display_name)))
    return entries


def _assert_unique_plugin_name(display_name: str, slug_name: str, kind: str, current_name: str | None = None) -> None:
    normalized_display = _normalized_display_name(display_name)
    normalized_slug = slug_name.strip().lower()
    if not normalized_display or not normalized_slug:
        raise HTTPException(status_code=400, detail="名称不能为空。")
    entries = _iter_existing_plugin_entries()
    for existing_kind, existing_slug, existing_display in [item for item in entries if item[0] == kind]:
        if current_name and existing_slug == current_name.strip().lower():
            continue
        kind_label = {"skill": "Skill", "agent": "Agent", "workflow": "Workflow"}.get(existing_kind, existing_kind)
        if existing_display == normalized_display:
            raise HTTPException(status_code=409, detail=f'名称 "{display_name}" 已被现有{kind_label}占用，请更换一个唯一名称。')
        if existing_slug == normalized_slug:
            raise HTTPException(status_code=409, detail=f'名称 "{display_name}" 会映射到已存在的{kind_label}标识 "{slug_name}"，不能覆盖。')
    for existing_kind, existing_slug, existing_display in [item for item in entries if item[0] != kind]:
        if current_name and existing_kind == kind and existing_slug == current_name.strip().lower():
            continue
        kind_label = {"skill": "Skill", "agent": "Agent", "workflow": "Workflow"}.get(existing_kind, existing_kind)
        if existing_display == normalized_display or existing_slug == normalized_slug:
            raise HTTPException(status_code=409, detail=f'名称 "{display_name}" 已被现有{kind_label}占用。')


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_text(path: Path, content: str) -> None:
    _ensure_parent(path)
    path.write_text(content, encoding="utf-8")


def _mirror_project_file(relative_path: Path, content: str) -> None:
    primary_path = PROJECT_ROOT / relative_path
    _write_text(primary_path, content)
    if PLATFORM_ROOT != PROJECT_ROOT:
        mirror_path = PLATFORM_ROOT / relative_path
        _write_text(mirror_path, content)


def _remove_project_path(relative_path: Path) -> None:
    targets = [PROJECT_ROOT / relative_path]
    if PLATFORM_ROOT != PROJECT_ROOT:
        targets.append(PLATFORM_ROOT / relative_path)
    for target in targets:
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()


def _strip_frontmatter(text: str) -> str:
    """If the text starts with YAML frontmatter, strip it and return the body."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return text

    end_index: int | None = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_index = i
            break

    if end_index is None:
        return text

    return "".join(lines[end_index + 1:]).lstrip("\n")


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _append_yaml_value(lines: list[str], key: str, value: Any, indent: int = 0) -> None:
    prefix = " " * indent
    if isinstance(value, dict):
        if not value:
            lines.append(f"{prefix}{key}: {{}}")
            return
        lines.append(f"{prefix}{key}:")
        for child_key, child_value in value.items():
            _append_yaml_value(lines, str(child_key), child_value, indent + 2)
        return
    if isinstance(value, list):
        if not value:
            lines.append(f"{prefix}{key}: []")
            return
        lines.append(f"{prefix}{key}:")
        item_prefix = " " * (indent + 2)
        for item in value:
            if isinstance(item, dict):
                lines.append(f"{item_prefix}-")
                for child_key, child_value in item.items():
                    _append_yaml_value(lines, str(child_key), child_value, indent + 4)
            else:
                lines.append(f"{item_prefix}- {_yaml_scalar(item)}")
        return
    lines.append(f"{prefix}{key}: {_yaml_scalar(value)}")


def _build_skill_markdown(
    name: str,
    description: str,
    content: str,
    skill_type: str = "doc",
    mode: str | None = None,
    approval_required: bool = False,
    completion_policy: dict[str, Any] | None = None,
    category: str = "other",
    input_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
) -> str:
    """
    Build the full SKILL.md text with frontmatter containing all skill metadata.

    The frontmatter is the single source of truth — no separate skill.yaml needed.
    """
    normalized_type = "hybrid" if skill_type == "exec" else (skill_type or "doc")
    normalized_category = (category or "other").strip().lower()
    if normalized_category not in {"query", "disposal", "other"}:
        normalized_category = "other"

    fm_lines = [
        "---",
        f"name: {name}",
        f"description: {description}",
        f"category: {normalized_category}",
        f"type: {normalized_type}",
    ]
    if normalized_type == "hybrid":
        fm_lines.append(f"mode: {mode or 'subprocess'}")
        fm_lines.append("entry: main.py")
        fm_lines.append("execute_policy:")
        fm_lines.append("  enabled: true")
        fm_lines.append(f"  approval_required: {'true' if approval_required else 'false'}")
        fm_lines.append("  audit: true")
    policy = completion_policy if isinstance(completion_policy, dict) else {}
    policy_enabled = bool(policy.get("enabled"))
    if policy_enabled:
        action_kind = str(policy.get("action_kind", "other")).strip() or "other"
        completion_effect = str(policy.get("completion_effect", "none")).strip() or "none"
        fm_lines.append("completion_policy:")
        fm_lines.append("  enabled: true")
        fm_lines.append(f"  action_kind: {action_kind}")
        fm_lines.append(f"  completion_effect: {completion_effect}")
    if isinstance(input_schema, dict) and input_schema:
        _append_yaml_value(fm_lines, "input_schema", input_schema)
    if isinstance(output_schema, dict) and output_schema:
        _append_yaml_value(fm_lines, "output_schema", output_schema)
    fm_lines.append("---")
    frontmatter = "\n".join(fm_lines)

    # Strip existing frontmatter from content if present
    body = _strip_frontmatter(content)
    body = body.strip() or f"# {branding.skill_label}\n\n{description.strip()}\n"
    if not body.endswith("\n"):
        body += "\n"
    return f"{frontmatter}\n\n{body}"


def _build_skill_main(name: str) -> str:
    return (
        "from __future__ import annotations\n\n"
        "import json\n"
        "import sys\n\n"
        "def _read_payload() -> dict:\n"
        "    if len(sys.argv) > 1:\n"
        "        return json.loads(sys.argv[1])\n"
        "    try:\n"
        "        return json.load(sys.stdin)\n"
        "    except json.JSONDecodeError:\n"
        "        return {}\n\n"
        "def main() -> None:\n"
        "    payload = _read_payload()\n"
        f"    print(json.dumps({{'skill': '{name}', 'input': payload}}, ensure_ascii=False))\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )


def _normalize_skill_type(skill_type: str) -> str:
    normalized = (skill_type or "doc").strip().lower()
    if normalized == "exec":
        normalized = "hybrid"
    if normalized not in {"doc", "hybrid"}:
        normalized = "doc"
    return normalized


def _read_skill_code(skill_name: str) -> str:
    skill_dir = PROJECT_ROOT / ".sentinelflow" / "plugins" / "skills" / skill_name
    code_path = skill_dir / "main.py"
    if not code_path.is_file():
        return ""
    return code_path.read_text(encoding="utf-8")


def _normalize_agent_role(req: AgentCreateRequest) -> str:
    return (req.role or ("primary" if req.mode == "primary" else "worker")).strip() or "worker"


def _assert_single_enabled_primary(req: AgentCreateRequest, current_name: str | None = None) -> None:
    role = _normalize_agent_role(req)
    if role != "primary" or not req.enabled:
        return
    for a in list_agent_definitions(AGENT_ROOT, include_system_primary=True):
        if current_name and a.name == current_name:
            continue
        if a.enabled and a.role == "primary":
            raise HTTPException(
                status_code=400,
                detail=f"系统中已经存在启用中的主 Agent：{a.name}。请先停用它，再启用新的主 Agent。",
            )


def _build_agent_yaml(req: AgentCreateRequest) -> str:
    role = _normalize_agent_role(req)
    lines = [
        f"name: {req.name}",
        f"description: {req.description or req.name}",
        f"role: {role}",
        f"enabled: {'true' if req.enabled else 'false'}",
    ]
    if req.description_cn:
        lines.append(f"description_cn: {req.description_cn}")
    lines.append(f"mode: {req.mode}")
    if req.color:
        lines.append(f"color: {req.color}")
    if req.skills:
        lines.append("skills:")
        lines.extend([f"  - {item}" for item in req.skills])
    if req.tools:
        lines.append("tools:")
        lines.extend([f"  - {item}" for item in req.tools])
    lines.append(f"doc_skill_mode: {req.doc_skill_mode or 'all'}")
    if req.doc_skill_allowlist:
        lines.append("doc_skill_allowlist:")
        lines.extend([f"  - {item}" for item in req.doc_skill_allowlist])
    if req.doc_skill_denylist:
        lines.append("doc_skill_denylist:")
        lines.extend([f"  - {item}" for item in req.doc_skill_denylist])
    if req.hybrid_doc_allowlist:
        lines.append("hybrid_doc_allowlist:")
        lines.extend([f"  - {item}" for item in req.hybrid_doc_allowlist])
    if req.exec_skill_allowlist:
        lines.append("exec_skill_allowlist:")
        lines.extend([f"  - {item}" for item in req.exec_skill_allowlist])
    if req.worker_allowlist_command:
        lines.append("worker_allowlist_command:")
        lines.extend([f"  - {item}" for item in req.worker_allowlist_command])
    if req.worker_allowlist_alert:
        lines.append("worker_allowlist_alert:")
        lines.extend([f"  - {item}" for item in req.worker_allowlist_alert])
    lines.append(f"worker_max_steps: {max(1, int(req.worker_max_steps or 3))}")
    lines.append(f"worker_parallel_limit: {max(1, int(req.worker_parallel_limit or 3))}")
    lines.append(f"use_global_model: {'true' if req.use_global_model else 'false'}")
    if not req.use_global_model:
        if req.llm_api_base_url:
            lines.append(f"llm_api_base_url: {req.llm_api_base_url}")
        if req.llm_api_key:
            lines.append(f"llm_api_key: {req.llm_api_key}")
        if req.llm_model:
            lines.append(f"llm_model: {req.llm_model}")
        if req.llm_temperature is not None:
            lines.append(f"llm_temperature: {req.llm_temperature}")
        if req.llm_timeout is not None:
            lines.append(f"llm_timeout: {req.llm_timeout}")
    lines.append("prompt_file: prompt.md")
    if (req.prompt_command or "").strip():
        lines.append("prompt_command_file: prompt.command.md")
    if (req.prompt_alert or "").strip():
        lines.append("prompt_alert_file: prompt.alert.md")
    if (req.prompt_synthesize or "").strip():
        lines.append("prompt_synthesize_file: prompt.synthesize.md")
    return "\n".join(lines) + "\n"


def _list_agent_defs() -> list[dict[str, Any]]:
    return [
        {
            "name": a.name,
            "description": a.description,
            "mode": a.mode,
            "role": a.role,
            "enabled": a.enabled,
            "location": a.location,
            "has_prompt": a.has_prompt,
            "use_global_model": a.use_global_model,
            "has_model_override": not a.use_global_model,
            "is_system": a.name == SYSTEM_PRIMARY_AGENT_NAME,
        }
        for a in list_agent_definitions(AGENT_ROOT)
    ]


def _read_agent_yaml(agent_name: str) -> dict[str, Any]:
    agent = load_agent_definition(AGENT_ROOT, agent_name)
    return {
        "name": agent.name,
        "description": agent.description,
        "mode": agent.mode,
        "role": agent.role,
        "enabled": agent.enabled,
        "color": agent.color,
        "skills": agent.skills,
        "tools": agent.tools,
        "prompt": agent.prompt,
        "prompt_command": agent.prompt_command,
        "prompt_alert": agent.prompt_alert,
        "prompt_synthesize": agent.prompt_synthesize,
        "location": agent.location,
        "has_prompt": agent.has_prompt,
        "is_system": agent.name == SYSTEM_PRIMARY_AGENT_NAME,
        "doc_skill_mode": agent.doc_skill_mode,
        "doc_skill_allowlist": agent.doc_skill_allowlist,
        "doc_skill_denylist": agent.doc_skill_denylist,
        "hybrid_doc_allowlist": agent.hybrid_doc_allowlist,
        "exec_skill_allowlist": agent.exec_skill_allowlist,
        "worker_allowlist_command": agent.worker_allowlist_command,
        "worker_allowlist_alert": agent.worker_allowlist_alert,
        "worker_max_steps": agent.worker_max_steps,
        "worker_parallel_limit": agent.worker_parallel_limit,
        "use_global_model": agent.use_global_model,
        "llm_api_base_url": agent.llm_api_base_url,
        "llm_api_key": "",
        "llm_api_key_configured": bool(agent.llm_api_key),
        "llm_model": agent.llm_model,
        "llm_temperature": agent.llm_temperature,
        "llm_timeout": agent.llm_timeout,
    }
