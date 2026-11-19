import { useCallback, useEffect, useState } from 'react'

/**
 * After "too many tries" (429) the backend says how many seconds to wait. `waiting` stays true
 * until then, so the screen can keep its button switched off instead of letting the person
 * try (and fail) again. Gives back { waiting, waitFor(seconds) }.
 */
export function useWait() {
  const [until, setUntil] = useState(null)

  useEffect(() => {
    if (until === null) return
    const timer = window.setTimeout(() => setUntil(null), Math.max(0, until - Date.now()))
    return () => window.clearTimeout(timer)
  }, [until])

  const waitFor = useCallback((seconds) => {
    setUntil(Date.now() + Math.max(0, seconds) * 1000)
  }, [])

  return { waiting: until !== null, waitFor }
}
