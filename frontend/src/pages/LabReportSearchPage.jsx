import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { startLabSearch } from '../api/doctors'
import { Button, ButtonLink } from '../components/Button'
import { Icon } from '../components/Icon'
import { Screen } from '../components/Screen'
import { problemText } from '../doctor/Question'
import { LAB_STAGES } from '../doctor/search'
import { SearchProgress } from '../doctor/SearchProgress'
import { useSearchRun } from '../doctor/useSearchRun'
import { dateLine } from '../documents/status'
import { useDocumentList } from '../documents/useDocuments'

const BACK = { to: '/doctor', label: 'Find a doctor' }
const ADD = '/documents/add?type=lab_report'
const READING = new Set(['processing', 'reviewed'])

// /doctor/lab (lab-report-doctor-search): find a doctor from a lab report. Only a lab report that
// has been checked and saved can be used (the person checks what was read first, in My
// documents). The person picks one (?report=<documentId> picks it already, from the report's
// own page), then taps Find doctors; the search runs as from words, and the result opens at
// /doctor/:requestId. The list of documents has no details, so a report shows when it was added.

export function LabReportSearchPage() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const { documents, problem, retry } = useDocumentList()
  const [chosen, setChosen] = useState(null)
  const run = useSearchRun()

  const reports = (documents ?? []).filter((document) => document.documentType === 'lab_report')
  const ready = reports.filter((document) => document.status === 'confirmed')
  const waiting = reports.filter((document) => document.status === 'needs_review')
  const reading = reports.filter((document) => READING.has(document.status))
  const asked = params.get('report')
  // What the person tapped; else the report named in the link; else the only one there is.
  const picked =
    chosen ?? (ready.some((document) => document.documentId === asked) ? asked : ready.length === 1 ? ready[0].documentId : null)

  async function find(event) {
    event.preventDefault()
    if (!picked || run.working) return
    const result = await run.go((options) => startLabSearch(picked, options))
    if (result) navigate(`/doctor/${encodeURIComponent(result.requestId)}`, { state: { result } })
  }

  if (run.working) {
    return (
      <Screen title="Finding a doctor" back={BACK}>
        <div className="intro">
          <h1 className="page-title">Finding a doctor</h1>
          <p className="lead">From your lab report</p>
        </div>
        <SearchProgress done={run.done} stages={LAB_STAGES} />
        <p className="fine-print">You can leave this screen. The search goes on, and it is kept under "Your recent searches".</p>
      </Screen>
    )
  }

  let body
  if (problem) {
    body = (
      <>
        <p className="alert" role="alert">
          We could not open your documents. {problem}
        </p>
        <Button onClick={retry}>Try again</Button>
      </>
    )
  } else if (!documents) {
    body = (
      <p className="lead" role="status">
        Opening your lab reports…
      </p>
    )
  } else {
    body = (
      <>
        {ready.length > 0 ? (
          <form className="form" onSubmit={find} noValidate>
            <section className="panel" aria-labelledby="pick-report">
              <h2 id="pick-report" className="panel__title">
                Which lab report?
              </h2>
              <ul className="doc-list" aria-labelledby="pick-report">
                {ready.map((document) => (
                  <li key={document.documentId}>
                    <button
                      type="button"
                      className="doc-card report-choice"
                      aria-pressed={picked === document.documentId}
                      onClick={() => setChosen(document.documentId)}
                    >
                      <span className="doc-card__text">
                        <span className="doc-card__title">Lab report</span>
                        <span className="doc-card__detail">{dateLine(document) ?? 'Saved'}</span>
                      </span>
                      {picked === document.documentId && <Icon name="check" size={28} strokeWidth={3} />}
                    </button>
                  </li>
                ))}
              </ul>
            </section>
            {run.problem && (
              <p className="alert" role="alert">
                {problemText(run.problem)}
              </p>
            )}
            <Button type="submit" icon="stethoscope" disabled={!picked}>
              {picked ? 'Find doctors' : 'Choose a lab report first'}
            </Button>
          </form>
        ) : (
          <p className="lead">
            {waiting.length > 0 || reading.length > 0
              ? 'Your lab report is not ready to use yet.'
              : 'You have no saved lab report yet. Add one: a photo of the report, or a PDF.'}
          </p>
        )}
        {waiting.length > 0 && (
          <section className="panel" aria-labelledby="check-first">
            <h2 id="check-first" className="panel__title">
              Check these first
            </h2>
            <p className="panel__hint">We read these reports. Please check what we read before we use it.</p>
            <ul className="recent-list">
              {waiting.map((document) => (
                <li key={document.documentId}>
                  <Link className="recent" to={`/documents/${encodeURIComponent(document.documentId)}/review`}>
                    <span className="recent__text">
                      <span className="recent__title">Lab report</span>
                      <span className="chip chip--check">Check it first</span>
                    </span>
                    {dateLine(document) && <span className="recent__date">{dateLine(document)}</span>}
                    <Icon name="chevronRight" size={26} />
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        )}
        {reading.length > 0 && (
          <p className="notice">
            {reading.length === 1 ? 'A lab report is' : `${reading.length} lab reports are`} still being read. It will be here
            once you have checked it.
          </p>
        )}
        <ButtonLink to={ADD} variant={ready.length > 0 || waiting.length > 0 ? 'secondary' : 'primary'} icon="plus">
          {ready.length > 0 ? 'Add a new lab report' : 'Add a lab report'}
        </ButtonLink>
      </>
    )
  }

  return (
    <Screen title="Find a doctor from a lab report" back={BACK}>
      <div className="intro">
        <h1 className="page-title">Use my lab report</h1>
        <p className="lead">We look at the results outside the normal range printed on the report, and find the right kind of doctor.</p>
      </div>
      {body}
      <p className="fine-print">
        <Icon name="lock" size={18} /> The kind of doctor is chosen by fixed rules in SeniorLife. No AI service reads your
        results for this, and the search keeps only which results were high or low, not their values.
      </p>
    </Screen>
  )
}
