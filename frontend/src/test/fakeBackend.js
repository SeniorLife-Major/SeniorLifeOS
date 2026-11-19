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
