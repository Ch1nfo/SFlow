import sqlite3
import json
import threading
from datetime import datetime, timezone
from typing import Any, Iterable

from sentinelflow.alerts.dedup import AlertDedupStore
from sentinelflow.domain.models import AlertHandlingTask
from sentinelflow.services.audit_service import AuditService
from sentinelflow.services.sqlite_support import open_sqlite_connection, sqlite_transaction
from sentinelflow.services.triage_service import TriageService
from sentinelflow.config.runtime import CONFIG_DIR

DB_PATH = CONFIG_DIR / "sys_queue.db"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_external_poll_final_facts(
    existing_result: dict[str, Any],
    *,
    previous_status: str,
    external_summary: str,
) -> dict[str, Any]:
    existing_final_facts = existing_result.get("final_facts")
    if not isinstance(existing_final_facts, dict):
        existing_final_facts = {}
    existing_judgment = existing_final_facts.get("judgment")
    if not isinstance(existing_judgment, dict):
        existing_judgment = {}
    existing_closure = existing_final_facts.get("closure")
    if not isinstance(existing_closure, dict):
        existing_closure = {}
    existing_consistency = existing_final_facts.get("consistency")
    prior_issues: list[Any] = []
    if isinstance(existing_consistency, dict) and isinstance(existing_consistency.get("issues"), list):
        prior_issues = list(existing_consistency.get("issues") or [])

    disposition = (
        str(existing_judgment.get("disposition", "")).strip()
        or str(existing_result.get("disposition", "")).strip()
        or "handled_manually"
    )
    return {
        **existing_final_facts,
        "judgment": {
            **existing_judgment,
            "disposition": disposition,
            "source": "external_poll_completion",
            "confidence": str(existing_judgment.get("confidence", "medium")).strip() or "medium",
        },
        "closure": {
            **existing_closure,
            "attempted": bool(existing_closure.get("attempted")) or True,
            "success": True,
            "status": str(existing_closure.get("status", "")).strip(),
            "memo": str(existing_closure.get("memo", "")).strip() or "已被人工处置",
            "detail_msg": external_summary,
            "source_type": "external",
            "source_name": "refresh_poll",
        },
        "task_outcome": {
            "success": True,
            "status": "completed",
            "source": "refresh_poll",
            "previous_status": previous_status,
        },
        "consistency": {
            "consistent": True,
            "issues": [],
            "superseded_issues": prior_issues,
        },
    }


def _rewrite_execution_trace_for_external_completion(
    trace: list[dict[str, Any]],
    *,
    external_step: dict[str, Any],
    final_facts: dict[str, Any],
    previous_status: str,
) -> list[dict[str, Any]]:
    filtered = [
        dict(item)
        for item in trace
        if isinstance(item, dict)
        and str(item.get("phase", "")).strip() not in {"final_facts", "final_status", "completed_externally"}
    ]
    external_summary = str(external_step.get("summary", "")).strip()
    return [
        *filtered,
        dict(external_step),
        {
            "phase": "final_facts",
            "title": "最终事实收敛",
            "summary": external_summary or "告警已不在轮询列表中，按已被人工处置完成收口。",
            "success": True,
            "data": final_facts,
        },
        {
            "phase": "final_status",
            "title": "最终执行状态",
            "summary": "已被人工处置，任务已完成。",
            "success": True,
            "data": {
                "success": True,
                "status": "completed",
                "action": "refresh_poll",
                "previous_status": previous_status,
            },
        },
    ]


def _parse_task_datetime(value: str, default_tz) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    if "T" not in normalized and " " in normalized:
        normalized = normalized.replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_tz or timezone.utc)
    return parsed.astimezone(timezone.utc)

class AlertDispatchService:
    """Dispatches fresh alerts into queued SentinelFlow handling tasks (SQLite backed)."""

    def __init__(
        self,
        dedup: AlertDedupStore,
        triage_service: TriageService,
        audit_service: AuditService | None = None,
    ) -> None:
        self.dedup = dedup
        self.triage_service = triage_service
        self.audit_service = audit_service or AuditService()
        
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        with self._get_conn() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS alert_tasks (
                    task_id TEXT PRIMARY KEY,
                    event_ids TEXT,
                    workflow_name TEXT,
                    title TEXT,
                    description TEXT,
                    source_id TEXT,
                    source_name TEXT,
                    alert_time TEXT,
                    updated_at TEXT,
                    status TEXT,
                    retry_count INTEGER,
                    last_action TEXT,
                    last_result_success INTEGER,
                    last_result_error TEXT,
                    last_result_data TEXT,
                    payload TEXT
                )
            ''')
            self._ensure_schema(conn)

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(alert_tasks)").fetchall()}
        if "alert_time" not in columns:
            conn.execute("ALTER TABLE alert_tasks ADD COLUMN alert_time TEXT DEFAULT ''")
        if "source_id" not in columns:
            conn.execute("ALTER TABLE alert_tasks ADD COLUMN source_id TEXT DEFAULT 'default'")
            conn.execute("UPDATE alert_tasks SET source_id = 'default' WHERE source_id IS NULL OR source_id = ''")
        if "source_name" not in columns:
            conn.execute("ALTER TABLE alert_tasks ADD COLUMN source_name TEXT DEFAULT '默认告警源'")
            conn.execute("UPDATE alert_tasks SET source_name = '默认告警源' WHERE source_name IS NULL OR source_name = ''")
        if "updated_at" not in columns:
            conn.execute("ALTER TABLE alert_tasks ADD COLUMN updated_at TEXT DEFAULT ''")
            conn.execute("UPDATE alert_tasks SET updated_at = COALESCE(updated_at, '') WHERE updated_at = ''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alert_tasks_event_ids ON alert_tasks(event_ids)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alert_tasks_source_event ON alert_tasks(source_id, event_ids)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alert_tasks_source_status ON alert_tasks(source_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alert_tasks_status ON alert_tasks(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alert_tasks_event_status ON alert_tasks(event_ids, status)")

    def _get_conn(self) -> sqlite3.Connection:
        return open_sqlite_connection(DB_PATH)

    def _row_to_task(self, row) -> AlertHandlingTask:
        result_raw = row["last_result_data"] if "last_result_data" in row.keys() else None
        payload_raw = row["payload"] if "payload" in row.keys() else None
        return AlertHandlingTask(
            task_id=row["task_id"],
            event_ids=row["event_ids"],
            workflow_name=row["workflow_name"],
            title=row["title"],
            description=row["description"],
            source_id=row["source_id"] if "source_id" in row.keys() else "default",
            source_name=row["source_name"] if "source_name" in row.keys() else "默认告警源",
            alert_time=row["alert_time"] if "alert_time" in row.keys() else "",
            updated_at=row["updated_at"] if "updated_at" in row.keys() else "",
            status=row["status"],
            retry_count=row["retry_count"],
            last_action=row["last_action"],
            last_result_success=bool(row["last_result_success"]) if row["last_result_success"] is not None else None,
            last_result_error=row["last_result_error"],
            last_result_data=json.loads(result_raw) if result_raw else {},
            payload=json.loads(payload_raw) if payload_raw else {},
        )

    def _save_task(self, task: AlertHandlingTask) -> None:
        with self.lock, sqlite_transaction(DB_PATH, begin_mode="IMMEDIATE") as conn:
            conn.execute('''
                INSERT OR REPLACE INTO alert_tasks
                (task_id, event_ids, workflow_name, title, description, source_id, source_name, alert_time, updated_at, status, retry_count, last_action, last_result_success, last_result_error, last_result_data, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                task.task_id, task.event_ids, task.workflow_name, task.title, task.description,
                task.source_id, task.source_name,
                task.alert_time, task.updated_at or _now_iso(), task.status, task.retry_count, task.last_action,
                1 if task.last_result_success else (0 if task.last_result_success is False else None),
                task.last_result_error, json.dumps(task.last_result_data), json.dumps(task.payload)
            ))

    def _insert_task_if_event_absent(self, task: AlertHandlingTask) -> bool:
        with self.lock, sqlite_transaction(DB_PATH, begin_mode="IMMEDIATE") as conn:
            cursor = conn.execute(
                '''
                INSERT INTO alert_tasks
                (task_id, event_ids, workflow_name, title, description, source_id, source_name, alert_time, updated_at, status, retry_count, last_action, last_result_success, last_result_error, last_result_data, payload)
                SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                WHERE NOT EXISTS (
                    SELECT 1 FROM alert_tasks WHERE source_id = ? AND event_ids = ?
                )
                ''',
                (
                    task.task_id,
                    task.event_ids,
                    task.workflow_name,
                    task.title,
                    task.description,
                    task.source_id,
                    task.source_name,
                    task.alert_time,
                    task.updated_at or _now_iso(),
                    task.status,
                    task.retry_count,
                    task.last_action,
                    1 if task.last_result_success else (0 if task.last_result_success is False else None),
                    task.last_result_error,
                    json.dumps(task.last_result_data),
                    json.dumps(task.payload),
                    task.source_id,
                    task.event_ids,
                ),
            )
            return cursor.rowcount > 0

    def _update_task_columns(
        self,
        task_id: str,
        updates: dict[str, Any],
        *,
        expected_statuses: Iterable[str] | None = None,
    ) -> AlertHandlingTask | None:
        if not updates:
            return self.get_task(task_id)
        updates = {
            **updates,
            "updated_at": updates.get("updated_at") or _now_iso(),
        }

        assignments = ", ".join(f"{column} = ?" for column in updates)
        params: list[Any] = list(updates.values())
        query = f"UPDATE alert_tasks SET {assignments} WHERE task_id = ?"
        params.append(task_id)
        if expected_statuses:
            status_list = list(expected_statuses)
            placeholders = ", ".join("?" for _ in status_list)
            query += f" AND status IN ({placeholders})"
            params.extend(status_list)

        with self.lock, sqlite_transaction(DB_PATH, begin_mode="IMMEDIATE") as conn:
            cursor = conn.execute(query, tuple(params))
            if cursor.rowcount <= 0:
                return None
            row = conn.execute("SELECT * FROM alert_tasks WHERE task_id = ?", (task_id,)).fetchone()
            return self._row_to_task(row) if row else None

    def _refresh_existing_task(
        self,
        existing: AlertHandlingTask,
        alert: dict,
        workflow_selection: dict[str, Any] | None = None,
        *,
        reset_to_queued: bool = False,
    ) -> AlertHandlingTask:
        alert_name = str(alert.get("alert_name", "未知告警")).strip() or "未知告警"
        source_id = str(alert.get("alert_source_id", existing.source_id or "default")).strip() or "default"
        source_name = str(alert.get("alert_source_name", existing.source_name or "默认告警源")).strip() or "默认告警源"
        workflow_name = str(existing.workflow_name or "agent_react").strip() or "agent_react"
        payload = dict(existing.payload) if isinstance(existing.payload, dict) else {}
        alert["alert_source_id"] = source_id
        alert["alert_source_name"] = source_name
        payload["alert_data"] = alert
        if workflow_selection is not None:
            payload["workflow_selection"] = workflow_selection

        updates: dict[str, Any] = {
            "title": alert_name,
            "description": f"Handle alert {existing.event_ids} through workflow {workflow_name}.",
            "source_id": source_id,
            "source_name": source_name,
            "alert_time": str(alert.get("alert_time", "")).strip(),
            "payload": json.dumps(payload),
        }
        expected_statuses = ["queued"]
        if reset_to_queued:
            expected_statuses = ["failed"]
            updates.update(
                {
                    "status": "queued",
                    "last_action": "refresh_poll",
                    "last_result_success": None,
                    "last_result_error": None,
                    "last_result_data": json.dumps({}),
                }
            )
            self.dedup.mark_processing(f"{source_id}:{existing.event_ids}")
        updated_task = self._update_task_columns(existing.task_id, updates, expected_statuses=expected_statuses)
        if not updated_task:
            latest = self.get_task(existing.task_id)
            return latest or existing
        self.audit_service.record(
            "alert_task_updated",
            f"Updated alert task for {existing.event_ids} with latest payload.",
            {
                "eventIds": existing.event_ids,
                "taskId": updated_task.task_id,
                "workflow": workflow_name,
                "resetToQueued": reset_to_queued,
                "status": updated_task.status,
            },
        )
        return updated_task

    def _list_missing_open_polled_tasks(self, active_event_ids_for_source: set[str], source_id: str = "default") -> list[AlertHandlingTask]:
        return [task for task in self.list_open_polled_tasks(source_id) if task.event_ids not in active_event_ids_for_source]

    def _complete_missing_polled_tasks(self, active_event_ids_for_source: set[str], source_id: str = "default") -> list[AlertHandlingTask]:
        completed: list[AlertHandlingTask] = []
        for task in self._list_missing_open_polled_tasks(active_event_ids_for_source, source_id):
            previous_status = task.status
            existing_result = dict(task.last_result_data) if isinstance(task.last_result_data, dict) else {}
            existing_trace = existing_result.get("execution_trace", [])
            if not isinstance(existing_trace, list):
                existing_trace = []
            preserved_trace = [
                dict(item)
                for item in existing_trace
                if isinstance(item, dict)
            ]
            if not preserved_trace:
                alert_data = (
                    task.payload.get("alert_data", {})
                    if isinstance(task.payload, dict) and isinstance(task.payload.get("alert_data"), dict)
                    else {}
                )
                preserved_trace = [
                    {
                        "phase": "alert_received",
                        "title": "接收告警",
                        "summary": "已接收任务告警上下文。",
                        "success": True,
                        "data": {
                            "eventIds": task.event_ids,
                            "alert_name": str(alert_data.get("alert_name", task.title)).strip(),
                            "sip": alert_data.get("sip", ""),
                            "dip": alert_data.get("dip", ""),
                            "alert_time": alert_data.get("alert_time", task.alert_time),
                        },
                    }
                ]
            external_summary = f"本次轮询未再发现该 {previous_status} 告警，按已被人工处置完成收口。"
            external_step = {
                "phase": "completed_externally",
                "title": "外部收口",
                "summary": external_summary,
                "success": True,
                "data": {
                    "success": True,
                    "status": "completed",
                    "previous_status": previous_status,
                    "action": "refresh_poll",
                },
            }
            updated_final_facts = _build_external_poll_final_facts(
                existing_result,
                previous_status=previous_status,
                external_summary=external_summary,
            )
            updated_result = {
                **existing_result,
                "summary": str(existing_result.get("summary") or "已被人工处置").strip(),
                "reason": str(existing_result.get("reason") or external_summary).strip(),
                "disposition": str(
                    existing_result.get("disposition")
                    or updated_final_facts.get("judgment", {}).get("disposition", "handled_manually")
                ).strip(),
                "success": True,
                "final_facts": updated_final_facts,
                "execution_trace": _rewrite_execution_trace_for_external_completion(
                    preserved_trace,
                    external_step=external_step,
                    final_facts=updated_final_facts,
                    previous_status=previous_status,
                ),
            }
            updated_task = self._update_task_columns(
                task.task_id,
                {
                    "status": "completed",
                    "last_action": "refresh_poll",
                    "last_result_success": 1,
                    "last_result_error": None,
                    "last_result_data": json.dumps(updated_result),
                },
                expected_statuses=["queued", "failed"],
            )
            if not updated_task:
                continue
            self.dedup.mark_done(f"{task.source_id}:{task.event_ids}")
            self.audit_service.record(
                "alert_task_completed_externally",
                f"Marked {previous_status} alert {task.event_ids} as completed because it disappeared from the latest poll.",
                {"eventIds": task.event_ids, "taskId": task.task_id, "previousStatus": previous_status, "sourceId": task.source_id},
            )
            completed.append(updated_task)
        return completed

    async def dispatch(
        self,
        alerts: list[dict],
        *,
        allow_missing_completion: bool = True,
        source_id: str = "default",
        source_name: str = "默认告警源",
    ) -> tuple[list[AlertHandlingTask], int, int, list[AlertHandlingTask], list[str]]:
        queued: list[AlertHandlingTask] = []
        skipped = 0
        updated = 0
        errors: list[str] = []
        active_event_ids_for_source: set[str] = set()

        for alert in alerts:
            alert["alert_source_id"] = str(alert.get("alert_source_id", source_id)).strip() or source_id
            alert["alert_source_name"] = str(alert.get("alert_source_name", source_name)).strip() or source_name
            event_id = str(alert.get("eventIds", "")).strip()
            if not event_id:
                errors.append("Skipping alert with empty eventIds.")
                continue
            active_event_ids_for_source.add(event_id)
            effective_source_id = str(alert.get("alert_source_id", source_id)).strip() or "default"
            existing = self.get_task_by_event_id(event_id, source_id=effective_source_id)
            if existing and existing.status == "queued":
                workflow_selection = existing.payload.get("workflow_selection", {}) if isinstance(existing.payload, dict) else {}
                self._refresh_existing_task(existing, alert, workflow_selection if isinstance(workflow_selection, dict) else {})
                updated += 1
                continue
            if existing and existing.status == "failed":
                workflow_selection = existing.payload.get("workflow_selection", {}) if isinstance(existing.payload, dict) else {}
                self._refresh_existing_task(
                    existing,
                    alert,
                    workflow_selection if isinstance(workflow_selection, dict) else {},
                    reset_to_queued=False,
                )
                updated += 1
                continue
            if existing and existing.status == "running":
                skipped += 1
                self.audit_service.record(
                    "alert_dispatch_skipped_running",
                    f"Skipped duplicate alert {event_id} because the original task is still running.",
                    {"eventIds": event_id, "taskId": existing.task_id, "sourceId": effective_source_id},
                )
                continue
            if existing and existing.status == "awaiting_approval":
                skipped += 1
                self.audit_service.record(
                    "alert_dispatch_skipped_awaiting_approval",
                    f"Skipped duplicate alert {event_id} because the original task is awaiting approval.",
                    {"eventIds": event_id, "taskId": existing.task_id, "sourceId": effective_source_id},
                )
                continue
            if existing and existing.status in {"succeeded", "completed"}:
                skipped += 1
                self.audit_service.record(
                    "alert_dispatch_skipped_finished",
                    f"Skipped duplicate alert {event_id} because the original task has already been finalized.",
                    {"eventIds": event_id, "taskId": existing.task_id, "status": existing.status, "sourceId": effective_source_id},
                )
                continue
            dedup_key = f"{effective_source_id}:{event_id}"
            if not self.dedup.mark_processing(dedup_key):
                skipped += 1
                self.audit_service.record(
                    "alert_dispatch_skipped",
                    f"Skipped duplicate or concurrently processing alert {event_id}.",
                    {"eventIds": event_id, "sourceId": effective_source_id},
                )
                continue

            try:
                task = await self.triage_service.build_task(alert)
                inserted = self._insert_task_if_event_absent(task)
                if not inserted:
                    self.dedup.forget(dedup_key)
                    skipped += 1
                    self.audit_service.record(
                        "alert_dispatch_skipped_race",
                        f"Skipped duplicate alert {event_id} because another task was inserted concurrently.",
                        {"eventIds": event_id, "taskId": task.task_id, "sourceId": effective_source_id},
                    )
                    continue
                queued.append(task)
                self.audit_service.record(
                    "alert_dispatched",
                    f"Dispatched alert {event_id} to workflow {task.workflow_name}.",
                    {"eventIds": event_id, "taskId": task.task_id, "workflow": task.workflow_name, "sourceId": task.source_id},
                )
            except Exception as exc:
                self.dedup.mark_failed(dedup_key)
                errors.append(f"Failed to dispatch alert {event_id}: {exc}")
                self.audit_service.record(
                    "alert_dispatch_failed",
                    f"Failed to dispatch alert {event_id}.",
                    {"eventIds": event_id, "error": str(exc), "sourceId": effective_source_id},
                )

        completed: list[AlertHandlingTask] = []
        if allow_missing_completion:
            completed = self._complete_missing_polled_tasks(active_event_ids_for_source, source_id)
        else:
            missing_candidates = self._list_missing_open_polled_tasks(active_event_ids_for_source, source_id)
            if missing_candidates:
                self.audit_service.record(
                    "alert_missing_completion_skipped",
                    "Skipped closing missing queued/failed alerts because the latest poll could not be confirmed as a complete snapshot.",
                    {
                        "count": len(missing_candidates),
                        "eventIds": [task.event_ids for task in missing_candidates],
                        "sourceId": source_id,
                    },
                )
        return queued, skipped, updated, completed, errors

    def list_queued_tasks(self, source_id: str | None = None) -> list[AlertHandlingTask]:
        return self.list_tasks(source_id=source_id)

    def list_open_polled_tasks(self, source_id: str | None = None) -> list[AlertHandlingTask]:
        with self.lock, self._get_conn() as conn:
            if source_id:
                rows = conn.execute(
                    "SELECT * FROM alert_tasks WHERE source_id = ? AND status IN ('queued', 'failed')",
                    (source_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM alert_tasks WHERE status IN ('queued', 'failed')"
                ).fetchall()
            return [self._row_to_task(row) for row in rows]

    def list_failed_retry_candidates(self, retry_interval_seconds: int, max_retry_count: int = 3, source_id: str | None = None) -> list[AlertHandlingTask]:
        if retry_interval_seconds <= 0:
            return []
        with self.lock, self._get_conn() as conn:
            if source_id:
                rows = conn.execute(
                    "SELECT * FROM alert_tasks WHERE source_id = ? AND status = 'failed' AND retry_count < ?",
                    (source_id, max_retry_count),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM alert_tasks WHERE status = 'failed' AND retry_count < ?",
                    (max_retry_count,),
                ).fetchall()
            candidates = [self._row_to_task(row) for row in rows]
        now = datetime.now(timezone.utc)
        eligible: list[AlertHandlingTask] = []
        for task in candidates:
            updated_at = str(task.updated_at or "").strip()
            if not updated_at:
                continue
            try:
                updated_dt = datetime.fromisoformat(updated_at)
            except ValueError:
                continue
            if updated_dt.tzinfo is None:
                updated_dt = updated_dt.replace(tzinfo=timezone.utc)
            if (now - updated_dt).total_seconds() >= retry_interval_seconds:
                eligible.append(task)
        return eligible

    def list_tasks(self, source_id: str | None = None) -> list[AlertHandlingTask]:
        with self.lock, self._get_conn() as conn:
            if source_id:
                rows = conn.execute("SELECT * FROM alert_tasks WHERE source_id = ?", (source_id,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM alert_tasks").fetchall()
            return [self._row_to_task(row) for row in rows]

    def clear_demo_tasks(self) -> int:
        removed_keys: list[str] = []
        tasks = self.list_tasks()
        
        for task in tasks:
            payload = task.payload if isinstance(task.payload, dict) else {}
            alert_data = payload.get("alert_data") if isinstance(payload.get("alert_data"), dict) else {}
            if str(alert_data.get("alert_source", "")).strip() == "sentinelflow_demo":
                removed_keys.append(f"{task.source_id}:{task.event_ids}")
                with self.lock, sqlite_transaction(DB_PATH) as conn:
                    conn.execute("DELETE FROM alert_tasks WHERE task_id = ?", (task.task_id,))
        
        for key in removed_keys:
            self.dedup.forget(key)
            
        return len(removed_keys)

    def delete_tasks_before(self, cutoff: datetime) -> int:
        default_tz = cutoff.tzinfo or timezone.utc
        cutoff_utc = cutoff.astimezone(timezone.utc) if cutoff.tzinfo else cutoff.replace(tzinfo=timezone.utc)
        removed_keys: list[str] = []
        removed_task_ids: list[str] = []
        for task in self.list_tasks():
            candidate_time = _parse_task_datetime(task.alert_time, default_tz) or _parse_task_datetime(task.updated_at, default_tz)
            if candidate_time is None or candidate_time >= cutoff_utc:
                continue
            removed_task_ids.append(task.task_id)
            removed_keys.append(f"{task.source_id}:{task.event_ids}")

        if not removed_task_ids:
            return 0

        with self.lock, sqlite_transaction(DB_PATH, begin_mode="IMMEDIATE") as conn:
            for task_id in removed_task_ids:
                conn.execute("DELETE FROM alert_tasks WHERE task_id = ?", (task_id,))

        for key in removed_keys:
            self.dedup.forget(key)

        self.audit_service.record(
            "weekly_alert_cleanup",
            f"Deleted {len(removed_task_ids)} alert tasks before {cutoff_utc.isoformat()}.",
            {"count": len(removed_task_ids), "cutoff": cutoff_utc.isoformat()},
        )
        return len(removed_task_ids)

    def get_task(self, task_id: str) -> AlertHandlingTask | None:
        with self.lock, self._get_conn() as conn:
            row = conn.execute("SELECT * FROM alert_tasks WHERE task_id = ?", (task_id,)).fetchone()
            if row:
                return self._row_to_task(row)
        return None

    def get_task_by_event_id(self, event_id: str, source_id: str | None = None) -> AlertHandlingTask | None:
        with self.lock, self._get_conn() as conn:
            if source_id:
                row = conn.execute(
                    "SELECT * FROM alert_tasks WHERE source_id = ? AND event_ids = ? ORDER BY rowid DESC LIMIT 1",
                    (source_id, event_id),
                ).fetchone()
            else:
                row = conn.execute("SELECT * FROM alert_tasks WHERE event_ids = ? ORDER BY rowid DESC LIMIT 1", (event_id,)).fetchone()
            if row:
                return self._row_to_task(row)
        return None

    def mark_task_running(self, task_id: str, action: str) -> AlertHandlingTask | None:
        task = self._update_task_columns(
            task_id,
            {
                "status": "running",
                "last_action": action,
                "last_result_error": None,
                "last_result_data": json.dumps({}),
            },
            expected_statuses=["queued"],
        )
        if not task:
            return None
        self.audit_service.record(
            "task_running",
            f"Task {task_id} entered running state.",
            {"taskId": task_id, "eventIds": task.event_ids, "action": action},
        )
        return task

    def mark_task_awaiting_approval(
        self,
        task_id: str,
        action: str,
        result_data: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> AlertHandlingTask | None:
        task = self._update_task_columns(
            task_id,
            {
                "status": "awaiting_approval",
                "last_action": action,
                "last_result_success": None,
                "last_result_error": error,
                "last_result_data": json.dumps(result_data or {}),
            },
            expected_statuses=["running"],
        )
        if not task:
            return None
        self.audit_service.record(
            "task_awaiting_approval",
            f"Task {task_id} is waiting for skill approval.",
            {"taskId": task_id, "eventIds": task.event_ids, "action": action},
        )
        return task

    def mark_task_running_from_approval(self, task_id: str, action: str) -> AlertHandlingTask | None:
        task = self._update_task_columns(
            task_id,
            {
                "status": "running",
                "last_action": action,
                "last_result_error": None,
            },
            expected_statuses=["awaiting_approval"],
        )
        if not task:
            return None
        self.audit_service.record(
            "task_resumed_from_approval",
            f"Task {task_id} resumed after approval decision.",
            {"taskId": task_id, "eventIds": task.event_ids, "action": action},
        )
        return task

    def prepare_retry(self, task_id: str) -> AlertHandlingTask | None:
        task = self.get_task(task_id)
        if not task:
            return None
        updated_task = self._update_task_columns(
            task_id,
            {
                "status": "queued",
                "retry_count": task.retry_count + 1,
                "last_result_error": None,
                "last_result_success": None,
                "last_result_data": json.dumps({}),
            },
            expected_statuses=["failed"],
        )
        if not updated_task:
            return None
        
        self.audit_service.record(
            "task_retry_prepared",
            f"Task {task_id} prepared for retry.",
            {"taskId": task_id, "eventIds": updated_task.event_ids, "retryCount": updated_task.retry_count},
        )
        return updated_task

    def finalize_task(
        self,
        task_id: str,
        action: str,
        success: bool,
        result_data: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> AlertHandlingTask | None:
        task = self.get_task(task_id)
        if not task:
            return None
        updated_task = self._update_task_columns(
            task_id,
            {
                "status": "succeeded" if success else "failed",
                "last_action": action,
                "last_result_success": 1 if success else 0,
                "last_result_error": error,
                "last_result_data": json.dumps(result_data or {}),
            },
            expected_statuses=["running"],
        )
        if not updated_task:
            current_task = self.get_task(task_id)
            self.audit_service.record(
                "task_finish_conflict",
                f"Task {task_id} could not be finalized because its status changed.",
                {
                    "taskId": task_id,
                    "expectedStatuses": ["running"],
                    "action": action,
                    "success": success,
                    "currentStatus": current_task.status if current_task else "",
                },
            )
            return None
        
        if success:
            self.dedup.mark_done(f"{updated_task.source_id}:{updated_task.event_ids}")
        else:
            self.dedup.mark_failed(f"{updated_task.source_id}:{updated_task.event_ids}")
            
        self.audit_service.record(
            "task_finished",
            f"Task {task_id} finished execution. Success: {success}",
            {
                "taskId": task_id,
                "eventIds": updated_task.event_ids,
                "success": success,
                "error": error,
                "action": action,
            },
        )
        return updated_task
