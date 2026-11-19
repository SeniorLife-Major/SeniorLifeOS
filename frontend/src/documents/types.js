// The kinds of document the backend knows (REQUIRED_FIELDS in backend/tools/document_rules.py).
//   label  — the document's name in lists and headings
//   choice — the button on "Add a document", when it needs more words than the label
export const DOCUMENT_TYPES = [
  { key: 'identity', label: 'ID card', choice: 'ID card (Aadhaar, PAN)' },
  { key: 'income_certificate', label: 'Income certificate' },
  { key: 'residence_proof', label: 'Residence proof' },
  { key: 'health_insurance', label: 'Health insurance' },
  { key: 'bank_passbook', label: 'Bank passbook' },
  { key: 'disability_certificate', label: 'Disability certificate' },
  { key: 'prescription', label: 'Prescription' },
  { key: 'lab_report', label: 'Lab report' },
  { key: 'medical_bill', label: 'Medical bill' },
]

/** Health papers: they go to a cloud service or AI only with their own yes (MEDICAL_TYPES in backend/tools/medical.py). */
export const MEDICAL_TYPES = new Set(['prescription', 'lab_report', 'medical_bill'])

export function isKnownType(key) {
  return DOCUMENT_TYPES.some((type) => type.key === key)
}

// Kinds the app no longer offers to add (the owner's choice, Decision 74). They stay in the list
// above, so ones already saved, or found by the reader after "I don't know", still show their name.
const NOT_OFFERED = new Set(['residence_proof', 'disability_certificate'])

/** The kinds offered on "Add a document" and "Type the details in". */
export const ADDABLE_TYPES = DOCUMENT_TYPES.filter((type) => !NOT_OFFERED.has(type.key))

export function canAdd(key) {
  return ADDABLE_TYPES.some((type) => type.key === key)
}

/** The document's name, e.g. "Income certificate"; "Document" while the kind is not known. */
export function typeLabel(key) {
  return DOCUMENT_TYPES.find((type) => type.key === key)?.label ?? 'Document'
}

/** A document's name inside a sentence: "Income certificate" → "income certificate", but "ID card" stays "ID card". */
export function inSentence(label) {
  return /^[A-Z][a-z]/.test(label) ? label.charAt(0).toLowerCase() + label.slice(1) : label
}
