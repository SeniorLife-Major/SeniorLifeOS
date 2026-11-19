import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { App } from '../App'
import { MESSAGES } from '../api/client'
import { saveSession } from '../api/session'
import { fakeBackend, makeProfile, MEERA, ok, serveProfile } from '../test/fakeBackend'

const root = document.documentElement
const fontScale = () => root.style.getPropertyValue('--font-scale')
const PATCH = 'PATCH /api/v1/me/profile'

function signedInWith(profile) {
  saveSession('token-meera', 3600)
  const backend = fakeBackend({ 'GET /api/v1/auth/me': () => ok(MEERA) })
  const store = serveProfile(backend, profile)
  return { backend, store }
}

function renderApp(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

async function openSettings(profile = makeProfile()) {
  const setup = signedInWith(profile)
  renderApp('/settings')
  await screen.findByRole('heading', { level: 2, name: 'Text size' })
  return setup
}

describe('the saved display choices', () => {
  it('apply on every screen and are remembered for next time', async () => {
    signedInWith(makeProfile({ accessibility: { fontScale: 1.5, highContrast: true, voice: false } }))
    renderApp('/')

    await screen.findByRole('heading', { level: 1, name: 'Meera' })
    await waitFor(() => expect(fontScale()).toBe('1.5'))
    expect(root.dataset.contrast).toBe('high')
    expect(JSON.parse(window.localStorage.getItem('seniorlife.display'))).toEqual({ fontScale: 1.5, highContrast: true })
  })
})

describe('Settings', () => {
  it('is reached from Home and leads back to it', async () => {
    const user = userEvent.setup()
    signedInWith(makeProfile())
    renderApp('/')

    await user.click(await screen.findByRole('link', { name: 'Settings' }))
    expect(screen.getByRole('heading', { level: 1, name: 'Settings' })).toBeInTheDocument()
    expect(document.title).toBe('Settings · SeniorLife')

    await user.click(screen.getByRole('link', { name: 'Home' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Meera' })).toBeInTheDocument()
  })

  it('shows the saved choices', async () => {
    await openSettings(
      makeProfile({
        homeLocation: { label: 'Andheri West, Mumbai', lat: 19.136, lon: 72.827 },
        accessibility: { fontScale: 1.25, highContrast: true, voice: false },
        consents: { useLlm: true, medicalCloud: false, cloudOcr: true },
      }),
    )

    expect(screen.getByRole('button', { name: 'A normal' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('button', { name: 'A+ larger' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'A++ largest' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('switch', { name: 'Stronger contrast' })).toBeChecked()
    expect(screen.getByText('Andheri West, Mumbai')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Change my home' })).toHaveAttribute('href', '/settings/home')
    expect(screen.getByRole('switch', { name: 'Read my document photos with a cloud service' })).toBeChecked()
    expect(screen.getByRole('switch', { name: 'My health papers may go to a cloud AI' })).not.toBeChecked()
    expect(screen.getByRole('switch', { name: 'An AI may read the text of my papers' })).toBeChecked()
    // The switches are for documents; doctor search always uses the AI (Decision 73), and it says so.
    expect(
      screen.getByText('Find a doctor always uses an AI service to understand the problem you describe. Your name is hidden from it.'),
    ).toBeInTheDocument()
  })

  it('says when no home is set yet', async () => {
    await openSettings(makeProfile({ homeLocation: null }))
    expect(screen.getByText('Not set yet')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Add my home' })).toHaveAttribute('href', '/settings/home')
  })

  it('names a home set from the phone without a place name', async () => {
    await openSettings(makeProfile({ homeLocation: { label: null, lat: 19.12, lon: 72.847 } }))
    expect(screen.getByText("Set from your phone's location")).toBeInTheDocument()
  })

  it('changes the text size at once, then saves it', async () => {
    const user = userEvent.setup()
    const { backend, store } = await openSettings()
    const held = store.hold()

    await user.click(screen.getByRole('button', { name: 'A++ largest' }))

    // Before the backend has answered:
    expect(fontScale()).toBe('1.5')
    expect(screen.getByRole('button', { name: 'A++ largest' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('status')).toHaveTextContent('Saving…')

    held.resolve()
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Saved.'))
    expect(backend.sent(PATCH).map((request) => request.body)).toEqual([{ accessibility: { fontScale: 1.5 } }])
    expect(store.current.accessibility.fontScale).toBe(1.5)
  })

  it('turns on stronger contrast for every screen', async () => {
    const user = userEvent.setup()
    const { backend } = await openSettings()

    await user.click(screen.getByRole('switch', { name: 'Stronger contrast' }))

    expect(screen.getByRole('switch', { name: 'Stronger contrast' })).toBeChecked()
    expect(root.dataset.contrast).toBe('high')
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Saved.'))
    expect(backend.sent(PATCH)[0].body).toEqual({ accessibility: { highContrast: true } })
  })

  it('saves each AI choice on its own', async () => {
    const user = userEvent.setup()
    const { backend, store } = await openSettings()

    await user.click(screen.getByRole('switch', { name: 'Read my document photos with a cloud service' }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Saved.'))
    await user.click(screen.getByRole('switch', { name: 'An AI may read the text of my papers' }))
    await waitFor(() => expect(backend.sent(PATCH)).toHaveLength(2))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Saved.'))

    expect(backend.sent(PATCH).map((request) => request.body)).toEqual([
      { consents: { cloudOcr: true } },
      { consents: { useLlm: true } },
    ])
    expect(store.current.consents).toEqual({ useLlm: true, medicalCloud: false, cloudOcr: true })
    expect(screen.getByRole('switch', { name: 'My health papers may go to a cloud AI' })).not.toBeChecked()
  })

  it('puts a change back and says so when it cannot be saved', async () => {
    const user = userEvent.setup()
    const { backend } = await openSettings()
    backend.on(PATCH, () => {
      throw new TypeError('Failed to fetch')
    })

    await user.click(screen.getByRole('switch', { name: 'Stronger contrast' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(`Not saved. ${MESSAGES.offline}`)
    expect(screen.getByRole('switch', { name: 'Stronger contrast' })).not.toBeChecked()
    expect(root.dataset.contrast).toBeUndefined()

    await user.click(screen.getByRole('button', { name: 'A++ largest' }))
    await waitFor(() => expect(backend.sent(PATCH)).toHaveLength(2))
    await waitFor(() => expect(screen.getByRole('button', { name: 'A normal' })).toHaveAttribute('aria-pressed', 'true'))
    expect(fontScale()).toBe('1')
    expect(screen.getByRole('alert')).toHaveTextContent(`Not saved. ${MESSAGES.offline}`)
  })

  it('sends quick changes one at a time, and the newest one wins', async () => {
    const user = userEvent.setup()
    const { backend, store } = await openSettings()
    const first = store.hold()

    await user.click(screen.getByRole('button', { name: 'A+ larger' }))
    await user.click(screen.getByRole('button', { name: 'A++ largest' }))

    expect(backend.sent(PATCH)).toHaveLength(1) // the second waits for the first
    expect(fontScale()).toBe('1.5')

    first.resolve()
    await waitFor(() => expect(backend.sent(PATCH)).toHaveLength(2))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Saved.'))
    expect(store.current.accessibility.fontScale).toBe(1.5)
    expect(fontScale()).toBe('1.5')
    expect(screen.getByRole('button', { name: 'A++ largest' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('offers to try again when the settings cannot be loaded', async () => {
    const user = userEvent.setup()
    saveSession('token-meera', 3600)
    let tries = 0
    fakeBackend({
      'GET /api/v1/auth/me': () => ok(MEERA),
      'GET /api/v1/me/profile': () => {
        tries += 1
        if (tries === 1) throw new TypeError('Failed to fetch')
        return ok(makeProfile())
      },
    })
    renderApp('/settings')

    expect(await screen.findByRole('alert')).toHaveTextContent(`We could not load your settings. ${MESSAGES.offline}`)
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('heading', { level: 2, name: 'Text size' })).toBeInTheDocument()
  })
})
