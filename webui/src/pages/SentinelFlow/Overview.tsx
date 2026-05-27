import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronRight, LayoutDashboard, ListTodo, MessageSquareText, Siren } from 'lucide-react'
import { fetchDashboardSummary, fetchHealth } from '@/api/sentinelflow'
import Surface from '@/components/sentinelflow/Surface'
import StatusBadge from '@/components/sentinelflow/StatusBadge'
import PageHeader from '@/components/common/PageHeader'
import { brand, withProductName } from '@/config/brand'
import { useSentinelFlowAsyncData } from '@/hooks/useSentinelFlowAsyncData'
import { useSentinelFlowResourceStore } from '@/hooks/useSentinelFlowResourceStore'
import { useSentinelFlowLiveRefresh } from '@/hooks/useSentinelFlowLiveRefresh'
import { useSentinelFlowPollStore } from '@/hooks/useSentinelFlowPollStore'
import { getRuntimeActivityBadgeLabel, getRuntimeActivityStatus, readRuntimeActivity, subscribeRuntimeActivity, type RuntimeActivity } from '@/utils/sentinelflowRuntimeSync'
import { getEffectiveTaskStatus } from '@/utils/sentinelflowTaskStatus'

export default function SentinelFlowOverviewPage() {
  const { data: health, reload: reloadHealth } = useSentinelFlowAsyncData(fetchHealth, [])
  const { data: poll, reload: reloadPoll } = useSentinelFlowPollStore('all')
  const { data: skills, reload: reloadSkills } = useSentinelFlowResourceStore('skills')
  const { data: summary, reload: reloadSummary } = useSentinelFlowAsyncData(fetchDashboardSummary, [])
  const [activity, setActivity] = useState<RuntimeActivity | null>(() => readRuntimeActivity())

  useEffect(() => {
    return subscribeRuntimeActivity((next) => {
      setActivity(next)
      void Promise.all([reloadHealth(), reloadPoll({ force: true, silent: true }), reloadSkills(), reloadSummary()])
    })
  }, [reloadHealth, reloadPoll, reloadSkills, reloadSummary])

  useSentinelFlowLiveRefresh(() => {
    void reloadPoll({ silent: true })
  }, { intervalMs: 5000 })

  useSentinelFlowLiveRefresh(() => {
    void reloadSummary()
  }, { intervalMs: 30000 })

  useSentinelFlowLiveRefresh(() => {
    void reloadSkills({ silent: true })
  }, { intervalMs: 60000 })

  const tasks = poll?.tasks ?? []
  const skillCount = skills?.skills?.length ?? 0
  const runningCount = tasks.filter((task) => getEffectiveTaskStatus(task) === 'running').length
  const awaitingApprovalCount = tasks.filter((task) => getEffectiveTaskStatus(task) === 'awaiting_approval').length
  const failedCount = tasks.filter((task) => {
    const status = getEffectiveTaskStatus(task)
    return status === 'failed' || status === 'pending_closure'
  }).length

  return (
    <div className="sentinelflow-page-stack">
      <PageHeader
        title={`${brand.productName} 总览`}
        description={withProductName('查看平台当前运行状态、任务概览和最近动作。')}
        icon={<LayoutDashboard className="w-8 h-8" />}
      />

      <div className="grid gap-4 md:grid-cols-3">
        <Link
          to="/alerts"
          className="group flex items-center gap-4 rounded-xl border border-gray-200 bg-white p-5 transition-all hover:border-sky-200 hover:shadow-md"
        >
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-red-50 text-red-500">
            <Siren className="h-5 w-5" />
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-semibold text-gray-900">告警工作台</h3>
            <p className="mt-1 text-xs text-gray-500">进入研判、处置、通知和单条闭环操作入口。</p>
          </div>
          <ChevronRight className="h-4 w-4 text-gray-300 transition-colors group-hover:text-sky-500" />
        </Link>

        <Link
          to="/tasks"
          className="group flex items-center gap-4 rounded-xl border border-gray-200 bg-white p-5 transition-all hover:border-violet-200 hover:shadow-md"
        >
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-violet-50 text-violet-500">
            <ListTodo className="h-5 w-5" />
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-semibold text-gray-900">任务中心</h3>
            <p className="mt-1 text-xs text-gray-500">查看 queued / running / failed / succeeded 生命周期。</p>
          </div>
          <ChevronRight className="h-4 w-4 text-gray-300 transition-colors group-hover:text-violet-500" />
        </Link>

        <Link
          to="/conversation"
          className="group flex items-center gap-4 rounded-xl border border-gray-200 bg-white p-5 transition-all hover:border-emerald-200 hover:shadow-md"
        >
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-emerald-50 text-emerald-500">
            <MessageSquareText className="h-5 w-5" />
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-semibold text-gray-900">对话指挥台</h3>
            <p className="mt-1 text-xs text-gray-500">{withProductName('通过自然语言调度 SentinelFlow 的核心能力。')}</p>
          </div>
          <ChevronRight className="h-4 w-4 text-gray-300 transition-colors group-hover:text-emerald-500" />
        </Link>
      </div>

      <Surface title="平台总览" subtitle="优先展示值班最关心的运行态势、任务密度与能力可用性。">
        <div className="sentinelflow-stat-grid">
          <div className="sentinelflow-stat-card"><span>当前轮询拉取</span><strong>{poll?.fetched_count ?? 0}</strong><em>最新一次拉取结果</em></div>
          <div className="sentinelflow-stat-card"><span>待处理任务</span><strong>{poll?.queued_count ?? 0}</strong><em>待值班处理队列</em></div>
          <div className="sentinelflow-stat-card"><span>可用 Skills</span><strong>{skillCount}</strong><em>纯文本 / 文本+可执行</em></div>
          <div className="sentinelflow-stat-card"><span>可用 Agents</span><strong>{summary?.totals.agents ?? 0}</strong><em>当前可用的子 Agent 数量</em></div>
          <div className="sentinelflow-stat-card"><span>业务触发</span><strong>{summary?.judgment.business_trigger ?? 0}</strong><em>已识别业务/测试触发</em></div>
          <div className="sentinelflow-stat-card"><span>误报</span><strong>{summary?.judgment.false_positive ?? 0}</strong><em>已识别规则误报</em></div>
          <div className="sentinelflow-stat-card"><span>真实攻击</span><strong>{summary?.judgment.true_attack ?? 0}</strong><em>已识别真实攻击</em></div>
          <div className="sentinelflow-stat-card"><span>封禁 IP</span><strong>{summary?.operations.banned_ip_count ?? 0}</strong><em>已执行封禁动作</em></div>
        </div>
      </Surface>

      <div className="sentinelflow-grid-2">
        <Surface title="运行健康度" subtitle="聚焦 Runtime、轮询、任务生命周期和演示模式。">
          <div className="sentinelflow-stack-list">
            <div className="sentinelflow-stack-item">
              <strong>{brand.productName} API</strong>
              <div className="sentinelflow-inline-status">
                <StatusBadge tone={health?.status === 'ok' ? 'success' : 'danger'}>{health?.status ?? 'unknown'}</StatusBadge>
                {health?.demo_mode ? <StatusBadge tone="warn">演示模式</StatusBadge> : null}
              </div>
            </div>
            <div className="sentinelflow-stack-item">
              <strong>轮询与分发</strong>
              <div className="sentinelflow-inline-status">
                <span>拉取 {poll?.fetched_count ?? 0}</span>
                <span>排队 {poll?.queued_count ?? 0}</span>
                <span>失败 {poll?.failed_count ?? 0}</span>
              </div>
            </div>
            <div className="sentinelflow-stack-item">
              <strong>任务生命周期</strong>
              <div className="sentinelflow-inline-status">
                <span>执行中 {runningCount}</span>
                <span>待审批 {awaitingApprovalCount}</span>
                <span>失败 {failedCount}</span>
              </div>
            </div>
          </div>
        </Surface>

        <Surface title="最近平台动作" subtitle="记录最近一次跨页面操作，方便值班快速确认当前处理对象。">
          {activity ? (
            <div className="sentinelflow-activity-banner">
              <div className="sentinelflow-response-row">
                <StatusBadge tone={getRuntimeActivityStatus(activity) === 'success' ? 'success' : 'warn'}>
                  {getRuntimeActivityBadgeLabel(activity)}
                </StatusBadge>
                <span>{new Date(activity.timestamp).toLocaleString()}</span>
              </div>
              <strong>{activity.title}</strong>
              <p className="sentinelflow-muted-text">{activity.detail}</p>
            </div>
          ) : (
            <p className="sentinelflow-muted-text">当前还没有新的平台动作记录。</p>
          )}
        </Surface>
      </div>

    </div>
  )
}
