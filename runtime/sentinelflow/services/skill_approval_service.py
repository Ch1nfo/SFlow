from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sentinelflow.config.runtime import CONFIG_DIR
from sentinelflow.domain.models import SkillApprovalRecord
from sentinelflow.services.sqlite_support import open_sqlite_connection, sqlite_transaction

DB_PATH = CONFIG_DIR / "sys_queue.db"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


class SkillApprovalService:
    @staticmethod
    def build_skill_arguments_key(skill_name: str, arguments_fingerprint: str) -> str:
        return f"{str(skill_name or '').strip()}::{str(arguments_fingerprint or '').strip()}"

    def __init__(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        with self.lock, sqlite_transaction(DB_PATH, begin_mode="IMMEDIATE") as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS skill_approvals (
                    approval_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    scope_type TEXT NOT NULL,
                    scope_ref TEXT NOT NULL,
                    status TEXT NOT NULL,
                    skill_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    arguments_fingerprint TEXT NOT NULL,
                    approval_required INTEGER NOT NULL,
                    checkpoint_thread_id TEXT DEFAULT '',
                    checkpoint_ns TEXT DEFAULT '',
                    parent_checkpoint_thread_id TEXT DEFAULT '',
                    parent_checkpoint_ns TEXT DEFAULT '',
                    tool_call_id TEXT DEFAULT '',
                    parent_tool_call_id TEXT DEFAULT '',
                    message TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    decided_at TEXT DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS skill_checkpoints (
                    checkpoint_thread_id TEXT PRIMARY KEY,
                    checkpoint_ns TEXT NOT NULL,
                    checkpoint_kind TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    scope_type TEXT NOT NULL,
                    scope_ref TEXT NOT NULL,
                    agent_name TEXT DEFAULT '',
                    execution_entry TEXT DEFAULT '',
                    action_hint TEXT DEFAULT '',
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_skill_approvals_run_fp ON skill_approvals(run_id, skill_name, arguments_fingerprint)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_skill_approvals_status ON skill_approvals(status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_skill_checkpoints_run_id ON skill_checkpoints(run_id)"
            )

    def _get_conn(self):
        return open_sqlite_connection(DB_PATH)

    def _fetch_one(self, query: str, params: tuple[Any, ...] = ()) -> Any:
        with self.lock:
            conn = self._get_conn()
            try:
                return conn.execute(query, params).fetchone()
            finally:
                conn.close()

    def _fetch_all(self, query: str, params: tuple[Any, ...] = ()) -> list[Any]:
        with self.lock:
            conn = self._get_conn()
            try:
                return conn.execute(query, params).fetchall()
            finally:
                conn.close()

    def _row_to_approval(self, row) -> SkillApprovalRecord:
        arguments_json = row["arguments_json"] if "arguments_json" in row.keys() else "{}"
        try:
            arguments = json.loads(arguments_json) if arguments_json else {}
        except json.JSONDecodeError:
            arguments = {}
        return SkillApprovalRecord(
            approval_id=row["approval_id"],
            run_id=row["run_id"],
            scope_type=row["scope_type"],
            scope_ref=row["scope_ref"],
            status=row["status"],
            skill_name=row["skill_name"],
            arguments=arguments if isinstance(arguments, dict) else {},
            arguments_fingerprint=row["arguments_fingerprint"],
            approval_required=bool(row["approval_required"]),
            checkpoint_thread_id=row["checkpoint_thread_id"] if "checkpoint_thread_id" in row.keys() else "",
            checkpoint_ns=row["checkpoint_ns"] if "checkpoint_ns" in row.keys() else "",
            parent_checkpoint_thread_id=row["parent_checkpoint_thread_id"] if "parent_checkpoint_thread_id" in row.keys() else "",
            parent_checkpoint_ns=row["parent_checkpoint_ns"] if "parent_checkpoint_ns" in row.keys() else "",
            tool_call_id=row["tool_call_id"] if "tool_call_id" in row.keys() else "",
            parent_tool_call_id=row["parent_tool_call_id"] if "parent_tool_call_id" in row.keys() else "",
            message=row["message"] if "message" in row.keys() else "",
            created_at=row["created_at"] if "created_at" in row.keys() else "",
            decided_at=row["decided_at"] if "decided_at" in row.keys() else "",
        )

    def fingerprint_arguments(self, arguments: dict[str, Any] | None) -> str:
        normalized = _json_safe(arguments or {})
        serialized = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def normalize_arguments(self, arguments: dict[str, Any] | None) -> dict[str, Any]:
        normalized = _json_safe(arguments or {})
        return normalized if isinstance(normalized, dict) else {}

    def get_by_id(self, approval_id: str) -> SkillApprovalRecord | None:
        row = self._fetch_one(
            "SELECT * FROM skill_approvals WHERE approval_id = ?",
            (approval_id,),
        )
        return self._row_to_approval(row) if row else None

    def list_pending(self, *, scope_type: str | None = None, scope_ref: str | None = None) -> list[SkillApprovalRecord]:
        query = "SELECT * FROM skill_approvals WHERE status = 'pending'"
        params: list[Any] = []
        if scope_type:
            query += " AND scope_type = ?"
            params.append(scope_type)
        if scope_ref:
            query += " AND scope_ref = ?"
            params.append(scope_ref)
        query += " ORDER BY created_at ASC"
        rows = self._fetch_all(query, tuple(params))
        return [self._row_to_approval(row) for row in rows]

    def find_existing(self, run_id: str, skill_name: str, arguments_fingerprint: str) -> SkillApprovalRecord | None:
        row = self._fetch_one(
            """
            SELECT * FROM skill_approvals
            WHERE run_id = ? AND skill_name = ? AND arguments_fingerprint = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (run_id, skill_name, arguments_fingerprint),
        )
        return self._row_to_approval(row) if row else None

    def find_active_pending_for_run(self, run_id: str) -> SkillApprovalRecord | None:
        row = self._fetch_one(
            """
            SELECT * FROM skill_approvals
            WHERE run_id = ? AND status = 'pending'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (run_id,),
        )
        return self._row_to_approval(row) if row else None

    def get_latest_pending_for_scope(self, scope_type: str, scope_ref: str) -> SkillApprovalRecord | None:
        scope_type = str(scope_type or "").strip()
        scope_ref = str(scope_ref or "").strip()
        if not scope_type or not scope_ref:
            return None
        row = self._fetch_one(
            """
            SELECT * FROM skill_approvals
            WHERE status = 'pending' AND scope_type = ? AND scope_ref = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (scope_type, scope_ref),
        )
        return self._row_to_approval(row) if row else None

    def create_or_reuse_pending(
        self,
        *,
        run_id: str,
        scope_type: str,
        scope_ref: str,
        skill_name: str,
        arguments: dict[str, Any],
        approval_required: bool,
        checkpoint_thread_id: str,
        checkpoint_ns: str,
        tool_call_id: str = "",
        parent_checkpoint_thread_id: str = "",
        parent_checkpoint_ns: str = "",
        parent_tool_call_id: str = "",
        message: str = "",
    ) -> SkillApprovalRecord:
        normalized_arguments = self.normalize_arguments(arguments)
        fingerprint = self.fingerprint_arguments(normalized_arguments)
        record = SkillApprovalRecord(
            approval_id=uuid4().hex,
            run_id=run_id,
            scope_type=scope_type,
            scope_ref=scope_ref,
            status="pending",
            skill_name=skill_name,
            arguments=normalized_arguments,
            arguments_fingerprint=fingerprint,
            approval_required=approval_required,
            checkpoint_thread_id=checkpoint_thread_id,
            checkpoint_ns=checkpoint_ns,
            parent_checkpoint_thread_id=parent_checkpoint_thread_id,
            parent_checkpoint_ns=parent_checkpoint_ns,
            tool_call_id=tool_call_id,
            parent_tool_call_id=parent_tool_call_id,
            message=message,
            created_at=_now_iso(),
            decided_at="",
        )
        with self.lock, sqlite_transaction(DB_PATH, begin_mode="IMMEDIATE") as conn:
            if tool_call_id:
                existing_row = conn.execute(
                    """
                    SELECT * FROM skill_approvals
                    WHERE checkpoint_thread_id = ? AND tool_call_id = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (checkpoint_thread_id, tool_call_id),
                ).fetchone()
                existing = self._row_to_approval(existing_row) if existing_row else None
                if existing and existing.status == "pending":
                    return existing

            conn.execute(
                """
                INSERT INTO skill_approvals (
                    approval_id, run_id, scope_type, scope_ref, status, skill_name,
                    arguments_json, arguments_fingerprint, approval_required,
                    checkpoint_thread_id, checkpoint_ns,
                    parent_checkpoint_thread_id, parent_checkpoint_ns,
                    tool_call_id, parent_tool_call_id,
                    message, created_at, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.approval_id,
                    record.run_id,
                    record.scope_type,
                    record.scope_ref,
                    record.status,
                    record.skill_name,
                    json.dumps(record.arguments, ensure_ascii=False, sort_keys=True),
                    record.arguments_fingerprint,
                    1 if record.approval_required else 0,
                    record.checkpoint_thread_id,
                    record.checkpoint_ns,
                    record.parent_checkpoint_thread_id,
                    record.parent_checkpoint_ns,
                    record.tool_call_id,
                    record.parent_tool_call_id,
                    record.message,
                    record.created_at,
                    record.decided_at,
                ),
            )
        return record

    def set_decision(self, approval_id: str, decision: str) -> SkillApprovalRecord | None:
        if decision not in {"approved", "rejected", "cancelled", "consumed"}:
            raise ValueError(f"Unsupported approval decision: {decision}")
        decided_at = _now_iso()
        with self.lock, sqlite_transaction(DB_PATH, begin_mode="IMMEDIATE") as conn:
            cursor = conn.execute(
                "UPDATE skill_approvals SET status = ?, decided_at = ? WHERE approval_id = ? AND status = 'pending'",
                (decision, decided_at, approval_id),
            )
            if cursor.rowcount <= 0:
                return None
        return self.get_by_id(approval_id)

    def update_parent_context(
        self,
        approval_id: str,
        *,
        parent_checkpoint_thread_id: str,
        parent_checkpoint_ns: str,
        parent_tool_call_id: str,
    ) -> SkillApprovalRecord | None:
        with self.lock, sqlite_transaction(DB_PATH, begin_mode="IMMEDIATE") as conn:
            conn.execute(
                """
                UPDATE skill_approvals
                SET parent_checkpoint_thread_id = ?, parent_checkpoint_ns = ?, parent_tool_call_id = ?
                WHERE approval_id = ?
                """,
                (
                    parent_checkpoint_thread_id,
                    parent_checkpoint_ns,
                    parent_tool_call_id,
                    approval_id,
                ),
            )
        return self.get_by_id(approval_id)

    def save_checkpoint(
        self,
        *,
        checkpoint_thread_id: str,
        checkpoint_ns: str,
        checkpoint_kind: str,
        run_id: str,
        scope_type: str,
        scope_ref: str,
        agent_name: str,
        execution_entry: str,
        action_hint: str,
        state_payload: dict[str, Any],
    ) -> None:
        now = _now_iso()
        payload = json.dumps(_json_safe(state_payload), ensure_ascii=False, sort_keys=True)
        with self.lock, sqlite_transaction(DB_PATH, begin_mode="IMMEDIATE") as conn:
            conn.execute(
                """
                INSERT INTO skill_checkpoints (
                    checkpoint_thread_id, checkpoint_ns, checkpoint_kind, run_id,
                    scope_type, scope_ref, agent_name, execution_entry, action_hint,
                    state_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(checkpoint_thread_id) DO UPDATE SET
                    checkpoint_ns = excluded.checkpoint_ns,
                    checkpoint_kind = excluded.checkpoint_kind,
                    run_id = excluded.run_id,
                    scope_type = excluded.scope_type,
                    scope_ref = excluded.scope_ref,
                    agent_name = excluded.agent_name,
                    execution_entry = excluded.execution_entry,
                    action_hint = excluded.action_hint,
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (
                    checkpoint_thread_id,
                    checkpoint_ns,
                    checkpoint_kind,
                    run_id,
                    scope_type,
                    scope_ref,
                    agent_name,
                    execution_entry,
                    action_hint,
                    payload,
                    now,
                    now,
                ),
            )

    def load_checkpoint(self, checkpoint_thread_id: str) -> dict[str, Any] | None:
        row = self._fetch_one(
            "SELECT * FROM skill_checkpoints WHERE checkpoint_thread_id = ?",
            (checkpoint_thread_id,),
        )
        if not row:
            return None
        try:
            state_json = json.loads(row["state_json"]) if row["state_json"] else {}
        except json.JSONDecodeError:
            state_json = {}
        return {
            "checkpoint_thread_id": row["checkpoint_thread_id"],
            "checkpoint_ns": row["checkpoint_ns"],
            "checkpoint_kind": row["checkpoint_kind"],
            "run_id": row["run_id"],
            "scope_type": row["scope_type"],
            "scope_ref": row["scope_ref"],
            "agent_name": row["agent_name"],
            "execution_entry": row["execution_entry"],
            "action_hint": row["action_hint"],
            "state": state_json if isinstance(state_json, dict) else {},
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def delete_checkpoint(self, checkpoint_thread_id: str) -> None:
        with self.lock, sqlite_transaction(DB_PATH, begin_mode="IMMEDIATE") as conn:
            conn.execute(
                "DELETE FROM skill_checkpoints WHERE checkpoint_thread_id = ?",
                (checkpoint_thread_id,),
            )

    def purge_orphan_alert_task_artifacts(self) -> dict[str, int]:
        """Drop checkpoints/approvals whose alert_task row no longer exists."""
        with self.lock, sqlite_transaction(DB_PATH, begin_mode="IMMEDIATE") as conn:
            checkpoints_deleted = conn.execute(
                """
                DELETE FROM skill_checkpoints
                WHERE scope_type = 'alert_task'
                  AND scope_ref NOT IN (SELECT task_id FROM alert_tasks)
                """
            ).rowcount
            approvals_deleted = conn.execute(
                """
                DELETE FROM skill_approvals
                WHERE scope_type = 'alert_task'
                  AND scope_ref NOT IN (SELECT task_id FROM alert_tasks)
                """
            ).rowcount
        return {
            "checkpoints_deleted": max(int(checkpoints_deleted or 0), 0),
            "approvals_deleted": max(int(approvals_deleted or 0), 0),
        }

    def serialize_approval(self, record: SkillApprovalRecord) -> dict[str, Any]:
        payload = asdict(record)
        payload["arguments_summary"] = self.build_arguments_summary(record.arguments)
        return payload

    def build_arguments_summary(self, arguments: dict[str, Any]) -> str:
        if not arguments:
            return "无参数"
        parts: list[str] = []
        items = list(arguments.items())
        for key, value in items[:5]:
            rendered = value if isinstance(value, (str, int, float, bool)) or value is None else json.dumps(_json_safe(value), ensure_ascii=False)
            parts.append(f"{key}: {rendered}")
        remaining_count = max(len(items) - 5, 0)
        if remaining_count:
            parts.append(f"还有 {remaining_count} 个")
        return " | ".join(parts) if parts else "无参数"
