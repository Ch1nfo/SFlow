from __future__ import annotations

import json
from typing import Literal

from sentinelflow.agent.catalog import load_skill_catalog
from sentinelflow.agent.context_utils import build_context_envelope, build_context_manifest, format_context_manifest_header
from sentinelflow.agent.prompt_builder import PromptBuildContext, build_prompt
from sentinelflow.agent.message_trace import count_react_turns
from sentinelflow.agent.run_log_tracer import get_active_tracer, scope_label
from sentinelflow.agent.state import SentinelFlowAgentState

try:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
except ModuleNotFoundError:  # pragma: no cover
    AIMessage = HumanMessage = SystemMessage = object  # type: ignore[assignment]


async def agent_node(state: SentinelFlowAgentState, llm, skill_root) -> dict:
    cancel_event = state.get("cancel_event")
    if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
        raise RuntimeError("用户已停止当前任务。")

    alert_data = state["alert_data"]
    is_human_command = alert_data.get("alert_source") == "human_command"
    readable_skills = state.get("readable_skills")
    skill_catalog = load_skill_catalog(skill_root, readable_skills)
    custom_prompt = str(state.get("system_prompt_override", "")).strip()

    if is_human_command:
        prompt = build_prompt(
            PromptBuildContext(
                base_prompt=custom_prompt,
                mode="agent_command",
                entry_type="conversation",
                skill_catalog=skill_catalog,
            )
        )
        system_msg = SystemMessage(content=prompt)
        payload = str(alert_data.get("payload", ""))
        delegated_task_prompt = str(alert_data.get("delegated_task_prompt", ""))
        if delegated_task_prompt.strip():
            prior_facts = alert_data.get("prior_facts", {}) if isinstance(alert_data.get("prior_facts"), dict) else {}
            manifest = build_context_manifest(
                current_goal=delegated_task_prompt,
                entry_type="conversation",
                original_input=payload,
                current_task_prompt=delegated_task_prompt,
                model_summary=prior_facts,
            )
            envelope = build_context_envelope(
                original_input=payload,
                delegated_task=delegated_task_prompt,
                prior_facts=prior_facts,
                authoritative_inputs={
                    "delegated_task": delegated_task_prompt,
                    "original_input": payload,
                    "prior_facts": prior_facts,
                },
            )
            initial_msg = HumanMessage(
                content=(
                    f"{format_context_manifest_header(manifest)}\n"
                    "请执行以下主 Agent 分派任务。当前执行目标以 delegated_task 为准，"
                    "original_input 只作为背景：\n\n"
                    f"```json\n{json.dumps(envelope, ensure_ascii=False, indent=2)}\n```"
                )
            )
        else:
            initial_msg = HumanMessage(content=f"请执行以下人工指令：{payload}")
    else:
        handling_intent = str(alert_data.get("handling_intent", "")).strip()
        prompt = build_prompt(
            PromptBuildContext(
                base_prompt=custom_prompt,
                mode="agent_alert",
                entry_type="alert",
                action_hint=handling_intent,
                skill_catalog=skill_catalog,
            )
        )
        system_msg = SystemMessage(content=prompt)
        alert_json = json.dumps(alert_data, ensure_ascii=False, indent=2)
        delegated_task_prompt = str(alert_data.get("delegated_task_prompt", ""))
        if delegated_task_prompt.strip():
            prior_facts = alert_data.get("prior_facts", {}) if isinstance(alert_data.get("prior_facts"), dict) else {}
            manifest = build_context_manifest(
                current_goal=delegated_task_prompt,
                entry_type="alert",
                original_input=alert_data,
                current_task_prompt=delegated_task_prompt,
                model_summary=prior_facts,
            )
            envelope = build_context_envelope(
                original_input=alert_data,
                delegated_task=delegated_task_prompt,
                prior_facts=prior_facts,
                authoritative_inputs={
                    "delegated_task": delegated_task_prompt,
                    "original_input": alert_data,
                    "prior_facts": prior_facts,
                },
            )
            initial_msg = HumanMessage(
                content=(
                    f"{format_context_manifest_header(manifest)}\n"
                    "请分析并处置以下上下文。当前执行目标以 delegated_task 为准，"
                    "original_input 只作为背景：\n\n"
                    f"```json\n{json.dumps(envelope, ensure_ascii=False, indent=2)}\n```"
                )
            )
        else:
            initial_msg = HumanMessage(content=f"请分析并处置以下告警：\n\n```json\n{alert_json}\n```")

    current_messages = list(state.get("messages", []))
    input_seeded = bool(state.get("input_seeded"))
    if not current_messages:
        messages_to_send = [system_msg, initial_msg]
        seeded_messages = [initial_msg]
        seeded_flag = True
    elif not input_seeded:
        messages_to_send = [system_msg] + current_messages + [initial_msg]
        seeded_messages = [initial_msg]
        seeded_flag = True
    else:
        messages_to_send = [system_msg] + current_messages
        seeded_messages = []
        seeded_flag = True

    agent_name = str(state.get("agent_name", "")).strip()
    scope = scope_label(agent_name, default="子 Agent")
    tracer = get_active_tracer()
    if tracer is not None and not current_messages:
        tracer.log_system_prompt(
            scope=scope,
            graph="agent_react",
            agent_name=agent_name,
            content=str(getattr(system_msg, "content", "")),
            node="agent_node",
        )

    turn = count_react_turns(current_messages) + 1
    if tracer is not None:
        tracer.log_llm_request(
            scope=scope,
            graph="agent_react",
            agent_name=agent_name,
            turn=turn,
            messages=messages_to_send,
            node="agent_node",
            alert_data=alert_data if isinstance(alert_data, dict) else {},
        )
    response = await llm.ainvoke(messages_to_send)
    if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
        raise RuntimeError("用户已停止当前任务。")
    if tracer is not None:
        tracer.log_llm_response(
            scope=scope,
            graph="agent_react",
            agent_name=agent_name,
            turn=turn,
            message=response,
            node="agent_node",
            request_messages=messages_to_send,
            alert_data=alert_data if isinstance(alert_data, dict) else {},
        )
    return {"messages": seeded_messages + [response], "input_seeded": seeded_flag}


def should_continue(state: SentinelFlowAgentState) -> Literal["tools", "__end__"]:
    last_msg = state["messages"][-1]
    if isinstance(last_msg, AIMessage) and getattr(last_msg, "tool_calls", None):
        return "tools"
    return "__end__"
