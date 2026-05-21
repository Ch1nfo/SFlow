from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field

class CommandDispatchRequest(BaseModel):
    command_text: str = Field(..., alias="commandText")
    history: list[dict[str, str]] | None = None
    agent_name: str | None = Field(default=None, alias="agentName")


class AlertActionRequest(BaseModel):
    action: str
    task: dict[str, Any] | None = None
    alert: dict[str, Any] | None = None
    source_id: str | None = Field(default=None, alias="sourceId")


class RuntimeConfigRequest(BaseModel):
    poll_interval_seconds: int | None = Field(default=None, alias="pollIntervalSeconds")
    failed_retry_interval_seconds: int | None = Field(default=None, alias="failedRetryIntervalSeconds")
    demo_mode: bool | None = Field(default=None, alias="demoMode")
    demo_fallback: bool | None = Field(default=None, alias="demoFallback")
    verify_ssl: bool | None = Field(default=None, alias="verifySsl")
    agent_enabled: bool | None = Field(default=None, alias="agentEnabled")
    llm_api_base_url: str | None = Field(default=None, alias="llmApiBaseUrl")
    llm_api_key: str | None = Field(default=None, alias="llmApiKey")
    llm_model: str | None = Field(default=None, alias="llmModel")
    llm_temperature: float | None = Field(default=None, alias="llmTemperature")
    llm_timeout: int | None = Field(default=None, alias="llmTimeout")
    llm_thinking_adapter_enabled: bool | None = Field(default=None, alias="llmThinkingAdapterEnabled")
    weekly_alert_cleanup_enabled: bool | None = Field(default=None, alias="weeklyAlertCleanupEnabled")
    run_log_retention_days: int | None = Field(default=None, alias="runLogRetentionDays")
    alert_source_enabled: bool | None = Field(default=None, alias="alertSourceEnabled")
    alert_source_type: str | None = Field(default=None, alias="alertSourceType")
    alert_source_url: str | None = Field(default=None, alias="alertSourceUrl")
    alert_source_method: str | None = Field(default=None, alias="alertSourceMethod")
    alert_source_headers: str | None = Field(default=None, alias="alertSourceHeaders")
    alert_source_query: str | None = Field(default=None, alias="alertSourceQuery")
    alert_source_body: str | None = Field(default=None, alias="alertSourceBody")
    alert_source_timeout: int | None = Field(default=None, alias="alertSourceTimeout")
    alert_source_sample_payload: str | None = Field(default=None, alias="alertSourceSamplePayload")
    alert_parser_rule: dict[str, Any] | None = Field(default=None, alias="alertParserRule")
    alert_script_code: str | None = Field(default=None, alias="alertScriptCode")
    alert_script_timeout: int | None = Field(default=None, alias="alertScriptTimeout")
    auto_execute_enabled: bool | None = Field(default=None, alias="autoExecuteEnabled")
    alert_sources: list[dict[str, Any]] | None = Field(default=None, alias="alertSources")

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "poll_interval_seconds": self.poll_interval_seconds,
            "failed_retry_interval_seconds": self.failed_retry_interval_seconds,
            "demo_mode": self.demo_mode,
            "demo_fallback": self.demo_fallback,
            "verify_ssl": self.verify_ssl,
            "agent_enabled": self.agent_enabled,
            "llm_api_base_url": self.llm_api_base_url,
            "llm_api_key": self.llm_api_key,
            "llm_model": self.llm_model,
            "llm_temperature": self.llm_temperature,
            "llm_timeout": self.llm_timeout,
            "llm_thinking_adapter_enabled": self.llm_thinking_adapter_enabled,
            "weekly_alert_cleanup_enabled": self.weekly_alert_cleanup_enabled,
            "run_log_retention_days": self.run_log_retention_days,
            "alert_source_enabled": self.alert_source_enabled,
            "alert_source_type": self.alert_source_type,
            "alert_source_url": self.alert_source_url,
            "alert_source_method": self.alert_source_method,
            "alert_source_headers": self.alert_source_headers,
            "alert_source_query": self.alert_source_query,
            "alert_source_body": self.alert_source_body,
            "alert_source_timeout": self.alert_source_timeout,
            "alert_source_sample_payload": self.alert_source_sample_payload,
            "alert_parser_rule": self.alert_parser_rule if isinstance(self.alert_parser_rule, dict) else None,
            "alert_script_code": self.alert_script_code,
            "alert_script_timeout": self.alert_script_timeout,
            "auto_execute_enabled": self.auto_execute_enabled,
            "alert_sources": self.alert_sources,
        }
        return {key: value for key, value in payload.items() if value is not None}


class RagConfigRequest(BaseModel):
    enabled: bool | None = None
    knowledge_id: str | None = Field(default=None, alias="knowledgeId")
    api_key: str | None = Field(default=None, alias="apiKey")
    top_k: int | None = Field(default=None, alias="topK")
    similarity_threshold: float | None = Field(default=None, alias="similarityThreshold")
    retrieve_strategy: int | None = Field(default=None, alias="retrieveStrategy")
    enable_rerank_model: bool | None = Field(default=None, alias="enableRerankModel")
    rerank_model: str | None = Field(default=None, alias="rerankModel")

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.enabled is not None:
            payload["rag_enabled"] = self.enabled
        if self.knowledge_id is not None:
            payload["rag_knowledge_id"] = self.knowledge_id
        if self.api_key is not None:
            payload["rag_api_key"] = self.api_key
        if self.top_k is not None:
            payload["rag_top_k"] = self.top_k
        if self.similarity_threshold is not None:
            payload["rag_similarity_threshold"] = self.similarity_threshold
        if self.retrieve_strategy is not None:
            payload["rag_retrieve_strategy"] = self.retrieve_strategy
        if self.enable_rerank_model is not None:
            payload["rag_enable_rerank_model"] = self.enable_rerank_model
        if self.rerank_model is not None:
            payload["rag_rerank_model"] = self.rerank_model
        return payload


class AlertSourceParserGenerateRequest(BaseModel):
    sample_payload: str = Field(..., alias="samplePayload")


class AlertSourceParserPreviewRequest(BaseModel):
    sample_payload: str = Field(..., alias="samplePayload")
    parser_rule: dict[str, Any] | None = Field(default=None, alias="parserRule")


class SkillCreateRequest(BaseModel):
    name: str
    description: str
    category: str = "other"
    type: str = "doc"
    content: str = ""
    mode: str | None = None
    code: str = ""
    approval_required: bool = Field(default=False, alias="approvalRequired")
    completion_policy: dict[str, Any] | None = Field(default=None, alias="completionPolicy")


class SkillDebugRequest(BaseModel):
    arguments: dict[str, Any] | None = None
    context: dict[str, Any] | None = None


class WorkflowCreateRequest(BaseModel):
    name: str
    description: str = ""
    template: str = "close"
    workflow: dict[str, Any] | None = None


class WorkflowRunRequest(BaseModel):
    context: dict[str, Any] | None = None


class AgentCreateRequest(BaseModel):
    name: str
    description: str = ""
    description_cn: str | None = None
    prompt: str
    prompt_command: str | None = Field(default=None, alias="promptCommand")
    prompt_alert: str | None = Field(default=None, alias="promptAlert")
    prompt_synthesize: str | None = Field(default=None, alias="promptSynthesize")
    mode: str = "subagent"
    role: str | None = None
    enabled: bool = True
    color: str | None = None
    skills: list[str] | None = None
    tools: list[str] | None = None
    doc_skill_mode: str = Field(default="all", alias="docSkillMode")
    doc_skill_allowlist: list[str] | None = Field(default=None, alias="docSkillAllowlist")
    doc_skill_denylist: list[str] | None = Field(default=None, alias="docSkillDenylist")
    hybrid_doc_allowlist: list[str] | None = Field(default=None, alias="hybridDocAllowlist")
    exec_skill_allowlist: list[str] | None = Field(default=None, alias="execSkillAllowlist")
    worker_allowlist_command: list[str] | None = Field(default=None, alias="workerAllowlistCommand")
    worker_allowlist_alert: list[str] | None = Field(default=None, alias="workerAllowlistAlert")
    worker_max_steps: int = Field(default=3, alias="workerMaxSteps")
    worker_parallel_limit: int = Field(default=3, alias="workerParallelLimit")
    use_global_model: bool = Field(default=True, alias="useGlobalModel")
    llm_api_base_url: str | None = Field(default=None, alias="llmApiBaseUrl")
    llm_api_key: str | None = Field(default=None, alias="llmApiKey")
    llm_model: str | None = Field(default=None, alias="llmModel")
    llm_temperature: float | None = Field(default=None, alias="llmTemperature")
    llm_timeout: int | None = Field(default=None, alias="llmTimeout")


class DeleteRequest(BaseModel):
    name: str | None = None


class ApprovalDecisionRequest(BaseModel):
    stream: bool = False
