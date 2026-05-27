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

export type PollReloadResult = {
  data: PollAlertsResponse | null
  error: string | null
}

type PollStoreEntry = PollStoreSnapshot & {
  inFlight: Promise<PollReloadResult> | null
  pendingForceReload: ReloadOptions | null
  forceFlushPromise: Promise<PollReloadResult> | null
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
    pendingForceReload: null,
    forceFlushPromise: null,
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

function buildReloadResult(entry: PollStoreEntry): PollReloadResult {
  return {
    data: entry.data,
    error: entry.error,
  }
}

async function performPollFetch(sourceId: string, options: ReloadOptions = {}): Promise<PollReloadResult> {
  const entry = getEntry(sourceId)
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
      return buildReloadResult(entry)
    })
    .catch((error) => {
      if (!options.silent || !entry.data) {
        entry.error = error instanceof Error ? error.message : 'Unknown error'
      }
      return buildReloadResult(entry)
    })
    .finally(() => {
      entry.loading = false
      entry.inFlight = null
      notify(entry)
    })
  notify(entry)
  return entry.inFlight
}

async function flushForcePollReloads(sourceId: string): Promise<PollReloadResult> {
  const entry = getEntry(sourceId)
  if (entry.forceFlushPromise) {
    return entry.forceFlushPromise
  }
  entry.forceFlushPromise = (async () => {
    while (entry.inFlight || entry.pendingForceReload) {
      if (entry.inFlight) {
        await entry.inFlight
        continue
      }
      const pending = entry.pendingForceReload
      if (!pending) continue
      entry.pendingForceReload = null
      await performPollFetch(sourceId, { ...pending, force: true })
    }
    return buildReloadResult(entry)
  })().finally(() => {
    entry.forceFlushPromise = null
  })
  return entry.forceFlushPromise
}

async function loadPollState(sourceId: string, options: ReloadOptions = {}): Promise<PollReloadResult> {
  const entry = getEntry(sourceId)
  const now = Date.now()
  if (!options.force && entry.data && now - entry.updatedAt < FRESH_MS) {
    return buildReloadResult(entry)
  }
  if (options.force && (entry.inFlight || entry.forceFlushPromise)) {
    entry.pendingForceReload = options
    return flushForcePollReloads(sourceId)
  }
  if (entry.inFlight) {
    return entry.inFlight
  }
  return performPollFetch(sourceId, options)
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
