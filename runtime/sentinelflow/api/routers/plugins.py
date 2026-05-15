import json
from pathlib import Path
from typing import Any
from fastapi import APIRouter, HTTPException
from sentinelflow.api.schemas import SkillCreateRequest, SkillDebugRequest, WorkflowCreateRequest, WorkflowRunRequest, AgentCreateRequest
from sentinelflow.api.deps import PROJECT_ROOT, PLATFORM_ROOT, SKILL_ROOT, AGENT_ROOT, WORKFLOW_ROOT, skill_runtime, agent_workflow_runner
from sentinelflow.agent.registry import SYSTEM_PRIMARY_AGENT_NAME, list_agent_definitions, load_agent_definition
from sentinelflow.workflows.agent_workflow_registry import load_agent_workflow, list_agent_workflows, serialize_agent_workflow_detail, serialize_agent_workflow_summary
from sentinelflow.api.utils import (
    _slugify, _normalize_workflow_id, _mirror_project_file, _remove_project_path,
    _build_skill_markdown, _build_skill_main, _assert_unique_plugin_name,
    _normalize_skill_type, _read_skill_code, _list_agent_defs, _read_agent_yaml,
    _assert_single_enabled_primary, _build_agent_yaml,
)


router = APIRouter(prefix="/api/sentinelflow")

@router.get("/skills")
def list_skills() -> dict[str, Any]:
    skills = []
    for name in skill_runtime.list_skills():
        read = skill_runtime.read_skill(name)
        skills.append({
            "name": read.name,
            "type": read.type.value,
            "description": read.description,
            "category": read.category,
            "executable": read.executable,
            "approval_required": read.approval_required,
            "entry": read.entry,
            "mode": read.mode.value if read.mode else None,
            "completion_policy": read.completion_policy,
        })
    return {"skills": skills}


@router.post("/skills")
def create_skill(payload: SkillCreateRequest) -> dict[str, Any]:
    skill_name = _slugify(payload.name)
    _assert_unique_plugin_name(payload.name, skill_name, "skill")
    skill_dir = Path(".sentinelflow") / "plugins" / "skills" / skill_name
    if (PROJECT_ROOT / skill_dir).exists():
        raise HTTPException(status_code=409, detail=f'Skill 标识 "{skill_name}" 已存在，不能覆盖。')
    skill_type = _normalize_skill_type(payload.type)
    description = payload.description.strip()
    if not description: raise ValueError("Skill 描述不能为空。")
    if not payload.content.strip(): raise ValueError("Skill 文档内容不能为空。")
    if skill_type == "hybrid" and not payload.code.strip(): raise ValueError("文本 + 可执行 Skill 必须提供代码内容。")

    _mirror_project_file(
        skill_dir / "SKILL.md",
        _build_skill_markdown(
            payload.name,
            description,
            payload.content,
            skill_type,
            payload.mode,
            payload.approval_required,
            payload.completion_policy,
            payload.category,
        ),
    )
    if skill_type == "hybrid":
        _mirror_project_file(skill_dir / "main.py", payload.code if payload.code.strip() else _build_skill_main(skill_name))

    return get_skill(skill_name)


@router.post("/skills/{name}/save")
def save_skill(name: str, payload: SkillCreateRequest) -> dict[str, Any]:
    old_slug = _slugify(name)
    new_slug = _slugify(payload.name)
    existing_input_schema: dict[str, Any] = {}
    existing_output_schema: dict[str, Any] = {}
    try:
        existing = skill_runtime.read_skill(old_slug)
        existing_input_schema = existing.input_schema if isinstance(existing.input_schema, dict) else {}
        existing_output_schema = existing.output_schema if isinstance(existing.output_schema, dict) else {}
    except Exception:
        existing_input_schema = {}
        existing_output_schema = {}
    
    if old_slug != new_slug:
        _assert_unique_plugin_name(payload.name, new_slug, "skill", current_name=old_slug)
        new_dir = PROJECT_ROOT / ".sentinelflow" / "plugins" / "skills" / new_slug
        if new_dir.exists():
            raise HTTPException(status_code=409, detail=f'Skill 标识 "{new_slug}" 已存在，不能覆盖。')
            
    skill_type = _normalize_skill_type(payload.type)
    description = payload.description.strip()
    if not description: raise ValueError("Skill 描述不能为空。")
    if not payload.content.strip(): raise ValueError("Skill 文档不能为空。")
    if skill_type == "hybrid" and not payload.code.strip(): raise ValueError("文本 + 可执行 Skill 必须提供代码内容。")

    if old_slug != new_slug:
        _remove_project_path(Path(".sentinelflow") / "plugins" / "skills" / old_slug)

    skill_dir = Path(".sentinelflow") / "plugins" / "skills" / new_slug
    _mirror_project_file(
        skill_dir / "SKILL.md",
        _build_skill_markdown(
            payload.name,
            description,
            payload.content,
            skill_type,
            payload.mode,
            payload.approval_required,
            payload.completion_policy,
            payload.category,
            existing_input_schema,
            existing_output_schema,
        ),
    )
    if skill_type == "hybrid":
        _mirror_project_file(skill_dir / "main.py", payload.code if payload.code.strip() else _build_skill_main(new_slug))
    else:
        _remove_project_path(skill_dir / "main.py")
    return get_skill(new_slug)


@router.post("/skills/{name}/delete")
def delete_skill(name: str) -> dict[str, Any]:
    skill_name = _slugify(name)
    _remove_project_path(Path(".sentinelflow") / "plugins" / "skills" / skill_name)
    return {"deleted": True, "name": skill_name}


@router.get("/skills/{name}")
def get_skill(name: str) -> dict[str, Any]:
    read = skill_runtime.read_skill(name)
    return {
        "name": read.name, "type": read.type.value, "description": read.description,
        "category": read.category,
        "markdown": read.markdown, "code": _read_skill_code(read.name), "executable": read.executable,
        "approval_required": read.approval_required,
        "entry": read.entry, "mode": read.mode.value if read.mode else None,
        "input_schema": read.input_schema, "output_schema": read.output_schema,
        "completion_policy": read.completion_policy,
    }


@router.post("/skills/{name}/debug")
def debug_skill(name: str, payload: SkillDebugRequest) -> dict[str, Any]:
    result = skill_runtime.execute_skill(name, payload.arguments or {}, payload.context or {})
    return {"success": result.success, "skill": result.skill, "data": result.data if isinstance(result.data, dict) else {"result": result.data}, "error": result.error}

@router.get("/workflows")
def list_sentinelflow_workflows() -> dict[str, Any]:
    return {"workflows": [serialize_agent_workflow_summary(item) for item in list_agent_workflows(WORKFLOW_ROOT)]}

@router.get("/workflows/{workflow_id}")
def get_sentinelflow_workflow(workflow_id: str) -> dict[str, Any]:
    workflow = load_agent_workflow(WORKFLOW_ROOT, _normalize_workflow_id(workflow_id))
    return serialize_agent_workflow_detail(workflow)

@router.post("/workflows")
def create_sentinelflow_workflow(payload: WorkflowCreateRequest) -> dict[str, Any]:
    workflow_id = _slugify(payload.name)
    _assert_unique_plugin_name(payload.name, workflow_id, "workflow")
    relative_dir = Path(".sentinelflow") / "plugins" / "workflows" / workflow_id
    if (PROJECT_ROOT / relative_dir).exists(): raise HTTPException(status_code=409, detail=f'Workflow "{workflow_id}" 已存在。')
    workflow_data = payload.workflow if isinstance(payload.workflow, dict) else {
        "name": payload.name.strip(), "description": payload.description.strip(),
        "enabled": True, "scenarios": ["alert", "task"], "selection_keywords": [],
        "steps": [],
    }
    workflow_data["name"] = payload.name.strip()
    workflow_data["description"] = payload.description.strip() or workflow_data.get("description", "")
    _mirror_project_file(relative_dir / "workflow.json", json.dumps(workflow_data, ensure_ascii=False, indent=2) + "\n")
    return serialize_agent_workflow_detail(load_agent_workflow(WORKFLOW_ROOT, workflow_id))

@router.post("/workflows/{workflow_id}/save")
def save_sentinelflow_workflow(workflow_id: str, payload: WorkflowCreateRequest) -> dict[str, Any]:
    workflow_slug = _normalize_workflow_id(workflow_id)
    relative_dir = Path(".sentinelflow") / "plugins" / "workflows" / workflow_slug
    workflow_json = PROJECT_ROOT / relative_dir / "workflow.json"
    workflow_data = payload.workflow if isinstance(payload.workflow, dict) else {}
    if not isinstance(payload.workflow, dict) and workflow_json.is_file():
        existing = json.loads(workflow_json.read_text(encoding="utf-8"))
        if isinstance(existing, dict): workflow_data = existing
    workflow_data["name"] = payload.name.strip() or workflow_data.get("name") or workflow_slug
    workflow_data["description"] = payload.description.strip()
    _mirror_project_file(relative_dir / "workflow.json", json.dumps(workflow_data, ensure_ascii=False, indent=2) + "\n")
    return serialize_agent_workflow_detail(load_agent_workflow(WORKFLOW_ROOT, workflow_slug))

@router.post("/workflows/{workflow_id}/delete")
def delete_sentinelflow_workflow(workflow_id: str) -> dict[str, Any]:
    workflow_slug = _normalize_workflow_id(workflow_id)
    _remove_project_path(Path(".sentinelflow") / "plugins" / "workflows" / workflow_slug)
    return {"deleted": True, "id": workflow_slug}

@router.post("/workflows/{workflow_id}/run")
async def run_sentinelflow_workflow(workflow_id: str, payload: WorkflowRunRequest) -> dict[str, Any]:
    workflow = load_agent_workflow(WORKFLOW_ROOT, _normalize_workflow_id(workflow_id))
    detail = serialize_agent_workflow_detail(workflow)
    validation = detail.get("validation", {})
    if isinstance(validation, dict) and not validation.get("valid", False):
        return {"success": False, "workflow_id": workflow.id, "error": "Agent Workflow 校验未通过。"}
    context = payload.context or {}
    alert = context.get("alert") if isinstance(context.get("alert"), dict) else context
    return await agent_workflow_runner.execute_workflow(workflow, alert if isinstance(alert, dict) else {})

@router.get("/agents")
def list_sentinelflow_agents() -> dict[str, Any]:
    return {"agents": _list_agent_defs()}

@router.get("/agents/{name}")
def get_sentinelflow_agent(name: str) -> dict[str, Any]:
    return _read_agent_yaml(_slugify(name))

@router.post("/agents")
def create_sentinelflow_agent(payload: AgentCreateRequest) -> dict[str, Any]:
    agent_name = _slugify(payload.name)
    _assert_unique_plugin_name(payload.name, agent_name, "agent")
    if (PROJECT_ROOT / Path(".sentinelflow") / "plugins" / "agents" / agent_name).exists():
        raise HTTPException(status_code=409, detail=f'Agent 标识 "{agent_name}" 已存在。')
    normalized = payload.model_copy(update={"name": agent_name, "role": "worker"})
    _assert_single_enabled_primary(normalized, current_name=agent_name if agent_name == SYSTEM_PRIMARY_AGENT_NAME else None)
    relative_dir = Path(".sentinelflow") / "plugins" / "agents" / agent_name
    _mirror_project_file(relative_dir / "agent.yaml", _build_agent_yaml(normalized))
    _mirror_project_file(relative_dir / "prompt.md", payload.prompt.strip() + "\n")
    _write_agent_prompt_variants(relative_dir, normalized)
    return _read_agent_yaml(agent_name)

@router.post("/agents/{name}/save")
def save_sentinelflow_agent(name: str, payload: AgentCreateRequest) -> dict[str, Any]:
    current_name = _slugify(name)
    target_name = _slugify(payload.name)
    if not target_name: raise HTTPException(status_code=400, detail="不能为空。")
    existing_agent = load_agent_definition(AGENT_ROOT, current_name)
    current_relative_dir = Path(".sentinelflow") / "plugins" / "agents" / current_name
    target_relative_dir = Path(".sentinelflow") / "plugins" / "agents" / target_name
    if current_name != target_name and (PROJECT_ROOT / target_relative_dir / "agent.yaml").exists():
        raise HTTPException(status_code=400, detail=f'"{target_name}" 已存在。')
    _assert_unique_plugin_name(payload.name, target_name, "agent", current_name=current_name)
    forced_role = "primary" if current_name == SYSTEM_PRIMARY_AGENT_NAME else "worker"
    normalized = payload.model_copy(
        update={
            "name": target_name,
            "role": forced_role,
            "llm_api_key": payload.llm_api_key if payload.llm_api_key else existing_agent.llm_api_key,
        }
    )
    _assert_single_enabled_primary(normalized, current_name=current_name)
    _mirror_project_file(target_relative_dir / "agent.yaml", _build_agent_yaml(normalized))
    _mirror_project_file(target_relative_dir / "prompt.md", payload.prompt.strip() + "\n")
    _write_agent_prompt_variants(target_relative_dir, normalized)
    if current_name != target_name and current_name != SYSTEM_PRIMARY_AGENT_NAME:
        _remove_project_path(current_relative_dir)
    return _read_agent_yaml(target_name)

@router.post("/agents/{name}/delete")
def delete_sentinelflow_agent(name: str) -> dict[str, Any]:
    agent_name = _slugify(name)
    if agent_name == SYSTEM_PRIMARY_AGENT_NAME: raise HTTPException(status_code=400, detail="系统主 Agent 不能删除。")
    _remove_project_path(Path(".sentinelflow") / "plugins" / "agents" / agent_name)
    return {"deleted": True, "name": agent_name}


def _write_agent_prompt_variants(relative_dir: Path, payload: AgentCreateRequest) -> None:
    prompt_files = {
        "prompt.command.md": payload.prompt_command,
        "prompt.alert.md": payload.prompt_alert,
        "prompt.synthesize.md": payload.prompt_synthesize,
    }
    for filename, content in prompt_files.items():
        target = relative_dir / filename
        if (content or "").strip():
            _mirror_project_file(target, content.strip() + "\n")
        else:
            _remove_project_path(target)
    # Clear deprecated mode-specific prompt files saved by older versions.
    for filename in (
        "prompt.workflow.select.md",
        "prompt.orchestrate.command.md",
        "prompt.orchestrate.alert.md",
        "prompt.synthesize.command.md",
        "prompt.synthesize.alert.md",
    ):
        _remove_project_path(relative_dir / filename)
