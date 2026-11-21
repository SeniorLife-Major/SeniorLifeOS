import { describe, expect, it } from 'vitest'
import { checkTyped, choicesFor, showValue } from '../documents/fields'
import REAL from '../test/realSchemeChecks.json'
import { factValue, gapAction, groupSchemes, SCHEME_LOOK } from './schemes'

describe('scheme words', () => {
  it('know every status the backend gives a scheme', () => {
    const statuses = new Set(Object.values(REAL).flatMap((check) => check.schemes.map((scheme) => scheme.status)))
    for (const status of statuses) expect(SCHEME_LOOK[status], status).toBeDefined()
  })

  it('group the schemes in order and leave out empty groups', () => {
    expect(groupSchemes(REAL.screenAfter.schemes).map((group) => [group.status, group.schemes.length])).toEqual([
      ['likely', 2],
      ['add_documents', 1],
      ['not_eligible', 1],
    ])
  })

  it('say what the papers show', () => {
    expect(factValue('age', 68)).toBe('68 years')
    expect(factValue('gender', 'female')).toBe('Woman')
    expect(factValue('ration_card_category', 'PHH')).toBe('Priority household (PHH)')
    expect(factValue('disability_percent', 85)).toBe('85%')
    expect(factValue('widowed', true)).toBe('Yes, the certificate names you')
    expect(factValue('annual_income', 120000)).toBe('₹1,20,000 a year')
    expect(factValue('age', null)).toBeNull()
  })

  it('lead each thing to do to the right screen', () => {
    expect(gapAction({ reason: 'missing', documentType: 'ration_card' })).toEqual({
      to: '/documents/add?type=ration_card',
      label: 'Add the ration card',
    })
    expect(gapAction({ reason: 'missing', documentType: 'identity' }).label).toBe('Add the ID card')
    expect(gapAction({ reason: 'missing', documentType: 'residence_proof' })).toBeNull() // not offered
    expect(gapAction({ reason: 'expired', documentType: 'identity', documentId: 'a b' }).to).toBe('/documents/a%20b/new-copy')
    expect(gapAction({ reason: 'detail_missing', documentType: 'death_certificate', documentId: 'd' }).to).toBe('/documents/d')
    expect(gapAction({ reason: 'check', documentType: null, documentId: null })).toBeNull()
  })
})

describe('the new details', () => {
  it('are chosen from lists', () => {
    expect(choicesFor('gender').options.map(([code]) => code)).toEqual(['female', 'male', 'other'])
    expect(choicesFor('category').options.map(([code]) => code)).toEqual(['AAY', 'PHH', 'BPL', 'APL', 'NPHH'])
    expect(choicesFor('text')).toBeUndefined()
    expect(checkTyped('card_category', '', { required: true })).toEqual({ problem: 'Please choose the card type.' })
    expect(checkTyped('gender', 'female', { required: false })).toEqual({ value: 'female' })
  })

  it('are shown and checked as the backend expects', () => {
    expect(showValue('card_category', 'AAY')).toBe('Antyodaya (AAY)')
    expect(showValue('gender', 'male')).toBe('Man')
    expect(showValue('age_at_death', 74)).toBe('74 years')
    expect(checkTyped('age_at_death', '74 years', { required: false })).toEqual({ value: '74' })
    expect(checkTyped('age_at_death', '300', { required: false }).problem).toBeDefined()
    expect(checkTyped('registration_number', 'd-2024:27-90012', { required: false })).toEqual({ value: 'D-2024:27-90012' })
    expect(checkTyped('ration_card_number', 'ABCD', { required: false }).problem).toBeDefined()
  })
})
