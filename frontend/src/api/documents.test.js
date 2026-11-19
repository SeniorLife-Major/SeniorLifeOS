import { describe, expect, it } from 'vitest'
import { fail, fakeBackend, ok } from '../test/fakeBackend'
import { ApiError, MESSAGES } from './client'
import {
  createManualDocument,
  deleteDocument,
  getDocument,
  getDocumentFile,
  getReview,
  listDocuments,
  matchProofs,
  replaceDocument,
  replaceManually,
  sendReview,
  uploadDocument,
} from './documents'
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

describe('the review calls', () => {
  it('ask for what is waiting, and send the decision with only the changed details', async () => {
    const backend = fakeBackend({
      'GET /api/v1/documents/d-1/review': () => ok({ documentId: 'd-1', candidates: { name: 'Meera' } }),
      'POST /api/v1/documents/d-1/review': () => ok({ documentId: 'd-1', status: 'confirmed' }),
    })

    expect(await getReview('d-1')).toEqual({ documentId: 'd-1', candidates: { name: 'Meera' } })
    expect(await sendReview('d-1', 'confirm', { annual_income: '266000' })).toEqual({ documentId: 'd-1', status: 'confirmed' })
    await sendReview('d-1', 'reject')

    const sent = backend.sent('POST /api/v1/documents/d-1/review')
    expect(sent.map((request) => request.body)).toEqual([
      { action: 'confirm', edits: { annual_income: '266000' } },
      { action: 'reject', edits: {} },
    ])
    expect(sent[0].headers.get('Content-Type')).toBe('application/json')
  })

  it('fetch the file with the sign-in token, as a file and not as JSON', async () => {
    saveSession('token-meera', 3600)
    const backend = fakeBackend({
      'GET /api/v1/documents/d-1/file': () => ({ status: 200, bytes: new Uint8Array([1, 2, 3]), type: 'image/png' }),
    })

    const blob = await getDocumentFile('d-1')
    expect(blob.type).toBe('image/png')
    expect(new Uint8Array(await blob.arrayBuffer())).toEqual(new Uint8Array([1, 2, 3]))
    expect(backend.sent('GET /api/v1/documents/d-1/file')[0].headers.get('Authorization')).toBe('Bearer token-meera')
  })

  it("still give the backend's message when the file is not there", async () => {
    fakeBackend({
      'GET /api/v1/documents/d-1/file': () => fail(404, 'NOT_FOUND', "This version's file is no longer kept"),
    })

    const error = await failure(getDocumentFile('d-1'))
    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(404)
    expect(error.message).toBe("This version's file is no longer kept")
  })
})

describe("the rest of a document's life", () => {
  it("sends a new copy as a file to the document's versions, with the same choices and no type", async () => {
    saveSession('token-meera', 3600)
    const route = 'POST /api/v1/documents/doc%201/versions' // the id is encoded in the address
    const backend = fakeBackend({ [route]: () => ok({ documentId: 'doc 1', status: 'confirmed' }, 202) })
    const file = new File([new Uint8Array(10)], 'renewed.jpg', { type: 'image/jpeg' })

    await replaceDocument('doc 1', file, { consents: CONSENTS })

    const [request] = backend.sent(route)
    expect(request.body).toBe(file)
    expect(request.headers.get('Content-Type')).toBe('image/jpeg')
    expect(Object.fromEntries(request.query)).toEqual({
      filename: 'renewed.jpg',
      cloudOcrConsent: 'true',
      medicalCloudConsent: 'false',
      useLlm: 'true',
      background: 'true',
    })
  })

  it('sends typed-in details as JSON, for a new document and for a new copy', async () => {
    saveSession('token-meera', 3600)
    const backend = fakeBackend({
      'POST /api/v1/documents/manual': () => ok({ documentId: 'd-2', status: 'confirmed' }, 201),
      'POST /api/v1/documents/d-1/versions/manual': () => ok({ documentId: 'd-1', status: 'confirmed' }, 201),
    })

    await createManualDocument('income_certificate', { name: 'Meera Rao', annual_income: '256000' })
    await replaceManually('d-1', { name: 'Meera Rao' })

    expect(backend.sent('POST /api/v1/documents/manual')[0].body).toEqual({
      documentType: 'income_certificate',
      fields: { name: 'Meera Rao', annual_income: '256000' },
    })
    expect(backend.sent('POST /api/v1/documents/d-1/versions/manual')[0].body).toEqual({ fields: { name: 'Meera Rao' } })
  })

  it("keeps the backend's reasons when typed-in details are refused", async () => {
    saveSession('token-meera', 3600)
    fakeBackend({
      'POST /api/v1/documents/manual': () =>
        fail(422, 'VALIDATION_FAILED', 'Some fields need correcting', { errors: ['aadhaar_number: not valid'] }),
    })

    const error = await failure(createManualDocument('identity', { name: 'Meera Rao' }))

    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(422)
    expect(error.details).toEqual({ errors: ['aadhaar_number: not valid'] })
  })

  it('deletes with DELETE, and asks which documents give the proofs', async () => {
    saveSession('token-meera', 3600)
    const answer = { asOf: '2026-11-19', matches: [], proofs: [] }
    const backend = fakeBackend({
      'DELETE /api/v1/documents/d-1': () => ok({ documentId: 'd-1', deleted: true }),
      'POST /api/v1/documents/match': () => ok(answer),
    })

    expect(await deleteDocument('d-1')).toEqual({ documentId: 'd-1', deleted: true })
    expect(await matchProofs(['proof_of_age', 'health_cover'])).toEqual(answer)
    expect(backend.sent('POST /api/v1/documents/match')[0].body).toEqual({ proofs: ['proof_of_age', 'health_cover'] })
  })

  it('asks for the file of a new copy by its version', async () => {
    saveSession('token-meera', 3600)
    const route = 'GET /api/v1/documents/d-1/file'
    const backend = fakeBackend({ [route]: () => ({ status: 200, bytes: new Uint8Array([1]), type: 'image/png' }) })

    await getDocumentFile('d-1', undefined, 2)
    await getDocumentFile('d-1')

    expect(backend.sent(route).map((request) => request.query.get('version'))).toEqual(['2', null])
  })
})
