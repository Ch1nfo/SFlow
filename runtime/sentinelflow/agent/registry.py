from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from sentinelflow.agent.prompts import SYSTEM_PRIMARY_DEFAULT_PROMPT
from sentinelflow.config.runtime import SentinelFlowRuntimeConfig

SYSTEM_PRIMARY_AGENT_NAME = "system-primary"


def _read_bool(value: str | bool | None, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class SentinelFlowAgentDefinition:
    name: str
    description: str
    mode: str
    role: str
    enabled: bool
    color: str
    prompt: str
    prompt_command: str
    prompt_alert: str
    prompt_synthesize: str
    skills: list[str]
    tools: list[str]
    doc_skill_mode: str
    doc_skill_allowlist: list[str]
    doc_skill_denylist: list[str]
    hybrid_doc_allowlist: list[str]
    exec_skill_allowlist: list[str]
    worker_allowlist_command: list[str]
    worker_allowlist_alert: list[str]
    worker_max_steps: int
    worker_parallel_limit: int
    use_global_model: bool
    llm_api_base_url: str
    llm_api_key: str
    llm_model: str
    llm_temperature: float | None
    llm_timeout: int | None
    location: str
    has_prompt: bool

    def prompt_for_mode(self, mode: str) -> str:
        if self.role != "primary":
            return self.prompt
        mapping = {
            "agent_command": self.prompt_command,
            "primary_orchestrate_command": self.prompt_command,
            "agent_alert": self.prompt_alert,
            "primary_orchestrate_alert": self.prompt_alert,
        }
        specialized = str(mapping.get(mode, "")).strip()
        return specialized or self.prompt

    def resolve_runtime_config(self, runtime_config: SentinelFlowRuntimeConfig) -> SentinelFlowRuntimeConfig:
        if self.use_global_model:
            return runtime_config
        return replace(
            runtime_config,
            llm_api_base_url=self.llm_api_base_url.strip() or runtime_config.llm_api_base_url,
            llm_api_key=self.llm_api_key.strip() or runtime_config.llm_api_key,
            llm_model=self.llm_model.strip() or runtime_config.llm_model,
            llm_temperature=self.llm_temperature if self.llm_temperature is not None else runtime_config.llm_temperature,
            llm_timeout=self.llm_timeout if self.llm_timeout is not None else runtime_config.llm_timeout,
        )


def _build_system_primary(agent_root: Path) -> SentinelFlowAgentDefinition:
    return SentinelFlowAgentDefinition(
        name=SYSTEM_PRIMARY_AGENT_NAME,
        description="系统自动生成的主 Agent，负责承接默认入口任务并统一调度子 Agent。",
        mode="primary",
        role="primary",
        enabled=True,
        color="#0f766e",
        prompt=SYSTEM_PRIMARY_DEFAULT_PROMPT,
        prompt_command="",
        prompt_alert="",
        prompt_synthesize="",
        skills=[],
        tools=[],
        doc_skill_mode="all",
        doc_skill_allowlist=[],
        doc_skill_denylist=[],
        hybrid_doc_allowlist=[],
        exec_skill_allowlist=[],
        worker_allowlist_command=[],
        worker_allowlist_alert=[],
        worker_max_steps=3,
        worker_parallel_limit=3,
        use_global_model=True,
        llm_api_base_url="",
        llm_api_key="",
        llm_model="",
        llm_temperature=None,
        llm_timeout=None,
        location=f"system://{agent_root.name}/{SYSTEM_PRIMARY_AGENT_NAME}",
        has_prompt=True,
    )


def _parse_agent_yaml(agent_dir: Path) -> SentinelFlowAgentDefinition:
    yaml_path = agent_dir / "agent.yaml"
    prompt_path = agent_dir / "prompt.md"
    prompt_command_path = agent_dir / "prompt.command.md"
    prompt_alert_path = agent_dir / "prompt.alert.md"
    prompt_synthesize_path = agent_dir / "prompt.synthesize.md"
    if not yaml_path.is_file():
        raise FileNotFoundError(f"Agent not found: {agent_dir.name}")

    raw = yaml_path.read_text(encoding="utf-8")
    data: dict[str, object] = {
        "name": agent_dir.name,
        "description": "",
        "mode": "subagent",
        "role": "primary" if agent_dir.name.endswith("primary") else "worker",
        "enabled": True,
        "color": "",
        "skills": [],
        "tools": [],
        "doc_skill_mode": "all",
        "doc_skill_allowlist": [],
        "doc_skill_denylist": [],
        "hybrid_doc_allowlist": [],
        "exec_skill_allowlist": [],
        "worker_allowlist_command": [],
        "worker_allowlist_alert": [],
        "worker_max_steps": 3,
        "worker_parallel_limit": 3,
        "use_global_model": True,
        "llm_api_base_url": "",
        "llm_api_key": "",
        "llm_model": "",
        "llm_temperature": None,
        "llm_timeout": None,
        "prompt": prompt_path.read_text(encoding="utf-8").strip() if prompt_path.is_file() else "",
        "prompt_command": prompt_command_path.read_text(encoding="utf-8").strip() if prompt_command_path.is_file() else "",
        "prompt_alert": prompt_alert_path.read_text(encoding="utf-8").strip() if prompt_alert_path.is_file() else "",
        "prompt_synthesize": prompt_synthesize_path.read_text(encoding="utf-8").strip() if prompt_synthesize_path.is_file() else "",
        "location": str(agent_dir),
        "has_prompt": prompt_path.is_file(),
    }

    current_list_key: str | None = None
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if line.startswith("skills:"):
            current_list_key = "skills"
            continue
        if line.startswith("tools:"):
            current_list_key = "tools"
            continue
        if line.startswith("doc_skill_allowlist:"):
            current_list_key = "doc_skill_allowlist"
            continue
        if line.startswith("doc_skill_denylist:"):
            current_list_key = "doc_skill_denylist"
            continue
        if line.startswith("hybrid_doc_allowlist:"):
            current_list_key = "hybrid_doc_allowlist"
            continue
        if line.startswith("exec_skill_allowlist:"):
            current_list_key = "exec_skill_allowlist"
            continue
        if line.startswith("worker_allowlist_command:"):
            current_list_key = "worker_allowlist_command"
            continue
        if line.startswith("worker_allowlist_alert:"):
            current_list_key = "worker_allowlist_alert"
            continue
        if line.startswith("  - ") and current_list_key is not None:
            target = data[current_list_key]
            if isinstance(target, list):
                target.append(line.split("-", 1)[1].strip())
            continue
        current_list_key = None
        if line.startswith("name:"):
            data["name"] = line.split(":", 1)[1].strip()
        elif line.startswith("description:"):
            data["description"] = line.split(":", 1)[1].strip()
        elif line.startswith("mode:"):
            data["mode"] = line.split(":", 1)[1].strip()
        elif line.startswith("role:"):
            data["role"] = line.split(":", 1)[1].strip()
        elif line.startswith("enabled:"):
            data["enabled"] = _read_bool(line.split(":", 1)[1].strip(), True)
        elif line.startswith("color:"):
            data["color"] = line.split(":", 1)[1].strip()
        elif line.startswith("doc_skill_mode:"):
            data["doc_skill_mode"] = line.split(":", 1)[1].strip() or "all"
        elif line.startswith("use_global_model:"):
            data["use_global_model"] = _read_bool(line.split(":", 1)[1].strip(), True)
        elif line.startswith("worker_max_steps:"):
            value = line.split(":", 1)[1].strip()
            data["worker_max_steps"] = int(value) if value else 3
        elif line.startswith("worker_parallel_limit:"):
            value = line.split(":", 1)[1].strip()
            data["worker_parallel_limit"] = int(value) if value else 3
        elif line.startswith("llm_api_base_url:"):
            data["llm_api_base_url"] = line.split(":", 1)[1].strip()
        elif line.startswith("llm_api_key:"):
            data["llm_api_key"] = line.split(":", 1)[1].strip()
        elif line.startswith("llm_model:"):
            data["llm_model"] = line.split(":", 1)[1].strip()
        elif line.startswith("llm_temperature:"):
            value = line.split(":", 1)[1].strip()
            data["llm_temperature"] = float(value) if value else None
        elif line.startswith("llm_timeout:"):
            value = line.split(":", 1)[1].strip()
            data["llm_timeout"] = int(value) if value else None

    return SentinelFlowAgentDefinition(
        name=str(data["name"]),
        description=str(data["description"]),
        mode=str(data["mode"]),
        role=str(data["role"] or ("primary" if str(data["mode"]) == "primary" else "worker")),
        enabled=bool(data["enabled"]),
        color=str(data["color"]),
        prompt=str(data["prompt"]),
        prompt_command=str(data["prompt_command"]),
        prompt_alert=str(data["prompt_alert"]),
        prompt_synthesize=str(data["prompt_synthesize"]),
        skills=list(data["skills"]),  # type: ignore[arg-type]
        tools=list(data["tools"]),  # type: ignore[arg-type]
        doc_skill_mode=str(data["doc_skill_mode"] or "all"),
        doc_skill_allowlist=list(data["doc_skill_allowlist"]),  # type: ignore[arg-type]
        doc_skill_denylist=list(data["doc_skill_denylist"]),  # type: ignore[arg-type]
        hybrid_doc_allowlist=list(data["hybrid_doc_allowlist"] or data["skills"]),  # type: ignore[arg-type]
        exec_skill_allowlist=list(data["exec_skill_allowlist"] or data["skills"]),  # type: ignore[arg-type]
        worker_allowlist_command=list(data["worker_allowlist_command"]),  # type: ignore[arg-type]
        worker_allowlist_alert=list(data["worker_allowlist_alert"]),  # type: ignore[arg-type]
        worker_max_steps=max(1, int(data["worker_max_steps"])) if isinstance(data["worker_max_steps"], int) else 3,
        worker_parallel_limit=max(1, int(data["worker_parallel_limit"])) if isinstance(data["worker_parallel_limit"], int) else 3,
        use_global_model=bool(data["use_global_model"]),
        llm_api_base_url=str(data["llm_api_base_url"]),
        llm_api_key=str(data["llm_api_key"]),
        llm_model=str(data["llm_model"]),
        llm_temperature=data["llm_temperature"] if isinstance(data["llm_temperature"], float) or data["llm_temperature"] is None else None,
        llm_timeout=data["llm_timeout"] if isinstance(data["llm_timeout"], int) or data["llm_timeout"] is None else None,
        location=str(data["location"]),
        has_prompt=bool(data["has_prompt"]),
    )


def list_agent_definitions(agent_root: Path, include_system_primary: bool = True) -> list[SentinelFlowAgentDefinition]:
    if not agent_root.is_dir():
        return [_build_system_primary(agent_root)] if include_system_primary else []
    agents: list[SentinelFlowAgentDefinition] = []
    for yaml_path in sorted(agent_root.glob("*/agent.yaml")):
        agents.append(_parse_agent_yaml(yaml_path.parent))
    if include_system_primary and not any(agent.enabled and agent.role == "primary" for agent in agents):
        agents.insert(0, _build_system_primary(agent_root))
    return agents


def load_agent_definition(agent_root: Path, agent_name: str) -> SentinelFlowAgentDefinition:
    if agent_name == SYSTEM_PRIMARY_AGENT_NAME and not (agent_root / agent_name / "agent.yaml").is_file():
        return _build_system_primary(agent_root)
    return _parse_agent_yaml(agent_root / agent_name)


def resolve_default_agent(agent_root: Path, preferred_name: str | None = None) -> SentinelFlowAgentDefinition | None:
    agents = list_agent_definitions(agent_root, include_system_primary=True)
    if not agents:
        return None
    if preferred_name:
        preferred = next((item for item in agents if item.name == preferred_name and item.enabled), None)
        if preferred is not None:
            return preferred
    primary = next((item for item in agents if item.enabled and item.role == "primary"), None)
    if primary is not None:
        return primary
    return next((item for item in agents if item.enabled), None)
