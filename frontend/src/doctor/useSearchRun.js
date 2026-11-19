import { useCallback, useEffect, useRef, useState } from 'react'
import { stagesDone } from './search'

/**
 * Runs one call to the doctor search (start or answer) and follows its steps.
 *   working  — true while the backend works
 *   done     — how many STAGES are done (search.js)
 *   problem  — the error of the last call (an ApiError, or another error), or null
 *   go(call, startAt) — call({ onStep, signal }) is startSearch or answerSearch; startAt is how
 *     many stages were already done. Gives back the result, or null when it failed or the screen
 *     closed. Leaving the screen stops waiting; the backend still finishes and keeps the search.
 */
export function useSearchRun() {
  const [run, setRun] = useState({ working: false, done: 0, problem: null })
  const controller = useRef(null)

  useEffect(() => () => controller.current?.abort(), [])

  const go = useCallback(async (call, startAt = 0) => {
    controller.current?.abort()
    const current = new AbortController()
    controller.current = current
    setRun({ working: true, done: startAt, problem: null })
    const onStep = (step) => {
      if (!current.signal.aborted) setRun((now) => ({ ...now, done: stagesDone(now.done, step) }))
    }
    try {
      const result = await call({ onStep, signal: current.signal })
      if (current.signal.aborted) return null
      setRun({ working: false, done: 0, problem: null })
      return result
    } catch (error) {
      if (current.signal.aborted || error?.name === 'AbortError') return null
      setRun({ working: false, done: 0, problem: error })
      return null
    }
  }, [])

  return { ...run, go }
}
