import { afterEach, describe, expect, it, vi } from 'vitest'
import { fail, fakeBackend, ok } from '../test/fakeBackend'
import { ApiError, api, MESSAGES, setUnauthorizedHandler } from './client'
import { readToken, saveSession } from './session'

async function failure(promise) {
  try {
    await promise
  } catch (error) {
    if (error instanceof ApiError) return error
    throw error
  }
  throw new Error('expected the call to fail')
}

describe('api()', () => {
  afterEach(() => {
    setUnauthorizedHandler(null)
  })

  it('gives back the data inside the envelope', async () => {
    fakeBackend({ 'GET /api/v1/things': () => ok({ name: 'pension card' }) })
    await expect(api('/things')).resolves.toEqual({ name: 'pension card' })
  })

  it('sends JSON, asks for JSON, and adds the saved token', async () => {
    const backend = fakeBackend({ 'POST /api/v1/things': () => ok({}) })
    saveSession('secret-token', 60)

    await api('/things', { method: 'POST', body: { a: 1 } })

    const [sent] = backend.sent('POST /api/v1/things')
    expect(sent.body).toEqual({ a: 1 })
    expect(sent.headers.get('Content-Type')).toBe('application/json')
    expect(sent.headers.get('Accept')).toBe('application/json')
    expect(sent.headers.get('Authorization')).toBe('Bearer secret-token')
  })

  it('sends no Authorization header when nobody is signed in', async () => {
    const backend = fakeBackend({ 'GET /api/v1/things': () => ok([]) })
    await api('/things')
    expect(backend.sent('GET /api/v1/things')[0].headers.has('Authorization')).toBe(false)
  })

  it("shows the backend's own message, with its code, status and request ID", async () => {
    fakeBackend({ 'POST /api/v1/auth/login': () => fail(401, 'INVALID_CREDENTIALS', 'The login or password is wrong') })

    const error = await failure(api('/auth/login', { method: 'POST', body: {} }))
    expect(error.message).toBe('The login or password is wrong')
    expect(error.code).toBe('INVALID_CREDENTIALS')
    expect(error.status).toBe(401)
    expect(error.requestId).toBe('req-test')
  })

  it('reads the wait time after too many tries from the details', async () => {
    fakeBackend({
      'POST /api/v1/auth/login': () =>
        fail(429, 'TOO_MANY_REQUESTS', 'Too many sign-in attempts. Please wait 15 minutes and try again.', {
          retryAfterSeconds: 900,
        }),
    })

    const error = await failure(api('/auth/login', { method: 'POST', body: {} }))
    expect(error.retryAfterSeconds).toBe(900)
    expect(error.message).toBe('Too many sign-in attempts. Please wait 15 minutes and try again.')
  })

  it('has no wait time for other errors', async () => {
    fakeBackend({ 'GET /api/v1/things': () => fail(409, 'CONFLICT', 'Already there') })
    expect((await failure(api('/things'))).retryAfterSeconds).toBeUndefined()
  })

  it("replaces the developer's field-by-field message with a plain one", async () => {
    fakeBackend({
      'POST /api/v1/things': () =>
        fail(422, 'VALIDATION_FAILED', "Request validation failed. Check 'details' for field-level errors.", [
          { field: 'login', message: 'String should have at most 254 characters', type: 'string_too_long' },
        ]),
    })

    const error = await failure(api('/things', { method: 'POST', body: {} }))
    expect(error.message).toBe(MESSAGES.fieldErrors)
    expect(error.details).toEqual([expect.objectContaining({ field: 'login' })])
  })

  it('keeps a 422 message the backend wrote for people', async () => {
    fakeBackend({
      'POST /api/v1/auth/register': () =>
        fail(422, 'VALIDATION_FAILED', 'Enter a 10-digit mobile number or an e-mail address'),
    })
    const error = await failure(api('/auth/register', { method: 'POST', body: {} }))
    expect(error.message).toBe('Enter a 10-digit mobile number or an e-mail address')
  })

  it('says something went wrong on our side for a server error', async () => {
    fakeBackend({ 'GET /api/v1/things': () => fail(500, 'INTERNAL_SERVER_ERROR', 'An unexpected internal server error occurred.') })
    expect((await failure(api('/things'))).message).toBe(MESSAGES.serverProblem)
  })

  it('says the internet is the problem when the backend cannot be reached', async () => {
    fakeBackend({
      'GET /api/v1/things': () => {
        throw new TypeError('Failed to fetch')
      },
    })
    const error = await failure(api('/things'))
    expect(error.code).toBe('OFFLINE')
    expect(error.status).toBe(0)
    expect(error.message).toBe(MESSAGES.offline)
  })

  it('says SeniorLife is not answering when a proxy answers instead (backend down)', async () => {
    fakeBackend({ 'GET /api/v1/things': () => ({ status: 502, text: '<html>Bad Gateway</html>' }) })
    const error = await failure(api('/things'))
    expect(error.code).toBe('NOT_ANSWERING')
    expect(error.message).toBe(MESSAGES.notAnswering)
  })

  it('does not trust an answer that is not the envelope', async () => {
    fakeBackend({ 'GET /api/v1/things': () => ({ status: 200, text: '<html>hello</html>' }) })
    expect((await failure(api('/things'))).code).toBe('ODD_ANSWER')
  })

  it('lets a cancelled request stay cancelled', async () => {
    fakeBackend({
      'GET /api/v1/things': () => {
        throw new DOMException('The operation was aborted.', 'AbortError')
      },
    })
    await expect(api('/things')).rejects.toMatchObject({ name: 'AbortError' })
  })

  it('forgets the token and tells the app when the sign-in has ended', async () => {
    fakeBackend({
      'GET /api/v1/things': () =>
        fail(401, 'UNAUTHORIZED', 'Your sign-in has ended or is not valid; please sign in again'),
    })
    const ended = vi.fn()
    setUnauthorizedHandler(ended)
    saveSession('old-token', 60)

    await failure(api('/things'))
    expect(readToken()).toBeNull()
    expect(ended).toHaveBeenCalledTimes(1)
  })

  it('does not treat a wrong password as an ended sign-in', async () => {
    fakeBackend({ 'POST /api/v1/auth/login': () => fail(401, 'INVALID_CREDENTIALS', 'The login or password is wrong') })
    const ended = vi.fn()
    setUnauthorizedHandler(ended)

    await failure(api('/auth/login', { method: 'POST', body: {} }))
    expect(ended).not.toHaveBeenCalled()
  })
})
