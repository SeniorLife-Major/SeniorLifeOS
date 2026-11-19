import { checkRows, checkTyped, fieldKind, fieldLabel, FIELD_ORDER, isBlank, REQUIRED_FIELDS, toInput, toInputRows } from './fields'
import { notesFor } from './reviewNotes'

/**
 * The details to go through, one at a time, from the backend's review (api/documents.js getReview):
 *   key, label, kind, original (the value as read), notes (plain sentences), required, and state:
 *     'read'     — found on the paper: the person says it is right, or changes it
 *     'missing'  — required but not found: the person types it in
 *     'optional' — not found and not needed: the person may add it, or skip it
 * In the document's own order (FIELD_ORDER), then any detail this app doesn't know.
 */
export function buildSteps(review) {
  const candidates = review.candidates ?? {}
  const missing = new Set(review.missingFields ?? [])
  const keys = new Set([...Object.keys(candidates), ...missing, ...(review.optionalFields ?? [])])
  const order = FIELD_ORDER[review.documentType] ?? []
  const sorted = [...order.filter((key) => keys.has(key)), ...[...keys].filter((key) => !order.includes(key)).sort()]
  const required = new Set([...(REQUIRED_FIELDS[review.documentType] ?? []), ...missing])
  const notes = notesFor(review.flags, sorted)

  return sorted.map((key) => {
    const original = candidates[key] ?? null
    let state = 'read'
    if (missing.has(key)) state = 'missing'
    else if (isBlank(original)) state = 'optional'
    return {
      key,
      label: fieldLabel(key),
      kind: fieldKind(key),
      original,
      notes: notes[key] ?? [],
      required: required.has(key),
      state,
    }
  })
}

/** The value as read, in the same form a typed answer is sent in (to tell a real change from the same value). */
export function originalAsSent(step) {
  if (step.kind === 'rows') return checkRows(step.key, toInputRows(step.key, step.original), { required: false }).value ?? []
  return checkTyped(step.key, toInput(step.key, step.original), { required: false }).value
}

/**
 * An answer for each step (key → { status, value }):
 *   accepted — right as read; changed / added — the person typed `value`; skipped — not added.
 * Only changed and added details go to the backend.
 */
export function editsFrom(steps, answers) {
  const edits = {}
  for (const step of steps) {
    const answer = answers[step.key]
    if (answer && (answer.status === 'changed' || answer.status === 'added')) edits[step.key] = answer.value
  }
  return edits
}

/** The answer for what the person typed, once it has passed its check. */
export function answerFor(step, value) {
  if (step.state === 'read') {
    return JSON.stringify(value) === JSON.stringify(originalAsSent(step))
      ? { status: 'accepted' }
      : { status: 'changed', value }
  }
  return isBlank(value) ? { status: 'skipped' } : { status: 'added', value }
}
