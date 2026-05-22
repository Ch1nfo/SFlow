import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, ChevronRight, Clock, ListTodo, RotateCcw, ShieldCheck, XCircle } from 'lucide-react'
import {
  decideApproval,
  fetchAlertTaskDetail,
  fetchRuntimeSettings,
  handleAlertAction,
  type ApprovalDecisionResponse,
  type AlertActionResponse,
  type AlertTask,
  type ExecutionTraceItem,
} from '@/api/sentinelflow'
import JsonPreview from '@/components/sentinelflow/JsonPreview'
import MarkdownContent from '@/components/sentinelflow/MarkdownContent'
import Surface from '@/components/sentinelflow/Surface'
import StatusBadge from '@/components/sentinelflow/StatusBadge'
import PageHeader from '@/components/common/PageHeader'
import { withProductName } from '@/config/brand'
import { useSentinelFlowAsyncData } from '@/hooks/useSentinelFlowAsyncData'
import { useSentinelFlowLiveRefresh } from '@/hooks/useSentinelFlowLiveRefresh'
import { useSentinelFlowPollStore } from '@/hooks/useSentinelFlowPollStore'
import { readSessionValue, writeSessionValue } from '@/utils/sentinelflowLocalState'
import { getRuntimeActivityBadgeLabel, getRuntimeActivityStatus, publishRuntimeActivity, readRuntimeActivity, subscribeRuntimeActivity, type RuntimeActivity } from '@/utils/sentinelflowRuntimeSync'
import { getEffectiveTaskStatus } from '@/utils/sentinelflowTaskStatus'

type TaskFilter = 'all' | 'queued' | 'running' | 'awaiting_approval' | 'succeeded' | 'completed' | 'pending_closure' | 'failed'
const TASK_FILTER_KEY = 'sentinelflow:tasks:filter'
const TASK_LIST_INITIAL_RENDER_COUNT = 120
const TASK_LIST_RENDER_INCREMENT = 120

type ToolInvocationResult = {
  key: string
  skillName: string
  toolName: string
  toolCallId: string
  success: boolean | null
  source: string
  input: Record<string, unknown>
  output: Record<string, unknown>
  outputNote: string
  raw: Record<string, unknown>
}

const TASK_FILTER_LABELS: Record<TaskFilter, string> = {
  all: '全部',
  queued: '排队中',
  running: '执行中',
  awaiting_approval: '待审批',
  succeeded: '已完成',
  completed: '已被人工处置',
  pending_closure: '待结单',
  failed: '失败',
}

function getTaskStatusLabel(status: TaskFilter | AlertTask['status']) {
  if (status === 'queued') return '排队中'
  if (status === 'running') return '执行中'
  if (status === 'pending_closure') return '未执行'
  if (status === 'pending_manual_closure') return '自动完成待结单'
  if (status === 'awaiting_approval') return '待审批'
  if (status === 'succeeded') return '已完成'
  if (status === 'completed') return '已被人工处置'
  if (status === 'failed') return '失败'
  return '全部'
}

function isApprovalPendingAction(result: AlertActionResponse | ApprovalDecisionResponse): boolean {
  const data = result.data ?? {}
  const approvalRequest = data.approval_request
  return (
    result.task?.status === 'awaiting_approval' ||
    data.approval_pending === true ||
    (typeof approvalRequest === 'object' && approvalRequest !== null && String((approvalRequest as Record<string, unknown>).approval_id ?? '').trim().length > 0)
  )
}

function getDispositionLabel(value: string) {
  if (value === 'true_attack') return '真实攻击'
  if (value === 'business_trigger') return '业务触发'
  if (value === 'false_positive') return '误报'
  if (value === 'handled_manually') return '已被人工处置'
  return value || '未明确'
}

function splitAlertIps(value: unknown): string[] {
  const text = String(value ?? '').trim()
  if (!text) return []
  return text
    .split(/[,\n，;；]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function formatIpPreview(value: unknown, limit = 3): { text: string; fullText: string } {
  const ips = splitAlertIps(value)
  if (!ips.length) {
    const fallback = String(value ?? '').trim()
    return { text: fallback || '未提供', fullText: fallback || '未提供' }
  }
  if (ips.length <= limit) {
    const joined = ips.join(',')
    return { text: joined, fullText: joined }
  }
  return {
    text: `${ips.slice(0, limit).join(',')}...共${ips.length}个`,
    fullText: ips.join(','),
  }
}

function getTone(task: AlertTask): 'neutral' | 'success' | 'warn' | 'danger' {
  const status = getEffectiveTaskStatus(task)
  if (status === 'succeeded' || status === 'completed') return 'success'
  if (status === 'pending_closure' || status === 'awaiting_approval') return 'warn'
  if (status === 'pending_manual_closure') return 'warn'
  if (status === 'failed') return 'danger'
  if (status === 'running') return 'warn'
  return 'neutral'
}

function isFailedBucketStatus(status: string): boolean {
  return status === 'failed' || status === 'pending_closure'
}

function isReDisposableTask(task: AlertTask): boolean {
  return isFailedBucketStatus(String(getEffectiveTaskStatus(task)))
}

function toSortableTime(value: string | undefined) {
  const text = String(value ?? '').trim()
  if (!text) return 0
  const timestamp = Date.parse(text.replace(' ', 'T'))
  return Number.isNaN(timestamp) ? 0 : timestamp
}

function getTraceTone(item: ExecutionTraceItem): 'success' | 'danger' | 'warn' | 'info' {
  if (item.success === true) return 'success'
  if (item.success === false) return 'danger'
  if (item.phase === 'skill_runs' || item.phase === 'actions') return 'info'
  return 'warn'
}

function buildFallbackTrace(task: AlertTask | null): ExecutionTraceItem[] {
  if (!task) return []
  const payload = (task.payload?.alert_data as Record<string, unknown> | undefined) ?? {}
  const workflowSelection = (task.payload?.workflow_selection as Record<string, unknown> | undefined) ?? {}
  const trace: ExecutionTraceItem[] = [
    {
      phase: 'alert_received',
      title: '接收告警',
      summary: '已接收任务告警上下文。',
      success: true,
      data: {
        eventIds: task.event_ids,
        alert_name: String(payload.alert_name ?? task.title ?? '').trim(),
        sip: payload.sip ?? '',
        dip: payload.dip ?? '',
        alert_time: payload.alert_time ?? task.alert_time ?? '',
        current_judgment: payload.current_judgment ?? '',
        history_judgment: payload.history_judgment ?? '',
        payload: payload.payload ?? '',
      },
    },
  ]
  if (Object.keys(workflowSelection).length) {
    trace.push({
      phase: 'workflow_selection',
      title: 'Workflow 记录',
      summary: String(workflowSelection.reason ?? workflowSelection.workflow_id ?? '存在历史 Workflow 记录。').trim(),
      success: true,
      data: workflowSelection,
    })
  }
  if (task.status === 'queued') {
    trace.push({
      phase: 'final_status',
      title: '当前执行状态',
      summary: '任务已进入排队中，等待自动执行或人工处置。',
      success: null,
      data: {
        status: 'queued',
        success: null,
      },
    })
  } else if (task.status === 'running') {
    trace.push({
      phase: 'final_status',
      title: '当前执行状态',
      summary: '任务正在执行中，请等待最新结果刷新。',
      success: null,
      data: {
        status: 'running',
        success: null,
      },
    })
  }
  return trace
}

function normalizeWorkflowRuns(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) return []
  return value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
}

function getTaskFlowLabel(task: AlertTask): string {
  const result = (task.last_result_data ?? {}) as Record<string, unknown>
  const workflowRuns = normalizeWorkflowRuns(result.workflow_runs)
  const workflowRun = workflowRuns[0] ?? null
  if (workflowRun) {
    return `Workflow / ${String(workflowRun.workflow_name ?? workflowRun.workflow_id ?? '未命名流程').trim() || '未命名流程'}`
  }
  const workflowName = String(task.workflow_name ?? '').trim()
  if (!workflowName || workflowName === 'agent_react') return '主 Agent'
  return workflowName
}

function asRecord(value: unknown): Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function asRecordArray(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) return []
  return value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
}

function parseMaybeJsonRecord(value: unknown): Record<string, unknown> {
  if (value && typeof value === 'object' && !Array.isArray(value)) return value as Record<string, unknown>
  if (typeof value !== 'string') return {}
  try {
    const parsed = JSON.parse(value)
    return asRecord(parsed)
  } catch {
    return {}
  }
}

function compactDisplayText(value: unknown, limit = 180): string {
  const text = String(value ?? '').replace(/\s+/g, ' ').trim()
  if (!text) return ''
  return text.length > limit ? `${text.slice(0, limit).trim()}...` : text
}

function summarizeLargeTextField(value: unknown): string {
  const text = String(value ?? '')
  if (!text.trim()) return ''
  const lowered = text.toLowerCase()
  const isHttp = lowered.includes('http/1.') || lowered.includes('content-length:') || text.includes('\r\n\r\n')
  const urlEncodedMatches = text.match(/%[0-9a-fA-F]{2}/g)?.length ?? 0
  const controlChars = Array.from(text).filter((char) => char.charCodeAt(0) < 32 && !['\r', '\n', '\t'].includes(char)).length
  if (text.length > 1000 || isHttp || urlEncodedMatches >= 12 || controlChars > 0) {
    const kind = isHttp ? 'HTTP payload' : urlEncodedMatches >= 12 ? 'URL 编码内容' : controlChars > 0 ? '二进制/控制字符内容' : '大字段'
    return `${kind}，长度 ${text.length} 字符，内容已省略`
  }
  return compactDisplayText(text, 240)
}

function getToolInvocationOutput(item: Record<string, unknown>): Record<string, unknown> {
  const payload = asRecord(item.payload)
  if (Object.keys(payload).length) return payload
  const result = asRecord(item.result)
  if (Object.keys(result).length) {
    const argumentsRecord = asRecord(item.arguments)
    const skillName = String(item.skill_name ?? '').trim().toLowerCase()
    const resultKeys = Object.keys(result)
    const isSparseClosureResult =
      ['exec', 'close', 'soc_close', 'alert_close'].includes(skillName) &&
      resultKeys.length <= 2 &&
      Object.keys(argumentsRecord).length
    if (isSparseClosureResult) {
      return {
        returned: result,
        submitted_arguments: argumentsRecord,
      }
    }
    return result
  }
  const toolPayload = asRecord(item.tool_payload)
  const toolPayloadData = asRecord(toolPayload.data)
  if (Object.keys(toolPayloadData).length) return toolPayloadData
  const resultSummary = asRecord(item.result_summary)
  if (Object.keys(resultSummary).length) {
    const summaryOutput: Record<string, unknown> = {}
    if (typeof resultSummary.success === 'boolean') summaryOutput.success = resultSummary.success
    if (String(resultSummary.summary ?? '').trim()) summaryOutput.summary = resultSummary.summary
    const keyFacts = asRecord(resultSummary.key_facts)
    if (Object.keys(keyFacts).length) summaryOutput.key_facts = keyFacts
    if (String(resultSummary.error ?? '').trim()) summaryOutput.error = resultSummary.error
    if (String(resultSummary.result_ref ?? '').trim()) summaryOutput.result_ref = resultSummary.result_ref
    if (Object.keys(summaryOutput).length) return summaryOutput
  }
  if (String(item.error ?? '').trim() || String(item.tool_error ?? '').trim()) {
    return {
      error: String(item.error ?? item.tool_error ?? '').trim(),
    }
  }
  return {}
}

function getToolInvocationOutputNote(item: Record<string, unknown>): string {
  const resultSummary = asRecord(item.result_summary)
  if (Object.keys(resultSummary).length && !Object.keys(asRecord(item.result)).length && !Object.keys(asRecord(item.payload)).length) {
    return '该输出来自压缩后的 result_summary；完整原始结果请查看处置全流程或运行日志。'
  }
  const skillName = String(item.skill_name ?? '').trim().toLowerCase()
  const result = asRecord(item.result)
  const argumentsRecord = asRecord(item.arguments)
  if (
    ['exec', 'close', 'soc_close', 'alert_close'].includes(skillName) &&
    Object.keys(result).length <= 2 &&
    Object.keys(argumentsRecord).length
  ) {
    return '结单接口返回字段较少，已同时展示本次实际提交参数。'
  }
  return ''
}

function isSuccessfulSkillItem(item: Record<string, unknown>): boolean {
  if (item.tool_success === false || item.success === false) return false
  if (String(item.tool_error ?? '').trim() || String(item.error ?? '').trim()) return false
  const toolPayload = asRecord(item.tool_payload)
  if (toolPayload.success === false || String(toolPayload.error ?? '').trim()) return false
  const payload = asRecord(item.payload)
  if (String(payload.error ?? '').trim()) return false
  const result = asRecord(item.result)
  if (String(result.error ?? '').trim()) return false
  if (item.tool_success === true || item.success === true || toolPayload.success === true) return true
  return false
}

function buildToolInvocation(item: Record<string, unknown>, source: string, fallbackIndex: number): ToolInvocationResult | null {
  const skillName = String(item.skill_name ?? '').trim()
  if (!skillName) return null
  const toolName = String(item.tool_name ?? 'execute_skill').trim() || 'execute_skill'
  const toolCallId = String(item.tool_call_id ?? '').trim()
  const input = asRecord(item.arguments)
  const output = getToolInvocationOutput(item)
  const outputNote = getToolInvocationOutputNote(item)
  const successValue = item.success
  const success = typeof successValue === 'boolean' ? successValue : null
  const key = toolCallId || `${skillName}-${source}-${fallbackIndex}-${JSON.stringify(input)}-${JSON.stringify(output)}`
  return {
    key,
    skillName,
    toolName,
    toolCallId,
    success,
    source,
    input,
    output,
    outputNote,
    raw: item,
  }
}

function buildToolInvocationFromSummary(item: Record<string, unknown>, source: string, fallbackIndex: number): ToolInvocationResult | null {
  const toolName = String(item.name ?? item.tool_name ?? 'execute_skill').trim() || 'execute_skill'
  const args = asRecord(item.args)
  const skillName = String(args.skill_name ?? item.skill_name ?? toolName).trim()
  if (!skillName) return null
  const input = parseMaybeJsonRecord(args.arguments)
  const resultSummary = asRecord(item.result_summary)
  const successValue = resultSummary.success ?? item.success ?? item.tool_success
  const success = typeof successValue === 'boolean' ? successValue : null
  const toolCallId = String(item.id ?? item.tool_call_id ?? resultSummary.result_ref ?? '').trim()
  const normalizedItem = {
    skill_name: skillName,
    tool_name: toolName,
    tool_call_id: toolCallId,
    arguments: input,
    result_summary: resultSummary,
    success,
    tool_success: success,
  }
  const output = getToolInvocationOutput(normalizedItem)
  const outputNote = getToolInvocationOutputNote(normalizedItem)
  const key = toolCallId || `${skillName}-${source}-${fallbackIndex}-${JSON.stringify(input)}-${JSON.stringify(output)}`
  return {
    key,
    skillName,
    toolName,
    toolCallId,
    success,
    source,
    input,
    output,
    outputNote,
    raw: { ...item, normalized_item: normalizedItem },
  }
}

function collectWorkerToolSummaries(value: unknown, sourcePrefix = ''): ToolInvocationResult[] {
  const results: ToolInvocationResult[] = []
  const walk = (workerResults: Array<Record<string, unknown>>, prefix: string) => {
    workerResults.forEach((worker, workerIndex) => {
      const workerName = String(worker.worker ?? worker.worker_agent ?? worker.agent_name ?? `worker-${workerIndex + 1}`).trim()
      const source = `${prefix}${workerName} · Worker compact result`
      asRecordArray(worker.tool_calls_summary).forEach((item) => {
        const invocation = buildToolInvocationFromSummary(item, source, results.length)
        if (invocation) results.push(invocation)
      })
      walk(asRecordArray(worker.worker_results), `${prefix}${workerName}/`)
    })
  }
  walk(asRecordArray(value), sourcePrefix)
  return results
}

function collectToolInvocationResults(resultData: Record<string, unknown>, trace: ExecutionTraceItem[]): ToolInvocationResult[] {
  const results: ToolInvocationResult[] = []
  const seen = new Set<string>()
  const addInvocation = (invocation: ToolInvocationResult | null) => {
    if (!invocation || seen.has(invocation.key)) return
    seen.add(invocation.key)
    results.push(invocation)
  }
  const addItem = (item: Record<string, unknown>, source: string) => {
    addInvocation(buildToolInvocation(item, source, results.length))
  }

  collectWorkerToolSummaries(resultData.worker_results).forEach(addInvocation)
  asRecordArray(resultData.workflow_runs).forEach((workflowRun) => {
    const workflowName = String(workflowRun.workflow_name ?? workflowRun.workflow_id ?? 'workflow').trim()
    collectWorkerToolSummaries(workflowRun.worker_results, `${workflowName}/`).forEach(addInvocation)
  })

  trace.forEach((traceItem) => {
    const data = asRecord(traceItem.data)
    asRecordArray(data.runs).forEach((item) => addItem(item, traceItem.title || '技能调用记录'))
    asRecordArray(data.steps).forEach((item) => addItem(item, traceItem.title || '处置动作'))
    if (traceItem.phase === 'closure') {
      addItem(data, traceItem.title || '结单结果')
    }
  })

  return results
}

function buildTraceSummaryRows(item: ExecutionTraceItem): Array<{ label: string; value: string }> {
  const data = asRecord(item.data)
  const rows: Array<{ label: string; value: string }> = []
  const add = (label: string, value: unknown, limit = 180) => {
    const text = compactDisplayText(value, limit)
    if (text) rows.push({ label, value: text })
  }
  const addRaw = (label: string, value: unknown) => {
    const text = String(value ?? '').trim()
    if (text) rows.push({ label, value: text })
  }

  if (item.phase === 'alert_received') {
    addRaw('事件号', data.eventIds)
    addRaw('告警名称', data.alert_name)
    addRaw('源 IP', data.sip)
    addRaw('目标 IP', data.dip)
    addRaw('目标端口', data.dport)
    addRaw('告警时间', data.alert_time)
    addRaw('告警源', data.alert_source)
    add('当前研判', data.current_judgment, 220)
    add('历史研判', data.history_judgment, 220)
    const payloadSummary = summarizeLargeTextField(data.payload)
    if (payloadSummary) rows.push({ label: 'payload', value: payloadSummary })
    return rows
  }

  if (item.phase === 'workflow_selection') {
    addRaw('Workflow', data.workflow_name ?? data.workflow_id)
    addRaw('执行模式', data.execution_mode)
    addRaw('执行状态', data.execution_status)
    add('返回摘要', data.summary ?? data.reason)
    const steps = asRecordArray(data.steps)
    if (steps.length) rows.push({ label: '步骤数', value: String(steps.length) })
    const nextStep = asRecord(data.next_step)
    if (Object.keys(nextStep).length) addRaw('下一步', nextStep.name ?? nextStep.agent ?? nextStep.id)
    return rows
  }

  if (item.phase === 'context_control') {
    const records = asRecordArray(data.records)
    const warnings = Array.isArray(data.context_warnings) ? data.context_warnings : []
    const missing = Array.isArray(data.missing_required_inputs) ? data.missing_required_inputs : []
    rows.push({ label: '上下文记录', value: `${records.length} 条` })
    rows.push({ label: '上下文告警', value: warnings.length ? warnings.map(String).join(', ') : '无' })
    rows.push({ label: '缺失输入', value: missing.length ? `${missing.length} 项` : '无' })
    return rows
  }

  if (item.phase === 'analysis') {
    addRaw('分类', getDispositionLabel(String(data.disposition ?? '').trim()))
    add('结论', data.summary, 220)
    add('理由', data.reason, 220)
    const evidence = Array.isArray(data.evidence) ? data.evidence.map((entry) => compactDisplayText(entry, 120)).filter(Boolean) : []
    if (evidence.length) rows.push({ label: '关键依据', value: evidence.join('；') })
    return rows
  }

  if (item.phase === 'skill_runs' || item.phase === 'actions') {
    const items = [...asRecordArray(data.runs), ...asRecordArray(data.steps)]
    const successful = items.filter(isSuccessfulSkillItem)
    const failed = items.filter((entry) => !isSuccessfulSkillItem(entry))
    if (items.length) rows.push({ label: '调用统计', value: `成功 ${successful.length} / 失败或拦截 ${failed.length}` })
    const successfulNames = successful.map((entry) => String(entry.skill_name ?? '').trim()).filter(Boolean)
    const failedNames = failed.map((entry) => String(entry.skill_name ?? '').trim()).filter(Boolean)
    if (successfulNames.length) rows.push({ label: '成功 Skill', value: successfulNames.join(', ') })
    if (failedNames.length) rows.push({ label: '失败/拦截 Skill', value: failedNames.join(', ') })
    return rows
  }

  if (item.phase === 'closure') {
    addRaw('状态码', data.status)
    add('备注', data.memo)
    addRaw('处置类型', data.detailMsg ?? data.detail_msg)
    addRaw('事件号', data.eventIds)
    return rows
  }

  if (item.phase === 'final_status') {
    addRaw('状态', data.status)
    addRaw('动作', data.action)
    add('错误', data.error, 220)
    return rows
  }

  addRaw('状态', data.status ?? data.execution_status ?? data.action)
  add('摘要', data.summary ?? data.reason ?? data.error, 220)
  return rows
}

function TraceDataSummary({ item }: { item: ExecutionTraceItem }) {
  const rows = buildTraceSummaryRows(item)
  if (!rows.length) return null
  return (
    <div className="mb-3 grid gap-2 md:grid-cols-2">
      {rows.map((row) => (
        <div key={`${item.phase}-${row.label}`} className="rounded-lg border border-slate-200 bg-slate-50 p-3">
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{row.label}</div>
          <div className="mt-1 break-words text-sm text-slate-800">{row.value}</div>
        </div>
      ))}
    </div>
  )
}

function ToolInvocationResults({ tools, ownerId }: { tools: ToolInvocationResult[]; ownerId: string }) {
  const [openKeys, setOpenKeys] = useState<Record<string, boolean>>({})

  useEffect(() => {
    setOpenKeys({})
  }, [ownerId])

  if (!tools.length) {
    return <p className="sentinelflow-muted-text">暂无可展示的工具调用结果。</p>
  }

  return (
    <div className="space-y-3">
      {tools.map((tool, index) => {
        const open = Boolean(openKeys[tool.key])
        return (
          <div key={tool.key} className="rounded-xl border border-gray-200 bg-white p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">调用 {index + 1}</span>
                  <StatusBadge tone={tool.success === false ? 'danger' : tool.success === true ? 'success' : 'info'}>{tool.skillName}</StatusBadge>
                  <span className="text-xs text-gray-500">{tool.source}</span>
                </div>
                <p className="text-sm text-gray-700">
                  {tool.toolCallId ? `${tool.toolName} / ${tool.toolCallId}` : tool.toolName}
                </p>
              </div>
              <button
                type="button"
                className="sentinelflow-ghost-button shrink-0"
                onClick={() => setOpenKeys((current) => ({ ...current, [tool.key]: !open }))}
              >
                {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                {open ? '收起详情' : '展开详情'}
              </button>
            </div>
            {open ? (
              <div className="mt-3 grid gap-3 xl:grid-cols-2">
                <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                  <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-600">输入</div>
                  <JsonPreview value={tool.input} />
                </div>
                <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                  <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-600">输出</div>
                  {tool.outputNote ? <div className="mb-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">{tool.outputNote}</div> : null}
                  <JsonPreview value={tool.output} />
                </div>
              </div>
            ) : null}
          </div>
        )
      })}
    </div>
  )
}

function ProcessTrace({ trace, traceOwnerId }: { trace: ExecutionTraceItem[]; traceOwnerId: string }) {
  const [openKeys, setOpenKeys] = useState<Record<string, boolean>>({})

  useEffect(() => {
    setOpenKeys({})
  }, [traceOwnerId])

  if (!trace.length) {
    return <p className="sentinelflow-muted-text">该任务生成于旧版本，暂无完整处置全流程明细。</p>
  }

  return (
    <div className="space-y-3">
      {trace.map((item, index) => {
        const detailKey = `${item.phase}-${index}`
        const open = Boolean(openKeys[detailKey])
        const data = item.data && Object.keys(item.data).length ? item.data : null
        const isFinalFactsStep = item.phase === 'final_facts' && data
        const finalFactsJudgment = isFinalFactsStep ? ((data.judgment as Record<string, unknown> | undefined) ?? {}) : {}
        const finalFactsClosure = isFinalFactsStep ? ((data.closure as Record<string, unknown> | undefined) ?? {}) : {}
        const finalFactsConsistency = isFinalFactsStep ? ((data.consistency as Record<string, unknown> | undefined) ?? {}) : {}
        const finalFactsOutcome = isFinalFactsStep ? ((data.task_outcome as Record<string, unknown> | undefined) ?? {}) : {}
        const finalFactsIssues = isFinalFactsStep && Array.isArray(finalFactsConsistency.issues)
          ? finalFactsConsistency.issues.map((issue) => String(issue).trim()).filter(Boolean)
          : []
        return (
          <div key={detailKey} className="rounded-xl border border-gray-200 bg-white p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 space-y-1">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">步骤 {index + 1}</span>
                  <StatusBadge tone={getTraceTone(item)}>{item.title}</StatusBadge>
                </div>
                {item.summary ? <p className="text-sm text-gray-700">{item.summary}</p> : null}
              </div>
              {data ? (
                <button
                  type="button"
                  className="sentinelflow-ghost-button shrink-0"
                  onClick={() => setOpenKeys((current) => ({ ...current, [detailKey]: !open }))}
                >
                  {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                  {open ? '收起详情' : '展开详情'}
                </button>
              ) : null}
            </div>
            {isFinalFactsStep ? (
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                <div className="rounded-lg border border-blue-100 bg-blue-50 p-3">
                  <div className="text-xs font-semibold uppercase tracking-wide text-blue-700">最终研判分类</div>
                  <div className="mt-1 text-sm font-semibold text-blue-950">{getDispositionLabel(String(finalFactsJudgment.disposition ?? '').trim())}</div>
                  <div className="mt-1 text-xs text-blue-900">
                    {`来源：${String(finalFactsJudgment.source ?? 'unknown').trim() || 'unknown'} / 置信度：${String(finalFactsJudgment.confidence ?? 'unknown').trim() || 'unknown'}`}
                  </div>
                </div>
                <div className="rounded-lg border border-emerald-100 bg-emerald-50 p-3">
                  <div className="text-xs font-semibold uppercase tracking-wide text-emerald-700">最终任务状态</div>
                  <div className="mt-1 text-sm font-semibold text-emerald-950">{getTaskStatusLabel(String(finalFactsOutcome.status ?? '').trim() || 'failed')}</div>
                  <div className="mt-1 text-xs text-emerald-900">
                    {`结单：${Boolean(finalFactsClosure.attempted) ? (Boolean(finalFactsClosure.success) ? '成功' : '失败') : '未执行'}${String(finalFactsClosure.status ?? '').trim() ? ` / 状态码：${String(finalFactsClosure.status ?? '').trim()}` : ''}`}
                  </div>
                </div>
                <div className={`rounded-lg border p-3 ${finalFactsIssues.length ? 'border-amber-200 bg-amber-50' : 'border-slate-200 bg-slate-50'} md:col-span-2`}>
                  <div className={`text-xs font-semibold uppercase tracking-wide ${finalFactsIssues.length ? 'text-amber-700' : 'text-slate-600'}`}>
                    一致性检查
                  </div>
                  <div className={`mt-1 text-sm ${finalFactsIssues.length ? 'text-amber-900' : 'text-slate-700'}`}>
                    {finalFactsIssues.length ? '检测到过程结果存在冲突，当前结果已按真实执行事实优先收敛。' : '未发现结果冲突，最终结果已与执行事实保持一致。'}
                  </div>
                  {finalFactsIssues.length ? (
                    <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-amber-900">
                      {finalFactsIssues.map((issue) => (
                        <li key={`${detailKey}-${issue}`}>{issue}</li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              </div>
            ) : null}
            {open && data ? (
              <div className="mt-3">
                <TraceDataSummary item={item} />
                <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">原始审计数据</div>
                <JsonPreview value={data} />
              </div>
            ) : null}
          </div>
        )
      })}
    </div>
  )
}

export default function SentinelFlowTasksPage() {
  const { data: poll, loading, error, reload: reloadPoll } = useSentinelFlowPollStore('all')
  const { data: settings } = useSentinelFlowAsyncData(fetchRuntimeSettings, [])
  const [activity, setActivity] = useState<RuntimeActivity | null>(() => {
    const current = readRuntimeActivity()
    return current?.type === 'alert_action' ? current : null
  })
  const [runningAction, setRunningAction] = useState('')
  const [filter, setFilter] = useState<TaskFilter>(() => readSessionValue<TaskFilter>(TASK_FILTER_KEY, 'all'))
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  const [finalJudgmentExpanded, setFinalJudgmentExpanded] = useState(false)
  const [toolResultsExpanded, setToolResultsExpanded] = useState(false)
  const [processExpanded, setProcessExpanded] = useState(false)
  const [selectedTaskDetail, setSelectedTaskDetail] = useState<AlertTask | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [visibleTaskCount, setVisibleTaskCount] = useState(TASK_LIST_INITIAL_RENDER_COUNT)
  const taskListPanelRef = useRef<HTMLDivElement | null>(null)
  const detailPanelRef = useRef<HTMLDivElement | null>(null)
  const detailRequestSeq = useRef(0)
  const [taskListPanelHeight, setTaskListPanelHeight] = useState<number | null>(null)
  const [taskListMaxHeight, setTaskListMaxHeight] = useState<number | null>(null)
  const tasks = poll?.tasks ?? []
  const autoExecuteEnabled = Boolean(poll?.auto_execute_enabled)
  const autoExecuteRunning = Boolean(poll?.auto_execute_running)

  useEffect(() => {
    writeSessionValue(TASK_FILTER_KEY, filter)
  }, [filter])

  useEffect(() => {
    return subscribeRuntimeActivity((next) => {
      if (next.type !== 'alert_action') return
      setActivity(next)
      void reloadPoll({ silent: true })
    })
  }, [reloadPoll])

  const filteredTasks = useMemo(() => {
    const base = filter === 'all'
      ? tasks
      : tasks.filter((task) => {
          const status = String(getEffectiveTaskStatus(task))
          if (filter === 'failed') return isFailedBucketStatus(status)
          if (filter === 'succeeded') return status === 'succeeded'
          if (filter === 'pending_closure') return status === 'pending_closure' || status === 'pending_manual_closure'
          return status === filter
        })
    return [...base].sort((left, right) => toSortableTime(right.alert_time) - toSortableTime(left.alert_time))
  }, [filter, tasks])
  const visibleFilteredTasks = filteredTasks.slice(0, visibleTaskCount)

  useEffect(() => {
    setSelectedTaskId((current) => {
      if (!filteredTasks.length) return null
      if (current && filteredTasks.some((task) => task.task_id === current)) return current
      return filteredTasks[0]?.task_id ?? null
    })
  }, [filteredTasks])

  useEffect(() => {
    setFinalJudgmentExpanded(false)
    setToolResultsExpanded(false)
    setProcessExpanded(false)
  }, [selectedTaskId])

  useEffect(() => {
    setVisibleTaskCount(TASK_LIST_INITIAL_RENDER_COUNT)
  }, [filter])

  const selectedTaskSummary =
    filteredTasks.find((task) => task.task_id === selectedTaskId) ??
    filteredTasks[0] ??
    null
  const selectedTask = selectedTaskDetail?.task_id === selectedTaskSummary?.task_id ? selectedTaskDetail : selectedTaskSummary

  useEffect(() => {
    const taskId = selectedTaskSummary?.task_id ?? ''
    const requestSeq = detailRequestSeq.current + 1
    detailRequestSeq.current = requestSeq
    setSelectedTaskDetail(null)
    setDetailError(null)
    if (!taskId) return
    void fetchAlertTaskDetail(taskId)
      .then((response) => {
        if (detailRequestSeq.current !== requestSeq) return
        setSelectedTaskDetail(response.task)
        setDetailError(null)
      })
      .catch((detailLoadError) => {
        if (detailRequestSeq.current !== requestSeq) return
        setSelectedTaskDetail(null)
        setDetailError(detailLoadError instanceof Error ? detailLoadError.message : '任务详情加载失败，当前展示列表摘要。')
      })
  }, [selectedTaskSummary?.task_id])

  useEffect(() => {
    const detailNode = detailPanelRef.current
    const listPanelNode = taskListPanelRef.current
    if (!detailNode || !listPanelNode || typeof ResizeObserver === 'undefined') return

    const syncHeight = () => {
      try {
        const detailHeight = Math.max(0, Math.round(detailNode.getBoundingClientRect().height))
        const scrollNode = listPanelNode.querySelector('.sentinelflow-task-list-scroll') as HTMLDivElement | null
        const scrollHeight = scrollNode?.offsetHeight ?? 0
        const chromeHeight = Math.max(
          0,
          Math.round(listPanelNode.offsetHeight - scrollHeight),
        )
        const nextHeight = Math.max(0, detailHeight - chromeHeight)
        setTaskListPanelHeight(detailHeight || null)
        setTaskListMaxHeight(nextHeight || null)
      } catch {
        setTaskListPanelHeight(null)
        setTaskListMaxHeight(null)
      }
    }

    try {
      syncHeight()
      const observer = new ResizeObserver(() => syncHeight())
      observer.observe(detailNode)
      return () => observer.disconnect()
    } catch {
      setTaskListPanelHeight(null)
      setTaskListMaxHeight(null)
      return
    }
  }, [selectedTaskId, finalJudgmentExpanded, toolResultsExpanded, processExpanded, filteredTasks.length, selectedTask?.task_id, selectedTask?.status])

  const refreshTasks = useCallback(() => {
    void reloadPoll({ silent: true })
  }, [reloadPoll])

  useSentinelFlowLiveRefresh(refreshTasks, {
    intervalMs: autoExecuteEnabled || tasks.some((task) => task.status === 'running') ? 2000 : 5000,
  })

  async function handleAutoExecuteToggle() {
    const action = autoExecuteEnabled ? 'auto_execute_stop' : 'auto_execute_start'
    setRunningAction(action)
    try {
      const result = await handleAlertAction(action, undefined, undefined, 'all')
      const next: RuntimeActivity = {
        type: 'alert_action',
        title: autoExecuteEnabled ? '停止自动执行' : '开始自动执行',
        detail: result.success ? (autoExecuteEnabled ? '后台自动执行已停止。' : '后台自动执行已开启。') : result.error ?? '自动执行失败。',
        success: result.success,
        timestamp: new Date().toISOString(),
      }
      setActivity(next)
      publishRuntimeActivity(next)
      void reloadPoll({ force: true, silent: true })
    } finally {
      setRunningAction('')
    }
  }

  async function handleRetry(task: AlertTask) {
    setRunningAction(task.task_id)
    try {
      const result = await handleAlertAction('retry_task', task)
      const next: RuntimeActivity = {
        type: 'alert_action',
        title: `${task.title} / retry`,
        detail: isApprovalPendingAction(result) ? '任务已暂停，等待技能审批。' : result.success ? '任务重试完成。' : result.error ?? '任务重试失败。',
        success: result.success,
        status: isApprovalPendingAction(result) ? 'pending_approval' : result.success ? 'success' : 'failed',
        timestamp: new Date().toISOString(),
      }
      setActivity(next)
      publishRuntimeActivity(next)
      void reloadPoll({ force: true, silent: true })
    } finally {
      setRunningAction('')
    }
  }

  async function handleApprovalDecision(decision: 'approve' | 'reject') {
    const approvalId = String(selectedApprovalRequest.approval_id ?? '').trim()
    if (!approvalId) return
    setRunningAction(decision)
    try {
      const result = await decideApproval(approvalId, decision)
      const next: RuntimeActivity = {
        type: 'alert_action',
        title: `${selectedTask?.title ?? '任务'} / ${decision}`,
        detail: isApprovalPendingAction(result) ? '任务已暂停，等待后续技能审批。' : result.success ? '审批决定已处理。' : result.error ?? '审批处理失败。',
        success: result.success,
        status: isApprovalPendingAction(result) ? 'pending_approval' : result.success ? 'success' : 'failed',
        timestamp: new Date().toISOString(),
      }
      setActivity(next)
      publishRuntimeActivity(next)
      void reloadPoll({ force: true, silent: true })
    } finally {
      setRunningAction('')
    }
  }

  const selectedPayload = (selectedTask?.payload?.alert_data as Record<string, unknown> | undefined) ?? {}
  const selectedResult = selectedTask?.last_result_data ?? {}
  const selectedFinalFacts = (selectedResult.final_facts as Record<string, unknown> | undefined) ?? {}
  const selectedFinalJudgment = (selectedFinalFacts.judgment as Record<string, unknown> | undefined) ?? {}
  const selectedFinalConsistency = (selectedFinalFacts.consistency as Record<string, unknown> | undefined) ?? {}
  const selectedConsistencyIssues = Array.isArray(selectedFinalConsistency.issues)
    ? selectedFinalConsistency.issues.map((item) => String(item).trim()).filter(Boolean)
    : []
  const selectedWorkflowRuns = normalizeWorkflowRuns(selectedResult.workflow_runs)
  const selectedWorkflowRun = selectedWorkflowRuns[0] ?? null
  const selectedWorkflowSelection =
    (selectedResult.workflow_selection as Record<string, unknown> | undefined) ??
    (selectedTask?.payload?.workflow_selection as Record<string, unknown> | undefined) ??
    {}
  const selectedApprovalRequest = (selectedResult.approval_request as Record<string, unknown> | undefined) ?? {}
  const selectedClosureStep = (
    (selectedResult.effective_closure_step as Record<string, unknown> | undefined)
    ?? (selectedResult.closure_step as Record<string, unknown> | undefined)
  ) ?? {}
  const selectedFinalJudgmentMarkdown = String(selectedResult.final_judgment_markdown ?? '').trim()
  const selectedReason = String(selectedResult.reason ?? '').trim()
  const selectedDisposition = String(selectedFinalJudgment.disposition ?? selectedResult.disposition ?? '').trim()
  const selectedSummary = String(selectedResult.summary ?? '').trim()
  const selectedEvidence = Array.isArray(selectedResult.evidence)
    ? selectedResult.evidence.map((item) => String(item).trim()).filter(Boolean)
    : []
  const hasFinalJudgment = Boolean(
    selectedFinalJudgmentMarkdown || selectedDisposition || selectedReason || selectedSummary,
  )
  const hideTaskError = Boolean(selectedClosureStep.attempted) && Boolean(selectedClosureStep.success)
  const selectedTrace = Array.isArray(selectedResult.execution_trace) && selectedResult.execution_trace.length
    ? (selectedResult.execution_trace as ExecutionTraceItem[])
    : buildFallbackTrace(selectedTask)
  const selectedToolResults = collectToolInvocationResults(selectedResult, selectedTrace)
  const dipPreview = formatIpPreview(selectedPayload.dip)
  const workflowDecision = String(
    selectedWorkflowRun?.workflow_name ?? selectedWorkflowRun?.workflow_id ?? selectedTask?.workflow_name ?? '',
  ).trim()
  const workflowDecisionReason = String(
    selectedWorkflowRun?.summary ?? selectedWorkflowRun?.reason ?? selectedWorkflowSelection.reason ?? '',
  ).trim()

  return (
    <div className="sentinelflow-page-stack">
      <PageHeader
        title="任务中心"
        description="按状态查看任务流转、执行细节和完整处置链路。"
        icon={<ListTodo className="w-8 h-8" />}
        action={
          <button
            type="button"
            className={`flex items-center gap-2 rounded-lg px-4 py-2 transition-colors ${autoExecuteEnabled ? 'border border-red-200 bg-white text-red-600 hover:bg-red-50' : 'bg-red-600 text-white hover:bg-red-700'}`}
            onClick={() => void handleAutoExecuteToggle()}
            disabled={runningAction !== ''}
          >
            <RotateCcw className="h-4 w-4" />
            {autoExecuteEnabled ? (autoExecuteRunning ? '自动执行中' : '停止自动执行') : '开始自动执行'}
          </button>
        }
      />

      <Surface title="任务中心" subtitle={withProductName('展示任务从排队、执行到闭环的完整生命周期。当前由主 Agent 统一统筹，可按需调用子 Agent、Workflow 与技能。')}>
        <div className="grid gap-4 md:grid-cols-4">
          <div className="rounded-xl border border-gray-200 bg-white p-5">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm text-gray-500">排队中</span>
              <Clock className="h-4 w-4 text-amber-500" />
            </div>
            <div className="text-3xl font-bold text-gray-900">{tasks.filter((task) => getEffectiveTaskStatus(task) === 'queued').length}</div>
          </div>
          <div className="rounded-xl border border-gray-200 bg-white p-5">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm text-gray-500">执行中</span>
              <RotateCcw className="h-4 w-4 text-sky-500" />
            </div>
            <div className="text-3xl font-bold text-gray-900">{tasks.filter((task) => getEffectiveTaskStatus(task) === 'running').length}</div>
          </div>
          <div className="rounded-xl border border-gray-200 bg-white p-5">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm text-gray-500">已完成</span>
              <ShieldCheck className="h-4 w-4 text-emerald-500" />
            </div>
            <div className="text-3xl font-bold text-gray-900">{tasks.filter((task) => {
              const status = getEffectiveTaskStatus(task)
              return status === 'succeeded' || status === 'completed'
            }).length}</div>
          </div>
          <div className="rounded-xl border border-gray-200 bg-white p-5">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm text-gray-500">失败</span>
              <XCircle className="h-4 w-4 text-red-500" />
            </div>
            <div className="text-3xl font-bold text-gray-900">{tasks.filter((task) => isFailedBucketStatus(String(getEffectiveTaskStatus(task)))).length}</div>
          </div>
        </div>

        <div className="mb-4 mt-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex gap-1 rounded-lg bg-gray-100 p-1">
            {(['all', 'queued', 'running', 'awaiting_approval', 'succeeded', 'completed', 'pending_closure', 'failed'] as TaskFilter[]).map((item) => (
              <button key={item} type="button" className={`rounded-md px-4 py-2 text-sm transition-colors ${filter === item ? 'bg-white font-medium text-slate-800 shadow-sm' : 'text-gray-600 hover:text-gray-900'}`} onClick={() => setFilter(item)}>
                {TASK_FILTER_LABELS[item]}
              </button>
            ))}
          </div>
          <div className="sentinelflow-inline-metrics">
            <span>mode: {settings?.runtime.agent_enabled ? 'Agent' : 'Basic'}</span>
            <span>自动执行: {autoExecuteEnabled ? (autoExecuteRunning ? '自动执行中' : '已开启') : '未开启'}</span>
            <button type="button" className="sentinelflow-ghost-button" onClick={() => void reloadPoll({ force: true, silent: true })}>刷新任务视图</button>
          </div>
        </div>

        {activity ? (
          <div className="sentinelflow-activity-banner">
            <div className="sentinelflow-activity-banner-header">
              <StatusBadge tone={getRuntimeActivityStatus(activity) === 'success' ? 'success' : 'warn'}>{getRuntimeActivityBadgeLabel(activity)}</StatusBadge>
              <span>{new Date(activity.timestamp).toLocaleString()}</span>
            </div>
            <strong>{activity.title}</strong>
            <p className="sentinelflow-muted-text">{activity.detail}</p>
          </div>
        ) : null}
      </Surface>

      <Surface title="任务工作面" subtitle="左侧选择任务，右侧查看详情与完整处置全流程。">
        {loading ? <p className="sentinelflow-muted-text">正在读取任务分发结果...</p> : null}
        {error ? <div className="sentinelflow-message-block sentinelflow-message-error">{error}</div> : null}
        {poll && typeof poll.tasks_total === 'number' && poll.tasks_total > (poll.tasks?.length ?? 0) ? (
          <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
            当前接口仅返回前 {poll.tasks?.length ?? 0} 条任务，共 {poll.tasks_total} 条；筛选和统计可能只覆盖已加载任务。
          </div>
        ) : null}
        {!loading && !error ? (
          <div className="sentinelflow-grid-2 items-start">
            <div
              ref={taskListPanelRef}
              className="sentinelflow-detail-panel h-auto overflow-hidden"
              style={taskListPanelHeight ? { height: `${taskListPanelHeight}px` } : undefined}
            >
              <h3>筛选结果</h3>
              <div
                className="sentinelflow-task-list-scroll"
                style={taskListMaxHeight ? { maxHeight: `${taskListMaxHeight}px` } : undefined}
              >
                <div className="sentinelflow-task-list">
                  {filteredTasks.length ? visibleFilteredTasks.map((task) => (
                    <button key={task.task_id} type="button" className={`sentinelflow-task-tile${selectedTask?.task_id === task.task_id ? ' sentinelflow-task-tile-active' : ''}`} onClick={() => setSelectedTaskId(task.task_id)}>
                      <div className="sentinelflow-response-row">
                        <strong>{task.title}</strong>
                        <StatusBadge tone={getTone(task)}>{getTaskStatusLabel(getEffectiveTaskStatus(task))}</StatusBadge>
                      </div>
                      <span>{task.alert_time || '未提供告警时间'}</span>
                      <span>{getTaskFlowLabel(task)}</span>
                    </button>
                  )) : <p className="sentinelflow-muted-text">当前筛选条件下没有任务。</p>}
                  {visibleFilteredTasks.length < filteredTasks.length ? (
                    <button
                      type="button"
                      className="sentinelflow-ghost-button w-full"
                      onClick={() => setVisibleTaskCount((current) => current + TASK_LIST_RENDER_INCREMENT)}
                    >
                      显示更多任务（{visibleFilteredTasks.length}/{filteredTasks.length}）
                    </button>
                  ) : null}
                </div>
              </div>
            </div>

            <div ref={detailPanelRef} className="sentinelflow-detail-panel h-auto self-start">
              <h3>任务详情</h3>
              {selectedTask ? (
                <div className="sentinelflow-response-stack">
                  {detailError ? (
                    <div className="sentinelflow-message-block sentinelflow-message-error">{detailError}</div>
                  ) : null}
                  <div className="sentinelflow-response-row">
                    <StatusBadge tone={getTone(selectedTask)}>{getTaskStatusLabel(getEffectiveTaskStatus(selectedTask))}</StatusBadge>
                    <span>{selectedTask.alert_time || '未提供告警时间'}</span>
                    <span>{getTaskFlowLabel(selectedTask)}</span>
                  </div>
                  <p className="sentinelflow-muted-text">{selectedTask.description}</p>

                  <div className="sentinelflow-context-grid">
                    <div className="sentinelflow-context-card"><strong>告警名称</strong><span>{String(selectedPayload.alert_name ?? '未提供')}</span></div>
                    <div className="sentinelflow-context-card"><strong>事件号</strong><span>{selectedTask.event_ids || '未提供'}</span></div>
                    <div className="sentinelflow-context-card"><strong>源 IP</strong><span>{String(selectedPayload.sip ?? '未提供')}</span></div>
                    <div className="sentinelflow-context-card"><strong>目标 IP</strong><span title={dipPreview.fullText}>{dipPreview.text}</span></div>
                    <div className="sentinelflow-context-card"><strong>告警时间</strong><span>{String(selectedPayload.alert_time ?? selectedTask.alert_time ?? '未提供')}</span></div>
                    <div className="sentinelflow-context-card"><strong>当前研判</strong><span>{String(selectedPayload.current_judgment ?? '未提供')}</span></div>
                  </div>

                  {selectedWorkflowRun && workflowDecision ? (
                    <div className="rounded-xl border border-amber-100 bg-amber-50 p-4">
                      <div className="text-xs font-semibold uppercase tracking-wide text-amber-700">Workflow 调用</div>
                      <div className="mt-2 text-sm font-semibold text-amber-950">
                        {`主 Agent 调用了流程：${workflowDecision}`}
                      </div>
                      {workflowDecisionReason ? (
                        <div className="mt-2 text-sm text-amber-900">
                          {`Workflow 返回：${workflowDecisionReason}`}
                        </div>
                      ) : (
                        <div className="mt-2 text-sm text-amber-900">
                          该 Workflow 已作为主 Agent 的一个中间能力被调用。
                        </div>
                      )}
                    </div>
                  ) : null}

                  {hasFinalJudgment ? (
                    <div className="rounded-xl border border-blue-100 bg-blue-50 p-4">
                      <div className="flex items-center justify-between gap-3">
                        <div>
                          <div className="text-xs font-semibold uppercase tracking-wide text-blue-700">最终研判</div>
                          <p className="mt-1 text-sm text-blue-900">
                            {finalJudgmentExpanded ? '已展开完整研判内容。' : '点击展开查看完整研判思路与内容。'}
                          </p>
                        </div>
                        <button type="button" className="sentinelflow-ghost-button" onClick={() => setFinalJudgmentExpanded((current) => !current)}>
                          {finalJudgmentExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                          {finalJudgmentExpanded ? '收起最终研判' : '展开最终研判'}
                        </button>
                      </div>
                      {finalJudgmentExpanded ? (
                        selectedFinalJudgmentMarkdown ? (
                          <div className="mt-4 text-sm text-blue-900">
                            <MarkdownContent content={selectedFinalJudgmentMarkdown} />
                          </div>
                        ) : (
                          <div className="mt-4">
                            <div className="text-sm font-semibold text-blue-950">{`分类：${getDispositionLabel(selectedDisposition)}`}</div>
                            {selectedSummary ? <div className="mt-2 text-sm text-blue-900">结论：{selectedSummary}</div> : null}
                            {selectedReason ? <div className="mt-2 text-sm text-blue-900">理由：{selectedReason}</div> : null}
                            {selectedEvidence.length ? (
                              <div className="mt-3">
                                <div className="text-xs font-semibold uppercase tracking-wide text-blue-700">关键依据</div>
                                <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-blue-900">
                                  {selectedEvidence.map((item, index) => (
                                    <li key={`${selectedTask.task_id}-evidence-${index}`}>{item}</li>
                                  ))}
                                </ul>
                              </div>
                            ) : null}
                          </div>
                        )
                      ) : null}
                    </div>
                  ) : null}

                  <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">工具调用结果</div>
                        <p className="mt-1 text-sm text-gray-600">
                          {selectedToolResults.length ? `共调用 ${selectedToolResults.length} 个 Skill，展开后查看调用顺序、输入和输出。` : '暂无可展示的工具调用结果。'}
                        </p>
                      </div>
                      <button type="button" className="sentinelflow-ghost-button" onClick={() => setToolResultsExpanded((current) => !current)}>
                        {toolResultsExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                        {toolResultsExpanded ? '收起工具调用结果' : '展开工具调用结果'}
                      </button>
                    </div>
                    {toolResultsExpanded ? <div className="mt-4"><ToolInvocationResults tools={selectedToolResults} ownerId={selectedTask.task_id} /></div> : null}
                  </div>

                  {selectedConsistencyIssues.length ? (
                    <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
                      <div className="text-xs font-semibold uppercase tracking-wide text-amber-700">结果收敛提示</div>
                      <div className="mt-2 text-sm text-amber-900">检测到过程结果存在冲突，当前页面已按真实执行事实优先收敛展示。</div>
                    </div>
                  ) : null}
                  {String(selectedApprovalRequest.approval_id ?? '').trim() ? (
                    <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
                      <div className="text-xs font-semibold uppercase tracking-wide text-amber-700">待审批 Skill</div>
                      <div className="mt-2 text-sm font-semibold text-amber-950">{String(selectedApprovalRequest.skill_name ?? '').trim() || '未命名 Skill'}</div>
                      <div className="mt-2 text-sm text-amber-900">{String(selectedApprovalRequest.message ?? '该 Skill 需要审批后才能继续执行。').trim()}</div>
                      <div className="mt-2 text-xs text-amber-800">参数：{String(selectedApprovalRequest.arguments_summary ?? '无参数').trim() || '无参数'}</div>
                      <div className="mt-3 flex gap-2">
                        <button type="button" className="sentinelflow-primary-button" onClick={() => void handleApprovalDecision('approve')} disabled={runningAction !== ''}>批准并继续</button>
                        <button type="button" className="sentinelflow-ghost-button" onClick={() => void handleApprovalDecision('reject')} disabled={runningAction !== ''}>拒绝并继续</button>
                      </div>
                    </div>
                  ) : null}

                  {selectedTask.last_result_error && !hideTaskError ? <div className="sentinelflow-message-block sentinelflow-message-error">{selectedTask.last_result_error}</div> : null}

                  <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">处置全流程</div>
                        <p className="mt-1 text-sm text-gray-600">展开后可查看从接收告警、主 Agent 研判、技能调用到结单结果的完整细节。</p>
                      </div>
                      <button type="button" className="sentinelflow-ghost-button" onClick={() => setProcessExpanded((current) => !current)}>
                        {processExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                        {processExpanded ? '收起处置全流程' : '展开处置全流程'}
                      </button>
                    </div>
                    {processExpanded ? <div className="mt-4"><ProcessTrace trace={selectedTrace} traceOwnerId={selectedTask.task_id} /></div> : null}
                  </div>

                  {isReDisposableTask(selectedTask) ? (
                    <div className="flex justify-end">
                      <button type="button" className="sentinelflow-ghost-button" onClick={() => void handleRetry(selectedTask)} disabled={runningAction !== ''}>
                        {runningAction === selectedTask.task_id ? '重试中...' : getEffectiveTaskStatus(selectedTask) === 'pending_closure' ? '重新处置' : '重试任务'}
                      </button>
                    </div>
                  ) : null}
                </div>
              ) : <p className="sentinelflow-muted-text">选择一条任务后查看详情。</p>}
            </div>
          </div>
        ) : null}
      </Surface>
    </div>
  )
}
