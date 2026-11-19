import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import { MESSAGES } from '../api/client'
import { saveSession } from '../api/session'
import REAL from '../test/realDoctorStreams.json'
import {
  ALL_STEPS,
  deferred,
  fail,
  fakeBackend,
  makeDoctor,
  makeProfile,
  makeSearch,
  MEERA,
  ok,
  serveProfile,
  serveSearches,
  streamed,
} from '../test/fakeBackend'

const START = 'POST /api/v1/health-requests'
const ANSWER = 'POST /api/v1/health-requests/search-1/answer'

afterEach(() => {
  delete navigator.geolocation
})

function signedIn(profile = makeProfile(), searches = []) {
  saveSession('token-meera', 3600)
  const backend = fakeBackend({ 'GET /api/v1/auth/me': () => ok(MEERA) })
  serveProfile(backend, profile)
  serveSearches(backend, searches)
  return backend
}

function renderApp(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

/** The phone's location: { coords } to share it, or { code } to refuse (1 = permission denied). */
function phoneLocation(answer) {
  const geolocation = {
    getCurrentPosition: vi.fn((success, failure) =>
      answer.coords ? success({ coords: answer.coords }) : failure({ code: answer.code }),
    ),
  }
  Object.defineProperty(navigator, 'geolocation', { value: geolocation, configurable: true })
  return geolocation
}

async function ask(user, words) {
  await user.type(await screen.findByLabelText('What is the problem?'), words)
  await user.click(screen.getByRole('button', { name: 'Find doctors' }))
}

/** The page heading, once the screen has put the focus on it (that happens just after it appears). */
async function focusOn(name) {
  const heading = await screen.findByRole('heading', { level: 1, name })
  await waitFor(() => expect(heading).toHaveFocus())
}

const stage = (label) => screen.getByText(label, { selector: '.search-step__label', exact: false })
/** Waits for a stage's whole label as a screen reader hears it ("…, done"). */
const findStage = (words) =>
  screen.findByText((_, element) => element?.className === 'search-step__label' && element.textContent === words)

const QUESTION = makeSearch({
  status: 'needs_location',
  specialty: null,
  searchLocation: null,
  doctors: [],
  question: 'I found more than one place with that name: Andheri West, Mumbai; Andheri East, Mumbai. Which one do you mean?',
  options: ['Andheri West, Mumbai', 'Andheri East, Mumbai'],
})

describe('Find a doctor', () => {
  it('opens from Home, takes the problem in words, ticks off real steps, and shows the doctors', async () => {
    const user = userEvent.setup()
    const backend = signedIn()
    const hold = deferred()
    backend.on(START, () => ({
      status: 201,
      events: [
        ['step', { step: 'validate_request' }],
        ['step', { step: 'safety_check' }],
        hold.promise,
        ...streamed(makeSearch(), ALL_STEPS.slice(2)).events,
      ],
    }))
    renderApp('/')

    await user.click(await screen.findByRole('link', { name: /Find a doctor/ }))
    expect(screen.getByRole('button', { name: 'Tell us the problem first' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Ghutno mein dard' }))
    expect(screen.getByLabelText('What is the problem?')).toHaveValue('Ghutno mein dard')
    await user.click(screen.getByRole('button', { name: 'Find doctors' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Finding a doctor' })).toBeInTheDocument()
    expect(screen.getByText('“Ghutno mein dard”')).toBeInTheDocument()
    expect(await findStage('Checking for emergency signs, done')).toBeInTheDocument()
    expect(stage('Choosing the right kind of doctor')).toHaveTextContent('doing now')
    expect(stage('Picking the best three')).not.toHaveTextContent(/done|doing now/)
    expect(screen.getByRole('status')).toHaveTextContent('Choosing the right kind of doctor…')
    expect(backend.sent(START)[0].body).toEqual({ query: 'Ghutno mein dard' })

    hold.resolve()
    await focusOn('2 doctors near your home')
    expect(screen.getByText('Orthopaedic doctor (bones and joints)')).toBeInTheDocument()
    const first = screen.getByRole('article', { name: 'Dr. Asha Menon' })
    expect(within(first).getByText('Nearest')).toBeInTheDocument()
    expect(within(first).getByText('Demo Bone and Joint Clinic, Andheri West')).toBeInTheDocument()
    expect(within(first).getByText('1.2 km away')).toBeInTheDocument()
    expect(within(first).getByText('Today 10 am to 1 pm')).toBeInTheDocument()
    expect(within(first).getByRole('list', { name: 'Why this doctor' })).toHaveTextContent('88% recommend (64 patient stories)')
    const directions = within(first).getByRole('link', { name: 'Directions to Dr. Asha Menon' })
    expect(directions).toHaveAttribute('href', 'https://www.openstreetmap.org/directions?to=19.1197%2C72.8464')
    expect(directions).toHaveAttribute('target', '_blank')
    expect(within(first).getByRole('link', { name: "Doctor's page: Dr. Asha Menon" })).toHaveAttribute(
      'href',
      'https://www.practo.com/mumbai/doctor/demo-asha-menon',
    )
    const second = screen.getByRole('article', { name: 'Dr. Vikram Joshi' })
    expect(within(second).queryByRole('link', { name: /Doctor's page/ })).toBeNull() // it has none
  })

  it("shows the backend's notes, the doctors not picked on request, and the place search's credit", async () => {
    const user = userEvent.setup()
    signedIn(makeProfile(), [
      makeSearch({
        isTopChoice: false,
        notes: ['None of these doctors has both a registration verified by Practo and at least 70% of patients recommending them, so none is picked as a first choice.'],
        doctors: [makeDoctor({ labels: [], flags: ['Not a first choice: Practo has not verified the registration'] })],
        otherDoctors: [makeDoctor({ doctorId: 'dr-9', name: 'Dr. Ravi Rao', labels: [] })],
        searchLocation: { label: 'Andheri West, Mumbai', source: 'answer' },
        attribution: '© OpenStreetMap contributors',
      }),
    ])
    renderApp('/doctor/search-1')

    expect(await screen.findByRole('heading', { level: 1, name: '1 doctor near Andheri West, Mumbai' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Please note' })).toHaveTextContent('none is picked as a first choice')
    expect(screen.getByRole('list', { name: 'Please note' })).toHaveTextContent('Not a first choice')
    expect(screen.queryByRole('article', { name: 'Dr. Ravi Rao' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'See 1 more doctor' }))
    expect(screen.getByRole('article', { name: 'Dr. Ravi Rao' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Hide the other doctors' })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText(/Place search: © OpenStreetMap contributors\./)).toBeInTheDocument()
  })

  it('asks where to look when the place is not clear, and goes on with the place picked', async () => {
    const user = userEvent.setup()
    const backend = signedIn(makeProfile({ homeLocation: { label: 'Andheri West', lat: 19.12, lon: 72.85 } }))
    backend.on(START, () => streamed(QUESTION, ['validate_request', 'safety_check', 'understand_request', 'map_specialty', 'locate']))
    const hold = deferred()
    backend.on(ANSWER, () => ({
      status: 200,
      events: [hold.promise, ...streamed(makeSearch(), ['ask_location', 'locate', 'search_doctors', 'verify', 'rank', 'explain']).events],
    }))
    renderApp('/doctor')

    await ask(user, 'Knee doctor in Andheri')
    await focusOn('One question')
    expect(screen.getByText(QUESTION.question)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Near my home, Andheri West' })).toBeInTheDocument()
    expect(screen.getByText('Where you are is used for this search only. It is not saved.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Andheri East, Mumbai' }))

    // The stages done before the question stay done.
    expect(await findStage('Choosing the right kind of doctor, done')).toBeInTheDocument()
    expect(stage('Looking for doctors nearby')).toHaveTextContent('doing now')
    hold.resolve()
    expect(await screen.findByRole('heading', { level: 1, name: '2 doctors near your home' })).toBeInTheDocument()
    expect(backend.sent(ANSWER).map((request) => request.body)).toEqual([{ location: 'Andheri East, Mumbai' }])
  })

  it("uses the phone's location only when asked, and says so when the phone does not share it", async () => {
    const user = userEvent.setup()
    const backend = signedIn(makeProfile(), [QUESTION])
    backend.on(ANSWER, () => streamed(makeSearch(), ['locate'], 200))
    phoneLocation({ code: 1 })
    renderApp('/doctor/search-1')

    expect(screen.queryByRole('button', { name: /Near my home/ })).toBeNull() // no home saved
    await user.click(await screen.findByRole('button', { name: 'Near me now' }))
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Your phone did not share where you are. Please type the area and the city instead.',
    )
    expect(screen.getByLabelText('Or type the area and city')).toHaveFocus()
    expect(backend.sent(ANSWER)).toEqual([])

    phoneLocation({ coords: { latitude: 19.1197, longitude: 72.8464 } })
    await user.click(screen.getByRole('button', { name: 'Near me now' }))
    expect(await screen.findByRole('heading', { level: 1, name: '2 doctors near your home' })).toBeInTheDocument()
    expect(backend.sent(ANSWER)[0].body).toEqual({ location: { lat: 19.1197, lon: 72.8464 } })
  })

  it('takes a typed place, and asks for one when the box is empty', async () => {
    const user = userEvent.setup()
    const backend = signedIn(makeProfile(), [QUESTION])
    backend.on(ANSWER, () => streamed(makeSearch(), ['locate'], 200))
    renderApp('/doctor/search-1')

    await user.click(await screen.findByRole('button', { name: 'Use this' }))
    expect(screen.getByLabelText('Or type the area and city')).toHaveAccessibleDescription('Please type the area and the city.')
    await user.type(screen.getByLabelText('Or type the area and city'), '  Versova, Mumbai ')
    await user.click(screen.getByRole('button', { name: 'Use this' }))
    await screen.findByRole('heading', { level: 1, name: '2 doctors near your home' })
    expect(backend.sent(ANSWER)[0].body).toEqual({ location: 'Versova, Mumbai' })
  })

  it('answers the question about the kind of doctor in words', async () => {
    const user = userEvent.setup()
    const question = makeSearch({
      status: 'needs_clarification',
      doctors: [],
      question: 'Which kind of doctor do you need, or what problem would you like help with?',
    })
    const backend = signedIn(makeProfile(), [question])
    backend.on(ANSWER, () => streamed(makeSearch(), ['ask_clarification'], 200))
    renderApp('/doctor/search-1')

    await user.type(await screen.findByLabelText('Your answer'), 'My eyes are weak')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    await screen.findByRole('heading', { level: 1, name: '2 doctors near your home' })
    expect(backend.sent(ANSWER)[0].body).toEqual({ clarification: 'My eyes are weak' })
  })

  it('shows a question that can no longer be answered as the backend says, with a way to start again', async () => {
    const user = userEvent.setup()
    const backend = signedIn(makeProfile(), [QUESTION])
    backend.on(ANSWER, () => fail(409, 'CONFLICT', 'This question was not answered in time. Please start again.'))
    renderApp('/doctor/search-1')

    await user.click(await screen.findByRole('button', { name: 'Andheri West, Mumbai' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('This question was not answered in time. Please start again.')
    expect(screen.getByRole('link', { name: 'Start a new search' })).toHaveAttribute('href', '/doctor')
  })
})

describe('An emergency', () => {
  const EMERGENCY = {
    numbers: [
      { number: '112', for: 'Any emergency (police, fire, ambulance)' },
      { number: '108', for: 'Ambulance' },
    ],
    message: 'This may be an emergency. Call 112 or 108 now, or go to the nearest hospital emergency.',
    redFlags: ['chest_pain', 'breathlessness'],
    modelSigns: [],
    source: 'rules',
  }

  it('stops the search and shows big call buttons and the signs noticed', async () => {
    const user = userEvent.setup()
    const backend = signedIn()
    backend.on(START, () =>
      streamed(makeSearch({ status: 'emergency', doctors: [], specialty: null, emergency: EMERGENCY }), ['validate_request', 'safety_check', 'emergency']),
    )
    renderApp('/doctor')

    await ask(user, 'chest pain and I cannot breathe')
    await focusOn('This may be an emergency')
    expect(screen.getByText(EMERGENCY.message)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Call 112/ })).toHaveAttribute('href', 'tel:112')
    expect(screen.getByRole('link', { name: /Call 108/ })).toHaveAttribute('href', 'tel:108')
    const signs = screen.getByRole('region', { name: 'Signs we noticed' })
    expect(within(signs).getAllByRole('listitem').map((item) => item.textContent)).toEqual(['Chest pain', 'Hard to breathe'])
    expect(screen.queryByRole('link', { name: 'Emergency? Call 112' })).toBeNull() // it has its own buttons
    expect(screen.getByRole('link', { name: 'I am safe now. Find a doctor instead' })).toHaveAttribute('href', '/doctor')
    // Same words would be flagged again, so the screen says how to look for something else.
    expect(screen.getByText('If you are safe and need a doctor for something else, describe that other problem. Naming these signs again, even with "no", brings back this screen.')).toBeInTheDocument()
  })

  it('puts the mental health line first when the words speak of self-harm', async () => {
    signedIn(makeProfile(), [
      makeSearch({
        status: 'emergency',
        doctors: [],
        emergency: {
          ...EMERGENCY,
          redFlags: ['self_harm'],
          numbers: [...EMERGENCY.numbers, { number: '14416', for: 'Tele-MANAS mental health helpline (free, 24 hours)' }],
        },
      }),
    ])
    renderApp('/doctor/search-1')

    expect(await screen.findByRole('heading', { level: 1, name: 'You do not have to face this alone' })).toBeInTheDocument()
    const calls = screen.getAllByRole('link', { name: /^Call / })
    expect(calls.map((link) => link.getAttribute('href'))).toEqual(['tel:14416', 'tel:112', 'tel:108'])
    expect(screen.queryByText(/describe that other problem/)).toBeNull() // not asked to change their words
  })

  it('asks first when the danger sign may not be happening now, and goes on only on "No"', async () => {
    const user = userEvent.setup()
    const asking = makeSearch({
      status: 'needs_emergency_confirmation',
      doctors: [],
      question: 'Do you want to call emergency services (112 or 108) now?',
      emergency: { ...EMERGENCY, source: null },
    })
    const backend = signedIn(makeProfile(), [asking])
    backend.on(ANSWER, ({ body }) =>
      body.callEmergency
        ? streamed(makeSearch({ status: 'emergency', doctors: [], emergency: EMERGENCY }), ['confirm_emergency', 'emergency'], 200)
        : streamed(makeSearch(), ['confirm_emergency', 'map_specialty'], 200),
    )
    const { unmount } = renderApp('/doctor/search-1')

    expect(await screen.findByRole('heading', { level: 1, name: asking.question })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Call 112/ })).toHaveAttribute('href', 'tel:112')
    await user.click(screen.getByRole('button', { name: 'No, find a doctor' }))
    expect(await screen.findByRole('heading', { level: 1, name: '2 doctors near your home' })).toBeInTheDocument()
    unmount()

    renderApp('/doctor/search-1')
    await user.click(await screen.findByRole('button', { name: 'Yes, I need help now' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'This may be an emergency' })).toBeInTheDocument()
    expect(backend.sent(ANSWER).map((request) => request.body)).toEqual([{ callEmergency: false }, { callEmergency: true }])
  })
})

describe('When a search does not find doctors', () => {
  it('says why, with the notes, and offers a new search', async () => {
    signedIn(makeProfile(), [
      makeSearch({ status: 'no_doctors_nearby', doctors: [], notes: ['No doctor of this kind within 25 km in our list.'] }),
    ])
    renderApp('/doctor/search-1')

    expect(await screen.findByRole('heading', { level: 1, name: 'No doctor found nearby' })).toBeInTheDocument()
    expect(screen.getByText('Kind of doctor: Orthopaedic doctor (bones and joints)')).toBeInTheDocument()
    expect(screen.getByText('No doctor of this kind within 25 km in our list.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Start a new search' })).toHaveAttribute('href', '/doctor')
  })

  it('keeps what was typed and shows the message when the search is refused', async () => {
    const user = userEvent.setup()
    const backend = signedIn()
    backend.on(START, () =>
      fail(429, 'TOO_MANY_REQUESTS', 'You have asked many times in the last hour. Please wait 10 minutes and try again. In an emergency, call 112 or 108 now.'),
    )
    renderApp('/doctor')

    await ask(user, 'knee pain')
    expect(await screen.findByRole('alert')).toHaveTextContent('Please wait 10 minutes and try again.')
    expect(screen.getByLabelText('What is the problem?')).toHaveValue('knee pain')
  })

  it('points to the recent searches when the connection is lost part way', async () => {
    const user = userEvent.setup()
    const backend = signedIn()
    backend.on(START, () => ({ status: 201, events: [['step', { step: 'safety_check' }]] }))
    renderApp('/doctor')

    await ask(user, 'knee pain')
    expect(await screen.findByRole('alert')).toHaveTextContent(MESSAGES.streamLost)
  })

  it("says a search that is gone can't be found", async () => {
    signedIn()
    renderApp('/doctor/old-search')
    expect(await screen.findByRole('heading', { level: 1, name: 'We could not find this search' })).toBeInTheDocument()
  })
})

describe('Recent searches and the AI note', () => {
  it('lists the recent searches with how each ended, and opens one', async () => {
    const user = userEvent.setup()
    signedIn(makeProfile(), [makeSearch(), makeSearch({ ...QUESTION, requestId: 'search-2' })])
    renderApp('/doctor')

    const recent = await screen.findByRole('region', { name: 'Your recent searches' })
    const links = within(recent).getAllByRole('link')
    expect(links.map((link) => link.textContent)).toEqual([
      'Orthopaedic doctor (bones and joints)Doctors found19 Nov',
      'Doctor searchWaiting for your answer19 Nov',
    ])
    await user.click(links[1])
    expect(await screen.findByRole('heading', { level: 1, name: 'One question' })).toBeInTheDocument()
  })

  it('always says an AI service reads the words, whatever the document switches say', async () => {
    const note = /An AI service reads your words to choose the right kind of doctor\. Your name is hidden from it\./
    signedIn(makeProfile({ consents: { useLlm: false, medicalCloud: false, cloudOcr: false } }))
    const { unmount } = renderApp('/doctor')
    expect(await screen.findByText(note)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Change this in Settings' })).toBeNull() // Settings can't change it
    unmount()

    signedIn(makeProfile({ consents: { useLlm: true, medicalCloud: true, cloudOcr: true } }))
    renderApp('/doctor')
    expect(await screen.findByText(note)).toBeInTheDocument()
  })
})

describe('A result as the real backend sends it (test/realDoctorStreams.json)', () => {
  it('shows every doctor with its reasons, warnings and registration note', async () => {
    const block = REAL.found.split('\n\n').find((part) => part.startsWith('event: result'))
    const real = JSON.parse(block.slice(block.indexOf('data: ') + 6)).data
    signedIn(makeProfile(), [real])
    renderApp(`/doctor/${real.requestId}`)

    expect(await screen.findByRole('heading', { level: 1, name: '3 doctors near your current location' })).toBeInTheDocument()
    expect(screen.getByText('Cardiologist (heart)')).toBeInTheDocument()
    const cards = screen.getAllByRole('article')
    expect(cards.map((card) => within(card).getByRole('heading').textContent)).toEqual(
      real.doctors.map((doctor) => doctor.name),
    )
    const first = within(cards[0])
    expect(first.getByText('Fee ₹1,500')).toBeInTheDocument()
    expect(first.getByText(real.doctors[0].flags[0])).toBeInTheDocument()
    expect(first.getByText(real.doctors[0].registrationNote)).toBeInTheDocument()
    expect(first.getByRole('link', { name: `Directions to ${real.doctors[0].name}` })).toBeInTheDocument()
  })
})
