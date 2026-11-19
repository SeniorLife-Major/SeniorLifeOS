import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Tests import describe/it/expect from vitest (no globals), so Testing Library can't clean up
// by itself: remove what each test rendered before the next one starts.
afterEach(() => {
  cleanup()
})
