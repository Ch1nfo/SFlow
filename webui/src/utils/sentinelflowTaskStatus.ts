import type { AlertTask, ExecutionTraceItem } from '@/api/sentinelflow'

export function hasExternalPollCompletion(result: Record<string, unknown>): boolean {
  const trace = Array.isArray(result.execution_trace) ? (result.execution_trace as ExecutionTraceItem[]) : []
  return trace.some((item) => item.phase === 'completed_externally' && item.success === true)
}

export function getEffectiveTaskStatus(task: Pick<AlertTask, 'status' | 'last_result_data'>): AlertTask['status'] | string {
  const result = (task.last_result_data ?? {}) as Record<string, unknown>
  if (task.status === 'completed' && hasExternalPollCompletion(result)) {
    return 'completed'
  }
  const finalFacts = (result.final_facts as Record<string, unknown> | undefined) ?? {}
  const taskOutcome = (finalFacts.task_outcome as Record<string, unknown> | undefined) ?? {}
  const status = String(taskOutcome.status ?? '').trim()
  return status || task.status
}
