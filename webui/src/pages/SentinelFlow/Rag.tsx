import { useEffect, useState } from 'react'
import { Database, Save } from 'lucide-react'
import { fetchRagSettings, saveRagSettings, type RagSettings } from '@/api/sentinelflow'
import StatusBadge from '@/components/sentinelflow/StatusBadge'
import Surface from '@/components/sentinelflow/Surface'
import PageHeader from '@/components/common/PageHeader'
import { useSentinelFlowAsyncData } from '@/hooks/useSentinelFlowAsyncData'
import { readSessionValue, writeSessionValue } from '@/utils/sentinelflowLocalState'

const RAG_DRAFT_KEY = 'sentinelflow:rag:draft'

type RagDraft = {
  enabled: boolean
  knowledgeId: string
  apiKey: string
  topK: string
  similarityThreshold: string
  retrieveStrategy: string
  enableRerankModel: boolean
  rerankModel: string
}

function buildDraft(settings: RagSettings, prevDraft?: RagDraft | null): RagDraft {
  return {
    enabled: prevDraft?.enabled ?? settings.enabled,
    knowledgeId: prevDraft?.knowledgeId ?? settings.knowledge_id,
    apiKey: prevDraft?.apiKey ?? '',
    topK: prevDraft?.topK ?? String(settings.top_k),
    similarityThreshold: prevDraft?.similarityThreshold ?? String(settings.similarity_threshold),
    retrieveStrategy: prevDraft?.retrieveStrategy ?? String(settings.retrieve_strategy),
    enableRerankModel: prevDraft?.enableRerankModel ?? settings.enable_rerank_model,
    rerankModel: prevDraft?.rerankModel ?? settings.rerank_model,
  }
}

function getRetrieveStrategyLabel(strategy: number | string | undefined) {
  const value = String(strategy ?? '')
  if (value === '1') return '语义检索'
  if (value === '2') return '全文检索'
  if (value === '3') return '混合检索'
  return value || '—'
}

export default function SentinelFlowRagPage() {
  const { data: settings, loading, error, reload } = useSentinelFlowAsyncData(fetchRagSettings, [])
  const [draft, setDraft] = useState<RagDraft | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (!settings) return
    const prevDraft = readSessionValue<RagDraft | null>(RAG_DRAFT_KEY, null)
    setDraft(buildDraft(settings, prevDraft))
  }, [settings])

  useEffect(() => {
    if (!draft) return
    writeSessionValue(RAG_DRAFT_KEY, draft)
  }, [draft])

  function updateDraft(patch: Partial<RagDraft>) {
    setDraft((prev) => prev ? { ...prev, ...patch } : prev)
    setSaved(false)
    setSaveError(null)
  }

  async function handleSave() {
    if (!draft) return
    setSaving(true)
    setSaveError(null)
    try {
      await saveRagSettings({
        enabled: draft.enabled,
        knowledgeId: draft.knowledgeId,
        apiKey: draft.apiKey || undefined,
        topK: Number(draft.topK) || 5,
        similarityThreshold: Number(draft.similarityThreshold) || 0.8,
        retrieveStrategy: Number(draft.retrieveStrategy) || 3,
        enableRerankModel: draft.enableRerankModel,
        rerankModel: draft.rerankModel,
      })
      setSaved(true)
      writeSessionValue(RAG_DRAFT_KEY, null)
      await reload()
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <PageHeader
        title="RAG 配置"
        description="配置 RAG 向量知识库连接参数。"
        icon={<Database className="h-6 w-6" />}
      />

      {/* Status Cards */}
      <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <Surface title="RAG 状态">
          <div className="flex items-center gap-2">
            <StatusBadge tone={settings?.enabled ? 'success' : 'neutral'}>
              {settings?.enabled ? '已启用' : '已禁用'}
            </StatusBadge>
          </div>
        </Surface>
        <Surface title="Top K">
          <div className="text-sm font-semibold leading-6 text-gray-800">
            {settings?.top_k ?? '—'}
          </div>
        </Surface>
        <Surface title="相似度阈值">
          <div className="text-sm font-semibold leading-6 text-gray-800">
            {settings?.similarity_threshold ?? '—'}
          </div>
        </Surface>
        <Surface title="检索策略">
          <div className="text-sm font-semibold leading-6 text-gray-800">
            {getRetrieveStrategyLabel(settings?.retrieve_strategy)}
          </div>
        </Surface>
        <Surface title="Rerank 模型">
          <div className="flex items-center gap-2">
            <StatusBadge tone={settings?.enable_rerank_model ? 'info' : 'neutral'}>
              {settings?.enable_rerank_model ? settings.rerank_model : '未启用'}
            </StatusBadge>
          </div>
        </Surface>
      </div>

      {/* Config Form */}
      <div className="mt-6">
        <Surface title="RAG 连接配置">
          {loading && <p className="text-sm text-gray-500">加载中...</p>}
          {error && <p className="text-sm text-red-600">加载失败：{error}</p>}
          {draft && (
            <div className="space-y-5">
              {/* Enable RAG Toggle */}
              <div className="flex items-center justify-between rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
                <div>
                  <div className="text-sm font-semibold text-gray-900">启用 RAG</div>
                  <div className="text-xs text-gray-500">开启后，告警分析时将自动查询 RAG 知识库获取历史研判参考。</div>
                </div>
                <button
                  type="button"
                  onClick={() => updateDraft({ enabled: !draft.enabled })}
                  className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                    draft.enabled ? 'bg-sky-600' : 'bg-gray-300'
                  }`}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                      draft.enabled ? 'translate-x-6' : 'translate-x-1'
                    }`}
                  />
                </button>
              </div>

              {/* Knowledge ID */}
              <div>
                <label className="block text-sm font-semibold text-gray-900">Knowledge ID</label>
                <p className="text-xs text-gray-500 mt-0.5">RAG 向量知识库的唯一标识。</p>
                <input
                  type="text"
                  value={draft.knowledgeId}
                  onChange={(e) => updateDraft({ knowledgeId: e.target.value })}
                  className="mt-2 block w-full rounded-xl border border-gray-300 px-3 py-2 text-sm text-gray-900 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
                  placeholder="2875eb66-b4e7-4d16-a4f6-e37c774b8cc6"
                />
              </div>

              {/* API Key */}
              <div>
                <label className="block text-sm font-semibold text-gray-900">API Key</label>
                <p className="text-xs text-gray-500 mt-0.5">RAG 接口鉴权密钥。</p>
                <input
                  type="password"
                  value={draft.apiKey}
                  onChange={(e) => updateDraft({ apiKey: e.target.value })}
                  className="mt-2 block w-full rounded-xl border border-gray-300 px-3 py-2 text-sm text-gray-900 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
                  placeholder={settings?.api_key_configured ? '已配置（留空则不更新）' : '请输入 API Key'}
                />
              </div>

              {/* Top K */}
              <div>
                <label className="block text-sm font-semibold text-gray-900">Top K</label>
                <p className="text-xs text-gray-500 mt-0.5">返回最相似的前 K 条知识条目。</p>
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={draft.topK}
                  onChange={(e) => updateDraft({ topK: e.target.value })}
                  className="mt-2 block w-32 rounded-xl border border-gray-300 px-3 py-2 text-sm text-gray-900 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
                />
              </div>

              {/* Similarity Threshold */}
              <div>
                <label className="block text-sm font-semibold text-gray-900">Similarity Threshold</label>
                <p className="text-xs text-gray-500 mt-0.5">相似度阈值（0-1），低于此分值的条目会被过滤。</p>
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  value={draft.similarityThreshold}
                  onChange={(e) => updateDraft({ similarityThreshold: e.target.value })}
                  className="mt-2 block w-32 rounded-xl border border-gray-300 px-3 py-2 text-sm text-gray-900 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
                />
              </div>

              {/* Retrieve Strategy */}
              <div>
                <label className="block text-sm font-semibold text-gray-900">Retrieve Strategy</label>
                <p className="text-xs text-gray-500 mt-0.5">检索策略编号，决定向量检索的匹配方式。</p>
                <select
                  value={draft.retrieveStrategy}
                  onChange={(e) => {
                    const val = e.target.value
                    updateDraft({
                      retrieveStrategy: val,
                      ...(val === '3' ? { enableRerankModel: true } : {}),
                    })
                  }}
                  className="mt-2 block w-48 rounded-xl border border-gray-300 px-3 py-2 text-sm text-gray-900 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
                >
                  <option value="1">语义检索</option>
                  <option value="2">全文检索</option>
                  <option value="3">混合检索</option>
                </select>
              </div>

              {/* Enable Rerank Model Toggle */}
              <div className="flex items-center justify-between rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
                <div>
                  <div className="text-sm font-semibold text-gray-900">Enable Rerank Model</div>
                  <div className="text-xs text-gray-500">
                    {draft.retrieveStrategy === '3'
                      ? '混合检索模式下强制开启 Rerank。'
                      : '开启后，会对检索结果进行重排序以提高相关性。'}
                  </div>
                </div>
                <button
                  type="button"
                  disabled={draft.retrieveStrategy === '3'}
                  onClick={() => updateDraft({ enableRerankModel: !draft.enableRerankModel })}
                  className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                    draft.retrieveStrategy === '3'
                      ? 'bg-sky-600/60 cursor-not-allowed'
                      : draft.enableRerankModel
                        ? 'bg-sky-600'
                        : 'bg-gray-300'
                  }`}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                      draft.enableRerankModel ? 'translate-x-6' : 'translate-x-1'
                    }`}
                  />
                </button>
              </div>

              {/* Rerank Model */}
              {draft.enableRerankModel && (
                <div>
                  <label className="block text-sm font-semibold text-gray-900">Rerank Model</label>
                  <p className="text-xs text-gray-500 mt-0.5">重排序模型名称。</p>
                  <input
                    type="text"
                    value={draft.rerankModel}
                    onChange={(e) => updateDraft({ rerankModel: e.target.value })}
                    className="mt-2 block w-64 rounded-xl border border-gray-300 px-3 py-2 text-sm text-gray-900 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
                    placeholder="bge-reranker-base"
                  />
                </div>
              )}

              {/* Save */}
              <div className="flex items-center gap-3 pt-2">
                <button
                  type="button"
                  disabled={saving}
                  onClick={handleSave}
                  className="inline-flex items-center gap-2 rounded-xl bg-sky-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-sky-700 disabled:opacity-50"
                >
                  <Save className="h-4 w-4" />
                  {saving ? '保存中...' : '保存配置'}
                </button>
                {saved && <span className="text-sm text-emerald-600">已保存</span>}
                {saveError && <span className="text-sm text-red-600">{saveError}</span>}
              </div>
            </div>
          )}
        </Surface>
      </div>
    </div>
  )
}
