from __future__ import annotations

import json
import re
import threading
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sentinelflow.config.runtime import CONFIG_DIR, load_runtime_config, save_runtime_config


RUN_LOG_ROOT = CONFIG_DIR / "run_logs"
_SAFE_NAME_RE = re.compile(r"[^0-9A-Za-z._-]+")


@dataclass(frozen=True, slots=True)
class RunLogRef:
    date: str
    log_id: str
    path: Path

    def to_dict(self) -> dict[str, str]:
        return {
            "date": self.date,
            "log_id": self.log_id,
            "path": str(self.path),
        }


def _safe_name(value: Any, fallback: str = "unknown") -> str:
    text = str(value or "").strip() or fallback
    text = _SAFE_NAME_RE.sub("_", text)
    return text[:120] or fallback


def _json_default(value: Any) -> str:
    return str(value)


def _event_date(alert_data: dict[str, Any]) -> str:
    raw = str(alert_data.get("alert_time") or alert_data.get("alertTime") or "").strip()
    if len(raw) >= 10:
        candidate = raw[:10]
        try:
            date.fromisoformat(candidate)
            return candidate
        except ValueError:
            pass
    return datetime.now().date().isoformat()


def _event_title(alert_data: dict[str, Any], task_title: str = "") -> str:
    alert_name = str(alert_data.get("alert_name") or alert_data.get("alertName") or task_title or "").strip()
    event_ids = str(alert_data.get("eventIds") or alert_data.get("event_ids") or "").strip()
    sip = str(alert_data.get("sip") or "").strip()
    dip = str(alert_data.get("dip") or "").strip()
    parts = [part for part in [event_ids, alert_name, f"sip={sip}" if sip else "", f"dip={dip}" if dip else ""] if part]
    return " ".join(parts) or "未命名告警"


class AgentRunLogService:
    def __init__(self, root: Path = RUN_LOG_ROOT) -> None:
        self.root = root
        self._lock = threading.Lock()
        self._seq_counters: dict[str, int] = {}

    def _seq_key(self, ref: RunLogRef) -> str:
        return f"{ref.date}/{ref.log_id}"

    def _max_seq_on_disk(self, path: Path) -> int:
        if not path.is_file():
            return 0
        max_seq = 0
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(item, dict):
                        continue
                    candidate = item.get("seq")
                    if isinstance(candidate, int) and candidate > max_seq:
                        max_seq = candidate
                        continue
                    nested = item.get("data") if isinstance(item.get("data"), dict) else {}
                    nested_seq = nested.get("seq") if isinstance(nested, dict) else None
                    if isinstance(nested_seq, int) and nested_seq > max_seq:
                        max_seq = nested_seq
        except OSError:
            return max_seq
        return max_seq

    def _next_seq_locked(self, ref: RunLogRef) -> int:
        key = self._seq_key(ref)
        if key not in self._seq_counters:
            self._seq_counters[key] = self._max_seq_on_disk(ref.path)
        self._seq_counters[key] += 1
        return self._seq_counters[key]

    def derive_ref(
        self,
        *,
        task_id: str,
        event_ids: str,
        alert_data: dict[str, Any] | None = None,
    ) -> RunLogRef:
        alert_data = alert_data or {}
        log_date = _event_date(alert_data)
        safe_event = _safe_name(event_ids or alert_data.get("eventIds") or task_id, "event")
        safe_task = _safe_name(task_id, "task")
        log_id = f"{safe_event}_{safe_task}"
        return RunLogRef(date=log_date, log_id=log_id, path=self.root / log_date / f"{log_id}.jsonl")

    def retention_days(self) -> int:
        return max(int(getattr(load_runtime_config(), "run_log_retention_days", 1) or 1), 1)

    def set_retention_days(self, days: int) -> int:
        normalized = max(int(days or 1), 1)
        save_runtime_config({"run_log_retention_days": normalized})
        self.cleanup()
        return normalized

    def cleanup(self) -> None:
        if not self.root.exists():
            return
        cutoff = date.today() - timedelta(days=self.retention_days() - 1)
        for child in self.root.iterdir():
            if not child.is_dir():
                continue
            try:
                child_date = date.fromisoformat(child.name)
            except ValueError:
                continue
            if child_date >= cutoff:
                continue
            for file_path in child.glob("*.jsonl"):
                file_path.unlink(missing_ok=True)
            try:
                child.rmdir()
            except OSError:
                pass

    def start_run(
        self,
        *,
        task_id: str,
        event_ids: str,
        alert_data: dict[str, Any],
        selected_action: str,
        execution_entry: str,
        task_title: str = "",
    ) -> RunLogRef:
        self.cleanup()
        log_date = _event_date(alert_data)
        date_dir = self.root / log_date
        safe_event = _safe_name(event_ids or alert_data.get("eventIds") or task_id, "event")
        safe_task = _safe_name(task_id, "task")
        log_id = f"{safe_event}_{safe_task}"
        ref = RunLogRef(date=log_date, log_id=log_id, path=date_dir / f"{log_id}.jsonl")
        metadata = {
            "task_id": task_id,
            "event_ids": event_ids or str(alert_data.get("eventIds") or ""),
            "title": _event_title(alert_data, task_title),
            "alert_name": str(alert_data.get("alert_name") or ""),
            "alert_time": str(alert_data.get("alert_time") or ""),
            "source_id": str(alert_data.get("_source_id") or ""),
            "source_name": str(alert_data.get("_source_name") or alert_data.get("alert_source") or ""),
            "selected_action": selected_action,
            "execution_entry": execution_entry,
        }
        self.append(ref, "run_started", "接收告警", {"alert_data": alert_data, "metadata": metadata}, metadata=metadata)
        return ref

    def append(
        self,
        ref: RunLogRef | dict[str, Any] | None,
        phase: str,
        title: str,
        data: Any,
        *,
        level: str = "info",
        metadata: dict[str, Any] | None = None,
        seq: int | None = None,
    ) -> None:
        resolved = self._resolve_ref(ref)
        if resolved is None:
            return
        with self._lock:
            resolved_seq = seq if seq is not None else self._next_seq_locked(resolved)
            payload = data
            if isinstance(payload, dict) and "seq" not in payload:
                payload = {**payload, "seq": resolved_seq}
            event = {
                "ts": datetime.now().isoformat(timespec="milliseconds"),
                "seq": resolved_seq,
                "level": level,
                "phase": phase,
                "title": title,
                "metadata": metadata or {},
                "data": payload,
            }
            line = json.dumps(event, ensure_ascii=False, default=_json_default)
            resolved.path.parent.mkdir(parents=True, exist_ok=True)
            with resolved.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def record_agent_result(self, ref: RunLogRef | dict[str, Any] | None, agent_result: dict[str, Any]) -> None:
        if not isinstance(agent_result, dict):
            return
        for index, workflow in enumerate(agent_result.get("workflow_runs") or []):
            self.append(ref, "workflow", f"Workflow 调用 #{index + 1}", workflow)
        for index, step in enumerate(agent_result.get("execution_trace") or []):
            self.append(ref, "execution_trace", f"处置全流程 #{index + 1}", step)
        for key in (
            "primary_action_steps",
            "aggregated_action_steps",
            "aggregated_closure_steps",
            "effective_closure_step",
            "final_facts",
            "structured_judgment",
            "final_judgment_synthesis",
        ):
            if key in agent_result:
                self.append(ref, key, key, agent_result.get(key))
        if agent_result.get("final_response"):
            self.append(ref, "final_response", "最终响应", agent_result.get("final_response"))
        summary_keys = (
            "agent_name",
            "primary_agent",
            "orchestrated",
            "orchestration_strategy",
            "success",
            "approval_pending",
            "approval_request",
            "route",
        )
        summary = {key: agent_result.get(key) for key in summary_keys if key in agent_result}
        if summary:
            self.append(ref, "run_summary", "运行摘要", summary)

    def list_dates(self) -> list[dict[str, Any]]:
        self.cleanup()
        if not self.root.exists():
            return []
        dates: list[dict[str, Any]] = []
        for child in sorted(self.root.iterdir(), reverse=True):
            if not child.is_dir():
                continue
            count = len(list(child.glob("*.jsonl")))
            if count:
                dates.append({"date": child.name, "count": count})
        return dates

    def list_alerts(self, log_date: str) -> list[dict[str, Any]]:
        date_dir = self.root / _safe_name(log_date)
        if not date_dir.is_dir():
            return []
        alerts: list[dict[str, Any]] = []
        for file_path in sorted(date_dir.glob("*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True):
            events = self._read_events(file_path, limit=1)
            first = events[0] if events else {}
            metadata = first.get("metadata") if isinstance(first.get("metadata"), dict) else {}
            try:
                with file_path.open("r", encoding="utf-8") as handle:
                    event_count = sum(1 for _ in handle)
            except OSError:
                event_count = 0
            alerts.append(
                {
                    "date": log_date,
                    "log_id": file_path.stem,
                    "event_ids": metadata.get("event_ids", ""),
                    "task_id": metadata.get("task_id", ""),
                    "title": metadata.get("title") or file_path.stem,
                    "alert_name": metadata.get("alert_name", ""),
                    "alert_time": metadata.get("alert_time", ""),
                    "source_name": metadata.get("source_name", ""),
                    "event_count": event_count,
                    "updated_at": datetime.fromtimestamp(file_path.stat().st_mtime).isoformat(timespec="seconds"),
                }
            )
        return alerts

    def read_log(self, log_date: str, log_id: str, *, limit: int | None = 500, tail: bool = True) -> dict[str, Any]:
        path = self.root / _safe_name(log_date) / f"{_safe_name(log_id)}.jsonl"
        if not path.is_file():
            return {"date": log_date, "log_id": log_id, "events": []}
        normalized_limit = max(1, min(int(limit or 500), 5000)) if limit is not None else None
        events, total_events = self._read_events_with_count(path, limit=normalized_limit, tail=tail)
        first_events = self._read_events(path, limit=1)
        first = first_events[0] if first_events and isinstance(first_events[0], dict) else {}
        metadata = first.get("metadata", {}) if isinstance(first.get("metadata"), dict) else {}
        return {
            "date": log_date,
            "log_id": log_id,
            "metadata": metadata if isinstance(metadata, dict) else {},
            "events": events,
            "total_events": total_events,
            "returned_events": len(events),
            "truncated": total_events > len(events),
            "tail": tail,
        }

    def _resolve_ref(self, ref: RunLogRef | dict[str, Any] | None) -> RunLogRef | None:
        if isinstance(ref, RunLogRef):
            return ref
        if not isinstance(ref, dict):
            return None
        log_date = str(ref.get("date") or "").strip()
        log_id = str(ref.get("log_id") or "").strip()
        if not log_date or not log_id:
            return None
        return RunLogRef(date=log_date, log_id=log_id, path=self.root / _safe_name(log_date) / f"{_safe_name(log_id)}.jsonl")

    def _read_events(self, path: Path, limit: int | None = None) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if limit is not None and len(events) >= limit:
                        break
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        item = {"ts": "", "level": "error", "phase": "log_parse_error", "title": "日志行解析失败", "data": line}
                    if isinstance(item, dict):
                        events.append(item)
        except OSError:
            return []
        return events

    def _parse_event_line(self, line: str) -> dict[str, Any] | None:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            item = {"ts": "", "level": "error", "phase": "log_parse_error", "title": "日志行解析失败", "data": line}
        return item if isinstance(item, dict) else None

    def _read_events_with_count(self, path: Path, *, limit: int | None = None, tail: bool = True) -> tuple[list[dict[str, Any]], int]:
        total = 0
        if limit is not None and tail:
            events_tail: deque[dict[str, Any]] = deque(maxlen=limit)
            try:
                with path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        total += 1
                        item = self._parse_event_line(line)
                        if item is not None:
                            events_tail.append(item)
            except OSError:
                return [], 0
            return list(events_tail), total

        events: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    total += 1
                    if limit is not None and len(events) >= limit:
                        continue
                    item = self._parse_event_line(line)
                    if item is not None:
                        events.append(item)
        except OSError:
            return [], 0
        return events, total
