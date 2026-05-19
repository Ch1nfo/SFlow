import { useEffect, useRef } from 'react'

type LiveRefreshOptions = {
  enabled?: boolean
  intervalMs?: number
}

export function useSentinelFlowLiveRefresh(
  callback: () => void | Promise<void>,
  options: LiveRefreshOptions = {},
) {
  const { enabled = true, intervalMs = 5000 } = options
  const inFlightRef = useRef(false)

  useEffect(() => {
    if (!enabled) return

    const run = () => {
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
        return
      }
      if (inFlightRef.current) return
      inFlightRef.current = true
      let result: void | Promise<void>
      try {
        result = callback()
      } catch {
        inFlightRef.current = false
        return
      }
      void Promise.resolve(result).finally(() => {
        inFlightRef.current = false
      })
    }

    const timer = window.setInterval(run, intervalMs)
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        run()
      }
    }

    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', handleVisibilityChange)
      inFlightRef.current = false
    }
  }, [callback, enabled, intervalMs])
}
