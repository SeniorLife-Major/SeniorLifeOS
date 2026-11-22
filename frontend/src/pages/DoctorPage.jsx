import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { listSearches, startSearch } from '../api/doctors'
import { Button, ButtonLink } from '../components/Button'
import { TextField } from '../components/Field'
import { Icon } from '../components/Icon'
import { Screen } from '../components/Screen'
import { problemText } from '../doctor/Question'
import { searchChip, searchDate } from '../doctor/search'
import { SearchProgress } from '../doctor/SearchProgress'
import { useSearchRun } from '../doctor/useSearchRun'

const HOME = { to: '/', label: 'Home' }
export const QUERY_MAX = 2000 // MAX_QUERY_CHARS in backend/graphs/health_graph.py
const EXAMPLES = ['Knee pain', 'Sugar check-up', 'Eye problem', 'Ghutno mein dard']

// /doctor (the mockup's Ask and Searching screens): the person says what is wrong in their own
// words; while the backend works, its steps are ticked off; the result opens at /doctor/:requestId.
// Or they use a saved lab report instead (/doctor/lab, LabReportSearchPage).

export function DoctorPage() {
  const navigate = useNavigate()
  const [text, setText] = useState('')
  const run = useSearchRun()
  const ready = text.trim().length > 0

  async function find(event) {
    event.preventDefault()
    if (!ready || run.working) return
    const result = await run.go((options) => startSearch(text.trim(), options))
    if (result) navigate(`/doctor/${encodeURIComponent(result.requestId)}`, { state: { result } })
  }

  if (run.working) {
    return (
      <Screen title="Finding a doctor" back={HOME}>
        <div className="intro">
          <h1 className="page-title">Finding a doctor</h1>
          <p className="lead quote">“{text.trim()}”</p>
        </div>
        <SearchProgress done={run.done} />
        <p className="fine-print">You can leave this screen. The search goes on, and it is kept under "Your recent searches".</p>
      </Screen>
    )
  }

  return (
    <Screen title="Find a doctor" back={HOME}>
      <h1 className="page-title">Find a doctor</h1>
      <form className="form" onSubmit={find} noValidate>
        <TextField
          id="problem"
          label="What is the problem?"
          hint="English, Hindi or Hinglish. Your own words are fine."
          multiline
          rows={4}
          maxLength={QUERY_MAX}
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
        <section className="examples" aria-labelledby="examples">
          <h2 id="examples" className="panel__title">
            Or tap one
          </h2>
          <div className="pick-buttons">
            {EXAMPLES.map((example) => (
              <button key={example} type="button" className="type-choice" onClick={() => setText(example)}>
                {example}
              </button>
            ))}
          </div>
        </section>
        {run.problem && (
          <p className="alert" role="alert">
            {problemText(run.problem)}
          </p>
        )}
        <Button type="submit" icon="stethoscope" disabled={!ready}>
          {ready ? 'Find doctors' : 'Tell us the problem first'}
        </Button>
      </form>
      <section className="panel" aria-labelledby="from-lab">
        <h2 id="from-lab" className="panel__title">
          Have a lab report?
        </h2>
        <p className="panel__hint">We look at the results outside the normal range and find the right kind of doctor.</p>
        <ButtonLink to="/doctor/lab" variant="secondary" icon="document">
          Use my lab report
        </ButtonLink>
      </section>
      {/* Always: doctor search always uses the AI (Decision 73); the Settings switches are for documents. */}
      <p className="fine-print">
        <Icon name="lock" size={18} /> An AI service reads your words to choose the right kind of doctor. Your name is
        hidden from it.
      </p>
      <RecentSearches />
    </Screen>
  )
}

/** The searches of the last 30 days (the backend keeps them that long), newest first. */
function RecentSearches() {
  const [state, setState] = useState({ searches: null, failed: false })

  useEffect(() => {
    const controller = new AbortController()
    listSearches(controller.signal).then(
      (searches) => setState({ searches, failed: false }),
      (error) => {
        if (!controller.signal.aborted && error?.name !== 'AbortError') setState({ searches: null, failed: true })
      },
    )
    return () => controller.abort()
  }, [])

  if (state.failed) return <p className="fine-print">Your recent searches could not be loaded just now.</p>
  if (!state.searches || state.searches.length === 0) return null

  return (
    <section className="panel" aria-labelledby="recent">
      <h2 id="recent" className="panel__title">
        Your recent searches
      </h2>
      <ul className="recent-list">
        {state.searches.map((search) => {
          const chip = searchChip(search.status)
          const date = searchDate(search.createdAt)
          return (
            <li key={search.requestId}>
              <Link className="recent" to={`/doctor/${encodeURIComponent(search.requestId)}`}>
                <span className="recent__text">
                  <span className="recent__title">{search.specialty?.label ?? 'Doctor search'}</span>
                  {search.fromLabReport && <span className="recent__from">From your lab report</span>}
                  <span className={`chip chip--${chip.kind}`}>{chip.word}</span>
                </span>
                {date && <span className="recent__date">{date}</span>}
                <Icon name="chevronRight" size={26} />
              </Link>
            </li>
          )
        })}
      </ul>
      <p className="panel__hint">Searches are kept for 30 days.</p>
    </section>
  )
}
