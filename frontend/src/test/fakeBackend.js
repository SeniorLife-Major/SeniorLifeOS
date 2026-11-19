import { vi } from 'vitest'

// A pretend backend for tests: `fetch` is replaced by a function that answers from a table of
// routes ("POST /api/v1/auth/login") with the backend's envelope, and remembers every request.
// A handler can throw (e.g. a TypeError) to act as if the network failed.
//
// A handler gets the request: { method, path, query (URLSearchParams), headers (Headers), body }
// (body is the JSON body, parsed; anything else as sent), and gives back a reply:
//   { status, body }  body is sent as JSON
//   { status, text }  text is sent as it is (e.g. an HTML error page from a proxy)

const META = { serverTime: '2026-11-19T10:00:00Z', version: 'test', requestId: 'req-test' }

export function ok(data, status = 200) {
  return { status, body: { status: 'SUCCESS', data, meta: META } }
}

export function fail(status, code, message, details) {
  return {
    status,
    body: { status: 'ERROR', error: { code, message, details: details ?? null }, requestUri: '/api', meta: META },
  }
}

export function fakeBackend(routes = {}) {
  const table = new Map(Object.entries(routes))
  const requests = []

  const fetchMock = vi.fn(async (input, init = {}) => {
    const url = new URL(String(input), 'http://localhost')
    const method = init.method ?? 'GET'
    let body = init.body
    if (typeof init.body === 'string') {
      try {
        body = JSON.parse(init.body)
      } catch {
        body = init.body
      }
    }
    const request = { method, path: url.pathname, query: url.searchParams, headers: new Headers(init.headers), body }
    requests.push(request)

    const handler = table.get(`${method} ${url.pathname}`)
    const reply = handler ? await handler(request) : fail(404, 'NOT_FOUND', `The fake backend has no ${method} ${url.pathname}`)
    const text = reply.text ?? JSON.stringify(reply.body ?? null)
    return new Response(text, {
      status: reply.status ?? 200,
      headers: { 'Content-Type': reply.text === undefined ? 'application/json' : 'text/html' },
    })
  })
  vi.stubGlobal('fetch', fetchMock)

  return {
    fetch: fetchMock,
    requests,
    /** Adds or replaces one route. */
    on(route, handler) {
      table.set(route, handler)
    },
    /** The requests sent to one route, oldest first. */
    sent(route) {
      return requests.filter((request) => `${request.method} ${request.path}` === route)
    },
  }
}

export const MEERA = { userId: 'u-1', login: '+919876543210', displayName: 'Meera', role: 'senior' }

export function session(user = MEERA, token = 'token-meera') {
  return { user, accessToken: token, tokenType: 'bearer', expiresIn: 43200 }
}

/** A promise the test settles when it wants: to hold a fake answer back and see the screen meanwhile. */
export function deferred() {
  let resolve
  let reject
  const promise = new Promise((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}

export function makeProfile(changes = {}) {
  return {
    language: 'en',
    homeLocation: null,
    accessibility: { fontScale: 1, highContrast: false, voice: false },
    consents: { useLlm: false, medicalCloud: false, cloudOcr: false },
    consentsUpdatedAt: null,
    ...changes,
  }
}

const round3 = (value) => Math.round(value * 1000) / 1000

/**
 * Serves GET and PATCH /api/v1/me/profile like backend/api/profile.py: only what is sent changes.
 * A place name is looked up in `places`: one place ({ label, lat, lon }), or a list for a name that
 * fits several (answered with the backend's 422 and its options). Gives back the store:
 *   current — the profile as the fake backend holds it now
 *   hold()  — the next PATCH waits until the test calls resolve() on what this gives back
 */
export function serveProfile(backend, profile, places = {}) {
  const holds = []
  const store = {
    current: profile,
    hold() {
      const held = deferred()
      holds.push(held)
      return held
    },
  }
  backend.on('GET /api/v1/me/profile', () => ok(store.current))
  backend.on('PATCH /api/v1/me/profile', async ({ body }) => {
    const held = holds.shift()
    if (held) await held.promise
    const next = { ...store.current }
    if (body.accessibility) next.accessibility = { ...store.current.accessibility, ...body.accessibility }
    if (body.consents) next.consents = { ...store.current.consents, ...body.consents }
    if ('homeLocation' in body) {
      const home = body.homeLocation
      if (home === null) {
        next.homeLocation = null
      } else if (typeof home.lat === 'number') {
        next.homeLocation = { label: home.place ?? null, lat: round3(home.lat), lon: round3(home.lon) }
      } else {
        const found = places[home.place]
        if (!found) return fail(422, 'VALIDATION_FAILED', 'This place was not found; add the city, or share your location')
        if (Array.isArray(found)) {
          return fail(422, 'VALIDATION_FAILED', 'This name fits more than one place; choose one and send its lat and lon', {
            options: found,
          })
        }
        next.homeLocation = found
      }
    }
    store.current = next
    return ok(store.current)
  })
  return store
}

/** A document as backend/api/documents.py sends it (DocumentDto): a saved income certificate unless changed. */
export function makeDocument(changes = {}) {
  return {
    documentId: 'doc-1',
    entry: 'upload',
    filename: 'photo.jpg',
    mimeType: 'image/jpeg',
    sizeBytes: 120000,
    documentType: 'income_certificate',
    status: 'confirmed',
    version: 1,
    fileStored: true,
    issueDate: null,
    expiryDate: null,
    createdAt: '2026-11-01T09:00:00Z',
    updatedAt: '2026-11-01T09:00:00Z',
    reviewFlags: [],
    warnings: [],
    errors: [],
    fields: {},
    replacement: null,
    ...changes,
  }
}

/**
 * Serves the document routes like backend/api/documents.py: the list (newest first, without
 * fields, flags, warnings or errors), one document (404 when it is not there), and uploads
 * (background=true: 202 with a new document, still "processing"). Gives back the store:
 *   current              — the documents as the fake backend holds them now, newest first
 *   change(id, changes)  — e.g. the reader has finished: change('doc-1', { status: 'needs_review' })
 */
export function serveDocuments(backend, documents = []) {
  let added = 0
  const store = {
    current: [...documents],
    change(documentId, changes) {
      store.current = store.current.map((document) =>
        document.documentId === documentId ? { ...document, ...changes } : document,
      )
    },
  }
  const listed = (document) => ({ ...document, reviewFlags: [], warnings: [], errors: [], fields: {} })
  const serveOne = (documentId) =>
    backend.on(`GET /api/v1/documents/${documentId}`, () => {
      const found = store.current.find((document) => document.documentId === documentId)
      return found ? ok(found) : fail(404, 'NOT_FOUND', 'Document not found')
    })

  store.current.forEach((document) => serveOne(document.documentId))
  backend.on('GET /api/v1/documents', () => ok(store.current.map(listed)))
  backend.on('POST /api/v1/documents', ({ query, headers, body }) => {
    added += 1
    const document = makeDocument({
      documentId: `doc-new-${added}`,
      filename: query.get('filename'),
      mimeType: headers.get('content-type'),
      sizeBytes: body.size,
      documentType: query.get('claimedType'),
      status: 'processing',
      createdAt: META.serverTime,
      updatedAt: META.serverTime,
    })
    store.current = [document, ...store.current]
    serveOne(document.documentId)
    return ok(document, 202)
  })
  return store
}
