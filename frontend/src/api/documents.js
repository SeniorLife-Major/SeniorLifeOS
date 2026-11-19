import { api } from './client'

// backend/api/documents.py — a document (DocumentDto):
//   documentId, entry ("upload" or "manual"), filename, mimeType, sizeBytes, documentType,
//   status, version, fileStored, issueDate and expiryDate ("YYYY-MM-DD" or null),
//   createdAt, updatedAt, reviewFlags, warnings, errors, fields, replacement
// The list leaves out reviewFlags, warnings, errors and fields.
//
// status (backend/graphs/document_graph.py, backend/storage/service.py):
//   processing            being read
//   reviewed              checked, being saved
//   needs_review          waiting for the owner to check the details
//   confirmed             saved and ready to use
//   needs_reupload        the photo or file could not be read well enough
//   needs_classification  the reader could not tell what kind of document it is
//   rejected              not kept (the file was refused, or the owner said the details were wrong)
//   failed_tool           something broke while reading
//
// A newer copy of a saved document (a renewal) is a new version. While it is being read or waits
// for the owner's check, the document stays "confirmed" with the old copy in use, and the new one
// is under replacement: { version, entry, status } (plus reviewFlags, warnings and errors when one
// document is asked for). Once the new copy is confirmed it becomes the one in use.

/** The backend's limit (MAX_UPLOAD_BYTES in backend/graphs/document_graph.py): 5,000,000 bytes. */
export const MAX_UPLOAD_BYTES = 5_000_000
export const UPLOAD_TYPES = ['image/jpeg', 'image/png', 'application/pdf']
const FILENAME_MAX = 255

export function listDocuments(signal) {
  return api('/documents', { signal })
}

export function getDocument(documentId, signal) {
  return api(`/documents/${encodeURIComponent(documentId)}`, { signal })
}

/**
 * What is waiting for the owner's check (ReviewDto): documentId, documentType, candidates (each
 * detail as read, full ID numbers included), missingFields (required, not read), optionalFields
 * (may be added), flags (the reader's notes) and errors. 409 when nothing is waiting.
 */
export function getReview(documentId, signal) {
  return api(`/documents/${encodeURIComponent(documentId)}/review`, { signal })
}

/**
 * action: 'confirm' (with edits: { field: new value }) or 'reject'. Gives back the document: its
 * status is "confirmed" or "rejected", or still "needs_review" with errors when the backend
 * refused the details.
 */
export function sendReview(documentId, action, edits = {}) {
  return api(`/documents/${encodeURIComponent(documentId)}/review`, { method: 'POST', body: { action, edits } })
}

/**
 * The document's file (photo or PDF) as a Blob; it needs the sign-in token, so it can't be a plain
 * link. The copy in use, or `version` (a new copy waiting for the owner's check).
 */
export function getDocumentFile(documentId, signal, version) {
  const query = version ? `?version=${encodeURIComponent(version)}` : ''
  return api(`/documents/${encodeURIComponent(documentId)}/file${query}`, { signal, as: 'blob' })
}

const NAMELESS = { 'application/pdf': 'document.pdf', 'image/png': 'photo.png' }

/** The file's name for the backend (1 to 255 characters); the end is kept, as it holds the extension. */
function uploadName(file) {
  const name = (file.name || '').trim() || NAMELESS[file.type] || 'photo.jpg'
  return name.length <= FILENAME_MAX ? name : name.slice(name.length - FILENAME_MAX)
}

/**
 * Sends one file (raw body, its own Content-Type) to be read in the background. Gives back the
 * document straight away, still "processing"; follow it with getDocument.
 *   documentType — what the person said it is, or null for "I don't know"
 *   consents     — { cloudOcr, medicalCloud, useLlm } for this document
 */
export function uploadDocument(file, { documentType, consents }) {
  const query = uploadQuery(file, consents)
  if (documentType) query.set('claimedType', documentType)
  return api(`/documents?${query}`, { method: 'POST', file })
}

function uploadQuery(file, consents) {
  return new URLSearchParams({
    filename: uploadName(file),
    cloudOcrConsent: String(Boolean(consents.cloudOcr)),
    medicalCloudConsent: String(Boolean(consents.medicalCloud)),
    useLlm: String(Boolean(consents.useLlm)),
    background: 'true',
  })
}

/**
 * A newer copy of a saved document (a renewal), read in the background. Gives back the document,
 * still "confirmed" with the old copy in use and the new one under replacement, "processing".
 * 409 when the document is not saved yet, or a newer copy is already being read or checked.
 */
export function replaceDocument(documentId, file, { consents }) {
  const query = uploadQuery(file, consents)
  return api(`/documents/${encodeURIComponent(documentId)}/versions?${query}`, { method: 'POST', file })
}

/**
 * A document whose details the person typed in, with no photo: { field: text, or a list of rows }.
 * Typing in counts as checking, so it comes back "confirmed". When a detail is not right the
 * backend saves nothing and answers 422 with error.details.errors (see documents/typeIn.js).
 */
export function createManualDocument(documentType, fields) {
  return api('/documents/manual', { method: 'POST', body: { documentType, fields } })
}

/** A newer copy of a saved document, typed in; it is in use at once. Same answers as above, and 409 as for replaceDocument. */
export function replaceManually(documentId, fields) {
  return api(`/documents/${encodeURIComponent(documentId)}/versions/manual`, { method: 'POST', body: { fields } })
}

/** Removes the document and everything kept for it, for good. 409 while it is being read. */
export function deleteDocument(documentId) {
  return api(`/documents/${encodeURIComponent(documentId)}`, { method: 'DELETE' })
}

/**
 * Which of the person's documents give these proofs (PROOFS in backend/tools/document_rules.py).
 * Gives back { asOf, matches: [], proofs: [{ proof, documentType, documentId, status }] }, status
 * being usable, unconfirmed (being read or waiting for a check), expired or missing.
 */
export function matchProofs(proofs) {
  return api('/documents/match', { method: 'POST', body: { proofs } })
}
