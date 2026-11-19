import { ButtonLink } from '../components/Button'
import { FocusedTitle } from '../components/FocusedTitle'
import { Icon } from '../components/Icon'
import { Screen } from '../components/Screen'
import { CallButtons } from './CallButtons'
import { isSelfHarm, signsOf } from './search'

/** The emergency screen (the mockup's Emergency): call now, the numbers, the signs noticed. */
export function EmergencyView({ emergency }) {
  const selfHarm = isSelfHarm(emergency)
  const signs = signsOf(emergency)
  const heading = selfHarm ? 'You do not have to face this alone' : 'This may be an emergency'
  return (
    <Screen title="Emergency" back={{ to: '/', label: 'Home' }} emergency={false} tone="emergency">
      <span className="emergency__icon">
        <Icon name="alertCircle" size={44} strokeWidth={2.6} />
      </span>
      <div className="intro">
        <FocusedTitle>{heading}</FocusedTitle>
        <p className="lead">
          {selfHarm
            ? 'Please talk to someone now. These calls are free, any time of day.'
            : (emergency?.message ?? 'Please call for help now.')}
        </p>
      </div>
      <CallButtons numbers={emergency?.numbers} />
      {signs.length > 0 && (
        <section className="emergency__signs" aria-labelledby="signs">
          <h2 id="signs" className="emergency__signs-title">
            Signs we noticed
          </h2>
          <ul>
            {signs.map((sign) => (
              <li key={sign}>{sign}</li>
            ))}
          </ul>
        </section>
      )}
      <p className="emergency__stay">If someone is with you, ask them to stay with you.</p>
      {/* The backend's rules flag these signs whenever they are named, even with "no", so the same
          words would bring this screen back (Decision 64). Not shown for self-harm: there the
          person is not asked to change their words. */}
      {!selfHarm && (
        <p className="emergency__stay">
          If you are safe and need a doctor for something else, describe that other problem. Naming these signs
          again, even with &quot;no&quot;, brings back this screen.
        </p>
      )}
      <ButtonLink to="/doctor" variant="secondary">
        I am safe now. Find a doctor instead
      </ButtonLink>
    </Screen>
  )
}
