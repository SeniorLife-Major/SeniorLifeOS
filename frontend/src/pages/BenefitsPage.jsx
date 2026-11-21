import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { screenSchemes } from '../api/benefits'
import { ApiError, MESSAGES } from '../api/client'
import { Button } from '../components/Button'
import { Icon } from '../components/Icon'
import { Screen } from '../components/Screen'
import { firstStep, groupSchemes, SCHEME_LOOK } from '../benefits/schemes'

const HOME = { to: '/', label: 'Home' }
const TITLE = 'Schemes for me'

// /benefits: every demo scheme checked against the person's confirmed papers (POST
// /benefits/checks with no scheme). Each scheme opens at /benefits/:schemeId.

export const DEMO_NOTE =
  'These are made-up schemes for this demonstration, not real government schemes. Nothing is sent to any office.'

function SchemeCard({ scheme }) {
  const look = SCHEME_LOOK[scheme.status] ?? SCHEME_LOOK.add_documents
  const step = scheme.status === 'likely' || scheme.status === 'not_eligible' ? null : firstStep(scheme)
  return (
    <li>
      <Link to={`/benefits/${encodeURIComponent(scheme.schemeId)}`} className="doc-card scheme-card">
        <span className="doc-card__text">
          <span className="doc-card__title">{scheme.name}</span>
          <span className={`chip chip--${look.kind}`}>{look.word}</span>
          {scheme.benefit && <span className="doc-card__detail">{scheme.benefit}</span>}
          {step && <span className="doc-card__detail scheme-card__step">{step}</span>}
        </span>
        <Icon name="chevronRight" size={28} />
      </Link>
    </li>
  )
}

export function BenefitsPage() {
  const [state, setState] = useState({ check: null, problem: null })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setState({ check: null, problem: null })
    screenSchemes(controller.signal).then(
      (check) => {
        if (!controller.signal.aborted) setState({ check, problem: null })
      },
      (error) => {
        if (controller.signal.aborted || error?.name === 'AbortError') return
        setState({ check: null, problem: error instanceof ApiError ? error.message : MESSAGES.serverProblem })
      },
    )
    return () => controller.abort()
  }, [attempt])

  const retry = useCallback(() => setAttempt((now) => now + 1), [])

  if (!state.check) {
    return (
      <Screen title={TITLE} back={HOME}>
        <h1 className="page-title">{TITLE}</h1>
        {state.problem ? (
          <>
            <p className="alert" role="alert">
              We could not look at your papers. {state.problem}
            </p>
            <Button onClick={retry}>Try again</Button>
          </>
        ) : (
          <p className="lead" role="status">
            Looking at your papers…
          </p>
        )}
      </Screen>
    )
  }

  const groups = groupSchemes(state.check.schemes)
  return (
    <Screen title={TITLE} back={HOME}>
      <div className="intro">
        <h1 className="page-title">{TITLE}</h1>
        <p className="lead">Pensions and health cover your papers may get you. Tap one to see why, and what to add.</p>
      </div>
      {state.check.notes.map((note) => (
        <p key={note} className="note scheme-note">
          <Icon name="alertCircle" size={24} />
          <span>{note}</span>
        </p>
      ))}
      {state.check.status !== 'screened' && groups.length === 0 && (
        <p className="alert" role="alert">
          The schemes could not be checked right now. Please try again later.
        </p>
      )}
      {groups.map((group) => (
        <section key={group.status} className="scheme-group" aria-labelledby={`group-${group.status}`}>
          <h2 id={`group-${group.status}`} className="panel__title">
            {group.title}
          </h2>
          <ul className="doc-list" aria-labelledby={`group-${group.status}`}>
            {group.schemes.map((scheme) => (
              <SchemeCard key={scheme.schemeId} scheme={scheme} />
            ))}
          </ul>
        </section>
      ))}
      <p className="fine-print">
        <Icon name="lock" size={18} /> Only papers you have checked and saved are used. {DEMO_NOTE}
      </p>
    </Screen>
  )
}
