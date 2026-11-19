import { checkRows, checkTyped, emptyRow, FIELD_ORDER, fieldKind, fieldLabel, REQUIRED_FIELDS } from './fields'
import { todayISO } from './status'

// Typing a document in, with no photo (POST /documents/manual and /documents/{id}/versions/manual).
// Every detail the backend accepts for the kind of document is on one form (FIELD_ORDER is
// REQUIRED_FIELDS plus OPTIONAL_MANUAL_FIELDS in backend/graphs/document_graph.py).

/** The form's details, in order: { key, label, kind, required }. */
export function typeInFields(documentType) {
  const required = new Set(REQUIRED_FIELDS[documentType] ?? [])
  return (FIELD_ORDER[documentType] ?? []).map((key) => ({
    key,
    label: fieldLabel(key),
    kind: fieldKind(key),
    required: required.has(key),
  }))
}

/** Where the form starts: every box empty; a required list with one empty row to fill. */
export function emptyTypeIn(documentType) {
  return Object.fromEntries(
    typeInFields(documentType).map((field) => {
      if (field.kind !== 'rows') return [field.key, '']
      return [field.key, field.required ? [emptyRow(field.key)] : []]
    }),
  )
}

/**
 * Checks the whole form. Gives back:
 *   fields   — what to send: only the details that were filled in
 *   problems — { key: message } for single details
 *   rows     — { key: problems } for lists, as checkRows gives them
 *   places   — { key: [the place on screen of each row sent] }: empty rows are not sent, so the
 *              backend's row numbers count only the rows sent (see typeInRefusal)
 *   ok       — nothing to fix
 */
export function checkTypeIn(documentType, values, { now = todayISO() } = {}) {
  const fields = {}
  const problems = {}
  const rows = {}
  const places = {}
  for (const field of typeInFields(documentType)) {
    const value = values[field.key]
    if (field.kind === 'rows') {
      places[field.key] = (value ?? []).flatMap((row, index) =>
        Object.values(row ?? {}).some((text) => String(text ?? '').trim() !== '') ? [index] : [],
      )
      const result = checkRows(field.key, value ?? [], { required: field.required })
      if (result.problems) rows[field.key] = result.problems
      else if (result.value.length > 0) fields[field.key] = result.value
      continue
    }
    const result = checkTyped(field.key, value, { required: field.required, now })
    if (result.problem) problems[field.key] = result.problem
    else if (result.value !== '') fields[field.key] = result.value
  }
  const ok = Object.keys(problems).length === 0 && Object.keys(rows).length === 0
  return { fields, problems, rows, places, ok }
}

// The reasons take_manual_entry in backend/graphs/document_graph.py gives, as sentences.
const ROW_REASONS = {
  'no test name': 'Please type the name of the test.',
  'no medicine name': 'Please type the name of the medicine.',
  'no description': 'Please type what the item is.',
  'amount is not readable': 'Please type the amount in numbers, like 1250.50.',
  'value is not a number': 'Please type the result as a number.',
}

// FUTURE_DATE_LABELS in backend/graphs/document_graph.py
const FUTURE_DATES = {
  'Issue date': 'issue_date',
  'Report date': 'report_date',
  'Prescription date': 'prescription_date',
  'Bill date': 'bill_date',
}

function notValid(key) {
  if (fieldKind(key) === 'aadhaar') return 'This Aadhaar number is not right. Please check each digit.'
  return 'This does not look right. Please check it.'
}

/**
 * When the backend refuses typed-in details (422, error.details.errors), the same shape as
 * checkTypeIn: { problems, rows, message }. A reason this app doesn't know goes in the message.
 * places (from checkTypeIn) turns the backend's row numbers back into places on screen.
 */
export function typeInRefusal(errors, places = {}) {
  const problems = {}
  const rows = {}
  let other = false
  for (const error of Array.isArray(errors) ? errors : []) {
    if (typeof error !== 'string') continue
    let match = /^([a-z_]+) row (\d+): (.+)$/.exec(error)
    if (match) {
      const sent = Number(match[2]) - 1
      const place = places[match[1]]?.[sent] ?? sent
      rows[match[1]] = { ...rows[match[1]], [place]: ROW_REASONS[match[3]] ?? 'Please check this one.' }
      continue
    }
    match = /^([a-z_]+): (required|not valid)$/.exec(error)
    if (match) {
      problems[match[1]] = match[2] === 'required' ? 'Please type this in.' : notValid(match[1])
      continue
    }
    match = /^(.+) cannot be in the future$/.exec(error)
    if (match && FUTURE_DATES[match[1]]) {
      problems[FUTURE_DATES[match[1]]] = 'This date cannot be in the future.'
      continue
    }
    other = true
  }
  const marked = Object.keys(problems).length > 0 || Object.keys(rows).length > 0
  let message = 'Please check the details marked below.'
  if (other) message = 'These details could not be saved. Please check them and try again.'
  if (other && marked) message = `${message} Some are marked below.`
  return { problems, rows, message }
}
