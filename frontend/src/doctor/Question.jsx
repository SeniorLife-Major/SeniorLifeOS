import { useRef, useState } from 'react'
import { ApiError, MESSAGES } from '../api/client'
import { Button, ButtonLink } from '../components/Button'
import { TextField } from '../components/Field'
import { FocusedTitle } from '../components/FocusedTitle'
import { Icon } from '../components/Icon'
import { Screen } from '../components/Screen'
import { useProfile } from '../profile/ProfileProvider'
import { CallButtons } from './CallButtons'

const BACK = { to: '/doctor', label: 'Find a doctor' }
const PERMISSION_DENIED = 1 // GeolocationPositionError.PERMISSION_DENIED

const SAY = {
  noPlace: 'Please type the area and the city.',
  noAnswer: 'Please type your answer.',
  locating: 'Finding where you are…',
  noPermission: 'Your phone did not share where you are. Please type the area and the city instead.',
  notFound: 'We could not find where you are. Please type the area and the city instead.',
}

export function problemText(problem) {
  if (!problem) return null
  return problem instanceof ApiError ? problem.message : MESSAGES.serverProblem
}

/** The one question a search may ask. answer(body) sends it (answerSearch's body). */
export function Question({ result, answer, problem }) {
  if (result.status === 'needs_emergency_confirmation') {
    return <EmergencyQuestion result={result} answer={answer} problem={problem} />
  }
  if (result.status === 'needs_location') return <LocationQuestion result={result} answer={answer} problem={problem} />
  return <TextQuestion result={result} answer={answer} problem={problem} />
}

function Problem({ text }) {
  if (!text) return null
  return (
    <p className="alert" role="alert">
      {text}
    </p>
  )
}

function StartAgain() {
  return (
    <ButtonLink to="/doctor" variant="quiet">
      Start a new search
    </ButtonLink>
  )
}

/** The rules saw a danger sign but the model doubts it is happening now: call, or go on. */
function EmergencyQuestion({ result, answer, problem }) {
  return (
    <Screen title="Do you need help now?" back={BACK} emergency={false}>
      <div className="intro">
        <FocusedTitle>{result.question || 'Do you want to call emergency services now?'}</FocusedTitle>
        {result.emergency?.message && <p className="lead">{result.emergency.message}</p>}
      </div>
      <CallButtons numbers={result.emergency?.numbers} />
      <Problem text={problemText(problem)} />
      <div className="actions">
        <Button className="button--danger" onClick={() => answer({ callEmergency: true })}>
          Yes, I need help now
        </Button>
        <Button variant="secondary" onClick={() => answer({ callEmergency: false })}>
          No, find a doctor
        </Button>
      </div>
      <StartAgain />
    </Screen>
  )
}

/** "Which kind of doctor do you need…?": the person answers in their own words. */
function TextQuestion({ result, answer, problem }) {
  const [text, setText] = useState('')
  const [error, setError] = useState(null)
  const box = useRef(null)

  function send(event) {
    event.preventDefault()
    if (!text.trim()) {
      setError(SAY.noAnswer)
      box.current?.focus()
      return
    }
    setError(null)
    answer({ clarification: text.trim() })
  }

  return (
    <Screen title="One question" back={BACK}>
      <div className="intro">
        <FocusedTitle>One question</FocusedTitle>
        <p className="lead">{result.question}</p>
      </div>
      <Problem text={problemText(problem)} />
      <form className="form" onSubmit={send} noValidate>
        <TextField
          id="answer"
          label="Your answer"
          hint="English, Hindi or Hinglish. Your own words are fine."
          multiline
          rows={3}
          maxLength={500}
          value={text}
          onChange={(event) => setText(event.target.value)}
          error={error}
          ref={box}
        />
        <Button type="submit">Send</Button>
      </form>
      <StartAgain />
    </Screen>
  )
}

/** "Where should I look?": near me (the phone's location), near my home, a place offered, or typed. */
function LocationQuestion({ result, answer, problem }) {
  const { profile } = useProfile()
  const [place, setPlace] = useState('')
  const [error, setError] = useState(null)
  const [locating, setLocating] = useState(false)
  const [phoneProblem, setPhoneProblem] = useState(null)
  const box = useRef(null)
  const canLocate = typeof navigator !== 'undefined' && 'geolocation' in navigator
  const home = profile?.homeLocation

  function nearMe() {
    if (locating) return
    setPhoneProblem(null)
    setLocating(true)
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setLocating(false)
        answer({ location: { lat: position.coords.latitude, lon: position.coords.longitude } })
      },
      (failure) => {
        setLocating(false)
        setPhoneProblem(failure?.code === PERMISSION_DENIED ? SAY.noPermission : SAY.notFound)
        box.current?.focus()
      },
      { enableHighAccuracy: false, timeout: 20000, maximumAge: 60000 },
    )
  }

  function useTyped(event) {
    event.preventDefault()
    if (!place.trim()) {
      setError(SAY.noPlace)
      box.current?.focus()
      return
    }
    setError(null)
    answer({ location: place.trim() })
  }

  return (
    <Screen title="One question" back={BACK}>
      <div className="intro">
        <FocusedTitle>One question</FocusedTitle>
        <p className="lead">{result.question}</p>
      </div>
      <Problem text={problemText(problem) ?? phoneProblem} />
      <div className="place-choices">
        {(result.options ?? []).map((option) => (
          <Button key={option} icon="mapPin" onClick={() => answer({ location: option })}>
            {option}
          </Button>
        ))}
        {canLocate && (
          <Button icon="navigation" onClick={nearMe} disabled={locating}>
            {locating ? SAY.locating : 'Near me now'}
          </Button>
        )}
        {home && (
          <Button variant="secondary" icon="mapPin" onClick={() => answer({ location: 'home' })}>
            {home.label ? `Near my home, ${home.label}` : 'Near my home'}
          </Button>
        )}
      </div>
      <form className="form" onSubmit={useTyped} noValidate>
        <TextField
          id="place"
          label="Or type the area and city"
          autoComplete="off"
          maxLength={200}
          value={place}
          onChange={(event) => setPlace(event.target.value)}
          error={error}
          ref={box}
        />
        <Button type="submit" variant="secondary">
          Use this
        </Button>
      </form>
      <p className="fine-print">
        <Icon name="lock" size={18} /> Where you are is used for this search only. It is not saved.
      </p>
      <StartAgain />
    </Screen>
  )
}
