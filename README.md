<div align="center">

# SentinelFlow

### AI-Native SecOps Control Plane — Multi-Agent SOC Automation

[![Version](https://img.shields.io/badge/version-1.2.1-blue.svg)](https://github.com/Ch1nfo/SentinelFlow/releases)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey.svg)](#)
[![Built with LangGraph](https://img.shields.io/badge/built%20with-LangGraph-orange.svg)](https://github.com/langchain-ai/langgraph)

English | [中文](README_ZH.md)

</div>

---

## Why SentinelFlow?

Modern Security Operations Centers face an overwhelming volume of alerts — most teams spend hours triaging events that could be handled in seconds with proper automation. Existing SIEM platforms offer rules-based correlation, but lack the contextual reasoning needed to handle novel threats or complex multi-step investigations.

**SentinelFlow** is a full-stack SOC automation platform that combines a **LangGraph-powered multi-agent orchestration runtime** with a **React WebUI** for alert management and operator collaboration. Instead of rigid playbooks, you get a flexible, extensible agent system where a Primary Supervisor Agent coordinates specialized Worker Sub-Agents — each equipped with pluggable Skills that can call external APIs, run enrichment scripts, close tickets, and more.

- **Multi-Agent Orchestration** — Supervisor + Worker SubGraph pattern via LangGraph, with sequential and parallel worker delegation plus supervisor-guided workflows
- **Full Operator Console** — Unified WebUI for Overview, Alert Workbench, Task Center, Conversation Console, Skills, RAG, Agents, Workflows, Settings, and run-log inspection
- **Pluggable Skill System** — Drop a `SKILL.md` + `main.py` into the local plugin workspace; agents discover and invoke them automatically with granular per-agent permission control
- **Dual Entry Points** — Accepts both raw security alerts (JSON payloads from SIEM/SOAR) and free-form human commands via the WebUI conversation console
- **Agent Workflow Engine** — Define reusable multi-step workflows for high-frequency scenarios; the Primary Agent loads the plan, then calls each worker with concrete step prompts
- **Multi-Source Alert Ingestion** — Configure multiple named alert sources, each using REST/HTTP polling or a Python script entrypoint with its own parser and schedule
- **AI-Assisted Parser Generation** — Paste a sample alert payload and let the LLM auto-generate the field-mapping parser rule, with preview and one-click apply
- **Source-Scoped Async Auto-Execution & Retry** — Enable the background executor per alert source, process queued alerts asynchronously, and retry failed tasks after source-specific delays
- **Approval & Resume Flow** — `approval_required` skills pause manual graph execution, surface approval cards in the UI, then resume from checkpoint after approve/reject
- **Run-Local Context Window Control** — Each LLM call receives a budgeted `llm_prompt_view` with task anchors, `case_context`, recent ReAct turns, and compact tool records while full state/checkpoints/run logs remain lossless
- **SOC Execution Guardrails** — Authority traces, key facts, task-anchor selection, and pre-execution input checks help agents keep target IPs, recipients, event IDs, and closure facts precise
- **Thinking-Model Adapter** — Optional settings toggle for providers such as DeepSeek that need explicit `thinking: disabled` request bodies
- **Fine-Grained Governance** — Per-agent skill permissions, mode-aware worker allowlists, execution approval gates, audit logging, and agent-level model overrides

## Screenshots

|                        Security Overview Dashboard                        |                        Conversation Console                        |
| :----------------------------------------------------------: | :----------------------------------------------------------: |
| ![image-20260405225720016](https://raw.githubusercontent.com/Ch1nfo/picbed/main/img/20260405225720053.png) | ![image-20260405231100920](https://raw.githubusercontent.com/Ch1nfo/picbed/main/img/20260406140803364.png) |

|                        Alert Workbench                        |                        Skill Management                        |
| :----------------------------------------------------------: | :----------------------------------------------------------: |
| ![image-20260405225903594](https://raw.githubusercontent.com/Ch1nfo/picbed/main/img/20260405225903635.png)| ![image-20260405230107750](https://raw.githubusercontent.com/Ch1nfo/picbed/main/img/20260405230107788.png) |

|                        Agent Management                         |                        Workflow Management                        |
| :----------------------------------------------------------: | :----------------------------------------------------------: |
| ![image-20260405230145352](https://raw.githubusercontent.com/Ch1nfo/picbed/main/img/20260405230145399.png) | ![image-20260405230315299](https://raw.githubusercontent.com/Ch1nfo/picbed/main/img/20260405230315341.png) |

## Features

### Multi-Agent Orchestration

- **Supervisor + Worker SubGraph** — Primary Agent uses LangGraph's `ToolNode` to delegate tasks to Worker Sub-Agents, each compiled as an isolated ReAct SubGraph wrapped as a `@tool`
- **Parallel Delegation** — Primary Agent can dispatch multiple independent sub-tasks to different workers simultaneously via `delegate_parallel`
- **Agent Workflow Engine** — Define reusable workflows for common scenarios (e.g., phishing triage, IP enrichment + block); `run_workflow` loads the fixed plan, while the Primary Agent remains responsible for calling each Worker with a complete task prompt
- **SOC Context Window Manager** — `prepare_messages_for_llm()` builds a prompt view before every Supervisor/Worker call: system prompt, current task anchor, `case_context`, recent ReAct turns, and compacted older tool results
- **Run-Local Case Context** — During a single run, agents maintain structured facts such as goal, alert refs, actions taken, missing inputs, pending approvals, completed steps, and do-not-repeat hints
- **Mode-Aware Worker Permissions** — The Primary Agent can use different worker allowlists for conversation-style execution and alert-handling execution
- **Prompt Variants & Synthesis** — Primary Agents can define base, command, alert, and synthesis prompts, while Worker Agents keep a single execution prompt
- **Cancellation & Step Limits** — All graphs respect a `cancel_event` threading flag; `worker_max_steps` caps orchestration recursion depth

### Pluggable Skill System

- **SKILL.md-based discovery** — Each skill is a directory with a `SKILL.md` (YAML frontmatter + documentation body) and an optional `main.py` entrypoint
- **Two skill types**: `doc` (knowledge-only, read by agent) and `hybrid` (doc + executable subprocess)
- **Per-agent permission control** — `doc_skill_allowlist`, `exec_skill_allowlist`, `approval_required` flags per skill; `approval_required` only applies to the Conversation Console and manual single-alert handling, each execution is approved separately, while auto-execution / auto-retry / debug bypass approval
- **Subprocess execution** — Skills run in isolated subprocesses with structured JSON I/O; audit logging built in
- **Compact LLM surface** — Large tool outputs and skill documents are kept in runtime state/run logs but summarized before they re-enter the LLM context
- **In-WebUI Skill Management** — Create, edit, delete, inspect, and debug skills directly from the dedicated Skills page

### Alert Ingestion Pipeline

- **Multiple named alert sources** — Manage more than one upstream source from Settings; each source carries its own name, enablement state, parser, poll interval, retry interval, auto-execution flag, and optional source-specific analysis prompt
- **Dual alert source types** — `api` mode polls any REST endpoint (configurable method, headers, query, body); `script` mode runs a custom Python script and reads its stdout as the alert payload
- **AI-powered parser generation** — Paste a raw sample payload; the LLM auto-generates a `field_mapping` parser rule with live preview and one-click apply
- **Fetch / Parse Validation** — Test upstream fetches, import parser JSON, and preview parsed alert records before saving settings
- **Flexible field mapping** — Point-path-based rules map arbitrary JSON structures to SentinelFlow's canonical alert schema (`eventIds`, `alert_name`, `sip`, `dip`, `alert_time`, etc.)
- **Source-aware deduplication & idempotency** — SQLite-backed dedup store prevents re-queueing already-active alerts while keeping identical event IDs from different sources isolated
- **Per-source polling scheduler** — Each enabled source can poll on its own interval; the UI can trigger immediate polling for the selected source, while the API also supports all-source polling
- **Fallback & retry** — Failed tasks can be retried manually or automatically after the retry interval configured for the matching alert source

### Task Queue & Execution

- **SQLite-backed source-aware task queue** — Alert handling tasks, source IDs, source names, and approval records are persisted to `.sentinelflow/sys_queue.db` by default; survives process restarts
- **Continuous source-scoped auto-execution** — Enable the auto-executor loop per source to process queued tasks asynchronously without human action
- **Automatic retry for failed tasks** — Configure failed-task retry intervals per source to let SentinelFlow retry eligible failed alerts in the background
- **Manual handling** — Trigger single-task execution from the alert workbench at any time
- **Task lifecycle** — `queued → running → awaiting_approval / pending_closure / succeeded / failed / completed`; manual approval can pause a task without losing checkpoint state
- **Full execution trace** — Every task stores a structured `execution_trace` covering alert receipt, workflow usage, agent analysis, skill calls, approval state, closure result, and final status
- **Fact-based result convergence** — Final status, judgment, disposal outcome, closure state, and workflow usage are converged into structured `final_facts`
- **Completion Policy Semantics** — Skill-level `completion_policy` distinguishes enrichment, containment, notification, and closure effects so task state follows real execution facts rather than loose text summaries
- **Run Log Traceability** — LLM prompt views, window statistics, worker boundaries, constructed skill arguments, approvals, and final task results are recorded for audit and troubleshooting

### Security Operations WebUI

- **Overview Dashboard** — Unified platform summary for runtime health, task counts, agent/skill availability, judgment distribution, disposal outcomes, and recent activity
- **Alert Workbench** — Switch between alert sources, browse source-scoped task queues, start/stop automatic execution, manually trigger alert tasks, and inspect full alert context, approval state, and execution traces
- **Task Center** — Review queue state, retry failed work, approve pending skills, and inspect full-process execution details from a task-first view
- **Conversation Console** — Free-form command interface with multi-session history, streaming replies, collapsible worker/skill summaries, execution context details, and inline approval cards
- **Configuration Center** — Unified settings page for LLM credentials, thinking-model adapter, multi-source alert connection, parser rules, polling schedules, retry intervals, run-log retention, and auto-execution toggles — all persisted without restarting the server
- **RAG Settings** — Configure the built-in RAG skill, retrieval parameters, API key, rerank model, and agent availability from the WebUI
- **Run Log Viewer** — Inspect per-alert execution logs, prompt windows, worker/skill events, and argument provenance from the Settings page debug panel
- **Skill Management** — Create, view, edit, and delete skills; run debug executions with custom arguments
- **Agent Management** — Configure Primary Agent and Worker Sub-Agents: prompts (default / alert / command / synthesis variants), LLM overrides, skill permissions, and mode-aware worker delegation
- **Workflow Management** — Create and edit Agent Workflows; run test executions from the UI

### Platform & Architecture

- **FastAPI backend** — Async Python runtime with structured JSON API; uvicorn server
- **React + Vite frontend** — TypeScript, TailwindCSS, component-based architecture
- **Unified dev entrypoint** — `python scripts/dev.py dev` starts the full stack in one command
- **Source-first local layout** — Runtime code lives under `runtime/`, WebUI under `webui/`, helper scripts under `scripts/`, and local plugin/runtime state is stored under the project-root `.sentinelflow/` workspace by default

## Architecture Overview

<details>
<summary><strong>System Architecture Diagram</strong></summary>

```
┌─────────────────────────────────────────────────────────────────┐
│                   React WebUI (Vite + TS)                       │
│  ┌──────────────┐  ┌─────────────────┐  ┌───────────────────┐   │
│  │ Overview /   │  │ Conversation UI │  │ Plugin & Config   │   │
│  │ Alerts /     │  │  + approvals    │  │    Management     │   │
│  │ Tasks        │  │                 │  │                   │   │
│  └──────────────┘  └─────────────────┘  └───────────────────┘   │
└──────────────────────────┬──────────────────────────────────────┘
                           │  REST API (FastAPI)
┌──────────────────────────▼──────────────────────────────────────┐
│              SentinelFlow Runtime (Python / FastAPI)            │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │               Multi-Agent Orchestrator                     │  │
│  │   ┌──────────────────────────────────────────────────┐    │  │
│  │   │  Primary Agent (Supervisor)                       │    │  │
│  │   │  LangGraph StateGraph + ToolNode                  │    │  │
│  │   │  Context Window → ReAct → Worker/Skill Tools      │    │  │
│  │   │    ↓ sequential / parallel worker delegation      │    │  │
│  │   │  ┌────────────┐  ┌────────────┐  ┌────────────┐  │    │  │
│  │   │  │  Worker A  │  │  Worker B  │  │  Worker C  │  │    │  │
│  │   │  │ ReAct Sub- │  │ ReAct Sub- │  │ ReAct Sub- │  │    │  │
│  │   │  │   Graph    │  │   Graph    │  │   Graph    │  │    │  │
│  │   │  └────────────┘  └────────────┘  └────────────┘  │    │  │
│  │   └──────────────────────────────────────────────────┘    │  │
│  └────────────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                    Skill Runtime                           │  │
│  │    loader → executor → subprocess isolation → audit log    │  │
│  └────────────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │            Prompt Window & Run Log Traceability            │  │
│  │  task anchors → case_context → compact tool records        │  │
│  │  full state/checkpoints/run logs remain available          │  │
│  └────────────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │              Alert Ingestion & Task Queue                  │  │
│  │  Multi-Source API/Script Poller → Parser → Dedup → Queue   │  │
│  │  Source-Scoped Auto-Executor → Task Runner → Agent/Workflow│  │
│  └────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

**Core Design Patterns**

- **Supervisor + Worker SubGraph** — Workers are compiled ReAct SubGraphs and wrapped as `@tool` functions; compact summaries, key facts, action results, approvals, and errors surface back to the Supervisor
- **SOC context window control** — `prepare_messages_for_llm()` creates the actual LLM prompt view from full state, preserving task anchors and recent ReAct turns while compressing older tool records
- **Run-local case context** — `case_context` carries current goal, alert refs, facts, actions taken, missing inputs, pending approvals, completed steps, and do-not-repeat hints for the current run only
- **SKILL.md discovery** — Skills are file-system plugins; no code changes needed to add new capabilities
- **Dual entry types** — `alert` (JSON from SIEM) and `conversation` (human command); both routed through the same agent runtime
- **Source-aware SQLite task persistence** — Alert tasks survive restarts; source-scoped event IDs and atomic status transitions prevent duplicate execution
- **Atomic result serialization** — All graph results pass through `_serialize_alert_result` for a consistent, structured execution trace
- **Lossless audit / compact inference split** — Full state, checkpoints, and run logs are retained; only the prompt sent to the LLM is windowed and compacted

**Key Components**

- **`SentinelFlowAgentService`** — Top-level service; routes to orchestrator or single-agent graph; serializes results
- **`build_orchestrator_graph()`** — Compiles the Supervisor + Worker multi-agent LangGraph
- **`build_agent_graph()`** — Builds a single-agent ReAct SubGraph (used for both workers and standalone agents)
- **`context_utils`** — Builds context manifests, task anchors, case context, prompt windows, key facts, compact tool summaries, and pre-execution input checks
- **`RunLogTracer` / `AgentRunLogService`** — Records per-run LLM prompt views, window information, worker boundaries, skill calls, approvals, and final results
- **`AlertDispatchService`** — SQLite-backed source-aware task queue; handles create, dedup, status transition, and finalization
- **`AlertPollingService`** — Per-source scheduler that polls enabled API/script alert sources and dispatches normalized alerts into the task queue
- **`AlertAutoExecutionService`** — Asyncio-based source-scoped executor loop; processes queued and retry-eligible tasks without human action
- **`AlertParserGenerator`** — LLM-assisted + heuristic field-mapping rule generator for arbitrary JSON alert payloads
- **`SentinelFlowSkillRuntime`** — Manages skill lifecycle; adapts skills as LangChain tools for agent use
- **`AgentWorkflowRegistry`** — Lists and resolves workflow definitions for multi-step Agent Workflows
- **`SkillApprovalService`** — Persists approval records and checkpoint resume state for human-in-the-loop execution
- **`weekly_alert_cleanup_service`** — Optional weekly cleanup for stored alert tasks and run artifacts
- **`AuditService`** — Records runtime audit events for dispatch, task execution, approval handling, and background services

</details>

<details>
<summary><strong>Project Structure</strong></summary>

```
.
├── pyproject.toml                      # Python package metadata & CLI entrypoint
├── scripts/
│   ├── dev.py                          # Unified local dev entrypoint
│   └── serve_webui.py                  # Production WebUI static file server
├── .sentinelflow/                      # Local plugins, runtime.json, SQLite queue (generated at runtime)
├── runtime/
│   └── sentinelflow/
│       ├── agent/
│       │   ├── service.py              # Top-level agent service (orchestration logic)
│       │   ├── orchestrator_graph.py   # Supervisor + Worker SubGraph builder
│       │   ├── graph.py                # Single-agent ReAct graph builder
│       │   ├── registry.py             # Agent definition loader (agent.yaml)
│       │   ├── prompts.py              # System prompts & appendix templates
│       │   ├── context_utils.py        # Context manifest, prompt window, case_context, compact records
│       │   ├── run_log_tracer.py       # LLM prompt/run-log event tracing
│       │   ├── skill_run_analyzer.py   # Skill/closure/action result convergence
│       │   ├── policy.py               # Per-agent skill permission resolver
│       │   ├── nodes.py                # LangGraph node implementations
│       │   ├── tools.py                # Agent-facing tool definitions
│       │   └── state.py                # Agent graph state schema
│       ├── skills/
│       │   ├── loader.py               # SKILL.md discovery & validation
│       │   ├── executor.py             # Skill subprocess execution
│       │   ├── adapters.py             # Skill → LangChain tool adapters
│       │   ├── resolver.py             # Local/plugin skill resolution
│       │   └── models.py               # Skill data models
│       ├── alerts/
│       │   ├── client.py               # Alert source HTTP/script client
│       │   ├── poller.py               # Scheduled polling service
│       │   ├── parser_runtime.py       # Field-mapping parser engine
│       │   ├── parser_generator.py     # LLM + heuristic parser rule generator
│       │   └── dedup.py                # Alert deduplication store
│       ├── services/
│       │   ├── agent_run_log_service.py # Per-alert JSONL run logs
│       │   ├── dispatch_service.py     # SQLite-backed task queue & lifecycle
│       │   ├── task_runner_service.py  # Task execution orchestration
│       │   ├── auto_execution_service.py # Continuous auto-executor loop
│       │   ├── skill_approval_service.py # Skill approval records + checkpoint persistence
│       │   ├── triage_service.py       # Rule-based alert disposition fallback
│       │   ├── weekly_alert_cleanup_service.py # Optional weekly cleanup
│       │   └── audit_service.py        # Audit event log
│       ├── tools/                      # Built-in operational tools
│       ├── workflows/                  # Agent workflow registry & runner
│       ├── api/                        # FastAPI route handlers
│       ├── config/                     # Runtime config loader (.env + persisted JSON)
│       └── domain/                     # Shared enums, models, errors
│   └── tests/                          # Runtime regression tests
├── webui/
│   └── src/
│       ├── components/                 # React UI components
│       ├── pages/                      # Page-level views
│       ├── api/                        # API client (fetch wrappers)
│       ├── hooks/                      # Custom React hooks
│       └── styles/                     # Global styles & Tailwind config
```

</details>

<details>
<summary><strong>Development Guide</strong></summary>

### Environment Requirements

- Python 3.11+
- Node.js 18+ / pnpm 8+
- (Optional) A LangGraph-compatible LLM API key (OpenAI-compatible endpoint)

### Development Commands

```bash
# Clone and set up Python environment
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Install WebUI dependencies
cd webui && pnpm install && cd ..

# Start the full dev stack (backend + frontend)
python scripts/dev.py dev

# Start backend only
python scripts/dev.py backend

# Start WebUI dev server only
python scripts/dev.py webui-dev

# Build WebUI for production
python scripts/dev.py webui-build

# Serve a built WebUI bundle
python scripts/dev.py webui-serve
```

After editable install, you can also use the CLI directly:

```bash
sentinelflow dev
sentinelflow backend
```

### Environment Configuration

The preferred way to configure SentinelFlow is through the **WebUI Settings panel** — all settings are persisted to `.sentinelflow/runtime.json` by default without requiring a server restart.

Alternatively, create a project-root `.env` file for environment-level defaults:

```bash
touch .env
```

Key environment variables (all prefixed with `SENTINELFLOW_`):

```ini
# LLM Configuration (OpenAI-compatible)
SENTINELFLOW_LLM_API_KEY=sk-...
SENTINELFLOW_LLM_API_BASE_URL=https://api.openai.com/v1
SENTINELFLOW_LLM_MODEL=gpt-4o
SENTINELFLOW_LLM_THINKING_ADAPTER_ENABLED=false  # enable only for thinking-model adapters such as DeepSeek

# Alert Source
SENTINELFLOW_ALERT_SOURCE_ENABLED=false
SENTINELFLOW_ALERT_SOURCE_TYPE=api          # "api" or "script"
SENTINELFLOW_ALERT_SOURCE_URL=https://your-siem/api/alerts
SENTINELFLOW_POLL_INTERVAL_SECONDS=60

# Auto-execution
SENTINELFLOW_AUTO_EXECUTE_ENABLED=false

# Runtime
SENTINELFLOW_AGENT_ENABLED=true
SENTINELFLOW_RUN_LOG_RETENTION_DAYS=1
SENTINELFLOW_WEEKLY_ALERT_CLEANUP_ENABLED=false

# RAG skill defaults
SENTINELFLOW_RAG_ENABLED=true
SENTINELFLOW_RAG_KNOWLEDGE_ID=your-knowledge-id
SENTINELFLOW_RAG_API_KEY=sk-...
```

### Tech Stack

**Backend**: Python 3.11 · FastAPI · uvicorn · LangGraph · LangChain · Pydantic v2 · python-dotenv · SQLite

**Frontend**: React 19 · TypeScript · Vite 7 · TailwindCSS · React Router

**AI Runtime**: LangGraph (StateGraph + ToolNode) · LangChain Core · langchain-openai

</details>

## Quick Start

### 1. Install Python Dependencies

```bash
# Linux/Mac
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
# Windows CMD
python -m venv .venv
.venv\Scripts\activate.bat
pip install -e ".[dev]"
```

### 2. Install WebUI Dependencies

```bash
cd webui
pnpm install
cd ..
```

### 3. Start the Full Stack

```bash
python scripts/dev.py dev
```

This starts:
- **Backend API** on `http://127.0.0.1:8001`
- **WebUI** on `http://127.0.0.1:5173`

For a production-like local preview, build the frontend and serve the static bundle:

```bash
python scripts/dev.py webui-build
python scripts/dev.py webui-serve
```

### 4. Configure via WebUI

Open the WebUI and navigate to **Settings**. Configure your LLM endpoint and one or more alert sources — all settings are persisted immediately without a restart.

Alternatively, create a `.env` file for environment-level defaults:

```bash
touch .env
# Edit .env with your SENTINELFLOW_LLM_API_KEY, SENTINELFLOW_LLM_API_BASE_URL, etc.
```

### 5. Add Your First Skill (Optional)

Create a new directory under `.sentinelflow/plugins/skills/` (default local workspace) with a `SKILL.md`, or use the **Skill Management** page in the WebUI to create one directly:

```markdown
---
name: get-ip-info
description: Query IP geolocation and threat intelligence for a given IP address
type: hybrid
mode: subprocess
entry: main.py
execute_policy:
  enabled: true
  approval_required: false
  audit: true
---

# get-ip-info

Query IP reputation, ASN, and geolocation using external threat intel APIs.

## Input

- `ip`: The IP address to look up

## Output

Returns a JSON object with `country`, `asn`, `reputation`, `is_malicious`.
```

The agent will automatically discover and invoke this skill when appropriate.

`approval_required` only affects two entry points: the **Conversation Console** and **manual single-alert handling / manual retry**. In those two entry points, every actual skill execution requires a fresh approval. When **auto-execution** is enabled, SentinelFlow will execute the skill directly even if `approval_required: true` is set.

## FAQ

<details>
<summary><strong>What LLM providers does SentinelFlow support?</strong></summary>

SentinelFlow uses an OpenAI-compatible API interface (`langchain-openai`). Any provider that supports the OpenAI Chat Completions API format works — including OpenAI, Anthropic (via proxy), DeepSeek, Qwen, local models via Ollama/LM Studio, and API relay services.

Configure the endpoint in the WebUI Settings or via environment variables:
```ini
SENTINELFLOW_LLM_API_BASE_URL=https://your-provider/v1
SENTINELFLOW_LLM_API_KEY=your-key
SENTINELFLOW_LLM_MODEL=model-name
```

For DeepSeek-style thinking models, enable **Thinking Model Adapter** in Settings or set `SENTINELFLOW_LLM_THINKING_ADAPTER_ENABLED=true`. When enabled, SentinelFlow sends `thinking: {"type": "disabled"}` to avoid provider-side `reasoning_content` replay errors. Leave it disabled for providers that do not support this request body.

</details>

<details>
<summary><strong>What alert source types are supported?</strong></summary>

SentinelFlow supports multiple named alert sources from the Settings panel. Each source can use one of two modes:

- **API mode** (`api`): Polls any REST/HTTP endpoint. Supports GET/POST, custom headers, query parameters, and request body. Ideal for SIEM/SOAR platforms with a REST API.
- **Script mode** (`script`): Runs a Python script you write directly in the UI. The script should print a JSON object to stdout containing `count` and `alerts`. Use this for custom data sources, local log files, or any integration that doesn't expose a REST endpoint.

Each source has its own parser rule, polling interval, failed-task retry interval, auto-execution flag, and optional alert-analysis prompt. Tasks are stored with `source_id` / `source_name`, and deduplication is scoped by source plus event ID.

</details>

<details>
<summary><strong>How does the AI parser generation work?</strong></summary>

Paste a sample alert JSON payload in the Settings panel and click **Generate Parser**. SentinelFlow sends the sample to your configured LLM, which returns a `field_mapping` rule that maps your schema's fields to SentinelFlow's canonical alert fields (`eventIds`, `alert_name`, `sip`, `dip`, etc.). A live preview shows how the rule would parse your sample. If the LLM call fails or is unavailable, a heuristic fallback rule is generated instead.

</details>

<details>
<summary><strong>How do I define a Worker Sub-Agent?</strong></summary>

Create a directory under `.sentinelflow/plugins/agents/` (default local workspace) with an `agent.yaml` and optional prompt files, or use the **Agent Management** page in the WebUI:

```yaml
# agent.yaml
name: ip-enrichment-worker
description: Specialized worker for IP enrichment and threat intel queries
role: worker
enabled: true
exec_skill_allowlist:
  - get-ip-info
  - virustotal-lookup
worker_max_steps: 3
```

The Primary Agent will automatically discover and delegate to this worker when appropriate.

</details>

<details>
<summary><strong>How does the Primary Agent decide to use a Worker?</strong></summary>

The Primary Agent (Supervisor) is bound with all available Worker Sub-Graphs as tools via LangGraph's `ToolNode`. On each reasoning step, the LLM decides whether to call a worker tool (sequentially or in parallel), invoke a preset workflow, or finish. The `worker_max_steps` setting caps the total number of delegation steps to prevent runaway orchestration.

</details>

<details>
<summary><strong>What is auto-execution mode?</strong></summary>

When enabled from the Settings panel, Alert Workbench, or `SENTINELFLOW_AUTO_EXECUTE_ENABLED=true` for the default source, SentinelFlow runs a background asyncio loop that continuously picks up `queued` tasks for enabled sources and executes them through the agent pipeline without requiring manual intervention. If a source has `failed_retry_interval_seconds` configured, eligible failed tasks from that source can also be retried automatically after the delay. You can stop automation per source from the UI.

</details>

<details>
<summary><strong>Can I run SentinelFlow without an LLM API key?</strong></summary>

The WebUI and alert ingestion pipeline work without an LLM key. However, the AI agent features (multi-agent orchestration, skill invocation, LLM-based triage, parser generation) require a configured LLM endpoint. The `TriageService` provides rule-based fallback disposition for alerts when the agent is not configured.

</details>

<details>
<summary><strong>Where is project state stored?</strong></summary>

- **Agent definitions**: `.sentinelflow/plugins/agents/` by default
- **Skills**: `.sentinelflow/plugins/skills/` by default
- **Workflows**: `.sentinelflow/plugins/workflows/` by default
- **Runtime config** (persisted from WebUI): `.sentinelflow/runtime.json` by default
- **Task queue / approvals**: `.sentinelflow/sys_queue.db` (SQLite)
- **Run logs**: `.sentinelflow/run_logs/` by default
- **Environment defaults**: `.env` at project root (optional)

In the current project layout, the effective local workspace is the project-root `.sentinelflow/` directory.

</details>

<details>
<summary><strong>How do I define a fixed multi-step Agent Workflow?</strong></summary>

Create a `workflow.json` file under `.sentinelflow/plugins/workflows/<workflow-id>/` (default local workspace), or use the **Workflow Management** page in the WebUI. The Primary Agent uses structured LLM reasoning to select the best workflow for incoming alerts, or falls back to free ReAct if no workflow matches. In v1.1.0, `run_workflow` loads the fixed plan only; the Primary Agent still calls each Worker step itself and must provide a concrete task prompt for every step.

```json
{
  "id": "phishing-triage-v1",
  "name": "Phishing Alert Triage Workflow",
  "description": "Standard phishing alert triage with URL analysis and sender verification",
  "enabled": true,
  "scenarios": ["phishing", "suspicious_email"],
  "selection_keywords": ["phishing", "malicious_url", "suspicious_sender"],
  "steps": [
    { "agent": "url-analysis-worker", "name": "URL Analysis", "task_prompt": "Analyze the URLs in this alert for malicious indicators." },
    { "agent": "sender-reputation-worker", "name": "Sender Check", "task_prompt": "Check the sender reputation and domain age." },
    { "agent": "closure-worker", "name": "Close Alert", "task_prompt": "Based on the above findings, close the alert with appropriate disposition." }
  ]
}
```

</details>

## Release Notes

- **v1.2.1** — WebUI performance and layout polish, runtime approval/logging fixes, and a fully independent frontend stack.
  - **WebUI** — Stale-while-revalidate caching for alerts, skills, and agents; faster Alert Workbench / Skills / Agents list loading; collapsible Settings sections with collapsed summaries; collapsible `Surface` panels; conversation message collapse; Chinese task lifecycle labels on Overview; Task Center execution detail improvements; independent Markdown styles and frontend scaffolding.
  - **Runtime** — Fix stale approval ID reuse on secondary approval; order tool-call summaries by real execution timeline; improve skill argument filling stability; top-level thinking-model request adapter; refine run-log display extraction.
  - **Project** — Remove legacy third-party UI attribution files; keep runtime and business pages as SentinelFlow-native implementations.
- **v1.2.0** — RAG settings, run-log tracing, expanded context management for longer tasks, `input_schema` pre-execution validation, closure/completion logic tightening, SQLite/poller stability fixes, alert workbench and poll-store optimizations, thinking-model adapter, and broad WebUI polish.
- **v1.1.0** — Multi-agent execution integrity, source-aware alert tasks, workflow runner, prompt window management, run-log traceability, RAG settings, and thinking-model adapter.

## Documentation

This README is the current primary guide. More detailed user manuals for agent configuration, skill development, workflow authoring, API reference, and deployment are planned.

## Contributing

Issues and suggestions are welcome!

Before submitting PRs, please ensure:

- Python: `python -m pytest runtime/tests/` passes
- Keep runtime imports package-based under `sentinelflow.*`
- User-created skills, agents, and workflows belong under the local `.sentinelflow/plugins/` workspace, not inside package source modules

For new features, please open an Issue for discussion before submitting a PR.

## License

MIT License © SentinelFlow contributors

## Contact

- 📧 Email: ch1nfo@foxmail.com

---

<div align="center">

**⭐ If this project is helpful to you, please give it a Star! ⭐**

</div>
