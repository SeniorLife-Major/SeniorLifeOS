import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { App } from './App'

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

describe('App', () => {
  it('opens on the home screen with the two main choices and settings', () => {
    renderAt('/')

    expect(screen.getByRole('heading', { level: 1, name: 'Welcome' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /My documents/ })).toHaveAttribute('href', '/documents')
    expect(screen.getByRole('link', { name: /Find a doctor/ })).toHaveAttribute('href', '/doctor')
    expect(screen.getByRole('link', { name: 'Settings' })).toHaveAttribute('href', '/settings')
    expect(document.title).toBe('Home · SeniorLife')
  })

  it('offers the emergency number on the home screen and on an unknown page', () => {
    for (const path of ['/', '/no-such-page']) {
      const { unmount } = renderAt(path)
      expect(screen.getByRole('link', { name: 'Emergency? Call 112' })).toHaveAttribute('href', 'tel:112')
      unmount()
    }
  })

  it('explains an unknown address in plain words and leads back home', async () => {
    const user = userEvent.setup()
    renderAt('/no-such-page')

    expect(screen.getByRole('heading', { level: 1, name: 'We could not find that page' })).toBeInTheDocument()
    expect(document.title).toBe('Page not found · SeniorLife')

    await user.click(screen.getByRole('link', { name: 'Go to Home' }))
    expect(screen.getByRole('heading', { level: 1, name: 'Welcome' })).toBeInTheDocument()
  })
})
