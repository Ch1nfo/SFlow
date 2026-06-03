import sqlite3
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import uuid4

from sentinelflow.alerts.dedup import AlertDedupStore
from sentinelflow.domain.models import AlertHandlingTask
from sentinelflow.services.audit_service import AuditService
from sentinelflow.services.sqlite_support import open_sqlite_connection, sqlite_connection, sqlite_transaction
from sentinelflow.services.triage_service import TriageService
from sentinelflow.config.runtime import CONFIG_DIR

DB_PATH = CONFIG_DIR / "sys_queue.db"
DEFAULT_STALE_RUNNING_TIMEOUT_SECONDS = 60 * 60
DEFAULT_STUCK_RUNNING_STEP_TIMEOUT_SECONDS = 10 * 60
BAN_IP_FIELDS = {"ban_ip", "banned_ip", "blocked_ip", "ip", "source_ip", "sip", "target", "target_ip", "name"}
DEFAULT_LIST_TASK_LIMIT = 120
SCHEMA_META_BANNED_IPS_BACKFILL_KEY = "alert_tasks_banned_ips_backfill_v1"
SCHEMA_META_INVALID_RESULT_JSON_AUDIT_KEY = "alert_tasks_invalid_result_json_audit_v1"
TASK_ROW_COLUMNS = """
    task_id, event_ids, workflow_name, title, description, source_id, source_name,
    alert_time, updated_at, sort_time, status, retry_count, last_action,
    last_result_success, last_result_error, disposition, outcome_status,
    banned_ips, result_summary
"""
logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _split_ip_values(value: Any) -> set[str]:
    if isinstance(value, list):
        values: set[str] = set()
        for item in value:
            values.update(_split_ip_values(item))
        return values
    text = str(value or "").strip()
    if not text:
        return set()
    return {item.strip() for item in re.split(r"[,，;；\s]+", text) if item.strip()}


def _collect_ip_values(payload: Any) -> set[str]:
    values: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key).strip() in BAN_IP_FIELDS:
                values.update(_split_ip_values(value))
            elif isinstance(value, (dict, list)):
                values.update(_collect_ip_values(value))
    elif isinstance(payload, list):
        for item in payload:
            values.update(_collect_ip_values(item))
    return values


def _looks_like_successful_ban_action(action_name: str, payload: dict[str, Any], *, completion_effect: str = "") -> bool:
    combined_text = " ".join(
        [
            str(action_name or "").strip().lower(),
            str(completion_effect or "").strip().lower(),
            str(payload.get("kind", "")).strip().lower(),
            str(payload.get("action", "")).strip().lower(),
            str(payload.get("result", "")).strip().lower(),
            str(payload.get("message", "")).strip().lower(),
        ]
    )
    if not any(token in combined_text for token in ("ban", "block", "containment", "封禁", "阻断", "遏制")):
        return False
    if bool(payload.get("error")):
        return False
    success_value = payload.get("success")
    if isinstance(success_value, bool):
        return success_value
    status_value = str(payload.get("status", "")).strip().lower()
    return status_value not in {"fail", "failed", "error"}


def _collect_banned_ips_from_result(result: dict[str, Any]) -> set[str]:
    banned_ips: set[str] = set()
    final_facts = result.get("final_facts")
    if isinstance(final_facts, dict):
        disposal = final_facts.get("disposal", {})
        if isinstance(disposal, dict):
            actions = disposal.get("actions", [])
            if isinstance(actions, list):
                for action in actions:
                    if not isinstance(action, dict) or not bool(action.get("success")):
                        continue
                    kind = str(action.get("kind", "")).strip()
                    completion_effect = str(action.get("completion_effect", "")).strip()
                    if kind != "ban_ip" and completion_effect not in {"containment", "closure"} and not _looks_like_successful_ban_action(str(action.get("skill_name", "")), action, completion_effect=completion_effect):
                        continue
                    banned_ips.update(_split_ip_values(action.get("target", "")))
    aggregated_action_steps = result.get("aggregated_action_steps")
    if isinstance(aggregated_action_steps, list):
        for step in aggregated_action_steps:
            if not isinstance(step, dict):
                continue
            payload = step.get("result", {})
            payload = payload if isinstance(payload, dict) else {}
            arguments = step.get("arguments", {})
            arguments = arguments if isinstance(arguments, dict) else {}
            combined_payload = {**arguments, **payload}
            if _looks_like_successful_ban_action(str(step.get("skill_name", "")), combined_payload, completion_effect=str(step.get("completion_effect", ""))):
                banned_ips.update(_collect_ip_values(combined_payload))
    actions = result.get("actions")
    if isinstance(actions, dict):
        for action_name, item in actions.items():
            if action_name == "tool_runs" and isinstance(item, list):
                for run in item:
                    if isinstance(run, dict):
                        banned_ips.update(_collect_banned_ips_from_tool_summaries(run.get("tool_calls_summary", []), step_success=bool(run.get("success", True))))
                continue
            if isinstance(item, dict) and _looks_like_successful_ban_action(str(action_name), item):
                banned_ips.update(_collect_ip_values(item))
    worker_results = result.get("worker_results")
    if isinstance(worker_results, list):
        for worker_result in worker_results:
            if isinstance(worker_result, dict):
                banned_ips.update(_collect_banned_ips_from_tool_summaries(worker_result.get("tool_calls_summary", []), step_success=bool(worker_result.get("success", True))))
    workflow_runs = result.get("workflow_runs")
    if isinstance(workflow_runs, list):
        for workflow_run in workflow_runs:
            if isinstance(workflow_run, dict):
                banned_ips.update(_collect_banned_ips_from_result(workflow_run))
    return banned_ips


def _collect_banned_ips_from_tool_summaries(tool_summaries: Any, *, step_success: bool = True) -> set[str]:
    banned_ips: set[str] = set()
    if not step_success or not isinstance(tool_summaries, list):
        return banned_ips
    for item in tool_summaries:
        if not isinstance(item, dict):
            continue
        args = item.get("args", {})
        args = args if isinstance(args, dict) else {}
        arguments = args.get("arguments", {})
        arguments = arguments if isinstance(arguments, dict) else {}
        payload = {
            **arguments,
            **(item.get("key_facts", {}) if isinstance(item.get("key_facts"), dict) else {}),
        }
        skill_name = str(args.get("skill_name") or item.get("skill_name") or item.get("name") or "").strip()
        if _looks_like_successful_ban_action(skill_name, payload):
            banned_ips.update(_collect_ip_values(payload))
    return banned_ips


def _stale_running_timeout_seconds() -> int:
    raw = str(os.getenv("SENTINELFLOW_STALE_RUNNING_TIMEOUT_SECONDS", "")).strip()
    if not raw:
        return DEFAULT_STALE_RUNNING_TIMEOUT_SECONDS
    try:
        return max(60, int(raw))
    except ValueError:
        return DEFAULT_STALE_RUNNING_TIMEOUT_SECONDS


def _stuck_running_step_timeout_seconds() -> int:
    raw = str(os.getenv("SENTINELFLOW_STUCK_RUNNING_STEP_TIMEOUT_SECONDS", "")).strip()
    if not raw:
        return DEFAULT_STUCK_RUNNING_STEP_TIMEOUT_SECONDS
    try:
        return max(60, int(raw))
    except ValueError:
        return DEFAULT_STUCK_RUNNING_STEP_TIMEOUT_SECONDS


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


def _resolve_disposition_from_result(result: dict[str, Any]) -> str:
    final_facts = result.get("final_facts")
    if isinstance(final_facts, dict):
        judgment = final_facts.get("judgment", {})
        if isinstance(judgment, dict):
            value = str(judgment.get("disposition", "")).strip()
            if value:
                return value
    return str(result.get("disposition", "")).strip() or "unknown"


def _resolve_outcome_status_from_result(result: dict[str, Any], status: str = "") -> str:
    final_facts = result.get("final_facts")
    if isinstance(final_facts, dict):
        outcome = final_facts.get("task_outcome", {})
        if isinstance(outcome, dict):
            value = str(outcome.get("status", "")).strip()
            if value:
                return value
    return str(status or "").strip()


def _derive_task_storage_fields(
    *,
    alert_time: str = "",
    updated_at: str = "",
    status: str = "",
    last_result_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    effective_updated = str(updated_at or "").strip() or _now_iso()
    sort_time = str(alert_time or "").strip() or effective_updated
    result = last_result_data if isinstance(last_result_data, dict) else {}
    disposition = _resolve_disposition_from_result(result)
    outcome_status = _resolve_outcome_status_from_result(result, status)
    banned = sorted(_collect_banned_ips_from_result(result))
    summary_text = str(result.get("summary") or result.get("reason") or "").strip()
    if len(summary_text) > 500:
        summary_text = summary_text[:497] + "..."
    return {
        "sort_time": sort_time,
        "disposition": disposition,
        "outcome_status": outcome_status,
        "banned_ips": json.dumps(banned, ensure_ascii=False),
        "result_summary": summary_text,
    }


def _decode_updates_result_data(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}
    return {}


def _enrich_updates_with_derived_fields(
    updates: dict[str, Any],
    current: AlertHandlingTask | None = None,
) -> dict[str, Any]:
    if not any(key in updates for key in ("alert_time", "updated_at", "status", "last_result_data")):
        return updates
    alert_time = str(updates.get("alert_time", current.alert_time if current else "") or "")
    updated_at = str(updates.get("updated_at", current.updated_at if current else "") or "")
    status = str(updates.get("status", current.status if current else "") or "")
    if "last_result_data" in updates:
        result = _decode_updates_result_data(updates["last_result_data"])
    elif current and isinstance(current.last_result_data, dict):
        result = current.last_result_data
    else:
        result = {}
    derived = _derive_task_storage_fields(
        alert_time=alert_time,
        updated_at=updated_at,
        status=status,
        last_result_data=result,
    )
    return {**updates, **derived}


def _log_query_duration(name: str, started: float, **fields: Any) -> None:
    duration_ms = round((time.monotonic() - started) * 1000, 2)
    logger.info(
        "dispatch_query %s duration_ms=%s %s",
        name,
        duration_ms,
        " ".join(f"{key}={value}" for key, value in fields.items()),
    )


SQL_DISPOSITION_EXPR = """
COALESCE(
    NULLIF(disposition, ''),
    CASE WHEN json_valid(last_result_data) = 1 THEN NULLIF(json_extract(last_result_data, '$.final_facts.judgment.disposition'), '') END,
    CASE WHEN json_valid(last_result_data) = 1 THEN NULLIF(json_extract(last_result_data, '$.disposition'), '') END,
    'unknown'
)
"""

SQL_OUTCOME_STATUS_EXPR = """
COALESCE(
    NULLIF(outcome_status, ''),
    CASE WHEN json_valid(last_result_data) = 1 THEN NULLIF(json_extract(last_result_data, '$.final_facts.task_outcome.status'), '') END,
    status
)
"""

DISPOSITION_BUCKETS = ("business_trigger", "false_positive", "true_attack", "unknown")


def _bucket_disposition(value: str) -> str:
    disposition = str(value or "unknown").strip() or "unknown"
    return disposition if disposition in DISPOSITION_BUCKETS else "unknown"


def _parse_banned_ips_column(raw: Any) -> list[str]:
    if not raw:
        return []
    try:
        values = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(values, list):
        return []
    return [str(item).strip() for item in values if str(item).strip()]


def _period_filter_sql(*, since: str, source_id: str | None = None) -> tuple[str, list[Any]]:
    clauses = ["(COALESCE(NULLIF(alert_time, ''), '') = '' OR alert_time >= ?)"]
    params: list[Any] = [since]
    if source_id:
        clauses.append("source_id = ?")
        params.append(source_id)
    return " AND ".join(clauses), params


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
        with sqlite_connection(DB_PATH) as conn:
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
                    payload TEXT,
                    running_heartbeat_at TEXT DEFAULT '',
                    running_step_key TEXT DEFAULT '',
                    running_step_title TEXT DEFAULT '',
                    running_step_started_at TEXT DEFAULT '',
                    running_step_updated_at TEXT DEFAULT '',
                    running_step_repeat_count INTEGER DEFAULT 0,
                    running_run_id TEXT DEFAULT ''
                )
            ''')
            self._ensure_schema(conn)
        self.recover_stale_running_tasks()

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        self._ensure_schema_meta_table(conn)
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
        if "sort_time" not in columns:
            conn.execute("ALTER TABLE alert_tasks ADD COLUMN sort_time TEXT NOT NULL DEFAULT ''")
        if "disposition" not in columns:
            conn.execute("ALTER TABLE alert_tasks ADD COLUMN disposition TEXT DEFAULT ''")
        if "outcome_status" not in columns:
            conn.execute("ALTER TABLE alert_tasks ADD COLUMN outcome_status TEXT DEFAULT ''")
        if "banned_ips" not in columns:
            conn.execute("ALTER TABLE alert_tasks ADD COLUMN banned_ips TEXT")
        if "result_summary" not in columns:
            conn.execute("ALTER TABLE alert_tasks ADD COLUMN result_summary TEXT DEFAULT ''")
        if "running_heartbeat_at" not in columns:
            conn.execute("ALTER TABLE alert_tasks ADD COLUMN running_heartbeat_at TEXT DEFAULT ''")
        if "running_step_key" not in columns:
            conn.execute("ALTER TABLE alert_tasks ADD COLUMN running_step_key TEXT DEFAULT ''")
        if "running_step_title" not in columns:
            conn.execute("ALTER TABLE alert_tasks ADD COLUMN running_step_title TEXT DEFAULT ''")
        if "running_step_started_at" not in columns:
            conn.execute("ALTER TABLE alert_tasks ADD COLUMN running_step_started_at TEXT DEFAULT ''")
        if "running_step_updated_at" not in columns:
            conn.execute("ALTER TABLE alert_tasks ADD COLUMN running_step_updated_at TEXT DEFAULT ''")
        if "running_step_repeat_count" not in columns:
            conn.execute("ALTER TABLE alert_tasks ADD COLUMN running_step_repeat_count INTEGER DEFAULT 0")
        if "running_run_id" not in columns:
            conn.execute("ALTER TABLE alert_tasks ADD COLUMN running_run_id TEXT DEFAULT ''")
        conn.execute("UPDATE alert_tasks SET sort_time = COALESCE(NULLIF(alert_time, ''), updated_at) WHERE sort_time IS NULL OR sort_time = ''")
        conn.execute(
            f"""
            UPDATE alert_tasks
            SET disposition = {SQL_DISPOSITION_EXPR}
            WHERE disposition IS NULL OR disposition = ''
            """
        )
        conn.execute(
            f"""
            UPDATE alert_tasks
            SET outcome_status = {SQL_OUTCOME_STATUS_EXPR}
            WHERE outcome_status IS NULL OR outcome_status = ''
            """
        )
        self._maybe_backfill_banned_ips(conn)
        self._maybe_audit_invalid_result_json(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alert_tasks_event_ids ON alert_tasks(event_ids)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alert_tasks_source_event ON alert_tasks(source_id, event_ids)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alert_tasks_source_status ON alert_tasks(source_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alert_tasks_status ON alert_tasks(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alert_tasks_event_status ON alert_tasks(event_ids, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alert_tasks_updated_at ON alert_tasks(updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alert_tasks_running_step ON alert_tasks(status, running_step_started_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alert_tasks_sort_time ON alert_tasks(sort_time DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alert_tasks_source_sort ON alert_tasks(source_id, sort_time DESC)")

    def _ensure_schema_meta_table(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )

    def _get_schema_meta(self, conn: sqlite3.Connection, key: str) -> str:
        row = conn.execute("SELECT value FROM schema_meta WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else ""

    def _set_schema_meta(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)", (key, value))

    def _is_banned_ips_backfill_complete(self, conn: sqlite3.Connection) -> bool:
        return self._get_schema_meta(conn, SCHEMA_META_BANNED_IPS_BACKFILL_KEY) == "completed"

    def _maybe_backfill_banned_ips(self, conn: sqlite3.Connection) -> None:
        if self._is_banned_ips_backfill_complete(conn):
            return
        rows = conn.execute(
            """
            SELECT task_id, last_result_data
            FROM alert_tasks
            WHERE banned_ips IS NULL OR banned_ips IN ('', '[]')
            """
        ).fetchall()
        updated = 0
        for row in rows:
            banned: list[str] = []
            raw = row["last_result_data"]
            if raw and str(raw).strip() not in {"", "{}"}:
                try:
                    result = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning(
                        "Skipping banned_ips backfill for task %s due to invalid JSON.",
                        row["task_id"],
                    )
                else:
                    if isinstance(result, dict):
                        banned = sorted(_collect_banned_ips_from_result(result))
            conn.execute(
                "UPDATE alert_tasks SET banned_ips = ? WHERE task_id = ?",
                (json.dumps(banned, ensure_ascii=False), row["task_id"]),
            )
            updated += 1
        conn.execute(
            """
            UPDATE alert_tasks
            SET banned_ips = '[]'
            WHERE banned_ips IS NULL OR banned_ips = ''
            """
        )
        self._set_schema_meta(conn, SCHEMA_META_BANNED_IPS_BACKFILL_KEY, "completed")
        logger.info("Completed banned_ips backfill for %s alert task(s).", updated)

    def _maybe_audit_invalid_result_json(self, conn: sqlite3.Connection) -> None:
        if self._get_schema_meta(conn, SCHEMA_META_INVALID_RESULT_JSON_AUDIT_KEY) == "completed":
            return
        invalid_count = 0
        sample_ids: list[str] = []
        for row in conn.execute(
            """
            SELECT task_id FROM alert_tasks
            WHERE last_result_data IS NOT NULL
              AND last_result_data NOT IN ('', '{}')
              AND json_valid(last_result_data) = 0
            """
        ):
            invalid_count += 1
            if len(sample_ids) < 5:
                sample_ids.append(str(row["task_id"]))
        if invalid_count:
            logger.warning(
                "alert_tasks contains invalid last_result_data JSON for %s task(s); sample task_ids=%s",
                invalid_count,
                sample_ids,
            )
        self._set_schema_meta(conn, SCHEMA_META_INVALID_RESULT_JSON_AUDIT_KEY, "completed")

    def _aggregate_banned_ips(self, conn: sqlite3.Connection, *, where_clause: str = "", params: tuple[Any, ...] = ()) -> list[str]:
        query = "SELECT banned_ips FROM alert_tasks"
        if where_clause:
            query += f" WHERE {where_clause}"
        banned_ips: set[str] = set()
        for row in conn.execute(query, params).fetchall():
            banned_ips.update(_parse_banned_ips_column(row["banned_ips"]))
        return sorted(banned_ips)

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
            last_result_data=self._decode_json_object(result_raw, context="last_result_data", task_id=row["task_id"]),
            payload=self._decode_json_object(payload_raw, context="payload", task_id=row["task_id"]),
            running_heartbeat_at=row["running_heartbeat_at"] if "running_heartbeat_at" in row.keys() else "",
            running_step_key=row["running_step_key"] if "running_step_key" in row.keys() else "",
            running_step_title=row["running_step_title"] if "running_step_title" in row.keys() else "",
            running_step_started_at=row["running_step_started_at"] if "running_step_started_at" in row.keys() else "",
            running_step_updated_at=row["running_step_updated_at"] if "running_step_updated_at" in row.keys() else "",
            running_step_repeat_count=int(row["running_step_repeat_count"] or 0) if "running_step_repeat_count" in row.keys() else 0,
            running_run_id=row["running_run_id"] if "running_run_id" in row.keys() else "",
        )

    def _decode_json_object(self, raw: str | None, *, context: str, task_id: str | None = None) -> dict[str, Any]:
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            self.audit_service.record(
                "alert_task_json_decode_failed",
                f"Failed to decode {context} JSON for alert task.",
                {"taskId": task_id, "context": context, "error": str(exc)},
            )
            return {}
        if not isinstance(value, dict):
            return {}
        return value

    def _save_task(self, task: AlertHandlingTask) -> None:
        updated_at = task.updated_at or _now_iso()
        derived = _derive_task_storage_fields(
            alert_time=task.alert_time,
            updated_at=updated_at,
            status=task.status,
            last_result_data=task.last_result_data if isinstance(task.last_result_data, dict) else {},
        )
        with self.lock, sqlite_transaction(DB_PATH, begin_mode="IMMEDIATE") as conn:
            conn.execute('''
                INSERT OR REPLACE INTO alert_tasks
                (task_id, event_ids, workflow_name, title, description, source_id, source_name, alert_time, updated_at, sort_time, status, retry_count, last_action, last_result_success, last_result_error, last_result_data, payload, disposition, outcome_status, banned_ips, result_summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                task.task_id, task.event_ids, task.workflow_name, task.title, task.description,
                task.source_id, task.source_name,
                task.alert_time, updated_at, derived["sort_time"], task.status, task.retry_count, task.last_action,
                1 if task.last_result_success else (0 if task.last_result_success is False else None),
                task.last_result_error, json.dumps(task.last_result_data), json.dumps(task.payload),
                derived["disposition"], derived["outcome_status"], derived["banned_ips"], derived["result_summary"],
            ))

    def _insert_task_if_event_absent(self, task: AlertHandlingTask) -> bool:
        updated_at = task.updated_at or _now_iso()
        derived = _derive_task_storage_fields(
            alert_time=task.alert_time,
            updated_at=updated_at,
            status=task.status,
            last_result_data=task.last_result_data if isinstance(task.last_result_data, dict) else {},
        )
        with self.lock, sqlite_transaction(DB_PATH, begin_mode="IMMEDIATE") as conn:
            cursor = conn.execute(
                '''
                INSERT INTO alert_tasks
                (task_id, event_ids, workflow_name, title, description, source_id, source_name, alert_time, updated_at, sort_time, status, retry_count, last_action, last_result_success, last_result_error, last_result_data, payload, disposition, outcome_status, banned_ips, result_summary)
                SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
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
                    updated_at,
                    derived["sort_time"],
                    task.status,
                    task.retry_count,
                    task.last_action,
                    1 if task.last_result_success else (0 if task.last_result_success is False else None),
                    task.last_result_error,
                    json.dumps(task.last_result_data),
                    json.dumps(task.payload),
                    derived["disposition"],
                    derived["outcome_status"],
                    derived["banned_ips"],
                    derived["result_summary"],
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
        expected_running_run_id: str | None = None,
    ) -> AlertHandlingTask | None:
        if not updates:
            return self.get_task(task_id)
        updates = {
            **updates,
            "updated_at": updates.get("updated_at") or _now_iso(),
        }

        with self.lock, sqlite_transaction(DB_PATH, begin_mode="IMMEDIATE") as conn:
            current_row = conn.execute("SELECT * FROM alert_tasks WHERE task_id = ?", (task_id,)).fetchone()
            current = self._row_to_task(current_row) if current_row else None
            updates = _enrich_updates_with_derived_fields(updates, current)

            assignments = ", ".join(f"{column} = ?" for column in updates)
            params: list[Any] = list(updates.values())
            query = f"UPDATE alert_tasks SET {assignments} WHERE task_id = ?"
            params.append(task_id)
            if expected_statuses:
                status_list = list(expected_statuses)
                placeholders = ", ".join("?" for _ in status_list)
                query += f" AND status IN ({placeholders})"
                params.extend(status_list)
            if expected_running_run_id is not None:
                query += " AND running_run_id = ?"
                params.append(str(expected_running_run_id or ""))

            cursor = conn.execute(query, tuple(params))
            if cursor.rowcount <= 0:
                return None
            row = conn.execute("SELECT * FROM alert_tasks WHERE task_id = ?", (task_id,)).fetchone()
            return self._row_to_task(row) if row else None

    def record_task_heartbeat(
        self,
        task_id: str,
        *,
        step_key: str,
        step_title: str = "",
    ) -> AlertHandlingTask | None:
        task_id = str(task_id or "").strip()
        normalized_step_key = str(step_key or "").strip()
        if not task_id or not normalized_step_key:
            return None
        now = _now_iso()
        with self.lock, sqlite_transaction(DB_PATH, begin_mode="IMMEDIATE") as conn:
            row = conn.execute("SELECT * FROM alert_tasks WHERE task_id = ?", (task_id,)).fetchone()
            current = self._row_to_task(row) if row else None
            if current is None or current.status != "running":
                return None
            same_step = str(current.running_step_key or "").strip() == normalized_step_key
            running_step_started_at = current.running_step_started_at if same_step and current.running_step_started_at else now
            repeat_count = (current.running_step_repeat_count + 1) if same_step else 1
            updates = {
                "updated_at": now,
                "running_heartbeat_at": now,
                "running_step_key": normalized_step_key,
                "running_step_title": str(step_title or normalized_step_key).strip()[:500],
                "running_step_started_at": running_step_started_at,
                "running_step_updated_at": now,
                "running_step_repeat_count": repeat_count,
            }
            updates = _enrich_updates_with_derived_fields(updates, current)
            assignments = ", ".join(f"{column} = ?" for column in updates)
            params = list(updates.values()) + [task_id]
            cursor = conn.execute(
                f"UPDATE alert_tasks SET {assignments} WHERE task_id = ? AND status = 'running'",
                tuple(params),
            )
            if cursor.rowcount <= 0:
                return None
            updated_row = conn.execute("SELECT * FROM alert_tasks WHERE task_id = ?", (task_id,)).fetchone()
            return self._row_to_task(updated_row) if updated_row else None

    def _refresh_existing_task(
        self,
        existing: AlertHandlingTask,
        alert: dict,
        workflow_selection: dict[str, Any] | None = None,
        *,
        reset_to_queued: bool = False,
    ) -> AlertHandlingTask | None:
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
        expected_statuses = ["queued", "failed"]
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
            self.audit_service.record(
                "alert_task_refresh_conflict",
                f"Skipped refreshing alert task for {existing.event_ids} because its status changed.",
                {"eventIds": existing.event_ids, "taskId": existing.task_id, "status": existing.status},
            )
            return None
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

    def recover_stale_running_tasks(
        self,
        *,
        source_id: str | None = None,
        timeout_seconds: int | None = None,
        stuck_step_timeout_seconds: int | None = None,
    ) -> list[AlertHandlingTask]:
        timeout = timeout_seconds if timeout_seconds is not None else _stale_running_timeout_seconds()
        stuck_timeout = stuck_step_timeout_seconds if stuck_step_timeout_seconds is not None else _stuck_running_step_timeout_seconds()
        if timeout <= 0 and stuck_timeout <= 0:
            return []
        now = datetime.now(timezone.utc)
        with self.lock, sqlite_connection(DB_PATH) as conn:
            if source_id:
                rows = conn.execute(
                    "SELECT * FROM alert_tasks WHERE source_id = ? AND status = 'running'",
                    (source_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM alert_tasks WHERE status = 'running'").fetchall()
            candidates = [self._row_to_task(row) for row in rows]

        recovered: list[AlertHandlingTask] = []
        for task in candidates:
            step_updated_at = _parse_task_datetime(task.running_step_updated_at, timezone.utc)
            step_started_at = _parse_task_datetime(task.running_step_started_at, timezone.utc)
            step_key = str(task.running_step_key or "").strip()
            step_last_active_at = step_updated_at or step_started_at
            if stuck_timeout > 0 and step_key and step_last_active_at is not None and (now - step_last_active_at).total_seconds() >= stuck_timeout:
                step_title = str(task.running_step_title or step_key).strip()
                error = f"任务运行卡在同一步超过 {stuck_timeout} 秒未更新，已由系统回收为失败状态，可重新执行。当前步骤：{step_title}"
                existing_result = dict(task.last_result_data) if isinstance(task.last_result_data, dict) else {}
                result_data = {
                    **existing_result,
                    "success": False,
                    "error": error,
                    "reason": str(existing_result.get("reason") or error).strip(),
                    "recovered_stuck_running_step": True,
                    "stuck_step": {
                        "key": step_key,
                        "title": step_title,
                        "started_at": task.running_step_started_at,
                        "updated_at": task.running_step_updated_at,
                        "heartbeat_at": task.running_heartbeat_at,
                        "repeat_count": task.running_step_repeat_count,
                        "timeout_seconds": stuck_timeout,
                    },
                }
                updated_task = self._update_task_columns(
                    task.task_id,
                    {
                        "status": "failed",
                        "last_action": task.last_action or "stuck_running_step_recovery",
                        "last_result_success": 0,
                        "last_result_error": error,
                        "last_result_data": json.dumps(result_data),
                        "running_heartbeat_at": "",
                        "running_step_key": "",
                        "running_step_title": "",
                        "running_step_started_at": "",
                        "running_step_updated_at": "",
                        "running_step_repeat_count": 0,
                        "running_run_id": "",
                    },
                    expected_statuses=["running"],
                )
                if not updated_task:
                    continue
                self.dedup.mark_failed(f"{updated_task.source_id}:{updated_task.event_ids}")
                self.audit_service.record(
                    "alert_task_stuck_running_step_recovered",
                    f"Recovered stuck running task {updated_task.event_ids} as failed.",
                    {
                        "eventIds": updated_task.event_ids,
                        "taskId": updated_task.task_id,
                        "sourceId": updated_task.source_id,
                        "stepKey": step_key,
                        "stepTitle": step_title,
                        "timeoutSeconds": stuck_timeout,
                    },
                )
                recovered.append(updated_task)
                continue
            updated_at = _parse_task_datetime(task.updated_at, timezone.utc)
            if updated_at is None or (now - updated_at).total_seconds() < timeout:
                continue
            error = f"任务运行超过 {timeout} 秒未更新，已由系统回收为失败状态，可重新执行。"
            existing_result = dict(task.last_result_data) if isinstance(task.last_result_data, dict) else {}
            result_data = {
                **existing_result,
                "success": False,
                "error": error,
                "reason": str(existing_result.get("reason") or error).strip(),
                "recovered_stale_running": True,
            }
            updated_task = self._update_task_columns(
                task.task_id,
                {
                    "status": "failed",
                    "last_action": task.last_action or "stale_running_recovery",
                    "last_result_success": 0,
                    "last_result_error": error,
                    "last_result_data": json.dumps(result_data),
                    "running_heartbeat_at": "",
                    "running_step_key": "",
                    "running_step_title": "",
                    "running_step_started_at": "",
                    "running_step_updated_at": "",
                    "running_step_repeat_count": 0,
                    "running_run_id": "",
                },
                expected_statuses=["running"],
            )
            if not updated_task:
                continue
            self.dedup.mark_failed(f"{updated_task.source_id}:{updated_task.event_ids}")
            self.audit_service.record(
                "alert_task_stale_running_recovered",
                f"Recovered stale running task {updated_task.event_ids} as failed.",
                {
                    "eventIds": updated_task.event_ids,
                    "taskId": updated_task.task_id,
                    "sourceId": updated_task.source_id,
                    "timeoutSeconds": timeout,
                },
            )
            recovered.append(updated_task)
        return recovered

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
        self.recover_stale_running_tasks(source_id=source_id)

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
                if self._refresh_existing_task(existing, alert, workflow_selection if isinstance(workflow_selection, dict) else {}):
                    updated += 1
                else:
                    skipped += 1
                continue
            if existing and existing.status == "failed":
                workflow_selection = existing.payload.get("workflow_selection", {}) if isinstance(existing.payload, dict) else {}
                if self._refresh_existing_task(
                    existing,
                    alert,
                    workflow_selection if isinstance(workflow_selection, dict) else {},
                    reset_to_queued=False,
                ):
                    updated += 1
                else:
                    skipped += 1
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

    def list_queued_tasks(self, source_id: str | None = None, *, limit: int | None = None) -> list[AlertHandlingTask]:
        normalized_limit = max(1, int(limit)) if limit is not None else None
        with self.lock, sqlite_connection(DB_PATH) as conn:
            params: list[Any] = []
            query = "SELECT * FROM alert_tasks WHERE status = 'queued'"
            if source_id:
                query += " AND source_id = ?"
                params.append(source_id)
            query += " ORDER BY COALESCE(NULLIF(alert_time, ''), updated_at) ASC, updated_at ASC"
            if normalized_limit is not None:
                query += " LIMIT ?"
                params.append(normalized_limit)
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_task(row) for row in rows]

    def list_open_polled_tasks(self, source_id: str | None = None) -> list[AlertHandlingTask]:
        with self.lock, sqlite_connection(DB_PATH) as conn:
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
        with self.lock, sqlite_connection(DB_PATH) as conn:
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
        with self.lock, sqlite_connection(DB_PATH) as conn:
            if source_id:
                rows = conn.execute("SELECT * FROM alert_tasks WHERE source_id = ?", (source_id,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM alert_tasks").fetchall()
            return [self._row_to_task(row) for row in rows]

    def list_task_rows(
        self,
        source_id: str | None = None,
        *,
        limit: int = DEFAULT_LIST_TASK_LIMIT,
        offset: int = 0,
        since: str | None = None,
        cursor_sort_time: str | None = None,
        cursor_task_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int, dict[str, str] | None]:
        """Return a page of compact task summaries.

        Ordering is ``sort_time DESC, task_id DESC`` so that keyset pagination is
        stable. When ``cursor_sort_time`` is provided we page via keyset (cheap on a
        large DB); otherwise we fall back to ``LIMIT/OFFSET``. ``since`` filters on the
        indexable ``sort_time`` column so per-view (today/week) loading stays fast.
        Returns ``(summaries, total, next_cursor)`` where ``next_cursor`` is ``None``
        when the page is the last one.
        """
        started = time.monotonic()
        normalized_limit = max(1, min(int(limit or DEFAULT_LIST_TASK_LIMIT), 1000))
        normalized_offset = max(0, int(offset or 0))
        since_value = str(since or "").strip()
        cursor_time = str(cursor_sort_time or "").strip()
        cursor_id = str(cursor_task_id or "").strip()
        use_cursor = bool(cursor_time)

        filter_clauses: list[str] = []
        filter_params: list[Any] = []
        if source_id:
            filter_clauses.append("source_id = ?")
            filter_params.append(source_id)
        if since_value:
            # Reuse the same alert_time window semantics as period_aggregates so the
            # list and the period summary agree; tasks without alert_time still show.
            filter_clauses.append("(COALESCE(NULLIF(alert_time, ''), '') = '' OR alert_time >= ?)")
            filter_params.append(since_value)

        count_sql = "SELECT COUNT(*) FROM alert_tasks"
        if filter_clauses:
            count_sql += " WHERE " + " AND ".join(filter_clauses)

        page_clauses = list(filter_clauses)
        page_params = list(filter_params)
        if use_cursor:
            page_clauses.append("(sort_time < ? OR (sort_time = ? AND task_id < ?))")
            page_params.extend([cursor_time, cursor_time, cursor_id])
        select_sql = f"SELECT {TASK_ROW_COLUMNS} FROM alert_tasks"
        if page_clauses:
            select_sql += " WHERE " + " AND ".join(page_clauses)
        select_sql += " ORDER BY sort_time DESC, task_id DESC LIMIT ?"
        if use_cursor:
            page_params.append(normalized_limit)
        else:
            select_sql += " OFFSET ?"
            page_params.extend([normalized_limit, normalized_offset])

        with self.lock, sqlite_connection(DB_PATH) as conn:
            total = int(conn.execute(count_sql, tuple(filter_params)).fetchone()[0])
            rows = conn.execute(select_sql, tuple(page_params)).fetchall()
        summaries = [self._row_to_task_summary(row) for row in rows]
        next_cursor: dict[str, str] | None = None
        if summaries and len(summaries) == normalized_limit:
            last = summaries[-1]
            next_cursor = {
                "sort_time": str(last.get("sort_time") or ""),
                "task_id": str(last.get("task_id") or ""),
            }
        _log_query_duration(
            "list_task_rows",
            started,
            row_count=len(summaries),
            total=total,
            source_id=source_id or "all",
            limit=normalized_limit,
            offset=normalized_offset,
            since=since_value,
            cursor=cursor_time,
        )
        return summaries, total, next_cursor

    def list_task_summaries(
        self,
        source_id: str | None = None,
        *,
        limit: int = DEFAULT_LIST_TASK_LIMIT,
        offset: int = 0,
        since: str | None = None,
        cursor_sort_time: str | None = None,
        cursor_task_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int, dict[str, str] | None]:
        return self.list_task_rows(
            source_id,
            limit=limit,
            offset=offset,
            since=since,
            cursor_sort_time=cursor_sort_time,
            cursor_task_id=cursor_task_id,
        )

    def task_status_counts(self, source_id: str | None = None) -> dict[str, int]:
        query = "SELECT status, COUNT(*) AS count FROM alert_tasks"
        params: tuple[Any, ...] = ()
        if source_id:
            query += " WHERE source_id = ?"
            params = (source_id,)
        query += " GROUP BY status"
        with self.lock, sqlite_connection(DB_PATH) as conn:
            rows = conn.execute(query, params).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def dashboard_aggregates(self) -> dict[str, Any]:
        started = time.monotonic()
        disposition_buckets = {bucket: 0 for bucket in DISPOSITION_BUCKETS}
        with self.lock, sqlite_connection(DB_PATH) as conn:
            total_tasks = int(conn.execute("SELECT COUNT(*) FROM alert_tasks").fetchone()[0])
            status_counts = {
                str(row["status"]): int(row["count"])
                for row in conn.execute("SELECT status, COUNT(*) AS count FROM alert_tasks GROUP BY status").fetchall()
            }
            disposition_rows = conn.execute(
                f"""
                SELECT {SQL_DISPOSITION_EXPR} AS disposition, COUNT(*) AS count
                FROM alert_tasks
                GROUP BY disposition
                """
            ).fetchall()
            closed_success = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*) FROM alert_tasks
                    WHERE last_action = 'triage_close'
                      AND {SQL_OUTCOME_STATUS_EXPR} = 'succeeded'
                    """
                ).fetchone()[0]
            )
            disposed_success = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*) FROM alert_tasks
                    WHERE last_action = 'triage_dispose'
                      AND {SQL_OUTCOME_STATUS_EXPR} = 'succeeded'
                    """
                ).fetchone()[0]
            )
            manual_completed = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*) FROM alert_tasks
                    WHERE {SQL_OUTCOME_STATUS_EXPR} = 'completed'
                    """
                ).fetchone()[0]
            )
            recent_rows = conn.execute(
                f"""
                SELECT task_id, event_ids, title, status, last_action,
                       {SQL_DISPOSITION_EXPR} AS disposition
                FROM alert_tasks
                WHERE last_result_success = 1
                   OR COALESCE(NULLIF(result_summary, ''), '') != ''
                   OR COALESCE(NULLIF(disposition, ''), '') NOT IN ('', 'unknown')
                ORDER BY updated_at DESC
                LIMIT 8
                """
            ).fetchall()
            banned_ips = self._aggregate_banned_ips(conn)

        for row in disposition_rows:
            disposition_buckets[_bucket_disposition(str(row["disposition"]))] += int(row["count"])

        recent_results = [
            {
                "task_id": row["task_id"],
                "event_ids": row["event_ids"],
                "title": row["title"],
                "status": row["status"],
                "last_action": row["last_action"],
                "disposition": str(row["disposition"] or "unknown").strip() or "unknown",
            }
            for row in recent_rows
        ]
        _log_query_duration("dashboard_aggregates", started, row_count=total_tasks)
        return {
            "total_tasks": total_tasks,
            "status_counts": status_counts,
            "dispositions": disposition_buckets,
            "closed_success": closed_success,
            "disposed_success": disposed_success,
            "manual_completed": manual_completed,
            "banned_ips": banned_ips,
            "recent_results": recent_results,
        }

    def period_aggregates(
        self,
        *,
        since: str,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        since_value = str(since or "").strip()
        if not since_value:
            raise ValueError("since is required for period aggregates")
        where_clause, params = _period_filter_sql(since=since_value, source_id=source_id)
        disposition_buckets = {bucket: 0 for bucket in DISPOSITION_BUCKETS}
        with self.lock, sqlite_connection(DB_PATH) as conn:
            tasks_in_period = int(
                conn.execute(f"SELECT COUNT(*) FROM alert_tasks WHERE {where_clause}", tuple(params)).fetchone()[0]
            )
            disposition_rows = conn.execute(
                f"""
                SELECT {SQL_DISPOSITION_EXPR} AS disposition, COUNT(*) AS count
                FROM alert_tasks
                WHERE {where_clause}
                GROUP BY disposition
                """,
                tuple(params),
            ).fetchall()
            manual_completed = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*) FROM alert_tasks
                    WHERE {where_clause}
                      AND {SQL_OUTCOME_STATUS_EXPR} = 'completed'
                    """,
                    tuple(params),
                ).fetchone()[0]
            )
            closed_success = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*) FROM alert_tasks
                    WHERE {where_clause}
                      AND last_action = 'triage_close'
                      AND {SQL_OUTCOME_STATUS_EXPR} = 'succeeded'
                    """,
                    tuple(params),
                ).fetchone()[0]
            )
            disposed_success = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*) FROM alert_tasks
                    WHERE {where_clause}
                      AND last_action = 'triage_dispose'
                      AND {SQL_OUTCOME_STATUS_EXPR} = 'succeeded'
                    """,
                    tuple(params),
                ).fetchone()[0]
            )
            banned_ips = self._aggregate_banned_ips(conn, where_clause=where_clause, params=tuple(params))

        for row in disposition_rows:
            disposition_buckets[_bucket_disposition(str(row["disposition"]))] += int(row["count"])

        _log_query_duration(
            "period_aggregates",
            started,
            row_count=tasks_in_period,
            source_id=source_id or "all",
            since=since_value,
        )
        return {
            "since": since_value,
            "source_id": source_id or "all",
            "tasks_in_period": tasks_in_period,
            "judgment": disposition_buckets,
            "operations": {
                "closed_success": closed_success,
                "disposed_success": disposed_success,
                "manual_completed": manual_completed,
                "banned_ip_count": len(banned_ips),
                "banned_ips": banned_ips,
            },
        }

    def list_task_headlines(
        self,
        *,
        since: str | None = None,
        source_id: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        started = time.monotonic()
        normalized_limit = max(1, min(int(limit or 200), 500))
        clauses: list[str] = []
        params: list[Any] = []
        if source_id:
            clauses.append("source_id = ?")
            params.append(source_id)
        if since:
            clauses.append("updated_at > ?")
            params.append(since)
        where_clause = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.lock, sqlite_connection(DB_PATH) as conn:
            rows = conn.execute(
                f"""
                SELECT task_id, source_id, source_name, updated_at, title
                FROM alert_tasks
                {where_clause}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                tuple([*params, normalized_limit]),
            ).fetchall()
            status_query = "SELECT status, COUNT(*) AS count FROM alert_tasks"
            status_params: tuple[Any, ...] = ()
            if source_id:
                status_query += " WHERE source_id = ?"
                status_params = (source_id,)
            status_query += " GROUP BY status"
            status_counts = {
                str(row["status"]): int(row["count"])
                for row in conn.execute(status_query, status_params).fetchall()
            }
            total_tasks = int(
                conn.execute(
                    "SELECT COUNT(*) FROM alert_tasks" + (" WHERE source_id = ?" if source_id else ""),
                    (source_id,) if source_id else (),
                ).fetchone()[0]
            )
            latest_updated_at = conn.execute(
                "SELECT MAX(updated_at) FROM alert_tasks" + (" WHERE source_id = ?" if source_id else ""),
                (source_id,) if source_id else (),
            ).fetchone()[0]

        tasks = [
            {
                "task_id": row["task_id"],
                "source_id": row["source_id"] if "source_id" in row.keys() else "default",
                "source_name": row["source_name"] if "source_name" in row.keys() else "默认告警源",
                "updated_at": row["updated_at"] if "updated_at" in row.keys() else "",
                "title": row["title"],
            }
            for row in rows
        ]
        groups: dict[str, dict[str, Any]] = {}
        for task in tasks:
            source_key = str(task["source_id"] or "default")
            group = groups.get(source_key)
            if group is None:
                groups[source_key] = {
                    "source_id": source_key,
                    "source_name": task["source_name"],
                    "count": 1,
                    "task_ids": [task["task_id"]],
                }
            else:
                group["count"] += 1
                group["task_ids"].append(task["task_id"])
        _log_query_duration(
            "list_task_headlines",
            started,
            row_count=len(tasks),
            source_id=source_id or "all",
            since=since or "",
        )
        return {
            "tasks": tasks,
            "new_task_ids": [task["task_id"] for task in tasks],
            "groups_by_source": list(groups.values()),
            "status_counts": status_counts,
            "tasks_total": total_tasks,
            "latest_updated_at": str(latest_updated_at or ""),
        }

    def _compact_result_data(self, raw: str | None) -> dict[str, Any]:
        if not raw:
            return {}
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(result, dict):
            return {}
        compact: dict[str, Any] = {}
        banned_ips = sorted(_collect_banned_ips_from_result(result))
        if banned_ips:
            compact["banned_ips"] = banned_ips
            compact["banned_ip_count"] = len(banned_ips)
        preserved_keys = (
            "success",
            "approval_pending",
            "approval_request",
            "final_facts",
            "disposition",
            "summary",
            "workflow_selection",
            "effective_closure_step",
            "closure_step",
        )
        for key in preserved_keys:
            if key in result:
                compact[key] = result.get(key)
        workflow_runs = result.get("workflow_runs")
        if isinstance(workflow_runs, list) and workflow_runs:
            compact["workflow_runs"] = [
                {
                    key: item.get(key)
                    for key in ("workflow_id", "workflow_name", "summary", "reason", "success")
                    if isinstance(item, dict) and key in item
                }
                for item in workflow_runs[:3]
                if isinstance(item, dict)
            ]
            if len(workflow_runs) > 3:
                compact["workflow_runs_truncated"] = True
                compact["workflow_runs_total"] = len(workflow_runs)
        omitted_keys = sorted(key for key in result.keys() if key not in preserved_keys and key != "workflow_runs")
        if omitted_keys:
            compact["summary_truncated"] = True
            compact["omitted_result_keys"] = omitted_keys[:12]
        return compact

    def _compact_payload(self, raw: str | None) -> dict[str, Any]:
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, dict):
            return {}
        compact: dict[str, Any] = {}
        alert_data = payload.get("alert_data")
        if isinstance(alert_data, dict):
            compact_alert = {
                key: alert_data.get(key)
                for key in (
                    "eventIds",
                    "event_ids",
                    "alert_name",
                    "alert_time",
                    "alert_source",
                    "alert_source_id",
                    "alert_source_name",
                    "sip",
                    "dip",
                    "current_judgment",
                    "history_judgment",
                )
                if key in alert_data
            }
            compact["alert_data"] = compact_alert
        workflow_selection = payload.get("workflow_selection")
        if isinstance(workflow_selection, dict):
            compact["workflow_selection"] = workflow_selection
        return compact

    def _row_to_task_summary(self, row) -> dict[str, Any]:
        result_success = row["last_result_success"]
        disposition = str(row["disposition"] if "disposition" in row.keys() else "").strip() or "unknown"
        outcome_status = str(row["outcome_status"] if "outcome_status" in row.keys() else "").strip()
        banned_ips_raw = row["banned_ips"] if "banned_ips" in row.keys() else "[]"
        banned_ips: list[str] = []
        if banned_ips_raw:
            try:
                parsed = json.loads(banned_ips_raw)
                if isinstance(parsed, list):
                    banned_ips = [str(item).strip() for item in parsed if str(item).strip()]
            except json.JSONDecodeError:
                banned_ips = []
        result_summary = str(row["result_summary"] if "result_summary" in row.keys() else "").strip()
        compact_result: dict[str, Any] = {
            "disposition": disposition,
            "summary": result_summary,
            "final_facts": {
                "judgment": {"disposition": disposition},
            },
        }
        if outcome_status:
            compact_result["final_facts"]["task_outcome"] = {"status": outcome_status}
        if banned_ips:
            compact_result["banned_ips"] = banned_ips
            compact_result["banned_ip_count"] = len(banned_ips)
        return {
            "task_id": row["task_id"],
            "event_ids": row["event_ids"],
            "workflow_name": row["workflow_name"],
            "title": row["title"],
            "description": row["description"],
            "source_id": row["source_id"] if "source_id" in row.keys() else "default",
            "source_name": row["source_name"] if "source_name" in row.keys() else "默认告警源",
            "alert_time": row["alert_time"] if "alert_time" in row.keys() else "",
            "updated_at": row["updated_at"] if "updated_at" in row.keys() else "",
            "sort_time": row["sort_time"] if "sort_time" in row.keys() else "",
            "status": row["status"],
            "retry_count": row["retry_count"],
            "last_action": row["last_action"],
            "last_result_success": bool(result_success) if result_success is not None else None,
            "last_result_error": row["last_result_error"],
            "last_result_data": compact_result,
            "payload": {},
            "summary": True,
        }

    def clear_demo_tasks(self) -> int:
        removed_keys: list[str] = []
        removed_task_ids: list[str] = []
        # Read only the payload (needed to identify the demo source) plus the keys;
        # skip the large last_result_data blob entirely.
        with self.lock, sqlite_connection(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT task_id, source_id, event_ids, payload FROM alert_tasks"
            ).fetchall()
        for row in rows:
            payload = _decode_updates_result_data(row["payload"])
            alert_data = payload.get("alert_data") if isinstance(payload.get("alert_data"), dict) else {}
            if str(alert_data.get("alert_source", "")).strip() == "sentinelflow_demo":
                removed_task_ids.append(row["task_id"])
                removed_keys.append(f"{row['source_id']}:{row['event_ids']}")

        if not removed_task_ids:
            return 0

        with self.lock, sqlite_transaction(DB_PATH, begin_mode="IMMEDIATE") as conn:
            for start in range(0, len(removed_task_ids), 500):
                chunk = removed_task_ids[start : start + 500]
                placeholders = ",".join(["?"] * len(chunk))
                conn.execute(f"DELETE FROM alert_tasks WHERE task_id IN ({placeholders})", tuple(chunk))

        for key in removed_keys:
            self.dedup.forget(key)

        return len(removed_keys)

    def purge_orphan_dedup_entries(self) -> int:
        """Remove dedup keys that no longer match a stored alert task."""
        with self.lock, sqlite_transaction(DB_PATH, begin_mode="IMMEDIATE") as conn:
            deleted = conn.execute(
                """
                DELETE FROM alert_dedup
                WHERE event_id NOT IN (
                    SELECT source_id || ':' || event_ids FROM alert_tasks
                )
                """
            ).rowcount
        return max(int(deleted or 0), 0)

    def run_incremental_vacuum(self) -> None:
        with self.lock, sqlite_connection(DB_PATH) as conn:
            conn.execute("PRAGMA incremental_vacuum")

    def run_weekly_alert_storage_cleanup(self, cutoff: datetime) -> dict[str, int]:
        """Delete all alert tasks before cutoff (any status), clear dedup, vacuum pages."""
        default_tz = cutoff.tzinfo or timezone.utc
        cutoff_utc = cutoff.astimezone(timezone.utc) if cutoff.tzinfo else cutoff.replace(tzinfo=timezone.utc)
        removed_keys: list[str] = []
        removed_task_ids: list[str] = []
        # Only read light columns (never the large payload / last_result_data blobs)
        # so cleanup stays cheap on a multi-hundred-MB database.
        with self.lock, sqlite_connection(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT task_id, source_id, event_ids, alert_time, updated_at FROM alert_tasks"
            ).fetchall()
        for row in rows:
            candidate_time = (
                _parse_task_datetime(row["alert_time"], default_tz)
                or _parse_task_datetime(row["updated_at"], default_tz)
            )
            if candidate_time is None or candidate_time >= cutoff_utc:
                continue
            removed_task_ids.append(row["task_id"])
            removed_keys.append(f"{row['source_id']}:{row['event_ids']}")

        stats = {
            "tasks_deleted": 0,
            "dedup_cleared": 0,
            "dedup_orphans_cleared": 0,
        }
        if removed_task_ids:
            with self.lock, sqlite_transaction(DB_PATH, begin_mode="IMMEDIATE") as conn:
                for start in range(0, len(removed_task_ids), 500):
                    chunk = removed_task_ids[start : start + 500]
                    placeholders = ",".join(["?"] * len(chunk))
                    conn.execute(f"DELETE FROM alert_tasks WHERE task_id IN ({placeholders})", tuple(chunk))

            for key in removed_keys:
                self.dedup.forget(key)

            stats["tasks_deleted"] = len(removed_task_ids)
            stats["dedup_cleared"] = len(removed_keys)
            self.audit_service.record(
                "weekly_alert_cleanup",
                f"Deleted {len(removed_task_ids)} alert tasks before {cutoff_utc.isoformat()}.",
                {"count": len(removed_task_ids), "cutoff": cutoff_utc.isoformat()},
            )

        stats["dedup_orphans_cleared"] = self.purge_orphan_dedup_entries()
        return stats

    def delete_tasks_before(self, cutoff: datetime) -> int:
        """Backward-compatible wrapper; prefer run_weekly_alert_storage_cleanup."""
        return self.run_weekly_alert_storage_cleanup(cutoff)["tasks_deleted"]

    def get_task(self, task_id: str) -> AlertHandlingTask | None:
        with self.lock, sqlite_connection(DB_PATH) as conn:
            row = conn.execute("SELECT * FROM alert_tasks WHERE task_id = ?", (task_id,)).fetchone()
            if row:
                return self._row_to_task(row)
        return None

    def get_task_by_event_id(self, event_id: str, source_id: str | None = None) -> AlertHandlingTask | None:
        with self.lock, sqlite_connection(DB_PATH) as conn:
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
        now = _now_iso()
        run_id = uuid4().hex
        task = self._update_task_columns(
            task_id,
            {
                "status": "running",
                "last_action": action,
                "last_result_error": None,
                "last_result_data": json.dumps({}),
                "running_heartbeat_at": now,
                "running_step_key": "task_running",
                "running_step_title": "任务进入运行态",
                "running_step_started_at": now,
                "running_step_updated_at": now,
                "running_step_repeat_count": 1,
                "running_run_id": run_id,
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
        expected_running_run_id: str | None = None,
    ) -> AlertHandlingTask | None:
        task = self._update_task_columns(
            task_id,
            {
                "status": "awaiting_approval",
                "last_action": action,
                "last_result_success": None,
                "last_result_error": error,
                "last_result_data": json.dumps(result_data or {}),
                "running_heartbeat_at": "",
                "running_step_key": "",
                "running_step_title": "",
                "running_step_started_at": "",
                "running_step_updated_at": "",
                "running_step_repeat_count": 0,
                "running_run_id": "",
            },
            expected_statuses=["running"],
            expected_running_run_id=expected_running_run_id,
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
        task = self.get_task(task_id)
        cleared_result_data: dict[str, Any] = {}
        if task is not None and isinstance(task.last_result_data, dict):
            cleared_result_data = {
                key: value
                for key, value in task.last_result_data.items()
                if key not in {"approval_pending", "approval_request"}
            }
        now = _now_iso()
        run_id = uuid4().hex
        task = self._update_task_columns(
            task_id,
            {
                "status": "running",
                "last_action": action,
                "last_result_error": None,
                "last_result_data": json.dumps(cleared_result_data),
                "running_heartbeat_at": now,
                "running_step_key": "task_resumed_from_approval",
                "running_step_title": "审批后恢复运行",
                "running_step_started_at": now,
                "running_step_updated_at": now,
                "running_step_repeat_count": 1,
                "running_run_id": run_id,
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
                "running_heartbeat_at": "",
                "running_step_key": "",
                "running_step_title": "",
                "running_step_started_at": "",
                "running_step_updated_at": "",
                "running_step_repeat_count": 0,
                "running_run_id": "",
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

    def force_restart_task(self, task_id: str) -> AlertHandlingTask | None:
        task = self.get_task(task_id)
        if not task:
            return None
        updated_task = self._update_task_columns(
            task_id,
            {
                "status": "queued",
                "retry_count": task.retry_count + 1,
                "last_action": "force_restart",
                "last_result_error": None,
                "last_result_success": None,
                "last_result_data": json.dumps({}),
                "running_heartbeat_at": "",
                "running_step_key": "",
                "running_step_title": "",
                "running_step_started_at": "",
                "running_step_updated_at": "",
                "running_step_repeat_count": 0,
                "running_run_id": "",
            },
        )
        if not updated_task:
            return None
        self.dedup.mark_processing(f"{updated_task.source_id}:{updated_task.event_ids}")
        self.audit_service.record(
            "task_force_restart_prepared",
            f"Task {task_id} force restarted from current status.",
            {
                "taskId": task_id,
                "eventIds": updated_task.event_ids,
                "previousStatus": task.status,
                "retryCount": updated_task.retry_count,
            },
        )
        return updated_task

    def finalize_task(
        self,
        task_id: str,
        action: str,
        success: bool,
        result_data: dict[str, Any] | None = None,
        error: str | None = None,
        expected_running_run_id: str | None = None,
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
                "running_heartbeat_at": "",
                "running_step_key": "",
                "running_step_title": "",
                "running_step_started_at": "",
                "running_step_updated_at": "",
                "running_step_repeat_count": 0,
                "running_run_id": "",
            },
            expected_statuses=["running"],
            expected_running_run_id=expected_running_run_id,
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
