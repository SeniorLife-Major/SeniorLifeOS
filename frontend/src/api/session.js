// The sign-in token. It lives in sessionStorage, so it is gone when the tab or browser closes, and
// never in localStorage (Decision 10). If the browser refuses storage (private mode, blocked site
// data), the token is kept in memory only and the person signs in again after a reload.

const KEY = 'seniorlife.session'

// The saved value: { token, expiresAt }, where expiresAt is in milliseconds since 1970, as Date.now().
let memory = null

function storage() {
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

export function saveSession(token, expiresInSeconds, now = Date.now()) {
  memory = { token, expiresAt: now + expiresInSeconds * 1000 }
  try {
    storage()?.setItem(KEY, JSON.stringify(memory))
  } catch {
    // Storage full or blocked: the copy in memory is enough for this page.
  }
}

export function clearSession() {
  memory = null
  try {
    storage()?.removeItem(KEY)
  } catch {
    // Nothing stored, or storage blocked: nothing to remove.
  }
}

function stored() {
  if (memory) return memory
  try {
    const text = storage()?.getItem(KEY)
    if (!text) return null
    const value = JSON.parse(text)
    if (
      typeof value === 'object' &&
      value !== null &&
      typeof value.token === 'string' &&
      typeof value.expiresAt === 'number'
    ) {
      memory = value
      return memory
    }
  } catch {
    // Not readable: treated as signed out below.
  }
  return null
}

/** The token, or null when there is none or it has run out (an old or broken one is removed). */
export function readToken(now = Date.now()) {
  const session = stored()
  if (session && session.token && session.expiresAt > now) return session.token
  clearSession()
  return null
}
