import { useRef, useState } from 'react'
import { LOGIN_MAX, NAME_MAX, PASSWORD_MAX, PASSWORD_MIN } from '../api/auth'
import { ApiError, MESSAGES } from '../api/client'
import { useAuth } from '../auth/AuthProvider'
import { Button } from '../components/Button'
import { PasswordField, TextField } from '../components/Field'
import { Screen } from '../components/Screen'

export function RegisterPage() {
  const { register } = useAuth()
  const [name, setName] = useState('')
  const [login, setLogin] = useState('')
  const [password, setPassword] = useState('')
  const [errors, setErrors] = useState({}) // { name, login, password }: the message under each box
  const [problem, setProblem] = useState(null)
  const [busy, setBusy] = useState(false)
  const nameBox = useRef(null)
  const loginBox = useRef(null)
  const passwordBox = useRef(null)

  async function submit(event) {
    event.preventDefault()
    if (busy) return

    const found = {
      name: name.trim() ? undefined : 'Please enter your name.',
      login: login.trim() ? undefined : 'Please enter your mobile number or email.',
      password:
        password.length >= PASSWORD_MIN ? undefined : `The password must be at least ${PASSWORD_MIN} characters.`,
    }
    setErrors(found)
    setProblem(null)
    if (found.name) return nameBox.current?.focus()
    if (found.login) return loginBox.current?.focus()
    if (found.password) return passwordBox.current?.focus()

    setBusy(true)
    try {
      await register(name, login, password) // on success this screen gives way to the app
    } catch (error) {
      setBusy(false)
      setProblem(error instanceof ApiError ? error.message : MESSAGES.serverProblem)
    }
  }

  return (
    <Screen title="Create an account" back={{ to: '/sign-in', label: 'Sign in' }}>
      <div className="intro">
        <h1 className="page-title">Create your account</h1>
        <p className="lead">Next time you will sign in with your mobile number or email and this password.</p>
      </div>

      <form className="form" noValidate onSubmit={submit}>
        <TextField
          id="name"
          label="Your name"
          hint="We will greet you by this name."
          ref={nameBox}
          value={name}
          onChange={(event) => setName(event.target.value)}
          error={errors.name}
          autoComplete="name"
          maxLength={NAME_MAX}
        />
        <TextField
          id="login"
          label="Mobile number or email"
          hint="A 10-digit mobile number or an email address."
          ref={loginBox}
          value={login}
          onChange={(event) => setLogin(event.target.value)}
          error={errors.login}
          autoComplete="username"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          maxLength={LOGIN_MAX}
        />
        <PasswordField
          id="password"
          label="Password"
          hint={`At least ${PASSWORD_MIN} letters, numbers or signs.`}
          ref={passwordBox}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          error={errors.password}
          autoComplete="new-password"
          maxLength={PASSWORD_MAX}
        />

        {problem && (
          <p className="alert" role="alert">
            {problem}
          </p>
        )}

        <Button type="submit" disabled={busy}>
          {busy ? 'Creating your account…' : 'Create account'}
        </Button>
      </form>
    </Screen>
  )
}
