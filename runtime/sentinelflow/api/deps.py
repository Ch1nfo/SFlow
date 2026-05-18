"""
Dependency injection shim for routers.

Defines shared utilities (_serialize) and re-exports all service singletons
from app.py so that routers import from a stable, single location.

NOTE: do NOT import from sentinelflow.api.app at module level here —
that would create a circular import (app → routers → deps → app).
Instead we use a lazy accessor pattern for the singletons.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import TYPE_CHECKING, Any


# ── Shared utilities (no circular risk) ─────────────────────────────────────

def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    return value


# ── Lazy singleton accessors ─────────────────────────────────────────────────
# We defer the import of app.py until first attribute access so that
# Python's module system can finish initialising app.py before deps.py
# tries to pull names from it.

def __getattr__(name: str):
    _EXPORTED = {
        "PROJECT_ROOT", "PLATFORM_ROOT", "SKILL_ROOT", "WORKFLOW_ROOT",
        "AGENT_ROOT", "PLATFORM_PLUGIN_ROOT", "branding",
        "skill_runtime", "audit_service", "agent_service",
        "triage_service", "agent_workflow_runner", "task_runner_service",
        "dispatch_service", "polling_service", "alert_parser_generator",
        "auto_execution_service", "weekly_alert_cleanup_service", "skill_approval_service",
        "agent_run_log_service",
    }
    if name in _EXPORTED:
        import sentinelflow.api.app as _app
        return getattr(_app, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
