import { api } from './client'
import { clearSession, saveSession } from './session'

// backend/api/auth.py
//   the person (UserDto):  { userId, login, displayName, role }   role is "senior" or "family"
//   a session (SessionDto): { user, accessToken, tokenType, expiresIn }   expiresIn is in seconds

/** The backend's own limits (backend/api/auth.py, backend/storage/accounts.py), checked here first
 *  so the message comes at once and names the field. */
export const PASSWORD_MIN = 8
export const PASSWORD_MAX = 128
export const NAME_MAX = 120
export const LOGIN_MAX = 254

// A new sign-in replaces any old token, so the old one is dropped before asking.

export async function signIn(login, password) {
  clearSession()
  const session = await api('/auth/login', { method: 'POST', body: { login, password } })
  saveSession(session.accessToken, session.expiresIn)
  return session.user
}

/** Senior Mode only makes senior accounts (Family Mode needs sharing in the backend first). */
export async function register(displayName, login, password) {
  clearSession()
  const session = await api('/auth/register', {
    method: 'POST',
    body: { displayName, login, password, role: 'senior' },
  })
  saveSession(session.accessToken, session.expiresIn)
  return session.user
}

/** Who the saved token belongs to. A token the backend no longer accepts gives a 401. */
export function currentUser(signal) {
  return api('/auth/me', { signal })
}

/** Ends the sign-in on the backend (every token of the account) and here. Here it always ends,
 *  even if the backend can't be reached. */
export async function signOut() {
  try {
    await api('/auth/logout', { method: 'POST' })
  } catch {
    // Offline or already signed out: forgetting the token here is what matters on this phone.
  } finally {
    clearSession()
  }
}
