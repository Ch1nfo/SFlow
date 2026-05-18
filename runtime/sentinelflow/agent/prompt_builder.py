from __future__ import annotations

from dataclasses import dataclass

from sentinelflow.agent.prompts import (
    ALERT_HANDLING_HINTS,
    DEFAULT_ALERT_SYSTEM_PROMPT,
    DEFAULT_COMMAND_SYSTEM_PROMPT,
    PLATFORM_ALERT_APPENDIX,
    PLATFORM_COMMAND_APPENDIX,
    PLATFORM_PRIMARY_ALERT_ORCHESTRATION_APPENDIX,
    PLATFORM_PRIMARY_COMMAND_ORCHESTRATION_APPENDIX,
    PRIMARY_ALERT_ORCHESTRATION_APPENDIX,
    PRIMARY_COMMAND_ORCHESTRATION_APPENDIX,
)


@dataclass(frozen=True)
class PromptBuildContext:
    base_prompt: str
    mode: str
    entry_type: str = ""
    action_hint: str = ""
    skill_catalog: str = ""
    worker_catalog: str = ""
    workflow_catalog: str = ""


_MODE_FALLBACK_TEMPLATES = {
    "agent_command": DEFAULT_COMMAND_SYSTEM_PROMPT,
    "agent_alert": DEFAULT_ALERT_SYSTEM_PROMPT,
    "primary_orchestrate_command": PRIMARY_COMMAND_ORCHESTRATION_APPENDIX,
    "primary_orchestrate_alert": PRIMARY_ALERT_ORCHESTRATION_APPENDIX,
}

_MODE_PLATFORM_APPENDICES = {
    "agent_command": PLATFORM_COMMAND_APPENDIX,
    "agent_alert": PLATFORM_ALERT_APPENDIX,
    "primary_orchestrate_command": PLATFORM_PRIMARY_COMMAND_ORCHESTRATION_APPENDIX,
    "primary_orchestrate_alert": PLATFORM_PRIMARY_ALERT_ORCHESTRATION_APPENDIX,
}


def build_prompt(context: PromptBuildContext) -> str:
    base_prompt = (context.base_prompt or "").strip()
    if base_prompt:
        template = _MODE_PLATFORM_APPENDICES.get(context.mode, "").strip()
        prompt = f"{base_prompt}\n\n{template}".strip() if template else base_prompt
    else:
        prompt = _MODE_FALLBACK_TEMPLATES.get(context.mode, "").strip()

    values = {
        "skill_catalog": context.skill_catalog,
        "worker_catalog": context.worker_catalog,
        "workflow_catalog": context.workflow_catalog,
    }
    for key, value in values.items():
        placeholder = f"{{{key}}}"
        if placeholder in prompt:
            prompt = prompt.replace(placeholder, value or _default_catalog_text(key))

    prompt = _append_catalog(prompt, "skill_catalog", "当前可用技能", context.skill_catalog)
    prompt = _append_catalog(prompt, "worker_catalog", "可用子 Agent", context.worker_catalog)
    prompt = _append_catalog(prompt, "workflow_catalog", "可用 Agent Workflow", context.workflow_catalog)

    # Disposition-specific hints only apply when using the built-in fallback prompt.
    if (
        not base_prompt
        and context.mode == "agent_alert"
        and context.action_hint in ALERT_HANDLING_HINTS
    ):
        prompt = f"{prompt}\n\n{ALERT_HANDLING_HINTS[context.action_hint]}".strip()
    return prompt.strip()


def _append_catalog(prompt: str, key: str, title: str, catalog: str) -> str:
    if not catalog:
        return prompt
    if f"{{{key}}}" in prompt:
        return prompt
    if catalog in prompt:
        return prompt
    return f"{prompt}\n\n{title}：\n{catalog}".strip()


def _default_catalog_text(key: str) -> str:
    mapping = {
        "skill_catalog": "（当前没有已加载技能）",
        "worker_catalog": "（当前没有可用子 Agent）",
        "workflow_catalog": "（当前没有可用 Agent Workflow）",
    }
    return mapping.get(key, "")
