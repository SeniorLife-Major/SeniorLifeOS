// Text size and stronger contrast, from the profile. They are put on <html> so every screen
// follows: --font-scale grows everything sized in rem (app.css), and data-contrast="high"
// switches to darker colours and thicker lines.
//
// A copy is kept in localStorage so the next visit opens at the right size straight away, even on
// the sign-in screen. It holds only these two display choices, nothing personal.

const KEY = 'seniorlife.display'

export const FONT_SCALE_MIN = 0.8
export const FONT_SCALE_MAX = 2
export const DEFAULT_DISPLAY = { fontScale: 1, highContrast: false }

function isDisplay(value) {
  return (
    typeof value?.fontScale === 'number' &&
    value.fontScale >= FONT_SCALE_MIN &&
    value.fontScale <= FONT_SCALE_MAX &&
    typeof value.highContrast === 'boolean'
  )
}

/** Shows every screen at this text size and contrast ({ fontScale, highContrast }). */
export function applyDisplay(display) {
  const shown = isDisplay(display) ? display : DEFAULT_DISPLAY
  const root = document.documentElement
  root.style.setProperty('--font-scale', String(shown.fontScale))
  if (shown.highContrast) root.dataset.contrast = 'high'
  else delete root.dataset.contrast
}

export function rememberDisplay(display) {
  if (!isDisplay(display)) return
  try {
    window.localStorage.setItem(KEY, JSON.stringify({ fontScale: display.fontScale, highContrast: display.highContrast }))
  } catch {
    // Storage blocked: the next visit opens at the normal size until the profile loads.
  }
}

/** The display choices from the last visit, or the normal ones. */
export function rememberedDisplay() {
  try {
    const value = JSON.parse(window.localStorage.getItem(KEY) ?? 'null')
    if (isDisplay(value)) return { fontScale: value.fontScale, highContrast: value.highContrast }
  } catch {
    // Unreadable: the normal ones below.
  }
  return DEFAULT_DISPLAY
}
