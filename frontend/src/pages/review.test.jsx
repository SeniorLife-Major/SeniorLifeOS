import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import { saveSession } from '../api/session'
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

const SEND = 'POST /api/v1/documents/doc-1/review'

// jsdom has no blob: addresses; the screen's use of them is followed here.
beforeEach(() => {
  URL.createObjectURL = vi.fn(() => 'blob:paper-1')
  URL.revokeObjectURL = vi.fn()
})
afterEach(() => {
  delete URL.createObjectURL
  delete URL.revokeObjectURL
})

function signedIn(review, options) {
  saveSession('token-meera', 3600)
  const backend = fakeBackend({ 'GET /api/v1/auth/me': () => ok(MEERA) })
  serveProfile(backend, makeProfile())
  serveDocuments(backend, [makeDocument({ status: 'needs_review', documentType: review.documentType })])
  serveReview(backend, review, options)
  return backend
}

function renderApp(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

const detail = (name) => screen.findByRole('heading', { level: 2, name })
const yes = () => screen.getByRole('button', { name: 'Yes, this is right' })
const press = (user, name) => user.click(screen.getByRole('button', { name }))

/** Says "Yes, this is right" to each detail named, in turn. */
async function accept(user, ...names) {
  for (const name of names) {
    await detail(name)
    await user.click(yes())
  }
}

function summaryItem(label) {
  const list = screen.getByRole('list', { name: 'The details' })
  return within(list)
    .getAllByRole('listitem')
    .find((item) => item.querySelector('.summary__label')?.textContent === label)
}

const INCOME = makeReview({
  candidates: { name: 'Meera Rao', annual_income: 256000, issuing_authority: 'Tahsildar, Demo Taluka' },
  missingFields: ['issue_date'],
  optionalFields: ['expiry_date'],
  flags: ['annual_income: low confidence (0.61)', 'issue_date: missing or invalid'],
})

describe('Checking the details', () => {
  it('goes through each detail with the photo, and saves only what the person changed or added', async () => {
    const user = userEvent.setup()
    const backend = signedIn(INCOME)
    renderApp('/documents/doc-1')

    await user.click(await screen.findByRole('link', { name: 'Check the details' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Check your income certificate' })).toBeInTheDocument()
    expect(await screen.findByRole('img', { name: 'Your document' })).toHaveAttribute('src', 'blob:paper-1')
    expect(backend.sent('GET /api/v1/documents/doc-1/file')[0].headers.get('Authorization')).toBe('Bearer token-meera')

    expect(await detail('Name')).toHaveFocus()
    expect(screen.getByText('1 of 5')).toBeInTheDocument()
    expect(screen.getByText('Meera Rao')).toBeInTheDocument()
    await user.click(yes())

    await detail('Yearly income')
    expect(screen.getByText('₹2,56,000')).toBeInTheDocument()
    expect(screen.getByText('We were not sure we read this right.')).toBeInTheDocument()
    await press(user, 'Change it')
    const box = screen.getByLabelText('Type it as printed on the paper.')
    expect(box).toHaveValue('256000')
    await user.clear(box)
    await user.type(box, '2,66,000')
    await press(user, 'Save')

    await detail('Date it was issued')
    const date = screen.getByLabelText('We could not find this on the paper. Please type it as printed.')
    await press(user, 'Save')
    expect(screen.getByText('Please choose the date.')).toBeInTheDocument()
    await user.type(date, '2025-12-23')
    await press(user, 'Save')

    await accept(user, 'Office that issued it')
    await detail('Valid till')
    expect(screen.getByLabelText('If this is on the paper, you may add it.')).toBeInTheDocument()
    await press(user, 'Skip')

    expect(await screen.findByRole('heading', { level: 1, name: 'All checked. Save it?' })).toHaveFocus()
    expect(summaryItem('Name')).toHaveTextContent('Meera Rao')
    expect(summaryItem('Yearly income')).toHaveTextContent('₹2,66,000You changed this')
    expect(summaryItem('Date it was issued')).toHaveTextContent('23 Dec 2025You added this')
    expect(summaryItem('Valid till')).toHaveTextContent('Not added')

    await press(user, 'Save to my documents')
    expect(await screen.findByRole('heading', { level: 1, name: 'Saved' })).toBeInTheDocument()
    expect(screen.getByText(/Your income certificate is now in My documents/)).toBeInTheDocument()
    expect(backend.sent(SEND).map((request) => request.body)).toEqual([
      { action: 'confirm', edits: { annual_income: '266000', issue_date: '2025-12-23' } },
    ])
    expect(screen.getByRole('link', { name: 'Back to My documents' })).toHaveAttribute('href', '/documents')
  })

  it('checks what is typed before moving on, and can go back a detail or cancel a change', async () => {
    const user = userEvent.setup()
    signedIn(makeReview())
    renderApp('/documents/doc-1/review')

    await accept(user, 'Name')
    await detail('Yearly income')
    await press(user, 'Change it')
    const box = screen.getByLabelText('Type it as printed on the paper.')
    await user.clear(box)
    await user.type(box, 'about two lakh')
    await press(user, 'Save')
    expect(screen.getByText('Please type the amount in numbers, like 256000.')).toBeInTheDocument()
    expect(box).toHaveFocus()
    expect(box).toHaveAttribute('aria-invalid', 'true')

    await press(user, 'Cancel')
    expect(screen.getByText('₹2,56,000')).toBeInTheDocument()

    await press(user, 'Previous detail')
    expect(await detail('Name')).toBeInTheDocument()
    await user.click(yes())
    await user.click(yes()) // the income, as read
    await accept(user, 'Date it was issued')
    expect(await screen.findByRole('heading', { level: 1, name: 'All checked. Save it?' })).toBeInTheDocument()
  })

  it('lets one detail be changed from the summary, and comes straight back', async () => {
    const user = userEvent.setup()
    const backend = signedIn(makeReview())
    renderApp('/documents/doc-1/review')
    await accept(user, 'Name', 'Yearly income', 'Date it was issued')

    await user.click(await screen.findByRole('button', { name: 'Change Name' }))
    await detail('Name')
    expect(screen.queryByRole('button', { name: 'Previous detail' })).not.toBeInTheDocument()
    await press(user, 'Change it')
    const box = screen.getByLabelText('Type it as printed on the paper.')
    await user.clear(box)
    await user.type(box, 'Meera R. Rao')
    await press(user, 'Save')

    expect(await screen.findByRole('heading', { level: 1, name: 'All checked. Save it?' })).toBeInTheDocument()
    expect(summaryItem('Name')).toHaveTextContent('Meera R. RaoYou changed this')
    expect(summaryItem('Yearly income')).not.toHaveTextContent('You changed this')

    await press(user, 'Save to my documents')
    await screen.findByRole('heading', { level: 1, name: 'Saved' })
    expect(backend.sent(SEND)[0].body).toEqual({ action: 'confirm', edits: { name: 'Meera R. Rao' } })
  })

  it('offers the list of states for a state', async () => {
    const user = userEvent.setup()
    const backend = signedIn(
      makeReview({
        documentType: 'residence_proof',
        candidates: { name: 'Meera Rao', issue_date: '2025-04-02' },
        missingFields: ['residence_state'],
      }),
    )
    renderApp('/documents/doc-1/review')
    await accept(user, 'Name')

    await detail('State')
    await user.selectOptions(screen.getByLabelText('We could not find this on the paper. Please type it as printed.'), 'Kerala')
    await press(user, 'Save')
    await accept(user, 'Date it was issued')

    expect(summaryItem('State')).toHaveTextContent('KeralaYou added this')
    await press(user, 'Save to my documents')
    await screen.findByRole('heading', { level: 1, name: 'Saved' })
    expect(backend.sent(SEND)[0].body.edits).toEqual({ residence_state: 'KL' })
  })
})

describe('Saving an ID card', () => {
  it('says the ID number is hidden now, only when one was saved', async () => {
    const user = userEvent.setup()
    const card = {
      documentType: 'identity',
      candidates: { name: 'Meera Rao', date_of_birth: '1956-03-14' },
      optionalFields: ['aadhaar_number', 'pan_number'],
    }
    signedIn(makeReview(card))
    const first = renderApp('/documents/doc-1/review')
    expect(await screen.findByRole('heading', { level: 1, name: 'Check your ID card' })).toBeInTheDocument()
    await accept(user, 'Name', 'Date of birth')
    for (const name of ['Aadhaar number', 'PAN number']) {
      await detail(name)
      await press(user, 'Skip')
    }
    await press(user, 'Save to my documents')
    expect(await screen.findByText(/Your ID card is now in My documents/)).not.toHaveTextContent('ID numbers are hidden')
    first.unmount()

    signedIn(makeReview({ ...card, candidates: { ...card.candidates, aadhaar_number: '234567890124' }, optionalFields: ['pan_number'] }))
    renderApp('/documents/doc-1/review')
    await accept(user, 'Name', 'Date of birth')
    await detail('Aadhaar number')
    expect(screen.getByText('2345 6789 0124')).toBeInTheDocument() // the full number, to compare with the card
    await user.click(yes())
    await detail('PAN number')
    await press(user, 'Skip')
    await press(user, 'Save to my documents')
    expect(await screen.findByText(/Your ID card is now in My documents/)).toHaveTextContent(
      'ID numbers are hidden now; we show just the last 4 digits.',
    )
  })
})

describe('Not saving a document', () => {
  it('asks first, goes back on "No", and tells the backend on "Yes"', async () => {
    const user = userEvent.setup()
    const backend = signedIn(makeReview())
    renderApp('/documents/doc-1/review')
    await detail('Name')

    await press(user, "Don't save this document")
    expect(screen.getByRole('heading', { level: 1, name: "Don't save this document?" })).toHaveFocus()
    await press(user, 'No, go back')
    expect(await detail('Name')).toBeInTheDocument()
    expect(backend.sent(SEND)).toHaveLength(0)

    await accept(user, 'Name', 'Yearly income', 'Date it was issued')
    await screen.findByRole('heading', { level: 1, name: 'All checked. Save it?' })
    await press(user, "Don't save this document")
    await press(user, 'No, go back')
    expect(screen.getByRole('heading', { level: 1, name: 'All checked. Save it?' })).toBeInTheDocument()

    await press(user, "Don't save this document")
    await press(user, "Yes, don't save it")
    expect(await screen.findByRole('heading', { level: 1, name: 'Not saved' })).toBeInTheDocument()
    expect(backend.sent(SEND).map((request) => request.body)).toEqual([{ action: 'reject', edits: {} }])
    expect(screen.getByRole('link', { name: 'Add a document' })).toHaveAttribute(
      'href',
      '/documents/add?type=income_certificate',
    )
  })
})

describe('When saving does not work', () => {
  it('shows which details the backend would not take, and saves on the next try', async () => {
    const user = userEvent.setup()
    const backend = signedIn(makeReview())
    backend.on(SEND, () =>
      ok(makeDocument({ status: 'needs_review', errors: ['Correct these required fields: annual_income'] })),
    )
    renderApp('/documents/doc-1/review')
    await accept(user, 'Name', 'Yearly income', 'Date it was issued')

    await press(user, 'Save to my documents')
    expect(await screen.findByRole('alert')).toHaveTextContent('Please fill in: Yearly income.')
    expect(summaryItem('Yearly income')).toHaveTextContent('Please check this one')
    expect(summaryItem('Name')).not.toHaveTextContent('Please check this one')

    await user.click(screen.getByRole('button', { name: 'Change Yearly income' }))
    await press(user, 'Change it')
    const box = screen.getByLabelText('Type it as printed on the paper.')
    await user.clear(box)
    await user.type(box, '256500')
    await press(user, 'Save')
    expect(summaryItem('Yearly income')).not.toHaveTextContent('Please check this one')

    serveReview(backend, makeReview()) // the backend takes it now
    await press(user, 'Save to my documents')
    expect(await screen.findByRole('heading', { level: 1, name: 'Saved' })).toBeInTheDocument()
    expect(backend.sent(SEND).map((request) => request.body.edits)).toEqual([{}, { annual_income: '256500' }])
  })

  it("passes on the backend's problem and lets the person try again", async () => {
    const user = userEvent.setup()
    const backend = signedIn(makeReview())
    backend.on(SEND, () =>
      fail(500, 'WORKFLOW_EXECUTION_FAILED', 'The review could not be applied; please try again'),
    )
    renderApp('/documents/doc-1/review')
    await accept(user, 'Name', 'Yearly income', 'Date it was issued')

    await press(user, 'Save to my documents')
    expect(await screen.findByRole('alert')).toHaveTextContent('The review could not be applied; please try again')
    expect(screen.getByRole('button', { name: 'Save to my documents' })).toBeEnabled()

    serveReview(backend, makeReview())
    await press(user, 'Save to my documents')
    expect(await screen.findByRole('heading', { level: 1, name: 'Saved' })).toBeInTheDocument()
  })
})

describe('Lists', () => {
  it('shows every row with its notes, and sends the whole list when it is changed', async () => {
    const user = userEvent.setup()
    const backend = signedIn(
      makeReview({
        documentType: 'prescription',
        candidates: {
          name: 'Meera Rao',
          doctor_name: 'Dr. A. Kulkarni',
          prescription_date: '2026-11-02',
          medicines: [
            { name: 'Metformin', strength: '500 mg', form: 'tablet', frequency: '1-0-1', duration: '30 days', source_ref: 'p1:l4' },
            { name: null, strength: '5 mg', form: null, frequency: '0-0-1', duration: null, problems: ['no medicine name'] },
          ],
        },
        flags: ['medicines: please check every row (2)', 'medicines row 2: no medicine name'],
      }),
    )
    renderApp('/documents/doc-1/review')
    await accept(user, 'Name', 'Doctor', 'Prescription date')

    await detail('Medicines')
    expect(screen.getByText('Metformin 500 mg, tablet, 1-0-1, 30 days')).toBeInTheDocument()
    expect(screen.getByText('No name 5 mg, 0-0-1')).toBeInTheDocument()
    expect(screen.getByText('Medicine 2: the medicine has no name.')).toBeInTheDocument()

    await press(user, 'Change them')
    await press(user, 'Save')
    expect(screen.getByRole('alert')).toHaveTextContent('Medicine 2: Please type the name of the medicine.')

    const second = screen.getByRole('group', { name: 'Medicine 2' })
    await user.type(within(second).getByLabelText('Medicine'), 'Amlodipine')
    await press(user, 'Add a medicine')
    expect(screen.getByRole('group', { name: 'Medicine 3' })).toBeInTheDocument()
    await press(user, 'Remove medicine 3')
    expect(screen.queryByRole('group', { name: 'Medicine 3' })).not.toBeInTheDocument()
    await press(user, 'Save')

    expect(await screen.findByRole('heading', { level: 1, name: 'All checked. Save it?' })).toBeInTheDocument()
    expect(summaryItem('Medicines')).toHaveTextContent('Amlodipine 5 mg, 0-0-1')
    expect(summaryItem('Medicines')).toHaveTextContent('You changed this')
    await press(user, 'Save to my documents')
    await screen.findByRole('heading', { level: 1, name: 'Saved' })
    expect(backend.sent(SEND)[0].body.edits).toEqual({
      medicines: [
        { name: 'Metformin', strength: '500 mg', form: 'tablet', frequency: '1-0-1', duration: '30 days' },
        { name: 'Amlodipine', strength: '5 mg', form: '', frequency: '0-0-1', duration: '' },
      ],
    })
  })

  it('asks for the rows of a required list that was not read', async () => {
    const user = userEvent.setup()
    const backend = signedIn(
      makeReview({
        documentType: 'lab_report',
        candidates: { name: 'Meera Rao', lab_name: 'City Lab', report_date: '2026-11-10' },
        missingFields: ['tests'],
      }),
    )
    renderApp('/documents/doc-1/review')
    await accept(user, 'Name', 'Lab', 'Report date')

    await detail('Tests')
    await press(user, 'Save')
    expect(screen.getByRole('alert')).toHaveTextContent('Please add at least one test.')

    const first = screen.getByRole('group', { name: 'Test 1' })
    await user.type(within(first).getByLabelText('Test'), 'HbA1c')
    await user.type(within(first).getByLabelText('Result'), '6.1')
    await user.type(within(first).getByLabelText('Unit'), '%')
    await press(user, 'Save')

    expect(summaryItem('Tests')).toHaveTextContent('HbA1c: 6.1 %')
    expect(summaryItem('Tests')).toHaveTextContent('You added this')
    await press(user, 'Save to my documents')
    await screen.findByRole('heading', { level: 1, name: 'Saved' })
    expect(backend.sent(SEND)[0].body.edits).toEqual({ tests: [{ name: 'HbA1c', value: '6.1', unit: '%', ref_range: '' }] })
  })
})

describe('The photo and the screen around it', () => {
  it('offers to open a PDF, and lets go of the file when the screen closes', async () => {
    signedIn(makeReview(), { file: { bytes: new Uint8Array([37, 80, 68, 70]), type: 'application/pdf' } })
    const { unmount } = renderApp('/documents/doc-1/review')

    expect(await screen.findByRole('link', { name: 'Open the PDF' })).toHaveAttribute('href', 'blob:paper-1')
    expect(URL.createObjectURL.mock.calls[0][0].type).toBe('application/pdf')
    unmount()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:paper-1')
  })

  it('still lets the person check the details when the photo cannot be shown', async () => {
    signedIn(makeReview(), { file: null })
    renderApp('/documents/doc-1/review')

    expect(await screen.findByText(/The photo could not be shown here/)).toBeInTheDocument()
    expect(await detail('Name')).toBeInTheDocument()
  })

  it('says when nothing is waiting for a check, or the document is gone, or the details cannot be opened', async () => {
    const user = userEvent.setup()
    const backend = signedIn(makeReview())
    backend.on('GET /api/v1/documents/doc-1/review', () => fail(409, 'CONFLICT', 'This document is not waiting for review'))
    const { unmount } = renderApp('/documents/doc-1/review')
    expect(await screen.findByRole('heading', { level: 1, name: 'Nothing to check here' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'See the document' })).toHaveAttribute('href', '/documents/doc-1')
    unmount()

    const gone = renderApp('/documents/doc-404/review')
    expect(await screen.findByRole('heading', { level: 1, name: 'We could not find this document' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to My documents' })).toHaveAttribute('href', '/documents')
    gone.unmount()

    backend.on('GET /api/v1/documents/doc-1/review', () => fail(503, 'STORAGE_UNAVAILABLE', 'set SENIORLIFE_FILE_KEY'))
    renderApp('/documents/doc-1/review')
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'We could not open the details. Your documents cannot be opened right now.',
    )
    serveReview(backend, makeReview())
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await detail('Name')).toBeInTheDocument()
  })
})
