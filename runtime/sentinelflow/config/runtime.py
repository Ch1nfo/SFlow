from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / ".sentinelflow"
CONFIG_PATH = CONFIG_DIR / "runtime.json"
ALERT_SOURCE_SCRIPT_DIR = CONFIG_DIR / "alert_sources"
ALERT_SOURCE_SCRIPT_PATH = ALERT_SOURCE_SCRIPT_DIR / "custom_fetch.py"
_CONFIG_LOCK = threading.Lock()
DEFAULT_ALERT_SOURCE_ID = "default"


def _read_bool_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _read_env_bool(name: str, default: bool = False) -> bool:
    return _read_bool_value(os.getenv(name), default)


def _normalize_alert_source_type(value: Any) -> str:
    normalized = str(value or "api").strip().lower()
    return normalized if normalized in {"api", "script"} else "api"


@dataclass(frozen=True, slots=True)
class AlertSourceConfig:
    id: str
    name: str
    alert_source_enabled: bool
    alert_source_type: str
    alert_source_url: str
    alert_source_method: str
    alert_source_headers: str
    alert_source_query: str
    alert_source_body: str
    alert_source_timeout: int
    alert_source_sample_payload: str
    alert_parser_rule: dict[str, Any]
    alert_script_code: str
    alert_script_timeout: int
    auto_execute_enabled: bool
    poll_interval_seconds: int
    failed_retry_interval_seconds: int
    analysis_prompt: str = ""

    @property
    def enabled(self) -> bool:
        return self.alert_source_enabled

    @property
    def type(self) -> str:
        return self.alert_source_type


@dataclass(frozen=True, slots=True)
class RagConfig:
    enabled: bool
    knowledge_id: str
    api_key: str
    top_k: int
    similarity_threshold: float
    retrieve_strategy: int
    enable_rerank_model: bool
    rerank_model: str


@dataclass(frozen=True, slots=True)
class SentinelFlowRuntimeConfig:
    demo_mode: bool
    demo_fallback: bool
    verify_ssl: bool
    agent_enabled: bool
    llm_api_base_url: str
    llm_api_key: str
    llm_model: str
    llm_temperature: float
    llm_timeout: int
    llm_thinking_adapter_enabled: bool
    llm_thinking: str
    weekly_alert_cleanup_enabled: bool
    run_log_retention_days: int
    full_report_format_skill: str
    alert_source_enabled: bool
    alert_source_type: str
    alert_source_url: str
    alert_source_method: str
    alert_source_headers: str
    alert_source_query: str
    alert_source_body: str
    alert_source_timeout: int
    alert_source_sample_payload: str
    alert_parser_rule: dict[str, Any]
    alert_script_code: str
    alert_script_timeout: int
    auto_execute_enabled: bool
    poll_interval_seconds: int
    failed_retry_interval_seconds: int
    alert_sources: list[AlertSourceConfig]
    rag: RagConfig


def _default_values() -> dict[str, Any]:
    return {
        "demo_mode": _read_env_bool("SENTINELFLOW_DEMO_MODE", False),
        "demo_fallback": _read_env_bool("SENTINELFLOW_DEMO_FALLBACK", True),
        "verify_ssl": _read_env_bool("SENTINELFLOW_VERIFY_SSL", True),
        "agent_enabled": _read_env_bool("SENTINELFLOW_AGENT_ENABLED", True),
        "llm_api_base_url": os.getenv("SENTINELFLOW_LLM_API_BASE_URL", "https://api.openai.com/v1").strip(),
        "llm_api_key": os.getenv("SENTINELFLOW_LLM_API_KEY", "").strip(),
        "llm_model": os.getenv("SENTINELFLOW_LLM_MODEL", "").strip(),
        "llm_temperature": float(os.getenv("SENTINELFLOW_LLM_TEMPERATURE", "0")),
        "llm_timeout": int(os.getenv("SENTINELFLOW_LLM_TIMEOUT", "60")),
        "llm_thinking_adapter_enabled": _read_env_bool("SENTINELFLOW_LLM_THINKING_ADAPTER_ENABLED", False),
        "llm_thinking": os.getenv("SENTINELFLOW_LLM_THINKING", "disabled").strip().lower() or "disabled",
        "weekly_alert_cleanup_enabled": _read_env_bool("SENTINELFLOW_WEEKLY_ALERT_CLEANUP_ENABLED", False),
        "run_log_retention_days": int(os.getenv("SENTINELFLOW_RUN_LOG_RETENTION_DAYS", "1")),
        "full_report_format_skill": os.getenv("SENTINELFLOW_FULL_REPORT_FORMAT_SKILL", "output-report").strip() or "output-report",
        "alert_source_enabled": _read_env_bool("SENTINELFLOW_ALERT_SOURCE_ENABLED", False),
        "alert_source_type": os.getenv("SENTINELFLOW_ALERT_SOURCE_TYPE", "api").strip().lower() or "api",
        "alert_source_url": os.getenv("SENTINELFLOW_ALERT_SOURCE_URL", "").strip(),
        "alert_source_method": os.getenv("SENTINELFLOW_ALERT_SOURCE_METHOD", "GET").strip().upper(),
        "alert_source_headers": os.getenv("SENTINELFLOW_ALERT_SOURCE_HEADERS", "").strip(),
        "alert_source_query": os.getenv("SENTINELFLOW_ALERT_SOURCE_QUERY", "").strip(),
        "alert_source_body": os.getenv("SENTINELFLOW_ALERT_SOURCE_BODY", "").strip(),
        "alert_source_timeout": int(os.getenv("SENTINELFLOW_ALERT_SOURCE_TIMEOUT", "15")),
        "alert_source_sample_payload": os.getenv("SENTINELFLOW_ALERT_SOURCE_SAMPLE_PAYLOAD", "").strip(),
        "alert_parser_rule": {},
        "alert_script_code": os.getenv("SENTINELFLOW_ALERT_SCRIPT_CODE", "").strip(),
        "alert_script_timeout": int(os.getenv("SENTINELFLOW_ALERT_SCRIPT_TIMEOUT", "30")),
        "auto_execute_enabled": _read_env_bool("SENTINELFLOW_AUTO_EXECUTE_ENABLED", False),
        "poll_interval_seconds": int(os.getenv("SENTINELFLOW_POLL_INTERVAL_SECONDS", "60")),
        "failed_retry_interval_seconds": int(os.getenv("SENTINELFLOW_FAILED_RETRY_INTERVAL_SECONDS", "0")),
        "alert_sources": [],
        "rag_enabled": _read_env_bool("SENTINELFLOW_RAG_ENABLED", True),
        "rag_knowledge_id": os.getenv("SENTINELFLOW_RAG_KNOWLEDGE_ID", "2875eb66-b4e7-4d16-a4f6-e37c774b8cc6").strip(),
        "rag_api_key": os.getenv("SENTINELFLOW_RAG_API_KEY", "sk-a492d612394b411b877164f4053aae69").strip(),
        "rag_top_k": int(os.getenv("SENTINELFLOW_RAG_TOP_K", "5")),
        "rag_similarity_threshold": float(os.getenv("SENTINELFLOW_RAG_SIMILARITY_THRESHOLD", "0.8")),
        "rag_retrieve_strategy": int(os.getenv("SENTINELFLOW_RAG_RETRIEVE_STRATEGY", "3")),
        "rag_enable_rerank_model": _read_env_bool("SENTINELFLOW_RAG_ENABLE_RERANK_MODEL", True),
        "rag_rerank_model": os.getenv("SENTINELFLOW_RAG_RERANK_MODEL", "bge-reranker-base").strip(),
    }


def _value_from_any(values: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in values:
            return values[key]
    return default


def _normalize_alert_source(values: dict[str, Any], index: int = 0) -> AlertSourceConfig:
    source_id = str(_value_from_any(values, "id", "source_id", "sourceId", default="")).strip()
    if not source_id:
        source_id = DEFAULT_ALERT_SOURCE_ID if index == 0 else f"source-{uuid4().hex[:8]}"
    name = str(_value_from_any(values, "name", "source_name", "sourceName", default="")).strip()
    if not name:
        name = "默认告警源" if index == 0 else f"告警源 {index + 1}"
    return AlertSourceConfig(
        id=source_id,
        name=name,
        alert_source_enabled=_read_bool_value(_value_from_any(values, "alert_source_enabled", "enabled", default=False), False),
        alert_source_type=_normalize_alert_source_type(_value_from_any(values, "alert_source_type", "type", default="api")),
        alert_source_url=str(_value_from_any(values, "alert_source_url", "url", default="")).strip(),
        alert_source_method=str(_value_from_any(values, "alert_source_method", "method", default="GET")).strip().upper() or "GET",
        alert_source_headers=str(_value_from_any(values, "alert_source_headers", "headers", default="")).strip(),
        alert_source_query=str(_value_from_any(values, "alert_source_query", "query", default="")).strip(),
        alert_source_body=str(_value_from_any(values, "alert_source_body", "body", default="")).strip(),
        alert_source_timeout=int(_value_from_any(values, "alert_source_timeout", "timeout", default=15) or 15),
        alert_source_sample_payload=str(_value_from_any(values, "alert_source_sample_payload", "sample_payload", "samplePayload", default="")).strip(),
        alert_parser_rule=(
            _value_from_any(values, "alert_parser_rule", "parser_rule", "parserRule", default={})
            if isinstance(_value_from_any(values, "alert_parser_rule", "parser_rule", "parserRule", default={}), dict)
            else {}
        ),
        alert_script_code=str(_value_from_any(values, "alert_script_code", "script_code", "scriptCode", default="")),
        alert_script_timeout=int(_value_from_any(values, "alert_script_timeout", "script_timeout", "scriptTimeout", default=30) or 30),
        auto_execute_enabled=_read_bool_value(_value_from_any(values, "auto_execute_enabled", "autoExecuteEnabled", default=False), False),
        poll_interval_seconds=max(int(_value_from_any(values, "poll_interval_seconds", "pollIntervalSeconds", default=60) or 0), 0),
        failed_retry_interval_seconds=max(int(_value_from_any(values, "failed_retry_interval_seconds", "failedRetryIntervalSeconds", default=0) or 0), 0),
        analysis_prompt=str(_value_from_any(values, "analysis_prompt", "analysisPrompt", default="")),
    )


def _legacy_source_values(values: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": DEFAULT_ALERT_SOURCE_ID,
        "name": "默认告警源",
        "alert_source_enabled": values.get("alert_source_enabled"),
        "alert_source_type": values.get("alert_source_type"),
        "alert_source_url": values.get("alert_source_url"),
        "alert_source_method": values.get("alert_source_method"),
        "alert_source_headers": values.get("alert_source_headers"),
        "alert_source_query": values.get("alert_source_query"),
        "alert_source_body": values.get("alert_source_body"),
        "alert_source_timeout": values.get("alert_source_timeout"),
        "alert_source_sample_payload": values.get("alert_source_sample_payload"),
        "alert_parser_rule": values.get("alert_parser_rule"),
        "alert_script_code": values.get("alert_script_code"),
        "alert_script_timeout": values.get("alert_script_timeout"),
        "auto_execute_enabled": values.get("auto_execute_enabled"),
        "poll_interval_seconds": values.get("poll_interval_seconds"),
        "failed_retry_interval_seconds": values.get("failed_retry_interval_seconds"),
        "analysis_prompt": "",
    }


def _normalize_alert_sources(values: dict[str, Any]) -> list[AlertSourceConfig]:
    raw_sources = values.get("alert_sources")
    sources: list[AlertSourceConfig] = []
    if isinstance(raw_sources, list):
        seen: set[str] = set()
        for index, item in enumerate(raw_sources):
            if not isinstance(item, dict):
                continue
            source = _normalize_alert_source(item, index)
            source_id = source.id
            if source_id in seen:
                source = _normalize_alert_source({**asdict(source), "id": f"{source_id}-{uuid4().hex[:6]}"}, index)
            seen.add(source.id)
            sources.append(source)
    if sources:
        return sources
    return [_normalize_alert_source(_legacy_source_values(values), 0)]


def _normalize_config(values: dict[str, Any]) -> SentinelFlowRuntimeConfig:
    alert_sources = _normalize_alert_sources(values)
    primary_source = alert_sources[0]
    return SentinelFlowRuntimeConfig(
        demo_mode=_read_bool_value(values.get("demo_mode"), False),
        demo_fallback=_read_bool_value(values.get("demo_fallback"), True),
        verify_ssl=_read_bool_value(values.get("verify_ssl"), True),
        agent_enabled=_read_bool_value(values.get("agent_enabled"), True),
        llm_api_base_url=str(values.get("llm_api_base_url", "")).strip() or "https://api.openai.com/v1",
        llm_api_key=str(values.get("llm_api_key", "")).strip(),
        llm_model=str(values.get("llm_model", "")).strip(),
        llm_temperature=float(values.get("llm_temperature", 0)),
        llm_timeout=int(values.get("llm_timeout", 60)),
        llm_thinking_adapter_enabled=_read_bool_value(values.get("llm_thinking_adapter_enabled"), False),
        llm_thinking=_normalize_llm_thinking(values.get("llm_thinking", "disabled")),
        weekly_alert_cleanup_enabled=_read_bool_value(values.get("weekly_alert_cleanup_enabled"), False),
        run_log_retention_days=max(int(values.get("run_log_retention_days", 1) or 1), 1),
        full_report_format_skill=str(values.get("full_report_format_skill", "output-report") or "output-report").strip() or "output-report",
        alert_source_enabled=primary_source.alert_source_enabled,
        alert_source_type=primary_source.alert_source_type,
        alert_source_url=primary_source.alert_source_url,
        alert_source_method=primary_source.alert_source_method,
        alert_source_headers=primary_source.alert_source_headers,
        alert_source_query=primary_source.alert_source_query,
        alert_source_body=primary_source.alert_source_body,
        alert_source_timeout=primary_source.alert_source_timeout,
        alert_source_sample_payload=primary_source.alert_source_sample_payload,
        alert_parser_rule=primary_source.alert_parser_rule,
        alert_script_code=primary_source.alert_script_code,
        alert_script_timeout=primary_source.alert_script_timeout,
        auto_execute_enabled=primary_source.auto_execute_enabled,
        poll_interval_seconds=primary_source.poll_interval_seconds,
        failed_retry_interval_seconds=primary_source.failed_retry_interval_seconds,
        alert_sources=alert_sources,
        rag=RagConfig(
            enabled=_read_bool_value(values.get("rag_enabled"), True),
            knowledge_id=str(values.get("rag_knowledge_id", "2875eb66-b4e7-4d16-a4f6-e37c774b8cc6")).strip(),
            api_key=str(values.get("rag_api_key", "sk-a492d612394b411b877164f4053aae69")).strip(),
            top_k=int(values.get("rag_top_k", 5)),
            similarity_threshold=float(values.get("rag_similarity_threshold", 0.8)),
            retrieve_strategy=int(values.get("rag_retrieve_strategy", 3)),
            enable_rerank_model=_read_bool_value(values.get("rag_enable_rerank_model"), True),
            rerank_model=str(values.get("rag_rerank_model", "bge-reranker-base")).strip(),
        ),
    )


def read_persisted_runtime_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def load_runtime_config() -> SentinelFlowRuntimeConfig:
    merged = _default_values()
    merged.update(read_persisted_runtime_config())
    return _normalize_config(merged)


def save_runtime_config(values: dict[str, Any]) -> SentinelFlowRuntimeConfig:
    with _CONFIG_LOCK:
        current = _default_values()
        current.update(read_persisted_runtime_config())
        current.update(values)
        normalized = _normalize_config(current)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(normalized), ensure_ascii=False, indent=2)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=CONFIG_DIR, prefix="runtime-", suffix=".json", delete=False) as handle:
                handle.write(payload)
                handle.flush()
                temp_path = Path(handle.name)
            temp_path.replace(CONFIG_PATH)
        finally:
            if temp_path is not None and temp_path.exists() and temp_path != CONFIG_PATH:
                temp_path.unlink(missing_ok=True)
        return normalized


def reset_runtime_config() -> SentinelFlowRuntimeConfig:
    with _CONFIG_LOCK:
        if CONFIG_PATH.exists():
            CONFIG_PATH.unlink()
    return load_runtime_config()


def should_use_demo_mode() -> bool:
    return load_runtime_config().demo_mode


def _normalize_llm_thinking(value: Any) -> str:
    normalized = str(value or "disabled").strip().lower()
    return normalized if normalized in {"enabled", "disabled"} else "disabled"


def build_llm_client_kwargs(config: SentinelFlowRuntimeConfig, *, temperature: float | None = None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": config.llm_model,
        "api_key": config.llm_api_key,
        "base_url": config.llm_api_base_url,
        "temperature": config.llm_temperature if temperature is None else temperature,
        "timeout": config.llm_timeout,
    }
    if _read_bool_value(getattr(config, "llm_thinking_adapter_enabled", False), False):
        kwargs["extra_body"] = {
            "thinking": {"type": "disabled"},
        }
    return kwargs
