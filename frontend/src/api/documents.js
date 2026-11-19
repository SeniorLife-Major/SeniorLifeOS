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
  const query = new URLSearchParams({
    filename: uploadName(file),
    cloudOcrConsent: String(Boolean(consents.cloudOcr)),
    medicalCloudConsent: String(Boolean(consents.medicalCloud)),
    useLlm: String(Boolean(consents.useLlm)),
    background: 'true',
  })
  if (documentType) query.set('claimedType', documentType)
  return api(`/documents?${query}`, { method: 'POST', file })
}
