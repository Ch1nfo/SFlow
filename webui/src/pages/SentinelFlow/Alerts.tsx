import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, ChevronRight, RefreshCw, Siren } from 'lucide-react'
import {
  decideApproval,
  fetchAlertTaskDetail,
  handleAlertAction,
  type AlertActionResponse,
  type AlertTask,
  type PollAlertsResponse,
} from '@/api/sentinelflow'
import StatusBadge from '@/components/sentinelflow/StatusBadge'
import Surface from '@/components/sentinelflow/Surface'
import PageHeader from '@/components/common/PageHeader'
import MarkdownContent from '@/components/sentinelflow/MarkdownContent'
import { withProductName } from '@/config/brand'
import { publishRuntimeActivity } from '@/utils/sentinelflowRuntimeSync'
import { getEffectiveTaskStatus } from '@/utils/sentinelflowTaskStatus'
import { useSentinelFlowLiveRefresh } from '@/hooks/useSentinelFlowLiveRefresh'
import { useSentinelFlowPollStore } from '@/hooks/useSentinelFlowPollStore'

const ALERTS_SELECTED_SOURCE_STORAGE_KEY = 'sentinelflow.alerts.selectedSourceId'
const ALERT_QUEUE_INITIAL_RENDER_COUNT = 120
const ALERT_QUEUE_RENDER_INCREMENT = 120
type AlertTimeRange = 'today' | 'week'

function readStoredSelectedSourceId(): string | null {
  try {
    return window.localStorage.getItem(ALERTS_SELECTED_SOURCE_STORAGE_KEY)
  } catch {
    return null
  }
}

function storeSelectedSourceId(sourceId: string | null) {
  try {
    if (sourceId) {
      window.localStorage.setItem(ALERTS_SELECTED_SOURCE_STORAGE_KEY, sourceId)
    } else {
      window.localStorage.removeItem(ALERTS_SELECTED_SOURCE_STORAGE_KEY)
    }
  } catch {
    // Ignore storage failures; the selector still works for the current session.
  }
}

function getDispositionLabel(value: string) {
  if (value === 'true_attack') return '真实攻击'
  if (value === 'business_trigger') return '业务触发'
  if (value === 'false_positive') return '误报'
  return value || '未明确'
}

function buildAlertsSummary(tasks: AlertTask[]) {
  const judgment = {
    business_trigger: 0,
    false_positive: 0,
    true_attack: 0,
    unknown: 0,
  }
  const bannedIps = new Set<string>()
  let manualCompleted = 0
  for (const task of tasks) {
    const result = (task.last_result_data ?? {}) as Record<string, unknown>
    const finalFacts = (result.final_facts as Record<string, unknown> | undefined) ?? {}
    const finalJudgment = (finalFacts.judgment as Record<string, unknown> | undefined) ?? {}
    const disposition = String(finalJudgment.disposition ?? result.disposition ?? 'unknown').trim()
    if (disposition === 'business_trigger' || disposition === 'false_positive' || disposition === 'true_attack') {
      judgment[disposition] += 1
    } else {
      judgment.unknown += 1
    }
    if (getEffectiveTaskStatus(task) === 'completed') {
      manualCompleted += 1
    }
    const disposal = (finalFacts.disposal as Record<string, unknown> | undefined) ?? {}
    const actions = Array.isArray(disposal.actions) ? disposal.actions : []
    for (const action of actions) {
      if (!action || typeof action !== 'object') continue
      const record = action as Record<string, unknown>
      if (String(record.kind ?? '').trim() === 'ban_ip' && record.success === true) {
        const target = String(record.target ?? '').trim()
        if (target) bannedIps.add(target)
      }
    }
  }
  return {
    judgment,
    operations: {
      closed_success: 0,
      disposed_success: 0,
      manual_completed: manualCompleted,
      banned_ip_count: bannedIps.size,
      banned_ips: Array.from(bannedIps).sort(),
    },
  }
}

function isApprovalPendingAction(result: AlertActionResponse): boolean {
  const data = result.data ?? {}
  const approvalRequest = data.approval_request
  return (
    result.task?.status === 'awaiting_approval' ||
    data.approval_pending === true ||
    (typeof approvalRequest === 'object' && approvalRequest !== null && String((approvalRequest as Record<string, unknown>).approval_id ?? '').trim().length > 0)
  )
}

function getTaskTone(task: AlertTask): 'neutral' | 'success' | 'warn' | 'danger' | 'info' {
  const status = getEffectiveTaskStatus(task)
  if (status === 'running') return 'info'
  if (status === 'queued') return 'warn'
  if (status === 'pending_closure') return 'warn'
  if (status === 'pending_manual_closure') return 'warn'
  if (status === 'awaiting_approval') return 'warn'
  if (status === 'failed') return 'danger'
  if (status === 'succeeded' || status === 'completed') return 'success'
  return 'neutral'
}

function splitAlertIps(value: unknown): string[] {
  const text = String(value ?? '').trim()
  if (!text) return []
  return text
    .split(/[,\n，;；]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function formatIpPreview(value: unknown): { text: string; fullText: string } {
  const ips = splitAlertIps(value)
  if (!ips.length) {
    const fallback = String(value ?? '').trim()
    return { text: fallback || '未提供', fullText: fallback || '未提供' }
  }
  if (ips.length <= 3) {
    const joined = ips.join(',')
    return { text: joined, fullText: joined }
  }
  return {
    text: `${ips.slice(0, 3).join(',')}...共${ips.length}个`,
    fullText: ips.join(','),
  }
}

function formatAlertTitle(value: unknown, limit = 30): { text: string; fullText: string } {
  const fullText = String(value ?? '').trim() || '未命名告警'
  if (fullText.length <= limit) {
    return { text: fullText, fullText }
  }
  return {
    text: `${fullText.slice(0, limit)}...`,
    fullText,
  }
}

const PAYLOAD_COLLAPSED_MAX_LINES = 10

/** 判断是否需要收起：按真实换行或超长单行，不再做 120 字硬断行。 */
function payloadNeedsCollapse(text: string, maxLines = PAYLOAD_COLLAPSED_MAX_LINES): boolean {
  const trimmed = text.trim()
  if (!trimmed) return false
  if (trimmed.split(/\r?\n/).length > maxLines) return true
  // 压缩 JSON 等单行超长内容
  return trimmed.length > maxLines * 100
}

function getTaskStatusClass(task: AlertTask): string {
  const status = getEffectiveTaskStatus(task)
  if (status === 'running') return 'running'
  if (status === 'queued') return 'queued'
  if (status === 'pending_closure') return 'queued'
  if (status === 'pending_manual_closure') return 'queued'
  if (status === 'awaiting_approval') return 'queued'
  if (status === 'failed') return 'danger'
  if (status === 'succeeded' || status === 'completed') return 'success'
  return 'neutral'
}

function getTaskStatusLabel(task: AlertTask): string {
  const status = getEffectiveTaskStatus(task)
  if (status === 'queued') return '排队中'
  if (status === 'running') return '执行中'
  if (status === 'pending_closure') return '未执行'
  if (status === 'pending_manual_closure') return '自动完成待结单'
  if (status === 'awaiting_approval') return '待审批'
  if (status === 'completed') return '已被人工处置'
  if (status === 'succeeded') return '已完成'
  if (status === 'failed') return '失败'
  return status
}

function isReDisposableStatus(task: AlertTask): boolean {
  const status = getEffectiveTaskStatus(task)
  return status === 'failed' || status === 'pending_closure'
}

function isPrimaryDisposeDisabled(task: AlertTask): boolean {
  const status = getEffectiveTaskStatus(task)
  return status === 'running' || status === 'succeeded' || status === 'completed' || status === 'pending_manual_closure' || status === 'awaiting_approval'
}

function toSortableTime(value: string | undefined): number {
  const text = String(value ?? '').trim()
  if (!text) return 0
  const normalized = text.includes('T') ? text : text.replace(' ', 'T')
  const timestamp = Date.parse(normalized)
  return Number.isNaN(timestamp) ? 0 : timestamp
}

function formatAlertTime(value: string | undefined): string {
  const text = String(value ?? '').trim()
  return text || '未提供'
}

function parseAlertDate(value: string | undefined): Date | null {
  const text = String(value ?? '').trim()
  if (!text) return null
  const normalized = text.includes('T') ? text : text.replace(' ', 'T')
  const parsed = new Date(normalized)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

function getStartOfDay(value: Date): Date {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate())
}

function getStartOfWeek(value: Date): Date {
  const startOfDay = getStartOfDay(value)
  const day = startOfDay.getDay()
  const diff = day === 0 ? -6 : 1 - day
  startOfDay.setDate(startOfDay.getDate() + diff)
  return startOfDay
}

function matchesAlertTimeRange(task: AlertTask, range: AlertTimeRange, now: Date): boolean {
  const alertDate = parseAlertDate(task.alert_time)
  if (!alertDate) return false
  if (range === 'today') {
    return alertDate >= getStartOfDay(now)
  }
  return alertDate >= getStartOfWeek(now)
}

function getSelectedAlertPayload(task: AlertTask | null): Record<string, unknown> {
  return (task?.payload?.alert_data as Record<string, unknown> | undefined) ?? {}
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

export default function SentinelFlowAlertsPage() {
  const [data, setData] = useState<PollAlertsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  const [selectedSourceId, setSelectedSourceId] = useState<string | null>(() => readStoredSelectedSourceId())
  const [timeRange, setTimeRange] = useState<AlertTimeRange>('today')
  const [payloadExpanded, setPayloadExpanded] = useState(false)
  const [finalJudgmentExpanded, setFinalJudgmentExpanded] = useState(false)
  const [actionState, setActionState] = useState<{ action: string; running: boolean }>({ action: '', running: false })
  const [actionNotice, setActionNotice] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)
  const [selectedTaskDetail, setSelectedTaskDetail] = useState<AlertTask | null>(null)
  const [visibleTaskCount, setVisibleTaskCount] = useState(ALERT_QUEUE_INITIAL_RENDER_COUNT)
  const queuePanelRef = useRef<HTMLDivElement | null>(null)
  const detailPanelRef = useRef<HTMLDivElement | null>(null)
  const detailRequestSeq = useRef(0)
  const [queuePanelHeight, setQueuePanelHeight] = useState<number | null>(null)
  const [queueListMaxHeight, setQueueListMaxHeight] = useState<number | null>(null)
  const autoExecuteEnabled = Boolean(data?.auto_execute_enabled)
  const autoExecuteRunning = Boolean(data?.auto_execute_running)
  const liveRefreshing = autoExecuteEnabled || actionState.running || (data?.tasks ?? []).some((task) => task.status === 'running')
  const { reload: reloadPollStore } = useSentinelFlowPollStore(selectedSourceId, { autoLoad: false })
  const { data: allSourcesPoll, reload: reloadAllSourcesPoll } = useSentinelFlowPollStore('all')

  const loadTasks = useCallback(async (options?: { force?: boolean; silent?: boolean }) => {
    const silent = options?.silent ?? false
    if (!silent) {
      setLoading(true)
      setError(null)
    }
    try {
      const result = await reloadPollStore({ force: options?.force, silent })
      if (!result) {
        if (!silent) setLoading(false)
        return
      }
      setData(result)
      setError(null)
      void reloadAllSourcesPoll({ silent: true })
      const knownSourceIds = (result.alert_sources ?? []).map((source) => source.id)
      const selectedSourceExists = selectedSourceId && (knownSourceIds.length === 0 || knownSourceIds.includes(selectedSourceId))
      const nextSelectedSourceId = selectedSourceExists ? selectedSourceId : result.source_id
      if (nextSelectedSourceId && nextSelectedSourceId !== selectedSourceId) {
        setSelectedSourceId(nextSelectedSourceId)
        storeSelectedSourceId(nextSelectedSourceId)
      }
      if (!silent) {
        setLoading(false)
      }
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Unknown error')
      if (!silent) {
        setLoading(false)
      }
    }
  }, [reloadAllSourcesPoll, reloadPollStore, selectedSourceId])

  useEffect(() => {
    void loadTasks()
  }, [loadTasks])

  useSentinelFlowLiveRefresh(
    () => loadTasks({ silent: true }),
    { intervalMs: liveRefreshing ? 2000 : 5000 },
  )

  const allTasks = [...(data?.tasks ?? [])].sort((left, right) => toSortableTime(right.alert_time) - toSortableTime(left.alert_time))
  const now = new Date()
  const tasks = allTasks.filter((task) => matchesAlertTimeRange(task, timeRange, now))
  const visibleTasks = tasks.slice(0, visibleTaskCount)
  const alertSources = data?.alert_sources ?? []
  const selectedSource = alertSources.find((source) => source.id === (selectedSourceId ?? data?.source_id)) ?? alertSources[0] ?? null

  useEffect(() => {
    setSelectedTaskId((current) => {
      if (!tasks.length) return null
      if (current && tasks.some((task) => task.task_id === current)) return current
      return tasks[0]?.task_id ?? null
    })
  }, [tasks])

  useEffect(() => {
    setPayloadExpanded(false)
    setFinalJudgmentExpanded(false)
    setActionNotice(null)
  }, [selectedTaskId])

  useEffect(() => {
    setVisibleTaskCount(ALERT_QUEUE_INITIAL_RENDER_COUNT)
  }, [timeRange, selectedSourceId])

  const selectedTaskSummary = tasks.find((task) => task.task_id === selectedTaskId) ?? tasks[0] ?? null
  const selectedTask = selectedTaskDetail?.task_id === selectedTaskSummary?.task_id ? selectedTaskDetail : selectedTaskSummary
  const summary = useMemo(() => buildAlertsSummary(tasks), [tasks])
  const weekSummary = useMemo(() => {
    const allSourceTasks = (allSourcesPoll?.tasks ?? []).filter((task) => matchesAlertTimeRange(task, 'week', now))
    return buildAlertsSummary(allSourceTasks)
  }, [allSourcesPoll?.tasks, now])

  useEffect(() => {
    const taskId = selectedTaskSummary?.task_id ?? ''
    const requestSeq = detailRequestSeq.current + 1
    detailRequestSeq.current = requestSeq
    setSelectedTaskDetail(null)
    if (!taskId) return
    void fetchAlertTaskDetail(taskId)
      .then((response) => {
        if (detailRequestSeq.current !== requestSeq) return
        setSelectedTaskDetail(response.task)
      })
      .catch(() => {
        if (detailRequestSeq.current !== requestSeq) return
        setSelectedTaskDetail(null)
      })
  }, [selectedTaskSummary?.task_id])
  const selectedPayload = getSelectedAlertPayload(selectedTask)
  const selectedPayloadText = String(selectedPayload.payload ?? '').trim()
  const shouldCollapsePayload = payloadNeedsCollapse(selectedPayloadText)
  const workflowSelection = (selectedTask?.payload?.workflow_selection as Record<string, unknown> | undefined) ?? {}
  const selectedResult = (selectedTask?.last_result_data ?? {}) as Record<string, unknown>
  const selectedFinalFacts = (selectedResult.final_facts as Record<string, unknown> | undefined) ?? {}
  const selectedFinalJudgment = (selectedFinalFacts.judgment as Record<string, unknown> | undefined) ?? {}
  const selectedFinalConsistency = (selectedFinalFacts.consistency as Record<string, unknown> | undefined) ?? {}
  const selectedTaskOutcome = (selectedFinalFacts.task_outcome as Record<string, unknown> | undefined) ?? {}
  const selectedFinalDisposal = (selectedFinalFacts.disposal as Record<string, unknown> | undefined) ?? {}
  const selectedConsistencyIssues = Array.isArray(selectedFinalConsistency.issues)
    ? selectedFinalConsistency.issues.map((item) => String(item).trim()).filter(Boolean)
    : []
  const selectedDisposalActions = Array.isArray(selectedFinalDisposal.actions)
    ? selectedFinalDisposal.actions.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
    : []
  const selectedTerminalActions = selectedDisposalActions.filter((item) => {
    const effect = String(item.completion_effect ?? '').trim()
    return Boolean(item.success) && Boolean(item.completion_policy_enabled)
  })
  const selectedTaskOutcomeStatus = String(selectedTaskOutcome.status ?? '').trim()
  const selectedWorkflowRuns = normalizeWorkflowRuns(selectedResult.workflow_runs)
  const selectedApprovalRequest = (selectedResult.approval_request as Record<string, unknown> | undefined) ?? {}
  const selectedApprovalId = String(selectedApprovalRequest.approval_id ?? '').trim()
  const selectedApprovalStatus = String(selectedApprovalRequest.status ?? '').trim()
  const selectedApprovalCanResolve = Boolean(selectedTask)
    && getEffectiveTaskStatus(selectedTask) === 'awaiting_approval'
    && Boolean(selectedApprovalId)
    && (!selectedApprovalStatus || selectedApprovalStatus === 'pending')
  const selectedWorkflowRun = selectedWorkflowRuns[0] ?? null
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
  const dipPreview = formatIpPreview(selectedPayload.dip)
  const workflowDecision = String(
    selectedWorkflowRun?.workflow_name ?? selectedWorkflowRun?.workflow_id ?? selectedTask?.workflow_name ?? '',
  ).trim()
  const workflowDecisionReason = String(
    selectedWorkflowRun?.summary ?? selectedWorkflowRun?.reason ?? workflowSelection.reason ?? '',
  ).trim()

  useEffect(() => {
    const detailNode = detailPanelRef.current
    const queueNode = queuePanelRef.current
    if (!detailNode || !queueNode || typeof ResizeObserver === 'undefined') return

    const syncHeight = () => {
      try {
        const detailHeight = Math.max(0, Math.round(detailNode.getBoundingClientRect().height))
        const scrollNode = queueNode.querySelector('.sentinelflow-alert-queue-scroll') as HTMLDivElement | null
        const scrollHeight = scrollNode?.offsetHeight ?? 0
        const chromeHeight = Math.max(0, Math.round(queueNode.offsetHeight - scrollHeight))
        const nextHeight = Math.max(0, detailHeight - chromeHeight)
        setQueuePanelHeight(detailHeight || null)
        setQueueListMaxHeight(nextHeight || null)
      } catch {
        setQueuePanelHeight(null)
        setQueueListMaxHeight(null)
      }
    }

    try {
      syncHeight()
      const observer = new ResizeObserver(() => syncHeight())
      observer.observe(detailNode)
      return () => observer.disconnect()
    } catch {
      setQueuePanelHeight(null)
      setQueueListMaxHeight(null)
      return
    }
  }, [selectedTaskId, payloadExpanded, finalJudgmentExpanded, selectedTask?.task_id, selectedTask?.status, selectedDisposition, selectedFinalJudgmentMarkdown, selectedReason, selectedSummary, selectedEvidence.length, selectedConsistencyIssues.length])

  async function runAction(action: string) {
    setActionState({ action, running: true })
    setActionNotice(null)
    void loadTasks({ silent: true })
    try {
      const result = action === 'refresh_poll' || action === 'auto_run_pending' || action === 'auto_execute_start' || action === 'auto_execute_stop'
        ? await handleAlertAction(action, undefined, undefined, selectedSourceId ?? data?.source_id)
        : selectedTask
          ? await handleAlertAction(action, selectedTask, undefined, selectedSourceId ?? data?.source_id)
          : null
      if (!result) {
        setActionNotice({ tone: 'error', text: '当前没有可执行的告警任务。' })
        return
      }
      const isApprovalPending = isApprovalPendingAction(result)
      if (isApprovalPending) {
        setActionNotice({ tone: 'info', text: '任务已暂停，等待技能审批。' })
      } else if (!result.success) {
        setActionNotice({ tone: 'error', text: result.error ?? '动作执行失败。' })
      }
      publishRuntimeActivity({
        type: 'alert_action',
        title: selectedTask ? `${selectedTask.title} / ${action}` : action,
        detail: isApprovalPending ? '任务已暂停，等待技能审批。' : result.success ? '动作执行完成。' : result.error ?? '动作执行失败。',
        success: result.success,
        status: isApprovalPending ? 'pending_approval' : result.success ? 'success' : 'failed',
        timestamp: new Date().toISOString(),
      })
      await loadTasks({ force: true })
    } catch (runError) {
      setActionNotice({ tone: 'error', text: runError instanceof Error ? runError.message : 'Unknown error' })
      publishRuntimeActivity({
        type: 'alert_action',
        title: selectedTask ? `${selectedTask.title} / ${action}` : action,
        detail: runError instanceof Error ? runError.message : 'Unknown error',
        success: false,
        status: 'failed',
        timestamp: new Date().toISOString(),
      })
    } finally {
      setActionState({ action: '', running: false })
    }
  }

  async function resolveApproval(decision: 'approve' | 'reject') {
    const approvalId = selectedApprovalId
    if (!approvalId) return
    setActionState({ action: decision, running: true })
    setActionNotice(null)
    try {
      const result = await decideApproval(approvalId, decision)
      if (!result.success) {
        setActionNotice({ tone: 'error', text: result.error ?? '审批处理失败。' })
      }
      await loadTasks({ force: true })
    } catch (approvalError) {
      setActionNotice({ tone: 'error', text: approvalError instanceof Error ? approvalError.message : '审批处理失败。' })
    } finally {
      setActionState({ action: '', running: false })
    }
  }

  return (
    <div className="sentinelflow-page-stack">
      <PageHeader
        title="告警工作台"
        description="查看轮询结果并执行单条告警的研判、处置和闭环动作。"
        icon={<Siren className="w-8 h-8" />}
        action={
          <button type="button" className="flex items-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-white transition-colors hover:bg-red-700" onClick={() => void runAction('refresh_poll')} disabled={actionState.running}>
            <RefreshCw className="h-4 w-4" />
            重新轮询
          </button>
        }
      />

      <Surface title="告警工作台" subtitle={withProductName('这里直接承载 SentinelFlow 的轮询结果、单条动作和人工复核操作。')}>
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-gray-200 bg-white p-4">
          <div>
            <div className="text-sm font-semibold text-gray-900">当前告警源</div>
            <div className="mt-1 text-sm text-gray-600">{selectedSource?.name ?? '默认告警源'}</div>
          </div>
          <select
            className="sentinelflow-settings-input max-w-xs"
            value={selectedSourceId ?? data?.source_id ?? ''}
            onChange={(event) => {
              const nextSourceId = event.target.value
              setSelectedSourceId(nextSourceId)
              storeSelectedSourceId(nextSourceId)
              setSelectedTaskId(null)
            }}
          >
            {alertSources.length ? alertSources.map((source) => (
              <option key={source.id} value={source.id}>{source.name}</option>
            )) : (
              <option value={data?.source_id ?? 'default'}>默认告警源</option>
            )}
          </select>
        </div>
        <div className="mb-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-5">
            <div className="mb-2 text-sm font-semibold text-emerald-900">业务触发</div>
            <div className="text-3xl font-bold text-emerald-950">{summary?.judgment.business_trigger ?? 0}</div>
            <div className="mt-2 text-xs text-emerald-800">已判定为业务原因触发的告警数量</div>
          </div>
          <div className="rounded-xl border border-slate-200 bg-slate-50 p-5">
            <div className="mb-2 text-sm font-semibold text-slate-900">误报</div>
            <div className="text-3xl font-bold text-slate-950">{summary?.judgment.false_positive ?? 0}</div>
            <div className="mt-2 text-xs text-slate-700">已归类为误报的告警数量</div>
          </div>
          <div className="rounded-xl border border-red-200 bg-red-50 p-5">
            <div className="mb-2 text-sm font-semibold text-red-900">真实攻击</div>
            <div className="text-3xl font-bold text-red-950">{summary?.judgment.true_attack ?? 0}</div>
            <div className="mt-2 text-xs text-red-800">已识别为真实攻击的告警数量</div>
          </div>
          <div className="rounded-xl border border-amber-200 bg-amber-50 p-5">
            <div className="mb-2 text-sm font-semibold text-amber-900">封禁 IP</div>
            <div className="text-3xl font-bold text-amber-950">{summary?.operations.banned_ip_count ?? 0}</div>
            <div className="mt-2 text-xs text-amber-800">已执行封禁动作并记录的 IP 数量</div>
          </div>
        </div>

        <div className="mb-4 grid gap-4 xl:grid-cols-2 xl:items-start">
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              className={timeRange === 'today' ? 'sentinelflow-primary-button' : 'sentinelflow-ghost-button'}
              onClick={() => {
                setTimeRange('today')
                setSelectedTaskId(null)
              }}
            >
              今日告警
            </button>
            <button
              type="button"
              className={timeRange === 'week' ? 'sentinelflow-primary-button' : 'sentinelflow-ghost-button'}
              onClick={() => {
                setTimeRange('week')
                setSelectedTaskId(null)
              }}
            >
              本周告警
            </button>
          </div>
          <div className="sentinelflow-action-bar xl:justify-start">
            <button type="button" className="sentinelflow-ghost-button" onClick={() => void runAction('refresh_poll')} disabled={actionState.running}>重新轮询</button>
            <button
              type="button"
              className={autoExecuteEnabled ? 'sentinelflow-ghost-button' : 'sentinelflow-primary-button'}
              onClick={() => void runAction(autoExecuteEnabled ? 'auto_execute_stop' : 'auto_execute_start')}
              disabled={actionState.running}
            >
              {autoExecuteEnabled ? (autoExecuteRunning ? '自动执行中' : '停止自动执行') : '开始自动执行'}
            </button>
          </div>
        </div>
        {actionNotice ? (
          <div className={`mb-4 rounded-xl border p-4 text-sm ${actionNotice.tone === 'error' ? 'border-red-200 bg-red-50 text-red-800' : 'border-amber-200 bg-amber-50 text-amber-900'}`}>
            {actionNotice.text}
          </div>
        ) : null}

        <div className="sentinelflow-grid-2 items-start">
          <div
            ref={queuePanelRef}
            className="sentinelflow-detail-panel h-auto overflow-hidden min-h-[220px]"
            style={queuePanelHeight ? { height: `${queuePanelHeight}px` } : undefined}
          >
            <h3>告警队列</h3>
            <div
              className="sentinelflow-alert-queue-scroll flex-1 min-h-0"
              style={queueListMaxHeight ? { maxHeight: `${queueListMaxHeight}px` } : undefined}
            >
              <table className="sentinelflow-data-table">
                <thead>
                  <tr><th>告警</th><th>告警时间</th><th>状态</th><th>执行方式</th></tr>
                </thead>
                <tbody>
                  {loading ? <tr><td colSpan={4}>正在加载...</td></tr> : null}
                  {error ? <tr><td colSpan={4}>加载失败：{error}</td></tr> : null}
                  {!loading && !error && tasks.length === 0 ? <tr><td colSpan={4}>{timeRange === 'today' ? '今日暂无告警任务。' : '本周暂无告警任务。'}</td></tr> : null}
                  {!loading && !error ? visibleTasks.map((task) => (
                    <tr
                      key={task.task_id}
                      className={[
                        selectedTask?.task_id === task.task_id ? 'sentinelflow-table-row-active' : '',
                        `sentinelflow-table-row-${getTaskStatusClass(task)}`,
                      ].filter(Boolean).join(' ')}
                      onClick={() => setSelectedTaskId(task.task_id)}
                    >
                      <td title={formatAlertTitle(task.title).fullText}>{formatAlertTitle(task.title).text}</td>
                      <td>{formatAlertTime(task.alert_time)}</td>
                      <td><StatusBadge tone={getTaskTone(task)}>{getTaskStatusLabel(task)}</StatusBadge></td>
                      <td>{getTaskFlowLabel(task)}</td>
                    </tr>
                  )) : null}
                  {!loading && !error && visibleTasks.length < tasks.length ? (
                    <tr>
                      <td colSpan={4}>
                        <button
                          type="button"
                          className="sentinelflow-ghost-button w-full"
                          onClick={() => setVisibleTaskCount((current) => current + ALERT_QUEUE_RENDER_INCREMENT)}
                        >
                          显示更多告警（{visibleTasks.length}/{tasks.length}）
                        </button>
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </div>

          <div ref={detailPanelRef} className={`sentinelflow-detail-panel h-auto self-start min-h-[220px] sentinelflow-detail-panel-${selectedTask ? getTaskStatusClass(selectedTask) : 'neutral'}`}>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <h3 className="mb-0">当前选中告警</h3>
              <button
                type="button"
                className="sentinelflow-primary-button"
                onClick={() => setFinalJudgmentExpanded((current) => !current)}
                disabled={!hasFinalJudgment}
                title={hasFinalJudgment ? undefined : '当前告警暂无最终研判信息'}
              >
                {finalJudgmentExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                {finalJudgmentExpanded ? '关闭研判信息' : '展示最终研判'}
              </button>
            </div>
            <div className="flex-1 min-h-0">
            {selectedTask ? (
              <div className="sentinelflow-response-stack">
                <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                  <div className="flex flex-wrap items-center gap-2 text-sm text-slate-700">
                    <StatusBadge tone={getTaskTone(selectedTask)}>{getTaskStatusLabel(selectedTask)}</StatusBadge>
                    <span>{formatAlertTime(selectedTask.alert_time)}</span>
                    <span className="text-slate-300">/</span>
                    <span>{getTaskFlowLabel(selectedTask)}</span>
                  </div>
                </div>
                {finalJudgmentExpanded ? (
                  <div className="rounded-xl border border-blue-100 bg-blue-50 p-4">
                    <div className="text-xs font-semibold uppercase tracking-wide text-blue-700">最终研判</div>
                    {selectedFinalJudgmentMarkdown ? (
                      <div className="mt-4 text-sm text-blue-900">
                        <MarkdownContent content={selectedFinalJudgmentMarkdown} />
                      </div>
                    ) : (
                      <div className="mt-4">
                        <div className="text-sm font-semibold text-blue-950">
                          {`分类：${getDispositionLabel(selectedDisposition)}`}
                        </div>
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
                    )}
                  </div>
                ) : (
                  <>
                    <div className="sentinelflow-context-grid">
                      <div className="sentinelflow-context-card"><strong>告警名称</strong><span>{String(selectedPayload.alert_name ?? selectedTask.title ?? '未提供')}</span></div>
                      <div className="sentinelflow-context-card"><strong>告警时间</strong><span>{formatAlertTime(selectedTask.alert_time)}</span></div>
                      <div className="sentinelflow-context-card"><strong>事件号</strong><span>{selectedTask.event_ids || '未提供'}</span></div>
                      <div className="sentinelflow-context-card"><strong>来源</strong><span>{String(selectedPayload.alert_source ?? '未提供')}</span></div>
                      <div className="sentinelflow-context-card"><strong>源 IP</strong><span>{String(selectedPayload.sip ?? '未提供')}</span></div>
                      <div className="sentinelflow-context-card"><strong>目的 IP</strong><span title={dipPreview.fullText}>{dipPreview.text}</span></div>
                      <div className="sentinelflow-context-card"><strong>当前研判</strong><span>{String(selectedPayload.current_judgment ?? '未提供')}</span></div>
                      <div className="sentinelflow-context-card"><strong>历史研判</strong><span>{String(selectedPayload.history_judgment ?? '未提供')}</span></div>
                    </div>
                    {selectedPayloadText ? (
                      <div className="rounded-xl border border-slate-200 bg-white p-4">
                        <div className="mb-2 flex items-center justify-between gap-3">
                          <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">告警 Payload</div>
                          {shouldCollapsePayload ? (
                            <button type="button" className="sentinelflow-ghost-button" onClick={() => setPayloadExpanded((current) => !current)}>
                              {payloadExpanded ? '收起' : '展开'}
                            </button>
                          ) : null}
                        </div>
                        <pre
                          className={`overflow-x-auto whitespace-pre-wrap text-xs leading-6 text-slate-700 ${
                            shouldCollapsePayload && !payloadExpanded ? 'max-h-[15rem] overflow-y-hidden' : ''
                          }`}
                        >
                          {selectedPayloadText}
                        </pre>
                      </div>
                    ) : null}
                    <div className="sentinelflow-action-bar">
                      <button type="button" className="sentinelflow-primary-button" onClick={() => void runAction('triage_dispose')} disabled={actionState.running || isPrimaryDisposeDisabled(selectedTask)}>处置当前告警</button>
                      {isReDisposableStatus(selectedTask) ? (
                        <button type="button" className="sentinelflow-ghost-button" onClick={() => void runAction('retry_task')} disabled={actionState.running}>
                          {getEffectiveTaskStatus(selectedTask) === 'pending_closure' ? '重新处置' : '重试任务'}
                        </button>
                      ) : null}
                    </div>
                    {selectedApprovalCanResolve ? (
                      <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
                        <div className="text-xs font-semibold uppercase tracking-wide text-amber-700">待审批 Skill</div>
                        <div className="mt-2 text-sm font-semibold text-amber-950">{String(selectedApprovalRequest.skill_name ?? '').trim() || '未命名 Skill'}</div>
                        <div className="mt-2 text-sm text-amber-900">{String(selectedApprovalRequest.message ?? '该 Skill 需要审批后才能继续执行。').trim()}</div>
                        <div className="mt-2 text-xs text-amber-800">参数：{String(selectedApprovalRequest.arguments_summary ?? '无参数').trim() || '无参数'}</div>
                        <div className="mt-3 flex gap-2">
                          <button type="button" className="sentinelflow-primary-button" onClick={() => void resolveApproval('approve')} disabled={actionState.running}>批准并继续</button>
                          <button type="button" className="sentinelflow-ghost-button" onClick={() => void resolveApproval('reject')} disabled={actionState.running}>拒绝并继续</button>
                        </div>
                      </div>
                    ) : null}
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
                  </>
                )}
                {!finalJudgmentExpanded && selectedTaskOutcomeStatus === 'pending_manual_closure' ? (
                  <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
                    <div className="text-xs font-semibold uppercase tracking-wide text-amber-700">自动处置完成</div>
                    <div className="mt-2 text-sm font-semibold text-amber-950">已完成自动封禁/通知，待人工在 SOC 结单。</div>
                    {selectedTerminalActions.length ? (
                      <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-amber-900">
                        {selectedTerminalActions.map((item, index) => {
                          const effect = String(item.completion_effect ?? '').trim()
                          const kind = String(item.kind ?? item.skill_name ?? '').trim()
                          const target = String(item.target ?? '').trim()
                          return (
                            <li key={`${selectedTask.task_id}-terminal-action-${index}`}>
                              {effect === 'notification' ? '终态通知' : effect === 'containment' ? '遏制动作' : '终态动作'}：{kind || '未命名 Skill'}{target ? ` / ${target}` : ''}
                            </li>
                          )
                        })}
                      </ul>
                    ) : null}
                  </div>
                ) : null}
                {!finalJudgmentExpanded && selectedConsistencyIssues.length ? (
                  <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
                    <div className="text-xs font-semibold uppercase tracking-wide text-amber-700">结果收敛提示</div>
                    <div className="mt-2 text-sm text-amber-900">检测到过程结果存在冲突，当前页面已按真实执行事实优先收敛展示。</div>
                  </div>
                ) : null}
                {!finalJudgmentExpanded && selectedTask.last_result_error && !hideTaskError ? <div className="sentinelflow-message-block sentinelflow-message-error">{selectedTask.last_result_error}</div> : null}
              </div>
            ) : (
              <p className="sentinelflow-muted-text">选择一条告警后查看详情。</p>
            )}
            </div>
          </div>
        </div>
      </Surface>

      <Surface
        title="研判与封禁摘要"
        subtitle="统计本周全部告警源的数据，不受上方「当前告警源」与「今日/本周」筛选影响。"
      >
        <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
          <div className="rounded-xl border border-gray-200 bg-white p-5">
            <div className="mb-3 text-sm font-semibold text-gray-900">研判结果概览（本周 · 全部告警源）</div>
            <div className="grid gap-3 md:grid-cols-2">
              <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4">
                <div className="text-xs text-emerald-800">业务触发</div>
                <div className="mt-1 text-2xl font-bold text-emerald-950">{weekSummary.judgment.business_trigger}</div>
              </div>
              <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                <div className="text-xs text-slate-700">误报</div>
                <div className="mt-1 text-2xl font-bold text-slate-950">{weekSummary.judgment.false_positive}</div>
              </div>
              <div className="rounded-lg border border-red-200 bg-red-50 p-4">
                <div className="text-xs text-red-800">真实攻击</div>
                <div className="mt-1 text-2xl font-bold text-red-950">{weekSummary.judgment.true_attack}</div>
              </div>
              <div className="rounded-lg border border-gray-200 bg-gray-50 p-4">
                <div className="text-xs text-gray-700">人工处置</div>
                <div className="mt-1 text-2xl font-bold text-gray-950">{weekSummary.operations.manual_completed}</div>
              </div>
            </div>
          </div>

          <div className="rounded-xl border border-gray-200 bg-white p-5">
            <div className="mb-3 text-sm font-semibold text-gray-900">封禁与处置（本周 · 全部告警源）</div>
            <div className="grid gap-3">
              <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
                <div className="text-xs text-amber-800">封禁 IP 数</div>
                <div className="mt-1 text-2xl font-bold text-amber-950">{weekSummary.operations.banned_ip_count}</div>
              </div>
            </div>
            <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-4">
              <div className="text-xs text-slate-700">已封禁 IP</div>
              <div className="sentinelflow-quick-actions">
                {weekSummary.operations.banned_ips.length > 0 ? (
                  weekSummary.operations.banned_ips.map((ip) => (
                    <span key={ip} className="sentinelflow-chip-button sentinelflow-chip-button-active">{ip}</span>
                  ))
                ) : (
                  <span className="sentinelflow-muted-text">本周还没有封禁记录。</span>
                )}
              </div>
            </div>
          </div>
        </div>
      </Surface>
    </div>
  )
}
