import { describe, expect, it } from 'vitest'
import { proofOf, PROOFS, proofsFromQuery } from './proofs'
import { isKnownType } from './types'

describe('proofs', () => {
  it('are the ones the backend knows (PROOFS in backend/tools/document_rules.py), each from a known kind of document', () => {
    expect(PROOFS.map((proof) => proof.key)).toEqual([
      'proof_of_identity',
      'proof_of_age',
      'proof_of_income',
      'proof_of_residence',
      'proof_of_disability',
      'health_cover',
      'bank_details',
    ])
    expect(PROOFS.every((proof) => isKnownType(proof.type))).toBe(true)
    expect(proofOf('proof_of_age').label).toBe('Proof of age')
  })

  it("are read from the address in the list's order, unknown ones left out", () => {
    expect(proofsFromQuery('health_cover,nonsense,proof_of_age')).toEqual(['proof_of_age', 'health_cover'])
    expect(proofsFromQuery(null)).toEqual([])
  })
})
