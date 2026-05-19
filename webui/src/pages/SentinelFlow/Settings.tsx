import { useEffect, useState, useRef, type ChangeEvent } from 'react'
import { Bug, RotateCcw, Save, Settings as SettingsIcon, X } from 'lucide-react'
import {
  fetchHealth,
  fetchRunLogAlerts,
  fetchRunLogDates,
  fetchRunLogDetail,
  fetchRuntimeSettings,
  generateAlertParser,
  resetRuntimeSettings,
  saveRunLogSettings,
  saveRuntimeSettings,
  testAlertParser,
  testAlertSourceFetch,
  type RunLogAlertSummary,
  type RunLogDateSummary,
  type RunLogDetail,
  type RuntimeSettingsResponse,
} from '@/api/sentinelflow'
import JsonPreview from '@/components/sentinelflow/JsonPreview'
import KeyValueList from '@/components/sentinelflow/KeyValueList'
import StatusBadge from '@/components/sentinelflow/StatusBadge'
import Surface from '@/components/sentinelflow/Surface'
import PageHeader from '@/components/common/PageHeader'
import { brand, withProductName } from '@/config/brand'
import { useSentinelFlowAsyncData } from '@/hooks/useSentinelFlowAsyncData'
import { readSessionValue, writeSessionValue } from '@/utils/sentinelflowLocalState'

const SETTINGS_DRAFT_KEY = 'sentinelflow:settings:draft'
const DEBUG_LOG_UNLOCK_CLICKS = 5

type SettingsDraft = {
  pollIntervalSeconds: string
  failedRetryIntervalSeconds: string
  agentEnabled: boolean
  llmApiBaseUrl: string
  llmApiKey: string
  llmModel: string
  llmTemperature: string
  llmTimeout: string
  weeklyAlertCleanupEnabled: boolean
  runLogRetentionDays: string
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
    weeklyAlertCleanupEnabled: settings.runtime.weekly_alert_cleanup_enabled,
    runLogRetentionDays: String(settings.runtime.run_log_retention_days ?? 1),
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

export default function SentinelFlowSettingsPage() {
  const { data: settings, loading, error, reload: reloadSettings, setData: setSettings } = useSentinelFlowAsyncData(fetchRuntimeSettings, [])
  const { data: health } = useSentinelFlowAsyncData(fetchHealth, [])
  const [saving, setSaving] = useState(false)
  const [saveMessage, setSaveMessage] = useState<string | null>(null)
  const [saveMessageTone, setSaveMessageTone] = useState<'success' | 'error'>('success')
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
      weeklyAlertCleanupEnabled: false,
      runLogRetentionDays: '1',
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
  const [activeRunLogRetentionDays, setActiveRunLogRetentionDays] = useState<number>(1)
  const [savingRunLogRetention, setSavingRunLogRetention] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const fetchPreviewStr = fetchPreview ? JSON.stringify(fetchPreview, null, 2) : ''
  const fetchPreviewLines = fetchPreviewStr.split('\n')
  const isLongPreview = fetchPreviewLines.length > 20
  const isScriptMode = draft.alertSourceType === 'script'
  const selectedSourceIndex = Math.max(0, draft.alertSources.findIndex((source) => source.id === draft.selectedSourceId))
  const canEditSourceAnalysisPrompt = selectedSourceIndex > 0

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

  useEffect(() => {
    if (!settings) return
    setDraft(buildDraft(settings))
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
    setDraft((current) => {
      const source = current.alertSources.find((item) => item.id === sourceId) ?? current.alertSources[0]
      if (!source) return current
      return {
        ...current,
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

  function addSource() {
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
      setDraft(buildDraft(saved))
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
      setDraft(buildDraft(saved))
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
      setDraft(buildDraft(reset))
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
    setDebugLogOpen(true)
    setDebugLogLoading(true)
    setDebugLogError(null)
    try {
      const data = await fetchRunLogDates()
      setDebugLogDates(data.dates ?? [])
      setActiveRunLogRetentionDays(data.retention_days ?? 1)
      updateDraft('runLogRetentionDays', String(data.retention_days ?? 1))
      const nextDate = selectedDebugDate || data.dates?.[0]?.date || ''
      setSelectedDebugDate(nextDate)
      if (nextDate) {
        const alertData = await fetchRunLogAlerts(nextDate)
        setDebugLogAlerts(alertData.alerts ?? [])
        const nextLogId = selectedDebugLogId || alertData.alerts?.[0]?.log_id || ''
        setSelectedDebugLogId(nextLogId)
        if (nextLogId) {
          setDebugLogDetail(await fetchRunLogDetail(nextDate, nextLogId))
        } else {
          setDebugLogDetail(null)
        }
      } else {
        setDebugLogAlerts([])
        setDebugLogDetail(null)
      }
    } catch (error) {
      setDebugLogDetail(null)
      setDebugLogError(error instanceof Error ? error.message : '读取详细运行日志失败')
    } finally {
      setDebugLogLoading(false)
    }
  }

  async function selectDebugDate(date: string) {
    setSelectedDebugDate(date)
    setSelectedDebugLogId('')
    setDebugLogDetail(null)
    setDebugLogLoading(true)
    setDebugLogError(null)
    try {
      const data = await fetchRunLogAlerts(date)
      setDebugLogAlerts(data.alerts ?? [])
      const firstLogId = data.alerts?.[0]?.log_id || ''
      setSelectedDebugLogId(firstLogId)
      if (firstLogId) setDebugLogDetail(await fetchRunLogDetail(date, firstLogId))
    } catch (error) {
      setDebugLogError(error instanceof Error ? error.message : '读取告警日志列表失败')
    } finally {
      setDebugLogLoading(false)
    }
  }

  async function selectDebugLog(date: string, logId: string) {
    setSelectedDebugLogId(logId)
    setDebugLogDetail(null)
    setDebugLogLoading(true)
    setDebugLogError(null)
    try {
      setDebugLogDetail(await fetchRunLogDetail(date, logId))
    } catch (error) {
      setDebugLogError(error instanceof Error ? error.message : '读取告警运行日志失败')
    } finally {
      setDebugLogLoading(false)
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
        description={withProductName('配置平台参数、告警接入和解析规则。')}
        icon={<SettingsIcon className="w-8 h-8" />}
      />

      <div className="grid gap-4 md:grid-cols-4">
        <div className="rounded-xl border border-gray-200 bg-white p-5">
          <div className="mb-2 text-sm font-semibold text-gray-900">运行模式</div>
          <div className="text-2xl font-bold text-gray-900">{settings?.runtime.agent_enabled ? 'Agent' : 'Basic'}</div>
        </div>
        <div className="rounded-xl border border-gray-200 bg-white p-5">
          <div className="mb-2 text-sm font-semibold text-gray-900">轮询周期</div>
          <div className="text-2xl font-bold text-gray-900">{settings?.runtime.poll_interval_seconds ?? '--'}</div>
        </div>
        <div className="rounded-xl border border-gray-200 bg-white p-5">
          <div className="mb-2 text-sm font-semibold text-gray-900">API 健康</div>
          <div className="text-2xl font-bold text-gray-900">{health?.status ?? 'unknown'}</div>
        </div>
        <div className="rounded-xl border border-gray-200 bg-white p-5">
          <div className="mb-2 text-sm font-semibold text-gray-900">{settings?.alert_source.type === 'script' ? '接入方式' : '解析规则'}</div>
          <div className="text-2xl font-bold text-gray-900">
            {settings?.alert_source.type === 'script' ? '脚本' : settings?.alert_source.parser_configured ? '已配置' : '未配置'}
          </div>
        </div>
      </div>

      <Surface title="平台设置" subtitle={withProductName('统一管理平台运行参数、告警接入和解析规则。')}>
        {loading ? <p className="sentinelflow-muted-text">{withProductName('正在读取 SentinelFlow 运行配置...')}</p> : null}
        {error ? <div className="sentinelflow-message-block sentinelflow-message-error">{error}</div> : null}
        {settings ? (
          <div className="sentinelflow-grid-2">
            <div className="sentinelflow-detail-panel">
              <h3>品牌与运行时</h3>
              <KeyValueList
                items={[
                  { label: '产品名称', value: settings.branding.product_name || brand.productName },
                  { label: '控制台标题', value: settings.branding.console_title || brand.consoleTitle },
                  { label: '轮询周期', value: `${settings.runtime.poll_interval_seconds} 秒` },
                  { label: '失败重试', value: Number(settings.runtime.failed_retry_interval_seconds) > 0 ? `${settings.runtime.failed_retry_interval_seconds} 秒后自动重试` : '未启用' },
                  { label: '告警接入', value: settings.alert_source.enabled ? `已启用 · ${settings.alert_source.type === 'script' ? '脚本' : '接口'}` : '未启用' },
                ]}
              />
            </div>
            <div className="sentinelflow-detail-panel">
              <h3>平台能力状态</h3>
              <div className="sentinelflow-stack-list">
                <div className="sentinelflow-stack-item"><strong>API 健康状态</strong><div className="sentinelflow-inline-status"><StatusBadge tone={health?.status === 'ok' ? 'success' : 'danger'}>{health?.status ?? 'unknown'}</StatusBadge></div></div>
                <div className="sentinelflow-stack-item"><strong>自然语言调度</strong><div className="sentinelflow-inline-status"><StatusBadge tone={settings.features.natural_language_dispatch ? 'success' : 'neutral'}>{settings.features.natural_language_dispatch ? 'enabled' : 'disabled'}</StatusBadge></div></div>
                <div className="sentinelflow-stack-item"><strong>自动轮询告警</strong><div className="sentinelflow-inline-status"><StatusBadge tone={settings.features.alert_polling ? 'success' : 'neutral'}>{settings.features.alert_polling ? 'enabled' : 'disabled'}</StatusBadge></div></div>
                <div className="sentinelflow-stack-item"><strong>Agent Runtime</strong><div className="sentinelflow-inline-status"><StatusBadge tone={settings.llm.agent_available ? 'success' : 'warn'}>{settings.llm.agent_available ? 'available' : 'missing deps'}</StatusBadge></div></div>
              </div>
            </div>
          </div>
        ) : null}
      </Surface>

      <Surface title="配置中心" subtitle="这里统一配置平台级通用参数，以及单个告警源的接入、轮询和解析规则。">
        {saveMessage ? <div className={`mb-4 sentinelflow-message-block ${saveMessageTone === 'success' ? 'sentinelflow-message-success' : 'sentinelflow-message-error'}`}>{saveMessage}</div> : null}
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
          <label className="sentinelflow-settings-field sentinelflow-settings-field-full"><span>LLM API Key</span><input type="password" className="sentinelflow-settings-input" value={draft.llmApiKey} onChange={(event) => updateDraft('llmApiKey', event.target.value)} placeholder={settings?.llm.api_key_configured ? '已配置，可重新填写覆盖' : ''} /></label>
          <label className="sentinelflow-settings-toggle"><input type="checkbox" checked={draft.agentEnabled} onChange={(event) => updateDraft('agentEnabled', event.target.checked)} /><span>启用 Agent Runtime</span></label>
          <label className="sentinelflow-settings-toggle"><input type="checkbox" checked={draft.alertSourceEnabled} onChange={(event) => updateDraft('alertSourceEnabled', event.target.checked)} /><span>启用告警接入</span></label>
          <label className="sentinelflow-settings-toggle"><input type="checkbox" checked={draft.weeklyAlertCleanupEnabled} onChange={(event) => updateDraft('weeklyAlertCleanupEnabled', event.target.checked)} /><span>每周刷新告警</span></label>
        </div>
        <div className="mt-3 sentinelflow-message-block">
          开启后，系统会在每周一 01:00 清理本周一 00:00 之前存储的告警；周一 00:00 到 01:00 的新告警会保留。
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
        <div className="fixed inset-0 z-50 flex items-center justify-center overflow-hidden bg-slate-950/60 p-4 overscroll-contain">
          <div className="flex h-[88vh] w-full max-w-6xl flex-col overflow-hidden rounded-2xl border border-slate-700 bg-slate-950 shadow-2xl">
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
            <div className="min-h-0 flex-1 overflow-auto p-5">
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
                  当前生效：保留 {activeRunLogRetentionDays} 天。默认保留 1 天。保留多天时，下方按日期分组；每个日期内按告警选择完整运行日志。
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
                    <div className="text-xs text-slate-500">{debugLogDetail?.events?.length ?? 0} 个事件</div>
                  </div>
                  <div className="max-h-[520px] space-y-3 overflow-auto pr-1">
                    {[...(debugLogDetail?.events ?? [])]
                      .sort((left, right) => runLogEventSeq(left) - runLogEventSeq(right))
                      .map((event, index) => (
                      <details key={`${event.ts}-${event.seq ?? index}`} className="rounded-lg border border-slate-800 bg-slate-950 p-3" open={index < 3}>
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
                          {shortEventData(event.data) ? <div className="mt-1 text-xs text-slate-400">{shortEventData(event.data)}</div> : null}
                        </summary>
                        <div className="mt-3 rounded-lg border border-slate-800 bg-slate-900 p-3 text-slate-100">
                          <JsonPreview value={event} />
                        </div>
                      </details>
                    ))}
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
