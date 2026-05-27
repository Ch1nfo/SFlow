from __future__ import annotations

import json
from typing import Annotated, Any

from sentinelflow.agent.context_utils import (
    build_context_manifest,
    build_safe_current_goal,
    compact_text,
    validate_execution_inputs,
    validate_skill_input_schema,
)
from sentinelflow.agent.message_trace import build_skill_input_audit_record
from sentinelflow.agent.run_log_tracer import get_active_tracer, scope_label
from sentinelflow.agent.state import SentinelFlowAgentState
from sentinelflow.domain.enums import SkillType
from sentinelflow.skills.adapters import SentinelFlowSkillRuntime
from sentinelflow.services.skill_approval_service import SkillApprovalService

try:
    from langchain_core.tools import tool
    from langgraph.prebuilt import InjectedState
except ModuleNotFoundError:  # pragma: no cover
    tool = None  # type: ignore[assignment]
    InjectedState = object  # type: ignore[assignment]


def _format_schema_property_label(
    field: str,
    field_schema: Any,
    *,
    description_limit: int = 120,
) -> str:
    if not isinstance(field_schema, dict):
        return str(field)
    parts: list[str] = []
    field_type = str(field_schema.get("type", "")).strip()
    if field_type:
        parts.append(field_type)
    enum_values = field_schema.get("enum")
    if isinstance(enum_values, list) and enum_values:
        enum_repr = "|".join(str(item) for item in enum_values)
        parts.append(f"enum={enum_repr}")
    label = f"{field}: {' '.join(parts)}" if parts else field
    description = compact_text(field_schema.get("description", ""), description_limit)
    if description:
        label += f" ({description})"
    return label


def build_agent_tools(
    skill_runtime: SentinelFlowSkillRuntime,
    approval_service: SkillApprovalService,
    *,
    enable_read_skill_document: bool = True,
    enable_execute_skill: bool = True,
    executable_skill_names: list[str] | None = None,
) -> list:
    if tool is None:
        raise ModuleNotFoundError("langchain_core/langgraph 未安装，无法构建 Agent tools。")

    tools: list = []

    def _normalize_skill_arguments(arguments: dict[str, Any] | str | None) -> tuple[dict[str, Any], str | None]:
        if arguments is None:
            return {}, None
        if isinstance(arguments, dict):
            nested = arguments.get("arguments")
            wrapper_keys = {"skill_name", "arguments"}
            if isinstance(nested, dict) and set(str(key) for key in arguments.keys()).issubset(wrapper_keys):
                return nested, None
            return arguments, None
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments)
            except json.JSONDecodeError:
                return {}, "Skill arguments must be a JSON object."
            if isinstance(parsed, dict):
                return _normalize_skill_arguments(parsed)
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

    def _audit_skill_input(
        state: SentinelFlowAgentState,
        *,
        skill_name: str,
        arguments: dict[str, Any] | None,
        outcome: str,
        compliant: bool,
        error: str = "",
        validation: dict[str, Any] | None = None,
        input_schema: dict[str, Any] | None = None,
        suggested_arguments: dict[str, Any] | None = None,
        execution_result: dict[str, Any] | None = None,
    ) -> None:
        tracer = get_active_tracer()
        if tracer is None:
            return
        agent_name = str(state.get("agent_name", "")).strip()
        audit = build_skill_input_audit_record(
            skill_name=skill_name,
            arguments=arguments,
            outcome=outcome,
            compliant=compliant,
            error=error,
            validation=validation,
            input_schema=input_schema,
            suggested_arguments=suggested_arguments,
            execution_result=execution_result,
        )
        tracer.log_skill_input_validation(
            scope=scope_label(agent_name, default="Agent"),
            graph="agent_react",
            agent_name=agent_name,
            audit=audit,
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

    def _has_input_schema(skill: Any) -> bool:
        return bool(getattr(getattr(skill, "spec", None), "input_schema", {}) or {})

    def _format_skill_schema_lines(skill_names: list[str] | None) -> list[str]:
        if not skill_names:
            return []
        lines: list[str] = []
        for name in skill_names:
            normalized_name = str(name or "").strip()
            if not normalized_name:
                continue
            try:
                skill = skill_runtime.resolver.resolve(normalized_name)
            except Exception:
                continue
            input_schema = getattr(skill.spec, "input_schema", None)
            if not isinstance(input_schema, dict) or not input_schema:
                continue
            required = input_schema.get("required") or []
            if not isinstance(required, list):
                required = []
            required_names = [str(field) for field in required]
            properties = input_schema.get("properties") or {}
            if not isinstance(properties, dict):
                properties = {}
            ordered_fields: list[str] = []
            for field_name in required_names:
                if field_name in properties and field_name not in ordered_fields:
                    ordered_fields.append(field_name)
            for field_name in properties.keys():
                normalized_name = str(field_name).strip()
                if normalized_name and normalized_name not in ordered_fields:
                    ordered_fields.append(normalized_name)
            prop_parts: list[str] = []
            for field in ordered_fields:
                field_schema = properties.get(field, {})
                prop_parts.append(_format_schema_property_label(field, field_schema))
            additional = input_schema.get("additionalProperties", True)
            additional_repr = "true" if (not isinstance(additional, bool)) or additional else "false"
            properties_repr = "{" + ", ".join(prop_parts) + "}" if prop_parts else "{}"
            lines.append(
                f"- {normalized_name}: required={required_names}; "
                f"properties={properties_repr}; "
                f"additionalProperties={additional_repr}"
            )
        return lines

    schema_lines = _format_skill_schema_lines(executable_skill_names)
    if schema_lines:
        schema_appendix = "\n".join(
            [
                "当前授权可执行 Skill 的入参契约（取自 input_schema，未列出的 Skill 未声明 input_schema，"
                "如需详情请用 read_skill_document 查看）：",
                *schema_lines,
            ]
        )
    else:
        schema_appendix = ""

    execute_skill_description = (
        "执行指定技能并返回 JSON 字符串结果。"
        "调用前请严格按下方 input_schema 传参；缺必填字段或类型不符会被拒绝并返回 "
        "missing_required_inputs / invalid_inputs，请据此修正后再调用。"
    )
    if schema_appendix:
        execute_skill_description = f"{execute_skill_description}\n\n{schema_appendix}"

    execute_skill_no_args_description = (
        "执行无入参技能并返回 JSON 字符串结果。仅适用于 input_schema 没有 required 字段的 Skill。"
    )
    if schema_appendix:
        execute_skill_no_args_description = f"{execute_skill_no_args_description}\n\n{schema_appendix}"

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
                markdown = str(result.markdown or "")
                preview = compact_text(markdown, 4000)
                return json.dumps(
                    {
                        "success": True,
                        "data": {
                            "skill_name": skill_name,
                            "markdown_preview": preview,
                            "preview_chars": len(preview),
                            "original_chars": len(markdown),
                            "truncated": len(preview) < len(markdown),
                            "note": "完整 Skill 文档保留在 runtime；LLM 默认只接收 preview 以控制上下文窗口。",
                        },
                        "error": None,
                    },
                    ensure_ascii=False,
                )
            except Exception as exc:
                return json.dumps(
                    {"success": False, "data": {}, "error": f"读取技能文档失败：{exc}"},
                    ensure_ascii=False,
                )

        tools.append(read_skill_document)

    if enable_execute_skill:
        def _execute_skill_impl(
            skill_name: str,
            state: Annotated[SentinelFlowAgentState, InjectedState()],  # type: ignore[misc]
            arguments: dict[str, Any] | str | None = None,
        ) -> str:
            normalized_arguments, argument_error = _normalize_skill_arguments(arguments)
            if argument_error is not None:
                _audit_skill_input(
                    state,
                    skill_name=skill_name,
                    arguments=normalized_arguments,
                    outcome="rejected_arguments_shape",
                    compliant=False,
                    error=argument_error,
                )
                return _invalid_arguments_payload(skill_name, argument_error)
            cancel_event = state.get("cancel_event")
            if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                return json.dumps(
                    {"success": False, "data": {}, "error": "用户已停止当前任务。"},
                    ensure_ascii=False,
                )
            skill, resolve_error = _resolve_skill_or_error(skill_name)
            if resolve_error is not None:
                return resolve_error
            if skill.spec.type == SkillType.DOC:
                return json.dumps(
                    {
                        "success": False,
                        "data": {"skill_name": skill_name},
                        "error": (
                            f"Skill「{skill_name}」是纯文本文档型 Skill，不能使用 execute_skill；"
                            f'请改用 read_skill_document("{skill_name}") 读取说明。'
                        ),
                    },
                    ensure_ascii=False,
                )
            executable_skills_raw = state.get("executable_skills")
            executable_skills = set(executable_skills_raw or [])
            if executable_skills_raw is not None and skill_name not in executable_skills:
                _audit_skill_input(
                    state,
                    skill_name=skill_name,
                    arguments=normalized_arguments,
                    outcome="rejected_unauthorized",
                    compliant=False,
                    error=f"当前 Agent 未被授权执行技能 {skill_name}。",
                )
                return json.dumps(
                    {"success": False, "data": {}, "error": f"当前 Agent 未被授权执行技能 {skill_name}。"},
                    ensure_ascii=False,
                )
            input_schema = getattr(skill.spec, "input_schema", {})
            input_schema = input_schema if isinstance(input_schema, dict) else {}
            schema_validation = validate_skill_input_schema(
                skill_name=skill_name,
                arguments=normalized_arguments,
                input_schema=input_schema,
            )
            if not schema_validation.get("valid"):
                suggested = _suggest_skill_arguments(skill_name, normalized_arguments)
                _audit_skill_input(
                    state,
                    skill_name=skill_name,
                    arguments=normalized_arguments,
                    outcome="rejected_schema",
                    compliant=False,
                    error="Skill 调用参数不符合 input_schema，请修正后再执行。",
                    validation=schema_validation,
                    input_schema=input_schema,
                    suggested_arguments=suggested or None,
                )
                return _build_validation_payload(
                    skill_name=skill_name,
                    arguments=normalized_arguments,
                    validation=schema_validation,
                    state=state,
                    error_message="Skill 调用参数不符合 input_schema，请修正后再执行。",
                    include_invalid_inputs=True,
                )
            alert_data = state.get("alert_data", {})
            task_prompt = ""
            if isinstance(alert_data, dict):
                task_prompt = str(alert_data.get("delegated_task_prompt") or alert_data.get("payload") or "")
            execution_validation = validate_execution_inputs(
                skill_name=skill_name,
                arguments=normalized_arguments,
                task_prompt=task_prompt,
                enforce_required=not _has_input_schema(skill),
            )
            if not execution_validation.get("valid"):
                _audit_skill_input(
                    state,
                    skill_name=skill_name,
                    arguments=normalized_arguments,
                    outcome="rejected_execution_inputs",
                    compliant=False,
                    error="Skill 调用缺少必需执行参数，请先补齐后再执行。",
                    validation=execution_validation,
                    input_schema=input_schema,
                )
                return _build_validation_payload(
                    skill_name=skill_name,
                    arguments=normalized_arguments,
                    validation=execution_validation,
                    state=state,
                    error_message="Skill 调用缺少必需执行参数，请先补齐后再执行。",
                    include_invalid_inputs=False,
                )
            execution_entry = str(state.get("execution_entry", "")).strip()
            if skill.spec.approval_required and execution_entry not in {"auto_alert", "debug"}:
                if _is_rejected_in_current_run(skill_name=skill_name, arguments=normalized_arguments, state=state):
                    _audit_skill_input(
                        state,
                        skill_name=skill_name,
                        arguments=normalized_arguments,
                        outcome="blocked_user_rejected",
                        compliant=False,
                        error=f"Skill「{skill_name}」已被用户拒绝，本轮相同参数不会再次发起审批。",
                        validation=schema_validation,
                        input_schema=input_schema,
                    )
                    return _rejected_payload(skill_name=skill_name, arguments=normalized_arguments)
                _audit_skill_input(
                    state,
                    skill_name=skill_name,
                    arguments=normalized_arguments,
                    outcome="blocked_approval_required",
                    compliant=True,
                    error="等待人工审批后执行。",
                    validation=schema_validation,
                    input_schema=input_schema,
                )
                return _approval_payload(skill_name=skill_name, arguments=normalized_arguments, state=state)
            context = {
                "event_id_ref": state.get("event_id_ref", ""),
                "alert_data": state.get("alert_data", {}),
            }
            try:
                result = skill_runtime.execute_skill(skill_name, normalized_arguments, context)
                payload = result.data if isinstance(result.data, dict) else {"result": result.data}
                _audit_skill_input(
                    state,
                    skill_name=skill_name,
                    arguments=normalized_arguments,
                    outcome="executed_success" if result.success else "executed_failure",
                    compliant=True,
                    error=str(result.error or "").strip(),
                    validation=schema_validation,
                    input_schema=input_schema,
                    execution_result={"success": result.success, "data": payload, "error": result.error},
                )
                return json.dumps(
                    {
                        "success": result.success,
                        "data": payload,
                        "error": result.error
                    },
                    ensure_ascii=False,
                )
            except Exception as exc:
                _audit_skill_input(
                    state,
                    skill_name=skill_name,
                    arguments=normalized_arguments,
                    outcome="executed_exception",
                    compliant=True,
                    error=f"Tool Execution Exception: {exc}",
                    validation=schema_validation,
                    input_schema=input_schema,
                )
                return json.dumps(
                    {
                        "success": False,
                        "data": {},
                        "error": f"Tool Execution Exception: {exc}"
                    },
                    ensure_ascii=False,
                )

        execute_skill = tool("execute_skill", description=execute_skill_description)(_execute_skill_impl)
        tools.append(execute_skill)

        def _execute_skill_no_args_impl(
            skill_name: str,
            state: Annotated[SentinelFlowAgentState, InjectedState()],  # type: ignore[misc]
        ) -> str:
            cancel_event = state.get("cancel_event")
            if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                return json.dumps(
                    {"success": False, "data": {}, "error": "用户已停止当前任务。"},
                    ensure_ascii=False,
                )
            skill, resolve_error = _resolve_skill_or_error(skill_name)
            if resolve_error is not None:
                return resolve_error
            if skill.spec.type == SkillType.DOC:
                return json.dumps(
                    {
                        "success": False,
                        "data": {"skill_name": skill_name},
                        "error": (
                            f"Skill「{skill_name}」是纯文本文档型 Skill，不能使用 execute_skill_no_args；"
                            f'请改用 read_skill_document("{skill_name}") 读取说明。'
                        ),
                    },
                    ensure_ascii=False,
                )
            executable_skills_raw = state.get("executable_skills")
            executable_skills = set(executable_skills_raw or [])
            if executable_skills_raw is not None and skill_name not in executable_skills:
                return json.dumps(
                    {"success": False, "data": {}, "error": f"当前 Agent 未被授权执行技能 {skill_name}。"},
                    ensure_ascii=False,
                )
            no_args: dict[str, Any] = {}
            input_schema = getattr(skill.spec, "input_schema", {})
            input_schema = input_schema if isinstance(input_schema, dict) else {}
            schema_validation = validate_skill_input_schema(
                skill_name=skill_name,
                arguments=no_args,
                input_schema=input_schema,
            )
            if not schema_validation.get("valid"):
                _audit_skill_input(
                    state,
                    skill_name=skill_name,
                    arguments=no_args,
                    outcome="rejected_schema",
                    compliant=False,
                    error="Skill 调用参数不符合 input_schema，请修正后再执行。",
                    validation=schema_validation,
                    input_schema=input_schema,
                )
                return _build_validation_payload(
                    skill_name=skill_name,
                    arguments=no_args,
                    validation=schema_validation,
                    state=state,
                    error_message="Skill 调用参数不符合 input_schema，请修正后再执行。",
                    include_invalid_inputs=True,
                )
            alert_data = state.get("alert_data", {})
            task_prompt = ""
            if isinstance(alert_data, dict):
                task_prompt = str(alert_data.get("delegated_task_prompt") or alert_data.get("payload") or "")
            execution_validation = validate_execution_inputs(
                skill_name=skill_name,
                arguments=no_args,
                task_prompt=task_prompt,
                enforce_required=not _has_input_schema(skill),
            )
            if not execution_validation.get("valid"):
                _audit_skill_input(
                    state,
                    skill_name=skill_name,
                    arguments=no_args,
                    outcome="rejected_execution_inputs",
                    compliant=False,
                    error="Skill 调用缺少必需执行参数，请先补齐后再执行。",
                    validation=execution_validation,
                    input_schema=input_schema,
                )
                return _build_validation_payload(
                    skill_name=skill_name,
                    arguments=no_args,
                    validation=execution_validation,
                    state=state,
                    error_message="Skill 调用缺少必需执行参数，请先补齐后再执行。",
                    include_invalid_inputs=False,
                )
            execution_entry = str(state.get("execution_entry", "")).strip()
            if skill.spec.approval_required and execution_entry not in {"auto_alert", "debug"}:
                if _is_rejected_in_current_run(skill_name=skill_name, arguments=no_args, state=state):
                    return _rejected_payload(skill_name=skill_name, arguments=no_args)
                _audit_skill_input(
                    state,
                    skill_name=skill_name,
                    arguments=no_args,
                    outcome="blocked_approval_required",
                    compliant=True,
                    error="等待人工审批后执行。",
                    validation=schema_validation,
                    input_schema=input_schema,
                )
                return _approval_payload(skill_name=skill_name, arguments=no_args, state=state)
            context = {
                "event_id_ref": state.get("event_id_ref", ""),
                "alert_data": state.get("alert_data", {}),
            }
            try:
                result = skill_runtime.execute_skill(skill_name, no_args, context)
                payload = result.data if isinstance(result.data, dict) else {"result": result.data}
                _audit_skill_input(
                    state,
                    skill_name=skill_name,
                    arguments=no_args,
                    outcome="executed_success" if result.success else "executed_failure",
                    compliant=True,
                    error=str(result.error or "").strip(),
                    validation=schema_validation,
                    input_schema=input_schema,
                    execution_result={"success": result.success, "data": payload, "error": result.error},
                )
                return json.dumps(
                    {
                        "success": result.success,
                        "data": payload,
                        "error": result.error,
                    },
                    ensure_ascii=False,
                )
            except Exception as exc:
                _audit_skill_input(
                    state,
                    skill_name=skill_name,
                    arguments=no_args,
                    outcome="executed_exception",
                    compliant=True,
                    error=f"Tool Execution Exception: {exc}",
                    validation=schema_validation,
                    input_schema=input_schema,
                )
                return json.dumps(
                    {
                        "success": False,
                        "data": {},
                        "error": f"Tool Execution Exception: {exc}",
                    },
                    ensure_ascii=False,
                )

        execute_skill_no_args = tool(
            "execute_skill_no_args", description=execute_skill_no_args_description
        )(_execute_skill_no_args_impl)
        tools.append(execute_skill_no_args)

    return tools
