import { describe, expect, it } from 'vitest'
import { checkTypeIn, emptyTypeIn, typeInFields, typeInRefusal } from './typeIn'

const NOW = '2026-11-19'
const NO_MEDICINE = { name: '', strength: '', form: '', frequency: '', duration: '' }

describe('typeInFields', () => {
  it('has every detail the backend accepts, required ones marked', () => {
    expect(typeInFields('income_certificate')).toEqual([
      { key: 'name', label: 'Name', kind: 'text', required: true },
      { key: 'annual_income', label: 'Yearly income', kind: 'amount', required: true },
      { key: 'issue_date', label: 'Date it was issued', kind: 'date', required: true },
      { key: 'issuing_authority', label: 'Office that issued it', kind: 'text', required: false },
      { key: 'expiry_date', label: 'Valid till', kind: 'date', required: false },
    ])
    expect(typeInFields('medical_bill').find((field) => field.key === 'items').required).toBe(false)
  })

  it('starts empty, with one row to fill only in a list that must have one', () => {
    expect(emptyTypeIn('identity')).toEqual({ name: '', date_of_birth: '', gender: '', aadhaar_number: '', pan_number: '' })
    expect(emptyTypeIn('prescription').medicines).toEqual([NO_MEDICINE])
    expect(emptyTypeIn('medical_bill').items).toEqual([])
  })
})

describe('checkTypeIn', () => {
  it('sends only what was filled in, each value cleaned as the backend expects', () => {
    const values = {
      ...emptyTypeIn('identity'),
      name: '  Meera   Rao ',
      date_of_birth: '1950-09-17',
      aadhaar_number: '3399 8698 7973',
    }
    expect(checkTypeIn('identity', values, { now: NOW })).toEqual({
      fields: { name: 'Meera Rao', date_of_birth: '1950-09-17', aadhaar_number: '339986987973' },
      problems: {},
      rows: {},
      places: {},
      ok: true,
    })
  })

  it('marks what is missing or wrong', () => {
    const values = { ...emptyTypeIn('income_certificate'), annual_income: 'about two lakh', issue_date: '2027-01-01' }
    const checked = checkTypeIn('income_certificate', values, { now: NOW })
    expect(checked.ok).toBe(false)
    expect(checked.problems).toEqual({
      name: 'Please type this in.',
      annual_income: 'Please type the amount in numbers, like 256000.',
      issue_date: 'This date cannot be in the future.',
    })
  })

  it('checks lists row by row, leaves empty rows out, and remembers where the sent rows are', () => {
    const values = {
      ...emptyTypeIn('prescription'),
      name: 'Meera Rao',
      doctor_name: 'Dr. Anil Kumar',
      prescription_date: '2026-09-01',
      medicines: [NO_MEDICINE, { ...NO_MEDICINE, name: 'Metformin', strength: '500 mg' }],
    }
    const checked = checkTypeIn('prescription', values, { now: NOW })
    expect(checked.ok).toBe(true)
    expect(checked.fields.medicines).toEqual([{ ...NO_MEDICINE, name: 'Metformin', strength: '500 mg' }])
    expect(checked.places).toEqual({ medicines: [1] })

    const noName = checkTypeIn('prescription', { ...values, medicines: [{ ...NO_MEDICINE, strength: '5 mg' }] }, { now: NOW })
    expect(noName.rows).toEqual({ medicines: { 0: 'Please type the name of the medicine.' } })
    const none = checkTypeIn('prescription', { ...values, medicines: [NO_MEDICINE] }, { now: NOW })
    expect(none.rows).toEqual({ medicines: { list: 'Please add at least one medicine.' } })
  })
})

describe('typeInRefusal', () => {
  it("turns the backend's reasons into sentences under each detail", () => {
    const errors = ['aadhaar_number: not valid', 'name: required', 'ifsc: not valid', 'Issue date cannot be in the future']
    expect(typeInRefusal(errors)).toEqual({
      problems: {
        aadhaar_number: 'This Aadhaar number is not right. Please check each digit.',
        name: 'Please type this in.',
        ifsc: 'This does not look right. Please check it.',
        issue_date: 'This date cannot be in the future.',
      },
      rows: {},
      message: 'Please check the details marked below.',
    })
  })

  it("puts a row's reason on the row as it is on screen", () => {
    const refusal = typeInRefusal(['items row 1: amount is not readable', 'items row 2: something new'], {
      items: [2, 4],
    })
    expect(refusal.rows).toEqual({
      items: { 2: 'Please type the amount in numbers, like 1250.50.', 4: 'Please check this one.' },
    })
  })

  it('says so when a reason is not one it knows', () => {
    expect(typeInRefusal(['pan_number: not a field of this document']).message).toBe(
      'These details could not be saved. Please check them and try again.',
    )
    expect(typeInRefusal(['name: required', 'odd']).message).toBe(
      'These details could not be saved. Please check them and try again. Some are marked below.',
    )
    expect(typeInRefusal(undefined).problems).toEqual({})
  })
})
