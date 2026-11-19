import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { Button } from '../components/Button'
import { Screen } from '../components/Screen'
import { useAuth } from './AuthProvider'

const SIGN_IN_PATHS = ['/sign-in', '/register']

/** Where to go after signing in: the screen first asked for, if it is one of ours. */
export function returnPath(state) {
  const from = state?.from
  if (typeof from !== 'string' || !from.startsWith('/') || from.startsWith('//')) return '/'
  return SIGN_IN_PATHS.some((path) => from === path || from.startsWith(`${path}?`)) ? '/' : from
}

function CheckingScreen() {
  return (
    <Screen title="Opening">
      <p className="lead" role="status">
        Opening SeniorLife…
      </p>
    </Screen>
  )
}

function UnreachableScreen({ message, onRetry }) {
  return (
    <Screen title="Could not open">
      <h1 className="page-title">We could not open SeniorLife</h1>
      <p className="lead">{message}</p>
      <Button onClick={onRetry}>Try again</Button>
    </Screen>
  )
}

/** Wraps the screens that need a signed-in person; anyone else is sent to sign in. */
export function RequireAuth() {
  const { status, problem, retry } = useAuth()
  const location = useLocation()

  if (status === 'checking') return <CheckingScreen />
  if (status === 'unreachable') return <UnreachableScreen message={problem} onRetry={retry} />
  if (status === 'signedOut') {
    return <Navigate to="/sign-in" replace state={{ from: `${location.pathname}${location.search}` }} />
  }
  return <Outlet />
}

/** Wraps the sign-in and new-account screens; someone already signed in goes on to the app. */
export function SignedOutOnly({ children }) {
  const { status } = useAuth()
  const location = useLocation()

  if (status === 'checking') return <CheckingScreen />
  if (status === 'signedIn') return <Navigate to={returnPath(location.state)} replace />
  return children
}
