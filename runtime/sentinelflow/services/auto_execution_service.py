from __future__ import annotations

import asyncio
import os
import threading
from typing import TYPE_CHECKING, Any

from sentinelflow.config.runtime import load_runtime_config
from sentinelflow.services.audit_service import AuditService
from sentinelflow.services.dispatch_service import AlertDispatchService

if TYPE_CHECKING:
    from sentinelflow.services.task_runner_service import AlertTaskRunnerService

DEFAULT_MAX_TASKS_PER_CYCLE = 10


def _max_tasks_per_cycle_from_env() -> int:
    raw = str(os.getenv("SENTINELFLOW_AUTO_EXECUTION_BATCH_SIZE", "")).strip()
    if not raw:
        return DEFAULT_MAX_TASKS_PER_CYCLE
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_MAX_TASKS_PER_CYCLE


def _run_task_in_isolated_loop(task_runner_service: "AlertTaskRunnerService", task: Any, execution_entry: str) -> dict[str, Any]:
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(task_runner_service.run_task(task, execution_entry=execution_entry))
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        asyncio.set_event_loop(None)
        loop.close()


class AlertAutoExecutionService:
    def __init__(
        self,
        dispatch_service: AlertDispatchService,
        task_runner_service: "AlertTaskRunnerService",
        audit_service: AuditService,
        interval_seconds: float = 1.0,
        max_tasks_per_cycle: int | None = None,
    ) -> None:
        self.dispatch_service = dispatch_service
        self.task_runner_service = task_runner_service
        self.audit_service = audit_service
        self.interval_seconds = interval_seconds
        self.max_tasks_per_cycle = max(1, int(max_tasks_per_cycle or _max_tasks_per_cycle_from_env()))
        self._enabled_by_source: dict[str, bool] = {}
        self._running_by_source: dict[str, bool] = {}
        self._loop_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._wake_lock = threading.Lock()
        self._wake_requested = False
        self._run_once_requested: set[str] = set()

    async def start(self) -> None:
        if self._loop_task and not self._loop_task.done():
            return
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._wake_requested = False
        self._run_once_requested = set()
        self._loop_task = asyncio.create_task(self._run_loop(), name="sentinelflow-auto-executor")

    async def stop(self) -> None:
        for source_id in list(self._enabled_by_source):
            self._enabled_by_source[source_id] = False
        self._run_once_requested = set()
        self._signal_event(self._stop_event)
        self._signal_event(self._wake_event)
        if self._loop_task:
            try:
                await self._loop_task
            finally:
                self._loop_task = None
                self._loop = None

    def enable(self, source_id: str = "default") -> None:
        source_id = source_id or "default"
        if self._enabled_by_source.get(source_id, False):
            return
        self._enabled_by_source[source_id] = True
        self.audit_service.record("auto_execution_enabled", "Enabled continuous automatic alert execution.", {"sourceId": source_id})
        self._request_wake()

    def disable(self, source_id: str = "default") -> None:
        source_id = source_id or "default"
        if not self._enabled_by_source.get(source_id, False):
            return
        self._enabled_by_source[source_id] = False
        self.audit_service.record("auto_execution_disabled", "Disabled continuous automatic alert execution.", {"sourceId": source_id})
        self._request_wake()

    def state(self, source_id: str = "default") -> dict[str, bool]:
        source_id = source_id or "default"
        return {
            "enabled": self._enabled_by_source.get(source_id, False),
            "running": self._running_by_source.get(source_id, False),
        }

    def all_states(self) -> dict[str, dict[str, bool]]:
        source_ids = set(self._enabled_by_source) | set(self._running_by_source)
        return {source_id: self.state(source_id) for source_id in sorted(source_ids)}

    def apply_persisted_state(self, enabled: bool | None = None) -> None:
        if enabled is not None:
            self._enabled_by_source["default"] = bool(enabled)
        for source in getattr(load_runtime_config(), "alert_sources", []) or []:
            source_id = str(getattr(source, "id", "") or "default").strip() or "default"
            self._enabled_by_source[source_id] = bool(getattr(source, "auto_execute_enabled", False))
        self._request_wake()

    def request_run_once(self, source_id: str = "default") -> None:
        self._run_once_requested.add(source_id or "default")
        self._request_wake()

    def _signal_event(self, event: asyncio.Event) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            event.set()
            return
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is loop:
            event.set()
            return
        loop.call_soon_threadsafe(event.set)

    def _request_wake(self) -> None:
        with self._wake_lock:
            self._wake_requested = True
        self._signal_event(self._wake_event)

    def _consume_wake_request(self) -> bool:
        with self._wake_lock:
            if not self._wake_requested:
                return False
            self._wake_requested = False
            return True

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            active_source_ids = {
                source_id for source_id, enabled in self._enabled_by_source.items() if enabled
            } | set(self._run_once_requested)
            if not active_source_ids:
                await self._wait_for_wake()
                continue

            run_once_source_ids = set(self._run_once_requested)
            self._run_once_requested.clear()
            for source_id in sorted(active_source_ids):
                allow_disabled = source_id in run_once_source_ids
                await self._run_pending_once(source_id=source_id, allow_disabled=allow_disabled)
            if self._stop_event.is_set():
                break
            if not any(self._enabled_by_source.values()):
                continue
            await self._wait_for_wake(timeout=self.interval_seconds)

    async def _wait_for_wake(self, timeout: float | None = None) -> bool:
        if self._consume_wake_request():
            return True
        self._wake_event.clear()
        if self._consume_wake_request():
            return True
        if timeout is None:
            await self._wake_event.wait()
            return self._consume_wake_request() or True
        try:
            await asyncio.wait_for(self._wake_event.wait(), timeout=timeout)
            return self._consume_wake_request() or True
        except asyncio.TimeoutError:
            return self._consume_wake_request()

    def _queued_tasks_for_source(self, source_id: str, limit: int):
        try:
            return self.dispatch_service.list_queued_tasks(source_id=source_id, limit=limit)
        except (AttributeError, TypeError):
            pass
        try:
            return [task for task in self.dispatch_service.list_tasks(source_id=source_id) if task.status == "queued"][:limit]
        except TypeError:
            return [task for task in self.dispatch_service.list_tasks() if task.status == "queued"][:limit]

    def _retry_candidates_for_source(self, retry_interval_seconds: int, source_id: str):
        try:
            return self.dispatch_service.list_failed_retry_candidates(retry_interval_seconds, max_retry_count=3, source_id=source_id)
        except TypeError:
            return self.dispatch_service.list_failed_retry_candidates(retry_interval_seconds, max_retry_count=3)

    def _retry_interval_for_source(self, source_id: str) -> int:
        config = load_runtime_config()
        for source in getattr(config, "alert_sources", []) or []:
            if str(getattr(source, "id", "")).strip() == source_id:
                return max(int(getattr(source, "failed_retry_interval_seconds", 0) or 0), 0)
        return max(int(getattr(config, "failed_retry_interval_seconds", 0) or 0), 0)

    async def _run_task_isolated(self, task, *, execution_entry: str) -> dict[str, Any]:
        return await asyncio.to_thread(
            _run_task_in_isolated_loop,
            self.task_runner_service,
            task,
            execution_entry,
        )

    async def _run_pending_once(self, *, source_id: str = "default", allow_disabled: bool = False) -> list[dict[str, Any]]:
        queued_tasks = self._queued_tasks_for_source(source_id, self.max_tasks_per_cycle + 1)
        retry_interval_seconds = self._retry_interval_for_source(source_id)
        retry_tasks = self._retry_candidates_for_source(retry_interval_seconds, source_id)
        if not queued_tasks and not retry_tasks:
            return []

        self._running_by_source[source_id] = True
        results: list[dict[str, Any]] = []
        processed_count = 0
        limit_reached = False
        try:
            for task in queued_tasks:
                if self._stop_event.is_set() or (not self._enabled_by_source.get(source_id, False) and not allow_disabled):
                    break
                if processed_count >= self.max_tasks_per_cycle:
                    limit_reached = True
                    break
                results.append(await self._run_task_isolated(task, execution_entry="auto_alert"))
                processed_count += 1
            for failed_task in retry_tasks:
                if self._stop_event.is_set() or (not self._enabled_by_source.get(source_id, False) and not allow_disabled):
                    break
                if processed_count >= self.max_tasks_per_cycle:
                    limit_reached = True
                    break
                prepared = self.dispatch_service.prepare_retry(failed_task.task_id)
                if not prepared:
                    continue
                self.audit_service.record(
                    "auto_retry_scheduled",
                    f"Automatically retried failed alert task {prepared.task_id}.",
                    {
                        "taskId": prepared.task_id,
                        "eventIds": prepared.event_ids,
                        "sourceId": getattr(prepared, "source_id", source_id),
                        "retryCount": prepared.retry_count,
                        "retryIntervalSeconds": retry_interval_seconds,
                    },
                )
                results.append(await self._run_task_isolated(prepared, execution_entry="auto_alert"))
                processed_count += 1
        finally:
            self._running_by_source[source_id] = False
        if limit_reached:
            self.audit_service.record(
                "auto_execution_batch_limited",
                f"Stopped automatic alert execution cycle after {processed_count} tasks; queued work remains.",
                {"sourceId": source_id, "processed": processed_count, "limit": self.max_tasks_per_cycle},
            )
            if self._enabled_by_source.get(source_id, False):
                self._request_wake()
        return results
