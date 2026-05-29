import type { AlertTask } from '@/api/sentinelflow'

/** List rows are compact summaries (payload is empty); keep full detail visible while polling refreshes. */
export function resolveSelectedTaskDisplay(
  detail: AlertTask | null,
  summary: AlertTask | null,
): AlertTask | null {
  if (!summary) return null
  if (detail?.task_id !== summary.task_id) return summary
  return {
    ...detail,
    status: summary.status,
    updated_at: summary.updated_at,
    last_action: summary.last_action,
    last_result_success: summary.last_result_success,
    last_result_error: summary.last_result_error,
    retry_count: summary.retry_count,
  }
}
