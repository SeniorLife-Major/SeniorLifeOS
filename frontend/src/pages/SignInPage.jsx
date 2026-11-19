import { useRef, useState } from 'react'
import { LOGIN_MAX, PASSWORD_MAX } from '../api/auth'
import { ApiError, MESSAGES } from '../api/client'
import { useAuth } from '../auth/AuthProvider'
import { Brand } from '../components/Brand'
import { Button, ButtonLink } from '../components/Button'
import { PasswordField, TextField } from '../components/Field'
import { Icon } from '../components/Icon'
import { Screen } from '../components/Screen'
import { useWait } from '../hooks/useWait'

// The notice from AuthProvider, in words.
const NOTICES = {
  ended: 'Your sign-in has ended. Please sign in again.',
  signedOut: 'You have signed out.',
}

export function SignInPage() {
  const { signIn, notice } = useAuth()
  const { waiting, waitFor } = useWait()
  const [login, setLogin] = useState('')
  const [password, setPassword] = useState('')
  const [errors, setErrors] = useState({}) // { login, password }: the message under each box
  const [problem, setProblem] = useState(null)
  const [waitMessage, setWaitMessage] = useState(null)
  const [busy, setBusy] = useState(false)
  const loginBox = useRef(null)
  const passwordBox = useRef(null)

  async function submit(event) {
    event.preventDefault()
    if (busy || waiting) return

    const found = {
      login: login.trim() ? undefined : 'Please enter your mobile number or email.',
      password: password ? undefined : 'Please enter your password.',
    }
    setErrors(found)
    setProblem(null)
    if (found.login) return loginBox.current?.focus()
    if (found.password) return passwordBox.current?.focus()

    setBusy(true)
    try {
      await signIn(login, password) // on success this screen gives way to the app
    } catch (error) {
      setBusy(false)
      if (error instanceof ApiError && error.retryAfterSeconds !== undefined) {
        setWaitMessage(error.message)
        waitFor(error.retryAfterSeconds)
      } else {
        setProblem(error instanceof ApiError ? error.message : MESSAGES.serverProblem)
      }
    }
  }

  const alert = waiting ? waitMessage : problem

  return (
    <Screen title="Sign in">
      <Brand />

      <div className="intro">
        <p className="home__hello" lang="hi">
          नमस्ते
        </p>
        <h1 className="page-title">Welcome back</h1>
        <p className="lead">Sign in to see your documents and find a doctor.</p>
      </div>

      {notice && !alert && (
        <p className="notice" role="status">
          {NOTICES[notice]}
        </p>
      )}

      <form className="form" noValidate onSubmit={submit}>
        <TextField
          id="login"
          label="Mobile number or email"
          ref={loginBox}
          value={login}
          onChange={(event) => setLogin(event.target.value)}
          error={errors.login}
          autoComplete="username"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          maxLength={LOGIN_MAX}
          placeholder="98765 43210"
        />
        <PasswordField
          id="password"
          label="Password"
          ref={passwordBox}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          error={errors.password}
          autoComplete="current-password"
          maxLength={PASSWORD_MAX}
          placeholder="Your password"
        />

        {alert && (
          <p className="alert" role="alert">
            {alert}
          </p>
        )}

        <div className="actions">
          <Button type="submit" disabled={busy || waiting}>
            {busy ? 'Signing in…' : waiting ? 'Please wait' : 'Sign in'}
          </Button>
          <ButtonLink to="/register" variant="secondary">
            New here? Create an account
          </ButtonLink>
        </div>
      </form>

      <div className="note">
        <span className="note__icon">
          <Icon name="lock" />
        </span>
        <p>After a few wrong tries we ask you to wait a little. This keeps your account safe.</p>
      </div>
    </Screen>
  )
}
