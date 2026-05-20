from __future__ import annotations

import asyncio
import threading
import time

from sentinelflow.alerts.client import SOCAlertApiClient
from sentinelflow.alerts.dedup import AlertDedupStore
from sentinelflow.domain.models import PollingDispatchResult
from sentinelflow.services.dispatch_service import AlertDispatchService
from sentinelflow.config.runtime import load_runtime_config


class AlertPollingService:
    def __init__(
        self,
        client: SOCAlertApiClient,
        dedup: AlertDedupStore,
        dispatch_service: AlertDispatchService,
    ) -> None:
        self.client = client
        self.dedup = dedup
        self.dispatch_service = dispatch_service
        self._latest_result = PollingDispatchResult(tasks=self.dispatch_service.list_tasks())
        self._latest_results: dict[str, PollingDispatchResult] = {}
        self._next_poll_at: dict[str, float] = {}
        self._loop_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._wake_lock = threading.Lock()
        self._wake_requested = False

    def _resolve_source(self, source_id: str | None = None):
        config = load_runtime_config()
        sources = list(getattr(config, "alert_sources", []) or [])
        if not sources:
            return None
        if source_id:
            selected = next((source for source in sources if source.id == source_id), None)
            if selected is not None:
                return selected
        return sources[0]

    def get_latest_result(self, source_id: str | None = None, *, include_tasks: bool = True) -> PollingDispatchResult:
        source = self._resolve_source(source_id)
        effective_source_id = source.id if source is not None else (source_id or "default")
        latest = self._latest_results.get(effective_source_id, self._latest_result)
        return PollingDispatchResult(
            fetched_count=latest.fetched_count,
            queued_count=latest.queued_count,
            updated_count=latest.updated_count,
            completed_count=latest.completed_count,
            skipped_count=latest.skipped_count,
            failed_count=latest.failed_count,
            snapshot_complete=latest.snapshot_complete,
            auto_execute_enabled=latest.auto_execute_enabled,
            auto_execute_running=latest.auto_execute_running,
            tasks=(
                self.dispatch_service.list_tasks(source_id=effective_source_id)
                if include_tasks and effective_source_id
                else self.dispatch_service.list_tasks()
                if include_tasks
                else []
            ),
            errors=list(latest.errors),
        )

    async def start(self) -> None:
        if self._loop_task and not self._loop_task.done():
            return
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._wake_requested = False
        self._loop_task = asyncio.create_task(self._run_scheduler(), name="sentinelflow-alert-poller")

    async def stop(self) -> None:
        self._signal_event(self._stop_event)
        self._signal_event(self._wake_event)
        if self._loop_task:
            try:
                await self._loop_task
            finally:
                self._loop_task = None
                self._loop = None

    def refresh_schedule(self) -> None:
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

    async def _run_scheduler(self) -> None:
        while not self._stop_event.is_set():
            config = load_runtime_config()
            sources = [
                source
                for source in getattr(config, "alert_sources", []) or []
                if source.alert_source_enabled and max(int(source.poll_interval_seconds or 0), 0) > 0
            ]
            if not sources:
                await self._wait_for_reconfigure()
                continue

            now = time.monotonic()
            for source in sources:
                self._next_poll_at.setdefault(source.id, now)
            active_ids = {source.id for source in sources}
            for source_id in list(self._next_poll_at):
                if source_id not in active_ids:
                    self._next_poll_at.pop(source_id, None)

            due_sources = [source for source in sources if self._next_poll_at.get(source.id, now) <= now]
            if due_sources:
                for source in due_sources:
                    await self.poll_once(source.id)
                    self._next_poll_at[source.id] = time.monotonic() + max(int(source.poll_interval_seconds or 0), 1)
                continue

            next_due = min(self._next_poll_at.get(source.id, now + 1) for source in sources)
            timeout = max(next_due - now, 0.1)
            reconfigured = await self._wait_for_reconfigure(timeout=timeout)
            if reconfigured or self._stop_event.is_set():
                continue

    async def _wait_for_reconfigure(self, timeout: int | None = None) -> bool:
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

    async def poll_once(self, source_id: str | None = None) -> PollingDispatchResult:
        source = self._resolve_source(source_id)
        if source is None:
            self._latest_result = PollingDispatchResult(
                failed_count=1,
                snapshot_complete=False,
                errors=["当前没有可用的告警源配置。"],
            )
            return self.get_latest_result(source_id)
        response = await asyncio.to_thread(self.client.fetch_open_alerts, source)
        if "error" in response:
            latest = PollingDispatchResult(
                fetched_count=0,
                queued_count=0,
                skipped_count=0,
                failed_count=1,
                snapshot_complete=False,
                auto_execute_enabled=self._latest_results.get(source.id, self._latest_result).auto_execute_enabled,
                auto_execute_running=self._latest_results.get(source.id, self._latest_result).auto_execute_running,
                errors=[str(response.get("error", "Unknown polling error"))],
            )
            self._latest_results[source.id] = latest
            self._latest_result = latest
            return self.get_latest_result(source.id)

        alerts = response.get("alerts", [])
        if not isinstance(alerts, list):
            latest = PollingDispatchResult(
                fetched_count=0,
                queued_count=0,
                skipped_count=0,
                failed_count=1,
                snapshot_complete=False,
                auto_execute_enabled=self._latest_results.get(source.id, self._latest_result).auto_execute_enabled,
                auto_execute_running=self._latest_results.get(source.id, self._latest_result).auto_execute_running,
                errors=["Polling response has invalid alerts structure."],
            )
            self._latest_results[source.id] = latest
            self._latest_result = latest
            return self.get_latest_result(source.id)

        if response.get("demo_mode") and not alerts and not response.get("fallback_triggered"):
            self.dispatch_service.clear_demo_tasks()

        snapshot_complete = bool(response.get("snapshot_complete"))
        queued_tasks, skipped, updated, completed, errors = await self.dispatch_service.dispatch(
            alerts,
            allow_missing_completion=snapshot_complete,
            source_id=source.id,
            source_name=source.name,
        )
        
        fallback_errors = []
        if response.get("fallback_triggered") and response.get("fallback_reason"):
            fallback_errors.append(f"由于告警源故障触发降级回退：{response['fallback_reason']}")
            
        combined_errors = fallback_errors + errors

        latest = PollingDispatchResult(
            fetched_count=len(alerts),
            queued_count=len(queued_tasks),
            updated_count=updated,
            completed_count=len(completed),
            skipped_count=skipped,
            failed_count=len(combined_errors),
            snapshot_complete=snapshot_complete,
            auto_execute_enabled=self._latest_results.get(source.id, self._latest_result).auto_execute_enabled,
            auto_execute_running=self._latest_results.get(source.id, self._latest_result).auto_execute_running,
            tasks=self.dispatch_service.list_tasks(source_id=source.id),
            errors=combined_errors,
        )
        self._latest_results[source.id] = latest
        self._latest_result = latest
        return self.get_latest_result(source.id)
