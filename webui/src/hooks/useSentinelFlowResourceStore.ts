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

type ResourceEntry<T> = ResourceSnapshot<T> & {
  inFlight: Promise<T | null> | null
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

async function loadResource<K extends ResourceKey>(
  key: K,
  options: ReloadOptions = {},
): Promise<ResourceData<K> | null> {
  const entry = getEntry(key)
  const now = Date.now()
  if (!options.force && entry.data && now - entry.updatedAt < FRESH_MS) {
    return entry.data
  }
  if (entry.inFlight) {
    return entry.inFlight as Promise<ResourceData<K> | null>
  }
  const shouldShowLoading = !options.silent && !entry.data
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
    if (autoLoad && !entry.data && !entry.inFlight) {
      void loadResource(key)
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
