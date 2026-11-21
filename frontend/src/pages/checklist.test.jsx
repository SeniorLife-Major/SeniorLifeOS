import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { App } from '../App'
import { saveSession } from '../api/session'
import { fail, fakeBackend, makeDocument, makeProfile, MEERA, ok, serveDocuments, serveProfile } from '../test/fakeBackend'

const MATCH = 'POST /api/v1/documents/match'

const DOCUMENTS = [
  makeDocument({ documentId: 'id', documentType: 'identity' }),
  makeDocument({ documentId: 'income', status: 'needs_review' }),
  makeDocument({ documentId: 'cover', documentType: 'health_insurance', expiryDate: '2026-03-31' }),
  makeDocument({ documentId: 'bank', documentType: 'bank_passbook', status: 'processing' }),
]

// What backend/storage/service.py match() would say about DOCUMENTS.
const ANSWERS = {
  proof_of_age: { documentType: 'identity', documentId: 'id', status: 'usable' },
  proof_of_income: { documentType: 'income_certificate', documentId: 'income', status: 'unconfirmed' },
  health_cover: { documentType: 'health_insurance', documentId: 'cover', status: 'expired' },
  bank_details: { documentType: 'bank_passbook', documentId: 'bank', status: 'unconfirmed' },
}

function signedIn() {
  saveSession('token-meera', 3600)
  const backend = fakeBackend({ 'GET /api/v1/auth/me': () => ok(MEERA) })
  serveProfile(backend, makeProfile())
  serveDocuments(backend, DOCUMENTS)
  backend.on(MATCH, ({ body }) =>
    ok({ asOf: '2026-11-19', matches: [], proofs: body.proofs.map((proof) => ({ proof, ...ANSWERS[proof] })) }),
  )
  return backend
}

function renderApp(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

const choice = (label) => screen.getByRole('button', { name: new RegExp(`^${label}`) })

describe('Do I have what I need?', () => {
  it('is reached from My documents, and asks what the office wanted', async () => {
    const user = userEvent.setup()
    const backend = signedIn()
    renderApp('/documents')

    await user.click(await screen.findByRole('link', { name: 'Do I have what I need?' }))
    expect(screen.getByRole('heading', { level: 1, name: 'Do I have what I need?' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Tick at least one first' })).toBeDisabled()
    expect(choice('Proof of age')).toHaveTextContent('An ID card with your date of birth')

    await user.click(choice('Health cover'))
    await user.click(choice('Proof of age'))
    await user.click(choice('Bank details'))
    await user.click(choice('Bank details')) // and un-ticked
    expect(choice('Proof of age')).toHaveAttribute('aria-pressed', 'true')
    expect(choice('Bank details')).toHaveAttribute('aria-pressed', 'false')
    await user.click(screen.getByRole('button', { name: 'Check my documents' }))

    expect(await screen.findByRole('list', { name: 'What the office asked for' })).toBeInTheDocument()
    expect(backend.sent(MATCH).map((request) => request.body)).toEqual([{ proofs: ['proof_of_age', 'health_cover'] }])
  })

  it('shows what is ready, to check, ended or missing, with what to do next', async () => {
    signedIn()
    renderApp(
      '/documents/checklist?need=proof_of_age,proof_of_income,health_cover,bank_details',
    )

    const list = await screen.findByRole('list', { name: 'What the office asked for' })
    const lines = within(list).getAllByRole('listitem')
    expect(lines.map((line) => line.textContent)).toEqual([
      'Proof of ageReadyID card, added 1 Nov 2026',
      'Proof of incomeNeeds your checkIncome certificate, waiting for you to check itCheck it now',
      'Health coverExpiredHealth insurance ended on 31 Mar 2026Add the new copy',
      'Bank detailsBeing readBank passbook, being read nowSee it',
    ])
    expect(within(lines[1]).getByRole('link')).toHaveAttribute('href', '/documents/income/review')
    expect(within(lines[2]).getByRole('link')).toHaveAttribute('href', '/documents/cover/new-copy')
    expect(within(lines[3]).getByRole('link')).toHaveAttribute('href', '/documents/bank')
    expect(screen.getByText('1 ready')).toBeInTheDocument()
    expect(screen.getByText('2 to check')).toBeInTheDocument()
    expect(screen.getByText('1 expired')).toBeInTheDocument()
  })

  it('says what is missing, with a way to add it', async () => {
    const backend = signedIn()
    backend.on(MATCH, () =>
      ok({ asOf: '2026-11-19', matches: [], proofs: [{ proof: 'proof_of_identity', documentType: null, documentId: null, status: 'missing' }] }),
    )
    renderApp('/documents/checklist?need=proof_of_identity')

    const list = await screen.findByRole('list', { name: 'What the office asked for' })
    const [line] = within(list).getAllByRole('listitem')
    expect(line).toHaveTextContent('Proof of identityNot in your documentsAn ID card, such as Aadhaar. Add it if the office needs it.Add it')
    expect(within(line).getByRole('link')).toHaveAttribute('href', '/documents/add?type=identity')
    expect(screen.getByText('1 missing')).toBeInTheDocument()
  })

  it('does not offer proof of address (Decision 74), but offers the papers the pensions need', async () => {
    const backend = signedIn()
    renderApp('/documents/checklist')
    await screen.findByRole('heading', { level: 1, name: 'Do I have what I need?' })
    expect(screen.queryByRole('button', { name: /^Proof of address/ })).toBeNull()
    expect(choice('Proof of disability')).toHaveTextContent('A disability certificate')
    expect(choice('Proof of widowhood')).toHaveTextContent("Your husband's or wife's death certificate")
    expect(choice('Proof of BPL')).toHaveTextContent('A ration card (AAY, BPL or PHH)')
    expect(choice('Proof of income')).toBeInTheDocument()
    expect(backend.sent(MATCH)).toEqual([])
  })

  it('sends a new copy waiting for a check to its own check', async () => {
    const backend = signedIn()
    backend.on('GET /api/v1/documents', () =>
      ok([makeDocument({ documentId: 'income', replacement: { version: 3, entry: 'upload', status: 'needs_review' } })]),
    )
    renderApp('/documents/checklist?need=proof_of_income')

    expect(await screen.findByRole('link', { name: 'Check it now' })).toHaveAttribute(
      'href',
      '/documents/income/review?version=3',
    )
  })

  it('can change what the office asked for, starting from the ticks it had', async () => {
    const user = userEvent.setup()
    signedIn()
    renderApp('/documents/checklist?need=proof_of_age,health_cover')

    await user.click(await screen.findByRole('link', { name: 'Change what they asked for' }))
    expect(choice('Proof of age')).toHaveAttribute('aria-pressed', 'true')
    expect(choice('Health cover')).toHaveAttribute('aria-pressed', 'true')
    expect(choice('Proof of income')).toHaveAttribute('aria-pressed', 'false')
    await user.click(choice('Proof of income'))
    await user.click(screen.getByRole('button', { name: 'Check my documents' }))
    const list = await screen.findByRole('list', { name: 'What the office asked for' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(3)
  })

  it('says when it could not look, and tries again', async () => {
    const user = userEvent.setup()
    const backend = signedIn()
    let answer = fail(503, 'STORAGE_UNAVAILABLE', 'DATABASE_URL is not set')
    backend.on(MATCH, () => answer)
    renderApp('/documents/checklist?need=proof_of_age')

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'We could not look at your documents. Your documents cannot be opened right now. Please try again in a little while.',
    )
    answer = ok({ asOf: '2026-11-19', matches: [], proofs: [{ proof: 'proof_of_age', ...ANSWERS.proof_of_age }] })
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('Ready')).toBeInTheDocument()
  })
})
