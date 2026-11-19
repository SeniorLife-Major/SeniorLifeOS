import { afterEach, describe, expect, it, vi } from 'vitest'
import { clearSession, readToken, saveSession } from './session'

const KEY = 'seniorlife.session'

describe('the saved sign-in', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('keeps the token in sessionStorage and never in localStorage', () => {
    saveSession('abc', 60, 1_000)

    expect(readToken(1_000)).toBe('abc')
    expect(JSON.parse(window.sessionStorage.getItem(KEY) ?? '{}')).toEqual({ token: 'abc', expiresAt: 61_000 })
    expect(window.localStorage.length).toBe(0)
  })

  it('stops giving the token once it has run out, and removes it', () => {
    saveSession('abc', 60, 1_000)

    expect(readToken(60_999)).toBe('abc')
    expect(readToken(61_000)).toBeNull()
    expect(window.sessionStorage.getItem(KEY)).toBeNull()
  })

  it('is gone after signing out', () => {
    saveSession('abc', 60)
    clearSession()

    expect(readToken()).toBeNull()
    expect(window.sessionStorage.getItem(KEY)).toBeNull()
  })

  it('treats a broken saved value as signed out and removes it', () => {
    window.sessionStorage.setItem(KEY, 'not json')
    expect(readToken()).toBeNull()
    expect(window.sessionStorage.getItem(KEY)).toBeNull()

    window.sessionStorage.setItem(KEY, JSON.stringify({ token: 42 }))
    expect(readToken()).toBeNull()
    expect(window.sessionStorage.getItem(KEY)).toBeNull()
  })

  it('reads a token saved earlier in this tab (after a reload)', () => {
    window.sessionStorage.setItem(KEY, JSON.stringify({ token: 'from-before', expiresAt: Date.now() + 60_000 }))
    expect(readToken()).toBe('from-before')
  })

  it('still works for this page when the browser refuses to store it', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError')
    })

    saveSession('kept-in-memory', 60)
    expect(readToken()).toBe('kept-in-memory')
  })
})
