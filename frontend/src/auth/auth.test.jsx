import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import { api, MESSAGES } from '../api/client'
import { readToken, saveSession } from '../api/session'
import { SignInPage } from '../pages/SignInPage'
import { fail, fakeBackend, MEERA, ok, session } from '../test/fakeBackend'
import { AuthProvider } from './AuthProvider'
import { RequireAuth, returnPath, SignedOutOnly } from './RequireAuth'

function renderApp(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

/** As if the page was reloaded after signing in: a token is saved and the backend knows it. */
function alreadySignedIn() {
  saveSession('token-meera', 3600)
  return fakeBackend({ 'GET /api/v1/auth/me': () => ok(MEERA) })
}

async function fillSignIn(user, login, password) {
  await user.type(screen.getByLabelText('Mobile number or email'), login)
  await user.type(screen.getByLabelText('Password'), password)
  await user.click(screen.getByRole('button', { name: 'Sign in' }))
}

describe('signing in', () => {
  it('sends someone who is not signed in to the sign-in screen', () => {
    fakeBackend()
    renderApp('/')
    expect(screen.getByRole('heading', { level: 1, name: 'Welcome back' })).toBeInTheDocument()
    expect(document.title).toBe('Sign in · SeniorLife')
  })

  it('signs in, keeps the token for this tab only, and opens Home with their name', async () => {
    const user = userEvent.setup()
    const backend = fakeBackend({ 'POST /api/v1/auth/login': () => ok(session()) })
    renderApp('/')

    await fillSignIn(user, '98765 43210', 'right password')

    expect(await screen.findByRole('heading', { level: 1, name: 'Meera' })).toBeInTheDocument()
    expect(backend.sent('POST /api/v1/auth/login')[0].body).toEqual({ login: '98765 43210', password: 'right password' })
    expect(readToken()).toBe('token-meera')
    expect(window.localStorage.length).toBe(0)
  })

  it('asks for both boxes before sending anything', async () => {
    const user = userEvent.setup()
    const backend = fakeBackend()
    renderApp('/sign-in')

    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(screen.getByText('Please enter your mobile number or email.')).toBeInTheDocument()
    expect(screen.getByText('Please enter your password.')).toBeInTheDocument()
    expect(screen.getByLabelText('Mobile number or email')).toHaveFocus()
    expect(screen.getByLabelText('Mobile number or email')).toHaveAttribute('aria-invalid', 'true')
    expect(backend.requests).toHaveLength(0)
  })

  it("shows the backend's message for a wrong password and stays on the screen", async () => {
    const user = userEvent.setup()
    fakeBackend({ 'POST /api/v1/auth/login': () => fail(401, 'INVALID_CREDENTIALS', 'The login or password is wrong') })
    renderApp('/sign-in')

    await fillSignIn(user, 'meera@example.com', 'wrong password')

    expect(await screen.findByRole('alert')).toHaveTextContent('The login or password is wrong')
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeEnabled()
    expect(screen.getByRole('heading', { level: 1, name: 'Welcome back' })).toBeInTheDocument()
    expect(readToken()).toBeNull()
  })

  it('switches the button off for as long as the backend says to wait after too many tries', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const backend = fakeBackend({
      'POST /api/v1/auth/login': () =>
        fail(429, 'TOO_MANY_REQUESTS', 'Too many sign-in attempts. Please wait 15 minutes and try again.', {
          retryAfterSeconds: 900,
        }),
    })
    renderApp('/sign-in')

    await fillSignIn(user, 'meera@example.com', 'guess')

    expect(await screen.findByRole('alert')).toHaveTextContent('Please wait 15 minutes')
    const button = screen.getByRole('button', { name: 'Please wait' })
    expect(button).toBeDisabled()
    await user.click(button)
    expect(backend.sent('POST /api/v1/auth/login')).toHaveLength(1)

    act(() => {
      vi.advanceTimersByTime(899_000)
    })
    expect(screen.getByRole('button', { name: 'Please wait' })).toBeDisabled()

    act(() => {
      vi.advanceTimersByTime(1_000)
    })
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeEnabled()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('says so plainly when SeniorLife cannot be reached', async () => {
    const user = userEvent.setup()
    fakeBackend({
      'POST /api/v1/auth/login': () => {
        throw new TypeError('Failed to fetch')
      },
    })
    renderApp('/sign-in')

    await fillSignIn(user, 'meera@example.com', 'right password')
    expect(await screen.findByRole('alert')).toHaveTextContent(MESSAGES.offline)
  })

  it('goes back to the screen first asked for after signing in', async () => {
    const user = userEvent.setup()
    fakeBackend({ 'POST /api/v1/auth/login': () => ok(session()) })
    render(
      <MemoryRouter initialEntries={['/papers?type=aadhaar']}>
        <AuthProvider>
          <Routes>
            <Route
              path="/sign-in"
              element={
                <SignedOutOnly>
                  <SignInPage />
                </SignedOutOnly>
              }
            />
            <Route element={<RequireAuth />}>
              <Route path="/papers" element={<p>The papers screen</p>} />
            </Route>
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    )

    await fillSignIn(user, 'meera@example.com', 'right password')
    expect(await screen.findByText('The papers screen')).toBeInTheDocument()
  })

  it('sends someone already signed in from the sign-in screen to Home', async () => {
    alreadySignedIn()
    renderApp('/sign-in')
    expect(await screen.findByRole('heading', { level: 1, name: 'Meera' })).toBeInTheDocument()
  })
})

describe('returnPath', () => {
  it('only returns to addresses inside the app, and never to the sign-in screens', () => {
    expect(returnPath({ from: '/documents?type=pan' })).toBe('/documents?type=pan')
    expect(returnPath({ from: '//evil.example' })).toBe('/')
    expect(returnPath({ from: 'https://evil.example' })).toBe('/')
    expect(returnPath({ from: '/sign-in' })).toBe('/')
    expect(returnPath({ from: '/register?x=1' })).toBe('/')
    expect(returnPath(null)).toBe('/')
    expect(returnPath({ from: 42 })).toBe('/')
  })
})

describe('creating an account', () => {
  it('is reached from the sign-in screen and leads back to it', async () => {
    const user = userEvent.setup()
    fakeBackend()
    renderApp('/sign-in')

    await user.click(screen.getByRole('link', { name: 'New here? Create an account' }))
    expect(screen.getByRole('heading', { level: 1, name: 'Create your account' })).toBeInTheDocument()

    await user.click(screen.getByRole('link', { name: 'Sign in' }))
    expect(screen.getByRole('heading', { level: 1, name: 'Welcome back' })).toBeInTheDocument()
  })

  it('checks every box first, including the password length', async () => {
    const user = userEvent.setup()
    const backend = fakeBackend()
    renderApp('/register')

    await user.click(screen.getByRole('button', { name: 'Create account' }))
    expect(screen.getByText('Please enter your name.')).toBeInTheDocument()
    expect(screen.getByText('Please enter your mobile number or email.')).toBeInTheDocument()
    expect(screen.getByText('The password must be at least 8 characters.')).toBeInTheDocument()
    expect(screen.getByLabelText('Your name')).toHaveFocus()

    await user.type(screen.getByLabelText('Your name'), 'Meera')
    await user.type(screen.getByLabelText('Mobile number or email'), '9876543210')
    await user.type(screen.getByLabelText('Password'), '1234567')
    await user.click(screen.getByRole('button', { name: 'Create account' }))
    expect(screen.getByText('The password must be at least 8 characters.')).toBeInTheDocument()
    expect(screen.queryByText('Please enter your name.')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Password')).toHaveFocus()
    expect(backend.requests).toHaveLength(0)
  })

  it('makes a senior account and opens Home', async () => {
    const user = userEvent.setup()
    const backend = fakeBackend({ 'POST /api/v1/auth/register': () => ok(session(), 201) })
    renderApp('/register')

    await user.type(screen.getByLabelText('Your name'), 'Meera')
    await user.type(screen.getByLabelText('Mobile number or email'), '9876543210')
    await user.type(screen.getByLabelText('Password'), '12345678')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Meera' })).toBeInTheDocument()
    expect(backend.sent('POST /api/v1/auth/register')[0].body).toEqual({
      displayName: 'Meera',
      login: '9876543210',
      password: '12345678',
      role: 'senior',
    })
    expect(readToken()).toBe('token-meera')
  })

  it("shows the backend's message when the login is taken", async () => {
    const user = userEvent.setup()
    fakeBackend({
      'POST /api/v1/auth/register': () => fail(409, 'LOGIN_TAKEN', 'An account with this login already exists'),
    })
    renderApp('/register')

    await user.type(screen.getByLabelText('Your name'), 'Meera')
    await user.type(screen.getByLabelText('Mobile number or email'), '9876543210')
    await user.type(screen.getByLabelText('Password'), '12345678')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('An account with this login already exists')
    expect(screen.getByRole('button', { name: 'Create account' })).toBeEnabled()
  })
})

describe('coming back to the app', () => {
  it('asks the backend whose saved token it is, then opens Home', async () => {
    const backend = alreadySignedIn()
    renderApp('/')

    expect(screen.getByRole('status')).toHaveTextContent('Opening SeniorLife…')
    expect(await screen.findByRole('heading', { level: 1, name: 'Meera' })).toBeInTheDocument()
    expect(backend.sent('GET /api/v1/auth/me')[0].headers.get('Authorization')).toBe('Bearer token-meera')
  })

  it('asks to sign in again when the backend no longer accepts the token', async () => {
    saveSession('old-token', 3600)
    fakeBackend({
      'GET /api/v1/auth/me': () =>
        fail(401, 'UNAUTHORIZED', 'Your sign-in has ended or is not valid; please sign in again'),
    })
    renderApp('/')

    expect(await screen.findByRole('heading', { level: 1, name: 'Welcome back' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Your sign-in has ended. Please sign in again.')
    expect(readToken()).toBeNull()
  })

  it('offers to try again when the backend cannot be reached, without signing out', async () => {
    const user = userEvent.setup()
    saveSession('token-meera', 3600)
    let tries = 0
    fakeBackend({
      'GET /api/v1/auth/me': () => {
        tries += 1
        if (tries === 1) throw new TypeError('Failed to fetch')
        return ok(MEERA)
      },
    })
    renderApp('/')

    expect(await screen.findByRole('heading', { level: 1, name: 'We could not open SeniorLife' })).toBeInTheDocument()
    expect(screen.getByText(MESSAGES.offline)).toBeInTheDocument()
    expect(readToken()).toBe('token-meera')

    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Meera' })).toBeInTheDocument()
  })
})

describe('signing out', () => {
  it('ends the sign-in on the backend and here, and says so', async () => {
    const user = userEvent.setup()
    const backend = alreadySignedIn()
    backend.on('POST /api/v1/auth/logout', () => ok({ signedOut: true }))
    renderApp('/')

    await user.click(await screen.findByRole('button', { name: 'Sign out' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Welcome back' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('You have signed out.')
    expect(backend.sent('POST /api/v1/auth/logout')[0].headers.get('Authorization')).toBe('Bearer token-meera')
    expect(readToken()).toBeNull()
  })

  it('still signs out on this phone when the backend cannot be reached', async () => {
    const user = userEvent.setup()
    const backend = alreadySignedIn()
    backend.on('POST /api/v1/auth/logout', () => {
      throw new TypeError('Failed to fetch')
    })
    renderApp('/')

    await user.click(await screen.findByRole('button', { name: 'Sign out' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Welcome back' })).toBeInTheDocument()
    expect(readToken()).toBeNull()
  })

  it('moves to the sign-in screen when the sign-in ends while the app is open', async () => {
    const backend = alreadySignedIn()
    renderApp('/')
    await screen.findByRole('heading', { level: 1, name: 'Meera' })

    backend.on('GET /api/v1/auth/me', () =>
      fail(401, 'UNAUTHORIZED', 'Your sign-in has ended or is not valid; please sign in again'),
    )
    await act(async () => {
      await api('/auth/me').catch(() => undefined)
    })

    expect(screen.getByRole('heading', { level: 1, name: 'Welcome back' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Your sign-in has ended. Please sign in again.')
  })
})
