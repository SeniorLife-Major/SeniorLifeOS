import { ButtonLink } from '../components/Button'
import { Screen } from '../components/Screen'

export function NotFoundPage() {
  return (
    <Screen title="Page not found">
      <h1 className="page-title">We could not find that page</h1>
      <p className="lead">The link may be old, or this part of the app is not open yet.</p>
      <ButtonLink to="/">Go to Home</ButtonLink>
    </Screen>
  )
}
