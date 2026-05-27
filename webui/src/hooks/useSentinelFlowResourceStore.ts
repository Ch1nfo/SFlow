import { useCallback, useEffect, useState } from 'react'
import { fetchAgents, fetchSkills } from '@/api/sentinelflow'

const LOADERS = {
  skills: fetchSkills,
  agents: fetchAgents,
} as const

type ResourceKey = keyof typeof LOADERS
type ResourceData<K extends ResourceKey> = Awaited<ReturnType<(typeof LOADERS)[K]>>

type ResourceSnapshot<T> = {
  data: T | null
  loading: boolean
  error: string | null
  updatedAt: number
}

type ReloadOptions = {
  force?: boolean
  silent?: boolean
}

type InternalLoadOptions = ReloadOptions & {
  background?: boolean
}

type ResourceEntry<T> = ResourceSnapshot<T> & {
  inFlight: Promise<T | null> | null
  pendingForceReload: InternalLoadOptions | null
  forceFlushPromise: Promise<T | null> | null
  subscribers: Set<() => void>
}

const FRESH_MS = 5000
const entries = new Map<ResourceKey, ResourceEntry<ResourceData<ResourceKey>>>()

function getEntry<K extends ResourceKey>(key: K): ResourceEntry<ResourceData<K>> {
  const existing = entries.get(key)
  if (existing) return existing as ResourceEntry<ResourceData<K>>
  const created: ResourceEntry<ResourceData<K>> = {
    data: null,
    loading: false,
    error: null,
    updatedAt: 0,
    inFlight: null,
    pendingForceReload: null,
    forceFlushPromise: null,
    subscribers: new Set(),
  }
  entries.set(key, created as ResourceEntry<ResourceData<ResourceKey>>)
  return created
}

function snapshot<T>(entry: ResourceEntry<T>): ResourceSnapshot<T> {
  return {
    data: entry.data,
    loading: entry.loading,
    error: entry.error,
    updatedAt: entry.updatedAt,
  }
}

function notify<T>(entry: ResourceEntry<T>) {
  entry.subscribers.forEach((subscriber) => subscriber())
}

async function performResourceFetch<K extends ResourceKey>(
  key: K,
  options: InternalLoadOptions = {},
): Promise<ResourceData<K> | null> {
  const entry = getEntry(key)
  if (entry.inFlight) {
    return entry.inFlight as Promise<ResourceData<K> | null>
  }

  const shouldShowLoading = !options.silent && !options.background && !entry.data
  if (shouldShowLoading) {
    entry.loading = true
    entry.error = null
    notify(entry)
  }

  entry.inFlight = LOADERS[key]()
    .then((next) => {
      entry.data = next as ResourceData<K>
      entry.error = null
      entry.updatedAt = Date.now()
      return next as ResourceData<K>
    })
    .catch((error) => {
      if (!options.silent || !entry.data) {
        entry.error = error instanceof Error ? error.message : 'Unknown error'
      }
      return entry.data
    })
    .finally(() => {
      entry.loading = false
      entry.inFlight = null
      notify(entry)
    }) as Promise<ResourceData<K> | null>

  notify(entry)
  return entry.inFlight as Promise<ResourceData<K> | null>
}

async function flushForceResourceReloads<K extends ResourceKey>(key: K): Promise<ResourceData<K> | null> {
  const entry = getEntry(key)
  if (entry.forceFlushPromise) {
    return entry.forceFlushPromise as Promise<ResourceData<K> | null>
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
      await performResourceFetch(key, { ...pending, force: true })
    }
    return entry.data as ResourceData<K> | null
  })().finally(() => {
    entry.forceFlushPromise = null
  }) as Promise<ResourceData<K> | null>

  return entry.forceFlushPromise
}

async function loadResource<K extends ResourceKey>(
  key: K,
  options: InternalLoadOptions = {},
): Promise<ResourceData<K> | null> {
  const entry = getEntry(key)
  const now = Date.now()
  const isFresh = Boolean(entry.data) && now - entry.updatedAt < FRESH_MS

  if (!options.force && isFresh && !options.background) {
    if (!entry.inFlight) {
      void loadResource(key, { force: true, silent: true, background: true })
    }
    return entry.data as ResourceData<K>
  }

  if (options.force && (entry.inFlight || entry.forceFlushPromise)) {
    entry.pendingForceReload = options
    return flushForceResourceReloads(key)
  }

  if (entry.inFlight) {
    return entry.inFlight as Promise<ResourceData<K> | null>
  }

  return performResourceFetch(key, options)
}

export function useSentinelFlowResourceStore<K extends ResourceKey>(
  key: K,
  options?: { autoLoad?: boolean },
) {
  const autoLoad = options?.autoLoad ?? true
  const [state, setState] = useState<ResourceSnapshot<ResourceData<K>>>(() => snapshot(getEntry(key)))

  useEffect(() => {
    const entry = getEntry(key)
    const sync = () => setState(snapshot(entry))
    entry.subscribers.add(sync)
    sync()
    if (autoLoad && !entry.inFlight) {
      void loadResource(key, entry.data ? { silent: true } : undefined)
    }
    return () => {
      entry.subscribers.delete(sync)
    }
  }, [autoLoad, key])

  const reload = useCallback((reloadOptions?: ReloadOptions) => loadResource(key, reloadOptions), [key])

  const setData = useCallback((next: ResourceData<K> | null) => {
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
