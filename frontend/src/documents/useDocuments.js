import { useCallback, useEffect, useState } from 'react'
import { ApiError, MESSAGES } from '../api/client'
import { getDocument, listDocuments } from '../api/documents'
import { isWorking } from './status'

export const READ_CHECK_MS = 3000 // an open document being read is asked about this often
export const LIST_CHECK_MS = 5000 // the list, while any document on it is being read
const LONGEST_GAP_MS = 15000 // the longest wait between tries while the backend can't be reached

function messageOf(error) {
  return error instanceof ApiError ? error.message : MESSAGES.serverProblem
}

/**
 * Loads something, and loads it again every `every` ms for as long as keepChecking(data) is true.
 * Gives back:
 *   data      — the newest answer, or null before the first
 *   problem   — the first load failed (with retry() to try again)
 *   notFound  — the backend said 404
 *   trouble   — a later check failed; it keeps trying, waiting longer each time
 */
function useCheckedLoad(load, keepChecking, every, key) {
  const [state, setState] = useState({ data: null, problem: null, notFound: false, trouble: false })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    let timer = null
    let loaded = false
    let failures = 0
    setState({ data: null, problem: null, notFound: false, trouble: false })

    async function run() {
      try {
        const data = await load(controller.signal)
        loaded = true
        failures = 0
        setState({ data, problem: null, notFound: false, trouble: false })
        if (keepChecking(data)) timer = window.setTimeout(run, every)
      } catch (error) {
        if (controller.signal.aborted) return
        if (error instanceof ApiError && error.status === 404) {
          setState({ data: null, problem: null, notFound: true, trouble: false })
          return
        }
        if (!loaded) {
          setState({ data: null, problem: messageOf(error), notFound: false, trouble: false })
          return
        }
        failures += 1
        setState((now) => ({ ...now, trouble: true }))
        timer = window.setTimeout(run, Math.min(every * 2 ** failures, LONGEST_GAP_MS))
      }
    }

    run()
    return () => {
      controller.abort()
      window.clearTimeout(timer)
    }
    // `load` and `keepChecking` belong to `key`; a new key (another document) starts again.
  }, [key, attempt]) // eslint-disable-line react-hooks/exhaustive-deps

  const retry = useCallback(() => setAttempt((now) => now + 1), [])
  return { ...state, retry }
}

/** One document; checked again every few seconds while it is being read. */
export function useDocument(documentId) {
  const { data, ...rest } = useCheckedLoad(
    (signal) => getDocument(documentId, signal),
    isWorking,
    READ_CHECK_MS,
    documentId,
  )
  return { document: data, ...rest }
}

/** The person's documents; checked again while any of them is being read. */
export function useDocumentList() {
  const { data, ...rest } = useCheckedLoad(
    listDocuments,
    (documents) => documents.some(isWorking),
    LIST_CHECK_MS,
    'all',
  )
  return { documents: data, ...rest }
}
