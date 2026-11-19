import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, MESSAGES } from '../api/client'
import { PLACE_MAX, placeOptions } from '../api/profile'
import { Button } from '../components/Button'
import { TextField } from '../components/Field'
import { Screen } from '../components/Screen'
import { useProfile } from '../profile/ProfileProvider'
import { ProfileStatus } from '../profile/ProfileStatus'
import { homeWords } from './SettingsPage'

const SAY = {
  noPlace: 'Please type your area and city.',
  choose: 'This name fits more than one place. Which one do you mean?',
  noPermission: 'Your phone did not share its location. You can type your area and city instead.',
  notFound: 'We could not find where your phone is. Please try again, or type your area and city.',
  outsideIndia: "Your phone's location seems to be outside India. Please type your area and city instead.",
  saved: 'Your home is saved.',
  removed: 'Your home is removed.',
}

const PERMISSION_DENIED = 1 // GeolocationPositionError.PERMISSION_DENIED

function messageOf(error) {
  return error instanceof ApiError ? error.message : MESSAGES.serverProblem
}

/** Where the person lives, for finding doctors near them: typed as area and city, or the phone's location. */
export function HomeLocationPage() {
  const { profile, update } = useProfile()
  const navigate = useNavigate()
  const [place, setPlace] = useState('')
  const [placeError, setPlaceError] = useState(null)
  const [problem, setProblem] = useState(null)
  const [options, setOptions] = useState(null)
  const [busy, setBusy] = useState(null) // 'place', 'locating' or 'removing' while waiting
  const placeBox = useRef(null)
  const canLocate = typeof navigator !== 'undefined' && 'geolocation' in navigator

  /** Saves the home and goes back to Settings; gives back the error if it could not be saved. */
  async function saveHome(homeLocation, note) {
    try {
      await update({ homeLocation })
    } catch (error) {
      setBusy(null)
      return error
    }
    navigate('/settings', { replace: true, state: { note } })
    return null
  }

  function startOver() {
    setProblem(null)
    setOptions(null)
  }

  async function savePlace(event) {
    event.preventDefault()
    if (busy) return
    startOver()
    if (!place.trim()) {
      setPlaceError(SAY.noPlace)
      placeBox.current?.focus()
      return
    }
    setPlaceError(null)
    setBusy('place')
    const error = await saveHome({ place }, SAY.saved)
    if (!error) return
    const choices = placeOptions(error)
    if (choices) setOptions(choices)
    else setProblem(messageOf(error))
  }

  async function choose(option) {
    if (busy) return
    setBusy('place')
    const error = await saveHome({ place: option.label, lat: option.lat, lon: option.lon }, SAY.saved)
    if (error) setProblem(messageOf(error))
  }

  function noneOfThese() {
    setOptions(null)
    placeBox.current?.focus()
  }

  function useMyLocation() {
    if (busy) return
    startOver()
    setBusy('locating')
    navigator.geolocation.getCurrentPosition(
      async (position) => {
        const { latitude: lat, longitude: lon } = position.coords
        const error = await saveHome({ lat, lon }, SAY.saved)
        if (!error) return
        // The backend refuses a point outside India (only Indian doctors are listed).
        setProblem(error instanceof ApiError && error.status === 422 ? SAY.outsideIndia : messageOf(error))
      },
      (failure) => {
        setBusy(null)
        setProblem(failure?.code === PERMISSION_DENIED ? SAY.noPermission : SAY.notFound)
      },
      { enableHighAccuracy: false, timeout: 20000, maximumAge: 60000 },
    )
  }

  async function removeHome() {
    if (busy) return
    startOver()
    setBusy('removing')
    const error = await saveHome(null, SAY.removed)
    if (error) setProblem(messageOf(error))
  }

  if (!profile) {
    return (
      <Screen title="My home" back={{ to: '/settings', label: 'Settings' }}>
        <h1 className="page-title">My home</h1>
        <ProfileStatus />
      </Screen>
    )
  }

  const home = profile.homeLocation

  return (
    <Screen title="My home" back={{ to: '/settings', label: 'Settings' }}>
      <div className="intro">
        <h1 className="page-title">My home</h1>
        <p className="lead">
          We use it to find doctors near you. It is kept to about 100 metres, not your exact door.
        </p>
      </div>

      {home && (
        <p className="notice">
          Your home now: <strong>{homeWords(home)}</strong>
        </p>
      )}

      <form className="form" noValidate onSubmit={savePlace}>
        <TextField
          id="place"
          label="Area and city"
          ref={placeBox}
          value={place}
          onChange={(event) => setPlace(event.target.value)}
          error={placeError}
          autoComplete="off"
          maxLength={PLACE_MAX}
          placeholder="Andheri West, Mumbai"
        />
        <Button type="submit" disabled={busy !== null}>
          {busy === 'place' ? 'Saving…' : 'Save this place'}
        </Button>
      </form>

      {options && (
        <section className="panel" aria-labelledby="choose-place">
          <h2 id="choose-place" className="panel__title">
            {SAY.choose}
          </h2>
          <div className="actions">
            {options.map((option) => (
              <Button
                key={`${option.lat},${option.lon}`}
                variant="secondary"
                disabled={busy !== null}
                onClick={() => choose(option)}
              >
                {option.label}
              </Button>
            ))}
            <Button variant="quiet" disabled={busy !== null} onClick={noneOfThese}>
              None of these
            </Button>
          </div>
        </section>
      )}

      {problem && (
        <p className="alert" role="alert">
          {problem}
        </p>
      )}

      {canLocate && (
        <>
          <p className="or">or</p>
          <Button variant="secondary" icon="navigation" disabled={busy !== null} onClick={useMyLocation}>
            {busy === 'locating' ? 'Finding your location…' : "Use my phone's location"}
          </Button>
        </>
      )}

      {home && (
        <Button variant="quiet" className="button--muted" disabled={busy !== null} onClick={removeHome}>
          {busy === 'removing' ? 'Removing…' : 'Remove my home'}
        </Button>
      )}
    </Screen>
  )
}
