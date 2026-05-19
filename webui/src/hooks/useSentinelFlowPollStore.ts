import { useCallback, useEffect, useState } from 'react'
import { fetchPollAlerts, type PollAlertsResponse } from '@/api/sentinelflow'

type PollStoreSnapshot = {
  data: PollAlertsResponse | null
  loading: boolean
  error: string | null
  updatedAt: number
}

type ReloadOptions = {
  force?: boolean
  silent?: boolean
}

type PollStoreEntry = PollStoreSnapshot & {
  inFlight: Promise<PollAlertsResponse | null> | null
  subscribers: Set<() => void>
}

const FRESH_MS = 1000
const entries = new Map<string, PollStoreEntry>()

function normalizeSourceId(sourceId?: string | null): string {
  return sourceId?.trim() || 'default'
}

function getEntry(sourceId?: string | null): PollStoreEntry {
  const key = normalizeSourceId(sourceId)
  const existing = entries.get(key)
  if (existing) return existing
  const created: PollStoreEntry = {
    data: null,
    loading: false,
    error: null,
    updatedAt: 0,
    inFlight: null,
    subscribers: new Set(),
  }
  entries.set(key, created)
  return created
}

function snapshot(entry: PollStoreEntry): PollStoreSnapshot {
  return {
    data: entry.data,
    loading: entry.loading,
    error: entry.error,
    updatedAt: entry.updatedAt,
  }
}

function notify(entry: PollStoreEntry) {
  entry.subscribers.forEach((subscriber) => subscriber())
}

async function loadPollState(sourceId: string, options: ReloadOptions = {}): Promise<PollAlertsResponse | null> {
  const entry = getEntry(sourceId)
  const now = Date.now()
  if (!options.force && entry.data && now - entry.updatedAt < FRESH_MS) {
    return entry.data
  }
  if (entry.inFlight) {
    return entry.inFlight
  }
  if (!options.silent) {
    entry.loading = true
    entry.error = null
    notify(entry)
  }
  entry.inFlight = fetchPollAlerts(sourceId === 'default' ? undefined : sourceId)
    .then((next) => {
      entry.data = next
      entry.error = null
      entry.updatedAt = Date.now()
      const resolvedSourceId = next.source_id?.trim()
      if (sourceId === 'default' && resolvedSourceId && resolvedSourceId !== sourceId) {
        const alias = getEntry(resolvedSourceId)
        alias.data = next
        alias.error = null
        alias.updatedAt = entry.updatedAt
        notify(alias)
      }
      return next
    })
    .catch((error) => {
      // 静默刷新失败时保留旧数据，避免后台轮询偶发错误挡住整页列表。
      if (!options.silent || !entry.data) {
        entry.error = error instanceof Error ? error.message : 'Unknown error'
      }
      return entry.data
    })
    .finally(() => {
      entry.loading = false
      entry.inFlight = null
      notify(entry)
    })
  notify(entry)
  return entry.inFlight
}

export function useSentinelFlowPollStore(sourceId?: string | null, options?: { autoLoad?: boolean }) {
  const key = normalizeSourceId(sourceId)
  const [state, setState] = useState<PollStoreSnapshot>(() => snapshot(getEntry(key)))
  const autoLoad = options?.autoLoad ?? true

  useEffect(() => {
    const entry = getEntry(key)
    const sync = () => setState(snapshot(entry))
    entry.subscribers.add(sync)
    sync()
    if (autoLoad && !entry.data && !entry.inFlight) {
      void loadPollState(key)
    }
    return () => {
      entry.subscribers.delete(sync)
    }
  }, [autoLoad, key])

  const reload = useCallback((reloadOptions?: ReloadOptions) => loadPollState(key, reloadOptions), [key])

  const setData = useCallback((next: PollAlertsResponse | null) => {
    const entry = getEntry(key)
    entry.data = next
    entry.error = null
    entry.updatedAt = next ? Date.now() : 0
    notify(entry)
  }, [key])

  return {
    ...state,
    loading: state.loading || (autoLoad && !state.data && !state.error),
    reload,
    setData,
  }
}
