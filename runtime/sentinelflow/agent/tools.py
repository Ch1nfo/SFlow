from __future__ import annotations

import json
from typing import Annotated, Any

from sentinelflow.agent.context_utils import (
    build_context_manifest,
    build_safe_current_goal,
    validate_execution_inputs,
    validate_skill_input_schema,
)
from sentinelflow.agent.state import SentinelFlowAgentState
from sentinelflow.skills.adapters import SentinelFlowSkillRuntime
from sentinelflow.services.skill_approval_service import SkillApprovalService

try:
    from langchain_core.tools import tool
    from langgraph.prebuilt import InjectedState
except ModuleNotFoundError:  # pragma: no cover
    tool = None  # type: ignore[assignment]
    InjectedState = object  # type: ignore[assignment]


def build_agent_tools(
    skill_runtime: SentinelFlowSkillRuntime,
    approval_service: SkillApprovalService,
    *,
    enable_read_skill_document: bool = True,
    enable_execute_skill: bool = True,
) -> list:
    if tool is None:
        raise ModuleNotFoundError("langchain_core/langgraph 未安装，无法构建 Agent tools。")

    tools: list = []
    CONTRACT_DESCRIPTION_MAX_CHARS = 1000

    def _normalize_skill_arguments(arguments: dict[str, Any] | str | None) -> tuple[dict[str, Any], str | None]:
        if arguments is None:
            return {}, None
        if isinstance(arguments, dict):
            return arguments, None
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments)
            except json.JSONDecodeError:
                return {}, "Skill arguments must be a JSON object."
            if isinstance(parsed, dict):
                return parsed, None
            return {}, "Skill arguments must be a JSON object."
        return {}, "Skill arguments must be a JSON object."

    def _invalid_arguments_payload(skill_name: str, error: str) -> str:
        return json.dumps(
            {
                "success": False,
                "data": {
                    "skill_name": skill_name,
                    "arguments": {},
                },
                "error": error,
            },
            ensure_ascii=False,
        )

    def _build_validation_payload(
        *,
        skill_name: str,
        arguments: dict[str, Any],
        validation: dict[str, Any],
        state: SentinelFlowAgentState,
        error_message: str,
        include_invalid_inputs: bool,
    ) -> str:
        alert_data = state.get("alert_data", {})
        safe_goal, safe_goal_meta = build_safe_current_goal(
            alert_data=alert_data if isinstance(alert_data, dict) else {},
            arguments=arguments,
            skill_name=skill_name,
        )
        manifest = build_context_manifest(
            current_goal=safe_goal,
            entry_type=str(state.get("execution_entry", "")).strip(),
            original_input=alert_data,
            current_task_prompt=safe_goal,
            current_skill_args=arguments,
            input_contract=validation.get("input_contract", {}),
            missing_required_inputs=validation.get("missing_required_inputs", []),
            current_goal_meta=safe_goal_meta,
        )
        data: dict[str, Any] = {
            "skill_name": skill_name,
            "arguments": arguments,
            "input_contract": validation.get("input_contract", {}),
            "missing_required_inputs": validation.get("missing_required_inputs", []),
            "context_manifest": manifest,
            "context_warnings": manifest.get("context_warnings", []),
        }
        suggested_arguments = _suggest_skill_arguments(skill_name, arguments)
        if suggested_arguments:
            data["suggested_arguments"] = suggested_arguments
        if include_invalid_inputs:
            data["invalid_inputs"] = validation.get("invalid_inputs", [])
        return json.dumps(
            {"success": False, "data": data, "error": error_message},
            ensure_ascii=False,
        )

    def _suggest_skill_arguments(skill_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        normalized_name = str(skill_name or "").strip().lower()
        if normalized_name != "rag" or not isinstance(arguments, dict) or arguments.get("query"):
            return {}
        parts: list[str] = []
        for field in ("alert_name", "sip", "dip", "dport"):
            value = str(arguments.get(field) or "").strip()
            if value:
                parts.append(value)
        payload = str(arguments.get("payload") or "").strip()
        if payload:
            compact_payload = " ".join(payload.split())
            if compact_payload:
                parts.append(compact_payload[:120])
        query = " ".join(parts).strip()
        return {"query": query} if query else {}

    def _resolve_skill_or_error(skill_name: str) -> tuple[Any, str | None]:
        try:
            return skill_runtime.resolver.resolve(skill_name), None
        except Exception as exc:
            payload = json.dumps(
                {"success": False, "data": {}, "error": f"加载 Skill 失败：{exc}"},
                ensure_ascii=False,
            )
            return None, payload

    def _clip_contract_text(value: Any, max_chars: int = CONTRACT_DESCRIPTION_MAX_CHARS) -> str:
        text = str(value or "").strip()
        if len(text) <= max_chars:
            return text
        return text[:max_chars].rstrip() + "..."

    def _completion_policy_payload(policy: Any) -> dict[str, Any]:
        if policy is None:
            return {}
        if isinstance(policy, dict):
            return dict(policy)
        payload: dict[str, Any] = {}
        for key in ("enabled", "action_kind", "completion_effect"):
            if hasattr(policy, key):
                payload[key] = getattr(policy, key)
        return payload

    def _contract_payload_from_skill(skill: Any) -> dict[str, Any]:
        spec = getattr(skill, "spec", None)
        return {
            "skill_name": str(getattr(spec, "name", "") or "").strip(),
            "description": _clip_contract_text(getattr(spec, "description", "")),
            "category": str(getattr(spec, "category", "") or "other"),
            "input_schema": getattr(spec, "input_schema", {}) if isinstance(getattr(spec, "input_schema", {}), dict) else {},
            "output_schema": getattr(spec, "output_schema", {}) if isinstance(getattr(spec, "output_schema", {}), dict) else {},
            "execute_policy": {
                "enabled": bool(getattr(spec, "execute_enabled", False)),
                "approval_required": bool(getattr(spec, "approval_required", False)),
                "audit": bool(getattr(spec, "audit_enabled", True)),
            },
            "completion_policy": _completion_policy_payload(getattr(spec, "completion_policy", None)),
            "entry": getattr(spec, "entry", None),
            "mode": str(getattr(getattr(spec, "mode", None), "value", getattr(spec, "mode", None)) or ""),
        }

    def _has_input_schema(skill: Any) -> bool:
        return bool(getattr(getattr(skill, "spec", None), "input_schema", {}) or {})

    def _message_attr(message: Any, key: str, default: Any = None) -> Any:
        if isinstance(message, dict):
            return message.get(key, default)
        return getattr(message, key, default)

    def _parse_message_json_content(message: Any) -> dict[str, Any]:
        content = _message_attr(message, "content", "")
        if not isinstance(content, str):
            return {}
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _has_read_skill_contract(skill_name: str, state: SentinelFlowAgentState) -> bool:
        normalized_name = str(skill_name or "").strip()
        if not normalized_name:
            return False
        for item in state.get("read_skill_contracts", []) or []:
            if str(item or "").strip() == normalized_name:
                return True
        for message in state.get("messages", []) or []:
            message_type = str(_message_attr(message, "type", "") or "").strip()
            message_name = str(_message_attr(message, "name", "") or "").strip()
            if message_type != "tool" and message_name != "read_skill_contract":
                continue
            if message_name and message_name != "read_skill_contract":
                continue
            payload = _parse_message_json_content(message)
            if payload.get("success") is not True:
                continue
            data = payload.get("data", {})
            if isinstance(data, dict) and str(data.get("skill_name") or "").strip() == normalized_name:
                return True
        return False

    def _contract_required_payload(skill_name: str) -> str:
        return json.dumps(
            {
                "success": False,
                "data": {
                    "skill_name": skill_name,
                    "required_action": "read_skill_contract",
                    "contract_required": True,
                },
                "error": f"执行 Skill「{skill_name}」前必须先读取 Skill Contract。",
            },
            ensure_ascii=False,
        )

    def _arguments_fingerprint(arguments: dict[str, Any] | None) -> str:
        return approval_service.fingerprint_arguments(arguments)

    def _rejection_key(skill_name: str, arguments: dict[str, Any] | None) -> str:
        return SkillApprovalService.build_skill_arguments_key(skill_name, _arguments_fingerprint(arguments))

    def _is_rejected_in_current_run(
        *,
        skill_name: str,
        arguments: dict[str, Any] | None,
        state: SentinelFlowAgentState,
    ) -> bool:
        rejected = set(state.get("rejected_fingerprints", []) or [])
        return _rejection_key(skill_name, arguments) in rejected

    def _rejected_payload(
        *,
        skill_name: str,
        arguments: dict[str, Any],
    ) -> str:
        normalized_arguments = approval_service.normalize_arguments(arguments)
        return json.dumps(
            {
                "success": False,
                "data": {
                    "approval_rejected": True,
                    "skill_name": skill_name,
                    "arguments": normalized_arguments,
                    "arguments_fingerprint": _arguments_fingerprint(normalized_arguments),
                },
                "error": f"Skill「{skill_name}」已被用户拒绝，本轮相同参数不会再次发起审批。",
            },
            ensure_ascii=False,
        )

    def _input_validation_payload(
        *,
        skill_name: str,
        arguments: dict[str, Any],
        state: SentinelFlowAgentState,
    ) -> str | None:
        alert_data = state.get("alert_data", {})
        task_prompt = ""
        if isinstance(alert_data, dict):
            task_prompt = str(alert_data.get("delegated_task_prompt") or alert_data.get("payload") or "")
        validation = validate_execution_inputs(
            skill_name=skill_name,
            arguments=arguments,
            task_prompt=task_prompt,
        )
        if validation.get("valid"):
            return None
        return _build_validation_payload(
            skill_name=skill_name,
            arguments=arguments,
            validation=validation,
            state=state,
            error_message="Skill 调用缺少必需执行参数，请先补齐后再执行。",
            include_invalid_inputs=False,
        )

    def _approval_payload(
        *,
        skill_name: str,
        arguments: dict[str, Any],
        state: SentinelFlowAgentState,
    ) -> str:
        normalized_arguments = approval_service.normalize_arguments(arguments)
        fingerprint = _arguments_fingerprint(normalized_arguments)
        return json.dumps(
            {
                "success": False,
                "data": {},
                "error": "该 Skill 需要审批后才能执行。",
                "approval_pending": True,
                "approval_request": {
                    "skill_name": skill_name,
                    "arguments": normalized_arguments,
                    "arguments_fingerprint": fingerprint,
                    "run_id": str(state.get("run_id", "")).strip(),
                    "scope_type": str(state.get("scope_type", "")).strip(),
                    "scope_ref": str(state.get("scope_ref", "")).strip(),
                    "checkpoint_thread_id": str(state.get("checkpoint_thread_id", "")).strip(),
                    "checkpoint_ns": str(state.get("graph_checkpoint_ns", state.get("checkpoint_ns", ""))).strip(),
                    "message": f"Skill「{skill_name}」需要人工审批后才能执行。",
                },
            },
            ensure_ascii=False,
        )

    if enable_read_skill_document or enable_execute_skill:
        @tool
        def read_skill_contract(
            skill_name: str,
            state: Annotated[SentinelFlowAgentState, InjectedState()],  # type: ignore[misc]
        ) -> str:
            """读取指定技能的轻量执行契约，包括 input_schema、执行策略和完成策略。"""
            readable_skills_raw = state.get("readable_skills")
            executable_skills_raw = state.get("executable_skills")
            readable_skills = set(readable_skills_raw or [])
            executable_skills = set(executable_skills_raw or [])
            if readable_skills_raw is not None or executable_skills_raw is not None:
                readable_allowed = readable_skills_raw is not None and skill_name in readable_skills
                executable_allowed = executable_skills_raw is not None and skill_name in executable_skills
                if not readable_allowed and not executable_allowed:
                    return json.dumps(
                        {"success": False, "data": {}, "error": f"当前 Agent 未被授权读取技能 {skill_name} 的执行契约。"},
                        ensure_ascii=False,
                    )
            skill, resolve_error = _resolve_skill_or_error(skill_name)
            if resolve_error is not None:
                return resolve_error
            return json.dumps(
                {"success": True, "data": _contract_payload_from_skill(skill), "error": None},
                ensure_ascii=False,
            )

        tools.append(read_skill_contract)

    if enable_read_skill_document:
        @tool
        def read_skill_document(
            skill_name: str,
            state: Annotated[SentinelFlowAgentState, InjectedState()],  # type: ignore[misc]
        ) -> str:
            """读取指定技能的完整说明文档并返回 JSON 结果。"""
            readable_skills_raw = state.get("readable_skills")
            readable_skills = set(readable_skills_raw or [])
            if readable_skills_raw is not None and skill_name not in readable_skills:
                return json.dumps(
                    {"success": False, "data": {}, "error": f"当前 Agent 未被授权读取技能 {skill_name} 的文档。"},
                    ensure_ascii=False,
                )
            try:
                result = skill_runtime.read_skill(skill_name)
                return json.dumps(
                    {"success": True, "data": result.markdown, "error": None},
                    ensure_ascii=False,
                )
            except Exception as exc:
                return json.dumps(
                    {"success": False, "data": {}, "error": f"读取技能文档失败：{exc}"},
                    ensure_ascii=False,
                )

        tools.append(read_skill_document)

    if enable_execute_skill:
        @tool
        def execute_skill(
            skill_name: str,
            state: Annotated[SentinelFlowAgentState, InjectedState()],  # type: ignore[misc]
            arguments: dict[str, Any] | str | None = None,
        ) -> str:
            """执行指定技能并返回 JSON 字符串结果。"""
            normalized_arguments, argument_error = _normalize_skill_arguments(arguments)
            if argument_error is not None:
                return _invalid_arguments_payload(skill_name, argument_error)
            cancel_event = state.get("cancel_event")
            if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                return json.dumps(
                    {"success": False, "data": {}, "error": "用户已停止当前任务。"},
                    ensure_ascii=False,
                )
            executable_skills_raw = state.get("executable_skills")
            executable_skills = set(executable_skills_raw or [])
            if executable_skills_raw is not None and skill_name not in executable_skills:
                return json.dumps(
                    {"success": False, "data": {}, "error": f"当前 Agent 未被授权执行技能 {skill_name}。"},
                    ensure_ascii=False,
                )
            skill, resolve_error = _resolve_skill_or_error(skill_name)
            if resolve_error is not None:
                return resolve_error
            if not _has_read_skill_contract(skill_name, state):
                return _contract_required_payload(skill_name)
            schema_validation = validate_skill_input_schema(
                skill_name=skill_name,
                arguments=normalized_arguments,
                input_schema=getattr(skill.spec, "input_schema", {}),
            )
            if not schema_validation.get("valid"):
                return _build_validation_payload(
                    skill_name=skill_name,
                    arguments=normalized_arguments,
                    validation=schema_validation,
                    state=state,
                    error_message="Skill 调用参数不符合 input_schema，请修正后再执行。",
                    include_invalid_inputs=True,
                )
            if not _has_input_schema(skill):
                validation_payload = _input_validation_payload(
                    skill_name=skill_name,
                    arguments=normalized_arguments,
                    state=state,
                )
                if validation_payload is not None:
                    return validation_payload
            execution_entry = str(state.get("execution_entry", "")).strip()
            if skill.spec.approval_required and execution_entry not in {"auto_alert", "debug"}:
                if _is_rejected_in_current_run(skill_name=skill_name, arguments=normalized_arguments, state=state):
                    return _rejected_payload(skill_name=skill_name, arguments=normalized_arguments)
                return _approval_payload(skill_name=skill_name, arguments=normalized_arguments, state=state)
            context = {
                "event_id_ref": state.get("event_id_ref", ""),
                "alert_data": state.get("alert_data", {}),
            }
            try:
                result = skill_runtime.execute_skill(skill_name, normalized_arguments, context)
                payload = result.data if isinstance(result.data, dict) else {"result": result.data}
                return json.dumps(
                    {
                        "success": result.success,
                        "data": payload,
                        "error": result.error
                    },
                    ensure_ascii=False,
                )
            except Exception as exc:
                return json.dumps(
                    {
                        "success": False,
                        "data": {},
                        "error": f"Tool Execution Exception: {exc}"
                    },
                    ensure_ascii=False,
                )

        tools.append(execute_skill)

        @tool
        def execute_skill_no_args(
            skill_name: str,
            state: Annotated[SentinelFlowAgentState, InjectedState()],  # type: ignore[misc]
        ) -> str:
            """执行无入参技能并返回 JSON 字符串结果。"""
            cancel_event = state.get("cancel_event")
            if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                return json.dumps(
                    {"success": False, "data": {}, "error": "用户已停止当前任务。"},
                    ensure_ascii=False,
                )
            executable_skills_raw = state.get("executable_skills")
            executable_skills = set(executable_skills_raw or [])
            if executable_skills_raw is not None and skill_name not in executable_skills:
                return json.dumps(
                    {"success": False, "data": {}, "error": f"当前 Agent 未被授权执行技能 {skill_name}。"},
                    ensure_ascii=False,
                )
            skill, resolve_error = _resolve_skill_or_error(skill_name)
            if resolve_error is not None:
                return resolve_error
            if not _has_read_skill_contract(skill_name, state):
                return _contract_required_payload(skill_name)
            schema_validation = validate_skill_input_schema(
                skill_name=skill_name,
                arguments={},
                input_schema=getattr(skill.spec, "input_schema", {}),
            )
            if not schema_validation.get("valid"):
                return _build_validation_payload(
                    skill_name=skill_name,
                    arguments={},
                    validation=schema_validation,
                    state=state,
                    error_message="Skill 调用参数不符合 input_schema，请修正后再执行。",
                    include_invalid_inputs=True,
                )
            if not _has_input_schema(skill):
                validation_payload = _input_validation_payload(
                    skill_name=skill_name,
                    arguments={},
                    state=state,
                )
                if validation_payload is not None:
                    return validation_payload
            execution_entry = str(state.get("execution_entry", "")).strip()
            if skill.spec.approval_required and execution_entry not in {"auto_alert", "debug"}:
                if _is_rejected_in_current_run(skill_name=skill_name, arguments={}, state=state):
                    return _rejected_payload(skill_name=skill_name, arguments={})
                return _approval_payload(skill_name=skill_name, arguments={}, state=state)
            context = {
                "event_id_ref": state.get("event_id_ref", ""),
                "alert_data": state.get("alert_data", {}),
            }
            try:
                result = skill_runtime.execute_skill(skill_name, {}, context)
                payload = result.data if isinstance(result.data, dict) else {"result": result.data}
                return json.dumps(
                    {
                        "success": result.success,
                        "data": payload,
                        "error": result.error,
                    },
                    ensure_ascii=False,
                )
            except Exception as exc:
                return json.dumps(
                    {
                        "success": False,
                        "data": {},
                        "error": f"Tool Execution Exception: {exc}",
                    },
                    ensure_ascii=False,
                )

        tools.append(execute_skill_no_args)

    return tools
