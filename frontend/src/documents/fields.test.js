import { describe, expect, it } from 'vitest'
import { checkRows, checkTyped, fieldLabel, showRow, showValue, toInputRows } from './fields'

const NOW = '2026-11-19'
const check = (key, text, required = true) => checkTyped(key, text, { required, now: NOW })

describe('showValue', () => {
  it('shows each kind of detail the way it is written in India', () => {
    expect(showValue('annual_income', 256000)).toBe('₹2,56,000')
    expect(showValue('total_amount', 1250.5)).toBe('₹1,250.5')
    expect(showValue('issue_date', '2025-12-23')).toBe('23 Dec 2025')
    expect(showValue('residence_state', 'MH')).toBe('Maharashtra')
    expect(showValue('disability_percent', 40)).toBe('40%')
    expect(showValue('aadhaar_number', '234567890124')).toBe('2345 6789 0124')
    expect(showValue('name', 'Meera Rao')).toBe('Meera Rao')
    expect(showValue('name', null)).toBe('')
  })

  it('gives every detail a name, even one this app does not know', () => {
    expect(fieldLabel('annual_income')).toBe('Yearly income')
    expect(fieldLabel('ifsc')).toBe('IFSC code')
    expect(fieldLabel('ward_number')).toBe('Ward number')
  })
})

describe('checkTyped', () => {
  it('takes amounts written the usual ways and sends just the number', () => {
    expect(check('annual_income', '₹2,56,000/-')).toEqual({ value: '256000' })
    expect(check('annual_income', 'Rs. 256000.00')).toEqual({ value: '256000' })
    expect(check('annual_income', '2.56 lakh')).toEqual({ problem: 'Please type the amount in numbers, like 256000.' })
    expect(check('total_amount', '1,250.50')).toEqual({ value: '1250.50' })
    expect(check('total_amount', '12.505').problem).toBeDefined()
  })

  it('checks dates are real, and not in the future when they cannot be', () => {
    expect(check('issue_date', '2025-12-23')).toEqual({ value: '2025-12-23' })
    expect(check('issue_date', '2026-02-30')).toEqual({ problem: 'Please choose a real date.' })
    expect(check('issue_date', '2026-11-20')).toEqual({ problem: 'This date cannot be in the future.' })
    expect(check('issue_date', NOW)).toEqual({ value: NOW })
    expect(check('expiry_date', '2031-03-31')).toEqual({ value: '2031-03-31' }) // an end date may be ahead
  })

  it('checks ID numbers and codes, and sends them in one form', () => {
    expect(check('aadhaar_number', '2345 6789-0124')).toEqual({ value: '234567890124' })
    expect(check('aadhaar_number', '2345 6789')).toEqual({ problem: 'An Aadhaar number has 12 digits.' })
    expect(check('pan_number', 'abcde1234f')).toEqual({ value: 'ABCDE1234F' })
    expect(check('pan_number', 'ABCD1234F').problem).toBeDefined()
    expect(check('account_number', '0012 3456 7890')).toEqual({ value: '001234567890' })
    expect(check('account_number', '12345').problem).toBeDefined()
    expect(check('ifsc', 'sbin0001234')).toEqual({ value: 'SBIN0001234' })
    expect(check('ifsc', 'SBIN1001234').problem).toBeDefined() // the fifth character is always 0
    expect(check('policy_number', ' pol/2026/77 ')).toEqual({ value: 'POL/2026/77' })
    expect(check('policy_number', 'ABCDEFG').problem).toBeDefined() // needs a digit
  })

  it('checks a percentage and a state', () => {
    expect(check('disability_percent', '40 %')).toEqual({ value: '40' })
    expect(check('disability_percent', '140')).toEqual({ problem: 'Please type a number from 0 to 100.' })
    expect(check('residence_state', 'KL')).toEqual({ value: 'KL' })
    expect(check('residence_state', 'Kerala')).toEqual({ problem: 'Please choose the state.' })
  })

  it('needs a required detail, and lets one that is not required be left empty', () => {
    expect(check('name', '   ')).toEqual({ problem: 'Please type this in.' })
    expect(check('issue_date', '')).toEqual({ problem: 'Please choose the date.' })
    expect(check('residence_state', '')).toEqual({ problem: 'Please choose the state.' })
    expect(check('expiry_date', '', false)).toEqual({ value: '' })
    expect(check('name', '  Meera   Rao ')).toEqual({ value: 'Meera Rao' })
  })
})

describe('rows', () => {
  it('are shown one line each', () => {
    expect(showRow('tests', { name: 'Haemoglobin', value: 13.2, unit: 'g/dL', ref_range: '12.0 - 15.5' })).toBe(
      'Haemoglobin: 13.2 g/dL (normal 12.0 - 15.5)',
    )
    expect(showRow('medicines', { name: 'Metformin', strength: '500 mg', form: 'tablet', frequency: '1-0-1', duration: '30 days' })).toBe(
      'Metformin 500 mg, tablet, 1-0-1, 30 days',
    )
    expect(showRow('items', { description: 'Consultation', amount: 500 })).toBe('Consultation: ₹500')
    expect(showRow('items', { description: null, amount: null })).toBe('No description: no amount')
  })

  it('become text to edit, without the reader\'s own notes', () => {
    const rows = [{ name: 'TSH', value: 2.1, unit: null, ref_range: '0.4 - 4.0', flag: 'normal', problems: [], source_ref: 'p1' }]
    expect(toInputRows('tests', rows)).toEqual([{ name: 'TSH', value: '2.1', unit: '', ref_range: '0.4 - 4.0' }])
  })

  it('drop rows left empty, and say which row needs fixing by its place on screen', () => {
    const typed = [
      { description: 'Room', amount: '1,500' },
      { description: '', amount: '' },
      { description: '', amount: '200' },
      { description: 'Tests', amount: 'two hundred' },
    ]
    expect(checkRows('items', typed, { required: false })).toEqual({
      problems: { 2: 'Please type what the item is.', 3: 'Please type the amount in numbers, like 1250.50.' },
    })

    typed[2].description = 'Medicines'
    typed[3].amount = '₹200'
    expect(checkRows('items', typed, { required: false })).toEqual({
      value: [
        { description: 'Room', amount: '1500' },
        { description: 'Medicines', amount: '200' },
        { description: 'Tests', amount: '200' },
      ],
    })
  })

  it('need at least one row when the list is required', () => {
    const empty = [{ name: '', strength: '', form: '', frequency: '', duration: '' }]
    expect(checkRows('medicines', empty, { required: true })).toEqual({ problems: { list: 'Please add at least one medicine.' } })
    expect(checkRows('items', empty.map(() => ({ description: '', amount: '' })), { required: false })).toEqual({ value: [] })
  })
})
