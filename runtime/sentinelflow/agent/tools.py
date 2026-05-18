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
        if include_invalid_inputs:
            data["invalid_inputs"] = validation.get("invalid_inputs", [])
        return json.dumps(
            {"success": False, "data": data, "error": error_message},
            ensure_ascii=False,
        )

    def _resolve_skill_or_error(skill_name: str) -> tuple[Any, str | None]:
        try:
            return skill_runtime.resolver.resolve(skill_name), None
        except Exception as exc:
            payload = json.dumps(
                {"success": False, "data": {}, "error": f"加载 Skill 失败：{exc}"},
                ensure_ascii=False,
            )
            return None, payload

    def _has_input_schema(skill: Any) -> bool:
        return bool(getattr(getattr(skill, "spec", None), "input_schema", {}) or {})

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
