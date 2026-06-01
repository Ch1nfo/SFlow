"""
SentinelFlow API Application Entry Point

All service singletons are initialized here and re-exported via deps.py.
Route handlers live in separate router modules (routers/).
"""
from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from sentinelflow.alerts.client import SOCAlertApiClient
from sentinelflow.alerts.dedup import AlertDedupStore
from sentinelflow.alerts.parser_generator import AlertParserGenerator
from sentinelflow.alerts.poller import AlertPollingService
from sentinelflow.agent.service import SentinelFlowAgentService
from sentinelflow.config.branding import load_branding_config
from sentinelflow.config.runtime import load_runtime_config
from sentinelflow.services.audit_service import AuditService
from sentinelflow.services.auto_execution_service import AlertAutoExecutionService
from sentinelflow.services.agent_run_log_service import AgentRunLogService
from sentinelflow.services.dispatch_service import AlertDispatchService
from sentinelflow.services.skill_approval_service import SkillApprovalService
from sentinelflow.services.task_runner_service import AlertTaskRunnerService
from sentinelflow.services.triage_service import TriageService
from sentinelflow.services.weekly_alert_cleanup_service import WeeklyAlertCleanupService
from sentinelflow.skills.adapters import SentinelFlowSkillRuntime
from sentinelflow.workflows.agent_workflow_runner import SentinelFlowAgentWorkflowRunner

# ── Path roots ──────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLATFORM_ROOT = PROJECT_ROOT.parent if (PROJECT_ROOT.parent / ".sentinelflow").is_dir() else PROJECT_ROOT
SKILL_ROOT = PROJECT_ROOT / ".sentinelflow" / "plugins" / "skills"
WORKFLOW_ROOT = PROJECT_ROOT / ".sentinelflow" / "plugins" / "workflows"
AGENT_ROOT = PROJECT_ROOT / ".sentinelflow" / "plugins" / "agents"
PLATFORM_PLUGIN_ROOT = PLATFORM_ROOT / ".sentinelflow" / "plugins"

# ── Branding & App ───────────────────────────────────────────────────────────
branding = load_branding_config()

# ── Service singletons ───────────────────────────────────────────────────────
skill_runtime = SentinelFlowSkillRuntime(SKILL_ROOT)
audit_service = AuditService()
agent_run_log_service = AgentRunLogService()
skill_approval_service = SkillApprovalService()
agent_service = SentinelFlowAgentService(
    project_root=PROJECT_ROOT,
    skill_runtime=skill_runtime,
    approval_service=skill_approval_service,
    audit_service=audit_service,
)
triage_service = TriageService()
agent_workflow_runner = SentinelFlowAgentWorkflowRunner(
    agent_service=agent_service,
    audit_service=audit_service,
)
agent_service.attach_workflow_runner(agent_workflow_runner)
dispatch_service = AlertDispatchService(
    dedup=AlertDedupStore(),
    triage_service=triage_service,
    audit_service=audit_service,
)
polling_service = AlertPollingService(
    client=SOCAlertApiClient(),
    dedup=dispatch_service.dedup,
    dispatch_service=dispatch_service,
)
task_runner_service = AlertTaskRunnerService(
    dispatch_service=dispatch_service,
    audit_service=audit_service,
    agent_service=agent_service,
    agent_workflow_runner=agent_workflow_runner,
    workflow_root=WORKFLOW_ROOT,
    run_log_service=agent_run_log_service,
)
auto_execution_service = AlertAutoExecutionService(
    dispatch_service=dispatch_service,
    task_runner_service=task_runner_service,
    audit_service=audit_service,
)
weekly_alert_cleanup_service = WeeklyAlertCleanupService(
    dispatch_service=dispatch_service,
    audit_service=audit_service,
    skill_approval_service=skill_approval_service,
)
alert_parser_generator = AlertParserGenerator()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    agent_run_log_service.cleanup()
    await polling_service.start()
    await auto_execution_service.start()
    await weekly_alert_cleanup_service.start()
    auto_execution_service.apply_persisted_state()
    try:
        yield
    finally:
        await weekly_alert_cleanup_service.stop()
        await auto_execution_service.stop()
        await polling_service.stop()


app = FastAPI(title=branding.api_title, version="1.3.0", lifespan=lifespan)

# ── Router registration ──────────────────────────────────────────────────────
from sentinelflow.api.routers import system, plugins, alerts  # noqa: E402

app.include_router(system.router)
app.include_router(plugins.router)
app.include_router(alerts.router)
