import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { App } from '../App'
import { saveSession } from '../api/session'
import {
  deferred,
  fail,
  fakeBackend,
  LAB_STEPS,
  makeDocument,
  makeLabReport,
  makeProfile,
  makeSearch,
  MEERA,
  ok,
  serveDocuments,
  serveProfile,
  serveSearches,
  streamed,
} from '../test/fakeBackend'

// Doctor search from a lab report (lab-report-doctor-search): /doctor/lab picks a checked and
// saved lab report; the result shows why this kind of doctor, and "see a doctor today" when the
// backend says a result is very abnormal.

const START = 'POST /api/v1/health-requests'
const SEE_TODAY = 'Some results are far outside the normal range. Please see a doctor today.'

const LAB = makeDocument({ documentId: 'lab-1', documentType: 'lab_report', createdAt: '2026-11-02T09:00:00Z' })
const HEART = makeSearch({
  specialty: { key: 'cardiologist', label: 'Cardiologist (heart)' },
  labReport: makeLabReport(),
})

function signedIn(documents = [LAB], searches = []) {
  saveSession('token-meera', 3600)
  const backend = fakeBackend({ 'GET /api/v1/auth/me': () => ok(MEERA) })
  serveProfile(backend, makeProfile())
  serveDocuments(backend, documents)
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

const stage = (label) => screen.getByText(label, { selector: '.search-step__label', exact: false })
const choice = (name) => screen.getByRole('button', { name: new RegExp(name) })

describe('Find a doctor from a lab report', () => {
  it('opens from Find a doctor, uses the only saved report, ticks off its steps and shows why this doctor', async () => {
    const user = userEvent.setup()
    const income = makeDocument({ documentId: 'doc-9' }) // not a lab report: not offered
    const backend = signedIn([LAB, income])
    const hold = deferred()
    backend.on(START, () => ({
      status: 201,
      events: [
        ['step', { step: 'validate_request' }],
        ['step', { step: 'read_lab_report' }],
        hold.promise,
        ...streamed(HEART, LAB_STEPS.slice(2)).events,
      ],
    }))
    renderApp('/doctor')

    await user.click(await screen.findByRole('link', { name: 'Use my lab report' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Use my lab report' })).toBeInTheDocument()
    const reports = screen.getByRole('list', { name: 'Which lab report?' })
    expect(within(reports).getAllByRole('button')).toHaveLength(1)
    expect(choice('Lab report')).toHaveAttribute('aria-pressed', 'true') // the only one, picked already
    expect(choice('Lab report')).toHaveTextContent('Added 2 Nov 2026')
    expect(screen.getByText(/No AI service reads your results for this/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Find doctors' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Finding a doctor' })).toBeInTheDocument()
    expect(screen.getByText('From your lab report')).toBeInTheDocument()
    await waitFor(() => expect(stage('Reading the results on your lab report')).toHaveTextContent('done'))
    expect(stage('Choosing the right kind of doctor')).toHaveTextContent('doing now')
    expect(backend.sent(START)[0].body).toEqual({ labReportId: 'lab-1' })

    hold.resolve()
    expect(await screen.findByRole('heading', { level: 1, name: '2 doctors near your home' })).toBeInTheDocument()
    const panel = screen.getByRole('region', { name: 'From your lab report' })
    expect(within(panel).getByText('Demo Path Labs, 2 Nov 2026')).toBeInTheDocument()
    expect(within(panel).getByText(makeLabReport().summary)).toBeInTheDocument()
    const found = within(panel).getByRole('list', { name: 'Results outside the normal range' })
    expect(found).toHaveTextContent('LDL CholesterolHighCardiologist (heart)')
    expect(within(panel).getByRole('link', { name: 'See the report' })).toHaveAttribute('href', '/documents/lab-1')
    expect(within(panel).getByText(/It is not a diagnosis/)).toBeInTheDocument()
    expect(screen.queryByText(SEE_TODAY)).toBeNull()
  })

  it('says to see a doctor today, on the question and on the doctors, when a result is very abnormal', async () => {
    const user = userEvent.setup()
    const labReport = makeLabReport({
      summary: 'Your lab report has results outside the normal range printed on it: Potassium (high).',
      seeToday: true,
      unjudged: 2,
      findings: [{ test: 'Potassium', flag: 'high', specialty: 'general_physician', specialtyLabel: 'General physician' }],
    })
    const asked = makeSearch({
      status: 'needs_location',
      specialty: null,
      searchLocation: null,
      doctors: [],
      question: 'Where should I look for a doctor?',
      labReport,
    })
    const backend = signedIn()
    backend.on(START, () => streamed(asked, ['validate_request', 'read_lab_report', 'map_specialty', 'locate']))
    backend.on('POST /api/v1/health-requests/search-1/answer', () =>
      streamed(makeSearch({ labReport }), ['ask_location', 'locate', 'search_doctors', 'verify', 'rank', 'explain']),
    )
    renderApp('/doctor/lab')

    await user.click(await screen.findByRole('button', { name: 'Find doctors' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'One question' })).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent(SEE_TODAY)
    expect(screen.getByRole('region', { name: 'From your lab report' })).toHaveTextContent('Potassium')
    expect(screen.getByText('2 results had no normal range we could read, so they were not compared.')).toBeInTheDocument()
    await user.type(screen.getByLabelText('Or type the area and city'), 'Andheri West, Mumbai')
    await user.click(screen.getByRole('button', { name: 'Use this' }))

    expect(await screen.findByRole('heading', { level: 1, name: '2 doctors near your home' })).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent(SEE_TODAY)
  })

  it('lets the person pick one of several reports, the one in the link picked already', async () => {
    const user = userEvent.setup()
    const older = makeDocument({ documentId: 'lab-2', documentType: 'lab_report', createdAt: '2026-10-05T09:00:00Z' })
    const backend = signedIn([LAB, older])
    backend.on(START, () => streamed(HEART, LAB_STEPS))
    renderApp('/doctor/lab?report=lab-2')

    expect(await screen.findByRole('button', { name: /Added 5 Oct 2026/ })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: /Added 2 Nov 2026/ })).toHaveAttribute('aria-pressed', 'false')
    await user.click(screen.getByRole('button', { name: /Added 2 Nov 2026/ }))
    expect(screen.getByRole('button', { name: /Added 2 Nov 2026/ })).toHaveAttribute('aria-pressed', 'true')
    await user.click(screen.getByRole('button', { name: 'Find doctors' }))
    await screen.findByRole('heading', { level: 1, name: '2 doctors near your home' })
    expect(backend.sent(START)[0].body).toEqual({ labReportId: 'lab-1' })
  })

  it('waits for a choice when there are several and none is named', async () => {
    const older = makeDocument({ documentId: 'lab-2', documentType: 'lab_report' })
    signedIn([LAB, older])
    renderApp('/doctor/lab?report=someone-elses')

    expect(await screen.findByRole('button', { name: 'Choose a lab report first' })).toBeDisabled()
  })

  it('asks for a check first, says a report is still being read, and offers to add one', async () => {
    signedIn([
      makeDocument({ documentId: 'lab-3', documentType: 'lab_report', status: 'needs_review' }),
      makeDocument({ documentId: 'lab-4', documentType: 'lab_report', status: 'processing' }),
    ])
    renderApp('/doctor/lab')

    expect(await screen.findByText('Your lab report is not ready to use yet.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Find doctors|Choose a lab report/ })).toBeNull()
    const check = within(screen.getByRole('region', { name: 'Check these first' })).getByRole('link')
    expect(check).toHaveAttribute('href', '/documents/lab-3/review')
    expect(check).toHaveTextContent('Check it first')
    expect(screen.getByText(/A lab report is still being read/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Add a lab report' })).toHaveAttribute('href', '/documents/add?type=lab_report')
  })

  it('offers to add one when there is none', async () => {
    signedIn([makeDocument()])
    renderApp('/doctor/lab')

    expect(await screen.findByText(/You have no saved lab report yet/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Add a lab report' })).toHaveAttribute('href', '/documents/add?type=lab_report')
  })

  it("shows the backend's reason when it refuses the report, and stays on the screen", async () => {
    const user = userEvent.setup()
    const backend = signedIn()
    backend.on(START, () => fail(409, 'CONFLICT', "Please check this lab report's details before using it"))
    renderApp('/doctor/lab')

    await user.click(await screen.findByRole('button', { name: 'Find doctors' }))
    expect(await screen.findByRole('alert')).toHaveTextContent("Please check this lab report's details before using it")
    expect(screen.getByRole('heading', { level: 1, name: 'Use my lab report' })).toBeInTheDocument()
  })

  it('says a report with no results could not be used', async () => {
    signedIn([LAB], [makeSearch({ status: 'rejected', specialty: null, doctors: [], labReport: makeLabReport({ findings: [] }) })])
    renderApp('/doctor/search-1')

    expect(await screen.findByRole('heading', { level: 1, name: 'No results on this lab report' })).toBeInTheDocument()
    expect(screen.queryByText('That was too long')).toBeNull()
  })
})

describe('Where else a lab report leads to a doctor', () => {
  it("a saved lab report's page offers to find a doctor for it; other documents do not", async () => {
    signedIn([LAB, makeDocument({ documentId: 'doc-9' })])
    const { unmount } = renderApp('/documents/lab-1')

    expect(await screen.findByRole('link', { name: 'Find a doctor for this report' })).toHaveAttribute(
      'href',
      '/doctor/lab?report=lab-1',
    )
    unmount()
    renderApp('/documents/doc-9')
    expect(await screen.findByRole('heading', { level: 1, name: 'Saved' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Find a doctor for this report' })).toBeNull()
  })

  it('a lab report waiting for its check does not offer it yet', async () => {
    signedIn([makeDocument({ documentId: 'lab-3', documentType: 'lab_report', status: 'needs_review' })])
    renderApp('/documents/lab-3')

    expect(await screen.findByRole('link', { name: 'Check the details' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Find a doctor for this report' })).toBeNull()
  })

  it('the recent searches say which came from a lab report', async () => {
    signedIn([LAB], [HEART, makeSearch({ requestId: 'search-2' })])
    renderApp('/doctor')

    const recent = await screen.findByRole('region', { name: 'Your recent searches' })
    expect(within(recent).getAllByRole('link').map((link) => link.textContent)).toEqual([
      'Cardiologist (heart)From your lab reportDoctors found19 Nov',
      'Orthopaedic doctor (bones and joints)Doctors found19 Nov',
    ])
  })
})
