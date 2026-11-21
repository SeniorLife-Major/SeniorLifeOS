import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { approveCheck, checkScheme } from '../api/benefits'
import { ApiError, MESSAGES } from '../api/client'
import { Button, ButtonLink } from '../components/Button'
import { TextField } from '../components/Field'
import { Icon } from '../components/Icon'
import { Screen } from '../components/Screen'
import { CRITERION_LOOK, factValue, gapAction, packagePapers, SCHEME_LOOK } from '../benefits/schemes'
import { DEMO_NOTE } from './BenefitsPage'

const BACK = { to: '/benefits', label: 'Schemes for me' }
export const TITLE_MAX = 120 // ApproveRequest.task_title in backend/api/benefits.py

// /benefits/:schemeId: one demo scheme against the person's papers (POST /benefits/checks with the
// scheme and the person's own answers). When every rule is met, every paper is ready and every
// answer given, the backend prepares the application package; "Yes, get it ready" approves it.

function problemOf(error) {
  return error instanceof ApiError ? error.message : MESSAGES.serverProblem
}

function Rules({ scheme }) {
  return (
    <section className="panel" aria-labelledby="rules">
      <h2 id="rules" className="panel__title">
        What it asks for
      </h2>
      <ul className="rule-list">
        {scheme.criteria.map((rule) => {
          const look = CRITERION_LOOK[rule.status] ?? CRITERION_LOOK.unknown
          const value = factValue(rule.fact, rule.value)
          return (
            <li key={rule.criterionId} className="rule">
              <span className={`need__dot need__dot--${look.kind}`} aria-hidden="true">
                <Icon name={look.icon} size={20} strokeWidth={2.8} />
              </span>
              <span className="need__text">
                <span className="rule__label">{rule.label}</span>
                <span className={`need__status need__status--${look.kind}`}>{look.word}</span>
                {value && rule.status !== 'conflict' && <span className="need__detail">Your papers: {value}</span>}
              </span>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function ToDo({ gaps }) {
  if (gaps.length === 0) return null
  return (
    <section className="scheme-group" aria-labelledby="to-do">
      <h2 id="to-do" className="panel__title">
        What to do
      </h2>
      <ul className="need-list" aria-labelledby="to-do">
        {gaps.map((gap) => {
          const action = gapAction(gap)
          return (
            <li key={`${gap.documentType}-${gap.reason}`} className="need">
              <span className="need__dot need__dot--check" aria-hidden="true">
                <Icon name="alertCircle" size={22} strokeWidth={2.8} />
              </span>
              <span className="need__text">
                <span className="need__detail need__detail--strong">{gap.message}</span>
                {action && (
                  <ButtonLink to={action.to} variant="secondary">
                    {action.label}
                  </ButtonLink>
                )}
              </span>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function Answers({ items, answers, onToggle, onSend, changed, busy }) {
  if (items.length === 0) return null
  return (
    <section className="panel" aria-labelledby="answers">
      <h2 id="answers" className="panel__title">
        Your answers
      </h2>
      <p className="panel__hint">No paper shows these, so the office asks you. Tick what is true for you.</p>
      <div className="answer-choices" role="group" aria-labelledby="answers">
        {items.map((item) => (
          <button
            key={item.key}
            type="button"
            className="type-choice answer-choice"
            aria-pressed={Boolean(answers[item.key])}
            onClick={() => onToggle(item.key)}
          >
            <Icon name={answers[item.key] ? 'check' : 'plus'} size={22} />
            <span>{item.label}</span>
          </button>
        ))}
      </div>
      {changed && (
        <Button onClick={onSend} disabled={busy}>
          {busy ? 'Checking…' : 'Check again with my answers'}
        </Button>
      )}
    </section>
  )
}

function Papers({ pack }) {
  return (
    <ul className="paper-list">
      {packagePapers(pack).map((paper) => (
        <li key={paper.documentType}>
          <Icon name="document" size={22} />
          <span>{paper.label}</span>
        </li>
      ))}
    </ul>
  )
}

function Steps({ pack }) {
  return (
    <ol className="steps-list">
      {pack.checklist.map((step) => (
        <li key={step}>{step}</li>
      ))}
    </ol>
  )
}

function Package({ check, onApproved, onStale }) {
  const pack = check.package
  const suggested = pack.tasks[0]?.title ?? ''
  const [title, setTitle] = useState(suggested)
  const [sending, setSending] = useState(false)
  const [problem, setProblem] = useState(null)

  async function approve() {
    setSending(true)
    setProblem(null)
    try {
      const changedTitle = title.trim() && title.trim() !== suggested ? title.trim() : undefined
      onApproved(await approveCheck(check.checkId, pack.actionId, changedTitle))
    } catch (error) {
      setSending(false)
      if (error instanceof ApiError && error.status === 409) onStale(error.message)
      else setProblem(problemOf(error))
    }
  }

  return (
    <section className="panel package" aria-labelledby="package">
      <h2 id="package" className="panel__title">
        Ready to apply
      </h2>
      <p className="panel__hint">The papers to take:</p>
      <Papers pack={pack} />
      <Steps pack={pack} />
      <TextField
        id="reminder"
        label="Your reminder"
        hint="Change it if you like, for example to add the day you will go."
        maxLength={TITLE_MAX}
        value={title}
        onChange={(event) => setTitle(event.target.value)}
      />
      {problem && (
        <p className="alert" role="alert">
          {problem}
        </p>
      )}
      <Button icon="check" onClick={approve} disabled={sending}>
        {sending ? 'Getting it ready…' : 'Yes, get it ready'}
      </Button>
      {pack.warning && <p className="fine-print">{pack.warning}</p>}
    </section>
  )
}

function Ready({ check }) {
  const pack = check.package
  return (
    <section className="panel package package--ready" aria-labelledby="ready" role="status">
      <h2 id="ready" className="panel__title">
        <Icon name="check" size={26} strokeWidth={2.8} /> Your application is ready to take
      </h2>
      <p className="lead">{pack.tasks[0]?.title}</p>
      <p className="panel__hint">Papers to take:</p>
      <Papers pack={pack} />
      <Steps pack={pack} />
      {pack.warning && <p className="fine-print">{pack.warning}</p>}
    </section>
  )
}

export function SchemePage() {
  const { schemeId } = useParams()
  const [state, setState] = useState({ check: null, problem: null, missing: false, busy: true })
  const [answers, setAnswers] = useState({})
  const [stale, setStale] = useState(null)
  const sent = useRef({})
  const controller = useRef(null)

  const run = useCallback(
    (declarations) => {
      controller.current?.abort()
      const mine = new AbortController()
      controller.current = mine
      sent.current = declarations
      setState((now) => ({ ...now, problem: null, busy: true }))
      checkScheme(schemeId, declarations, mine.signal).then(
        (check) => {
          if (mine.signal.aborted) return
          const given = Object.fromEntries((check.schemes[0]?.toConfirm ?? []).map((item) => [item.key, item.confirmed]))
          sent.current = given
          setAnswers(given)
          setState({ check, problem: null, missing: false, busy: false })
        },
        (error) => {
          if (mine.signal.aborted || error?.name === 'AbortError') return
          const missing = error instanceof ApiError && error.status === 404
          setState((now) => ({ ...now, problem: problemOf(error), missing, busy: false }))
        },
      )
    },
    [schemeId],
  )

  useEffect(() => {
    run({})
    return () => controller.current?.abort()
  }, [run])

  const scheme = state.check?.schemes[0]
  const title = scheme?.name ?? 'A scheme'

  if (!state.check) {
    return (
      <Screen title={title} back={BACK}>
        <h1 className="page-title">{state.missing ? 'Scheme not found' : title}</h1>
        {state.problem ? (
          <>
            <p className="alert" role="alert">
              {state.missing ? 'This scheme is not in the list.' : `We could not check this scheme. ${state.problem}`}
            </p>
            {state.missing ? (
              <ButtonLink to="/benefits">See the schemes</ButtonLink>
            ) : (
              <Button onClick={() => run(sent.current)}>Try again</Button>
            )}
          </>
        ) : (
          <p className="lead" role="status">
            Checking your papers…
          </p>
        )}
      </Screen>
    )
  }

  const look = SCHEME_LOOK[scheme?.status] ?? SCHEME_LOOK.add_documents
  const changed = (scheme?.toConfirm ?? []).some((item) => Boolean(answers[item.key]) !== Boolean(sent.current[item.key]))
  const status = state.check.status

  return (
    <Screen title={title} back={BACK}>
      <div className="intro">
        <h1 className="page-title">{title}</h1>
        {scheme?.benefit && <p className="lead">{scheme.benefit}</p>}
        {scheme?.summary && <p className="panel__hint">{scheme.summary}</p>}
        <span className={`chip chip--${look.kind}`}>{status === 'ready_for_manual_handoff' ? 'Ready to take' : look.word}</span>
      </div>
      {state.check.notes.map((note) => (
        <p key={note} className="note scheme-note">
          <Icon name="alertCircle" size={24} />
          <span>{note}</span>
        </p>
      ))}
      {stale && (
        <div className="alert" role="alert">
          <p>{stale}</p>
          <Button
            variant="secondary"
            onClick={() => {
              setStale(null)
              run(answers)
            }}
          >
            Check again
          </Button>
        </div>
      )}
      {status === 'ready_for_manual_handoff' && <Ready check={state.check} />}
      {status === 'awaiting_review' && !stale && (
        <Package key={state.check.checkId} check={state.check} onApproved={(check) => setState((now) => ({ ...now, check }))} onStale={setStale} />
      )}
      {scheme && <ToDo gaps={scheme.gaps} />}
      {scheme && status !== 'ready_for_manual_handoff' && scheme.status !== 'not_eligible' && (
        <Answers
          items={scheme.toConfirm}
          answers={answers}
          onToggle={(key) => setAnswers((now) => ({ ...now, [key]: !now[key] }))}
          onSend={() => run(answers)}
          changed={changed}
          busy={state.busy}
        />
      )}
      {state.problem && (
        <p className="alert" role="alert">
          We could not check this scheme. {state.problem}
        </p>
      )}
      {scheme && <Rules scheme={scheme} />}
      <p className="fine-print">{DEMO_NOTE}</p>
    </Screen>
  )
}
