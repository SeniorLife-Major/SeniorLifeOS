import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { App } from './App'
import { saveSession } from './api/session'
import { fakeBackend, MEERA, ok } from './test/fakeBackend'

function renderAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

function signedIn() {
  saveSession('token-meera', 3600)
  fakeBackend({ 'GET /api/v1/auth/me': () => ok(MEERA) })
}

describe('App', () => {
  it('opens on the home screen with the two main choices and settings', async () => {
    signedIn()
    renderAt('/')

    expect(await screen.findByRole('heading', { level: 1, name: 'Meera' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /My documents/ })).toHaveAttribute('href', '/documents')
    expect(screen.getByRole('link', { name: /Find a doctor/ })).toHaveAttribute('href', '/doctor')
    expect(screen.getByRole('link', { name: 'Settings' })).toHaveAttribute('href', '/settings')
    expect(document.title).toBe('Home · SeniorLife')
  })

  it('offers the emergency number on every screen, signed in or not', async () => {
    fakeBackend()
    for (const path of ['/sign-in', '/register', '/no-such-page']) {
      const { unmount } = renderAt(path)
      expect(screen.getByRole('link', { name: 'Emergency? Call 112' })).toHaveAttribute('href', 'tel:112')
      unmount()
    }

    signedIn()
    renderAt('/')
    await screen.findByRole('heading', { level: 1, name: 'Meera' })
    expect(screen.getByRole('link', { name: 'Emergency? Call 112' })).toHaveAttribute('href', 'tel:112')
  })

  it('explains an unknown address in plain words and leads back home', async () => {
    const user = userEvent.setup()
    signedIn()
    renderAt('/no-such-page')

    expect(screen.getByRole('heading', { level: 1, name: 'We could not find that page' })).toBeInTheDocument()
    expect(document.title).toBe('Page not found · SeniorLife')

    await user.click(screen.getByRole('link', { name: 'Go to Home' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Meera' })).toBeInTheDocument()
  })
})
