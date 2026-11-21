import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { App } from '../App'
import { saveSession } from '../api/session'
import { fail, fakeBackend, makeDocument, makeProfile, MEERA, ok, serveDocuments, serveProfile } from '../test/fakeBackend'

const MANUAL = 'POST /api/v1/documents/manual'

function signedIn(documents = []) {
  saveSession('token-meera', 3600)
  const backend = fakeBackend({ 'GET /api/v1/auth/me': () => ok(MEERA) })
  serveProfile(backend, makeProfile())
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

const save = () => screen.getByRole('button', { name: 'Save to my documents' })

describe('Typing a document in', () => {
  it('opens from "Add a document" with the kind chosen there, and sends only what was typed', async () => {
    const user = userEvent.setup()
    const { backend } = signedIn()
    renderApp('/documents/add')

    await user.click(await screen.findByRole('button', { name: 'Income certificate' }))
    const link = screen.getByRole('link', { name: 'No photo? Type the details in' })
    expect(link).toHaveAttribute('href', '/documents/type-in?type=income_certificate')
    await user.click(link)

    expect(screen.getByRole('heading', { level: 1, name: 'Type the details in' })).toBeInTheDocument()
    expect(screen.getByText('Income certificate', { selector: '.lead' })).toBeInTheDocument()
    expect(screen.getByLabelText('Office that issued it (if it is on the paper)')).toBeInTheDocument()
    await user.type(screen.getByLabelText('Name'), 'Meera Rao')
    await user.type(screen.getByLabelText('Yearly income'), 'Rs. 2,56,000')
    await user.type(screen.getByLabelText('Date it was issued'), '2025-12-23')
    await user.click(save())

    expect(await screen.findByRole('heading', { level: 1, name: 'Saved' })).toHaveFocus()
    expect(backend.sent(MANUAL).map((request) => request.body)).toEqual([
      {
        documentType: 'income_certificate',
        fields: { name: 'Meera Rao', annual_income: '256000', issue_date: '2025-12-23' },
      },
    ])
    expect(screen.getByText('Your income certificate is now in My documents. Only you can open it.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'See the document' })).toHaveAttribute('href', '/documents/doc-new-1')
  })

  it('asks what it is first when no kind was chosen, and can choose again', async () => {
    const user = userEvent.setup()
    signedIn()
    renderApp('/documents/add')

    await user.click(await screen.findByRole('link', { name: 'No photo? Type the details in' }))
    expect(screen.getByText('No photo? You can type the details from the paper yourself.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Bank passbook' }))
    expect(screen.getByLabelText('IFSC code')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Not a bank passbook? Choose again' }))
    expect(screen.getByRole('button', { name: 'ID card (Aadhaar, PAN)' })).toBeInTheDocument()
  })

  it('does not offer a residence proof, even from an old link (Decision 74)', async () => {
    signedIn()
    renderApp('/documents/type-in?type=residence_proof')

    const group = await screen.findByRole('group', { name: 'What is it?' })
    expect(within(group).getAllByRole('button').map((button) => button.textContent)).toEqual([
      'ID card (Aadhaar, PAN)',
      'Income certificate',
      'Health insurance',
      'Bank passbook',
      'Disability certificate',
      'Prescription',
      'Lab report',
      'Medical bill',
      "Death certificate (husband's or wife's)",
      'Ration card',
    ])
  })

  it('checks every detail before sending, and moves to the first one to fix', async () => {
    const user = userEvent.setup()
    const { backend } = signedIn()
    renderApp('/documents/type-in?type=identity')

    await user.type(await screen.findByLabelText('Aadhaar number (if it is on the paper)'), '1234')
    await user.click(save())

    expect(backend.sent(MANUAL)).toEqual([])
    expect(screen.getByRole('alert')).toHaveTextContent('Please check the details marked below.')
    expect(screen.getByLabelText('Name')).toHaveFocus()
    expect(screen.getByLabelText('Name')).toHaveAccessibleDescription('Please type this in.')
    expect(screen.getByLabelText('Date of birth')).toHaveAccessibleDescription('Please choose the date.')
    expect(screen.getByLabelText('Aadhaar number (if it is on the paper)')).toHaveAccessibleDescription(
      'An Aadhaar number has 12 digits.',
    )

    await user.type(screen.getByLabelText('Name'), 'M')
    expect(screen.getByLabelText('Name')).not.toHaveAccessibleDescription('Please type this in.')
  })

  it("shows the backend's refusal under the detail it names, then saves once it is fixed", async () => {
    const user = userEvent.setup()
    const { backend } = signedIn()
    backend.on(MANUAL, ({ body }) =>
      body.fields.aadhaar_number === '339986987973'
        ? ok(makeDocument({ documentId: 'doc-id', documentType: 'identity', entry: 'manual' }), 201)
        : fail(422, 'VALIDATION_FAILED', 'Some fields need correcting', { errors: ['aadhaar_number: not valid'] }),
    )
    renderApp('/documents/type-in?type=identity')

    await user.type(await screen.findByLabelText('Name'), 'Meera Rao')
    await user.type(screen.getByLabelText('Date of birth'), '1950-09-17')
    const aadhaar = screen.getByLabelText('Aadhaar number (if it is on the paper)')
    await user.type(aadhaar, '3399 8698 7974')
    await user.click(save())

    expect(await screen.findByText('This Aadhaar number is not right. Please check each digit.')).toBeInTheDocument()
    expect(aadhaar).toHaveFocus()
    expect(screen.getByRole('alert')).toHaveTextContent('Please check the details marked below.')

    await user.clear(aadhaar)
    await user.type(aadhaar, '3399 8698 7973')
    await user.click(save())
    expect(await screen.findByRole('heading', { level: 1, name: 'Saved' })).toBeInTheDocument()
    expect(screen.getByText(/ID numbers are hidden now; we show just the last 4 digits\./)).toBeInTheDocument()
  })

  it("types a prescription's medicines in, and a refused row is marked where it is on screen", async () => {
    const user = userEvent.setup()
    const { backend } = signedIn()
    backend.on(MANUAL, () =>
      fail(422, 'VALIDATION_FAILED', 'Some fields need correcting', { errors: ['medicines row 1: no medicine name'] }),
    )
    renderApp('/documents/type-in?type=prescription')

    await user.type(await screen.findByLabelText('Name'), 'Meera Rao')
    await user.type(screen.getByLabelText('Doctor'), 'Dr. Anil Kumar')
    await user.type(screen.getByLabelText('Prescription date'), '2026-09-01')
    await user.click(screen.getByRole('button', { name: 'Add a medicine' }))
    const second = screen.getByRole('group', { name: 'Medicine 2' })
    await user.type(within(second).getByLabelText('Medicine'), 'Metformin')
    await user.type(within(second).getByLabelText('Strength'), '500 mg')
    await user.click(save())

    expect(backend.sent(MANUAL)[0].body.fields.medicines).toEqual([
      { name: 'Metformin', strength: '500 mg', form: '', frequency: '', duration: '' },
    ])
    expect(await within(second).findByText('Please type the name of the medicine.')).toBeInTheDocument()
    expect(within(screen.getByRole('group', { name: 'Medicine 1' })).queryByText(/Please type/)).toBeNull()
  })

  it('needs at least one medicine on a prescription', async () => {
    const user = userEvent.setup()
    const { backend } = signedIn()
    renderApp('/documents/type-in?type=prescription')

    await user.click(await screen.findByRole('button', { name: 'Remove medicine 1' }))
    await user.click(save())

    expect(backend.sent(MANUAL)).toEqual([])
    expect(screen.getByText('Please add at least one medicine.')).toBeInTheDocument()
  })

  it('types in the new copy of a saved document; it is in use at once', async () => {
    const user = userEvent.setup()
    const { backend } = signedIn([makeDocument({ documentId: 'doc-1' })])
    renderApp('/documents/doc-1/new-copy/type-in')

    expect(await screen.findByText('The new copy of your income certificate')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: 'Type the new details in' })).toBeInTheDocument()
    await user.type(screen.getByLabelText('Name'), 'Meera Rao')
    await user.type(screen.getByLabelText('Yearly income'), '300000')
    await user.type(screen.getByLabelText('Date it was issued'), '2026-10-01')
    await user.click(save())

    expect(await screen.findByText('The new copy of your income certificate is saved. It is the one in use now.')).toBeInTheDocument()
    expect(backend.sent('POST /api/v1/documents/doc-1/versions/manual')[0].body).toEqual({
      fields: { name: 'Meera Rao', annual_income: '300000', issue_date: '2026-10-01' },
    })
    expect(backend.sent(MANUAL)).toEqual([])
  })

  it('says when the backend cannot save it, and keeps what was typed', async () => {
    const user = userEvent.setup()
    const { backend } = signedIn([makeDocument({ documentId: 'doc-1' })])
    backend.on('POST /api/v1/documents/doc-1/versions/manual', () =>
      fail(409, 'CONFLICT', 'A newer version of this document is already being added'),
    )
    renderApp('/documents/doc-1/new-copy/type-in')

    await user.type(await screen.findByLabelText('Name'), 'Meera Rao')
    await user.type(screen.getByLabelText('Yearly income'), '300000')
    await user.type(screen.getByLabelText('Date it was issued'), '2026-10-01')
    await user.click(save())

    expect(await screen.findByRole('alert')).toHaveTextContent('A newer version of this document is already being added')
    expect(screen.getByLabelText('Name')).toHaveValue('Meera Rao')
    expect(save()).toBeEnabled()
  })
})
