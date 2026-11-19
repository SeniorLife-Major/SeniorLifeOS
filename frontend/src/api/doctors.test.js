import { describe, expect, it, vi } from 'vitest'
import REAL from '../test/realDoctorStreams.json'
import { fail, fakeBackend, makeSearch, ok, streamed } from '../test/fakeBackend'
import { ApiError, MESSAGES } from './client'
import { answerSearch, getSearch, listSearches, startSearch } from './doctors'
import { saveSession } from './session'

const START = 'POST /api/v1/health-requests'

async function failure(promise) {
  try {
    await promise
  } catch (error) {
    return error
  }
  throw new Error('expected the call to fail')
}

describe('startSearch', () => {
  it('asks for server-sent events, passes on each step in order, and gives back the result', async () => {
    saveSession('token-meera', 3600)
    const result = makeSearch()
    const backend = fakeBackend({ [START]: () => streamed(result, ['safety_check', 'map_specialty']) })
    const steps = []

    const answer = await startSearch('Ghutno mein dard', { onStep: (step) => steps.push(step) })

    expect(answer).toEqual(result)
    expect(steps).toEqual(['safety_check', 'map_specialty'])
    const [request] = backend.sent(START)
    expect(request.body).toEqual({ query: 'Ghutno mein dard' }) // the saved consents are used by the backend
    expect(request.headers.get('Accept')).toBe('text/event-stream')
    expect(request.headers.get('Authorization')).toBe('Bearer token-meera')
  })

  it("throws the backend's message when the work fails part way", async () => {
    saveSession('token-meera', 3600)
    const failed = (code, message) => ({
      status: 201,
      events: [['step', { step: 'safety_check' }], ['error', fail(500, code, message).body]],
    })
    fakeBackend({ [START]: () => failed('WORKFLOW_FAILED', 'The request could not be handled; please try again') })
    const error = await failure(startSearch('knee pain', { onStep: () => {} }))
    expect(error).toBeInstanceOf(ApiError)
    expect(error.message).toBe('The request could not be handled; please try again')

    fakeBackend({ [START]: () => failed('INTERNAL_SERVER_ERROR', 'An unexpected internal server error occurred.') })
    expect((await failure(startSearch('knee pain', { onStep: () => {} }))).message).toBe(MESSAGES.serverProblem)
  })

  it('reads a refusal that comes before the work starts as the usual answer', async () => {
    saveSession('token-meera', 3600)
    fakeBackend({
      [START]: () =>
        fail(429, 'TOO_MANY_REQUESTS', 'You have asked many times in the last hour. In an emergency, call 112 or 108 now.', {
          retryAfterSeconds: 600,
        }),
    })

    const error = await failure(startSearch('knee pain', { onStep: () => {} }))

    expect(error.status).toBe(429)
    expect(error.message).toContain('call 112 or 108 now')
    expect(error.retryAfterSeconds).toBe(600)
  })

  it('says the connection was lost when the events stop before the result', async () => {
    saveSession('token-meera', 3600)
    fakeBackend({ [START]: () => ({ status: 201, events: [['step', { step: 'safety_check' }]] }) })

    const error = await failure(startSearch('knee pain', { onStep: () => {} }))

    expect(error.code).toBe('STREAM_LOST')
    expect(error.message).toBe(MESSAGES.streamLost)
  })
})

describe('the other doctor search calls', () => {
  it('answer the question, open one search, and list the recent ones', async () => {
    saveSession('token-meera', 3600)
    const backend = fakeBackend({
      'POST /api/v1/health-requests/search%201/answer': () => streamed(makeSearch(), ['locate'], 200),
      'GET /api/v1/health-requests/search-1': () => ok(makeSearch()),
      'GET /api/v1/health-requests': () => ok([]),
    })
    const steps = []

    await answerSearch('search 1', { location: { lat: 19.1, lon: 72.8 } }, { onStep: (step) => steps.push(step) })
    expect(await getSearch('search-1')).toEqual(makeSearch())
    expect(await listSearches()).toEqual([])

    expect(backend.sent('POST /api/v1/health-requests/search%201/answer')[0].body).toEqual({
      location: { lat: 19.1, lon: 72.8 },
    })
    expect(steps).toEqual(['locate'])
    expect(backend.sent('GET /api/v1/health-requests')[0].query.get('limit')).toBe('5')
    expect(backend.sent('GET /api/v1/health-requests/search-1')[0].headers.get('Accept')).toBe('application/json')
  })
})

describe('what the real backend sends (recorded in test/realDoctorStreams.json)', () => {
  /** Answers every request with this recording, cut into pieces of 7 bytes. */
  function replay(text) {
    const bytes = new TextEncoder().encode(text)
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        const stream = new ReadableStream({
          start(controller) {
            for (let at = 0; at < bytes.length; at += 7) controller.enqueue(bytes.slice(at, at + 7))
            controller.close()
          },
        })
        return new Response(stream, { status: 201, headers: { 'Content-Type': 'text/event-stream; charset=utf-8' } })
      }),
    )
  }

  it('is read step by step to the same result the screens use', async () => {
    saveSession('token-meera', 3600)
    const seen = {}
    for (const name of ['found', 'asked', 'answered', 'emergency']) {
      replay(REAL[name])
      const steps = []
      const result = await startSearch('words', { onStep: (step) => steps.push(step) })
      seen[name] = { status: result.status, first: steps[0], last: steps.at(-1) }
    }
    expect(seen).toEqual({
      found: { status: 'doctors_found', first: 'validate_request', last: 'explain' },
      asked: { status: 'needs_location', first: 'validate_request', last: 'locate' },
      answered: { status: 'doctors_found', first: 'ask_location', last: 'explain' },
      emergency: { status: 'emergency', first: 'validate_request', last: 'emergency' },
    })
  })

  it('turns its error event into the message to show', async () => {
    saveSession('token-meera', 3600)
    replay(REAL.failed)
    const error = await failure(startSearch('words', { onStep: () => {} }))
    expect(error).toBeInstanceOf(ApiError)
    expect(error.message).toBe('The request could not be handled; please try again')
  })
})
