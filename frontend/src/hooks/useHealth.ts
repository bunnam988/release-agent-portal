import { useEffect, useState } from 'react'
import { getHealth } from '../api/client'

/** Polls backend + opencode health for the sidebar connection indicator. */
export function useHealth(): boolean | null {
  const [health, setHealth] = useState<boolean | null>(null)

  useEffect(() => {
    let cancelled = false
    function poll() {
      getHealth()
        .then((res) => {
          if (!cancelled) setHealth(res.opencode_reachable)
        })
        .catch(() => {
          if (!cancelled) setHealth(false)
        })
    }
    poll()
    const interval = setInterval(poll, 15000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [])

  return health
}
