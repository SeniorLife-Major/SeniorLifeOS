// What an office may ask for, and which kind of document gives it (PROOFS in
// backend/tools/document_rules.py). "Do I have what I need?" lets the person tick these.
//   label — what the office calls it
//   hint  — the paper that gives it
//   type  — that kind of document, to add one when it is missing
export const PROOFS = [
  { key: 'proof_of_identity', label: 'Proof of identity', hint: 'An ID card, such as Aadhaar', type: 'identity' },
  { key: 'proof_of_age', label: 'Proof of age', hint: 'An ID card with your date of birth', type: 'identity' },
  { key: 'proof_of_income', label: 'Proof of income', hint: 'An income certificate', type: 'income_certificate' },
  { key: 'proof_of_residence', label: 'Proof of address', hint: 'A residence proof', type: 'residence_proof' },
  {
    key: 'proof_of_disability',
    label: 'Proof of disability',
    hint: 'A disability certificate',
    type: 'disability_certificate',
  },
  { key: 'health_cover', label: 'Health cover', hint: 'A health insurance card', type: 'health_insurance' },
  { key: 'bank_details', label: 'Bank details', hint: 'A bank passbook', type: 'bank_passbook' },
]

export function proofOf(key) {
  return PROOFS.find((proof) => proof.key === key)
}

/** The chosen proofs from the address (?need=proof_of_age,health_cover), in the list's order; unknown ones are left out. */
export function proofsFromQuery(value) {
  const wanted = new Set(String(value ?? '').split(','))
  return PROOFS.filter((proof) => wanted.has(proof.key)).map((proof) => proof.key)
}
