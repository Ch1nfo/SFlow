import json
import re
from typing import Any
from dataclasses import asdict
from fastapi import APIRouter, HTTPException, Query
from sentinelflow.api.schemas import RagConfigRequest, RuntimeConfigRequest, AlertSourceParserGenerateRequest, AlertSourceParserPreviewRequest
from sentinelflow.config.runtime import _normalize_config, load_runtime_config, read_persisted_runtime_config, reset_runtime_config, save_runtime_config
from sentinelflow.api.deps import agent_service, branding, audit_service, polling_service, alert_parser_generator, _serialize, auto_execution_service, agent_run_log_service, PROJECT_ROOT
from sentinelflow.alerts.client import SOCAlertApiClient
from sentinelflow.alerts.parser_runtime import parse_jsonish
from sentinelflow.api.utils import VISIBLE_RUNTIME_OVERRIDE_KEYS, _mirror_project_file
from pathlib import Path

router = APIRouter(prefix="/api/sentinelflow")


def _serialize_alert_source(source) -> dict[str, Any]:
    return {
        "id": source.id,
        "name": source.name,
        "enabled": source.alert_source_enabled,
        "type": source.alert_source_type,
        "url": source.alert_source_url,
        "method": source.alert_source_method,
        "headers": source.alert_source_headers,
        "query": source.alert_source_query,
        "body": source.alert_source_body,
        "timeout": source.alert_source_timeout,
        "sample_payload": source.alert_source_sample_payload,
        "parser_rule": source.alert_parser_rule,
        "parser_configured": bool(source.alert_parser_rule),
        "script_code": source.alert_script_code,
        "script_timeout": source.alert_script_timeout,
        "auto_execute_enabled": source.auto_execute_enabled,
        "poll_interval_seconds": str(source.poll_interval_seconds),
        "failed_retry_interval_seconds": str(source.failed_retry_interval_seconds),
        "analysis_prompt": source.analysis_prompt,
    }

@router.get("/health")
def health() -> dict[str, Any]:
    runtime_config = load_runtime_config()
    agent_available, _ = agent_service.is_available()
    return {
        "name": branding.api_title,
        "status": "ok",
        "demo_mode": runtime_config.demo_mode,
        "agent_enabled": runtime_config.agent_enabled,
        "agent_configured": agent_service.is_configured(),
        "agent_available": agent_available,
    }


@router.get("/audit/events")
def list_audit_events() -> dict[str, Any]:
    return {"events": [_serialize(event) for event in audit_service.list_events()]}


@router.get("/runtime/settings")
def runtime_settings() -> dict[str, Any]:
    runtime_config = load_runtime_config()
    persisted_config = {
        key: value
        for key, value in read_persisted_runtime_config().items()
        if key in VISIBLE_RUNTIME_OVERRIDE_KEYS
    }
    agent_available, agent_reason = agent_service.is_available()
    alert_sources = [_serialize_alert_source(source) for source in runtime_config.alert_sources]
    primary_source = runtime_config.alert_sources[0]
    return {
        "branding": {
            "product_name": branding.product_name,
            "console_title": branding.console_title,
        },
        "runtime": {
            "poll_interval_seconds": str(runtime_config.poll_interval_seconds),
            "failed_retry_interval_seconds": str(runtime_config.failed_retry_interval_seconds),
            "workflow_engine": branding.workflow_engine_label,
            "agent_enabled": runtime_config.agent_enabled,
            "auto_execute_enabled": runtime_config.auto_execute_enabled,
            "weekly_alert_cleanup_enabled": runtime_config.weekly_alert_cleanup_enabled,
            "run_log_retention_days": runtime_config.run_log_retention_days,
            "full_report_format_skill": runtime_config.full_report_format_skill,
        },
        "llm": {
            "api_base_url": runtime_config.llm_api_base_url,
            "api_key": "",
            "api_key_configured": bool(runtime_config.llm_api_key),
            "model": runtime_config.llm_model,
            "temperature": runtime_config.llm_temperature,
            "timeout": runtime_config.llm_timeout,
            "thinking_adapter_enabled": runtime_config.llm_thinking_adapter_enabled,
            "agent_configured": agent_service.is_configured(),
            "agent_available": agent_available,
            "agent_unavailable_reason": agent_reason or "",
        },
        "alert_source": {
            **_serialize_alert_source(primary_source),
        },
        "alert_sources": alert_sources,
        "default_alert_source_id": primary_source.id,
        "features": {
            "natural_language_dispatch": True,
            "alert_polling": runtime_config.alert_source_enabled,
            "hybrid_skills": True,
            "audit_timeline": True,
            "agent_runtime": True,
        },
        "persisted_overrides": persisted_config,
        "rag": {
            "enabled": runtime_config.rag.enabled,
            "knowledge_id": runtime_config.rag.knowledge_id,
            "api_key": "",
            "api_key_configured": bool(runtime_config.rag.api_key),
            "top_k": runtime_config.rag.top_k,
            "similarity_threshold": runtime_config.rag.similarity_threshold,
            "retrieve_strategy": runtime_config.rag.retrieve_strategy,
            "enable_rerank_model": runtime_config.rag.enable_rerank_model,
            "rerank_model": runtime_config.rag.rerank_model,
        },
    }

@router.post("/runtime/settings")
def save_settings(payload: RuntimeConfigRequest) -> dict[str, Any]:
    current = load_runtime_config()
    next_payload = payload.to_payload()
    if not payload.llm_api_key:
        next_payload["llm_api_key"] = current.llm_api_key
    save_runtime_config(next_payload)
    polling_service.refresh_schedule()
    auto_execution_service.apply_persisted_state()
    return runtime_settings()


@router.post("/runtime/settings/reset")
def reset_settings() -> dict[str, Any]:
    reset_runtime_config()
    polling_service.refresh_schedule()
    auto_execution_service.apply_persisted_state()
    return runtime_settings()


@router.get("/runtime/run-logs")
def list_run_log_dates() -> dict[str, Any]:
    return {
        "retention_days": agent_run_log_service.retention_days(),
        "dates": agent_run_log_service.list_dates(),
    }


@router.post("/runtime/run-logs/settings")
def save_run_log_settings(payload: dict[str, Any]) -> dict[str, Any]:
    retention_days = agent_run_log_service.set_retention_days(int(payload.get("retentionDays") or payload.get("retention_days") or 1))
    return {
        "retention_days": retention_days,
        "dates": agent_run_log_service.list_dates(),
    }


@router.get("/runtime/run-logs/{log_date}/alerts")
def list_run_log_alerts(log_date: str) -> dict[str, Any]:
    return {
        "date": log_date,
        "alerts": agent_run_log_service.list_alerts(log_date),
    }


@router.get("/runtime/run-logs/{log_date}/alerts/{log_id}")
def read_run_log(
    log_date: str,
    log_id: str,
    limit: int = Query(500, ge=1, le=5000),
    tail: bool = True,
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return agent_run_log_service.read_log(log_date, log_id, limit=limit, tail=tail, offset=offset)


@router.post("/runtime/settings/alert-source/test-fetch")
def test_alert_source_fetch(payload: RuntimeConfigRequest) -> dict[str, Any]:
    current = load_runtime_config()
    payload_values = payload.to_payload()
    merged_values = {
        **asdict(current),
        **payload_values,
    }
    if "alert_sources" not in payload_values:
        merged_values["alert_sources"] = [
            {
                "id": current.alert_sources[0].id if current.alert_sources else "default",
                "name": current.alert_sources[0].name if current.alert_sources else "默认告警源",
                "alert_source_enabled": merged_values.get("alert_source_enabled"),
                "alert_source_type": merged_values.get("alert_source_type"),
                "alert_source_url": merged_values.get("alert_source_url"),
                "alert_source_method": merged_values.get("alert_source_method"),
                "alert_source_headers": merged_values.get("alert_source_headers"),
                "alert_source_query": merged_values.get("alert_source_query"),
                "alert_source_body": merged_values.get("alert_source_body"),
                "alert_source_timeout": merged_values.get("alert_source_timeout"),
                "alert_source_sample_payload": merged_values.get("alert_source_sample_payload"),
                "alert_parser_rule": merged_values.get("alert_parser_rule"),
                "alert_script_code": merged_values.get("alert_script_code"),
                "alert_script_timeout": merged_values.get("alert_script_timeout"),
                "auto_execute_enabled": merged_values.get("auto_execute_enabled"),
                "poll_interval_seconds": merged_values.get("poll_interval_seconds"),
                "failed_retry_interval_seconds": merged_values.get("failed_retry_interval_seconds"),
            }
        ]
    temp_config = _normalize_config(merged_values)
    temp_source = temp_config.alert_sources[0]
    client = SOCAlertApiClient()
    if temp_source.alert_source_type == "script":
        result = client.fetch_script_alerts(temp_source)
    else:
        result = client.fetch_raw_alert_payload(temp_source, temp_config)
    if "error" in result:
        raise HTTPException(status_code=400, detail=str(result["error"]))
    return result


@router.post("/runtime/settings/alert-source/generate-parser")
def generate_alert_source_parser(payload: AlertSourceParserGenerateRequest) -> dict[str, Any]:
    try:
        generated = alert_parser_generator.generate(payload.sample_payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raw_sample = parse_jsonish(payload.sample_payload)
    preview = polling_service.client.preview_parse(raw_sample, generated.get("parser_rule"))
    return {
        **generated,
        "preview": preview,
    }


@router.post("/runtime/settings/alert-source/test-parse")
def test_alert_source_parse(payload: AlertSourceParserPreviewRequest) -> dict[str, Any]:
    raw_sample = parse_jsonish(payload.sample_payload)
    if raw_sample is None:
        raise HTTPException(status_code=400, detail="告警样本不是合法 JSON。")
    parser_rule = payload.parser_rule or load_runtime_config().alert_parser_rule
    if not parser_rule:
        raise HTTPException(status_code=400, detail="当前还没有可用的告警解析规则。")
    preview = polling_service.client.preview_parse(raw_sample, parser_rule)
    if preview.get("error"):
        raise HTTPException(status_code=400, detail=str(preview["error"]))
    return preview


# ── RAG 配置 ──────────────────────────────────────────────────────────────────

RAG_SKILL_DIR = PROJECT_ROOT / ".sentinelflow" / "plugins" / "skills" / "rag"
RAG_MAIN_PATH = RAG_SKILL_DIR / "main.py"
RAG_SKILL_MD_PATH = RAG_SKILL_DIR / "SKILL.md"


def _sync_rag_skill_md(config) -> None:
    """根据 RAG 配置同步 SKILL.md body 中的参数说明。"""
    if not RAG_SKILL_MD_PATH.is_file():
        return

    text = RAG_SKILL_MD_PATH.read_text(encoding="utf-8")

    # body: api-key 值
    text = re.sub(
        r"(`api-key`:\s*`)[^`]*(`)",
        r"\g<1>" + config.api_key + r"\g<2>",
        text,
    )

    # body: knowledgeId 值
    text = re.sub(
        r"(`knowledgeId`:\s*`\"?)[^\"`]*(\"?\"?`)",
        r"\g<1>" + config.knowledge_id + r"\g<2>",
        text,
    )

    # body: topK 值
    text = re.sub(
        r"(`topK`:\s*`)\d+(`)",
        r"\g<1>" + str(config.top_k) + r"\g<2>",
        text,
    )

    # body: similarityThreshold 值（包括 `>= 0.8` 这种上下文引用）
    text = re.sub(
        r"(`similarityThreshold`:\s*`)[\d.]+(`)",
        r"\g<1>" + str(config.similarity_threshold) + r"\g<2>",
        text,
    )
    text = re.sub(
        r"(相似度\s*>=\s*)[\d.]+",
        r"\g<1>" + str(config.similarity_threshold),
        text,
    )

    # body: retrieveStrategy 值
    text = re.sub(
        r"(`retrieveStrategy`:\s*`)\d+(`)",
        r"\g<1>" + str(config.retrieve_strategy) + r"\g<2>",
        text,
    )

    # body: enableRerankModel 值
    rerank_str = "true" if config.enable_rerank_model else "false"
    text = re.sub(
        r"(`enableRerankModel`:\s*`)(?:true|false)(`)",
        r"\g<1>" + rerank_str + r"\g<2>",
        text,
    )

    # body: rerankModel 值
    text = re.sub(
        r"(`rerankModel`:\s*`\"?)[^\"`]*(\"?\"?`)",
        r"\g<1>" + config.rerank_model + r"\g<2>",
        text,
    )

    _mirror_project_file(
        Path(".sentinelflow") / "plugins" / "skills" / "rag" / "SKILL.md",
        text,
    )


AGENT_YAML_PATH = PROJECT_ROOT / ".sentinelflow" / "plugins" / "agents" / "system-primary" / "agent.yaml"


def _sync_agent_rag(enabled: bool) -> None:
    """开启/关闭 RAG 时从 system-primary agent.yaml 的列表中添加/移除 rag。"""
    if not AGENT_YAML_PATH.is_file():
        return

    text = AGENT_YAML_PATH.read_text(encoding="utf-8")

    for section in ("skills:", "hybrid_doc_allowlist:", "exec_skill_allowlist:"):
        escaped = re.escape(section)

        def _replace_block(m: re.Match) -> str:
            block = m.group(1)
            block = block.replace("  - rag\n", "")
            if enabled:
                if not block.endswith("\n"):
                    block += "\n"
                block += "  - rag\n"
            return block

        text = re.sub(
            rf"({escaped}\n(?:(?:  - .+)\n)*)",
            _replace_block,
            text,
            flags=re.MULTILINE,
        )

    _mirror_project_file(
        Path(".sentinelflow") / "plugins" / "agents" / "system-primary" / "agent.yaml",
        text,
    )


def _sync_rag_main_py(config) -> None:
    """根据 RAG 配置更新 rag skill 的 main.py 中的 API_KEY 和 FIXED_PARAMS。"""
    if not RAG_MAIN_PATH.is_file():
        return

    code = RAG_MAIN_PATH.read_text(encoding="utf-8")

    code = re.sub(
        r'^API_KEY\s*=\s*"[^"]*"',
        f'API_KEY = "{config.api_key}"',
        code,
        flags=re.MULTILINE,
    )

    code = re.sub(
        r'"knowledgeId":\s*"[^"]*"',
        f'"knowledgeId": "{config.knowledge_id}"',
        code,
    )

    code = re.sub(
        r'"topK":\s*\d+',
        f'"topK": {config.top_k}',
        code,
    )

    code = re.sub(
        r'"similarityThreshold":\s*[\d.]+',
        f'"similarityThreshold": {config.similarity_threshold}',
        code,
    )

    code = re.sub(
        r'"retrieveStrategy":\s*\d+',
        f'"retrieveStrategy": {config.retrieve_strategy}',
        code,
    )

    code = re.sub(
        r'"enableRerankModel":\s*(?:True|False)',
        f'"enableRerankModel": {config.enable_rerank_model}',
        code,
    )

    code = re.sub(
        r'"rerankModel":\s*"[^"]*"',
        f'"rerankModel": "{config.rerank_model}"',
        code,
    )

    _mirror_project_file(
        Path(".sentinelflow") / "plugins" / "skills" / "rag" / "main.py",
        code,
    )


def _parse_rag_file_value(code: str, key: str) -> Any:
    """从 main.py 的 FIXED_PARAMS 字典中提取指定 key 的值。"""
    pattern = rf'"{key}":\s*([^,\n}}]+)'
    m = re.search(pattern, code)
    if not m:
        return None
    raw = m.group(1).strip()
    if raw in ("True", "False"):
        return raw == "True"
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1]
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return raw


def _read_rag_from_files() -> dict[str, Any]:
    """直接从 main.py 和 SKILL.md 读取 RAG 实际配置，以文件内容为准。"""
    result: dict[str, Any] = {
        "enabled": True,
        "knowledge_id": "",
        "api_key": "",
        "api_key_configured": False,
        "top_k": 5,
        "similarity_threshold": 0.8,
        "retrieve_strategy": 3,
        "enable_rerank_model": True,
        "rerank_model": "",
    }

    # 从 agent.yaml 判断 enabled 状态：rag 是否在 skills 列表中
    if AGENT_YAML_PATH.is_file():
        agent_text = AGENT_YAML_PATH.read_text(encoding="utf-8")
        result["enabled"] = bool(re.search(r"^skills:\n(?:  - .+\n)*  - rag\n", agent_text, flags=re.MULTILINE))

    # 从 main.py 读取各参数
    if RAG_MAIN_PATH.is_file():
        code = RAG_MAIN_PATH.read_text(encoding="utf-8")

        # API_KEY
        m = re.search(r'^API_KEY\s*=\s*"([^"]*)"', code, flags=re.MULTILINE)
        if m:
            api_key = m.group(1)
            result["api_key_configured"] = bool(api_key)

        # FIXED_PARAMS 中的各字段
        for key, field in [
            ("knowledgeId", "knowledge_id"),
            ("topK", "top_k"),
            ("similarityThreshold", "similarity_threshold"),
            ("retrieveStrategy", "retrieve_strategy"),
            ("enableRerankModel", "enable_rerank_model"),
            ("rerankModel", "rerank_model"),
        ]:
            val = _parse_rag_file_value(code, key)
            if val is not None:
                result[field] = val

    return result


@router.get("/runtime/rag-settings")
def rag_settings() -> dict[str, Any]:
    return _read_rag_from_files()


@router.post("/runtime/rag-settings")
def save_rag_settings(payload: RagConfigRequest) -> dict[str, Any]:
    current = load_runtime_config().rag
    merged = {
        "rag_enabled": payload.enabled if payload.enabled is not None else current.enabled,
        "rag_knowledge_id": payload.knowledge_id if payload.knowledge_id is not None else current.knowledge_id,
        "rag_api_key": payload.api_key if payload.api_key is not None else current.api_key,
        "rag_top_k": payload.top_k if payload.top_k is not None else current.top_k,
        "rag_similarity_threshold": payload.similarity_threshold if payload.similarity_threshold is not None else current.similarity_threshold,
        "rag_retrieve_strategy": payload.retrieve_strategy if payload.retrieve_strategy is not None else current.retrieve_strategy,
        "rag_enable_rerank_model": payload.enable_rerank_model if payload.enable_rerank_model is not None else current.enable_rerank_model,
        "rag_rerank_model": payload.rerank_model if payload.rerank_model is not None else current.rerank_model,
    }
    new_config = save_runtime_config(merged)
    _sync_agent_rag(new_config.rag.enabled)
    _sync_rag_skill_md(new_config.rag)
    _sync_rag_main_py(new_config.rag)
    return rag_settings()
