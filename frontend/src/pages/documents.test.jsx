import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import { MESSAGES } from '../api/client'
import { saveSession } from '../api/session'
import { SAY } from '../documents/prepareFile'
import { LIST_CHECK_MS, READ_CHECK_MS } from '../documents/useDocuments'
import { fail, fakeBackend, makeDocument, makeProfile, MEERA, ok, serveDocuments, serveProfile } from '../test/fakeBackend'

const LIST = 'GET /api/v1/documents'
const UPLOAD = 'POST /api/v1/documents'
const PATCH_PROFILE = 'PATCH /api/v1/me/profile'

function signedIn({ profile = makeProfile(), documents = [] } = {}) {
  saveSession('token-meera', 3600)
  const backend = fakeBackend({ 'GET /api/v1/auth/me': () => ok(MEERA) })
  serveProfile(backend, profile)
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

const photo = (name = 'card.jpg', size = 2000) => new File([new Uint8Array(size)], name, { type: 'image/jpeg' })
const pdf = (name = 'report.pdf', size = 2000) => new File([new Uint8Array(size)], name, { type: 'application/pdf' })
const typeButton = (name) => screen.getByRole('button', { name })
const fromPhone = () => screen.getByLabelText('Photo or PDF from this phone')

describe('My documents', () => {
  it('is reached from Home and lists the documents, the ones that need the person first', async () => {
    const user = userEvent.setup()
    signedIn({
      documents: [
        makeDocument({ documentId: 'income', status: 'confirmed', expiryDate: '2099-08-28' }),
        makeDocument({ documentId: 'bank', documentType: 'bank_passbook', status: 'needs_review' }),
        makeDocument({ documentId: 'rx', documentType: 'prescription', status: 'processing' }),
        makeDocument({ documentId: 'home', documentType: 'residence_proof', status: 'confirmed', expiryDate: '2020-03-31' }),
        makeDocument({ documentId: 'id', documentType: 'identity', status: 'needs_reupload' }),
      ],
    })
    renderApp('/')

    await user.click(await screen.findByRole('link', { name: /My documents/ }))
    expect(screen.getByRole('heading', { level: 1, name: 'My documents' })).toBeInTheDocument()
    expect(document.title).toBe('My documents · SeniorLife')

    const list = await screen.findByRole('list', { name: 'My documents' })
    const cards = within(list).getAllByRole('link')
    expect(cards.map((card) => card.textContent)).toEqual([
      'Bank passbookPlease checkWaiting for you to check the details.',
      'PrescriptionReadingBeing read. You can leave this screen.',
      'ID cardPhoto not clearPlease add a clearer photo.',
      'Residence proofExpiredEnded 31 Mar 2020',
      'Income certificateReadyValid till 28 Aug 2099',
    ])
    expect(cards.map((card) => card.getAttribute('href'))).toEqual([
      '/documents/bank',
      '/documents/rx',
      '/documents/id',
      '/documents/home',
      '/documents/income',
    ])
    expect(screen.getByText(/Only you can open these/)).toBeInTheDocument()
  })

  it('says when there are none yet, and offers to add one', async () => {
    signedIn()
    renderApp('/documents')

    expect(await screen.findByText(/No documents yet/)).toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'My documents' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Add a document' })).toHaveAttribute('href', '/documents/add')
  })

  it('says when the documents cannot be opened, and tries again', async () => {
    const user = userEvent.setup()
    const { backend } = signedIn({ documents: [makeDocument()] })
    backend.on(LIST, () => fail(503, 'STORAGE_UNAVAILABLE', 'Document storage needs SENIORLIFE_FILE_KEY'))
    renderApp('/documents')

    expect(await screen.findByRole('alert')).toHaveTextContent(`We could not open your documents. ${MESSAGES.storageDown}`)

    backend.on(LIST, () => ok([makeDocument()]))
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('link', { name: /Income certificate/ })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('looks again while a document is being read, and stops once none is', async () => {
    fakeClock()
    const { backend, store } = signedIn({ documents: [makeDocument({ status: 'processing' })] })
    renderApp('/documents')
    expect(await screen.findByRole('link', { name: /Reading/ })).toBeInTheDocument()

    store.change('doc-1', { status: 'needs_review' })
    await later(LIST_CHECK_MS)
    expect(screen.getByRole('link', { name: /Please check/ })).toBeInTheDocument()

    const asked = backend.sent(LIST).length
    await later(LIST_CHECK_MS * 4)
    expect(backend.sent(LIST)).toHaveLength(asked)
  })
})

describe('Add a document', () => {
  it('sends the photo with what it is and the choices from Settings, then shows it being read', async () => {
    const user = userEvent.setup()
    const { backend } = signedIn({
      profile: makeProfile({ consents: { cloudOcr: true, medicalCloud: false, useLlm: true } }),
    })
    renderApp('/documents')

    await user.click(await screen.findByRole('link', { name: 'Add a document' }))
    expect(screen.getByRole('heading', { level: 1, name: 'Add a document' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Choose what it is first' })).toBeDisabled()

    await user.click(typeButton('Income certificate'))
    expect(typeButton('Income certificate')).toHaveAttribute('aria-pressed', 'true')
    expect(typeButton('Bank passbook')).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('button', { name: 'Add a photo or PDF first' })).toBeDisabled()

    const card = photo('card.jpg')
    await user.upload(screen.getByLabelText('Photo from the camera'), card)
    expect(screen.getByText('Photo added: card.jpg')).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: 'Let a cloud service read it more carefully' })).toBeChecked()

    await user.click(screen.getByRole('button', { name: 'Upload' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Reading your document' })).toBeInTheDocument()
    const [request] = backend.sent(UPLOAD)
    expect(request.body).toBe(card)
    expect(request.headers.get('Content-Type')).toBe('image/jpeg')
    expect(Object.fromEntries(request.query)).toEqual({
      filename: 'card.jpg',
      claimedType: 'income_certificate',
      cloudOcrConsent: 'true',
      medicalCloudConsent: 'false',
      useLlm: 'true',
      background: 'true',
    })
    expect(screen.getByText('Income certificate, photo')).toBeInTheDocument()
  })

  it('asks a second yes for a health paper, for this document only', async () => {
    const user = userEvent.setup()
    const { backend } = signedIn() // everything off in Settings
    renderApp('/documents/add')

    await user.click(await screen.findByRole('button', { name: 'Prescription' }))
    const healthSwitch = () => screen.queryByRole('switch', { name: 'It is a health paper. May it go to the cloud too?' })
    expect(healthSwitch()).not.toBeInTheDocument() // nothing goes to the cloud, so nothing to ask

    await user.click(screen.getByRole('switch', { name: 'Let a cloud service read it more carefully' }))
    expect(healthSwitch()).not.toBeChecked()
    await user.click(healthSwitch())
    expect(healthSwitch()).toBeChecked()

    await user.click(typeButton('Bank passbook'))
    expect(healthSwitch()).not.toBeInTheDocument()
    await user.click(typeButton('Prescription'))

    await user.upload(fromPhone(), pdf('rx.pdf'))
    expect(screen.getByText('PDF added: rx.pdf')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Upload' }))
    await screen.findByRole('heading', { level: 1, name: 'Reading your document' })

    const { query, headers } = backend.sent(UPLOAD)[0]
    expect(headers.get('Content-Type')).toBe('application/pdf')
    expect(query.get('claimedType')).toBe('prescription')
    expect(query.get('cloudOcrConsent')).toBe('true')
    expect(query.get('medicalCloudConsent')).toBe('true')
    expect(query.get('useLlm')).toBe('false')
    expect(backend.sent(PATCH_PROFILE)).toHaveLength(0) // Settings stay as they were
  })

  it('lets the reader work out the type for "I don\'t know", keeping the health choice from Settings', async () => {
    const user = userEvent.setup()
    const { backend } = signedIn({
      profile: makeProfile({ consents: { cloudOcr: false, medicalCloud: true, useLlm: true } }),
    })
    renderApp('/documents/add')

    await user.click(await screen.findByRole('button', { name: "I don't know" }))
    await user.upload(fromPhone(), photo())
    await user.click(screen.getByRole('button', { name: 'Upload' }))
    await screen.findByRole('heading', { level: 1, name: 'Reading your document' })

    const { query } = backend.sent(UPLOAD)[0]
    expect(query.has('claimedType')).toBe(false)
    expect(query.get('cloudOcrConsent')).toBe('false')
    expect(query.get('medicalCloudConsent')).toBe('true')
    expect(query.get('useLlm')).toBe('true')
    expect(screen.getByText('Document, photo')).toBeInTheDocument()
  })

  it('opens with the type already chosen when a document is added again', async () => {
    signedIn()
    const { unmount } = renderApp('/documents/add?type=bank_passbook')
    expect(await screen.findByRole('button', { name: 'Bank passbook' })).toHaveAttribute('aria-pressed', 'true')
    unmount()

    renderApp('/documents/add?type=passport')
    await screen.findByRole('button', { name: 'Bank passbook' })
    for (const button of within(screen.getByRole('group', { name: '1. What is it?' })).getAllByRole('button')) {
      expect(button).toHaveAttribute('aria-pressed', 'false')
    }
  })

  it('does not offer a residence proof or a disability certificate (Decision 74)', async () => {
    signedIn()
    const { unmount } = renderApp('/documents/add')
    const group = await screen.findByRole('group', { name: '1. What is it?' })
    expect(within(group).getAllByRole('button').map((button) => button.textContent)).toEqual([
      'ID card (Aadhaar, PAN)',
      'Income certificate',
      'Health insurance',
      'Bank passbook',
      'Prescription',
      'Lab report',
      'Medical bill',
      "I don't know",
    ])
    unmount()

    // An old link that names one of them chooses nothing.
    renderApp('/documents/add?type=disability_certificate')
    for (const button of within(await screen.findByRole('group', { name: '1. What is it?' })).getAllByRole('button')) {
      expect(button).toHaveAttribute('aria-pressed', 'false')
    }
    expect(screen.getByRole('button', { name: 'Choose what it is first' })).toBeDisabled()
    expect(screen.getByRole('link', { name: 'No photo? Type the details in' })).toHaveAttribute('href', '/documents/type-in')
  })

  it('says in plain words why a file cannot be sent, and sends nothing', async () => {
    const user = userEvent.setup({ applyAccept: false })
    const { backend } = signedIn()
    renderApp('/documents/add')
    await user.click(await screen.findByRole('button', { name: 'Income certificate' }))

    await user.upload(fromPhone(), new File(['hello'], 'notes.txt', { type: 'text/plain' }))
    expect(screen.getByRole('alert')).toHaveTextContent(SAY.notPhotoOrPdf)
    expect(screen.getByRole('button', { name: 'Add a photo or PDF first' })).toBeDisabled()

    await user.upload(fromPhone(), photo('empty.jpg', 0))
    expect(screen.getByRole('alert')).toHaveTextContent(SAY.empty)

    await user.upload(fromPhone(), pdf('big.pdf', 5_000_001))
    expect(screen.getByRole('alert')).toHaveTextContent(SAY.pdfTooBig)

    // A HEIC photo is fine to pick; it has to be redrawn as a JPEG, which this test browser can't do.
    await user.upload(fromPhone(), new File(['heic'], 'IMG_1.heic', { type: 'image/heic' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Upload' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(SAY.photoKind)
    expect(screen.queryByText(/Photo added/)).not.toBeInTheDocument()

    expect(backend.sent(UPLOAD)).toHaveLength(0)
  })

  it('passes on when the reader is busy, and waits as long as the backend says after too many uploads', async () => {
    const user = fakeClock()
    const { backend } = signedIn()
    backend.on(UPLOAD, () =>
      fail(503, 'READER_BUSY', 'Many documents are being read right now; please try again in a few minutes'),
    )
    renderApp('/documents/add')
    await user.click(await screen.findByRole('button', { name: 'Health insurance' }))
    await user.upload(fromPhone(), photo())

    await user.click(screen.getByRole('button', { name: 'Upload' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Many documents are being read right now; please try again in a few minutes',
    )
    expect(screen.getByRole('button', { name: 'Upload' })).toBeEnabled() // the photo is kept for another try

    backend.on(UPLOAD, () =>
      fail(429, 'TOO_MANY_REQUESTS', 'Many documents were added in the last hour. Please wait 2 minutes and try again.', {
        retryAfterSeconds: 120,
      }),
    )
    await user.click(screen.getByRole('button', { name: 'Upload' }))
    expect(await screen.findByRole('button', { name: 'Please wait' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('Please wait 2 minutes and try again.')

    await later(119_000)
    expect(screen.getByRole('button', { name: 'Please wait' })).toBeDisabled()
    await later(1_000)
    expect(screen.getByRole('button', { name: 'Upload' })).toBeEnabled()
    expect(backend.sent(UPLOAD)).toHaveLength(2)
  })
})

describe('A document', () => {
  it('is followed while it is read, then asks for a check, and is no longer asked about', async () => {
    fakeClock()
    const { backend, store } = signedIn({ documents: [makeDocument({ status: 'processing' })] })
    renderApp('/documents/doc-1')

    expect(await screen.findByRole('heading', { level: 1, name: 'Reading your document' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Reading it now. This can take a minute.')
    expect(screen.getByText(/You can leave this screen/)).toBeInTheDocument()

    await later(READ_CHECK_MS) // still reading
    expect(screen.getByRole('heading', { level: 1, name: 'Reading your document' })).toBeInTheDocument()

    store.change('doc-1', {
      status: 'needs_review',
      fields: { name: { value: 'Meera' }, annual_income: { value: 96000 }, issue_date: { value: '2026-04-02' } },
    })
    await later(READ_CHECK_MS)
    expect(screen.getByRole('heading', { level: 1, name: 'Ready for you to check' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('We found 3 details. Please check them before we save anything.')
    expect(screen.getByRole('link', { name: 'Check the details' })).toHaveAttribute('href', '/documents/doc-1/review')
    expect(screen.queryByText(/You can leave this screen/)).not.toBeInTheDocument()

    const asked = backend.sent('GET /api/v1/documents/doc-1').length
    await later(READ_CHECK_MS * 5)
    expect(backend.sent('GET /api/v1/documents/doc-1')).toHaveLength(asked)
  })

  it('keeps trying when a check fails, and says so', async () => {
    fakeClock()
    const { backend, store } = signedIn({ documents: [makeDocument({ status: 'processing' })] })
    renderApp('/documents/doc-1')
    await screen.findByRole('heading', { level: 1, name: 'Reading your document' })

    const route = 'GET /api/v1/documents/doc-1'
    backend.on(route, () => {
      throw new TypeError('Failed to fetch')
    })
    await later(READ_CHECK_MS)
    expect(screen.getByText('We could not check just now. We will try again in a moment.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: 'Reading your document' })).toBeInTheDocument()

    store.change('doc-1', { status: 'confirmed' })
    backend.on(route, () => ok(store.current[0]))
    await later(READ_CHECK_MS * 2) // it waits longer after a failure
    expect(screen.getByRole('heading', { level: 1, name: 'Saved' })).toBeInTheDocument()
    expect(screen.queryByText(/We could not check just now/)).not.toBeInTheDocument()
  })

  it('explains why it could not be read, and offers to add it again', async () => {
    const cases = [
      [{ documentType: 'identity', status: 'needs_reupload' }, 'The photo was not clear enough', '/documents/add?type=identity'],
      [
        { documentType: 'lab_report', status: 'needs_reupload', mimeType: 'application/pdf' },
        'We could not read this PDF',
        '/documents/add?type=lab_report',
      ],
      [{ documentType: null, status: 'needs_classification' }, 'We could not tell what this is', '/documents/add'],
      [{ status: 'rejected' }, 'This document was not kept', '/documents/add?type=income_certificate'],
      [{ status: 'failed_tool' }, 'Something went wrong while reading it', '/documents/add?type=income_certificate'],
    ]
    for (const [changes, heading, again] of cases) {
      signedIn({ documents: [makeDocument(changes)] })
      const { unmount } = renderApp('/documents/doc-1')
      expect(await screen.findByRole('heading', { level: 1, name: heading })).toBeInTheDocument()
      expect(screen.getByRole('link', { name: 'Add it again' })).toHaveAttribute('href', again)
      expect(screen.queryByRole('link', { name: 'Check the details' })).not.toBeInTheDocument()
      unmount()
    }
  })

  it('says when it is saved, and when it has ended', async () => {
    signedIn({ documents: [makeDocument({ expiryDate: '2099-08-28' })] })
    const { unmount } = renderApp('/documents/doc-1')
    expect(await screen.findByRole('heading', { level: 1, name: 'Saved' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('This document is saved and ready to use. Valid till 28 Aug 2099.')
    expect(screen.queryByRole('link', { name: 'Add it again' })).not.toBeInTheDocument()
    unmount()

    signedIn({ documents: [makeDocument({ expiryDate: '2020-03-31' })] })
    renderApp('/documents/doc-1')
    expect(await screen.findByRole('heading', { level: 1, name: 'This document has ended' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('It ended on 31 Mar 2020.')
  })

  it('says when it is not there', async () => {
    signedIn()
    renderApp('/documents/doc-404')

    expect(await screen.findByRole('heading', { level: 1, name: 'We could not find this document' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to My documents' })).toHaveAttribute('href', '/documents')
  })
})
