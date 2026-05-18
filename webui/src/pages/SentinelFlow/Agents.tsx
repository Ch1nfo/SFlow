import { useEffect, useMemo, useState } from 'react'
import { Bot, ChevronDown, ChevronRight, Plus, RefreshCw } from 'lucide-react'
import {
  createAgent,
  deleteAgent,
  fetchAgentDetail,
  fetchAgents,
  fetchSkills,
  saveAgent,
  type AgentDetail,
  type AgentSummary,
  type SkillSummary,
} from '@/api/sentinelflow'
import PageHeader from '@/components/common/PageHeader'
import Surface from '@/components/sentinelflow/Surface'
import StatusBadge from '@/components/sentinelflow/StatusBadge'
import { brand, withProductName } from '@/config/brand'
import { useSentinelFlowAsyncData } from '@/hooks/useSentinelFlowAsyncData'

type AgentDraft = {
  name: string
  description: string
  prompt: string
  promptCommand: string
  promptAlert: string
  promptSynthesize: string
  mode: string
  role: string
  enabled: boolean
  color: string
  useGlobalModel: boolean
  llmApiBaseUrl: string
  llmApiKey: string
  llmModel: string
  llmTemperature: string
  llmTimeout: string
  docSkillMode: string
  docSkillAllowlist: string[]
  docSkillDenylist: string[]
  hybridDocAllowlist: string[]
  execSkillAllowlist: string[]
  workerAllowlistCommand: string[]
  workerAllowlistAlert: string[]
  workerMaxSteps: string
  workerParallelLimit: string
}

const EMPTY_DRAFT: AgentDraft = {
  name: '',
  description: '',
  prompt: '',
  promptCommand: '',
  promptAlert: '',
  promptSynthesize: '',
  mode: 'subagent',
  role: 'worker',
  enabled: true,
  color: '#2563eb',
  useGlobalModel: true,
  llmApiBaseUrl: '',
  llmApiKey: '',
  llmModel: '',
  llmTemperature: '0',
  llmTimeout: '60',
  docSkillMode: 'all',
  docSkillAllowlist: [],
  docSkillDenylist: [],
  hybridDocAllowlist: [],
  execSkillAllowlist: [],
  workerAllowlistCommand: [],
  workerAllowlistAlert: [],
  workerMaxSteps: '3',
  workerParallelLimit: '3',
}

function detailToDraft(detail: AgentDetail): AgentDraft {
  return {
    name: detail.name,
    description: detail.description,
    prompt: detail.prompt,
    promptCommand: detail.prompt_command || '',
    promptAlert: detail.prompt_alert || '',
    promptSynthesize: detail.prompt_synthesize || '',
    mode: detail.mode,
    role: detail.role || (detail.mode === 'primary' ? 'primary' : 'worker'),
    enabled: detail.enabled,
    color: detail.color || '#2563eb',
    useGlobalModel: detail.use_global_model,
    llmApiBaseUrl: detail.llm_api_base_url || '',
    llmApiKey: '',
    llmModel: detail.llm_model || '',
    llmTemperature: detail.llm_temperature != null ? String(detail.llm_temperature) : '0',
    llmTimeout: detail.llm_timeout != null ? String(detail.llm_timeout) : '60',
    docSkillMode: detail.doc_skill_mode || 'all',
    docSkillAllowlist: detail.doc_skill_allowlist || [],
    docSkillDenylist: detail.doc_skill_denylist || [],
    hybridDocAllowlist: detail.hybrid_doc_allowlist || [],
    execSkillAllowlist: detail.exec_skill_allowlist || [],
    workerAllowlistCommand: detail.worker_allowlist_command || [],
    workerAllowlistAlert: detail.worker_allowlist_alert || [],
    workerMaxSteps: String(detail.worker_max_steps ?? 3),
    workerParallelLimit: String(detail.worker_parallel_limit ?? 3),
  }
}

function buildPayload(draft: AgentDraft) {
  const isPrimary = draft.role === 'primary'
  return {
    name: draft.name,
    description: draft.description,
    prompt: draft.prompt,
    promptCommand: isPrimary ? draft.promptCommand : '',
    promptAlert: isPrimary ? draft.promptAlert : '',
    promptSynthesize: isPrimary ? draft.promptSynthesize : '',
    mode: draft.mode,
    role: draft.role,
    enabled: draft.enabled,
    color: draft.color,
    skills: draft.execSkillAllowlist,
    tools: [],
    docSkillMode: draft.docSkillMode,
    docSkillAllowlist: draft.docSkillAllowlist,
    docSkillDenylist: draft.docSkillDenylist,
    hybridDocAllowlist: draft.hybridDocAllowlist,
    execSkillAllowlist: draft.execSkillAllowlist,
    workerAllowlistCommand: draft.workerAllowlistCommand,
    workerAllowlistAlert: draft.workerAllowlistAlert,
    workerMaxSteps: Number(draft.workerMaxSteps || '3'),
    workerParallelLimit: Number(draft.workerParallelLimit || '3'),
    useGlobalModel: draft.useGlobalModel,
    llmApiBaseUrl: draft.useGlobalModel ? '' : draft.llmApiBaseUrl,
    llmApiKey: draft.useGlobalModel ? undefined : (draft.llmApiKey.trim() || undefined),
    llmModel: draft.useGlobalModel ? '' : draft.llmModel,
    llmTemperature: draft.useGlobalModel ? undefined : Number(draft.llmTemperature || '0'),
    llmTimeout: draft.useGlobalModel ? undefined : Number(draft.llmTimeout || '60'),
  }
}

function toggleName(list: string[], name: string) {
  return list.includes(name) ? list.filter((item) => item !== name) : [...list, name]
}

function isPrimaryAgent(agent: Pick<AgentSummary, 'name' | 'role'> | Pick<AgentDetail, 'name' | 'role' | 'is_system'> | null | undefined) {
  if (!agent) return false
  return agent.role === 'primary' || agent.name === 'system-primary' || ('is_system' in agent && agent.is_system === true)
}

function getAgentDisplayName(agent: Pick<AgentSummary, 'name' | 'role'> | Pick<AgentDetail, 'name' | 'role' | 'is_system'> | null | undefined) {
  if (!agent) return ''
  return isPrimaryAgent(agent) ? 'Sentinel' : agent.name
}

function SkillChips({
  items,
  selected,
  onToggle,
}: {
  items: string[]
  selected: string[]
  onToggle: (name: string) => void
}) {
  return (
    <div className="sentinelflow-quick-actions">
      {items.length ? items.map((name) => (
        <button key={name} type="button" className={`sentinelflow-chip-button${selected.includes(name) ? ' sentinelflow-chip-button-active' : ''}`} onClick={() => onToggle(name)}>
          {name}
        </button>
      )) : <span className="sentinelflow-muted-text">暂无可选项</span>}
    </div>
  )
}

function SkillSummaryChips({
  title,
  items,
}: {
  title: string
  items: string[]
}) {
  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50 p-4">
      <div className="mb-3 text-sm font-semibold text-gray-900">{title}</div>
      <div className="sentinelflow-quick-actions mb-0">
        {items.length ? items.map((name) => (
          <span key={`${title}-${name}`} className="sentinelflow-chip-button">
            {name}
          </span>
        )) : <span className="sentinelflow-muted-text">未配置</span>}
      </div>
    </div>
  )
}

function AgentForm({
  title,
  draft,
  onChange,
  onSubmit,
  submitting,
  submitText,
  skills,
  agents,
  advancedPromptExpanded,
  onToggleAdvancedPrompt,
}: {
  title: string
  draft: AgentDraft
  onChange: (updater: (current: AgentDraft) => AgentDraft) => void
  onSubmit: () => void
  submitting: boolean
  submitText: string
  skills: SkillSummary[]
  agents: AgentSummary[]
  advancedPromptExpanded: boolean
  onToggleAdvancedPrompt: () => void
}) {
  const docSkills = useMemo(() => skills.filter((skill) => skill.type === 'doc').map((skill) => skill.name), [skills])
  const hybridSkills = useMemo(() => skills.filter((skill) => skill.type === 'hybrid').map((skill) => skill.name), [skills])
  const workerAgents = useMemo(
    () => agents.filter((agent) => agent.role === 'worker' && agent.name !== draft.name).map((agent) => agent.name),
    [agents, draft.name],
  )
  const roleSummary =
    draft.role === 'primary'
      ? '系统主 Agent 是唯一中控，负责接收入口任务、阅读文本知识、分派子 Agent，并统一汇总最终结论。'
      : '子 Agent 负责执行主 Agent 分派的具体任务，可按权限读取文本 Skill 或执行授权 Skill。'

  return (
    <Surface title={title} subtitle="配置主 Agent、子 Agent 和它们可用的能力范围。">
      <div className={`sentinelflow-message-block mb-3 ${draft.role === 'primary' ? 'sentinelflow-message-info' : ''}`}>
        {roleSummary}
      </div>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <input className="sentinelflow-settings-input" placeholder="Agent 名称，如 duty-primary" value={draft.name} onChange={(event) => onChange((current) => ({ ...current, name: event.target.value }))} />
        <input className="sentinelflow-settings-input" placeholder="描述" value={draft.description} onChange={(event) => onChange((current) => ({ ...current, description: event.target.value }))} />
        <input className="sentinelflow-settings-input" value={draft.role === 'primary' ? '主 Agent' : '子 Agent'} disabled />
        <select className="sentinelflow-settings-input" value={draft.enabled ? 'enabled' : 'disabled'} onChange={(event) => onChange((current) => ({ ...current, enabled: event.target.value === 'enabled' }))}>
          <option value="enabled">已启用</option>
          <option value="disabled">已停用</option>
        </select>
      </div>

      <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <input className="sentinelflow-settings-input" placeholder="颜色，例如 #2563eb" value={draft.color} onChange={(event) => onChange((current) => ({ ...current, color: event.target.value }))} />
        <select className="sentinelflow-settings-input" value={draft.useGlobalModel ? 'global' : 'custom'} onChange={(event) => onChange((current) => ({ ...current, useGlobalModel: event.target.value === 'global' }))}>
          <option value="global">使用统一模型</option>
          <option value="custom">独立配置模型</option>
        </select>
        <select className="sentinelflow-settings-input" value={draft.docSkillMode} onChange={(event) => onChange((current) => ({ ...current, docSkillMode: event.target.value }))}>
          <option value="all">纯文本 Skill 全部可读</option>
          <option value="selected">纯文本 Skill 仅白名单可读</option>
          <option value="none">纯文本 Skill 全部禁用</option>
        </select>
        {draft.role === 'primary' ? (
          <select className="sentinelflow-settings-input" value={draft.mode} onChange={(event) => onChange((current) => ({ ...current, mode: event.target.value }))}>
            <option value="primary">primary</option>
            <option value="subagent">subagent</option>
          </select>
        ) : (
          <input className="sentinelflow-settings-input" value="worker / subagent" disabled />
        )}
      </div>

      {!draft.useGlobalModel ? (
        <>
          <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <input className="sentinelflow-settings-input" placeholder="LLM API Base URL" value={draft.llmApiBaseUrl} onChange={(event) => onChange((current) => ({ ...current, llmApiBaseUrl: event.target.value }))} />
            <input className="sentinelflow-settings-input" placeholder="LLM 模型名" value={draft.llmModel} onChange={(event) => onChange((current) => ({ ...current, llmModel: event.target.value }))} />
            <input className="sentinelflow-settings-input" placeholder="LLM API Key" value={draft.llmApiKey} onChange={(event) => onChange((current) => ({ ...current, llmApiKey: event.target.value }))} />
            <input className="sentinelflow-settings-input" placeholder="温度，例如 0" value={draft.llmTemperature} onChange={(event) => onChange((current) => ({ ...current, llmTemperature: event.target.value }))} />
          </div>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <input className="sentinelflow-settings-input" placeholder="超时（秒），例如 60" value={draft.llmTimeout} onChange={(event) => onChange((current) => ({ ...current, llmTimeout: event.target.value }))} />
          </div>
        </>
      ) : null}

      <textarea className="sentinelflow-command-input mt-3" rows={7} placeholder="基础 Agent Prompt" value={draft.prompt} onChange={(event) => onChange((current) => ({ ...current, prompt: event.target.value }))} />

      {draft.role === 'primary' ? (
        <div className="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-gray-900">高级 Prompt 配置（可选）</div>
              <div className="mt-1 text-sm leading-6 text-gray-500">
                默认收起；不填写时自动回退到基础 Prompt。用于单独配置主 Agent 的对话、告警、Workflow 和汇总模式。
              </div>
            </div>
            <button type="button" className="sentinelflow-ghost-button" onClick={onToggleAdvancedPrompt}>
              {advancedPromptExpanded ? '收起' : '展开'}
            </button>
          </div>
          {advancedPromptExpanded ? (
            <div className="mt-3 grid gap-3">
              <textarea className="sentinelflow-command-input" rows={4} placeholder="对话使用 Prompt（可选）" value={draft.promptCommand} onChange={(event) => onChange((current) => ({ ...current, promptCommand: event.target.value }))} />
              <textarea className="sentinelflow-command-input" rows={4} placeholder="告警分析 Prompt（可选）" value={draft.promptAlert} onChange={(event) => onChange((current) => ({ ...current, promptAlert: event.target.value }))} />
              <textarea className="sentinelflow-command-input" rows={4} placeholder="汇总 Prompt（可选）" value={draft.promptSynthesize} onChange={(event) => onChange((current) => ({ ...current, promptSynthesize: event.target.value }))} />
            </div>
          ) : null}
        </div>
      ) : null}

      {draft.docSkillMode === 'selected' ? (
        <div className="mt-4">
          <div className="mb-2 text-sm font-semibold text-gray-900">纯文本 Skill 白名单</div>
          <SkillChips items={docSkills} selected={draft.docSkillAllowlist} onToggle={(name) => onChange((current) => ({ ...current, docSkillAllowlist: toggleName(current.docSkillAllowlist, name) }))} />
        </div>
      ) : null}

      <div className="mt-4">
        <div className="mb-2 text-sm font-semibold text-gray-900">纯文本 Skill 禁用名单</div>
        <SkillChips items={docSkills} selected={draft.docSkillDenylist} onToggle={(name) => onChange((current) => ({ ...current, docSkillDenylist: toggleName(current.docSkillDenylist, name) }))} />
      </div>

      <div className="mt-4">
        <div className="mb-2 text-sm font-semibold text-gray-900">可读取的“文本 + 可执行”Skill 文档</div>
        <SkillChips items={hybridSkills} selected={draft.hybridDocAllowlist} onToggle={(name) => onChange((current) => ({ ...current, hybridDocAllowlist: toggleName(current.hybridDocAllowlist, name) }))} />
      </div>

      <div className="mt-4">
        <div className="mb-2 text-sm font-semibold text-gray-900">可执行的“文本 + 可执行”Skill</div>
        <SkillChips items={hybridSkills} selected={draft.execSkillAllowlist} onToggle={(name) => onChange((current) => ({ ...current, execSkillAllowlist: toggleName(current.execSkillAllowlist, name) }))} />
      </div>

      {draft.role === 'primary' ? (
        <div className="mt-4">
          <div className="mb-2 text-sm font-semibold text-gray-900">对话模式允许调度的子 Agent</div>
          <SkillChips items={workerAgents} selected={draft.workerAllowlistCommand} onToggle={(name) => onChange((current) => ({ ...current, workerAllowlistCommand: toggleName(current.workerAllowlistCommand, name) }))} />
          <div className="mt-2 text-sm leading-6 text-gray-500">
            用于自然语言对话、人工指令等对话入口；如果一个都不选，对话模式下主 Agent 不会委派任何子 Agent。
          </div>
        </div>
      ) : null}

      {draft.role === 'primary' ? (
        <div className="mt-4">
          <div className="mb-2 text-sm font-semibold text-gray-900">告警分析模式允许调度的子 Agent</div>
          <SkillChips items={workerAgents} selected={draft.workerAllowlistAlert} onToggle={(name) => onChange((current) => ({ ...current, workerAllowlistAlert: toggleName(current.workerAllowlistAlert, name) }))} />
          <div className="mt-2 text-sm leading-6 text-gray-500">
            用于轮询告警、单条告警处置等告警入口；如果一个都不选，告警分析模式下主 Agent 不会委派任何子 Agent。
          </div>
        </div>
      ) : null}

      {draft.role === 'primary' ? (
        <div className="mt-4">
          <div className="mb-2 text-sm font-semibold text-gray-900">子 Agent 调用深度</div>
          <input
            className="sentinelflow-settings-input"
            placeholder="子 Agent 调用深度，默认 3"
            value={draft.workerMaxSteps}
            onChange={(event) => onChange((current) => ({ ...current, workerMaxSteps: event.target.value }))}
          />
          <div className="mt-3 mb-2 text-sm font-semibold text-gray-900">单次并行调度上限</div>
          <input
            className="sentinelflow-settings-input"
            placeholder="单次并行调度上限，默认 3"
            value={draft.workerParallelLimit}
            onChange={(event) => onChange((current) => ({ ...current, workerParallelLimit: event.target.value }))}
          />
          <div className="mt-2 text-sm leading-6 text-gray-500">
            一次并行委派会同时调用多个子 Agent，但整体只算一层“子 Agent 调用深度”。这里用于限制单层最多并发多少个子 Agent。
          </div>
        </div>
      ) : null}

      <div className="mt-4">
        <button type="button" className="sentinelflow-primary-button" onClick={onSubmit} disabled={submitting}>
          <Plus className="mr-2 inline h-4 w-4" />
          {submitting ? '提交中...' : submitText}
        </button>
      </div>
    </Surface>
  )
}

export default function SentinelFlowAgentsPage() {
  const { data, loading, error, reload } = useSentinelFlowAsyncData(fetchAgents, [])
  const { data: skillData } = useSentinelFlowAsyncData(fetchSkills, [])
  const [creating, setCreating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [selected, setSelected] = useState<AgentSummary | null>(null)
  const [detail, setDetail] = useState<AgentDetail | null>(null)
  const [draft, setDraft] = useState<AgentDraft>(EMPTY_DRAFT)
  const [editDraft, setEditDraft] = useState<AgentDraft>(EMPTY_DRAFT)
  const [editing, setEditing] = useState(false)
  const [createFormExpanded, setCreateFormExpanded] = useState(false)
  const [promptExpanded, setPromptExpanded] = useState(false)
  const [advancedPromptExpanded, setAdvancedPromptExpanded] = useState(false)
  const [formError, setFormError] = useState('')

  useEffect(() => {
    setSelected((current) => current ?? data?.agents?.[0] ?? null)
  }, [data])

  useEffect(() => {
    if (!selected) {
      setDetail(null)
      setEditing(false)
      return
    }
    void fetchAgentDetail(selected.name).then((agent) => {
      setDetail(agent)
      setEditDraft(detailToDraft(agent))
      setEditing(false)
      setPromptExpanded(false)
      setAdvancedPromptExpanded(false)
    })
  }, [selected])

  const agents = data?.agents ?? []
  const skills = skillData?.skills ?? []
  const primaryAgent = useMemo(() => agents.find((agent) => agent.role === 'primary' && agent.enabled !== false) ?? null, [agents])
  const workerAgents = useMemo(() => agents.filter((agent) => agent.role !== 'primary'), [agents])
  const selectedIsSystem = detail?.is_system === true
  const topDraft = editing ? editDraft : draft
  const editingAgentName = editing ? editDraft.name : null

  async function handleCreate() {
    if (!draft.name.trim() || !draft.prompt.trim()) return
    setCreating(true)
    setFormError('')
    try {
      await createAgent(buildPayload(draft))
      await reload()
      setDraft(EMPTY_DRAFT)
      setAdvancedPromptExpanded(false)
      setCreateFormExpanded(false)
    } catch (error) {
      setFormError(error instanceof Error ? error.message : '创建 Agent 失败。')
    } finally {
      setCreating(false)
    }
  }

  async function handleSave() {
    if (!selected || !editDraft.prompt.trim()) return
    setSaving(true)
    setFormError('')
    try {
      const saved = await saveAgent(selected.name, buildPayload(editDraft))
      setDetail(saved)
      setEditDraft(detailToDraft(saved))
      await reload()
      setSelected((current) => {
        if (!current) return current
        return {
          ...current,
          name: saved.name,
          description: saved.description,
          mode: saved.mode,
          role: saved.role,
          enabled: saved.enabled,
          location: saved.location,
          has_prompt: saved.has_prompt,
          use_global_model: saved.use_global_model,
          has_model_override: !saved.use_global_model,
          is_system: saved.is_system,
        }
      })
      setEditing(false)
      setCreateFormExpanded(false)
      setAdvancedPromptExpanded(false)
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch (error) {
      setFormError(error instanceof Error ? error.message : '保存 Agent 失败。')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete() {
    if (!selected || deleting) return
    const confirmed = window.confirm(`确认删除 Agent “${selected.name}”吗？`)
    if (!confirmed) return
    setDeleting(true)
    try {
      await deleteAgent(selected.name)
      await reload()
      setSelected(null)
      setDetail(null)
      setEditDraft(EMPTY_DRAFT)
      setCreateFormExpanded(false)
    } finally {
      setDeleting(false)
    }
  }

  function handleStartEdit() {
    if (!detail) return
    setEditDraft(detailToDraft(detail))
    setEditing(true)
    setCreateFormExpanded(true)
    setFormError('')
    setAdvancedPromptExpanded(false)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  function handleCancelEdit() {
    if (!detail) return
    setEditDraft(detailToDraft(detail))
    setEditing(false)
    setCreateFormExpanded(false)
    setFormError('')
  }

  return (
    <div className="sentinelflow-page-stack">
      <PageHeader
        title={`${brand.productName} Agents`}
        description={withProductName('管理主 Agent、子 Agent 和技能权限。')}
        icon={<Bot className="w-8 h-8" />}
        action={
          <button type="button" onClick={() => void reload()} className="flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2 text-gray-700 transition-colors hover:bg-gray-50">
            <RefreshCw className="w-4 h-4" />
            刷新
          </button>
        }
      />

      <div className="rounded-xl border border-gray-200 bg-white p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-sm font-semibold text-gray-900">
              {editing
                ? (selectedIsSystem ? `编辑系统主 Agent：${getAgentDisplayName(detail)}` : `编辑 Agent：${editDraft.name}`)
                : '新建子 Agent'}
            </div>
            <div className="mt-1 text-sm text-gray-500">
              {editing
                ? '当前正在编辑已有 Agent。保存后会直接覆盖当前 Agent 配置；如果修改名称，系统会一并完成重命名。'
                : '默认隐藏新建表单；系统主 Agent 请在下方详情里点“开始编辑”后修改。'}
            </div>
          </div>
          {editing ? (
            <button type="button" className="sentinelflow-ghost-button" onClick={handleCancelEdit} disabled={saving}>
              取消编辑
            </button>
          ) : (
            <button type="button" className="sentinelflow-primary-button" onClick={() => setCreateFormExpanded((current) => !current)}>
              {createFormExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
              {createFormExpanded ? '收起新建' : '新建子 Agent'}
            </button>
          )}
        </div>

        {editing ? (
          <div className="sentinelflow-message-block sentinelflow-message-info mb-3">
            {selectedIsSystem
              ? '你当前正在编辑系统唯一主 Agent。它负责接收所有入口任务并统一调度子 Agent。'
              : '你当前正在编辑子 Agent。它只会在被主 Agent 分派任务时工作。'}
          </div>
        ) : null}

        {editing || createFormExpanded ? (
          <AgentForm
            title={editing ? 'Agent 编辑表单' : 'Agent 新建表单'}
            draft={topDraft}
            onChange={(updater) => editing ? setEditDraft(updater) : setDraft(updater)}
            onSubmit={() => editing ? void handleSave() : void handleCreate()}
            submitting={editing ? saving : creating}
            submitText={editing ? (selectedIsSystem ? '保存主 Agent' : '保存更改') : '新建子 Agent'}
            skills={skills}
            agents={agents}
            advancedPromptExpanded={advancedPromptExpanded}
            onToggleAdvancedPrompt={() => setAdvancedPromptExpanded((current) => !current)}
          />
        ) : null}
      </div>
      {formError ? <div className="sentinelflow-message-block sentinelflow-message-error">{formError}</div> : null}

      <Surface title="Agent 列表" subtitle="当前展示已创建的项目级 Agent 定义。">
        {loading ? <p className="sentinelflow-muted-text">正在读取 Agent 列表...</p> : null}
        {error ? <div className="sentinelflow-message-block sentinelflow-message-error">{error}</div> : null}
        {!loading && !error ? (
          <div className="sentinelflow-independent-scroll-grid">
            <div className="sentinelflow-detail-panel sentinelflow-independent-scroll-panel">
              <div className="sentinelflow-task-list">
                <div className="mb-2 text-sm font-semibold text-gray-900">系统主 Agent</div>
                {primaryAgent ? (
                  <button key={primaryAgent.name} type="button" className={`sentinelflow-task-tile${selected?.name === primaryAgent.name ? ' sentinelflow-task-tile-active' : ''}`} onClick={() => setSelected(primaryAgent)}>
                    <div className="flex items-start justify-between gap-3">
                      <strong>{getAgentDisplayName(primaryAgent)}</strong>
                      <div className="flex items-center gap-2">
                        <StatusBadge tone="success">系统主 Agent</StatusBadge>
                        {editingAgentName === primaryAgent.name ? <StatusBadge tone="warn">编辑中</StatusBadge> : null}
                      </div>
                    </div>
                    <span className="sentinelflow-task-tile-copy">{primaryAgent.description || '负责接收入口任务、分派子 Agent 并统一汇总结果。'}</span>
                    <div className="sentinelflow-response-row mt-3">
                      <span className="sentinelflow-muted-text">{primaryAgent.use_global_model === false ? '独立模型' : '统一模型'}</span>
                      <span className="sentinelflow-muted-text">{primaryAgent.is_system ? '系统自动生成' : '唯一中控'}</span>
                    </div>
                  </button>
                ) : (
                  <div className="sentinelflow-message-block sentinelflow-message-error">当前没有启用中的主 Agent。系统无法形成统一调度中枢。</div>
                )}

                <div className="mt-4 mb-2 text-sm font-semibold text-gray-900">子 Agent</div>
                {workerAgents.map((agent) => (
                  <button key={agent.name} type="button" className={`sentinelflow-task-tile${selected?.name === agent.name ? ' sentinelflow-task-tile-active' : ''}`} onClick={() => setSelected(agent)}>
                    <div className="flex items-start justify-between gap-3">
                      <strong>{agent.name}</strong>
                      <div className="flex items-center gap-2">
                        <StatusBadge tone="neutral">子 Agent</StatusBadge>
                        {editingAgentName === agent.name ? <StatusBadge tone="warn">编辑中</StatusBadge> : null}
                      </div>
                    </div>
                    <span className="sentinelflow-task-tile-copy">{agent.description || '无描述'}</span>
                    <div className="sentinelflow-response-row mt-3">
                      <span className="sentinelflow-muted-text">{agent.use_global_model === false ? '独立模型' : '统一模型'}</span>
                      <span className="sentinelflow-muted-text">{agent.enabled === false ? '已停用' : '已启用'}</span>
                    </div>
                  </button>
                ))}
                {agents.length === 0 ? <p className="sentinelflow-muted-text">当前还没有 Agent。</p> : null}
              </div>
            </div>
            <div className="sentinelflow-detail-panel sentinelflow-independent-scroll-panel">
              <h3>{detail ? getAgentDisplayName(detail) : 'Agent 详情'}</h3>
              {detail ? (
                <div className="space-y-3">
                  <div className="sentinelflow-stack-list">
                    <div className="sentinelflow-stack-item"><strong>Prompt 文件</strong><span>{detail.has_prompt ? '已生成' : '未生成'}</span></div>
                    <div className="sentinelflow-stack-item"><strong>角色</strong><span>{detail.role === 'primary' ? '系统主 Agent' : '子 Agent'}</span></div>
                    <div className="sentinelflow-stack-item"><strong>状态</strong><span>{detail.enabled ? '已启用' : '已停用'}</span></div>
                    <div className="sentinelflow-stack-item"><strong>模型策略</strong><span>{detail.use_global_model ? '使用统一模型' : '独立配置模型'}</span></div>
                    <div className="sentinelflow-stack-item"><strong>纯文本 Skill 权限</strong><span>{detail.doc_skill_mode}</span></div>
                  </div>
                  <div className={`sentinelflow-message-block ${detail.role === 'primary' ? 'sentinelflow-message-info' : ''}`}>
                    {detail.role === 'primary'
                      ? (detail.is_system
                        ? '这是系统自动生成的主 Agent。当前对话控制台和任务中心默认都在使用它；如果你后续创建并启用一个显式主 Agent，系统会自动切换到新的主 Agent。'
                        : '这是系统中唯一的主 Agent。它负责接收所有入口任务、决定是否调度子 Agent，并统一输出最终结论。')
                      : '这是一个子 Agent。它不会直接接收系统入口任务，只会在主 Agent 分派时按权限执行具体工作。'}
                  </div>
                  <div className="sentinelflow-stack-list">
                    <div className="sentinelflow-stack-item"><strong>Prompt</strong><span>{detail.prompt?.trim() ? '已配置' : '未配置'}</span></div>
                    {detail.role === 'primary' ? (
                      <>
                        <div className="sentinelflow-stack-item"><strong>对话模式子 Agent</strong><span>{detail.worker_allowlist_command.length || 0}</span></div>
                        <div className="sentinelflow-stack-item"><strong>告警分析子 Agent</strong><span>{detail.worker_allowlist_alert.length || 0}</span></div>
                        <div className="sentinelflow-stack-item"><strong>子 Agent 调用深度</strong><span>{detail.worker_max_steps}</span></div>
                        <div className="sentinelflow-stack-item"><strong>单次并行调度上限</strong><span>{detail.worker_parallel_limit}</span></div>
                      </>
                    ) : null}
                  </div>
                  <div className="grid gap-3">
                    <SkillSummaryChips title="纯文本白名单" items={detail.doc_skill_allowlist} />
                    <SkillSummaryChips title="纯文本禁用名单" items={detail.doc_skill_denylist} />
                    <SkillSummaryChips title="可读取的“文本 + 可执行”Skill 文档" items={detail.hybrid_doc_allowlist} />
                    <SkillSummaryChips title="可执行的“文本 + 可执行”Skill" items={detail.exec_skill_allowlist} />
                    {detail.role === 'primary' ? <SkillSummaryChips title="对话模式允许调度的子 Agent" items={detail.worker_allowlist_command} /> : null}
                    {detail.role === 'primary' ? <SkillSummaryChips title="告警分析模式允许调度的子 Agent" items={detail.worker_allowlist_alert} /> : null}
                  </div>
                  {detail.prompt?.trim() ? (
                    <div>
                      <div className="mb-2 flex items-center justify-between gap-3">
                        <div className="text-sm font-semibold text-gray-900">Prompt</div>
                        {detail.prompt.split('\n').length > 10 ? (
                          <button
                            type="button"
                            className="sentinelflow-ghost-button"
                            onClick={() => setPromptExpanded((current) => !current)}
                          >
                            {promptExpanded ? '收起' : '展开全部'}
                          </button>
                        ) : null}
                      </div>
                      <pre
                        className="rounded-xl border border-gray-200 bg-slate-50 p-4 text-sm leading-6 text-slate-700 whitespace-pre-wrap break-words"
                        style={promptExpanded ? undefined : { maxHeight: '15rem', overflow: 'hidden' }}
                      >
                        {detail.prompt}
                      </pre>
                    </div>
                  ) : null}
                  <div className="flex flex-wrap gap-3">
                    <button type="button" className="sentinelflow-primary-button" onClick={handleStartEdit}>
                      开始编辑
                    </button>
                    {!selectedIsSystem ? (
                      <button type="button" className="sentinelflow-ghost-button" onClick={() => void handleDelete()} disabled={deleting}>
                        {deleting ? '删除中...' : '删除 Agent'}
                      </button>
                    ) : null}
                  </div>
                </div>
              ) : (
                <p className="sentinelflow-muted-text">选择一个 Agent 查看详情。</p>
              )}
            </div>
          </div>
        ) : null}
      </Surface>
    </div>
  )
}
