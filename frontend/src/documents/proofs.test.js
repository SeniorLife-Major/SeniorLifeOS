import { describe, expect, it } from 'vitest'
import { proofOf, PROOFS, proofsFromQuery } from './proofs'
import { canAdd } from './types'

describe('proofs', () => {
  it('are the backend\'s (PROOFS in backend/tools/document_rules.py) whose paper can be added', () => {
    // Proof of address is left out: residence proofs are not offered (Decision 74).
    expect(PROOFS.map((proof) => proof.key)).toEqual([
      'proof_of_identity',
      'proof_of_age',
      'proof_of_income',
      'health_cover',
      'bank_details',
      'proof_of_bpl',
      'proof_of_widowhood',
      'proof_of_disability',
    ])
    expect(PROOFS.every((proof) => canAdd(proof.type))).toBe(true)
    expect(proofOf('proof_of_age').label).toBe('Proof of age')
  })

  it("are read from the address in the list's order, unknown ones left out", () => {
    expect(proofsFromQuery('health_cover,nonsense,proof_of_age')).toEqual(['proof_of_age', 'health_cover'])
    expect(proofsFromQuery('proof_of_residence,proof_of_disability,bank_details')).toEqual([
      'bank_details',
      'proof_of_disability',
    ])
    expect(proofsFromQuery(null)).toEqual([])
  })
})
