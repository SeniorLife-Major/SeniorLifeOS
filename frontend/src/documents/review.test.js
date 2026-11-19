import { describe, expect, it } from 'vitest'
import { notesFor, refusalOf } from './reviewNotes'
import { answerFor, buildSteps, editsFrom } from './reviewSteps'

const INCOME = ['name', 'annual_income', 'issue_date', 'issuing_authority', 'expiry_date']

describe('notesFor', () => {
  it("turns the reader's notes into plain sentences under their detail", () => {
    const notes = notesFor(
      [
        'name: read by the AI vision model from the picture, please check',
        'name: low confidence (0.72)',
        'annual_income: look-alike characters corrected (O→0), please check',
        'issuing_authority: not found in the text read on this machine, please check carefully',
        'issue_date is in the future',
        'document is expired',
        'issue_date: missing or invalid',
        'issuing_authority: something new the backend says, please check',
      ],
      INCOME,
    )
    expect(notes).toEqual({
      name: ['Read by an AI from the picture.', 'We were not sure we read this right.'],
      annual_income: ['Some letters or digits that look alike were changed (like O and 0).'],
      issuing_authority: ['We could not find this in the words we read.', 'Please check this one carefully.'],
      issue_date: ['This date is in the future.'],
      expiry_date: ['This date has passed.'],
    })
  })

  it('names the row of a list, and the bill total that does not add up', () => {
    const notes = notesFor(
      [
        'items: please check every row (2)',
        'items row 2: amount is not readable',
        'items row 2: no description',
        'items add up to 1700.00 but the total says 1750.00',
      ],
      ['name', 'provider', 'bill_date', 'total_amount', 'items'],
    )
    expect(notes).toEqual({
      items: ['Item 2: the amount could not be read.', 'Item 2: it does not say what the item is.'],
      total_amount: ['The items add up to ₹1,700, but the total says ₹1,750.'],
    })
  })

  it('leaves out notes about details that are not on this document', () => {
    expect(notesFor(['ifsc: failed format or checksum check', 'something general'], INCOME)).toEqual({})
  })
})

describe('refusalOf', () => {
  it("says which details the backend would not take, by their names", () => {
    expect(refusalOf(['Correct these required fields: annual_income, issue_date'])).toEqual({
      message: 'Please fill in: Yearly income, Date it was issued.',
      fields: ['annual_income', 'issue_date'],
    })
    expect(refusalOf(['Correct these fields: aadhaar_number'])).toEqual({
      message: 'Please check: Aadhaar number.',
      fields: ['aadhaar_number'],
    })
    expect(refusalOf(['Issue date cannot be in the future'])).toEqual({
      message: 'Date it was issued: this date cannot be in the future.',
      fields: ['issue_date'],
    })
    expect(refusalOf(['Edits may only correct extracted fields or fill in this document\'s required or optional fields'])).toEqual({
      message: 'These details could not be saved. Please check them and try again.',
      fields: [],
    })
  })
})

describe('buildSteps', () => {
  it("goes through the details in the document's order: read, missing, and ones that may be added", () => {
    const steps = buildSteps({
      documentType: 'income_certificate',
      candidates: { issuing_authority: 'Tahsildar', name: 'Meera Rao', annual_income: 256000, ward: 'B' },
      missingFields: ['issue_date'],
      optionalFields: ['expiry_date'],
      flags: ['name: low confidence (0.70)'],
    })

    expect(steps.map((step) => [step.key, step.state, step.required])).toEqual([
      ['name', 'read', true],
      ['annual_income', 'read', true],
      ['issue_date', 'missing', true],
      ['issuing_authority', 'read', false],
      ['expiry_date', 'optional', false],
      ['ward', 'read', false], // a detail this app doesn't know comes last
    ])
    expect(steps[0]).toMatchObject({ label: 'Name', kind: 'text', original: 'Meera Rao', notes: ['We were not sure we read this right.'] })
    expect(steps[2]).toMatchObject({ label: 'Date it was issued', kind: 'date', original: null, notes: [] })
  })
})

describe('answers and edits', () => {
  const [income] = buildSteps({ documentType: 'income_certificate', candidates: { annual_income: 256000 } })
  const [tests] = buildSteps({
    documentType: 'lab_report',
    candidates: { tests: [{ name: 'TSH', value: 2.1, unit: 'mIU/L', ref_range: null, flag: null }] },
  })

  it('count typing the same value again as "right as read"', () => {
    expect(answerFor(income, '256000')).toEqual({ status: 'accepted' })
    expect(answerFor(income, '266000')).toEqual({ status: 'changed', value: '266000' })
    expect(answerFor(tests, [{ name: 'TSH', value: '2.1', unit: 'mIU/L', ref_range: '' }])).toEqual({ status: 'accepted' })
    expect(answerFor(tests, [{ name: 'TSH', value: '2.4', unit: 'mIU/L', ref_range: '' }]).status).toBe('changed')
  })

  it('send only what the person changed or added', () => {
    const steps = buildSteps({
      documentType: 'income_certificate',
      candidates: { name: 'Meera Rao', annual_income: 256000 },
      missingFields: ['issue_date'],
      optionalFields: ['expiry_date'],
    })
    const answers = {
      name: { status: 'accepted' },
      annual_income: { status: 'changed', value: '266000' },
      issue_date: { status: 'added', value: '2025-12-23' },
      expiry_date: { status: 'skipped' },
    }
    expect(editsFrom(steps, answers)).toEqual({ annual_income: '266000', issue_date: '2025-12-23' })
  })
})
