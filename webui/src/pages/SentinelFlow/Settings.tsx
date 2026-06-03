import { useCallback, useEffect, useMemo, useState, useRef, type ChangeEvent } from 'react'
import { Bug, RotateCcw, Save, Settings as SettingsIcon, X } from 'lucide-react'
import {
  fetchHealth,
  fetchRunLogAlerts,
  fetchRunLogDates,
  fetchRunLogDetail,
  fetchRuntimeSettings,
  fetchSkills,
  generateAlertParser,
  resetRuntimeSettings,
  saveRunLogSettings,
  saveRuntimeSettings,
  testAlertParser,
  testAlertSourceFetch,
  type RunLogAlertSummary,
  type RunLogDateSummary,
  type RunLogDetail,
  type RunLogEvent,
  type RuntimeSettingsResponse,
  type SkillSummary,
} from '@/api/sentinelflow'
import JsonPreview from '@/components/sentinelflow/JsonPreview'
import Surface, { SurfacePreviewGrid } from '@/components/sentinelflow/Surface'
import PageHeader from '@/components/common/PageHeader'
import { brand, withProductName } from '@/config/brand'
import { useSentinelFlowAsyncData } from '@/hooks/useSentinelFlowAsyncData'
import { readSessionValue, writeSessionValue } from '@/utils/sentinelflowLocalState'

const SETTINGS_DRAFT_KEY = 'sentinelflow:settings:draft'
const DEBUG_LOG_UNLOCK_CLICKS = 5
const RUN_LOG_INITIAL_RENDER_COUNT = 80
const RUN_LOG_RENDER_INCREMENT = 80

function comparableAlertSource(source: AlertSourceDraft) {
  return {
    id: source.id,
    name: source.name,
    enabled: source.enabled,
    type: source.type,
    url: source.url,
    method: source.method,
    headers: source.headers,
    query: source.query,
    body: source.body,
    timeout: String(source.timeout),
    samplePayload: source.samplePayload,
    parserRule: source.parserRule ?? {},
    scriptCode: source.scriptCode,
    scriptTimeout: String(source.scriptTimeout),
    autoExecuteEnabled: source.autoExecuteEnabled,
    pollIntervalSeconds: String(source.pollIntervalSeconds),
    failedRetryIntervalSeconds: String(source.failedRetryIntervalSeconds),
    analysisPrompt: source.analysisPrompt,
  }
}

function comparableSettingsDraft(draft: SettingsDraft) {
  return {
    agentEnabled: draft.agentEnabled,
    llmApiBaseUrl: draft.llmApiBaseUrl,
    llmModel: draft.llmModel,
    llmTemperature: String(draft.llmTemperature),
    llmTimeout: String(draft.llmTimeout),
    llmThinkingAdapterEnabled: draft.llmThinkingAdapterEnabled,
    weeklyAlertCleanupEnabled: draft.weeklyAlertCleanupEnabled,
    runLogRetentionDays: String(draft.runLogRetentionDays),
    alertSources: draft.alertSources.map(comparableAlertSource),
  }
}

function settingsDraftContentEqual(left: SettingsDraft, right: SettingsDraft): boolean {
  return JSON.stringify(comparableSettingsDraft(left)) === JSON.stringify(comparableSettingsDraft(right))
}

function applySelectedSourceMirror(draft: SettingsDraft, sourceId: string): SettingsDraft {
  const source = draft.alertSources.find((item) => item.id === sourceId) ?? draft.alertSources[0]
  if (!source) return draft
  return {
    ...draft,
    selectedSourceId: source.id,
    pollIntervalSeconds: source.pollIntervalSeconds,
    failedRetryIntervalSeconds: source.failedRetryIntervalSeconds,
    alertSourceEnabled: source.enabled,
    alertSourceType: source.type,
    alertSourceUrl: source.url,
    alertSourceMethod: source.method,
    alertSourceHeaders: source.headers,
    alertSourceQuery: source.query,
    alertSourceBody: source.body,
    alertSourceTimeout: source.timeout,
    alertSourceSamplePayload: source.samplePayload,
    alertParserRule: source.parserRule,
    alertScriptCode: source.scriptCode,
    alertScriptTimeout: source.scriptTimeout,
    alertSourceName: source.name,
    alertSourceAnalysisPrompt: source.analysisPrompt,
  }
}

function mergeServerDraftWithSelection(serverDraft: SettingsDraft, current: SettingsDraft): SettingsDraft {
  const sourceId = current.alertSources.some((source) => source.id === current.selectedSourceId)
    ? current.selectedSourceId
    : serverDraft.selectedSourceId
  return applySelectedSourceMirror(serverDraft, sourceId)
}

type SettingsDraft = {
  pollIntervalSeconds: string
  failedRetryIntervalSeconds: string
  agentEnabled: boolean
  llmApiBaseUrl: string
  llmApiKey: string
  llmModel: string
  llmTemperature: string
  llmTimeout: string
  llmThinkingAdapterEnabled: boolean
  weeklyAlertCleanupEnabled: boolean
  runLogRetentionDays: string
  fullReportFormatSkill: string
  alertSourceEnabled: boolean
  alertSourceType: string
  alertSourceUrl: string
  alertSourceMethod: string
  alertSourceHeaders: string
  alertSourceQuery: string
  alertSourceBody: string
  alertSourceTimeout: string
  alertSourceSamplePayload: string
  alertParserRule: Record<string, unknown>
  alertScriptCode: string
  alertScriptTimeout: string
  alertSourceName: string
  alertSourceAnalysisPrompt: string
  selectedSourceId: string
  alertSources: AlertSourceDraft[]
}

type AlertSourceDraft = {
  id: string
  name: string
  enabled: boolean
  type: string
  url: string
  method: string
  headers: string
  query: string
  body: string
  timeout: string
  samplePayload: string
  parserRule: Record<string, unknown>
  scriptCode: string
  scriptTimeout: string
  autoExecuteEnabled: boolean
  pollIntervalSeconds: string
  failedRetryIntervalSeconds: string
  analysisPrompt: string
}

function sourceFromSettings(source: RuntimeSettingsResponse['alert_sources'][number]): AlertSourceDraft {
  return {
    id: source.id,
    name: source.name,
    enabled: source.enabled,
    type: source.type,
    url: source.url,
    method: source.method,
    headers: source.headers,
    query: source.query,
    body: source.body,
    timeout: String(source.timeout),
    samplePayload: source.sample_payload,
    parserRule: source.parser_rule,
    scriptCode: source.script_code,
    scriptTimeout: String(source.script_timeout),
    autoExecuteEnabled: source.auto_execute_enabled,
    pollIntervalSeconds: source.poll_interval_seconds,
    failedRetryIntervalSeconds: source.failed_retry_interval_seconds,
    analysisPrompt: source.analysis_prompt,
  }
}

function sourceToPayload(source: AlertSourceDraft) {
  return {
    id: source.id,
    name: source.name,
    enabled: source.enabled,
    type: source.type,
    url: source.url,
    method: source.method,
    headers: source.headers,
    query: source.query,
    body: source.body,
    timeout: source.timeout,
    samplePayload: source.samplePayload,
    parserRule: source.parserRule,
    scriptCode: source.scriptCode,
    scriptTimeout: source.scriptTimeout,
    autoExecuteEnabled: source.autoExecuteEnabled,
    pollIntervalSeconds: source.pollIntervalSeconds,
    failedRetryIntervalSeconds: source.failedRetryIntervalSeconds,
    analysisPrompt: source.analysisPrompt,
  }
}

function createBlankSource(index: number): AlertSourceDraft {
  const id = `source-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`
  return {
    id,
    name: `告警源 ${index + 1}`,
    enabled: false,
    type: 'api',
    url: '',
    method: 'GET',
    headers: '{}',
    query: '{}',
    body: '',
    timeout: '15',
    samplePayload: '',
    parserRule: {},
    scriptCode: '',
    scriptTimeout: '30',
    autoExecuteEnabled: false,
    pollIntervalSeconds: '60',
    failedRetryIntervalSeconds: '0',
    analysisPrompt: '',
  }
}

function buildDraft(settings: RuntimeSettingsResponse): SettingsDraft {
  const sources = (settings.alert_sources?.length ? settings.alert_sources : [settings.alert_source]).map(sourceFromSettings)
  const selectedSource = sources.find((source) => source.id === settings.default_alert_source_id) ?? sources[0] ?? createBlankSource(0)
  return {
    pollIntervalSeconds: selectedSource.pollIntervalSeconds,
    failedRetryIntervalSeconds: selectedSource.failedRetryIntervalSeconds,
    agentEnabled: settings.runtime.agent_enabled,
    llmApiBaseUrl: settings.llm.api_base_url,
    llmApiKey: '',
    llmModel: settings.llm.model,
    llmTemperature: String(settings.llm.temperature),
    llmTimeout: String(settings.llm.timeout),
    llmThinkingAdapterEnabled: settings.llm.thinking_adapter_enabled,
    weeklyAlertCleanupEnabled: settings.runtime.weekly_alert_cleanup_enabled,
    runLogRetentionDays: String(settings.runtime.run_log_retention_days ?? 1),
    fullReportFormatSkill: settings.runtime.full_report_format_skill || 'output-report',
    alertSourceEnabled: selectedSource.enabled,
    alertSourceType: selectedSource.type,
    alertSourceUrl: selectedSource.url,
    alertSourceMethod: selectedSource.method,
    alertSourceHeaders: selectedSource.headers,
    alertSourceQuery: selectedSource.query,
    alertSourceBody: selectedSource.body,
    alertSourceTimeout: selectedSource.timeout,
    alertSourceSamplePayload: selectedSource.samplePayload,
    alertParserRule: selectedSource.parserRule,
    alertScriptCode: selectedSource.scriptCode,
    alertScriptTimeout: selectedSource.scriptTimeout,
    alertSourceName: selectedSource.name,
    alertSourceAnalysisPrompt: selectedSource.analysisPrompt,
    selectedSourceId: selectedSource.id,
    alertSources: sources.length ? sources : [selectedSource],
  }
}

function shortEventData(value: unknown): string {
  if (typeof value === 'string') return value.length > 160 ? `${value.slice(0, 160)}...` : value
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>
    const message = record.message
    if (message && typeof message === 'object') {
      const messageRecord = message as Record<string, unknown>
      const reasoning = typeof messageRecord.reasoning === 'string' ? messageRecord.reasoning.trim() : ''
      if (reasoning) return reasoning.length > 160 ? `${reasoning.slice(0, 160)}...` : reasoning
      const content = typeof messageRecord.content === 'string' ? messageRecord.content.trim() : ''
      if (content) return content.length > 160 ? `${content.slice(0, 160)}...` : content
      const toolCalls = messageRecord.tool_calls
      if (Array.isArray(toolCalls) && toolCalls.length) {
        const names = toolCalls
          .map((item) => (item && typeof item === 'object' ? String((item as Record<string, unknown>).name || '') : ''))
          .filter(Boolean)
        if (names.length) return `工具调用：${names.join(', ')}`
      }
    }
    const promptStats = record.prompt_stats as Record<string, unknown> | undefined
    const systemPrompt = record.system_prompt
    if (systemPrompt && typeof systemPrompt === 'object') {
      const promptRecord = systemPrompt as Record<string, unknown>
      const chars = Number(promptRecord.content_chars || 0)
      const truncated = promptRecord.content_truncated === true
      const windowTrunc = promptStats?.window_truncated === true
      const dropped = Number(promptStats?.dropped_message_count || 0)
      const flag = truncated || windowTrunc ? `（已截断${dropped > 0 ? `，丢弃 ${dropped} 条` : ''}）` : ''
      const hint = `系统提示词 ${chars} 字${flag}`
      return hint.length > 160 ? `${hint.slice(0, 160)}...` : hint
    }
    const promptMessages = record.prompt_messages
    if (Array.isArray(promptMessages) && promptMessages.length) {
      const windowTrunc = promptStats?.window_truncated === true
      const dropped = Number(promptStats?.dropped_message_count || 0)
      const suffix = windowTrunc ? `（窗口已截断，丢弃 ${dropped} 条历史消息）` : ''
      return `提示词审计 ${promptMessages.length} 条消息${suffix}`
    }
    const audit = record.audit
    if (audit && typeof audit === 'object') {
      const auditRecord = audit as Record<string, unknown>
      const skillName = String(auditRecord.skill_name || '')
      const outcome = String(auditRecord.outcome || '')
      const compliant = auditRecord.compliant === true
      const reasons = auditRecord.failure_reasons
      if (!compliant && Array.isArray(reasons) && reasons.length) {
        const line = `${skillName} 不合规: ${reasons.slice(0, 2).join('; ')}`
        return line.length > 160 ? `${line.slice(0, 160)}...` : line
      }
      if (compliant && outcome.startsWith('executed')) {
        return `${skillName} 入参合规并已执行`
      }
      if (outcome === 'blocked_approval_required') {
        return `${skillName} 入参合规，等待审批`
      }
    }
    const provenance = record.argument_provenance
    if (Array.isArray(provenance) && provenance.length) {
      const first = provenance[0] as Record<string, unknown>
      const sources = first.argument_sources
      if (Array.isArray(sources) && sources.length) {
        const hit = sources[0] as Record<string, unknown>
        const path = String(hit.argument_path || '')
        const value = String(hit.value || '')
        const srcList = hit.sources
        const srcHint =
          Array.isArray(srcList) && srcList.length
            ? String((srcList[0] as Record<string, unknown>).path || (srcList[0] as Record<string, unknown>).kind || '')
            : '未匹配到上下文'
        const line = `入参 ${path}=${value} ← ${srcHint}`
        return line.length > 160 ? `${line.slice(0, 160)}...` : line
      }
    }
    const requestCount = record.request_messages
    if (Array.isArray(requestCount) && requestCount.length) {
      return `模型输入 ${requestCount.length} 条上下文消息`
    }
    const taskPrompt = typeof record.task_prompt === 'string' ? record.task_prompt.trim() : ''
    if (taskPrompt) return taskPrompt.length > 160 ? `${taskPrompt.slice(0, 160)}...` : taskPrompt
    const hit = [record.summary, record.content, record.final_response, record.error, record.title].find(
      (item) => typeof item === 'string' && item.trim(),
    )
    if (typeof hit === 'string') return hit.length > 160 ? `${hit.slice(0, 160)}...` : hit
  }
  return ''
}

function runLogEventSeq(event: { seq?: number; data?: unknown }): number {
  if (typeof event.seq === 'number') return event.seq
  if (event.data && typeof event.data === 'object' && typeof (event.data as Record<string, unknown>).seq === 'number') {
    return Number((event.data as Record<string, unknown>).seq)
  }
  return Number.MAX_SAFE_INTEGER
}

function runLogEventDomId(event: { seq?: number; ts: string }, index: number): string {
  const seq = runLogEventSeq(event)
  if (seq !== Number.MAX_SAFE_INTEGER) return `run-log-event-${seq}`
  return `run-log-event-${index}-${event.ts}`
}

function runLogEventDataRecord(event: { data?: unknown }): Record<string, unknown> | null {
  if (!event.data || typeof event.data !== 'object') return null
  return event.data as Record<string, unknown>
}

function llmRequestMatchesPromptReference(
  record: Record<string, unknown>,
  ref: Record<string, unknown>,
  strict: boolean,
): boolean {
  const digest = String(ref.prompt_digest || '').trim()
  if (!digest || String(record.prompt_digest || '').trim() !== digest) return false
  if (!strict) return true
  if (ref.turn !== undefined && ref.turn !== null && record.turn !== ref.turn) return false
  if (ref.graph && String(record.graph || '') !== String(ref.graph)) return false
  if (ref.agent_name && String(record.agent_name || '') !== String(ref.agent_name)) return false
  if (ref.node && String(record.node || '') !== String(ref.node)) return false
  if (ref.scope && String(record.scope || '') !== String(ref.scope)) return false
  return true
}

function findLlmRequestForPromptReference(
  events: RunLogEvent[],
  ref: Record<string, unknown>,
): { event: RunLogEvent; index: number } | null {
  const digest = String(ref.prompt_digest || '').trim()
  if (!digest) return null
  const sorted = [...events].sort((left, right) => runLogEventSeq(left) - runLogEventSeq(right))
  for (const strict of [true, false]) {
    for (let index = 0; index < sorted.length; index++) {
      const event = sorted[index]
      const record = runLogEventDataRecord(event)
      if (!record || record.event_type !== 'llm_request') continue
      if (!llmRequestMatchesPromptReference(record, ref, strict)) continue
      return { event, index }
    }
  }
  return null
}

function RunLogEventSummary({
  event,
  events,
  onJumpToPrompt,
}: {
  event: RunLogEvent
  events: RunLogEvent[]
  onJumpToPrompt: (ref: Record<string, unknown>) => void
}) {
  const record = runLogEventDataRecord(event)
  if (record) {
    const constructed = record.constructed_tool_calls
    const promptRef = record.prompt_reference
    if (Array.isArray(constructed) && constructed.length && promptRef && typeof promptRef === 'object') {
      const first = constructed[0] as Record<string, unknown>
      const skillName = String(first.skill_name || '')
      const refRecord = promptRef as Record<string, unknown>
      const digest = String(refRecord.prompt_digest || '').trim()
      const match = findLlmRequestForPromptReference(events, refRecord)
      const turnLabel = refRecord.turn !== undefined && refRecord.turn !== null ? String(refRecord.turn) : '?'
      return (
        <div className="mt-1 text-xs text-slate-400">
          <span>构造入参: {skillName}</span>
          <span className="mx-1">·</span>
          {match ? (
            <button
              type="button"
              className="text-indigo-300 underline decoration-indigo-500/50 underline-offset-2 hover:text-indigo-200"
              onClick={(clickEvent) => {
                clickEvent.preventDefault()
                clickEvent.stopPropagation()
                onJumpToPrompt(refRecord)
              }}
            >
              跳转到提示词原文（llm_request #{runLogEventSeq(match.event)}，第 {turnLabel} 轮）
            </button>
          ) : (
            <span className="text-amber-300">
              未找到对应 llm_request{digest ? `（digest=${digest.slice(0, 12)}…）` : ''}
            </span>
          )}
        </div>
      )
    }
    if (record.event_type === 'llm_request' && Array.isArray(record.prompt_messages) && record.prompt_messages.length) {
      const promptStats = record.prompt_stats as Record<string, unknown> | undefined
      const windowTrunc = promptStats?.window_truncated === true
      const dropped = Number(promptStats?.dropped_message_count || 0)
      const suffix = windowTrunc ? `（窗口已截断，丢弃 ${dropped} 条）` : ''
      return (
        <div className="mt-1 text-xs text-slate-400">
          提示词审计 {record.prompt_messages.length} 条消息{suffix}
          <span className="ml-1 text-indigo-300/90">· 本条为技能入参构造时的模型输入</span>
        </div>
      )
    }
  }
  const text = shortEventData(event.data)
  if (!text) return null
  return <div className="mt-1 text-xs text-slate-400">{text}</div>
}

export default function SentinelFlowSettingsPage() {
  const { data: settings, loading, error, reload: reloadSettings, setData: setSettings } = useSentinelFlowAsyncData(fetchRuntimeSettings, [])
  const { data: health } = useSentinelFlowAsyncData(fetchHealth, [])
  const { data: skillsData } = useSentinelFlowAsyncData(fetchSkills, [])
  const [saving, setSaving] = useState(false)
  const [saveMessage, setSaveMessage] = useState<string | null>(null)
  const [saveMessageTone, setSaveMessageTone] = useState<'success' | 'error'>('success')
  const [serverDraftChanged, setServerDraftChanged] = useState(false)
  const [parserSaveMessage, setParserSaveMessage] = useState<string | null>(null)
  const [parserSaveTone, setParserSaveTone] = useState<'success' | 'error'>('success')
  const [draft, setDraft] = useState<SettingsDraft>(() =>
    readSessionValue<SettingsDraft>(SETTINGS_DRAFT_KEY, {
      pollIntervalSeconds: '',
      failedRetryIntervalSeconds: '0',
      agentEnabled: true,
      llmApiBaseUrl: 'https://api.openai.com/v1',
      llmApiKey: '',
      llmModel: '',
      llmTemperature: '0',
      llmTimeout: '60',
      llmThinkingAdapterEnabled: false,
      weeklyAlertCleanupEnabled: false,
      runLogRetentionDays: '1',
      fullReportFormatSkill: 'output-report',
      alertSourceEnabled: false,
      alertSourceType: 'api',
      alertSourceUrl: '',
      alertSourceMethod: 'GET',
      alertSourceHeaders: '{}',
      alertSourceQuery: '{}',
      alertSourceBody: '',
      alertSourceTimeout: '15',
      alertSourceSamplePayload: '',
      alertParserRule: {},
      alertScriptCode: '',
      alertScriptTimeout: '30',
      alertSourceName: '默认告警源',
      alertSourceAnalysisPrompt: '',
      selectedSourceId: 'default',
      alertSources: [createBlankSource(0)],
    }),
  )
  const [parserMessage, setParserMessage] = useState<string | null>(null)
  const [parserWarnings, setParserWarnings] = useState<string[]>([])
  const [fetchMessage, setFetchMessage] = useState<string | null>(null)
  const [fetchMessageTone, setFetchMessageTone] = useState<'success' | 'error'>('success')
  const [parserPreview, setParserPreview] = useState<Array<Record<string, unknown>>>([])
  const [testingFetch, setTestingFetch] = useState(false)
  const [testingParse, setTestingParse] = useState(false)
  const [generatingParser, setGeneratingParser] = useState(false)
  const [fetchPreview, setFetchPreview] = useState<unknown>(null)
  const [fetchPreviewExpanded, setFetchPreviewExpanded] = useState(false)
  const [debugLogClickCount, setDebugLogClickCount] = useState(0)
  const [debugLogUnlocked, setDebugLogUnlocked] = useState(false)
  const [debugLogOpen, setDebugLogOpen] = useState(false)
  const [debugLogLoading, setDebugLogLoading] = useState(false)
  const [debugLogError, setDebugLogError] = useState<string | null>(null)
  const [debugLogDates, setDebugLogDates] = useState<RunLogDateSummary[]>([])
  const [debugLogAlerts, setDebugLogAlerts] = useState<RunLogAlertSummary[]>([])
  const [debugLogDetail, setDebugLogDetail] = useState<RunLogDetail | null>(null)
  const [selectedDebugDate, setSelectedDebugDate] = useState('')
  const [selectedDebugLogId, setSelectedDebugLogId] = useState('')
  const [visibleRunLogEventCount, setVisibleRunLogEventCount] = useState(RUN_LOG_INITIAL_RENDER_COUNT)
  const [highlightedRunLogEventId, setHighlightedRunLogEventId] = useState<string | null>(null)
  const [activeRunLogRetentionDays, setActiveRunLogRetentionDays] = useState<number>(1)
  const docSkillOptions = useMemo(() => {
    return ((skillsData?.skills ?? []) as SkillSummary[])
      .filter((skill) => skill.type === 'doc')
      .sort((a, b) => a.name.localeCompare(b.name))
  }, [skillsData?.skills])
  const [savingRunLogRetention, setSavingRunLogRetention] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const runLogEventRefs = useRef<Record<string, HTMLDetailsElement | null>>({})
  const debugLogRequestSeq = useRef(0)
  const serverDraftRef = useRef<SettingsDraft | null>(null)
  const userEditedRef = useRef(false)

  const jumpToRunLogPrompt = useCallback((ref: Record<string, unknown>) => {
    if (!debugLogDetail?.events?.length) return
    const match = findLlmRequestForPromptReference(debugLogDetail.events, ref)
    if (!match) return
    const eventId = runLogEventDomId(match.event, match.index)
    let element = runLogEventRefs.current[eventId]
    if (!element) {
      setVisibleRunLogEventCount((current) => Math.max(current, match.index + RUN_LOG_RENDER_INCREMENT))
      window.setTimeout(() => {
        const nextElement = runLogEventRefs.current[eventId]
        if (!nextElement) return
        setHighlightedRunLogEventId(eventId)
        nextElement.open = true
        nextElement.scrollIntoView({ behavior: 'smooth', block: 'center' })
        window.setTimeout(() => {
          setHighlightedRunLogEventId((current) => (current === eventId ? null : current))
        }, 2800)
      }, 0)
      return
    }
    setHighlightedRunLogEventId(eventId)
    element.open = true
    element.scrollIntoView({ behavior: 'smooth', block: 'center' })
    window.setTimeout(() => {
      setHighlightedRunLogEventId((current) => (current === eventId ? null : current))
    }, 2800)
  }, [debugLogDetail?.events])

  const sortedDebugLogEvents = useMemo(
    () => [...(debugLogDetail?.events ?? [])].sort((left, right) => runLogEventSeq(left) - runLogEventSeq(right)),
    [debugLogDetail?.events],
  )
  const visibleDebugLogEvents = sortedDebugLogEvents.slice(0, visibleRunLogEventCount)

  const fetchPreviewStr = fetchPreview ? JSON.stringify(fetchPreview, null, 2) : ''
  const fetchPreviewLines = fetchPreviewStr.split('\n')
  const isLongPreview = fetchPreviewLines.length > 20
  const isScriptMode = draft.alertSourceType === 'script'
  const selectedSourceIndex = Math.max(0, draft.alertSources.findIndex((source) => source.id === draft.selectedSourceId))
  const canEditSourceAnalysisPrompt = selectedSourceIndex > 0

  const configCenterPreviewItems = useMemo(() => {
    if (loading) {
      return [{ label: '配置状态', value: '加载中...' }]
    }
    const parserConfigured =
      isScriptMode || Object.keys(draft.alertParserRule ?? {}).length > 0 || Boolean(settings?.alert_source.parser_configured)
    const llmApiBase = draft.llmApiBaseUrl.trim()
    let llmApiSummary = llmApiBase || '未配置'
    if (llmApiBase) {
      try {
        const parsed = new URL(llmApiBase)
        llmApiSummary = `${parsed.host}${parsed.pathname.replace(/\/$/, '')}`
      } catch {
        llmApiSummary = llmApiBase.length > 32 ? `${llmApiBase.slice(0, 32)}...` : llmApiBase
      }
    }
    return [
      { label: 'LLM 模型', value: draft.llmModel.trim() || '未配置' },
      { label: 'LLM 地址', value: llmApiSummary },
      { label: 'LLM 温度', value: draft.llmTemperature.trim() || '0' },
      { label: 'LLM Key', value: settings?.llm.api_key_configured ? '已配置' : '未配置' },
      { label: '思考模型适配', value: draft.llmThinkingAdapterEnabled ? '已开启' : '未开启' },
      { label: '告警源', value: `${draft.alertSources.length} 个` },
      { label: '解析规则', value: isScriptMode ? '脚本模式' : parserConfigured ? '已配置' : '未配置' },
      { label: '每周刷新告警', value: draft.weeklyAlertCleanupEnabled ? '已开启' : '未开启' },
    ]
  }, [loading, draft, settings, isScriptMode])

  function handleImportRuleFromFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = (e) => {
      try {
        const text = e.target?.result as string
        const parsed = JSON.parse(text)
        updateDraft('alertParserRule', parsed)
        setParserMessage('导入规则成功，请在下方预览确认无误后点击“保存解析规则”。')
      } catch (err) {
        setParserMessage('导入失败：该文件不是合法的 JSON 格式。')
      }
    }
    reader.readAsText(file)
    event.target.value = ''
  }

  function applyServerSettingsToDraft(nextSettings: RuntimeSettingsResponse) {
    const nextDraft = buildDraft(nextSettings)
    serverDraftRef.current = nextDraft
    userEditedRef.current = false
    setServerDraftChanged(false)
    setDraft(nextDraft)
  }

  useEffect(() => {
    if (!settings) return
    const nextServerDraft = buildDraft(settings)
    serverDraftRef.current = nextServerDraft
    if (userEditedRef.current && !settingsDraftContentEqual(draft, nextServerDraft)) {
      setServerDraftChanged(true)
      return
    }
    userEditedRef.current = false
    setServerDraftChanged(false)
    setDraft((current) => mergeServerDraftWithSelection(nextServerDraft, current))
  }, [settings])

  useEffect(() => {
    writeSessionValue(SETTINGS_DRAFT_KEY, draft)
  }, [draft])

  useEffect(() => {
    if (!debugLogOpen) return
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previousOverflow
    }
  }, [debugLogOpen])

  function updateSelectedSource(current: SettingsDraft, updates: Partial<AlertSourceDraft>): SettingsDraft {
    const sourceId = current.selectedSourceId || current.alertSources[0]?.id || 'default'
    return {
      ...current,
      alertSources: current.alertSources.map((source) => (
        source.id === sourceId ? { ...source, ...updates } : source
      )),
    }
  }

  function updateDraft<K extends keyof SettingsDraft>(key: K, value: SettingsDraft[K]) {
    userEditedRef.current = true
    setDraft((current) => {
      const next = { ...current, [key]: value }
      if (key === 'alertSourceName') return updateSelectedSource(next, { name: String(value) })
      if (key === 'alertSourceEnabled') return updateSelectedSource(next, { enabled: Boolean(value) })
      if (key === 'alertSourceType') return updateSelectedSource(next, { type: String(value) })
      if (key === 'alertSourceUrl') return updateSelectedSource(next, { url: String(value) })
      if (key === 'alertSourceMethod') return updateSelectedSource(next, { method: String(value) })
      if (key === 'alertSourceHeaders') return updateSelectedSource(next, { headers: String(value) })
      if (key === 'alertSourceQuery') return updateSelectedSource(next, { query: String(value) })
      if (key === 'alertSourceBody') return updateSelectedSource(next, { body: String(value) })
      if (key === 'alertSourceTimeout') return updateSelectedSource(next, { timeout: String(value) })
      if (key === 'alertSourceSamplePayload') return updateSelectedSource(next, { samplePayload: String(value) })
      if (key === 'alertParserRule') return updateSelectedSource(next, { parserRule: value as Record<string, unknown> })
      if (key === 'alertScriptCode') return updateSelectedSource(next, { scriptCode: String(value) })
      if (key === 'alertScriptTimeout') return updateSelectedSource(next, { scriptTimeout: String(value) })
      if (key === 'pollIntervalSeconds') return updateSelectedSource(next, { pollIntervalSeconds: String(value) })
      if (key === 'failedRetryIntervalSeconds') return updateSelectedSource(next, { failedRetryIntervalSeconds: String(value) })
      if (key === 'alertSourceAnalysisPrompt') return updateSelectedSource(next, { analysisPrompt: String(value) })
      return next
    })
  }

  function selectSource(sourceId: string) {
    setDraft((current) => applySelectedSourceMirror(current, sourceId))
  }

  function addSource() {
    userEditedRef.current = true
    setDraft((current) => {
      const source = createBlankSource(current.alertSources.length)
      return {
        ...current,
        alertSources: [...current.alertSources, source],
        selectedSourceId: source.id,
        pollIntervalSeconds: source.pollIntervalSeconds,
        failedRetryIntervalSeconds: source.failedRetryIntervalSeconds,
        alertSourceEnabled: source.enabled,
        alertSourceType: source.type,
        alertSourceUrl: source.url,
        alertSourceMethod: source.method,
        alertSourceHeaders: source.headers,
        alertSourceQuery: source.query,
        alertSourceBody: source.body,
        alertSourceTimeout: source.timeout,
        alertSourceSamplePayload: source.samplePayload,
        alertParserRule: source.parserRule,
        alertScriptCode: source.scriptCode,
        alertScriptTimeout: source.scriptTimeout,
        alertSourceName: source.name,
        alertSourceAnalysisPrompt: source.analysisPrompt,
      }
    })
  }

  function deleteSelectedSource() {
    userEditedRef.current = true
    setDraft((current) => {
      if (current.alertSources.length <= 1) return current
      const remaining = current.alertSources.filter((source) => source.id !== current.selectedSourceId)
      const nextSource = remaining[0]
      return {
        ...current,
        alertSources: remaining,
        selectedSourceId: nextSource.id,
        pollIntervalSeconds: nextSource.pollIntervalSeconds,
        failedRetryIntervalSeconds: nextSource.failedRetryIntervalSeconds,
        alertSourceEnabled: nextSource.enabled,
        alertSourceType: nextSource.type,
        alertSourceUrl: nextSource.url,
        alertSourceMethod: nextSource.method,
        alertSourceHeaders: nextSource.headers,
        alertSourceQuery: nextSource.query,
        alertSourceBody: nextSource.body,
        alertSourceTimeout: nextSource.timeout,
        alertSourceSamplePayload: nextSource.samplePayload,
        alertParserRule: nextSource.parserRule,
        alertScriptCode: nextSource.scriptCode,
        alertScriptTimeout: nextSource.scriptTimeout,
        alertSourceName: nextSource.name,
        alertSourceAnalysisPrompt: nextSource.analysisPrompt,
      }
    })
  }

  async function handleSave() {
    setSaving(true)
    setSaveMessage(null)
    try {
      const saved = await saveRuntimeSettings({
        ...draft,
        llmApiKey: draft.llmApiKey.trim() || undefined,
        alertSources: draft.alertSources.map(sourceToPayload),
      })
      setSettings(saved)
      applyServerSettingsToDraft(saved)
      setSaveMessageTone('success')
      setSaveMessage(withProductName('配置已保存到 SentinelFlow 项目级配置文件。'))
      void reloadSettings()
    } catch (saveError) {
      setSaveMessageTone('error')
      setSaveMessage(saveError instanceof Error ? saveError.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  async function handleSaveParserRule() {
    setSaving(true)
    setParserSaveMessage(null)
    try {
      const saved = await saveRuntimeSettings({
        ...draft,
        llmApiKey: draft.llmApiKey.trim() || undefined,
        alertSources: draft.alertSources.map(sourceToPayload),
      })
      setSettings(saved)
      applyServerSettingsToDraft(saved)
      setParserSaveTone('success')
      setParserSaveMessage('解析规则已保存。')
      void reloadSettings()
    } catch (saveError) {
      setParserSaveTone('error')
      setParserSaveMessage(saveError instanceof Error ? saveError.message : '保存解析规则失败')
    } finally {
      setSaving(false)
    }
  }

  async function handleReset() {
    setSaving(true)
    setSaveMessage(null)
    try {
      const reset = await resetRuntimeSettings()
      setSettings(reset)
      applyServerSettingsToDraft(reset)
      setSaveMessageTone('success')
      setSaveMessage('项目级配置已重置为默认值。')
      setParserMessage(null)
      setParserWarnings([])
      setParserPreview([])
      setFetchPreview(null)
    } catch (resetError) {
      setSaveMessageTone('error')
      setSaveMessage(resetError instanceof Error ? resetError.message : '重置失败')
    } finally {
      setSaving(false)
    }
  }

  async function handleTestFetch() {
    setTestingFetch(true)
    setFetchMessage(null)
    try {
      const result = await testAlertSourceFetch({
        alertSourceEnabled: draft.alertSourceEnabled,
        alertSourceType: draft.alertSourceType,
        alertSourceUrl: draft.alertSourceUrl,
        alertSourceMethod: draft.alertSourceMethod,
        alertSourceHeaders: draft.alertSourceHeaders,
        alertSourceQuery: draft.alertSourceQuery,
        alertSourceBody: draft.alertSourceBody,
        alertSourceTimeout: draft.alertSourceTimeout,
        alertScriptCode: draft.alertScriptCode,
        alertScriptTimeout: draft.alertScriptTimeout,
      })
      setFetchPreview(result.raw_payload ?? result.alerts ?? result.raw_response ?? result ?? null)
      setFetchMessageTone('success')
      setFetchMessage(isScriptMode ? `脚本执行成功，返回 ${result.count ?? result.alerts?.length ?? 0} 条标准化告警。` : '接口测试成功，已经拿到原始告警响应。')
    } catch (testError) {
      setFetchPreview(null)
      setFetchMessageTone('error')
      setFetchMessage(testError instanceof Error ? testError.message : isScriptMode ? '脚本测试失败' : '接口测试失败')
    } finally {
      setTestingFetch(false)
    }
  }

  async function handleGenerateParser() {
    setGeneratingParser(true)
    setParserMessage(null)
    try {
      const generated = await generateAlertParser(draft.alertSourceSamplePayload)
      updateDraft('alertParserRule', generated.parser_rule)
      setParserPreview(generated.preview.alerts)
      setParserWarnings(generated.preview.warnings ?? [])
      setParserMessage(generated.reason)
    } catch (generateError) {
      setParserWarnings([])
      setParserMessage(generateError instanceof Error ? generateError.message : '自动解析失败')
    } finally {
      setGeneratingParser(false)
    }
  }

  async function handleTestParser() {
    setTestingParse(true)
    setParserMessage(null)
    try {
      const preview = await testAlertParser({
        samplePayload: draft.alertSourceSamplePayload,
        parserRule: draft.alertParserRule,
      })
      setParserPreview(preview.alerts)
      setParserWarnings(preview.warnings ?? [])
      setParserMessage(
        preview.warnings?.length
          ? `解析成功，预览到 ${preview.count} 条告警，并发现 ${preview.warnings.length} 条需要关注的解析风险。`
          : `解析成功，预览到 ${preview.count} 条告警。`,
      )
    } catch (previewError) {
      setParserWarnings([])
      setParserMessage(previewError instanceof Error ? previewError.message : '测试解析失败')
    } finally {
      setTestingParse(false)
    }
  }

  async function openDetailedRuntimeLog() {
    const requestSeq = debugLogRequestSeq.current + 1
    debugLogRequestSeq.current = requestSeq
    setDebugLogOpen(true)
    setVisibleRunLogEventCount(RUN_LOG_INITIAL_RENDER_COUNT)
    setHighlightedRunLogEventId(null)
    runLogEventRefs.current = {}
    setDebugLogLoading(true)
    setDebugLogError(null)
    try {
      const data = await fetchRunLogDates()
      if (debugLogRequestSeq.current !== requestSeq) return
      setDebugLogDates(data.dates ?? [])
      setActiveRunLogRetentionDays(data.retention_days ?? 1)
      updateDraft('runLogRetentionDays', String(data.retention_days ?? 1))
      const nextDate = selectedDebugDate || data.dates?.[0]?.date || ''
      setSelectedDebugDate(nextDate)
      if (nextDate) {
        const alertData = await fetchRunLogAlerts(nextDate)
        if (debugLogRequestSeq.current !== requestSeq) return
        setDebugLogAlerts(alertData.alerts ?? [])
        const nextLogId = selectedDebugLogId || alertData.alerts?.[0]?.log_id || ''
        setSelectedDebugLogId(nextLogId)
        if (nextLogId) {
          const detail = await fetchRunLogDetail(nextDate, nextLogId)
          if (debugLogRequestSeq.current !== requestSeq) return
          setDebugLogDetail(detail)
        } else {
          setDebugLogDetail(null)
        }
      } else {
        setDebugLogAlerts([])
        setDebugLogDetail(null)
      }
    } catch (error) {
      if (debugLogRequestSeq.current !== requestSeq) return
      setDebugLogDetail(null)
      setDebugLogError(error instanceof Error ? error.message : '读取详细运行日志失败')
    } finally {
      if (debugLogRequestSeq.current === requestSeq) setDebugLogLoading(false)
    }
  }

  async function selectDebugDate(date: string) {
    const requestSeq = debugLogRequestSeq.current + 1
    debugLogRequestSeq.current = requestSeq
    setHighlightedRunLogEventId(null)
    runLogEventRefs.current = {}
    setSelectedDebugDate(date)
    setSelectedDebugLogId('')
    setDebugLogDetail(null)
    setDebugLogLoading(true)
    setDebugLogError(null)
    try {
      const data = await fetchRunLogAlerts(date)
      if (debugLogRequestSeq.current !== requestSeq) return
      setDebugLogAlerts(data.alerts ?? [])
      const firstLogId = data.alerts?.[0]?.log_id || ''
      setSelectedDebugLogId(firstLogId)
      if (firstLogId) {
        const detail = await fetchRunLogDetail(date, firstLogId)
        if (debugLogRequestSeq.current !== requestSeq) return
        setDebugLogDetail(detail)
      }
    } catch (error) {
      if (debugLogRequestSeq.current !== requestSeq) return
      setDebugLogError(error instanceof Error ? error.message : '读取告警日志列表失败')
    } finally {
      if (debugLogRequestSeq.current === requestSeq) setDebugLogLoading(false)
    }
  }

  async function selectDebugLog(date: string, logId: string) {
    const requestSeq = debugLogRequestSeq.current + 1
    debugLogRequestSeq.current = requestSeq
    setSelectedDebugLogId(logId)
    setVisibleRunLogEventCount(RUN_LOG_INITIAL_RENDER_COUNT)
    setHighlightedRunLogEventId(null)
    runLogEventRefs.current = {}
    setDebugLogDetail(null)
    setDebugLogLoading(true)
    setDebugLogError(null)
    try {
      const detail = await fetchRunLogDetail(date, logId)
      if (debugLogRequestSeq.current !== requestSeq) return
      setDebugLogDetail(detail)
    } catch (error) {
      if (debugLogRequestSeq.current !== requestSeq) return
      setDebugLogError(error instanceof Error ? error.message : '读取告警运行日志失败')
    } finally {
      if (debugLogRequestSeq.current === requestSeq) setDebugLogLoading(false)
    }
  }

  async function handleSaveRunLogRetention() {
    const days = Math.max(Number.parseInt(draft.runLogRetentionDays, 10) || 1, 1)
    setSavingRunLogRetention(true)
    setDebugLogError(null)
    try {
      const data = await saveRunLogSettings(days)
      setActiveRunLogRetentionDays(data.retention_days ?? days)
      updateDraft('runLogRetentionDays', String(data.retention_days ?? days))
      setDebugLogDates(data.dates ?? [])
      if (selectedDebugDate && !(data.dates ?? []).some((item) => item.date === selectedDebugDate)) {
        setSelectedDebugDate('')
        setSelectedDebugLogId('')
        setDebugLogAlerts([])
        setDebugLogDetail(null)
      }
    } catch (error) {
      setDebugLogError(error instanceof Error ? error.message : '保存日志保留天数失败')
    } finally {
      setSavingRunLogRetention(false)
    }
  }

  function handleDebugLogButtonClick() {
    if (debugLogUnlocked) {
      void openDetailedRuntimeLog()
      return
    }
    setDebugLogClickCount((current) => {
      const next = current + 1
      if (next >= DEBUG_LOG_UNLOCK_CLICKS) {
        setDebugLogUnlocked(true)
        void openDetailedRuntimeLog()
      }
      return Math.min(next, DEBUG_LOG_UNLOCK_CLICKS)
    })
  }

  return (
    <div className="sentinelflow-page-stack">
      <PageHeader
        title="平台设置"
        description="告警接入、解析规则与运行参数"
        icon={<SettingsIcon className="w-8 h-8" />}
      />

      {loading ? <p className="sentinelflow-muted-text">正在读取运行配置...</p> : null}
      {error ? <div className="sentinelflow-message-block sentinelflow-message-error">{error}</div> : null}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <div className="rounded-xl border border-gray-200 bg-white p-5">
          <div className="mb-2 text-sm font-semibold text-gray-500">运行模式</div>
          <div className="text-2xl font-bold text-gray-900">{settings?.runtime.agent_enabled ? 'Agent' : 'Basic'}</div>
        </div>
        <div className="rounded-xl border border-gray-200 bg-white p-5">
          <div className="mb-2 text-sm font-semibold text-gray-500">轮询周期</div>
          <div className="text-2xl font-bold text-gray-900">
            {settings ? `${settings.runtime.poll_interval_seconds}s` : '--'}
          </div>
        </div>
        <div className="rounded-xl border border-gray-200 bg-white p-5">
          <div className="mb-2 text-sm font-semibold text-gray-500">失败重试</div>
          <div className="text-2xl font-bold text-gray-900">
            {settings && Number(settings.runtime.failed_retry_interval_seconds) > 0
              ? `${settings.runtime.failed_retry_interval_seconds}s`
              : settings
                ? '关闭'
                : '--'}
          </div>
        </div>
        <div className="rounded-xl border border-gray-200 bg-white p-5">
          <div className="mb-2 text-sm font-semibold text-gray-500">告警接入</div>
          <div className="text-2xl font-bold text-gray-900">
            {!settings
              ? '--'
              : settings.alert_source.enabled
                ? settings.alert_source.type === 'script'
                  ? '脚本'
                  : '接口'
                : '未启用'}
          </div>
        </div>
        <div className="rounded-xl border border-gray-200 bg-white p-5">
          <div className="mb-2 text-sm font-semibold text-gray-500">API</div>
          <div className="text-2xl font-bold text-gray-900">{health?.status === 'ok' ? '正常' : health?.status ?? '--'}</div>
        </div>
        <div className="rounded-xl border border-gray-200 bg-white p-5">
          <div className="mb-2 text-sm font-semibold text-gray-500">自然语言调度</div>
          <div className="text-2xl font-bold text-gray-900">
            {settings ? (settings.features.natural_language_dispatch ? '已开启' : '未开启') : '--'}
          </div>
        </div>
        <div className="rounded-xl border border-gray-200 bg-white p-5">
          <div className="mb-2 text-sm font-semibold text-gray-500">自动轮询</div>
          <div className="text-2xl font-bold text-gray-900">
            {settings ? (settings.features.alert_polling ? '已开启' : '未开启') : '--'}
          </div>
        </div>
        <div className="rounded-xl border border-gray-200 bg-white p-5">
          <div className="mb-2 text-sm font-semibold text-gray-500">Agent</div>
          <div className="text-2xl font-bold text-gray-900">
            {settings ? (settings.llm.agent_available ? '可用' : '不可用') : '--'}
          </div>
        </div>
      </div>

      <Surface
        title="配置中心"
        subtitle="平台级通用参数、告警源接入与解析规则。"
        collapsible
        defaultOpen={false}
        collapsedPreview={
          <>
            {serverDraftChanged ? (
              <div className="mb-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900">
                当前页面存在未保存草稿
              </div>
            ) : null}
            <SurfacePreviewGrid items={configCenterPreviewItems} />
          </>
        }
      >
        {saveMessage ? <div className={`mb-4 sentinelflow-message-block ${saveMessageTone === 'success' ? 'sentinelflow-message-success' : 'sentinelflow-message-error'}`}>{saveMessage}</div> : null}
        {serverDraftChanged ? (
          <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
            服务端配置已刷新，但当前页面存在未保存草稿，已保留本次会话草稿。
            <button
              type="button"
              className="ml-3 font-semibold text-amber-950 underline"
              onClick={() => {
                if (!serverDraftRef.current) return
                userEditedRef.current = false
                setServerDraftChanged(false)
                setDraft(serverDraftRef.current)
              }}
            >
              使用服务端配置
            </button>
          </div>
        ) : null}
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-gray-200 bg-white p-4">
          <div className="text-sm leading-6 text-gray-600">
            {withProductName('当前草稿会保存在浏览器会话里，同时也可以保存到 SentinelFlow 项目级配置文件。')}
          </div>
          <div className="flex items-center gap-2">
            <button type="button" className="flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2 text-gray-700 transition-colors hover:bg-gray-50" onClick={() => void handleReset()} disabled={saving}>
              <RotateCcw className="w-4 h-4" />
              恢复默认配置
            </button>
            <button type="button" className="flex items-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-white transition-colors hover:bg-red-700" onClick={() => void handleSave()} disabled={saving}>
              <Save className="w-4 h-4" />
              {saving ? '保存中...' : '保存到后端'}
            </button>
          </div>
        </div>

        <div className="sentinelflow-settings-form">
          <label className="sentinelflow-settings-field"><span>LLM API 地址</span><input className="sentinelflow-settings-input" value={draft.llmApiBaseUrl} onChange={(event) => updateDraft('llmApiBaseUrl', event.target.value)} /></label>
          <label className="sentinelflow-settings-field"><span>LLM 模型名</span><input className="sentinelflow-settings-input" value={draft.llmModel} onChange={(event) => updateDraft('llmModel', event.target.value)} /></label>
          <label className="sentinelflow-settings-field"><span>LLM 温度</span><input className="sentinelflow-settings-input" value={draft.llmTemperature} onChange={(event) => updateDraft('llmTemperature', event.target.value)} /></label>
          <label className="sentinelflow-settings-field"><span>LLM 超时（秒）</span><input className="sentinelflow-settings-input" value={draft.llmTimeout} onChange={(event) => updateDraft('llmTimeout', event.target.value)} /></label>
          <div className="sentinelflow-settings-field sentinelflow-settings-field-full">
            <span>LLM API Key</span>
            <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-center">
              <input type="password" className="sentinelflow-settings-input" value={draft.llmApiKey} onChange={(event) => updateDraft('llmApiKey', event.target.value)} placeholder={settings?.llm.api_key_configured ? '已配置，可重新填写覆盖' : ''} />
              <label className="sentinelflow-settings-toggle whitespace-nowrap rounded-lg border border-slate-200 px-3 py-2">
                <input type="checkbox" checked={draft.llmThinkingAdapterEnabled} onChange={(event) => updateDraft('llmThinkingAdapterEnabled', event.target.checked)} />
                <span>思考模型适配</span>
              </label>
            </div>
          </div>
          <label className="sentinelflow-settings-toggle"><input type="checkbox" checked={draft.agentEnabled} onChange={(event) => updateDraft('agentEnabled', event.target.checked)} /><span>启用 Agent Runtime</span></label>
          <label className="sentinelflow-settings-toggle"><input type="checkbox" checked={draft.alertSourceEnabled} onChange={(event) => updateDraft('alertSourceEnabled', event.target.checked)} /><span>启用告警接入</span></label>
          <label className="sentinelflow-settings-toggle"><input type="checkbox" checked={draft.weeklyAlertCleanupEnabled} onChange={(event) => updateDraft('weeklyAlertCleanupEnabled', event.target.checked)} /><span>每周刷新告警</span></label>
          <label className="sentinelflow-settings-field">
            <span>完整报告格式 Skill</span>
            <select className="sentinelflow-settings-input" value={draft.fullReportFormatSkill} onChange={(event) => updateDraft('fullReportFormatSkill', event.target.value)}>
              {draft.fullReportFormatSkill && !docSkillOptions.some((skill) => skill.name === draft.fullReportFormatSkill) ? (
                <option value={draft.fullReportFormatSkill}>{draft.fullReportFormatSkill}</option>
              ) : null}
              {docSkillOptions.length ? null : <option value={draft.fullReportFormatSkill || 'output-report'}>{draft.fullReportFormatSkill || 'output-report'}</option>}
              {docSkillOptions.map((skill) => (
                <option key={skill.name} value={skill.name}>{skill.name}</option>
              ))}
            </select>
          </label>
        </div>
        <div className="mt-3 sentinelflow-message-block">
          开启后，系统会在每周一 01:00 删除本周一 00:00 之前的全部告警任务（含未完成/失败），并清理关联的去重锁、审批与 checkpoint；周一 00:00 到 01:00 的新告警会保留。运行日志仍按「运行日志保留天数」单独清理。
        </div>
        <div className="mt-6 rounded-2xl border border-gray-200 bg-gray-50 p-5">
          <div className="mb-4">
            <h3 className="text-lg font-semibold text-gray-900">告警接入配置</h3>
            <p className="mt-1 text-sm text-gray-600">支持直接请求上游接口，或者在页面里粘贴 Python 脚本并输出标准告警 JSON。</p>
          </div>
          <div className="mb-4 rounded-xl border border-gray-200 bg-white p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <div className="text-sm font-semibold text-gray-900">告警源</div>
              <div className="flex gap-2">
                <button type="button" className="sentinelflow-ghost-button" onClick={addSource}>新增告警源</button>
                <button type="button" className="sentinelflow-ghost-button" onClick={deleteSelectedSource} disabled={draft.alertSources.length <= 1}>删除当前源</button>
              </div>
            </div>
            <div className="sentinelflow-quick-actions mb-4">
              {draft.alertSources.map((source) => (
                <button
                  key={source.id}
                  type="button"
                  className={`sentinelflow-chip-button${source.id === draft.selectedSourceId ? ' sentinelflow-chip-button-active' : ''}`}
                  onClick={() => selectSource(source.id)}
                >
                  {source.name || source.id}
                </button>
              ))}
            </div>
            <div className="sentinelflow-settings-form">
              <label className="sentinelflow-settings-field"><span>告警源名称</span><input className="sentinelflow-settings-input" value={draft.alertSourceName} onChange={(event) => updateDraft('alertSourceName', event.target.value)} /></label>
              <label className="sentinelflow-settings-toggle"><input type="checkbox" checked={draft.alertSourceEnabled} onChange={(event) => updateDraft('alertSourceEnabled', event.target.checked)} /><span>启用当前告警源</span></label>
            </div>
          </div>
          <div className="sentinelflow-settings-form">
            <label className="sentinelflow-settings-field"><span>告警轮询间隔（秒）</span><input className="sentinelflow-settings-input" value={draft.pollIntervalSeconds} onChange={(event) => updateDraft('pollIntervalSeconds', event.target.value)} /></label>
            <label className="sentinelflow-settings-field"><span>处置失败重试间隔（秒）</span><input className="sentinelflow-settings-input" value={draft.failedRetryIntervalSeconds} onChange={(event) => updateDraft('failedRetryIntervalSeconds', event.target.value)} placeholder="0 表示关闭自动重试" /></label>
            {canEditSourceAnalysisPrompt ? (
              <label className="sentinelflow-settings-field sentinelflow-settings-field-full"><span>当前源专属告警分析 Prompt</span><textarea className="sentinelflow-settings-input min-h-[140px]" value={draft.alertSourceAnalysisPrompt} onChange={(event) => updateDraft('alertSourceAnalysisPrompt', event.target.value)} placeholder="当前告警源会优先使用这里的主 Agent 告警分析 Prompt。" /></label>
            ) : (
              <div className="sentinelflow-settings-field sentinelflow-settings-field-full">
                <span>当前源专属告警分析 Prompt</span>
                <div className="sentinelflow-message-block">
                  第一个告警源使用 Agents 页面里主 Agent 的告警分析 Prompt；新增的第二个及后续告警源可在这里配置独立 Prompt。
                </div>
              </div>
            )}
            <label className="sentinelflow-settings-field"><span>接入方式</span><select className="sentinelflow-settings-input" value={draft.alertSourceType} onChange={(event) => updateDraft('alertSourceType', event.target.value)}><option value="api">接口接入</option><option value="script">脚本接入</option></select></label>
            {!isScriptMode ? (
              <>
                <label className="sentinelflow-settings-field sentinelflow-settings-field-full"><span>接口 URL</span><input className="sentinelflow-settings-input" value={draft.alertSourceUrl} onChange={(event) => updateDraft('alertSourceUrl', event.target.value)} placeholder="https://example.com/api/alerts" /></label>
                <label className="sentinelflow-settings-field"><span>请求方法</span><select className="sentinelflow-settings-input" value={draft.alertSourceMethod} onChange={(event) => updateDraft('alertSourceMethod', event.target.value)}><option value="GET">GET</option><option value="POST">POST</option></select></label>
                <label className="sentinelflow-settings-field"><span>接口超时（秒）</span><input className="sentinelflow-settings-input" value={draft.alertSourceTimeout} onChange={(event) => updateDraft('alertSourceTimeout', event.target.value)} /></label>
                <label className="sentinelflow-settings-field sentinelflow-settings-field-full"><span>Headers(JSON)</span><textarea className="sentinelflow-settings-input min-h-[120px]" value={draft.alertSourceHeaders} onChange={(event) => updateDraft('alertSourceHeaders', event.target.value)} /></label>
                <label className="sentinelflow-settings-field sentinelflow-settings-field-full"><span>Query(JSON)</span><textarea className="sentinelflow-settings-input min-h-[120px]" value={draft.alertSourceQuery} onChange={(event) => updateDraft('alertSourceQuery', event.target.value)} /></label>
                <label className="sentinelflow-settings-field sentinelflow-settings-field-full"><span>Body(JSON 或文本)</span><textarea className="sentinelflow-settings-input min-h-[120px]" value={draft.alertSourceBody} onChange={(event) => updateDraft('alertSourceBody', event.target.value)} /></label>
              </>
            ) : (
              <>
                <label className="sentinelflow-settings-field"><span>脚本超时（秒）</span><input className="sentinelflow-settings-input" value={draft.alertScriptTimeout} onChange={(event) => updateDraft('alertScriptTimeout', event.target.value)} /></label>
                <label className="sentinelflow-settings-field sentinelflow-settings-field-full">
                  <span>Python 脚本</span>
                  <textarea className="sentinelflow-settings-input min-h-[320px] font-mono text-xs" value={draft.alertScriptCode} onChange={(event) => updateDraft('alertScriptCode', event.target.value)} placeholder={'import json\n\nprint(json.dumps({"count": 0, "alerts": []}, ensure_ascii=False))'} />
                </label>
              </>
            )}
          </div>
          {fetchMessage ? <div className={`mt-4 sentinelflow-message-block ${fetchMessageTone === 'success' ? 'sentinelflow-message-success' : 'sentinelflow-message-error'}`}>{fetchMessage}</div> : null}
          {fetchPreview ? (
            <div className="mt-4 rounded-xl border border-gray-200 bg-white p-4">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-sm font-semibold text-gray-900">{isScriptMode ? '脚本输出预览' : '原始接口响应预览'}</span>
                {isLongPreview ? (
                  <button type="button" className="text-xs font-medium text-sky-600 hover:text-sky-800" onClick={() => setFetchPreviewExpanded(!fetchPreviewExpanded)}>
                    {fetchPreviewExpanded ? '收起' : '展开全文'}
                  </button>
                ) : null}
              </div>
              <pre className="overflow-x-auto whitespace-pre-wrap text-xs text-gray-700">
                {fetchPreviewExpanded || !isLongPreview ? fetchPreviewStr : fetchPreviewLines.slice(0, 20).join('\n') + '\n...'}
              </pre>
            </div>
          ) : null}
          <div className="mt-4 flex flex-wrap gap-2">
            <button type="button" className="flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2 text-gray-700 transition-colors hover:bg-gray-100" onClick={() => void handleTestFetch()} disabled={testingFetch}>
              {testingFetch ? '测试中...' : isScriptMode ? '测试脚本执行' : '测试接口拉取'}
            </button>
          </div>
        </div>
        {!isScriptMode ? <div className="mt-6 rounded-2xl border border-gray-200 bg-white p-5">
          <div className="mb-4">
            <h3 className="text-lg font-semibold text-gray-900">告警解析规则配置</h3>
            <p className="mt-1 text-sm text-gray-600">把接口返回的告警样本粘贴进来，点击自动解析后，平台会调用大模型生成一套可复用的解析规则。</p>
          </div>
          <label className="sentinelflow-settings-field sentinelflow-settings-field-full">
            <span>告警样本(JSON)</span>
            <textarea className="sentinelflow-settings-input min-h-[220px]" value={draft.alertSourceSamplePayload} onChange={(event) => updateDraft('alertSourceSamplePayload', event.target.value)} placeholder='{"data":{"records":[...]}}' />
          </label>
          <div className="mt-4 flex flex-wrap gap-2">
            <button type="button" className="flex items-center gap-2 rounded-lg border border-sky-300 bg-sky-50 px-4 py-2 text-sky-700 transition-colors hover:bg-sky-100" onClick={() => void handleGenerateParser()} disabled={generatingParser}>
              {generatingParser ? '分析中...' : '自动解析告警格式'}
            </button>
            <input type="file" accept=".json" className="hidden" ref={fileInputRef} onChange={handleImportRuleFromFile} />
            <button type="button" className="flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2 text-gray-700 transition-colors hover:bg-gray-50" onClick={() => fileInputRef.current?.click()}>
              导入已有规则
            </button>
            <button type="button" className="flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2 text-gray-700 transition-colors hover:bg-gray-50" onClick={() => void handleTestParser()} disabled={testingParse}>
              {testingParse ? '测试中...' : '测试解析结果'}
            </button>
            <button type="button" className="flex items-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-white transition-colors hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-60" onClick={() => void handleSaveParserRule()} disabled={saving}>
              {saving ? '保存中...' : '保存解析规则'}
            </button>
          </div>
          {parserSaveMessage ? <div className={`mt-4 sentinelflow-message-block ${parserSaveTone === 'success' ? 'sentinelflow-message-success' : 'sentinelflow-message-error'}`}>{parserSaveMessage}</div> : null}
          {parserMessage ? <div className="mt-4 sentinelflow-message-block">{parserMessage}</div> : null}
          {parserWarnings.length ? (
            <div className="mt-4 rounded-2xl border border-amber-300 bg-amber-50 px-4 py-4 text-amber-900">
              <div className="mb-2 text-sm font-semibold">解析风险提醒</div>
              <div className="mb-3 text-sm leading-6">
                当前解析规则可以跑通，但存在会影响去重或稳定性的风险。尤其是 `eventIds` 使用 fallback 生成时，可能导致重复建单。
              </div>
              <ul className="list-disc space-y-1 pl-5 text-sm leading-6">
                {parserWarnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </div>
          ) : null}
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
              <div className="mb-2 text-sm font-semibold text-gray-900">当前解析规则</div>
              <pre className="overflow-x-auto whitespace-pre-wrap text-xs text-gray-700">{JSON.stringify(draft.alertParserRule, null, 2)}</pre>
            </div>
            <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
              <div className="mb-2 text-sm font-semibold text-gray-900">解析结果预览</div>
              <pre className="overflow-x-auto whitespace-pre-wrap text-xs text-gray-700">{JSON.stringify(parserPreview, null, 2)}</pre>
            </div>
          </div>
        </div> : null}
        {settings && !settings.llm.agent_available ? <div className="sentinelflow-message-block sentinelflow-message-error">当前环境缺少 LangGraph/LLM 依赖：{settings.llm.agent_unavailable_reason || '未检测到相关依赖'}</div> : null}
      </Surface>
      <div className="flex justify-end">
        <button
          type="button"
          className={`inline-flex items-center gap-2 rounded-lg border px-4 py-2 text-sm transition-colors ${
            debugLogUnlocked
              ? 'border-slate-300 bg-slate-900 text-white hover:bg-slate-800'
              : 'border-slate-200 bg-white text-slate-500 hover:bg-slate-50'
          }`}
          onClick={handleDebugLogButtonClick}
          title={debugLogUnlocked ? '打开专业调试日志面板' : `连续点击 ${DEBUG_LOG_UNLOCK_CLICKS} 次解锁专业调试日志`}
        >
          <Bug className="h-4 w-4" />
          查看详细运行日志
          {!debugLogUnlocked && debugLogClickCount > 0 ? (
            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
              {`${DEBUG_LOG_UNLOCK_CLICKS - debugLogClickCount} 次后解锁`}
            </span>
          ) : null}
        </button>
      </div>
      {debugLogOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center overflow-hidden bg-slate-950/60 p-2 sm:p-3 overscroll-contain">
          <div className="flex h-[92vh] w-[96vw] max-w-[1600px] flex-col overflow-hidden rounded-2xl border border-slate-700 bg-slate-950 shadow-2xl">
            <div className="flex items-start justify-between gap-4 border-b border-slate-800 px-5 py-4">
              <div>
                <div className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-slate-300">
                  <Bug className="h-4 w-4" />
                  详细运行日志
                </div>
                <p className="mt-1 text-sm text-slate-400">
                  按日期和告警查看从接收告警到结束的 Agent 消息、工具调用、Worker、Workflow、执行轨迹和最终结果。
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <button type="button" className="rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:bg-slate-900" onClick={() => void openDetailedRuntimeLog()} disabled={debugLogLoading}>
                  {debugLogLoading ? '刷新中...' : '刷新'}
                </button>
                <button type="button" className="rounded-lg border border-slate-700 p-2 text-slate-200 hover:bg-slate-900" onClick={() => setDebugLogOpen(false)}>
                  <X className="h-4 w-4" />
                </button>
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto overflow-x-auto p-5">
              {debugLogError ? <div className="mb-4 rounded-xl border border-red-800 bg-red-950/60 p-4 text-sm text-red-100">{debugLogError}</div> : null}
              <div className="mb-4 flex flex-wrap items-end gap-3 rounded-xl border border-slate-800 bg-slate-900 p-4">
                <label className="flex flex-col gap-1 text-sm text-slate-300">
                  <span>日志保留天数</span>
                  <input
                    className="w-32 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
                    inputMode="numeric"
                    placeholder="1"
                    value={draft.runLogRetentionDays}
                    onChange={(event) => updateDraft('runLogRetentionDays', event.target.value)}
                  />
                </label>
                <button
                  type="button"
                  className="rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800"
                  onClick={() => void handleSaveRunLogRetention()}
                  disabled={savingRunLogRetention}
                >
                  {savingRunLogRetention ? '保存中...' : '保存保留策略'}
                </button>
                <div className="text-xs leading-5 text-slate-500">
                  当前生效：保留 {activeRunLogRetentionDays} 天。默认保留 1 天。保留多天时，下方按日期分组；每个日期内按告警选择运行日志，详情默认读取最近 500 个事件。
                </div>
              </div>
              {debugLogLoading && !debugLogDetail ? <div className="rounded-xl border border-slate-800 bg-slate-900 p-4 text-sm text-slate-300">正在读取详细运行日志...</div> : null}
              <div className="grid min-h-[560px] gap-4 lg:grid-cols-[180px_320px_minmax(0,1fr)]">
                <div className="rounded-xl border border-slate-800 bg-slate-900 p-3">
                  <div className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">日期</div>
                  <div className="space-y-2">
                    {debugLogDates.map((item) => (
                      <button
                        key={item.date}
                        type="button"
                        className={`w-full rounded-lg px-3 py-2 text-left text-sm transition-colors ${item.date === selectedDebugDate ? 'bg-slate-700 text-white' : 'text-slate-300 hover:bg-slate-800'}`}
                        onClick={() => void selectDebugDate(item.date)}
                      >
                        <div className="font-medium">{item.date}</div>
                        <div className="text-xs text-slate-500">{item.count} 条告警</div>
                      </button>
                    ))}
                    {!debugLogDates.length ? <div className="text-sm text-slate-500">暂无日志</div> : null}
                  </div>
                </div>
                <div className="rounded-xl border border-slate-800 bg-slate-900 p-3">
                  <div className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">告警</div>
                  <div className="max-h-[520px] space-y-2 overflow-auto pr-1">
                    {debugLogAlerts.map((alert) => (
                      <button
                        key={alert.log_id}
                        type="button"
                        className={`w-full rounded-lg px-3 py-2 text-left transition-colors ${alert.log_id === selectedDebugLogId ? 'bg-slate-700 text-white' : 'text-slate-300 hover:bg-slate-800'}`}
                        onClick={() => void selectDebugLog(selectedDebugDate, alert.log_id)}
                      >
                        <div className="text-sm font-medium">{alert.event_ids || alert.task_id || alert.log_id}</div>
                        <div className="mt-1 line-clamp-2 text-xs text-slate-400">{alert.title}</div>
                        <div className="mt-1 text-xs text-slate-500">{alert.event_count} 个事件 · {alert.updated_at}</div>
                      </button>
                    ))}
                    {selectedDebugDate && !debugLogAlerts.length ? <div className="text-sm text-slate-500">该日期暂无告警日志</div> : null}
                  </div>
                </div>
                <div className="min-w-0 rounded-xl border border-slate-800 bg-slate-900 p-3">
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <div>
                      <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">模型数据流转记录</div>
                      <div className="mt-1 text-sm text-slate-300">{debugLogDetail?.metadata?.title as string || selectedDebugLogId || '未选择告警'}</div>
                    </div>
                    <div className="text-xs text-slate-500">
                      {debugLogDetail?.truncated
                        ? `${debugLogDetail.returned_events ?? debugLogDetail.events.length}/${debugLogDetail.total_events ?? debugLogDetail.events.length} 个事件`
                        : `${debugLogDetail?.events?.length ?? 0} 个事件`}
                    </div>
                  </div>
                  <div className="max-h-[520px] space-y-3 overflow-y-auto overflow-x-auto pr-1">
                    {visibleDebugLogEvents
                      .map((event, index) => {
                        const eventId = runLogEventDomId(event, index)
                        const isHighlighted = highlightedRunLogEventId === eventId
                        return (
                      <details
                        key={eventId}
                        id={eventId}
                        ref={(node) => {
                          runLogEventRefs.current[eventId] = node
                        }}
                        className={`rounded-lg border bg-slate-950 p-3 transition-shadow ${
                          isHighlighted ? 'border-indigo-500/70 ring-2 ring-indigo-400/80' : 'border-slate-800'
                        }`}
                        open={index < 3}
                      >
                        <summary className="cursor-pointer list-none">
                          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                            <span>#{typeof event.seq === 'number' ? event.seq : index + 1}</span>
                            <span>{event.ts}</span>
                            <span className="rounded-full bg-slate-800 px-2 py-0.5 text-slate-300">{event.phase}</span>
                            {event.phase === 'react_trace' && event.data && typeof event.data === 'object' ? (
                              <span className="rounded-full bg-indigo-900/60 px-2 py-0.5 text-indigo-200">
                                {String((event.data as Record<string, unknown>).event_type || 'react')}
                              </span>
                            ) : null}
                            <span className={event.level === 'error' ? 'text-red-300' : event.level === 'warn' ? 'text-amber-300' : 'text-slate-400'}>{event.level}</span>
                          </div>
                          <div className="mt-1 text-sm font-medium text-slate-100">{event.title}</div>
                          <RunLogEventSummary
                            event={event}
                            events={debugLogDetail?.events ?? []}
                            onJumpToPrompt={jumpToRunLogPrompt}
                          />
                        </summary>
                        <div className="mt-3 min-w-0 overflow-x-auto rounded-lg border border-slate-800 bg-slate-900 p-3 text-slate-100">
                          <JsonPreview value={event} />
                        </div>
                      </details>
                        )
                      })}
                    {visibleDebugLogEvents.length < sortedDebugLogEvents.length ? (
                      <button
                        type="button"
                        className="w-full rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800"
                        onClick={() => setVisibleRunLogEventCount((current) => current + RUN_LOG_RENDER_INCREMENT)}
                      >
                        加载更多事件（{visibleDebugLogEvents.length}/{sortedDebugLogEvents.length}）
                      </button>
                    ) : null}
                    {debugLogDetail && !debugLogDetail.events.length ? <div className="text-sm text-slate-500">该告警日志为空</div> : null}
                    {!debugLogDetail && !debugLogLoading ? <div className="text-sm text-slate-500">请选择左侧日期和告警。</div> : null}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}
