import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth, useUser } from '../auth/AuthProvider'
import { Button } from '../components/Button'
import { Icon } from '../components/Icon'
import { Screen } from '../components/Screen'

export function HomePage() {
  const user = useUser()
  const { signOut } = useAuth()
  const [signingOut, setSigningOut] = useState(false)

  async function leave() {
    setSigningOut(true)
    await signOut() // never fails here; afterwards the sign-in screen takes over
  }

  return (
    <Screen title="Home">
      <div className="home__top">
        <div>
          <p className="home__hello" lang="hi">
            नमस्ते,
          </p>
          <h1 className="page-title">{user.displayName}</h1>
        </div>
        <Link to="/settings" className="icon-button" aria-label="Settings">
          <Icon name="settings" size={28} />
        </Link>
      </div>

      <Link to="/documents" className="tile tile--primary">
        <span className="tile__icon">
          <Icon name="document" size={36} strokeWidth={2} />
        </span>
        <span className="tile__text">
          <span className="tile__title">My documents</span>
          <span className="tile__hint">Your papers, kept locked and private</span>
        </span>
      </Link>

      <Link to="/doctor" className="tile tile--outline">
        <span className="tile__icon">
          <Icon name="stethoscope" size={36} strokeWidth={2} />
        </span>
        <span className="tile__text">
          <span className="tile__title">Find a doctor</span>
          <span className="tile__hint">Tell us what is wrong, in your own words</span>
        </span>
      </Link>

      <Button variant="quiet" className="button--muted" onClick={leave} disabled={signingOut}>
        {signingOut ? 'Signing out…' : 'Sign out'}
      </Button>
    </Screen>
  )
}
