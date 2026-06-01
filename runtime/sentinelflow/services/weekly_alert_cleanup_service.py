from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta

from typing import TYPE_CHECKING, Any

from sentinelflow.config.runtime import load_runtime_config
from sentinelflow.services.audit_service import AuditService
from sentinelflow.services.dispatch_service import AlertDispatchService

if TYPE_CHECKING:
    from sentinelflow.services.skill_approval_service import SkillApprovalService


class WeeklyAlertCleanupService:
    def __init__(
        self,
        dispatch_service: AlertDispatchService,
        audit_service: AuditService,
        skill_approval_service: "SkillApprovalService | None" = None,
        check_interval_seconds: float = 300.0,
    ) -> None:
        self.dispatch_service = dispatch_service
        self.audit_service = audit_service
        self.skill_approval_service = skill_approval_service
        self.check_interval_seconds = check_interval_seconds
        self._loop_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._last_cleanup_week: str = ""

    async def start(self) -> None:
        if self._loop_task and not self._loop_task.done():
            return
        self._stop_event = asyncio.Event()
        self._loop_task = asyncio.create_task(self._run_loop(), name="sentinelflow-weekly-alert-cleanup")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._loop_task:
            try:
                await self._loop_task
            finally:
                self._loop_task = None

    def run_due_cleanup(self, now: datetime | None = None) -> dict[str, Any]:
        config = load_runtime_config()
        if not config.weekly_alert_cleanup_enabled:
            return {"skipped": True}

        current = now or datetime.now().astimezone()
        week_start = datetime.combine(current.date() - timedelta(days=current.weekday()), time.min, tzinfo=current.tzinfo)
        cleanup_at = week_start + timedelta(hours=1)
        week_key = week_start.date().isoformat()
        if current < cleanup_at or self._last_cleanup_week == week_key:
            return {"skipped": True}

        task_stats = self.dispatch_service.run_weekly_alert_storage_cleanup(week_start)
        artifact_stats = {"checkpoints_deleted": 0, "approvals_deleted": 0}
        if self.skill_approval_service is not None:
            artifact_stats = self.skill_approval_service.purge_orphan_alert_task_artifacts()

        if any(
            int(task_stats.get(key, 0) or 0) > 0
            for key in ("tasks_deleted", "dedup_cleared", "dedup_orphans_cleared")
        ) or any(int(artifact_stats.get(key, 0) or 0) > 0 for key in ("checkpoints_deleted", "approvals_deleted")):
            self.dispatch_service.run_incremental_vacuum()

        self._last_cleanup_week = week_key
        summary = {
            "skipped": False,
            "cutoff": week_start.isoformat(),
            "cleanupAt": cleanup_at.isoformat(),
            **task_stats,
            **artifact_stats,
        }
        self.audit_service.record(
            "weekly_alert_cleanup_checked",
            "Weekly alert cleanup completed.",
            summary,
        )
        return summary

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_due_cleanup()
            except Exception as exc:
                self.audit_service.record(
                    "weekly_alert_cleanup_failed",
                    "Weekly alert cleanup failed.",
                    {"error": str(exc)},
                )
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.check_interval_seconds)
            except asyncio.TimeoutError:
                continue
