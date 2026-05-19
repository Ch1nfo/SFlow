from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any

from sentinelflow.agent.message_trace import count_react_turns, parse_tool_payload, serialize_message
from sentinelflow.services.agent_run_log_service import AgentRunLogService, RunLogRef

_active_tracer: contextvars.ContextVar["RunLogTracer | None"] = contextvars.ContextVar(
    "sentinelflow_run_log_tracer",
    default=None,
)


@dataclass
class RunLogTracer:
    service: AgentRunLogService
    ref: RunLogRef | dict[str, Any]

    def log(
        self,
        *,
        event_type: str,
        title: str,
        data: dict[str, Any],
        level: str = "info",
    ) -> None:
        payload = {
            "event_type": event_type,
            **data,
        }
        self.service.append(self.ref, "react_trace", title, payload, level=level)

    def log_system_prompt(
        self,
        *,
        scope: str,
        graph: str,
        agent_name: str,
        content: str,
        node: str = "agent_node",
    ) -> None:
        text = str(content or "")
        self.log(
            event_type="system_prompt",
            title=f"{scope} · 系统提示词",
            data={
                "scope": scope,
                "graph": graph,
                "agent_name": agent_name,
                "node": node,
                "content": text,
                "content_chars": len(text),
                "truncated": False,
            },
        )

    def log_llm_response(
        self,
        *,
        scope: str,
        graph: str,
        agent_name: str,
        turn: int,
        message: Any,
        node: str,
    ) -> None:
        serialized = serialize_message(message)
        self.log(
            event_type="llm_response",
            title=f"{scope} · ReAct 第 {turn} 轮 · 模型输出",
            data={
                "scope": scope,
                "graph": graph,
                "agent_name": agent_name,
                "node": node,
                "turn": turn,
                "message": serialized,
                "has_tool_calls": bool(serialized.get("tool_calls")),
                "has_reasoning": bool(str(serialized.get("reasoning", "")).strip()),
            },
        )

    def log_tool_results(
        self,
        *,
        scope: str,
        graph: str,
        agent_name: str,
        turn: int,
        messages: list[Any],
        node: str,
    ) -> None:
        for index, message in enumerate(messages, start=1):
            serialized = serialize_message(message)
            raw_content = serialized.get("content")
            parsed_payload = parse_tool_payload(raw_content)
            self.log(
                event_type="tool_result",
                title=f"{scope} · ReAct 第 {turn} 轮 · 工具返回 #{index}",
                data={
                    "scope": scope,
                    "graph": graph,
                    "agent_name": agent_name,
                    "node": node,
                    "turn": turn,
                    "tool_index": index,
                    "message": serialized,
                    "parsed_payload": parsed_payload if isinstance(parsed_payload, (dict, list)) else {"raw": parsed_payload},
                },
            )

    def log_worker_boundary(
        self,
        *,
        worker: str,
        step: int,
        event_type: str,
        title: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.log(
            event_type=event_type,
            title=title,
            data={
                "scope": f"worker:{worker}",
                "graph": "worker_react",
                "agent_name": worker,
                "worker_step": step,
                **(extra or {}),
            },
        )

def get_active_tracer() -> RunLogTracer | None:
    return _active_tracer.get()


def activate_run_log_tracer(service: AgentRunLogService | None, ref: RunLogRef | dict[str, Any] | None) -> RunLogTracer | None:
    if service is None or ref is None:
        return None
    tracer = RunLogTracer(service=service, ref=ref)
    _active_tracer.set(tracer)
    return tracer


def deactivate_run_log_tracer() -> None:
    _active_tracer.set(None)


def scope_label(agent_name: str, *, default: str = "Agent") -> str:
    name = str(agent_name or "").strip()
    return name or default


def make_logged_tool_node(
    tools: list[Any],
    *,
    scope: str,
    graph: str,
    agent_name: str = "",
    node: str = "tools_node",
):
    from langgraph.prebuilt import ToolNode

    base = ToolNode(tools)

    async def _node(state: dict[str, Any]) -> dict[str, Any]:
        tracer = get_active_tracer()
        resolved_agent = str(agent_name or state.get("agent_name", "")).strip()
        resolved_scope = scope if agent_name else scope_label(resolved_agent, default=scope)
        turn = max(count_react_turns(list(state.get("messages", []) or [])), 1)
        result = await base.ainvoke(state)
        if tracer is not None:
            new_messages = result.get("messages", []) if isinstance(result, dict) else []
            if new_messages:
                tracer.log_tool_results(
                    scope=resolved_scope,
                    graph=graph,
                    agent_name=resolved_agent or resolved_scope,
                    turn=turn,
                    messages=list(new_messages),
                    node=node,
                )
        return result

    return _node
