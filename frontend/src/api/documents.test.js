import { describe, expect, it } from 'vitest'
import { fail, fakeBackend, ok } from '../test/fakeBackend'
import { ApiError, MESSAGES } from './client'
import { getDocument, listDocuments, uploadDocument } from './documents'
import { saveSession } from './session'

const UPLOAD = 'POST /api/v1/documents'
const CONSENTS = { cloudOcr: true, medicalCloud: false, useLlm: true }

async function failure(promise) {
  try {
    await promise
  } catch (error) {
    return error
  }
  throw new Error('expected the call to fail')
}

describe('uploadDocument', () => {
  it('sends the file itself, with its own type, and the choices as query parameters', async () => {
    saveSession('token-meera', 3600)
    const backend = fakeBackend({ [UPLOAD]: () => ok({ documentId: 'd-1', status: 'processing' }, 202) })
    const file = new File(['%PDF-1.4 a page'], 'income.pdf', { type: 'application/pdf' })

    const document = await uploadDocument(file, { documentType: 'income_certificate', consents: CONSENTS })

    expect(document).toEqual({ documentId: 'd-1', status: 'processing' })
    const [request] = backend.sent(UPLOAD)
    expect(request.body).toBe(file) // the raw file, not JSON or a form
    expect(request.headers.get('Content-Type')).toBe('application/pdf')
    expect(request.headers.get('Authorization')).toBe('Bearer token-meera')
    expect(Object.fromEntries(request.query)).toEqual({
      filename: 'income.pdf',
      claimedType: 'income_certificate',
      cloudOcrConsent: 'true',
      medicalCloudConsent: 'false',
      useLlm: 'true',
      background: 'true',
    })
  })

  it('leaves the type out for "I don\'t know", and names a file that has no name', async () => {
    const backend = fakeBackend({ [UPLOAD]: () => ok({ documentId: 'd-2' }, 202) })

    await uploadDocument(new File(['png'], '', { type: 'image/png' }), {
      documentType: null,
      consents: { cloudOcr: false, medicalCloud: false, useLlm: false },
    })

    const { query } = backend.sent(UPLOAD)[0]
    expect(query.has('claimedType')).toBe(false)
    expect(query.get('filename')).toBe('photo.png')
    expect(query.get('cloudOcrConsent')).toBe('false')
  })

  it('keeps the end of a very long file name, as the backend takes at most 255 characters', async () => {
    const backend = fakeBackend({ [UPLOAD]: () => ok({ documentId: 'd-3' }, 202) })
    const name = `${'a'.repeat(300)}.jpg`

    await uploadDocument(new File(['jpg'], name, { type: 'image/jpeg' }), { documentType: null, consents: CONSENTS })

    const sentName = backend.sent(UPLOAD)[0].query.get('filename')
    expect(sentName).toHaveLength(255)
    expect(sentName.endsWith('.jpg')).toBe(true)
  })

  it("passes on the backend's words when the reader is busy or too much was sent", async () => {
    const backend = fakeBackend({
      [UPLOAD]: () =>
        fail(503, 'READER_BUSY', 'Many documents are being read right now; please try again in a few minutes'),
    })
    const file = new File(['jpg'], 'card.jpg', { type: 'image/jpeg' })

    const busy = await failure(uploadDocument(file, { documentType: 'identity', consents: CONSENTS }))
    expect(busy).toBeInstanceOf(ApiError)
    expect(busy.message).toBe('Many documents are being read right now; please try again in a few minutes')

    backend.on(UPLOAD, () =>
      fail(429, 'TOO_MANY_REQUESTS', 'Many documents were added in the last hour. Please wait 20 minutes and try again.', {
        retryAfterSeconds: 1200,
      }),
    )
    const tooMany = await failure(uploadDocument(file, { documentType: 'identity', consents: CONSENTS }))
    expect(tooMany.message).toBe('Many documents were added in the last hour. Please wait 20 minutes and try again.')
    expect(tooMany.retryAfterSeconds).toBe(1200)
  })
})

describe('listDocuments and getDocument', () => {
  it('ask for the list, and for one document by its ID', async () => {
    const backend = fakeBackend({
      'GET /api/v1/documents': () => ok([{ documentId: 'd-1' }]),
      'GET /api/v1/documents/d%2F1': () => ok({ documentId: 'd/1' }),
    })

    expect(await listDocuments()).toEqual([{ documentId: 'd-1' }])
    expect(await getDocument('d/1')).toEqual({ documentId: 'd/1' }) // the ID can't break out of its place
    expect(backend.requests.map((request) => request.body)).toEqual([undefined, undefined])
  })

  it('say plainly when the document store cannot be opened, not what the server says about it', async () => {
    fakeBackend({
      'GET /api/v1/documents': () =>
        fail(503, 'STORAGE_UNAVAILABLE', 'Document storage needs SENIORLIFE_FILE_KEY to be set'),
    })

    const error = await failure(listDocuments())
    expect(error.message).toBe(MESSAGES.storageDown)
    expect(error.status).toBe(503)
  })
})
