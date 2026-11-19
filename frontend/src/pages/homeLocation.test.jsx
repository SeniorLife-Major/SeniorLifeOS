import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import { saveSession } from '../api/session'
import { fail, fakeBackend, makeProfile, MEERA, ok, serveProfile } from '../test/fakeBackend'

const PATCH = 'PATCH /api/v1/me/profile'
const ANDHERI = { label: 'Andheri West, Mumbai, Maharashtra', lat: 19.136, lon: 72.827 }
const RAMPUR = [
  { label: 'Rampur, Uttar Pradesh', lat: 28.81, lon: 79.026 },
  { label: 'Rampur, Himachal Pradesh', lat: 31.45, lon: 77.63 },
]

/** The phone's answer when asked where it is: { coords } or a refusal { code }. */
function phoneLocation(answer) {
  const geolocation = {
    getCurrentPosition: vi.fn((success, failure) => (answer.coords ? success({ coords: answer.coords }) : failure({ code: answer.code }))),
  }
  Object.defineProperty(navigator, 'geolocation', { value: geolocation, configurable: true })
  return geolocation
}

/** Opens "My home" from Settings, signed in with this profile. */
async function openMyHome(profile = makeProfile(), places = {}) {
  saveSession('token-meera', 3600)
  const backend = fakeBackend({ 'GET /api/v1/auth/me': () => ok(MEERA) })
  const store = serveProfile(backend, profile, places)
  render(
    <MemoryRouter initialEntries={['/settings', '/settings/home']} initialIndex={1}>
      <App />
    </MemoryRouter>,
  )
  await screen.findByLabelText('Area and city')
  return { backend, store, sent: () => backend.sent(PATCH).map((request) => request.body) }
}

describe('My home', () => {
  afterEach(() => {
    delete navigator.geolocation
  })

  it('saves a home typed as area and city, and goes back to Settings', async () => {
    const user = userEvent.setup()
    const { sent } = await openMyHome(makeProfile(), { 'Andheri West, Mumbai': ANDHERI })

    await user.type(screen.getByLabelText('Area and city'), 'Andheri West, Mumbai')
    await user.click(screen.getByRole('button', { name: 'Save this place' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Settings' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Your home is saved.')
    expect(screen.getByText(ANDHERI.label)).toBeInTheDocument()
    expect(sent()).toEqual([{ homeLocation: { place: 'Andheri West, Mumbai' } }])
  })

  it('asks for a place before sending anything', async () => {
    const user = userEvent.setup()
    const { sent } = await openMyHome()

    await user.click(screen.getByRole('button', { name: 'Save this place' }))

    expect(screen.getByText('Please type your area and city.')).toBeInTheDocument()
    expect(screen.getByLabelText('Area and city')).toHaveFocus()
    expect(sent()).toEqual([])
  })

  it('lets the person choose when a name fits more than one place', async () => {
    const user = userEvent.setup()
    const { sent } = await openMyHome(makeProfile(), { Rampur: RAMPUR })

    await user.type(screen.getByLabelText('Area and city'), 'Rampur')
    await user.click(screen.getByRole('button', { name: 'Save this place' }))
    expect(
      await screen.findByRole('heading', { level: 2, name: 'This name fits more than one place. Which one do you mean?' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Rampur, Uttar Pradesh' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Rampur, Himachal Pradesh' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Settings' })).toBeInTheDocument()
    expect(screen.getByText('Rampur, Himachal Pradesh')).toBeInTheDocument()
    expect(sent()).toEqual([
      { homeLocation: { place: 'Rampur' } },
      { homeLocation: { place: 'Rampur, Himachal Pradesh', lat: 31.45, lon: 77.63 } },
    ])
  })

  it('goes back to typing when none of the places is right', async () => {
    const user = userEvent.setup()
    await openMyHome(makeProfile(), { Rampur: RAMPUR })

    await user.type(screen.getByLabelText('Area and city'), 'Rampur')
    await user.click(screen.getByRole('button', { name: 'Save this place' }))
    await user.click(await screen.findByRole('button', { name: 'None of these' }))

    expect(screen.queryByRole('button', { name: 'Rampur, Uttar Pradesh' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Area and city')).toHaveFocus()
  })

  it("shows the backend's message when the place is not found", async () => {
    const user = userEvent.setup()
    await openMyHome()

    await user.type(screen.getByLabelText('Area and city'), 'Nowhere at all')
    await user.click(screen.getByRole('button', { name: 'Save this place' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This place was not found; add the city, or share your location',
    )
    expect(screen.getByRole('heading', { level: 1, name: 'My home' })).toBeInTheDocument()
  })

  it('says so when place search is not available', async () => {
    const user = userEvent.setup()
    const { backend } = await openMyHome()
    backend.on(PATCH, () =>
      fail(503, 'PLACE_SEARCH_UNAVAILABLE', 'Place search is not available right now; share your location instead'),
    )

    await user.type(screen.getByLabelText('Area and city'), 'Andheri West, Mumbai')
    await user.click(screen.getByRole('button', { name: 'Save this place' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Place search is not available right now; share your location instead',
    )
  })

  it("saves the phone's location", async () => {
    const user = userEvent.setup()
    const phone = phoneLocation({ coords: { latitude: 19.11972, longitude: 72.84681 } })
    const { sent } = await openMyHome()

    await user.click(screen.getByRole('button', { name: "Use my phone's location" }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Settings' })).toBeInTheDocument()
    expect(screen.getByText("Set from your phone's location")).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Your home is saved.')
    expect(sent()).toEqual([{ homeLocation: { lat: 19.11972, lon: 72.84681 } }])
    expect(phone.getCurrentPosition).toHaveBeenCalledWith(
      expect.any(Function),
      expect.any(Function),
      expect.objectContaining({ enableHighAccuracy: false }),
    )
  })

  it('explains when the phone does not share its location', async () => {
    const user = userEvent.setup()
    phoneLocation({ code: 1 })
    const { sent } = await openMyHome()

    await user.click(screen.getByRole('button', { name: "Use my phone's location" }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Your phone did not share its location. You can type your area and city instead.',
    )
    expect(screen.getByRole('button', { name: "Use my phone's location" })).toBeEnabled()
    expect(sent()).toEqual([])
  })

  it('explains when the phone cannot find where it is', async () => {
    const user = userEvent.setup()
    phoneLocation({ code: 3 })
    await openMyHome()

    await user.click(screen.getByRole('button', { name: "Use my phone's location" }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'We could not find where your phone is. Please try again, or type your area and city.',
    )
  })

  it('explains when the location is outside India', async () => {
    const user = userEvent.setup()
    phoneLocation({ coords: { latitude: 51.5, longitude: -0.12 } })
    const { backend } = await openMyHome()
    backend.on(PATCH, () => fail(422, 'VALIDATION_FAILED', 'The home location must be a point in India (lat and lon)'))

    await user.click(screen.getByRole('button', { name: "Use my phone's location" }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      "Your phone's location seems to be outside India. Please type your area and city instead.",
    )
  })

  it('removes the home', async () => {
    const user = userEvent.setup()
    const { sent } = await openMyHome(makeProfile({ homeLocation: ANDHERI }))
    expect(screen.getByText(ANDHERI.label)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Remove my home' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Settings' })).toBeInTheDocument()
    expect(screen.getByText('Not set yet')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Your home is removed.')
    expect(sent()).toEqual([{ homeLocation: null }])
  })

  it('does not offer the location button when the phone cannot share its location', async () => {
    await openMyHome()
    expect(screen.queryByRole('button', { name: "Use my phone's location" })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remove my home' })).not.toBeInTheDocument()
  })
})
