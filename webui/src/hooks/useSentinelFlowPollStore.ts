import { useCallback, useEffect, useState } from 'react'
import { fetchPollAlerts, type AlertTask, type PollAlertsResponse, type TaskCursor } from '@/api/sentinelflow'

const DEFAULT_PAGE_SIZE = 60
const FRESH_MS = 1000

type ReloadOptions = {
  force?: boolean
  silent?: boolean
}

export type PollReloadResult = {
  data: PollAlertsResponse | null
  error: string | null
}

type PollMeta = Omit<PollAlertsResponse, 'tasks'>

type PollStoreSnapshot = {
  data: PollAlertsResponse | null
  loading: boolean
  loadingMore: boolean
  error: string | null
  updatedAt: number
  hasMore: boolean
}

type PollStoreEntry = {
  sourceId: string
  since: string
  pageSize: number
  tasks: AlertTask[]
  meta: PollMeta | null
  /** Cursor pointing at the next (older) page to load via "show more". */
  cursor: TaskCursor | null
  loadedOnce: boolean
  loading: boolean
  loadingMore: boolean
  error: string | null
  updatedAt: number
  inFlight: Promise<PollReloadResult> | null
  pendingForceReload: ReloadOptions | null
  forceFlushPromise: Promise<PollReloadResult> | null
  loadMoreInFlight: Promise<PollReloadResult> | null
  subscribers: Set<() => void>
}

const entries = new Map<string, PollStoreEntry>()

function normalizeSourceId(sourceId?: string | null): string {
  return sourceId?.trim() || 'default'
}

function entryKey(sourceId: string, since: string, pageSize: number): string {
  return `${sourceId}::${since}::${pageSize}`
}

function getEntry(sourceId?: string | null, since = '', pageSize = DEFAULT_PAGE_SIZE): PollStoreEntry {
  const normalizedSource = normalizeSourceId(sourceId)
  const key = entryKey(normalizedSource, since, pageSize)
  const existing = entries.get(key)
  if (existing) return existing
  const created: PollStoreEntry = {
    sourceId: normalizedSource,
    since,
    pageSize,
    tasks: [],
    meta: null,
    cursor: null,
    loadedOnce: false,
    loading: false,
    loadingMore: false,
    error: null,
    updatedAt: 0,
    inFlight: null,
    pendingForceReload: null,
    forceFlushPromise: null,
    loadMoreInFlight: null,
    subscribers: new Set(),
  }
  entries.set(key, created)
  return created
}

function stripTasks(response: PollAlertsResponse): PollMeta {
  const { tasks: _tasks, ...rest } = response
  return rest
}

function snapshotData(entry: PollStoreEntry): PollAlertsResponse | null {
  if (!entry.loadedOnce && !entry.meta) return null
  const meta = (entry.meta ?? {}) as PollMeta
  return { ...meta, tasks: entry.tasks } as PollAlertsResponse
}

function snapshot(entry: PollStoreEntry): PollStoreSnapshot {
  return {
    data: snapshotData(entry),
    loading: entry.loading,
    loadingMore: entry.loadingMore,
    error: entry.error,
    updatedAt: entry.updatedAt,
    hasMore: Boolean(entry.cursor),
  }
}

function notify(entry: PollStoreEntry) {
  entry.subscribers.forEach((subscriber) => subscriber())
}

function buildReloadResult(entry: PollStoreEntry): PollReloadResult {
  return {
    data: snapshotData(entry),
    error: entry.error,
  }
}

/**
 * A row is considered unchanged when its mutation markers match, so we can keep
 * the previous object reference and avoid re-rendering that row on refresh.
 */
function isSameTask(a: AlertTask, b: AlertTask): boolean {
  return (
    a.updated_at === b.updated_at &&
    a.status === b.status &&
    a.last_action === b.last_action &&
    a.retry_count === b.retry_count &&
    a.last_result_success === b.last_result_success
  )
}

/**
 * Merge the freshly fetched newest page into the accumulated list:
 * unchanged rows keep their reference (no flicker), changed rows are replaced,
 * brand-new rows appear at the top, and older accumulated pages are preserved.
 * Returns the existing array reference unchanged when nothing changed at all.
 */
function mergeFirstPage(existing: AlertTask[], incoming: AlertTask[]): AlertTask[] {
  const incomingIds = new Set(incoming.map((task) => task.task_id))
  const prevById = new Map(existing.map((task) => [task.task_id, task]))
  const head = incoming.map((task) => {
    const prev = prevById.get(task.task_id)
    return prev && isSameTask(prev, task) ? prev : task
  })
  const tail = existing.filter((task) => !incomingIds.has(task.task_id))
  const merged = head.concat(tail)
  if (merged.length === existing.length && merged.every((task, index) => task === existing[index])) {
    return existing
  }
  return merged
}

function appendPage(existing: AlertTask[], incoming: AlertTask[]): AlertTask[] {
  if (!incoming.length) return existing
  const ids = new Set(existing.map((task) => task.task_id))
  const added = incoming.filter((task) => !ids.has(task.task_id))
  return added.length ? existing.concat(added) : existing
}

function applyAlias(entry: PollStoreEntry, response: PollAlertsResponse) {
  if (entry.sourceId !== 'default') return
  const resolved = response.source_id?.trim()
  if (!resolved || resolved === entry.sourceId) return
  const alias = getEntry(resolved, entry.since, entry.pageSize)
  alias.tasks = entry.tasks
  alias.meta = entry.meta
  alias.cursor = entry.cursor
  alias.loadedOnce = entry.loadedOnce
  alias.error = null
  alias.updatedAt = entry.updatedAt
  notify(alias)
}

function performFirstPageFetch(entry: PollStoreEntry, options: ReloadOptions = {}): Promise<PollReloadResult> {
  if (entry.inFlight) {
    return entry.inFlight
  }
  if (!options.silent && !entry.loadedOnce) {
    entry.loading = true
    entry.error = null
    notify(entry)
  }
  entry.inFlight = fetchPollAlerts({
    sourceId: entry.sourceId === 'default' ? undefined : entry.sourceId,
    since: entry.since || undefined,
    limit: entry.pageSize,
  })
    .then((next) => {
      const incoming = next.tasks ?? []
      entry.tasks = entry.loadedOnce ? mergeFirstPage(entry.tasks, incoming) : incoming
      entry.meta = stripTasks(next)
      // Only (re)initialise the "load more" cursor on the very first load;
      // refreshing the newest page must not rewind an expanded list.
      if (!entry.loadedOnce) {
        entry.cursor = next.tasks_cursor ?? null
      }
      entry.loadedOnce = true
      entry.error = null
      entry.updatedAt = Date.now()
      applyAlias(entry, next)
      return buildReloadResult(entry)
    })
    .catch((error) => {
      if (!options.silent || !entry.loadedOnce) {
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

function flushForcePollReloads(entry: PollStoreEntry): Promise<PollReloadResult> {
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
      await performFirstPageFetch(entry, { ...pending, force: true })
    }
    return buildReloadResult(entry)
  })().finally(() => {
    entry.forceFlushPromise = null
  })
  return entry.forceFlushPromise
}

function loadPollState(entry: PollStoreEntry, options: ReloadOptions = {}): Promise<PollReloadResult> {
  const now = Date.now()
  if (!options.force && entry.loadedOnce && now - entry.updatedAt < FRESH_MS) {
    return Promise.resolve(buildReloadResult(entry))
  }
  if (options.force && (entry.inFlight || entry.forceFlushPromise)) {
    entry.pendingForceReload = options
    return flushForcePollReloads(entry)
  }
  if (entry.inFlight) {
    return entry.inFlight
  }
  return performFirstPageFetch(entry, options)
}

function performLoadMore(entry: PollStoreEntry): Promise<PollReloadResult> {
  if (entry.loadMoreInFlight) {
    return entry.loadMoreInFlight
  }
  const cursor = entry.cursor
  if (!cursor) {
    return Promise.resolve(buildReloadResult(entry))
  }
  entry.loadingMore = true
  notify(entry)
  entry.loadMoreInFlight = fetchPollAlerts({
    sourceId: entry.sourceId === 'default' ? undefined : entry.sourceId,
    since: entry.since || undefined,
    limit: entry.pageSize,
    cursorSortTime: cursor.sort_time,
    cursorTaskId: cursor.task_id,
  })
    .then((next) => {
      entry.tasks = appendPage(entry.tasks, next.tasks ?? [])
      entry.meta = stripTasks(next)
      entry.cursor = next.tasks_cursor ?? null
      entry.error = null
      entry.updatedAt = Date.now()
      return buildReloadResult(entry)
    })
    .catch((error) => {
      entry.error = error instanceof Error ? error.message : 'Unknown error'
      return buildReloadResult(entry)
    })
    .finally(() => {
      entry.loadingMore = false
      entry.loadMoreInFlight = null
      notify(entry)
    })
  return entry.loadMoreInFlight
}

export function useSentinelFlowPollStore(
  sourceId?: string | null,
  options?: { autoLoad?: boolean; since?: string | null; pageSize?: number },
) {
  const since = options?.since?.trim() || ''
  const pageSize = options?.pageSize ?? DEFAULT_PAGE_SIZE
  const normalizedSource = normalizeSourceId(sourceId)
  const key = entryKey(normalizedSource, since, pageSize)
  const [state, setState] = useState<PollStoreSnapshot>(() => snapshot(getEntry(sourceId, since, pageSize)))
  const autoLoad = options?.autoLoad ?? true

  useEffect(() => {
    const entry = getEntry(sourceId, since, pageSize)
    const sync = () => setState(snapshot(entry))
    entry.subscribers.add(sync)
    sync()
    if (autoLoad && !entry.loadedOnce && !entry.inFlight) {
      void loadPollState(entry)
    }
    return () => {
      entry.subscribers.delete(sync)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoLoad, key])

  const reload = useCallback(
    (reloadOptions?: ReloadOptions) => loadPollState(getEntry(sourceId, since, pageSize), reloadOptions ?? {}),
    [key], // eslint-disable-line react-hooks/exhaustive-deps
  )

  const loadMore = useCallback(
    () => performLoadMore(getEntry(sourceId, since, pageSize)),
    [key], // eslint-disable-line react-hooks/exhaustive-deps
  )

  const setData = useCallback(
    (next: PollAlertsResponse | null) => {
      const entry = getEntry(sourceId, since, pageSize)
      if (!next) {
        entry.tasks = []
        entry.meta = null
        entry.cursor = null
        entry.loadedOnce = false
        entry.updatedAt = 0
      } else {
        entry.tasks = next.tasks ?? []
        entry.meta = stripTasks(next)
        entry.cursor = next.tasks_cursor ?? null
        entry.loadedOnce = true
        entry.updatedAt = Date.now()
      }
      entry.error = null
      notify(entry)
    },
    [key], // eslint-disable-line react-hooks/exhaustive-deps
  )

  return {
    ...state,
    loading: state.loading || (autoLoad && !state.data && !state.error),
    reload,
    loadMore,
    setData,
  }
}
