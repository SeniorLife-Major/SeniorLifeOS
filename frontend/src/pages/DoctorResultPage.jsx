import { useCallback, useEffect, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { ApiError, MESSAGES } from '../api/client'
import { answerSearch, getSearch } from '../api/doctors'
import { Button, ButtonLink } from '../components/Button'
import { FocusedTitle } from '../components/FocusedTitle'
import { Screen } from '../components/Screen'
import { DoctorCard } from '../doctor/DoctorCard'
import { EmergencyView } from '../doctor/EmergencyView'
import { Question } from '../doctor/Question'
import { doneWhenAsked, endWords, QUESTIONS, resultsHeading } from '../doctor/search'
import { SearchProgress } from '../doctor/SearchProgress'
import { useSearchRun } from '../doctor/useSearchRun'

const BACK = { to: '/doctor', label: 'Find a doctor' }

// /doctor/:requestId: one search. It comes with the screen that started it (navigation state)
// or is fetched (GET /health-requests/{id}), and shows, by its status: the doctors, the one
// question (answering it runs the search on), the emergency screen, or why it ended.

export function DoctorResultPage() {
  const { requestId } = useParams()
  return <SearchResult key={requestId} requestId={requestId} />
}

function SearchResult({ requestId }) {
  const location = useLocation()
  const navigate = useNavigate()
  const given = location.state?.result?.requestId === requestId ? location.state.result : null
  const [result, setResult] = useState(given)
  const [load, setLoad] = useState({ problem: null, notFound: false, attempt: 0 })
  const run = useSearchRun()

  useEffect(() => {
    if (result) return undefined
    const controller = new AbortController()
    getSearch(requestId, controller.signal).then(
      (found) => {
        if (!controller.signal.aborted) setResult(found)
      },
      (error) => {
        if (controller.signal.aborted || error?.name === 'AbortError') return
        setLoad((now) => ({
          ...now,
          notFound: error instanceof ApiError && error.status === 404,
          problem: error instanceof ApiError ? error.message : MESSAGES.serverProblem,
        }))
      },
    )
    return () => controller.abort()
    // Fetched once per attempt (result is left out on purpose: an answer changes it, and that
    // must not fetch it again).
  }, [requestId, load.attempt])

  const reload = useCallback(() => {
    setResult(null)
    setLoad((now) => ({ problem: null, notFound: false, attempt: now.attempt + 1 }))
  }, [])

  async function answer(body) {
    const next = await run.go((options) => answerSearch(requestId, body, options), doneWhenAsked(result.status))
    if (!next) return
    setResult(next)
    // So Back from a doctor's page shows this, not the question again.
    navigate(location.pathname, { replace: true, state: { result: next } })
  }

  if (load.notFound) {
    return (
      <Screen title="Search not found" back={BACK}>
        <h1 className="page-title">We could not find this search</h1>
        <p className="lead">Searches are kept for 30 days. Please start a new one.</p>
        <ButtonLink to="/doctor">Find a doctor</ButtonLink>
      </Screen>
    )
  }

  if (!result) {
    return (
      <Screen title="Your search" back={BACK}>
        <h1 className="page-title">Your search</h1>
        {load.problem ? (
          <>
            <p className="alert" role="alert">
              We could not open this search. {load.problem}
            </p>
            <Button onClick={reload}>Try again</Button>
          </>
        ) : (
          <p className="lead" role="status">
            Opening your search…
          </p>
        )}
      </Screen>
    )
  }

  if (run.working) {
    return (
      <Screen title="Finding a doctor" back={BACK}>
        <h1 className="page-title">Finding a doctor</h1>
        <SearchProgress done={run.done} />
      </Screen>
    )
  }

  if (QUESTIONS.has(result.status)) return <Question result={result} answer={answer} problem={run.problem} />
  if (result.status === 'emergency') return <EmergencyView emergency={result.emergency} />
  if (result.status === 'doctors_found' && result.doctors?.length > 0) return <Doctors result={result} />
  return <Ended result={result} reload={reload} />
}

function Notes({ notes }) {
  if (!notes?.length) return null
  return (
    <section className="notice" aria-labelledby="notes">
      <h2 id="notes" className="notice__title">
        Please note
      </h2>
      <ul className="notice__list">
        {notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
    </section>
  )
}

/** The doctors found (the mockup's Results): the ones shown, then the others on request. */
function Doctors({ result }) {
  const [showOthers, setShowOthers] = useState(false)
  const others = result.otherDoctors ?? []
  const heading = resultsHeading(result)
  return (
    <Screen title={heading} back={BACK}>
      <div className="intro">
        <FocusedTitle>{heading}</FocusedTitle>
        {result.specialty && <p className="lead">{result.specialty.label}</p>}
      </div>
      <Notes notes={result.notes} />
      {result.doctors.map((doctor, index) => (
        <DoctorCard key={doctor.doctorId ?? index} doctor={doctor} id={`doctor-${index}`} />
      ))}
      {others.length > 0 && (
        <>
          <Button variant="secondary" aria-expanded={showOthers} onClick={() => setShowOthers((now) => !now)}>
            {showOthers ? 'Hide the other doctors' : `See ${others.length} more ${others.length === 1 ? 'doctor' : 'doctors'}`}
          </Button>
          {showOthers &&
            others.map((doctor, index) => (
              <DoctorCard key={doctor.doctorId ?? `other-${index}`} doctor={doctor} id={`other-${index}`} />
            ))}
        </>
      )}
      <p className="fine-print">
        Please call the clinic before you go. Doctor details are from Practo.
        {result.attribution && ` Place search: ${result.attribution}.`}
      </p>
      <ButtonLink to="/doctor" variant="secondary">
        Start a new search
      </ButtonLink>
    </Screen>
  )
}

/** A search that ended without doctors to show: why, the backend's notes, and what to do. */
function Ended({ result, reload }) {
  const words = endWords(result.status)
  return (
    <Screen title={words.heading} back={BACK}>
      <div className="intro">
        <FocusedTitle>{words.heading}</FocusedTitle>
        <p className="lead">{words.text}</p>
        {result.specialty && <p className="lead">Kind of doctor: {result.specialty.label}</p>}
      </div>
      <Notes notes={result.notes} />
      {result.status === 'running' ? (
        <Button onClick={reload}>Check again</Button>
      ) : (
        <ButtonLink to="/doctor">Start a new search</ButtonLink>
      )}
    </Screen>
  )
}
