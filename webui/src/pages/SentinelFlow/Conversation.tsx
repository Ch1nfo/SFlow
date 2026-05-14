import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Bot, CheckCircle2, ChevronDown, ChevronRight, FileJson, MessageSquareText, Plus, RotateCcw, Send, Square, Trash2, Workflow, Wrench } from 'lucide-react'
import type { ApprovalRequest, CommandDispatchResponse } from '@/api/sentinelflow'
import JsonPreview from '@/components/sentinelflow/JsonPreview'
import MarkdownContent from '@/components/sentinelflow/MarkdownContent'
import StatusBadge from '@/components/sentinelflow/StatusBadge'
import Surface from '@/components/sentinelflow/Surface'
import PageHeader from '@/components/common/PageHeader'
import {
  type CommandHistoryItem,
  createConversationSession,
  clearActiveConversation,
  deleteActiveConversation,
  getConversationRuntimeState,
  resolveConversationApproval,
  sanitizeDisplayText,
  setConversationActiveSession,
  setConversationDraft,
  startConversationRun,
  stopConversationRun,
  subscribeConversationRuntime,
  summarizeAssistantReply,
} from '@/utils/sentinelflowConversationStore'

type ToolCallLike = {
  name?: string
  args?: unknown
  id?: string
  type?: string
  key_facts?: unknown
  tool_payload?: unknown
}

type WorkerStepLike = {
  step?: number
  worker?: string
  worker_agent?: string
  agent_name?: string
  task_prompt?: string
  delegation_reason?: string
  tool_calls?: ToolCallLike[]
  tool_calls_summary?: ToolCallLike[]
  final_response?: string
  display_summary?: string
  short_summary?: string
  success?: boolean
  error?: string
  context_manifest?: unknown
  context_warnings?: unknown
  missing_required_inputs?: unknown
}

type CommandDataLike = {
  final_response?: string
  tool_calls?: ToolCallLike[]
  primary_agent?: string
  worker_agent?: string
  delegation_reason?: string
  approval_request?: ApprovalRequest
  worker_results?: WorkerStepLike[]
  worker_result?: {
    tool_calls?: ToolCallLike[]
    tool_calls_summary?: ToolCallLike[]
    agent_name?: string
  }
  workflow_runs?: unknown[]
  tool_calls_summary?: ToolCallLike[]
  context_manifest?: unknown
  context_warnings?: unknown
  missing_required_inputs?: unknown
  input_contract?: unknown
  authority_trace?: unknown
}

type DetailField = {
  label: string
  value: unknown
}

function CollapsibleChatMarkdown({ content, inverted = false }: { content: string; inverted?: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const normalizedContent = String(content ?? '')
  const shouldClamp = normalizedContent.length > 360 || normalizedContent.split(/\r?\n/).length > 10

  useEffect(() => {
    setExpanded(false)
  }, [normalizedContent])

  return (
    <div className="sentinelflow-chat-markdown">
      <div className={shouldClamp && !expanded ? 'sentinelflow-chat-markdown-clamped' : undefined}>
        <MarkdownContent content={normalizedContent} inverted={inverted} />
      </div>
      {shouldClamp ? (
        <button
          type="button"
          className={`sentinelflow-chat-expand-button ${inverted ? 'sentinelflow-chat-expand-button-inverted' : ''}`}
          onClick={() => setExpanded((current) => !current)}
        >
          {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          {expanded ? '收起' : '展开全部'}
        </button>
      ) : null}
    </div>
  )
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}

function asRecord(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {}
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function asToolCalls(value: unknown): ToolCallLike[] {
  return Array.isArray(value) ? value.filter(isRecord) as ToolCallLike[] : []
}

function getString(record: Record<string, unknown>, key: string): string {
  const value = record[key]
  return typeof value === 'string' ? value : ''
}

function getBooleanLabel(value: unknown): string {
  if (value === true) return '成功'
  if (value === false) return '失败'
  return ''
}

function formatDetailValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (typeof value === 'number') return String(value)
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function firstText(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) {
      return value
    }
  }
  return ''
}

function collectWorkerResults(data: CommandDataLike): WorkerStepLike[] {
  const directResults = Array.isArray(data.worker_results) ? data.worker_results as WorkerStepLike[] : []
  if (directResults.length) return directResults

  const workflowRuns = asArray(data.workflow_runs)
  return workflowRuns.flatMap((run) => {
    const runRecord = asRecord(run)
    return asArray(runRecord.worker_results).filter(isRecord) as WorkerStepLike[]
  })
}

function collectToolCalls(data: CommandDataLike): ToolCallLike[] {
  const calls: ToolCallLike[] = []
  calls.push(...asToolCalls(data.tool_calls))
  calls.push(...asToolCalls(data.tool_calls_summary))
  calls.push(...asToolCalls(data.worker_result?.tool_calls))
  calls.push(...asToolCalls(data.worker_result?.tool_calls_summary))
  collectWorkerResults(data).forEach((worker) => {
    calls.push(...asToolCalls(worker.tool_calls))
    calls.push(...asToolCalls(worker.tool_calls_summary))
  })
  asArray(data.workflow_runs).forEach((run) => {
    const runRecord = asRecord(run)
    calls.push(...asToolCalls(runRecord.tool_calls))
    calls.push(...asToolCalls(runRecord.tool_calls_summary))
    asArray(runRecord.actions_summary).forEach((action) => {
      const actionRecord = asRecord(action)
      calls.push(...asToolCalls(actionRecord.tool_calls))
      calls.push(...asToolCalls(actionRecord.tool_calls_summary))
    })
  })
  return calls.filter((call) => call.name || call.args)
}

function collectContextRecords(data: CommandDataLike): Array<{ source: string; manifest: Record<string, unknown> }> {
  const records: Array<{ source: string; manifest: Record<string, unknown> }> = []
  const rootManifest = asRecord(data.context_manifest)
  if (Object.keys(rootManifest).length) {
    records.push({ source: '当前响应', manifest: rootManifest })
  }
  collectWorkerResults(data).forEach((worker, index) => {
    const manifest = asRecord(worker.context_manifest)
    if (Object.keys(manifest).length) {
      const workerName = worker.worker || worker.worker_agent || worker.agent_name || `worker-${index + 1}`
      records.push({ source: `步骤 ${index + 1} · ${workerName}`, manifest })
    }
  })
  asArray(data.workflow_runs).forEach((run, index) => {
    const runRecord = asRecord(run)
    const manifest = asRecord(runRecord.context_manifest)
    if (Object.keys(manifest).length) {
      records.push({ source: getString(runRecord, 'workflow_name') || getString(runRecord, 'workflow_id') || `Workflow ${index + 1}`, manifest })
    }
  })
  return records
}

function ContextControlDetails({ records }: { records: Array<{ source: string; manifest: Record<string, unknown> }> }) {
  if (!records.length) return null
  return (
    <DetailSection title="执行上下文" icon={<FileJson className="h-4 w-4" />}>
      <div className="sentinelflow-detail-list">
        {records.map((record, index) => {
          const warnings = asArray(record.manifest.context_warnings).map(String).filter(Boolean)
          const missing = asArray(record.manifest.missing_required_inputs)
          const facts = asRecord(record.manifest.available_facts)
          return (
            <details key={`${record.source}-${index}`} className="sentinelflow-detail-disclosure">
              <summary>
                <span className="sentinelflow-detail-summary-main">{record.source}</span>
                <span className="sentinelflow-detail-summary-meta">
                  {missing.length ? `缺少 ${missing.length} 项` : warnings.length ? `${warnings.length} 个提示` : '上下文正常'}
                </span>
              </summary>
              <DetailFields fields={[
                { label: '当前目标', value: record.manifest.current_goal },
                { label: '入口', value: record.manifest.entry_type },
                { label: '必需对象', value: asArray(record.manifest.required_objects).join(', ') },
                { label: '上下文大小', value: formatDetailValue(record.manifest.context_size) },
                { label: '提示', value: warnings.join(', ') },
              ]} />
              {missing.length ? <JsonPreview value={{ missing_required_inputs: missing }} /> : null}
              {Object.keys(facts).length ? <JsonPreview value={{ available_facts: facts, authority_trace: record.manifest.authority_trace }} /> : null}
            </details>
          )
        })}
      </div>
    </DetailSection>
  )
}

function DetailSection({ title, icon, children }: { title: string; icon?: ReactNode; children: ReactNode }) {
  return (
    <section className="sentinelflow-detail-section">
      <div className="sentinelflow-detail-section-title">
        {icon}
        <span>{title}</span>
      </div>
      {children}
    </section>
  )
}

function DetailFields({ fields }: { fields: DetailField[] }) {
  const visibleFields = fields.filter((field) => field.value !== undefined && field.value !== null && field.value !== '')
  if (!visibleFields.length) return null
  return (
    <div className="sentinelflow-detail-field-grid">
      {visibleFields.map((field) => (
        <div key={field.label} className="sentinelflow-detail-field">
          <span>{field.label}</span>
          <strong title={formatDetailValue(field.value)}>{formatDetailValue(field.value)}</strong>
        </div>
      ))}
    </div>
  )
}

function ToolCallDetailList({ toolCalls }: { toolCalls: ToolCallLike[] }) {
  if (!toolCalls.length) return null
  return (
    <div className="sentinelflow-detail-list">
      {toolCalls.map((toolCall, index) => (
        <details key={`${toolCall.name || 'tool'}-${toolCall.id || index}`} className="sentinelflow-detail-disclosure">
          <summary>
            <span className="sentinelflow-detail-summary-main">{toolCall.name || `tool-${index + 1}`}</span>
            <span className="sentinelflow-detail-summary-meta">{formatToolArguments(toolCall.args)}</span>
          </summary>
          <JsonPreview value={{ args: toolCall.args ?? {}, key_facts: toolCall.key_facts, id: toolCall.id, type: toolCall.type }} />
        </details>
      ))}
    </div>
  )
}

function ConversationResponseDetails({
  response,
  resultTone,
  resultCode,
  approvalCard,
}: {
  response: CommandDispatchResponse
  resultTone: 'warn' | 'success' | 'danger' | 'neutral'
  resultCode: string
  approvalCard?: ApprovalRequest
}) {
  const data = extractCommandData(response.data)
  const dataRecord = asRecord(response.data)
  const workerResults = collectWorkerResults(data)
  const toolCalls = collectToolCalls(data)
  const workflowRuns = asArray(data.workflow_runs)
  const approval = approvalCard ?? data.approval_request
  const contextRecords = collectContextRecords(data)

  return (
    <div className="sentinelflow-chat-details sentinelflow-chat-details-structured">
      <DetailSection title="运行概览" icon={<CheckCircle2 className="h-4 w-4" />}>
        <div className="sentinelflow-response-row">
          <StatusBadge tone={resultTone}>{resultCode}</StatusBadge>
          <span className="sentinelflow-detail-route">route: {response.route || 'unknown'}</span>
        </div>
        <DetailFields
          fields={[
            { label: '主 Agent', value: data.primary_agent },
            { label: '子 Agent', value: data.worker_agent || data.worker_result?.agent_name },
            { label: 'Workflow', value: getString(dataRecord, 'workflow_name') || getString(dataRecord, 'workflow_id') },
            { label: '任务', value: getString(dataRecord, 'task_id') || getString(dataRecord, 'event_ids') },
            { label: '成功', value: getBooleanLabel(response.success) },
          ]}
        />
      </DetailSection>

      {approval?.skill_name ? (
        <DetailSection title="审批" icon={<Wrench className="h-4 w-4" />}>
          <DetailFields
            fields={[
              { label: 'Skill', value: approval.skill_name },
              { label: '状态', value: approval.status || 'pending' },
              { label: '审批 ID', value: approval.approval_id },
              { label: '参数摘要', value: approval.arguments_summary },
            ]}
          />
          {approval.message ? <div className="sentinelflow-detail-note">{approval.message}</div> : null}
          {approval.arguments ? (
            <details className="sentinelflow-detail-disclosure">
              <summary>
                <span className="sentinelflow-detail-summary-main">审批参数</span>
                <span className="sentinelflow-detail-summary-meta">{formatToolArguments(approval.arguments)}</span>
              </summary>
              <JsonPreview value={approval.arguments} />
            </details>
          ) : null}
        </DetailSection>
      ) : null}

      <ContextControlDetails records={contextRecords} />

      {workflowRuns.length ? (
        <DetailSection title="Workflow" icon={<Workflow className="h-4 w-4" />}>
          <div className="sentinelflow-detail-list">
            {workflowRuns.map((run, index) => {
              const runRecord = asRecord(run)
              return (
                <details key={`workflow-${index}`} className="sentinelflow-detail-disclosure">
                  <summary>
                    <span className="sentinelflow-detail-summary-main">{getString(runRecord, 'workflow_name') || getString(runRecord, 'workflow_id') || `Workflow ${index + 1}`}</span>
                    <span className="sentinelflow-detail-summary-meta">{getBooleanLabel(runRecord.success) || getString(runRecord, 'status') || '已记录'}</span>
                  </summary>
                  <JsonPreview value={run} />
                </details>
              )
            })}
          </div>
        </DetailSection>
      ) : null}

      {workerResults.length ? (
        <DetailSection title="子 Agent" icon={<Bot className="h-4 w-4" />}>
          <div className="sentinelflow-detail-list">
            {workerResults.map((worker, index) => {
              const title = worker.worker || worker.worker_agent || worker.agent_name || `worker-${index + 1}`
              const summary = firstText(worker.display_summary, worker.short_summary, worker.final_response, worker.error)
              return (
                <details key={`${title}-${index}`} className="sentinelflow-detail-disclosure">
                  <summary>
                    <span className="sentinelflow-detail-summary-main">{`步骤 ${index + 1} · ${title}`}</span>
                    <span className="sentinelflow-detail-summary-meta">{getBooleanLabel(worker.success) || `${asToolCalls(worker.tool_calls).length + asToolCalls(worker.tool_calls_summary).length} 次技能`}</span>
                  </summary>
                  <DetailFields fields={[
                    { label: '分派原因', value: worker.delegation_reason },
                    { label: '任务指令', value: worker.task_prompt },
                  ]} />
                  {summary ? <div className="sentinelflow-detail-text"><MarkdownContent content={summary} /></div> : null}
                </details>
              )
            })}
          </div>
        </DetailSection>
      ) : null}

      {toolCalls.length ? (
        <DetailSection title={`技能调用 ${toolCalls.length} 次`} icon={<Wrench className="h-4 w-4" />}>
          <ToolCallDetailList toolCalls={toolCalls} />
        </DetailSection>
      ) : null}

      <DetailSection title="原始数据" icon={<FileJson className="h-4 w-4" />}>
        <details className="sentinelflow-detail-disclosure">
          <summary>
            <span className="sentinelflow-detail-summary-main">完整响应 data</span>
            <span className="sentinelflow-detail-summary-meta">用于排障和审计</span>
          </summary>
          <JsonPreview value={response.data} />
        </details>
      </DetailSection>
    </div>
  )
}

function formatToolArguments(args: unknown): string {
  if (!args || typeof args !== 'object') return '无参数'
  const allEntries = Object.entries(args as Record<string, unknown>)
  const entries = allEntries.slice(0, 5)
  if (!entries.length) return '无参数'
  const summary = entries
    .map(([key, value]) => `${key}: ${typeof value === 'string' ? value : JSON.stringify(value)}`)
    .join(' | ')
  const remaining = allEntries.length - entries.length
  return remaining > 0 ? `${summary} | 还有 ${remaining} 个` : summary
}

function extractCommandData(data: unknown): CommandDataLike {
  if (!data || typeof data !== 'object') return {}
  return data as CommandDataLike
}

function approvalStatusLabel(status: string): string {
  if (status === 'approved') return '已批准并继续'
  if (status === 'rejected') return '已拒绝并继续'
  if (status === 'cancelled') return '已取消'
  if (status === 'consumed') return '已处理'
  return '等待 Skill 审批'
}

function approvalStatusTone(status: string): 'warn' | 'success' | 'danger' | 'neutral' {
  if (status === 'approved') return 'success'
  if (status === 'rejected') return 'danger'
  if (status === 'cancelled' || status === 'consumed') return 'neutral'
  return 'warn'
}

export default function SentinelFlowConversationPage() {
  const streamRef = useRef<HTMLDivElement | null>(null)
  const [runtimeState, setRuntimeState] = useState(() => getConversationRuntimeState())
  const [pendingCommand, setPendingCommand] = useState('')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [expandedToolSummaryIds, setExpandedToolSummaryIds] = useState<string[]>([])
  const { sessions, activeSessionId, running, runningSessionId, streamingReply, streamingStatus } = runtimeState

  const activeSession = useMemo(
    () => sessions.find((session) => session.id === activeSessionId) ?? sessions[0],
    [activeSessionId, sessions],
  )
  const commandText = activeSession?.draft ?? ''
  const history = activeSession?.history ?? []
  const orderedHistory = useMemo(() => [...history].reverse(), [history])
  const isActiveSessionRunning = running && runningSessionId === activeSession?.id
  const hasConversation = orderedHistory.length > 0 || isActiveSessionRunning
  const runningAgentStatus = streamingStatus.includes('子 Agent') ? streamingStatus : ''

  useEffect(() => {
    const element = streamRef.current
    if (!element) return
    element.scrollTop = element.scrollHeight
  }, [history, isActiveSessionRunning, streamingReply, streamingStatus])

  useEffect(() => subscribeConversationRuntime(setRuntimeState), [])

  function createNewSession() {
    createConversationSession()
    setExpandedId(null)
  }

  function handleClearConversation() {
    clearActiveConversation()
    setExpandedId(null)
  }

  function handleDeleteCurrentSession() {
    deleteActiveConversation()
    setExpandedId(null)
  }

  function toggleToolSummary(itemId: string) {
    setExpandedToolSummaryIds((current) => (
      current.includes(itemId) ? current.filter((id) => id !== itemId) : [...current, itemId]
    ))
  }

  function isExpandedPanel(itemId: string, panel: string): boolean {
    return expandedToolSummaryIds.includes(`${itemId}:${panel}`)
  }

  function toggleExpandedPanel(itemId: string, panel: string) {
    const key = `${itemId}:${panel}`
    setExpandedToolSummaryIds((current) => (
      current.includes(key) ? current.filter((id) => id !== key) : [...current, key]
    ))
  }

  async function handleStopCurrentRun() {
    await stopConversationRun()
  }

  async function handleRun() {
    await startConversationRun()
    setExpandedId(null)
  }

  return (
    <div className="sentinelflow-page-stack">
      <PageHeader
        title="对话指挥台"
        description="通过自然语言调用主 Agent 进行查询、分析和协同处理。"
        icon={<MessageSquareText className="w-8 h-8" />}
        action={
          <div className="sentinelflow-response-row">
            <button
              type="button"
              className="sentinelflow-ghost-button sentinelflow-inline-button"
              onClick={createNewSession}
              disabled={running}
            >
              <Plus className="h-4 w-4" />
              新建会话
            </button>
            {hasConversation ? (
              <button
                type="button"
                className="sentinelflow-ghost-button sentinelflow-inline-button"
                onClick={handleClearConversation}
                disabled={running}
              >
                <RotateCcw className="h-4 w-4" />
                清空会话
              </button>
            ) : null}
          </div>
        }
      />

      <Surface title="">
        <div className="sentinelflow-conversation-layout">
          <div className="sentinelflow-session-sidebar">
            <div className="sentinelflow-session-sidebar-label">对话历史</div>
            {sessions.map((session) => (
              <button
                key={session.id}
                type="button"
                className={`sentinelflow-session-chip${session.id === activeSession.id ? ' sentinelflow-session-chip-active' : ''}`}
                onClick={() => {
                  setConversationActiveSession(session.id)
                  setExpandedId(null)
                }}
              >
                <span>{session.title}</span>
              </button>
            ))}
            {sessions.length > 1 ? (
              <button
                type="button"
                className="sentinelflow-ghost-button sentinelflow-inline-button sentinelflow-session-delete-btn"
                onClick={handleDeleteCurrentSession}
                disabled={running}
              >
                <Trash2 className="h-4 w-4" />
                删除当前
              </button>
            ) : null}
          </div>

          <div className={`sentinelflow-chat-panel ${hasConversation ? '' : 'sentinelflow-chat-panel-empty'}`}>
            {hasConversation ? (
              <div ref={streamRef} className="sentinelflow-chat-stream">
                {orderedHistory.map((item) => {
                const assistantReply = summarizeAssistantReply(item.response)
                const isExpanded = expandedId === item.id
                const commandData = extractCommandData(item.response.data)
                const storedApproval = item.response.approval as ApprovalRequest | undefined
                const toolCalls = Array.isArray(commandData.tool_calls) ? commandData.tool_calls as ToolCallLike[] : []
                const workerToolCalls = Array.isArray(commandData.worker_result?.tool_calls) ? commandData.worker_result.tool_calls as ToolCallLike[] : []
                const workerResults = Array.isArray(commandData.worker_results) ? commandData.worker_results as WorkerStepLike[] : []
                const primaryAgent = typeof commandData.primary_agent === 'string' ? commandData.primary_agent : ''
                const workerAgent = typeof commandData.worker_agent === 'string' ? commandData.worker_agent : (typeof commandData.worker_result?.agent_name === 'string' ? commandData.worker_result.agent_name : '')
                const delegationReason = typeof commandData.delegation_reason === 'string' ? commandData.delegation_reason : ''
                const approvalRequest = commandData.approval_request as ApprovalRequest | undefined
                const normalizedApprovalRequest = approvalRequest ? {
                  approval_id: approvalRequest.approval_id || '',
                  run_id: approvalRequest.run_id || '',
                  scope_type: approvalRequest.scope_type || 'conversation',
                  scope_ref: approvalRequest.scope_ref || '',
                  status: approvalRequest.status || 'pending',
                  skill_name: approvalRequest.skill_name || '',
                  arguments: approvalRequest.arguments || {},
                  arguments_fingerprint: approvalRequest.arguments_fingerprint || '',
                  approval_required: approvalRequest.approval_required ?? true,
                  checkpoint_thread_id: approvalRequest.checkpoint_thread_id || '',
                  checkpoint_ns: approvalRequest.checkpoint_ns || '',
                  parent_checkpoint_thread_id: approvalRequest.parent_checkpoint_thread_id,
                  parent_checkpoint_ns: approvalRequest.parent_checkpoint_ns,
                  tool_call_id: approvalRequest.tool_call_id,
                  parent_tool_call_id: approvalRequest.parent_tool_call_id,
                  created_at: approvalRequest.created_at || '',
                  decided_at: approvalRequest.decided_at,
                  arguments_summary: approvalRequest.arguments_summary || '无参数',
                  message: approvalRequest.message || '',
                } satisfies ApprovalRequest : undefined
                const shouldUseCurrentApprovalRequest = Boolean(
                  normalizedApprovalRequest?.approval_id &&
                  (item.response.route === 'approval_required' || normalizedApprovalRequest.status === 'pending')
                )
                const approvalCard = shouldUseCurrentApprovalRequest ? normalizedApprovalRequest : (storedApproval ?? normalizedApprovalRequest)
                const approvalStatus = String(approvalCard?.status || '')
                const isApprovalPending = approvalStatus === 'pending' || (!approvalStatus && item.response.route === 'approval_required')
                const resultTone = isApprovalPending ? 'warn' : (item.response.success ? 'success' : 'warn')
                const resultLabel = isApprovalPending ? '待审批' : (item.response.success ? '已完成' : '失败')
                const resultCode = isApprovalPending ? 'pending_approval' : (item.response.success ? 'success' : 'error')
                const showApprovalCard = Boolean(approvalCard?.skill_name)
                const hideExecutionSummary = isApprovalPending
                const toolSummaryExpanded = expandedToolSummaryIds.includes(item.id)
                const workerChainExpanded = isExpandedPanel(item.id, 'workers')
                const hasAnyToolSummary = toolCalls.length > 0 || workerToolCalls.length > 0

                return (
                  <div key={item.id} className="sentinelflow-chat-turn">
                    <div className="sentinelflow-chat-meta">
                      <span>{new Date(item.createdAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</span>
                    </div>

                    <div className="sentinelflow-chat-bubble sentinelflow-chat-bubble-user">
                      <CollapsibleChatMarkdown content={item.command} inverted />
                    </div>

                    <div className="sentinelflow-chat-bubble sentinelflow-chat-bubble-assistant">
                      <div className="sentinelflow-response-row">
                        <strong>SentinelFlow</strong>
                      </div>
                      {(primaryAgent || workerAgent) ? (
                        <div className="sentinelflow-tool-call-summary">
                          {primaryAgent ? `主 Agent：${primaryAgent}` : ''}
                          {primaryAgent && workerAgent ? '  ·  ' : ''}
                          {workerAgent ? `子 Agent：${workerAgent}` : ''}
                        </div>
                      ) : null}
                      {delegationReason ? (
                        <div className="sentinelflow-tool-call-summary">
                          分派原因：{delegationReason}
                        </div>
                      ) : null}
                      {showApprovalCard ? (
                        <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
                          <div className="sentinelflow-response-row">
                            <div className="font-semibold">{approvalStatusLabel(approvalStatus || 'pending')}</div>
                            <StatusBadge tone={approvalStatusTone(approvalStatus || 'pending')}>
                              {approvalStatus || 'pending'}
                            </StatusBadge>
                          </div>
                          <div className="mt-1">{approvalCard?.message || `Skill「${approvalCard?.skill_name || '未命名 Skill'}」需要审批。`}</div>
                          <div className="mt-1 text-xs text-amber-800">参数：{approvalCard?.arguments_summary || '无参数'}</div>
                          {approvalCard?.status === 'pending' && approvalCard.approval_id ? (
                            <div className="mt-3 flex gap-2">
                              <button
                                type="button"
                                className="sentinelflow-primary-button"
                                onClick={() => void resolveConversationApproval(item.id, approvalCard.approval_id || '', 'approve')}
                                disabled={running}
                              >
                                批准并继续
                              </button>
                              <button
                                type="button"
                                className="sentinelflow-ghost-button"
                                onClick={() => void resolveConversationApproval(item.id, approvalCard.approval_id || '', 'reject')}
                                disabled={running}
                              >
                                拒绝并继续
                              </button>
                            </div>
                          ) : null}
                        </div>
                      ) : null}
                      {!hideExecutionSummary ? <MarkdownContent content={assistantReply} /> : null}
                      {!hideExecutionSummary && hasAnyToolSummary ? (
                        <button
                          type="button"
                          className="sentinelflow-tool-call-summary text-left text-gray-500 transition-colors hover:text-gray-700"
                          onClick={() => toggleToolSummary(item.id)}
                        >
                          {toolSummaryExpanded ? '收起技能调用详情' : `调用技能 ${toolCalls.length + workerToolCalls.length} 次`}
                        </button>
                      ) : null}
                      {!hideExecutionSummary && toolSummaryExpanded && toolCalls.length ? (
                        <div className="sentinelflow-tool-call-list">
                          {toolCalls.map((toolCall, index) => (
                            <div key={`${item.id}-tool-${index}`} className="sentinelflow-tool-call-card">
                              <div className="sentinelflow-response-row">
                                <StatusBadge tone="neutral">tool</StatusBadge>
                                <strong>{toolCall.name || `tool-${index + 1}`}</strong>
                              </div>
                              <span>{formatToolArguments(toolCall.args)}</span>
                            </div>
                          ))}
                        </div>
                      ) : null}
                      {!hideExecutionSummary && toolSummaryExpanded && workerToolCalls.length ? (
                        <>
                          <div className="sentinelflow-tool-call-summary">
                            {workerAgent ? `${workerAgent} 调用了 ${workerToolCalls.length} 次技能` : `子 Agent 调用了 ${workerToolCalls.length} 次技能`}
                          </div>
                          <div className="sentinelflow-tool-call-list">
                            {workerToolCalls.map((toolCall, index) => (
                              <div key={`${item.id}-worker-tool-${index}`} className="sentinelflow-tool-call-card">
                                <div className="sentinelflow-response-row">
                                  <StatusBadge tone="warn">子 Agent tool</StatusBadge>
                                  <strong>{toolCall.name || `tool-${index + 1}`}</strong>
                                </div>
                                <span>{formatToolArguments(toolCall.args)}</span>
                              </div>
                            ))}
                          </div>
                        </>
                      ) : null}
                      {!hideExecutionSummary && workerResults.length > 1 ? (
                        <div className="mt-2">
                          <button
                            type="button"
                            className="sentinelflow-tool-call-summary text-left text-gray-500 transition-colors hover:text-gray-700"
                            onClick={() => toggleExpandedPanel(item.id, 'workers')}
                          >
                            {workerChainExpanded ? '收起子 Agent 串联步骤' : `子 Agent 串联步骤 ${workerResults.length} 步`}
                          </button>
                          {workerChainExpanded ? (
                            <div className="sentinelflow-tool-call-list">
                              {workerResults.map((step, index) => (
                                <div key={`${item.id}-worker-step-${index}`} className="sentinelflow-tool-call-card">
                                  <div className="sentinelflow-response-row">
                                    <StatusBadge tone="warn">{`步骤 ${index + 1}`}</StatusBadge>
                                    <strong>{step.worker || step.worker_agent || step.agent_name || `worker-${index + 1}`}</strong>
                                  </div>
                                </div>
                              ))}
                            </div>
                          ) : null}
                        </div>
                      ) : null}
                      {item.response.error && !isApprovalPending ? <div className="sentinelflow-message-block sentinelflow-message-error">{item.response.error}</div> : null}
                      <div className="sentinelflow-chat-actions">
                        <StatusBadge tone={resultTone}>{resultLabel}</StatusBadge>
                        <button type="button" className="sentinelflow-ghost-button" onClick={() => setExpandedId((current) => current === item.id ? null : item.id)}>
                          {isExpanded ? '收起详情' : '查看详情'}
                        </button>
                      </div>
                      {isExpanded ? (
                        <ConversationResponseDetails
                          response={item.response}
                          resultTone={resultTone}
                          resultCode={resultCode}
                          approvalCard={approvalCard}
                        />
                      ) : null}
                    </div>
                  </div>
                )
                })}

                {isActiveSessionRunning ? (
                  <div className="sentinelflow-chat-turn">
                  {runtimeState.pendingCommand.trim() ? (
                    <div className="sentinelflow-chat-bubble sentinelflow-chat-bubble-user">
                      <CollapsibleChatMarkdown content={runtimeState.pendingCommand} inverted />
                    </div>
                  ) : null}
                  <div className="sentinelflow-chat-bubble sentinelflow-chat-bubble-assistant">
                    <div className="sentinelflow-response-row">
                      <strong>SentinelFlow</strong>
                    </div>
                    <MarkdownContent content={sanitizeDisplayText(streamingReply) || (runningAgentStatus ? '正在协同处理，请稍候…' : streamingStatus)} />
                    {runningAgentStatus ? (
                      <div className="sentinelflow-tool-call-summary">{runningAgentStatus}</div>
                    ) : null}
                  </div>
                </div>
                ) : null}
              </div>
            ) : (
              <div className="sentinelflow-chat-empty-state">
                <h2 className="sentinelflow-chat-empty-title">有什么可以帮你处理的？</h2>
              </div>
            )}

            <div className={`sentinelflow-chat-composer ${hasConversation ? '' : 'sentinelflow-chat-composer-home'}`}>
              <textarea
                id="sentinelflow-command-input"
                className="sentinelflow-command-input"
                placeholder="给 SentinelFlow 发送消息"
                value={commandText}
                onChange={(event) => setConversationDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.nativeEvent.isComposing) {
                    return
                  }
                  if (event.key === 'Enter' && !event.shiftKey && !isActiveSessionRunning) {
                    event.preventDefault()
                    void handleRun()
                  }
                }}
                rows={4}
              />
              <div className="sentinelflow-chat-composer-actions">
                <button
                  type="button"
                  className="sentinelflow-chat-send-button"
                  onClick={isActiveSessionRunning ? () => void handleStopCurrentRun() : () => void handleRun()}
                  disabled={!isActiveSessionRunning && !commandText.trim()}
                  aria-label={isActiveSessionRunning ? '停止当前任务' : '发送命令'}
                >
                  {isActiveSessionRunning ? <Square className="h-4 w-4" /> : <Send className="h-4 w-4" />}
                </button>
              </div>
            </div>
          </div>
        </div>
      </Surface>
    </div>
  )
}
