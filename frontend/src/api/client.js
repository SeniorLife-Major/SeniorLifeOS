import { clearSession, readToken } from './session'

// Every backend answer has the same shape (backend/common/schemas/response.py):
//   success: {"status": "SUCCESS", "data": ..., "meta": {"serverTime", "version", "requestId"}}
//   failure: {"status": "ERROR", "error": {"code", "message", "details"}, "requestUri", "meta"}
// The backend writes its error messages for people, so they are shown as they are, except the
// few below that are meant for developers.

export const API_ROOT = '/api/v1'

export const MESSAGES = {
  offline: 'Could not reach SeniorLife. Please check your internet and try again.',
  notAnswering: 'SeniorLife is not answering right now. Please try again in a little while.',
  oddAnswer: 'SeniorLife sent an answer we could not read. Please try again.',
  fieldErrors: 'Some details are not right. Please check them and try again.',
  serverProblem: 'Something went wrong on our side. Please try again in a little while.',
  storageDown: 'Your documents cannot be opened right now. Please try again in a little while.',
}

export class ApiError extends Error {
  constructor(message, code, status, details, requestId) {
    super(message)
    this.name = 'ApiError'
    /** The backend's error code (e.g. INVALID_CREDENTIALS), or OFFLINE / NOT_ANSWERING / ODD_ANSWER. */
    this.code = code
    /** The HTTP status; 0 when the backend could not be reached. */
    this.status = status
    this.details = details
    /** The backend's request ID, for finding the request in the server log. */
    this.requestId = requestId
  }

  /** Seconds to wait after "too many tries" (error.details.retryAfterSeconds; CORS hides Retry-After). */
  get retryAfterSeconds() {
    const seconds = this.details?.retryAfterSeconds
    return typeof seconds === 'number' ? seconds : undefined
  }
}

// Called when the backend answers 401 to anything but a wrong login or password: the sign-in has
// ended (signed out on another phone, the account changed, or the token ran out here).
let onUnauthorized = null

export function setUnauthorizedHandler(handler) {
  onUnauthorized = handler
}

function isErrorBody(value) {
  return value?.status === 'ERROR' && typeof value.error?.message === 'string'
}

function isSuccessBody(value) {
  return value?.status === 'SUCCESS'
}

function messageFor(body) {
  const { code, message, details } = body.error
  if (code === 'VALIDATION_FAILED' && Array.isArray(details)) return MESSAGES.fieldErrors // field-by-field check
  if (code === 'INTERNAL_SERVER_ERROR') return MESSAGES.serverProblem
  if (code === 'STORAGE_UNAVAILABLE') return MESSAGES.storageDown // its message names server settings
  return message
}

/**
 * Calls the backend and gives back `data`, or throws an ApiError with a message to show.
 * options: method ('GET' unless given), body (sent as JSON), file (a File or Blob sent as it is,
 * with its own type as Content-Type; for uploads), signal (to cancel the request).
 */
export async function api(path, options = {}) {
  const token = readToken()
  const headers = { Accept: 'application/json' }
  if (options.file !== undefined) headers['Content-Type'] = options.file.type || 'application/octet-stream'
  else if (options.body !== undefined) headers['Content-Type'] = 'application/json'
  if (token) headers.Authorization = `Bearer ${token}`

  let body
  if (options.file !== undefined) body = options.file
  else if (options.body !== undefined) body = JSON.stringify(options.body)

  let response
  try {
    response = await fetch(`${API_ROOT}${path}`, {
      method: options.method ?? 'GET',
      headers,
      body,
      signal: options.signal,
      cache: 'no-store',
    })
  } catch (error) {
    if (error?.name === 'AbortError') throw error // the screen stopped waiting
    throw new ApiError(MESSAGES.offline, 'OFFLINE', 0)
  }

  let parsed = undefined
  try {
    parsed = JSON.parse(await response.text())
  } catch {
    parsed = undefined
  }

  if (isSuccessBody(parsed) && response.ok) return parsed.data

  if (isErrorBody(parsed)) {
    if (response.status === 401 && parsed.error.code !== 'INVALID_CREDENTIALS') {
      clearSession()
      onUnauthorized?.()
    }
    throw new ApiError(messageFor(parsed), parsed.error.code, response.status, parsed.error.details, parsed.meta?.requestId)
  }

  // Not the backend's answer: usually the dev server or nginx saying the backend is down.
  if (response.status >= 500) throw new ApiError(MESSAGES.notAnswering, 'NOT_ANSWERING', response.status)
  throw new ApiError(MESSAGES.oddAnswer, 'ODD_ANSWER', response.status)
}
