import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { ApiError, MESSAGES } from '../api/client'
import { getProfile, updateProfile } from '../api/profile'
import { useAuth } from '../auth/AuthProvider'
import { applyDisplay, rememberDisplay } from './display'

/*
 * The signed-in person's profile (see api/profile.js), for every screen.
 *   status   — 'idle' (signed out), 'loading', 'ready', or 'failed' (problem holds the message)
 *   profile  — the profile, or null until it has loaded
 *   update(changes, { optimistic }) — saves a change and gives back the saved profile (or throws
 *              an ApiError). With optimistic, the change shows at once and is put back if the
 *              save fails; use it only for changes the backend can't refuse (switches, text size).
 *              Changes are sent one at a time, in order, and the screen shows the newest answer.
 *   reload() — loads the profile again (after 'failed')
 * The text size and contrast in the profile are applied to every screen.
 */

const ProfileContext = createContext(null)

function withChanges(profile, changes) {
  return {
    ...profile,
    accessibility: { ...profile.accessibility, ...changes.accessibility },
    consents: { ...profile.consents, ...changes.consents },
  }
}

function messageOf(error) {
  return error instanceof ApiError ? error.message : MESSAGES.serverProblem
}

export function ProfileProvider({ children }) {
  const { status: authStatus } = useAuth()
  const [state, setState] = useState({ status: 'idle', profile: null, problem: null })
  const [attempt, setAttempt] = useState(0)
  const saved = useRef(null) // the newest profile the backend has confirmed
  const latest = useRef(0) // the number of the newest change
  const queue = useRef(Promise.resolve()) // so changes reach the backend one at a time
  const round = useRef(0) // changes from before a sign-out or reload are not shown

  useEffect(() => {
    round.current += 1
    saved.current = null
    if (authStatus !== 'signedIn') {
      setState({ status: 'idle', profile: null, problem: null })
      return
    }
    const controller = new AbortController()
    setState({ status: 'loading', profile: null, problem: null })
    getProfile(controller.signal).then(
      (profile) => {
        saved.current = profile
        setState({ status: 'ready', profile, problem: null })
      },
      (error) => {
        if (controller.signal.aborted) return
        setState({ status: 'failed', profile: null, problem: messageOf(error) })
      },
    )
    return () => controller.abort()
  }, [authStatus, attempt])

  // Text size and contrast follow the profile on every screen, and are remembered for next time.
  const accessibility = state.profile?.accessibility
  useLayoutEffect(() => {
    if (!accessibility) return
    const display = { fontScale: accessibility.fontScale, highContrast: accessibility.highContrast }
    applyDisplay(display)
    rememberDisplay(display)
  }, [accessibility])

  const update = useCallback((changes, { optimistic = false } = {}) => {
    const number = ++latest.current
    const mine = round.current
    if (optimistic) {
      setState((now) => (now.profile ? { ...now, profile: withChanges(now.profile, changes) } : now))
    }
    const sending = queue.current.then(() => updateProfile(changes))
    queue.current = sending.catch(() => undefined)
    return sending.then(
      (profile) => {
        if (mine !== round.current) return profile
        saved.current = profile
        if (number === latest.current) setState({ status: 'ready', profile, problem: null })
        return profile
      },
      (error) => {
        if (mine === round.current && number === latest.current && saved.current) {
          setState({ status: 'ready', profile: saved.current, problem: null })
        }
        throw error
      },
    )
  }, [])

  const reload = useCallback(() => setAttempt((now) => now + 1), [])

  const value = useMemo(() => ({ ...state, update, reload }), [state, update, reload])
  return <ProfileContext.Provider value={value}>{children}</ProfileContext.Provider>
}

export function useProfile() {
  const value = useContext(ProfileContext)
  if (!value) throw new Error('useProfile must be used inside <ProfileProvider>')
  return value
}
