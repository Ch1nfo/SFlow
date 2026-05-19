export type AlertTaskStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'completed' | 'pending_closure' | 'pending_manual_closure' | 'awaiting_approval'

export type ApprovalRequest = {
  approval_id: string
  run_id: string
  scope_type: string
  scope_ref: string
  status: string
  skill_name: string
  arguments: Record<string, unknown>
  arguments_fingerprint: string
  approval_required: boolean
  checkpoint_thread_id: string
  checkpoint_ns: string
  parent_checkpoint_thread_id?: string
  parent_checkpoint_ns?: string
  tool_call_id?: string
  parent_tool_call_id?: string
  message?: string
  created_at: string
  decided_at?: string
  arguments_summary?: string
}

export type ExecutionTraceItem = {
  phase: string
  title: string
  summary?: string
  success?: boolean | null
  data?: Record<string, unknown>
}

export type FinalFacts = {
  judgment?: {
    disposition?: string
    source?: string
    confidence?: string
  }
  closure?: {
    attempted?: boolean
    success?: boolean
    status?: string
    memo?: string
    detail_msg?: string
    source_type?: string
    source_name?: string
  }
  disposal?: {
    attempted?: boolean
    success?: boolean
    actions?: Array<Record<string, unknown>>
  }
  workflow?: {
    used?: boolean
    count?: number
    workflow_ids?: string[]
  }
  task_outcome?: {
    success?: boolean
    status?: string
    source?: string
  }
  consistency?: {
    consistent?: boolean
    issues?: string[]
  }
}

export type AlertTaskResultData = Record<string, unknown> & {
  disposition?: string
  summary?: string
  reason?: string
  evidence?: string[]
  final_judgment_markdown?: string
  workflow_selection?: Record<string, unknown>
  workflow_runs?: Array<Record<string, unknown>>
  analysis_step?: Record<string, unknown>
  primary_action_steps?: Array<Record<string, unknown>>
  primary_closure_step?: Record<string, unknown>
  aggregated_action_steps?: Array<Record<string, unknown>>
  aggregated_actions?: Record<string, unknown>
  aggregated_closure_steps?: Array<Record<string, unknown>>
  effective_closure_step?: Record<string, unknown>
  action_steps?: Array<Record<string, unknown>>
  closure_step?: Record<string, unknown>
  final_facts?: FinalFacts
  execution_trace?: ExecutionTraceItem[]
}

export type AlertTask = {
  task_id: string
  event_ids: string
  workflow_name: string
  title: string
  description: string
  source_id?: string
  source_name?: string
  alert_time?: string
  status: AlertTaskStatus | string
  retry_count: number
  last_action: string
  last_result_success?: boolean | null
  last_result_error?: string | null
  last_result_data: AlertTaskResultData
  payload: Record<string, unknown>
}

export type PollAlertsResponse = {
  source_id?: string
  alert_sources?: Array<{
    id: string
    name: string
    enabled: boolean
    auto_execute_enabled: boolean
  }>
  fetched_count: number
  queued_count: number
  updated_count: number
  completed_count: number
  skipped_count: number
  failed_count: number
  auto_execute_enabled: boolean
  auto_execute_running: boolean
  tasks: AlertTask[]
  errors: string[]
}

export type AlertActionResponse = {
  action: string
  success: boolean
  task_id: string
  event_ids: string
  data: Record<string, unknown>
  task?: AlertTask | null
  error?: string | null
}

export type SkillSummary = {
  name: string
  type: string
  description: string
  category?: string
  executable: boolean
  approval_required: boolean
  completion_policy?: {
    enabled: boolean
    action_kind: string
    completion_effect: string
  }
  entry?: string | null
  mode?: string | null
}

export type SkillDetail = SkillSummary & {
  markdown: string
  code: string
  input_schema: Record<string, unknown>
  output_schema: Record<string, unknown>
}

export type SkillDebugResponse = {
  success: boolean
  skill: string
  data: Record<string, unknown>
  error?: string | null
}

export type CommandDispatchResponse = {
  command_text: string
  route: string
  success: boolean
  data: Record<string, unknown> & {
    approval_request?: ApprovalRequest
    resolved_approval?: ApprovalRequest
  }
  approval?: ApprovalRequest
  task?: AlertTask | null
  error?: string | null
}

export type ConversationHistoryMessage = {
  role: 'user' | 'assistant'
  content: string
}

export type CommandStreamEvent =
  | { type: 'request'; payload: { request_id: string } }
  | { type: 'meta'; payload: { route: string; success: boolean } }
  | { type: 'status'; payload: { text: string } }
  | { type: 'delta'; payload: { text: string } }
  | { type: 'done'; payload: CommandDispatchResponse }

export type HealthResponse = {
  name: string
  status: string
  demo_mode?: boolean
  agent_enabled?: boolean
  agent_configured?: boolean
  agent_available?: boolean
}

export type AuditEvent = {
  event_type: string
  message: string
  payload: Record<string, unknown>
  created_at: string
}

export type RuntimeSettingsResponse = {
  branding: {
    product_name: string
    console_title: string
  }
  runtime: {
    poll_interval_seconds: string
    failed_retry_interval_seconds: string
    workflow_engine: string
    agent_enabled: boolean
    auto_execute_enabled: boolean
    weekly_alert_cleanup_enabled: boolean
    run_log_retention_days: number
  }
  llm: {
    api_base_url: string
    api_key: string
    api_key_configured: boolean
    model: string
    temperature: number
    timeout: number
    agent_configured: boolean
    agent_available: boolean
    agent_unavailable_reason: string
  }
  alert_source: {
    id: string
    name: string
    enabled: boolean
    type: string
    url: string
    method: string
    headers: string
    query: string
    body: string
    timeout: number
    sample_payload: string
    parser_rule: Record<string, unknown>
    parser_configured: boolean
    script_code: string
    script_timeout: number
    auto_execute_enabled: boolean
    poll_interval_seconds: string
    failed_retry_interval_seconds: string
    analysis_prompt: string
  }
  alert_sources: AlertSourceSettings[]
  default_alert_source_id: string
  features: {
    natural_language_dispatch: boolean
    alert_polling: boolean
    hybrid_skills: boolean
    audit_timeline: boolean
    agent_runtime: boolean
  }
  rag: RagSettings
  persisted_overrides: Record<string, unknown>
}

export type RagSettings = {
  enabled: boolean
  knowledge_id: string
  api_key: string
  api_key_configured: boolean
  top_k: number
  similarity_threshold: number
  retrieve_strategy: number
  enable_rerank_model: boolean
  rerank_model: string
}

export type AlertSourceSettings = {
  id: string
  name: string
  enabled: boolean
  type: string
  url: string
  method: string
  headers: string
  query: string
  body: string
  timeout: number
  sample_payload: string
  parser_rule: Record<string, unknown>
  parser_configured: boolean
  script_code: string
  script_timeout: number
  auto_execute_enabled: boolean
  poll_interval_seconds: string
  failed_retry_interval_seconds: string
  analysis_prompt: string
}

export type AlertSourceFetchResponse = {
  count?: number
  alerts?: Array<Record<string, unknown>>
  raw_payload?: Record<string, unknown> | unknown[]
  raw_response?: string
  status_code?: number
}

export type AlertSourceParserGenerateResponse = {
  parser_rule: Record<string, unknown>
  strategy: string
  reason: string
  preview: {
    count: number
    alerts: Array<Record<string, unknown>>
    warnings?: string[]
    error?: string | null
  }
}

export type DashboardSummaryResponse = {
  totals: {
    tasks: number
    queued: number
    running: number
    awaiting_approval: number
    succeeded: number
    failed: number
    audit_events: number
    skills: number
    workflows: number
    agents: number
  }
  judgment: {
    business_trigger: number
    false_positive: number
    true_attack: number
    unknown: number
  }
  operations: {
    closed_success: number
    disposed_success: number
    manual_completed: number
    banned_ip_count: number
    banned_ips: string[]
  }
  automation?: {
    enabled: boolean
    running: boolean
  }
  recent_results: Array<{
    task_id: string
    event_ids: string
    title: string
    status: string
    last_action: string
    disposition: string
  }>
}

export type WorkflowSummary = {
  id: string
  name: string
  description: string
  enabled: boolean
  scenarios: string[]
  steps_count: number
  step_agents: string[]
  location: string
}

export type WorkflowDetail = WorkflowSummary & {
  selection_keywords: string[]
  steps: Array<{
    id: string
    name: string
    agent: string
    task_prompt?: string
  }>
  validation?: {
    valid: boolean
    errors: string[]
  }
}

export type WorkflowRunResponse = {
  success: boolean
  workflow_id: string
  workflow_name?: string
  error?: string
  validation?: {
    valid: boolean
    errors: string[]
  }
  workflow_steps?: Array<{
    id: string
    name: string
    agent: string
  }>
  worker_results?: Array<Record<string, unknown>>
  summary?: string
  reason?: string
}

export type ApprovalDecisionResponse = {
  success: boolean
  route: string
  approval?: ApprovalRequest
  data: Record<string, unknown>
  task?: AlertTask | null
  error?: string | null
}

export type AgentSummary = {
  name: string
  description: string
  mode: string
  role?: string
  enabled?: boolean
  location: string
  has_prompt: boolean
  use_global_model?: boolean
  has_model_override?: boolean
  is_system?: boolean
}

export type AgentDetail = AgentSummary & {
  color?: string
  prompt: string
  prompt_command?: string
  prompt_alert?: string
  prompt_synthesize?: string
  skills: string[]
  tools: string[]
  role: string
  enabled: boolean
  doc_skill_mode: string
  doc_skill_allowlist: string[]
  doc_skill_denylist: string[]
  hybrid_doc_allowlist: string[]
  exec_skill_allowlist: string[]
  worker_allowlist_command: string[]
  worker_allowlist_alert: string[]
  worker_max_steps: number
  worker_parallel_limit: number
  use_global_model: boolean
  llm_api_base_url?: string
  llm_api_key?: string
  llm_api_key_configured?: boolean
  llm_model?: string
  llm_temperature?: number | null
  llm_timeout?: number | null
}

async function parseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `HTTP ${response.status}`
    try {
      const payload = await response.json() as { detail?: string }
      if (payload?.detail) {
        message = payload.detail
      }
    } catch {
      // ignore
    }
    throw new Error(message)
  }
  return (await response.json()) as T
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path)
  return parseJson<T>(response)
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return parseJson<T>(response)
}

export async function fetchHealth(): Promise<HealthResponse> {
  return getJson('/api/sentinelflow/health')
}

export async function fetchDashboardSummary(): Promise<DashboardSummaryResponse> {
  return getJson('/api/sentinelflow/dashboard/summary')
}

export async function fetchPollAlerts(sourceId?: string): Promise<PollAlertsResponse> {
  const suffix = sourceId ? `?sourceId=${encodeURIComponent(sourceId)}` : ''
  return getJson(`/api/sentinelflow/alerts/state${suffix}`)
}

export async function fetchAllPollAlerts(): Promise<PollAlertsResponse> {
  return fetchPollAlerts('all')
}

export async function handleAlertAction(action: string, task?: AlertTask, alert?: Record<string, unknown>, sourceId?: string) {
  return postJson<AlertActionResponse>('/api/sentinelflow/alerts/handle', { action, task, alert, sourceId })
}

export async function fetchSkills(): Promise<{ skills: SkillSummary[] }> {
  return getJson('/api/sentinelflow/skills')
}

export async function fetchSkillDetail(name: string): Promise<SkillDetail> {
  return getJson(`/api/sentinelflow/skills/${encodeURIComponent(name)}`)
}

export async function createSkill(payload: {
  name: string
  description: string
  category?: string
  type: string
  content: string
  code: string
  mode?: string | null
  approvalRequired?: boolean
  completionPolicy?: {
    enabled: boolean
    action_kind: string
    completion_effect: string
  }
}) {
  return postJson<SkillDetail>('/api/sentinelflow/skills', payload)
}

export async function saveSkill(name: string, payload: {
  name: string
  description: string
  category?: string
  type: string
  content: string
  code: string
  mode?: string | null
  approvalRequired?: boolean
  completionPolicy?: {
    enabled: boolean
    action_kind: string
    completion_effect: string
  }
}) {
  return postJson<SkillDetail>(`/api/sentinelflow/skills/${encodeURIComponent(name)}/save`, payload)
}

export async function deleteSkill(name: string) {
  return postJson<{ deleted: boolean; name: string }>(`/api/sentinelflow/skills/${encodeURIComponent(name)}/delete`, {})
}

export async function debugSkill(name: string, payload: {
  arguments?: Record<string, unknown>
  context?: Record<string, unknown>
}) {
  return postJson<SkillDebugResponse>(`/api/sentinelflow/skills/${encodeURIComponent(name)}/debug`, payload)
}

export async function fetchWorkflows(): Promise<{ workflows: WorkflowSummary[] }> {
  return getJson('/api/sentinelflow/workflows')
}

export async function fetchWorkflowDetail(id: string): Promise<WorkflowDetail> {
  return getJson(`/api/sentinelflow/workflows/${encodeURIComponent(id)}`)
}

export async function createWorkflow(payload: {
  name: string
  description: string
  template: string
  workflow?: Record<string, unknown>
}) {
  return postJson('/api/sentinelflow/workflows', payload)
}

export async function saveWorkflow(id: string, payload: {
  name: string
  description: string
  template: string
  workflow?: Record<string, unknown>
}) {
  return postJson<WorkflowDetail>(`/api/sentinelflow/workflows/${encodeURIComponent(id)}/save`, payload)
}

export async function deleteWorkflow(id: string) {
  return postJson<{ deleted: boolean; id: string }>(`/api/sentinelflow/workflows/${encodeURIComponent(id)}/delete`, {})
}

export async function runWorkflow(id: string, payload: {
  context?: Record<string, unknown>
}) {
  return postJson<WorkflowRunResponse>(`/api/sentinelflow/workflows/${encodeURIComponent(id)}/run`, payload)
}

export async function fetchAgents(): Promise<{ agents: AgentSummary[] }> {
  return getJson('/api/sentinelflow/agents')
}

export async function fetchAgentDetail(name: string): Promise<AgentDetail> {
  return getJson(`/api/sentinelflow/agents/${encodeURIComponent(name)}`)
}

export async function createAgent(payload: {
  name: string
  description: string
  description_cn?: string
  prompt: string
  promptCommand?: string
  promptAlert?: string
  promptSynthesize?: string
  mode?: string
  role?: string
  enabled?: boolean
  color?: string
  skills?: string[]
  tools?: string[]
  docSkillMode?: string
  docSkillAllowlist?: string[]
  docSkillDenylist?: string[]
  hybridDocAllowlist?: string[]
  execSkillAllowlist?: string[]
  workerAllowlist?: string[]
  useGlobalModel?: boolean
  workerParallelLimit?: number
  llmApiBaseUrl?: string
  llmApiKey?: string
  llmModel?: string
  llmTemperature?: number
  llmTimeout?: number
}) {
  return postJson('/api/sentinelflow/agents', payload)
}

export async function saveAgent(name: string, payload: {
  name: string
  description: string
  description_cn?: string
  prompt: string
  promptCommand?: string
  promptAlert?: string
  promptSynthesize?: string
  mode?: string
  role?: string
  enabled?: boolean
  color?: string
  skills?: string[]
  tools?: string[]
  docSkillMode?: string
  docSkillAllowlist?: string[]
  docSkillDenylist?: string[]
  hybridDocAllowlist?: string[]
  execSkillAllowlist?: string[]
  workerAllowlist?: string[]
  useGlobalModel?: boolean
  workerParallelLimit?: number
  llmApiBaseUrl?: string
  llmApiKey?: string
  llmModel?: string
  llmTemperature?: number
  llmTimeout?: number
}) {
  return postJson<AgentDetail>(`/api/sentinelflow/agents/${encodeURIComponent(name)}/save`, payload)
}

export async function deleteAgent(name: string) {
  return postJson<{ deleted: boolean; name: string }>(`/api/sentinelflow/agents/${encodeURIComponent(name)}/delete`, {})
}

export async function dispatchCommand(commandText: string, history: ConversationHistoryMessage[] = []): Promise<CommandDispatchResponse> {
  return postJson('/api/sentinelflow/commands/dispatch', { commandText, history })
}

export async function streamCommand(
  commandText: string,
  history: ConversationHistoryMessage[] = [],
  onEvent?: (event: CommandStreamEvent) => void,
  signal?: AbortSignal,
): Promise<CommandDispatchResponse> {
  const response = await fetch('/api/sentinelflow/commands/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ commandText, history }),
    signal,
  })
  if (!response.ok || !response.body) {
    throw new Error(`HTTP ${response.status}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let finalPayload: CommandDispatchResponse | null = null

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() ?? ''

    for (const chunk of chunks) {
      const trimmed = chunk.trim()
      if (!trimmed) continue
      const dataLine = trimmed
        .split('\n')
        .find((line) => line.startsWith('data:'))
      if (!dataLine) continue
      const event = JSON.parse(dataLine.slice(5).trim()) as CommandStreamEvent
      onEvent?.(event)
      if (event.type === 'done') {
        finalPayload = event.payload
      }
    }
  }

  buffer += decoder.decode()

  if (buffer.trim()) {
    const dataLine = buffer
      .trim()
      .split('\n')
      .find((line) => line.startsWith('data:'))
    if (dataLine) {
      const event = JSON.parse(dataLine.slice(5).trim()) as CommandStreamEvent
      onEvent?.(event)
      if (event.type === 'done') {
        finalPayload = event.payload
      }
    }
  }

  if (!finalPayload) {
    throw new Error('流式响应未返回最终结果')
  }
  return finalPayload
}

export async function stopStreamingCommand(requestId: string) {
  return postJson<{ stopped: boolean; request_id?: string; error?: string }>('/api/sentinelflow/commands/stop', {
    request_id: requestId,
  })
}

export async function fetchPendingApprovals(): Promise<{ approvals: ApprovalRequest[] }> {
  return getJson('/api/sentinelflow/approvals/pending')
}

export async function decideApproval(approvalId: string, decision: 'approve' | 'reject') {
  return postJson<ApprovalDecisionResponse>(`/api/sentinelflow/approvals/${encodeURIComponent(approvalId)}/${decision}`, {
    stream: false,
  })
}

export async function streamApprovalDecision(
  approvalId: string,
  decision: 'approve' | 'reject',
  onEvent?: (event: CommandStreamEvent) => void,
  signal?: AbortSignal,
): Promise<CommandDispatchResponse> {
  const response = await fetch(`/api/sentinelflow/approvals/${encodeURIComponent(approvalId)}/${decision}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ stream: true }),
    signal,
  })
  if (!response.ok || !response.body) {
    throw new Error(`HTTP ${response.status}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let finalPayload: CommandDispatchResponse | null = null

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() ?? ''

    for (const chunk of chunks) {
      const trimmed = chunk.trim()
      if (!trimmed) continue
      const dataLine = trimmed
        .split('\n')
        .find((line) => line.startsWith('data:'))
      if (!dataLine) continue
      const event = JSON.parse(dataLine.slice(5).trim()) as CommandStreamEvent
      onEvent?.(event)
      if (event.type === 'done') {
        finalPayload = event.payload
      }
    }
  }

  buffer += decoder.decode()

  if (buffer.trim()) {
    const dataLine = buffer
      .trim()
      .split('\n')
      .find((line) => line.startsWith('data:'))
    if (dataLine) {
      const event = JSON.parse(dataLine.slice(5).trim()) as CommandStreamEvent
      onEvent?.(event)
      if (event.type === 'done') {
        finalPayload = event.payload
      }
    }
  }

  if (!finalPayload) {
    throw new Error('流式响应未返回最终结果')
  }
  return finalPayload
}

export async function fetchAuditEvents(): Promise<{ events: AuditEvent[] }> {
  return getJson('/api/sentinelflow/audit/events')
}

export async function fetchRuntimeSettings(): Promise<RuntimeSettingsResponse> {
  return getJson('/api/sentinelflow/runtime/settings')
}

export async function saveRuntimeSettings(payload: {
  pollIntervalSeconds: string
  failedRetryIntervalSeconds: string
  agentEnabled: boolean
  llmApiBaseUrl: string
  llmApiKey?: string
  llmModel: string
  llmTemperature: string
  llmTimeout: string
  weeklyAlertCleanupEnabled: boolean
  runLogRetentionDays?: string
  alertSourceEnabled?: boolean
  alertSourceType?: string
  alertSourceUrl?: string
  alertSourceMethod?: string
  alertSourceHeaders?: string
  alertSourceQuery?: string
  alertSourceBody?: string
  alertSourceTimeout?: string
  alertSourceSamplePayload?: string
  alertParserRule?: Record<string, unknown>
  alertScriptCode?: string
  alertScriptTimeout?: string
  alertSources?: Array<Record<string, unknown>>
}) {
  return postJson<RuntimeSettingsResponse>('/api/sentinelflow/runtime/settings', payload)
}

export type RunLogDateSummary = {
  date: string
  count: number
}

export type RunLogAlertSummary = {
  date: string
  log_id: string
  event_ids: string
  task_id: string
  title: string
  alert_name: string
  alert_time: string
  source_name: string
  event_count: number
  updated_at: string
}

export type RunLogEvent = {
  ts: string
  seq?: number
  level: string
  phase: string
  title: string
  metadata?: Record<string, unknown>
  data: unknown
}

export type RunLogDetail = {
  date: string
  log_id: string
  metadata?: Record<string, unknown>
  events: RunLogEvent[]
  total_events?: number
  returned_events?: number
  truncated?: boolean
  tail?: boolean
}

export async function fetchRunLogDates(): Promise<{ retention_days: number; dates: RunLogDateSummary[] }> {
  return getJson('/api/sentinelflow/runtime/run-logs')
}

export async function saveRunLogSettings(retentionDays: number): Promise<{ retention_days: number; dates: RunLogDateSummary[] }> {
  return postJson('/api/sentinelflow/runtime/run-logs/settings', { retentionDays })
}

export async function fetchRunLogAlerts(date: string): Promise<{ date: string; alerts: RunLogAlertSummary[] }> {
  return getJson(`/api/sentinelflow/runtime/run-logs/${encodeURIComponent(date)}/alerts`)
}

export async function fetchRunLogDetail(date: string, logId: string, limit = 500): Promise<RunLogDetail> {
  return getJson(`/api/sentinelflow/runtime/run-logs/${encodeURIComponent(date)}/alerts/${encodeURIComponent(logId)}?limit=${encodeURIComponent(String(limit))}&tail=true`)
}

export async function resetRuntimeSettings() {
  return postJson<RuntimeSettingsResponse>('/api/sentinelflow/runtime/settings/reset', {})
}

export async function testAlertSourceFetch(payload: {
  alertSourceEnabled?: boolean
  alertSourceType?: string
  alertSourceUrl?: string
  alertSourceMethod?: string
  alertSourceHeaders?: string
  alertSourceQuery?: string
  alertSourceBody?: string
  alertSourceTimeout?: string
  alertScriptCode?: string
  alertScriptTimeout?: string
}) {
  return postJson<AlertSourceFetchResponse>('/api/sentinelflow/runtime/settings/alert-source/test-fetch', payload)
}

export async function generateAlertParser(samplePayload: string) {
  return postJson<AlertSourceParserGenerateResponse>('/api/sentinelflow/runtime/settings/alert-source/generate-parser', {
    samplePayload,
  })
}

export async function testAlertParser(payload: {
  samplePayload: string
  parserRule?: Record<string, unknown>
}) {
  return postJson<AlertSourceParserGenerateResponse['preview']>('/api/sentinelflow/runtime/settings/alert-source/test-parse', payload)
}

export async function fetchRagSettings(): Promise<RagSettings> {
  return getJson('/api/sentinelflow/runtime/rag-settings')
}

export async function saveRagSettings(payload: {
  enabled?: boolean
  knowledgeId?: string
  apiKey?: string
  topK?: number
  similarityThreshold?: number
  retrieveStrategy?: number
  enableRerankModel?: boolean
  rerankModel?: string
}): Promise<RagSettings> {
  return postJson<RagSettings>('/api/sentinelflow/runtime/rag-settings', payload)
}
