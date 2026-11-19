import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

// The browser-tab icon (index.html and public/): read as files, since it is not part of the React app.
// Vitest runs in frontend/ (package.json).
const read = (path) => readFileSync(join(process.cwd(), path), 'utf8')

describe('the tab icon', () => {
  it('is linked from the page, and every linked file is there', () => {
    const page = new DOMParser().parseFromString(read('index.html'), 'text/html')
    const links = [...page.querySelectorAll('link[rel="icon"], link[rel="apple-touch-icon"]')].map((link) =>
      link.getAttribute('href'),
    )
    expect(links).toEqual(['/favicon.ico', '/favicon.svg', '/apple-touch-icon.png'])
    for (const href of links) expect(existsSync(join(process.cwd(), 'public', href)), href).toBe(true)
  })

  it("is the sign-in screen's mark: the shield icon on the app's blue", () => {
    const svg = read('public/favicon.svg')
    const icon = read('src/components/Icon.jsx')
    const shield = icon.match(/shieldCheck: \(\s*<>\s*<path d="([^"]+)"/)[1]
    expect(svg).toContain(`<path d="${shield}"/>`)
    expect(svg).toContain('<polyline points="9 12 11 14 15 10"/>')
    expect(svg.toLowerCase()).toContain(`fill="${read('index.html').match(/theme-color" content="(#\w+)"/)[1].toLowerCase()}"`)
    expect(read('src/components/Brand.jsx')).toContain('<Icon name="shieldCheck"')
  })
})
