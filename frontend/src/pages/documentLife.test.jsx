import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import { saveSession } from '../api/session'
import { READ_CHECK_MS } from '../documents/useDocuments'
import {
  fail,
  fakeBackend,
  makeDocument,
  makeProfile,
  makeReview,
  MEERA,
  ok,
  serveDocuments,
  serveProfile,
  serveReview,
} from '../test/fakeBackend'

// jsdom has no blob: addresses (the review screen shows the photo from one).
beforeEach(() => {
  URL.createObjectURL = vi.fn(() => 'blob:paper-1')
  URL.revokeObjectURL = vi.fn()
})
afterEach(() => {
  delete URL.createObjectURL
  delete URL.revokeObjectURL
  vi.useRealTimers()
})

function signedIn(documents) {
  saveSession('token-meera', 3600)
  const backend = fakeBackend({ 'GET /api/v1/auth/me': () => ok(MEERA) })
  serveProfile(backend, makeProfile({ consents: { useLlm: false, medicalCloud: false, cloudOcr: true } }))
  const store = serveDocuments(backend, documents)
  return { backend, store }
}

function renderApp(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

/** Fake time that still moves on its own, so screens can load; tests jump ahead with later(ms). */
function fakeClock() {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  return userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
}

async function later(ms) {
  await act(() => vi.advanceTimersByTimeAsync(ms))
}

const field = (value) => ({ value, sourceRef: 'entered_by_owner', reviewedBy: 'u-1', correctedByUser: false })
const copy = (status) => ({ version: 2, entry: 'upload', status, reviewFlags: [], warnings: [], errors: [] })

describe('A saved document', () => {
  it('shows its details in the order they are checked, with ID numbers hidden', async () => {
    signedIn([
      makeDocument({
        documentType: 'identity',
        entry: 'manual',
        filename: null,
        mimeType: null,
        fileStored: false,
        fields: {
          aadhaar_number: field('XXXX XXXX 7973'),
          date_of_birth: field('1950-09-17'),
          name: field('Meera Rao'),
        },
      }),
    ])
    renderApp('/documents/doc-1')

    const details = await screen.findByRole('region', { name: 'The details' })
    const items = within(details)
      .getAllByRole('term')
      .map((term) => `${term.textContent}: ${term.nextSibling.textContent}`)
    expect(items).toEqual(['Name: Meera Rao', 'Date of birth: 17 Sept 1950', 'Aadhaar number: XXXX XXXX 7973'])
    expect(within(details).getByText('You typed these details in.')).toBeInTheDocument()
    expect(screen.getByText('ID card, typed in')).toBeInTheDocument()
  })

  it('shows a list as one line per row', async () => {
    signedIn([
      makeDocument({
        documentType: 'prescription',
        fields: {
          name: field('Meera Rao'),
          medicines: field([{ name: 'Metformin', strength: '500 mg', frequency: '1-0-1' }]),
        },
      }),
    ])
    renderApp('/documents/doc-1')

    const details = await screen.findByRole('region', { name: 'The details' })
    expect(within(details).getByRole('listitem')).toHaveTextContent('Metformin 500 mg, 1-0-1')
  })
})

describe('A new copy (a renewed paper)', () => {
  it('is added as a photo, read, and checked against its own photo', async () => {
    const user = fakeClock()
    const { backend, store } = signedIn([makeDocument({ documentId: 'doc-1' })])
    renderApp('/documents/doc-1')

    await user.click(await screen.findByRole('link', { name: 'Got a renewed one? Add the new copy' }))
    expect(screen.getByRole('heading', { level: 1, name: 'Add the new copy' })).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: '1. What is it?' })).toBeNull() // its kind is known
    expect(screen.getByRole('link', { name: 'No photo? Type the new details in' })).toHaveAttribute(
      'href',
      '/documents/doc-1/new-copy/type-in',
    )
    await user.upload(
      screen.getByLabelText('Photo or PDF from this phone'),
      new File([new Uint8Array(2000)], 'renewed.jpg', { type: 'image/jpeg' }),
    )
    await user.click(screen.getByRole('button', { name: 'Upload' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Reading the new copy' })).toBeInTheDocument()
    const [sent] = backend.sent('POST /api/v1/documents/doc-1/versions')
    expect(sent.query.get('filename')).toBe('renewed.jpg')
    expect(sent.query.get('cloudOcrConsent')).toBe('true')
    expect(sent.query.has('claimedType')).toBe(false)
    expect(backend.sent('POST /api/v1/documents')).toEqual([])
    expect(screen.queryByRole('button', { name: 'Delete this document' })).toBeNull() // not while it is read

    store.change('doc-1', { replacement: copy('needs_review') })
    await later(READ_CHECK_MS)
    const check = await screen.findByRole('link', { name: 'Check the new copy' })
    expect(check).toHaveAttribute('href', '/documents/doc-1/review?version=2')

    serveReview(backend, makeReview())
    await user.click(check)
    expect(await screen.findByRole('img', { name: 'Your document' })).toBeInTheDocument()
    expect(backend.sent('GET /api/v1/documents/doc-1/file')[0].query.get('version')).toBe('2')
  })

  it('says it is saved, and in use, when its check is done', async () => {
    const user = userEvent.setup()
    const { backend } = signedIn([makeDocument({ replacement: copy('needs_review') })])
    serveReview(backend, makeReview())
    renderApp('/documents/doc-1/review?version=2')

    for (const label of ['Name', 'Yearly income', 'Date it was issued']) {
      await screen.findByRole('heading', { name: label })
      await user.click(screen.getByRole('button', { name: 'Yes, this is right' }))
    }
    await user.click(screen.getByRole('button', { name: 'Save to my documents' }))
    expect(await screen.findByText('The new copy of your income certificate is saved. It is the one in use now.')).toBeInTheDocument()
  })

  it('can be refused at its check, and the old copy stays in use', async () => {
    const user = userEvent.setup()
    const { backend } = signedIn([makeDocument({ replacement: copy('needs_review') })])
    serveReview(backend, makeReview())
    renderApp('/documents/doc-1/review?version=2')

    await user.click(await screen.findByRole('button', { name: "Don't save this document" }))
    expect(screen.getByRole('heading', { level: 1, name: "Don't save this new copy?" })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: "Yes, don't save it" }))
    expect(await screen.findByText('We will not keep this new copy. Your old one is still in use.')).toBeInTheDocument()
  })

  it("can't be added while another one is on the way", async () => {
    signedIn([makeDocument({ replacement: copy('needs_review') })])
    renderApp('/documents/doc-1/new-copy')

    expect(await screen.findByRole('heading', { level: 1, name: "A new copy can't be added now" })).toBeInTheDocument()
    expect(
      screen.getByText('A new copy is already being read or is waiting for your check. Please finish that one first.'),
    ).toBeInTheDocument()
    expect(screen.queryByLabelText('Photo or PDF from this phone')).toBeNull()
  })

  it('is asked for first when a document has ended', async () => {
    signedIn([makeDocument({ expiryDate: '2020-03-31' })])
    renderApp('/documents/doc-1')

    expect(await screen.findByRole('heading', { level: 1, name: 'This document has ended' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Add the new copy' })).toHaveAttribute('href', '/documents/doc-1/new-copy')
    expect(screen.queryByRole('link', { name: 'Got a renewed one? Add the new copy' })).toBeNull()
  })

  it('that could not be read is explained, and can be added again', async () => {
    signedIn([makeDocument({ replacement: copy('needs_reupload') })])
    renderApp('/documents/doc-1')

    expect(
      await screen.findByText('The new copy could not be read, so it was not kept. Your old copy is still in use.'),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Add the new copy again' })).toBeInTheDocument()
  })
})

describe('Deleting a document', () => {
  it('asks first; "No" keeps it, "Yes" removes it for good', async () => {
    const user = userEvent.setup()
    const { backend } = signedIn([makeDocument()])
    renderApp('/documents/doc-1')

    await user.click(await screen.findByRole('button', { name: 'Delete this document' }))
    expect(screen.getByRole('heading', { level: 1, name: 'Delete this document?' })).toHaveFocus()
    expect(
      screen.getByText(
        'Your income certificate and its photo will be removed for good, with every copy of it. This cannot be undone.',
      ),
    ).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'No, keep it' }))
    expect(screen.getByRole('heading', { level: 1, name: 'Saved' })).toBeInTheDocument()
    expect(backend.sent('DELETE /api/v1/documents/doc-1')).toEqual([])

    await user.click(screen.getByRole('button', { name: 'Delete this document' }))
    await user.click(screen.getByRole('button', { name: 'Yes, delete it' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Deleted' })).toBeInTheDocument()
    expect(backend.sent('DELETE /api/v1/documents/doc-1')).toHaveLength(1)
    expect(screen.getByRole('link', { name: 'Back to My documents' })).toHaveAttribute('href', '/documents')
  })

  it("says why when it can't be deleted just now", async () => {
    const user = userEvent.setup()
    const { backend } = signedIn([makeDocument({ status: 'needs_review' })])
    backend.on('DELETE /api/v1/documents/doc-1', () =>
      fail(409, 'CONFLICT', 'This document is being read right now; try again in a moment'),
    )
    renderApp('/documents/doc-1')

    await user.click(await screen.findByRole('button', { name: 'Delete this document' }))
    await user.click(screen.getByRole('button', { name: 'Yes, delete it' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('This document is being read right now; try again in a moment')
    expect(screen.getByRole('button', { name: 'Yes, delete it' })).toBeEnabled()
  })
})
