import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { currentUser, register as registerAccount, signIn as signInAccount, signOut as signOutAccount } from '../api/auth'
import { ApiError, setUnauthorizedHandler } from '../api/client'
import { clearSession, readToken } from '../api/session'

/*
 * What AuthProvider keeps:
 *   status  — 'checking'    a token was saved in this tab; asking the backend who it belongs to
 *             'signedIn'    `user` is known
 *             'signedOut'   no token, or the backend refused it
 *             'unreachable' a token is saved but the backend could not be asked (offline, server down)
 *   user    — the signed-in person (see api/auth.js), or null
 *   problem — the message to show while 'unreachable'
 *   notice  — why the person is on the sign-in screen, when it isn't their first visit:
 *             'ended' (the sign-in ended by itself) or 'signedOut' (they signed out)
 */

const AuthContext = createContext(null)

function startState() {
  return { status: readToken() ? 'checking' : 'signedOut', user: null, problem: null, notice: null }
}

const signedInAs = (user) => ({ status: 'signedIn', user, problem: null, notice: null })

export function AuthProvider({ children }) {
  const [state, setState] = useState(startState)

  // Any later 401 (other than a wrong password) means the sign-in has ended. The client has
  // already forgotten the token; here the screens follow.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setState((previous) => ({
        status: 'signedOut',
        user: null,
        problem: null,
        notice: previous.status === 'signedIn' || previous.status === 'checking' ? 'ended' : previous.notice,
      }))
    })
    return () => setUnauthorizedHandler(null)
  }, [])

  // A token saved in this tab (the page was reloaded): find out whose it is.
  useEffect(() => {
    if (state.status !== 'checking') return
    const controller = new AbortController()
    currentUser(controller.signal).then(
      (user) => setState(signedInAs(user)),
      (error) => {
        if (controller.signal.aborted) return
        if (error instanceof ApiError && error.status === 401) return // the handler above has moved on
        if (error instanceof ApiError && error.status === 404) {
          // The account no longer exists.
          clearSession()
          setState({ status: 'signedOut', user: null, problem: null, notice: 'ended' })
          return
        }
        const problem = error instanceof ApiError ? error.message : String(error)
        setState({ status: 'unreachable', user: null, problem, notice: null })
      },
    )
    return () => controller.abort()
  }, [state.status])

  const signIn = useCallback(async (login, password) => {
    setState(signedInAs(await signInAccount(login, password)))
  }, [])

  const register = useCallback(async (displayName, login, password) => {
    setState(signedInAs(await registerAccount(displayName, login, password)))
  }, [])

  const signOut = useCallback(async () => {
    await signOutAccount()
    setState({ status: 'signedOut', user: null, problem: null, notice: 'signedOut' })
  }, [])

  // Ask the backend again after 'unreachable'.
  const retry = useCallback(() => {
    setState(readToken() ? { status: 'checking', user: null, problem: null, notice: null } : startState())
  }, [])

  const value = useMemo(
    () => ({ ...state, signIn, register, signOut, retry }),
    [state, signIn, register, signOut, retry],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

/** status, user, problem, notice, and signIn / register / signOut / retry. */
export function useAuth() {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside <AuthProvider>')
  return value
}

/** The signed-in person. Only for screens behind <RequireAuth>, where there always is one. */
export function useUser() {
  const { user } = useAuth()
  if (!user) throw new Error('useUser must be used on a screen behind <RequireAuth>')
  return user
}
