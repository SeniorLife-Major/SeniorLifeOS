import { useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { ApiError, MESSAGES } from '../api/client'
import { Icon } from '../components/Icon'
import { Screen } from '../components/Screen'
import { Switch } from '../components/Switch'
import { useProfile } from '../profile/ProfileProvider'
import { ProfileStatus } from '../profile/ProfileStatus'

const SIZES = [
  { scale: 1, label: 'A', words: 'normal' },
  { scale: 1.25, label: 'A+', words: 'larger' },
  { scale: 1.5, label: 'A++', words: 'largest' },
]

// What each choice allows, as the backend uses it (graphs/document_graph.py, api/health_requests.py).
const CONSENTS = [
  {
    key: 'cloudOcr',
    label: 'Read my document photos with a cloud service',
    hint: 'Reads blurry or handwritten pages better.',
  },
  {
    key: 'medicalCloud',
    label: 'My health papers and questions may go to a cloud AI',
    hint: 'Needed to read prescriptions, lab reports and bills, and to understand health questions.',
  },
  {
    key: 'useLlm',
    label: 'An AI may read my words and the text of my papers',
    hint: 'Helps when what I type is unclear, or a detail on a paper is hard to find.',
  },
]

export function homeWords(home) {
  if (!home) return 'Not set yet'
  return home.label || "Set from your phone's location"
}

export function SettingsPage() {
  const { profile, update } = useProfile()
  const location = useLocation()
  // A message from the screen before (e.g. "Your home is saved."), else the outcome of saving here.
  const [note, setNote] = useState(() => {
    const text = location.state?.note
    return typeof text === 'string' ? { kind: 'saved', text } : null
  })
  const pending = useRef(0)
  const failure = useRef(null)

  // Each change shows at once and is saved straight away. While changes are on their way the
  // note says "Saving…"; when the last one is done it says "Saved.", or why something was not
  // saved (that change is then put back).
  function save(change) {
    if (pending.current === 0) failure.current = null
    pending.current += 1
    setNote({ kind: 'saving', text: 'Saving…' })
    update(change, { optimistic: true })
      .catch((error) => {
        failure.current = `Not saved. ${error instanceof ApiError ? error.message : MESSAGES.serverProblem}`
      })
      .finally(() => {
        pending.current -= 1
        if (pending.current > 0) return
        setNote(failure.current ? { kind: 'error', text: failure.current } : { kind: 'saved', text: 'Saved.' })
      })
  }

  if (!profile) {
    return (
      <Screen title="Settings" back={{ to: '/', label: 'Home' }}>
        <h1 className="page-title">Settings</h1>
        <ProfileStatus />
      </Screen>
    )
  }

  const { accessibility, consents, homeLocation } = profile

  return (
    <Screen title="Settings" back={{ to: '/', label: 'Home' }}>
      <h1 className="page-title">Settings</h1>

      <section className="panel" aria-labelledby="text-size">
        <h2 id="text-size" className="panel__title">
          Text size
        </h2>
        <div className="size-choices" role="group" aria-labelledby="text-size">
          {SIZES.map((size) => (
            <button
              key={size.scale}
              type="button"
              className="size-choice"
              style={{ fontSize: `${1.125 * size.scale}rem` }}
              aria-label={`${size.label} ${size.words}`}
              aria-pressed={accessibility.fontScale === size.scale}
              onClick={() => save({ accessibility: { fontScale: size.scale } })}
            >
              {size.label}
            </button>
          ))}
        </div>
        <p className="panel__hint">This is how big the words will be.</p>
      </section>

      <section className="panel panel--list" aria-label="Contrast">
        <Switch
          id="high-contrast"
          label="Stronger contrast"
          hint="Darker words, thicker lines."
          checked={accessibility.highContrast}
          onChange={(on) => save({ accessibility: { highContrast: on } })}
        />
      </section>

      <section className="panel panel--row" aria-labelledby="my-home">
        <span className="panel__icon">
          <Icon name="mapPin" size={28} />
        </span>
        <span className="panel__text">
          <span id="my-home" className="panel__label">
            My home
          </span>
          <span className="panel__value">{homeWords(homeLocation)}</span>
        </span>
        <Link
          to="/settings/home"
          className="small-button"
          aria-label={homeLocation ? 'Change my home' : 'Add my home'}
        >
          {homeLocation ? 'Change' : 'Add'}
        </Link>
      </section>

      <section className="panel" aria-labelledby="ai-choices">
        <h2 id="ai-choices" className="panel__title">
          What may go to AI services
        </h2>
        <p className="panel__hint">
          You can change these any time. Before a photo or a paper&apos;s text is sent, we black out the ID numbers,
          faces and QR codes we can find.
        </p>
        <div className="switch-list">
          {CONSENTS.map((consent) => (
            <Switch
              key={consent.key}
              id={`consent-${consent.key}`}
              label={consent.label}
              hint={consent.hint}
              checked={consents[consent.key]}
              onChange={(on) => save({ consents: { [consent.key]: on } })}
            />
          ))}
        </div>
      </section>

      <p className="save-note" role="status">
        {note && note.kind !== 'error' ? note.text : ''}
      </p>
      {note?.kind === 'error' && (
        <p className="alert save-note" role="alert">
          {note.text}
        </p>
      )}
    </Screen>
  )
}
