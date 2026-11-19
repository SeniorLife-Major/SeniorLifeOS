import { Button } from '../components/Button'
import { useProfile } from './ProfileProvider'

/** What a settings screen shows until the profile is there: "Loading…", or the problem and Try again. */
export function ProfileStatus() {
  const { status, problem, reload } = useProfile()

  if (status === 'failed') {
    return (
      <>
        <p className="alert" role="alert">
          We could not load your settings. {problem}
        </p>
        <Button onClick={reload}>Try again</Button>
      </>
    )
  }
  return (
    <p className="lead" role="status">
      Loading your settings…
    </p>
  )
}
