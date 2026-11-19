// What a document's status means for the person, in a few words.
//   kind   — 'reading', 'check', 'ready', 'expired' or 'problem' (the colour of its label)
//   chip   — the short label, e.g. "Please check"
//   detail — one line under it
// The backend's own warnings and errors are not shown: many are written for developers and some
// hold error details (backend/graphs/document_graph.py).

const WORKING = new Set(['processing', 'reviewed'])
const REFUSED = new Set(['needs_reupload', 'needs_classification', 'failed_tool'])

/**
 * What is happening to a newer copy of a saved document (see api/documents.js): 'reading',
 * 'saving', 'check' (waiting for the person), 'refused' (it could not be read; the old copy is
 * still in use), or null when there is none (or the person said not to keep it).
 */
export function newCopyState(document) {
  const status = document?.replacement?.status
  if (status === 'processing') return 'reading'
  if (status === 'reviewed') return 'saving'
  if (status === 'needs_review') return 'check'
  if (REFUSED.has(status)) return 'refused'
  return null
}

/**
 * Can a newer copy be added now? Only to a saved document, and not while another new copy is
 * being read or waits for a check (_replaceable in backend/storage/service.py).
 */
export function canAddNewCopy(document) {
  if (document?.status !== 'confirmed') return false
  const copy = newCopyState(document)
  return copy === null || copy === 'refused'
}

/** Still being worked on (the document, or its new copy), so the screen should look again in a moment. */
export function isWorking(document) {
  if (WORKING.has(document?.status)) return true
  const copy = newCopyState(document)
  return copy === 'reading' || copy === 'saving'
}

/** Today as "YYYY-MM-DD", on this phone's calendar. */
export function todayISO(now = new Date()) {
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${now.getFullYear()}-${month}-${day}`
}

const DATE_FORMAT = new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })

/** "2029-08-28" or "2029-08-28T10:00:00Z" → "28 Aug 2029"; null when there is no date. */
export function formatDate(value) {
  if (typeof value !== 'string') return null
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value)
  if (!match) return null
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
  return Number.isNaN(date.getTime()) ? null : DATE_FORMAT.format(date)
}

export function isExpired(document, today = todayISO()) {
  return document.status === 'confirmed' && typeof document.expiryDate === 'string' && document.expiryDate < today
}

/** "Valid till 28 Aug 2029", else "Issued …", else "Added …"; null when there is no date. */
export function dateLine(document) {
  if (formatDate(document.expiryDate)) return `Valid till ${formatDate(document.expiryDate)}`
  if (formatDate(document.issueDate)) return `Issued ${formatDate(document.issueDate)}`
  if (formatDate(document.createdAt)) return `Added ${formatDate(document.createdAt)}`
  return null
}

export function isPdf(document) {
  return document.mimeType === 'application/pdf'
}

export function statusOf(document, today = todayISO()) {
  switch (newCopyState(document)) {
    case 'reading':
      return { kind: 'reading', chip: 'Reading', detail: 'The new copy is being read. The old one is still in use.' }
    case 'saving':
      return { kind: 'reading', chip: 'Saving', detail: 'The new copy is being saved.' }
    case 'check':
      return { kind: 'check', chip: 'Please check', detail: 'Waiting for you to check the new copy.' }
    default:
      break
  }
  switch (document.status) {
    case 'processing':
      return { kind: 'reading', chip: 'Reading', detail: 'Being read. You can leave this screen.' }
    case 'reviewed':
      return { kind: 'reading', chip: 'Saving', detail: 'Being saved.' }
    case 'needs_review':
      return { kind: 'check', chip: 'Please check', detail: 'Waiting for you to check the details.' }
    case 'confirmed':
      if (isExpired(document, today)) {
        return { kind: 'expired', chip: 'Expired', detail: `Ended ${formatDate(document.expiryDate)}` }
      }
      return { kind: 'ready', chip: 'Ready', detail: dateLine(document) ?? 'Saved and ready to use' }
    case 'needs_reupload':
      // also a PDF with no pages or more than 20 (backend/tools/file_safety.py)
      if (isPdf(document)) return { kind: 'problem', chip: 'Could not read', detail: 'Please add a clearer copy.' }
      return { kind: 'problem', chip: 'Photo not clear', detail: 'Please add a clearer photo.' }
    case 'needs_classification':
      return { kind: 'problem', chip: 'Type not known', detail: 'Please add it again and choose what it is.' }
    case 'rejected':
      return { kind: 'problem', chip: 'Not kept', detail: 'This one was not kept.' }
    case 'failed_tool':
      return { kind: 'problem', chip: 'Could not read', detail: 'Something went wrong while reading it.' }
    default:
      return { kind: 'problem', chip: 'Not ready', detail: 'This document is not ready to use.' }
  }
}

// Documents that need the person come first: to check, being read, problems, expired, ready.
const ORDER = { check: 0, reading: 1, problem: 2, expired: 3, ready: 4 }

/** The list in that order; within each group, the backend's order (newest first) is kept. */
export function inAttentionOrder(documents, today = todayISO()) {
  return documents
    .map((document, index) => ({ document, index, rank: ORDER[statusOf(document, today).kind] }))
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .map((entry) => entry.document)
}
