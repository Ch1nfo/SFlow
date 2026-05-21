from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any

from sentinelflow.agent.message_trace import (
    build_skill_input_audit_record,
    compute_prompt_digest,
    count_react_turns,
    extract_skill_tool_calls,
    parse_tool_payload,
    serialize_message,
    serialize_messages_for_prompt_audit,
    summarize_prompt_stats,
)
from sentinelflow.services.agent_run_log_service import AgentRunLogService, RunLogRef

_active_tracer: contextvars.ContextVar["RunLogTracer | None"] = contextvars.ContextVar(
    "sentinelflow_run_log_tracer",
    default=None,
)


@dataclass
class RunLogTracer:
    service: AgentRunLogService
    ref: RunLogRef | dict[str, Any]

    def __post_init__(self) -> None:
        self._last_prompt_digest_by_turn: dict[tuple[str, str, str, str, int], str] = {}
        self._last_prompt_stats_by_turn: dict[tuple[str, str, str, str, int], dict[str, Any]] = {}

    @staticmethod
    def _prompt_cache_key(
        *,
        scope: str,
        graph: str,
        agent_name: str,
        node: str,
        turn: int,
    ) -> tuple[str, str, str, str, int]:
        # 同一次 run 里可能出现同名 scope（例如主 Agent 名字与某个 worker 名字重合），
        # 所以缓存键必须把 graph / agent_name / node 也纳入，避免后写者覆盖前者，
        # 导致 skill_call_decision 引到错误的 llm_request。
        return (
            str(graph or ""),
            str(agent_name or ""),
            str(scope or ""),
            str(node or ""),
            int(turn),
        )

    @staticmethod
    def _summarize_prompt_messages(prompt_messages: list[dict[str, Any]], *, preview_chars: int = 240) -> list[dict[str, Any]]:
        summary: list[dict[str, Any]] = []
        for item in prompt_messages:
            content = str(item.get("content", "") or "")
            preview = content[:preview_chars]
            summary.append(
                {
                    "message_index": item.get("message_index"),
                    "role": item.get("role") or item.get("type"),
                    "type": item.get("type"),
                    "content_chars": item.get("content_chars", len(content)),
                    "content_truncated": item.get("content_truncated", False),
                    "preview": preview,
                    "preview_truncated": len(content) > len(preview),
                    "tool_call_count": len(item.get("tool_calls") or []) if isinstance(item.get("tool_calls"), list) else 0,
                }
            )
        return summary

    @staticmethod
    def _system_prompt_summary(prompt_messages: list[dict[str, Any]]) -> dict[str, Any]:
        for item in prompt_messages:
            role = str(item.get("role") or item.get("type") or "").strip().lower()
            if role not in {"system", "systemmessage"}:
                continue
            return {
                "message_index": item.get("message_index"),
                "content_chars": item.get("content_chars", 0),
                "content_truncated": item.get("content_truncated", False),
            }
        return {"message_index": None, "content_chars": 0, "content_truncated": False}

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

    def log_llm_request(
        self,
        *,
        scope: str,
        graph: str,
        agent_name: str,
        turn: int,
        messages: list[Any],
        node: str,
        alert_data: dict[str, Any] | None = None,
        window_info: dict[str, Any] | None = None,
    ) -> int:
        llm_window_info = dict(window_info or {})
        prompt_messages, audit_window_info = serialize_messages_for_prompt_audit(messages)
        digest = compute_prompt_digest(prompt_messages)
        prompt_stats = summarize_prompt_stats(messages, prompt_messages, window_info=audit_window_info)
        if llm_window_info:
            prompt_stats["llm_window"] = llm_window_info
        truncated = bool(prompt_stats.get("window_truncated")) or bool(prompt_stats.get("content_truncated_any"))
        title_suffix = "提示词审计（已截断）" if truncated else "提示词审计"
        self.log(
            event_type="llm_request",
            title=f"{scope} · ReAct 第 {turn} 轮 · {title_suffix}",
            data={
                "scope": scope,
                "graph": graph,
                "agent_name": agent_name,
                "node": node,
                "turn": turn,
                "prompt_digest": digest,
                "audit_strip": ["additional_kwargs", "response_metadata"],
                "prompt_storage": "summary_only",
                "system_prompt": self._system_prompt_summary(prompt_messages),
                "prompt_message_summaries": self._summarize_prompt_messages(prompt_messages),
                "prompt_stats": prompt_stats,
                "llm_window_info": llm_window_info,
                "alert_data_keys": sorted(str(key) for key in (alert_data or {}).keys()) if isinstance(alert_data, dict) else [],
            },
        )
        cache_key = self._prompt_cache_key(
            scope=scope,
            graph=graph,
            agent_name=agent_name,
            node=node,
            turn=turn,
        )
        self._last_prompt_digest_by_turn[cache_key] = digest
        self._last_prompt_stats_by_turn[cache_key] = prompt_stats
        return prompt_stats.get("logged_message_count", len(prompt_messages))

    def log_skill_call_decision(
        self,
        *,
        scope: str,
        graph: str,
        agent_name: str,
        turn: int,
        request_messages: list[Any],
        response_message: Any,
        node: str,
    ) -> None:
        """Reference the llm_request prompt by digest instead of duplicating it."""
        serialized_response = serialize_message(response_message)
        constructed_calls = extract_skill_tool_calls(serialized_response.get("tool_calls") or [])
        if not constructed_calls:
            return
        skill_names = ", ".join(
            str(item.get("skill_name", "")).strip() for item in constructed_calls if str(item.get("skill_name", "")).strip()
        )
        cache_key = self._prompt_cache_key(
            scope=scope,
            graph=graph,
            agent_name=agent_name,
            node=node,
            turn=turn,
        )
        digest = self._last_prompt_digest_by_turn.get(cache_key, "")
        prompt_stats = self._last_prompt_stats_by_turn.get(cache_key, {})
        prompt_ref_source = "llm_request_cache" if digest else "recomputed_from_messages"
        if not digest:
            prompt_messages, window_info = serialize_messages_for_prompt_audit(request_messages)
            digest = compute_prompt_digest(prompt_messages)
            prompt_stats = summarize_prompt_stats(request_messages, prompt_messages, window_info=window_info)
        self.log(
            event_type="skill_call_decision",
            title=f"{scope} · Skill 调用决策 · {skill_names or 'execute_skill'}",
            data={
                "scope": scope,
                "graph": graph,
                "agent_name": agent_name,
                "node": node,
                "turn": turn,
                "prompt_reference": {
                    "event_type": "llm_request",
                    "turn": turn,
                    "scope": scope,
                    "graph": graph,
                    "agent_name": agent_name,
                    "node": node,
                    "prompt_digest": digest,
                    "source": prompt_ref_source,
                    "note": "完整提示词请打开同 (graph, agent_name, node, turn) 的 llm_request 事件，避免重复落盘。",
                },
                "prompt_stats": prompt_stats,
                "model_content": str(serialized_response.get("content", "")).strip(),
                "constructed_tool_calls": constructed_calls,
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
        request_messages: list[Any] | None = None,
        alert_data: dict[str, Any] | None = None,
    ) -> None:
        serialized = serialize_message(message)
        tool_calls = serialized.get("tool_calls") or []
        if request_messages and extract_skill_tool_calls(tool_calls):
            self.log_skill_call_decision(
                scope=scope,
                graph=graph,
                agent_name=agent_name,
                turn=turn,
                request_messages=request_messages,
                response_message=message,
                node=node,
            )
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
                "has_tool_calls": bool(tool_calls),
                "has_reasoning": bool(str(serialized.get("reasoning", "")).strip()),
            },
        )

    def log_skill_input_validation(
        self,
        *,
        scope: str,
        graph: str,
        agent_name: str,
        audit: dict[str, Any],
        node: str = "execute_skill",
    ) -> None:
        skill_name = str(audit.get("skill_name", "")).strip() or "skill"
        compliant = bool(audit.get("compliant"))
        outcome = str(audit.get("outcome", "")).strip()
        level = "info" if compliant else "warn"
        title = (
            f"{scope} · Skill 入参合规 · {skill_name} · 通过"
            if compliant
            else f"{scope} · Skill 入参不合规 · {skill_name} · {outcome}"
        )
        self.log(
            event_type="skill_input_validation",
            title=title,
            data={
                "scope": scope,
                "graph": graph,
                "agent_name": agent_name,
                "node": node,
                "audit": audit,
            },
            level=level,
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
    tool_node_cls: Any = None,
):
    from langgraph.prebuilt import ToolNode

    base = (tool_node_cls or ToolNode)(tools)

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
