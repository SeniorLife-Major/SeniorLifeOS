import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ApiError, MESSAGES } from '../api/client'
import { listDocuments, matchProofs } from '../api/documents'
import { Button, ButtonLink } from '../components/Button'
import { Icon } from '../components/Icon'
import { Screen } from '../components/Screen'
import { proofOf, PROOFS, proofsFromQuery } from '../documents/proofs'
import { dateLine, formatDate, newCopyState } from '../documents/status'
import { typeLabel } from '../documents/types'

const BACK = { to: '/documents', label: 'My documents' }
const TITLE = 'Do I have what I need?'

// /documents/checklist: the person ticks what an office asked for; ?need=proof_of_age,… shows
// what they already have (POST /documents/match). ?change=1 opens the ticks again.

export function ChecklistPage() {
  const [params] = useSearchParams()
  const needs = proofsFromQuery(params.get('need'))
  if (needs.length === 0 || params.has('change')) return <ChooseNeeds start={needs} />
  return <Results needs={needs} />
}

function ChooseNeeds({ start }) {
  const navigate = useNavigate()
  const [ticked, setTicked] = useState(start)

  function toggle(key) {
    setTicked((now) => (now.includes(key) ? now.filter((item) => item !== key) : [...now, key]))
  }

  function check(event) {
    event.preventDefault()
    if (ticked.length === 0) return
    const ordered = PROOFS.map((proof) => proof.key).filter((key) => ticked.includes(key))
    navigate(`/documents/checklist?need=${ordered.join(',')}`)
  }

  return (
    <Screen title={TITLE} back={BACK}>
      <div className="intro">
        <h1 className="page-title">{TITLE}</h1>
        <p className="lead">Tick the papers an office asked for. We will look for them in your documents.</p>
      </div>
      <form className="form" onSubmit={check} noValidate>
        <section className="panel" aria-labelledby="asked-for">
          <h2 id="asked-for" className="panel__title">
            What did they ask for?
          </h2>
          <div className="type-choices" role="group" aria-labelledby="asked-for">
            {PROOFS.map((proof) => (
              <button
                key={proof.key}
                type="button"
                className="type-choice need-choice"
                aria-pressed={ticked.includes(proof.key)}
                onClick={() => toggle(proof.key)}
              >
                <span className="need-choice__label">{proof.label}</span>
                <span className="need-choice__hint">{proof.hint}</span>
              </button>
            ))}
          </div>
        </section>
        <Button type="submit" disabled={ticked.length === 0}>
          {ticked.length === 0 ? 'Tick at least one first' : 'Check my documents'}
        </Button>
      </form>
    </Screen>
  )
}

// How each answer looks (the mockup's Checklist screen).
const LOOK = {
  usable: { kind: 'ready', word: 'Ready' },
  check: { kind: 'check', word: 'Needs your check' },
  reading: { kind: 'reading', word: 'Being read' },
  expired: { kind: 'expired', word: 'Expired' },
  missing: { kind: 'missing', word: 'Not in your documents' },
}

/** One line of the answer: { look, detail, action: { to, label } | null }. */
export function needLine(match, document) {
  const proof = proofOf(match.proof)
  const label = typeLabel(match.documentType ?? proof.type)
  const path = document ? `/documents/${document.documentId}` : null
  switch (match.status) {
    case 'usable': {
      const dates = document ? dateLine(document) : null
      return { look: LOOK.usable, detail: dates ? `${label}, ${dates.charAt(0).toLowerCase()}${dates.slice(1)}` : label, action: null }
    }
    case 'unconfirmed': {
      const copy = newCopyState(document)
      if (document?.status === 'needs_review' || copy === 'check') {
        const version = copy === 'check' ? `?version=${document.replacement.version}` : ''
        return {
          look: LOOK.check,
          detail: `${label}, waiting for you to check it`,
          action: { to: `${path}/review${version}`, label: 'Check it now' },
        }
      }
      return { look: LOOK.reading, detail: `${label}, being read now`, action: path && { to: path, label: 'See it' } }
    }
    case 'expired': {
      const ended = formatDate(document?.expiryDate)
      return {
        look: LOOK.expired,
        detail: ended ? `${label} ended on ${ended}` : `${label} has ended`,
        action: path && { to: `${path}/new-copy`, label: 'Add the new copy' },
      }
    }
    default:
      return {
        look: LOOK.missing,
        detail: `${proof.hint}. Add it if the office needs it.`,
        action: { to: `/documents/add?type=${proof.type}`, label: 'Add it' },
      }
  }
}

function Results({ needs }) {
  const [state, setState] = useState({ answer: null, documents: null, problem: null })
  const [attempt, setAttempt] = useState(0)
  const key = needs.join(',')

  useEffect(() => {
    const controller = new AbortController()
    setState({ answer: null, documents: null, problem: null })
    Promise.all([matchProofs(key.split(',')), listDocuments(controller.signal)]).then(
      ([answer, documents]) => {
        if (!controller.signal.aborted) setState({ answer, documents, problem: null })
      },
      (error) => {
        if (controller.signal.aborted) return
        setState({ answer: null, documents: null, problem: error instanceof ApiError ? error.message : MESSAGES.serverProblem })
      },
    )
    return () => controller.abort()
  }, [key, attempt])

  const retry = useCallback(() => setAttempt((now) => now + 1), [])
  const change = `/documents/checklist?need=${key}&change=1`

  if (!state.answer) {
    return (
      <Screen title={TITLE} back={BACK}>
        <h1 className="page-title">{TITLE}</h1>
        {state.problem ? (
          <>
            <p className="alert" role="alert">
              We could not look at your documents. {state.problem}
            </p>
            <Button onClick={retry}>Try again</Button>
          </>
        ) : (
          <p className="lead" role="status">
            Looking at your documents…
          </p>
        )}
      </Screen>
    )
  }

  const byId = new Map(state.documents.map((document) => [document.documentId, document]))
  const lines = state.answer.proofs.map((match) => ({
    proof: proofOf(match.proof),
    ...needLine(match, match.documentId ? byId.get(match.documentId) : undefined),
  }))
  const count = (kinds) => lines.filter((line) => kinds.includes(line.look.kind)).length
  const totals = [
    [count(['ready']), 'ready', 'ready'],
    [count(['check', 'reading']), 'to check', 'check'],
    [count(['expired']), 'expired', 'expired'],
    [count(['missing']), 'missing', 'missing'],
  ].filter(([number]) => number > 0)

  return (
    <Screen title={TITLE} back={BACK}>
      <div className="intro">
        <h1 className="page-title">{TITLE}</h1>
        <p className="lead">An office asked for these papers. Here is what you already have.</p>
      </div>
      <p className="need-totals">
        {totals.map(([number, words, kind]) => (
          <span key={kind} className={`chip chip--${kind}`}>
            {number} {words}
          </span>
        ))}
      </p>
      <ul className="need-list" aria-label="What the office asked for">
        {lines.map((line) => (
          <li key={line.proof.key} className="need">
            <span className={`need__dot need__dot--${line.look.kind}`} aria-hidden="true">
              <Icon name={line.look.kind === 'ready' ? 'check' : 'alertCircle'} size={22} strokeWidth={2.8} />
            </span>
            <span className="need__text">
              <span className="need__title">{line.proof.label}</span>
              <span className={`need__status need__status--${line.look.kind}`}>{line.look.word}</span>
              <span className="need__detail">{line.detail}</span>
              {line.action && (
                <ButtonLink to={line.action.to} variant="secondary">
                  {line.action.label}
                </ButtonLink>
              )}
            </span>
          </li>
        ))}
      </ul>
      <ButtonLink to={change} variant="quiet">
        Change what they asked for
      </ButtonLink>
    </Screen>
  )
}
