import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { ApiError, MESSAGES } from '../api/client'
import { getReview, sendReview } from '../api/documents'
import { Button, ButtonLink } from '../components/Button'
import { Icon } from '../components/Icon'
import { Screen } from '../components/Screen'
import { DetailStep, RowsStep } from '../documents/DetailStep'
import { showRow, showValue } from '../documents/fields'
import { Paper, usePaper } from '../documents/Paper'
import { refusalOf } from '../documents/reviewNotes'
import { buildSteps, editsFrom } from '../documents/reviewSteps'
import { isKnownType, typeLabel } from '../documents/types'

const BACK = { to: '/documents', label: 'My documents' }
const ID_FIELDS = ['aadhaar_number', 'pan_number', 'account_number', 'policy_number']

function messageOf(error) {
  return error instanceof ApiError ? error.message : MESSAGES.serverProblem
}

/** "Income certificate" → "income certificate", but "ID card" stays "ID card". */
function inSentence(label) {
  return /^[A-Z][a-z]/.test(label) ? label.charAt(0).toLowerCase() + label.slice(1) : label
}

/** A heading that takes the focus when it appears, so screen readers start there. */
function FocusedTitle({ children }) {
  const ref = useRef(null)
  useEffect(() => {
    ref.current?.focus()
  }, [])
  return (
    <h1 ref={ref} tabIndex={-1} className="page-title">
      {children}
    </h1>
  )
}

export function ReviewPage() {
  const { documentId } = useParams()
  const [load, setLoad] = useState({ review: null, problem: null, notFound: false, notWaiting: false })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setLoad({ review: null, problem: null, notFound: false, notWaiting: false })
    getReview(documentId, controller.signal).then(
      (review) => setLoad({ review, problem: null, notFound: false, notWaiting: false }),
      (error) => {
        if (controller.signal.aborted) return
        const status = error instanceof ApiError ? error.status : 0
        setLoad({ review: null, problem: messageOf(error), notFound: status === 404, notWaiting: status === 409 })
      },
    )
    return () => controller.abort()
  }, [documentId, attempt])

  const retry = useCallback(() => setAttempt((now) => now + 1), [])

  if (load.notFound || load.notWaiting) {
    return (
      <Screen title="Nothing to check" back={BACK}>
        <h1 className="page-title">{load.notFound ? 'We could not find this document' : 'Nothing to check here'}</h1>
        <p className="lead">
          {load.notFound
            ? 'It may have been removed, or the link is old.'
            : 'This document is not waiting for a check. It may have been checked already, perhaps on another phone.'}
        </p>
        <ButtonLink to={load.notFound ? '/documents' : `/documents/${documentId}`}>
          {load.notFound ? 'Go to My documents' : 'See the document'}
        </ButtonLink>
      </Screen>
    )
  }

  if (!load.review) {
    return (
      <Screen title="Check the details" back={BACK}>
        <h1 className="page-title">Check the details</h1>
        {load.problem ? (
          <>
            <p className="alert" role="alert">
              We could not open the details. {load.problem}
            </p>
            <Button onClick={retry}>Try again</Button>
          </>
        ) : (
          <p className="lead" role="status">
            Opening the details…
          </p>
        )}
      </Screen>
    )
  }

  return <ReviewFlow documentId={documentId} review={load.review} />
}

function ReviewFlow({ documentId, review }) {
  const steps = useMemo(() => buildSteps(review), [review])
  const paper = usePaper(documentId)
  const [answers, setAnswers] = useState({})
  const [at, setAt] = useState(0)
  // steps → summary → saved; or summary/steps → reject → rejected
  const [phase, setPhase] = useState(steps.length > 0 ? 'steps' : 'summary')
  const [fromSummary, setFromSummary] = useState(false)
  const [rejectFrom, setRejectFrom] = useState('steps') // where "No, go back" returns to
  const [sending, setSending] = useState(false)
  const [problem, setProblem] = useState(null)
  const [refused, setRefused] = useState([])

  const name = typeLabel(review.documentType)
  const title = `Check your ${inSentence(name)}`

  function answer(step, given) {
    setAnswers((now) => ({ ...now, [step.key]: given }))
    setRefused((now) => now.filter((key) => key !== step.key))
    if (fromSummary || at >= steps.length - 1) {
      setFromSummary(false)
      setPhase('summary')
    } else {
      setAt(at + 1)
    }
  }

  function change(index) {
    setAt(index)
    setFromSummary(true)
    setProblem(null)
    setPhase('steps')
  }

  function askReject() {
    setRejectFrom(phase)
    setProblem(null)
    setPhase('reject')
  }

  function backToSummary() {
    setFromSummary(false)
    setPhase('summary')
  }

  async function save() {
    setSending(true)
    setProblem(null)
    try {
      const result = await sendReview(documentId, 'confirm', editsFrom(steps, answers))
      if (result.status === 'needs_review') {
        // The backend refused the details; nothing was saved and the check can go on.
        const refusal = refusalOf(result.errors)
        setProblem(refusal.message || 'These details could not be saved. Please check them and try again.')
        setRefused(refusal.fields)
      } else if (result.status === 'confirmed' || result.status === 'reviewed') {
        setPhase('saved')
      } else if (result.status === 'rejected') {
        setPhase('rejected')
      } else {
        setPhase('failed')
      }
    } catch (error) {
      setProblem(messageOf(error))
    } finally {
      setSending(false)
    }
  }

  async function reject() {
    setSending(true)
    setProblem(null)
    try {
      await sendReview(documentId, 'reject')
      setPhase('rejected')
    } catch (error) {
      setProblem(messageOf(error))
    } finally {
      setSending(false)
    }
  }

  if (phase === 'saved') {
    const hasId = steps.some((step) => {
      if (!ID_FIELDS.includes(step.key)) return false
      const given = answers[step.key]
      if (given?.status === 'changed' || given?.status === 'added') return given.value !== ''
      return step.state === 'read' && given?.status !== 'skipped'
    })
    return (
      <Screen title="Saved" back={BACK}>
        <span className="outcome-icon outcome-icon--ready">
          <Icon name="check" size={40} strokeWidth={2.6} />
        </span>
        <FocusedTitle>Saved</FocusedTitle>
        <p className="lead">
          Your {inSentence(name)} is now in My documents. Only you can open it.
          {hasId && ' ID numbers are hidden now; we show just the last 4 digits.'}
        </p>
        <ButtonLink to="/documents">Back to My documents</ButtonLink>
      </Screen>
    )
  }

  if (phase === 'rejected') {
    const again = isKnownType(review.documentType) ? `/documents/add?type=${review.documentType}` : '/documents/add'
    return (
      <Screen title="Not saved" back={BACK}>
        <FocusedTitle>Not saved</FocusedTitle>
        <p className="lead">We will not keep this document. You can add the right one any time.</p>
        <ButtonLink to={again} icon="plus">
          Add a document
        </ButtonLink>
        <ButtonLink to="/documents" variant="secondary">
          Back to My documents
        </ButtonLink>
      </Screen>
    )
  }

  if (phase === 'failed') {
    return (
      <Screen title="Not saved" back={BACK}>
        <FocusedTitle>It could not be saved</FocusedTitle>
        <p className="lead">Something went wrong on our side. Please add the document again in a little while.</p>
        <ButtonLink to="/documents">Back to My documents</ButtonLink>
      </Screen>
    )
  }

  if (phase === 'reject') {
    return (
      <Screen title="Don't save this document?" back={BACK}>
        <FocusedTitle>Don&apos;t save this document?</FocusedTitle>
        <p className="lead">We will not keep it, and its photo is deleted. You can add it again any time.</p>
        {problem && (
          <p className="alert" role="alert">
            {problem}
          </p>
        )}
        <div className="actions">
          <Button onClick={reject} disabled={sending}>
            {sending ? 'One moment…' : "Yes, don't save it"}
          </Button>
          <Button variant="secondary" onClick={() => setPhase(rejectFrom)} disabled={sending}>
            No, go back
          </Button>
        </div>
      </Screen>
    )
  }

  if (phase === 'summary') {
    return (
      <Screen title="All checked" back={BACK}>
        <FocusedTitle>All checked. Save it?</FocusedTitle>
        <ul className="summary" aria-label="The details">
          {steps.map((step, index) => (
            <SummaryRow
              key={step.key}
              step={step}
              answer={answers[step.key]}
              refused={refused.includes(step.key)}
              onChange={() => change(index)}
            />
          ))}
        </ul>
        {problem && (
          <p className="alert" role="alert">
            {problem}
          </p>
        )}
        <Button onClick={save} disabled={sending}>
          {sending ? 'Saving…' : 'Save to my documents'}
        </Button>
        <Button variant="quiet" className="button--muted" onClick={askReject} disabled={sending}>
          Don&apos;t save this document
        </Button>
      </Screen>
    )
  }

  const step = steps[at]
  const StepView = step.kind === 'rows' ? RowsStep : DetailStep
  return (
    <Screen title={title} back={BACK}>
      <h1 className="page-title">{title}</h1>
      <Paper paper={paper} />
      <StepView
        key={step.key}
        step={step}
        answer={answers[step.key]}
        position={`${at + 1} of ${steps.length}`}
        onAnswer={(given) => answer(step, given)}
      />
      <div className="review-nav">
        {at > 0 && !fromSummary && (
          <Button variant="quiet" icon="arrowLeft" onClick={() => setAt(at - 1)}>
            Previous detail
          </Button>
        )}
        {fromSummary && (
          <Button variant="quiet" icon="arrowLeft" onClick={backToSummary}>
            Back to all details
          </Button>
        )}
        <Button variant="quiet" className="button--muted" onClick={askReject}>
          Don&apos;t save this document
        </Button>
      </div>
    </Screen>
  )
}

function SummaryRow({ step, answer, refused, onChange }) {
  const typed = answer?.status === 'changed' || answer?.status === 'added'
  const value = typed ? answer.value : step.original
  let mark = null
  if (answer?.status === 'changed') mark = 'You changed this'
  else if (answer?.status === 'added') mark = 'You added this'

  let shown
  if (answer?.status === 'skipped' || (step.state !== 'read' && !typed)) {
    shown = <span className="summary__value summary__value--none">Not added</span>
  } else if (step.kind === 'rows') {
    shown = (
      <ul className="summary__rows">
        {(value ?? []).map((row, index) => (
          <li key={index}>{showRow(step.key, row)}</li>
        ))}
      </ul>
    )
  } else {
    shown = <span className="summary__value">{showValue(step.key, value) || 'Removed'}</span>
  }

  return (
    <li className={refused ? 'summary__item summary__item--refused' : 'summary__item'}>
      <span className="summary__text">
        <span className="summary__label">{step.label}</span>
        {shown}
        {mark && <span className="summary__mark">{mark}</span>}
        {refused && <span className="field__error">Please check this one</span>}
      </span>
      <button type="button" className="small-button" aria-label={`Change ${step.label}`} onClick={onChange}>
        Change
      </button>
    </li>
  )
}
