export type RuntimeActivityStatus = 'success' | 'failed' | 'pending_approval' | 'running'

export type RuntimeActivity = {
  type: 'alert_action' | 'command_dispatch'
  title: string
  detail: string
  success: boolean
  status?: RuntimeActivityStatus
  timestamp: string
}

const STORAGE_KEY = 'sentinelflow:last-runtime-activity'
const EVENT_NAME = 'sentinelflow:runtime-activity'

export function publishRuntimeActivity(activity: RuntimeActivity) {
  if (typeof window === 'undefined') return
  window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(activity))
  window.dispatchEvent(new CustomEvent<RuntimeActivity>(EVENT_NAME, { detail: activity }))
}

export function readRuntimeActivity(): RuntimeActivity | null {
  if (typeof window === 'undefined') return null
  const value = window.sessionStorage.getItem(STORAGE_KEY)
  if (!value) return null
  try {
    return JSON.parse(value) as RuntimeActivity
  } catch {
    return null
  }
}

export function subscribeRuntimeActivity(handler: (activity: RuntimeActivity) => void) {
  if (typeof window === 'undefined') return () => undefined
  const listener = (event: Event) => {
    const customEvent = event as CustomEvent<RuntimeActivity>
    if (customEvent.detail) {
      handler(customEvent.detail)
    }
  }
  window.addEventListener(EVENT_NAME, listener)
  return () => window.removeEventListener(EVENT_NAME, listener)
}

export function getRuntimeActivityStatus(activity: RuntimeActivity): RuntimeActivityStatus {
  if (activity.status) return activity.status
  if (!activity.success && (activity.detail.includes('等待技能审批') || activity.detail.includes('待审批'))) {
    return 'pending_approval'
  }
  return activity.success ? 'success' : 'failed'
}

export function getRuntimeActivityBadgeLabel(activity: RuntimeActivity): string {
  const status = getRuntimeActivityStatus(activity)
  if (status === 'running') return '正在执行'
  if (status === 'pending_approval') return '等待技能审批'
  return status === 'success' ? '最新动作成功' : '最新动作失败'
}
