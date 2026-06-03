from __future__ import annotations

import json
import logging
from pathlib import Path

from sentinelflow.agent.nodes import agent_node, should_continue
from sentinelflow.agent.run_log_tracer import get_active_tracer, make_logged_tool_node
from sentinelflow.agent.state import SentinelFlowAgentState
from sentinelflow.agent.tools import build_agent_tools
from sentinelflow.config.runtime import SentinelFlowRuntimeConfig, build_llm_client_kwargs
from sentinelflow.services.skill_approval_service import SkillApprovalService
from sentinelflow.skills.adapters import SentinelFlowSkillRuntime

LOGGER = logging.getLogger(__name__)


def build_agent_graph(
    project_root: Path,
    skill_runtime: SentinelFlowSkillRuntime,
    approval_service: SkillApprovalService,
    runtime_config: SentinelFlowRuntimeConfig,
    *,
    enable_read_skill_document: bool = True,
    enable_execute_skill: bool = True,
    executable_skill_names: list[str] | None = None,
    enable_synthesis_node: bool = True,
):
    try:
        from langgraph.graph import END, START, StateGraph
        from langgraph.prebuilt import ToolNode
        from langchain_openai import ChatOpenAI
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise ModuleNotFoundError(
            "缺少 Agent 运行依赖，请安装 langgraph、langchain-openai 和 langchain-core。"
        ) from exc

    tools = build_agent_tools(
        skill_runtime,
        approval_service,
        enable_read_skill_document=enable_read_skill_document,
        enable_execute_skill=enable_execute_skill,
        executable_skill_names=executable_skill_names,
    )
    llm = ChatOpenAI(**build_llm_client_kwargs(runtime_config)).bind_tools(tools)

    skill_root = project_root / ".sentinelflow" / "plugins" / "skills"

    async def _agent(state: SentinelFlowAgentState) -> dict:
        alert_data = state.get("alert_data", {})
        current_event_id = str(alert_data.get("eventIds", "")).strip()
        ref_event_id = str(state.get("event_id_ref", "")).strip()
        if current_event_id and ref_event_id and current_event_id != ref_event_id:
            raise ValueError("检测到 event_id_ref 与当前 alert_data.eventIds 不一致。")
        output = await agent_node(state, llm, skill_root)
        if current_event_id and not ref_event_id:
            output["event_id_ref"] = current_event_id
        return output

    def _extract_approval_request(state: SentinelFlowAgentState) -> dict | None:
        for msg in reversed(list(state.get("messages", []))):
            if getattr(msg, "type", "") != "tool":
                continue
            content = getattr(msg, "content", "")
            if not isinstance(content, str):
                continue
            try:
                payload = json.loads(content)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict) or not payload.get("approval_pending"):
                continue
            request = payload.get("approval_request", {})
            return request if isinstance(request, dict) else None
        return None

    def _approval_gate(state: SentinelFlowAgentState) -> dict:
        request = _extract_approval_request(state)
        if not request:
            return {"approval_pending": False, "approval_request": {}}
        return {
            "approval_pending": True,
            "approval_request": request,
        }

    def _route_after_tools(state: SentinelFlowAgentState) -> str:
        return "__end__" if state.get("approval_pending") else "agent_node"

    builder = StateGraph(SentinelFlowAgentState)
    builder.add_node("agent_node", _agent)
    builder.add_node(
        "tools_node",
        make_logged_tool_node(
            tools,
            scope="Agent",
            graph="agent_react",
            agent_name="",
            node="tools_node",
            tool_node_cls=ToolNode,
        ),
    )
    builder.add_node("approval_gate", _approval_gate)
    builder.add_edge(START, "agent_node")
    if enable_synthesis_node:
        builder.add_node("synthesis_node", _build_synthesis_node(runtime_config))
        # When the agent decides to stop (no more tool calls), run synthesis before END
        builder.add_conditional_edges("agent_node", should_continue, {"tools": "tools_node", "agent_node": "agent_node", "__end__": "synthesis_node"})
        builder.add_edge("synthesis_node", END)
    else:
        builder.add_conditional_edges("agent_node", should_continue, {"tools": "tools_node", "agent_node": "agent_node", "__end__": END})
    builder.add_edge("tools_node", "approval_gate")
    builder.add_conditional_edges("approval_gate", _route_after_tools, {"agent_node": "agent_node", "__end__": END})
    return builder.compile()


def _build_synthesis_node(runtime_config: SentinelFlowRuntimeConfig):
    """Factory that returns the synthesis_node coroutine closed over runtime_config."""

    async def _synthesis(state: SentinelFlowAgentState) -> dict:
        """
        Compress the agent conversation into a structured AlertJudgment.

        Skipped when:
        - approval is pending (agent has not finished yet)
        - alert_source is human_command (no judgment fields needed)
        """
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import HumanMessage, SystemMessage
        from sentinelflow.agent.prompts import SYNTHESIS_SYSTEM_PROMPT
        from sentinelflow.agent.schemas import parse_alert_judgment_content, stringify_llm_content

        # ── Skip conditions ──────────────────────────────────────────────────
        if state.get("approval_pending"):
            return {}
        alert_data = state.get("alert_data") or {}
        if str(alert_data.get("alert_source", "")).strip() == "human_command":
            return {}

        # ── Build synthesis message ──────────────────────────────────────────
        alert_json = json.dumps(alert_data, ensure_ascii=False, indent=2)
        conversation_parts: list[str] = [
            f"原始告警数据：\n```json\n{alert_json}\n```\n\n以下是处理过程："
        ]
        for msg in state.get("messages", []):
            msg_type = getattr(msg, "type", "")
            content = str(getattr(msg, "content", "")).strip()
            if not content:
                continue
            if msg_type == "ai":
                conversation_parts.append(f"\nAgent 分析：{content}")
            elif msg_type == "tool":
                # Truncate long tool results to stay within context limits
                conversation_parts.append(f"\nSkill 返回：{content[:600]}")

        synthesis_messages = [
            SystemMessage(
                content=(
                    f"{SYNTHESIS_SYSTEM_PROMPT}\n\n"
                    "输出要求：只返回一个 JSON 对象，不要使用 Markdown 代码块或额外说明。"
                )
            ),
            HumanMessage(content="\n".join(conversation_parts)),
        ]

        # ── Call synthesis LLM ───────────────────────────────────────────────
        try:
            llm = ChatOpenAI(**build_llm_client_kwargs(runtime_config, temperature=0))
            response = await llm.ainvoke(synthesis_messages)
            tracer = get_active_tracer()
            if tracer is not None:
                tracer.log(
                    event_type="synthesis_llm_response",
                    title="研判合成 · 模型输出",
                    data={
                        "scope": "synthesis",
                        "graph": "agent_react",
                        "agent_name": "synthesis_node",
                        "node": "synthesis_node",
                        "message": {
                            "type": "ai",
                            "content": stringify_llm_content(getattr(response, "content", response)),
                        },
                    },
                )
            content = stringify_llm_content(getattr(response, "content", response))
            result = parse_alert_judgment_content(content)
            return {"structured_judgment": result.model_dump()}
        except Exception:
            LOGGER.exception(
                "synthesis_node: synthesis output parsing failed, "
                "_serialize_alert_result will fall back to text parsing."
            )
            return {"structured_judgment": None}

    return _synthesis
