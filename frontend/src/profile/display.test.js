import { afterEach, describe, expect, it, vi } from 'vitest'
import { applyDisplay, DEFAULT_DISPLAY, rememberDisplay, rememberedDisplay } from './display'

const root = document.documentElement
const KEY = 'seniorlife.display'

describe('the display choices', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('puts the text size and the contrast on the whole page', () => {
    applyDisplay({ fontScale: 1.5, highContrast: true })
    expect(root.style.getPropertyValue('--font-scale')).toBe('1.5')
    expect(root.dataset.contrast).toBe('high')

    applyDisplay({ fontScale: 1, highContrast: false })
    expect(root.style.getPropertyValue('--font-scale')).toBe('1')
    expect(root.dataset.contrast).toBeUndefined()
  })

  it('uses the normal size for a value outside what the backend allows', () => {
    applyDisplay({ fontScale: 9, highContrast: true })
    expect(root.style.getPropertyValue('--font-scale')).toBe('1')
    expect(root.dataset.contrast).toBeUndefined()
  })

  it('remembers only the two display choices, for the next visit', () => {
    rememberDisplay({ fontScale: 1.25, highContrast: true, other: 'not kept' })

    expect(JSON.parse(window.localStorage.getItem(KEY))).toEqual({ fontScale: 1.25, highContrast: true })
    expect(rememberedDisplay()).toEqual({ fontScale: 1.25, highContrast: true })
  })

  it('opens at the normal size when nothing usable was remembered', () => {
    expect(rememberedDisplay()).toEqual(DEFAULT_DISPLAY)

    window.localStorage.setItem(KEY, 'not json')
    expect(rememberedDisplay()).toEqual(DEFAULT_DISPLAY)

    window.localStorage.setItem(KEY, JSON.stringify({ fontScale: 0.5, highContrast: false }))
    expect(rememberedDisplay()).toEqual(DEFAULT_DISPLAY)
  })

  it('keeps working when the browser refuses storage', () => {
    const blocked = () => {
      throw new DOMException('blocked', 'SecurityError')
    }
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(blocked)
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(blocked)

    expect(() => rememberDisplay({ fontScale: 1.5, highContrast: false })).not.toThrow()
    expect(rememberedDisplay()).toEqual(DEFAULT_DISPLAY)
  })
})
