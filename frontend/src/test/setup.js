import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'
import { clearSession } from '../api/session'

// Tests import describe/it/expect from vitest (no globals), so Testing Library can't clean up
// by itself: remove what each test rendered before the next one starts. Then undo the fake
// backend and fake clock, and forget any sign-in, so no test leans on the one before.
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
  clearSession()
  window.sessionStorage.clear()
  window.localStorage.clear()
})
