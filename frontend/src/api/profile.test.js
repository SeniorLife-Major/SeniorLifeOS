import { describe, expect, it } from 'vitest'
import { ApiError } from './client'
import { placeOptions } from './profile'

const RAMPUR_UP = { label: 'Rampur, Uttar Pradesh', lat: 28.81, lon: 79.026 }

describe('placeOptions', () => {
  it('finds the places to choose from in the backend’s 422', () => {
    const error = new ApiError('This name fits more than one place', 'VALIDATION_FAILED', 422, {
      options: [RAMPUR_UP, { label: 'no position' }],
    })
    expect(placeOptions(error)).toEqual([RAMPUR_UP])
  })

  it('finds nothing in any other error', () => {
    expect(placeOptions(new ApiError('This place was not found', 'VALIDATION_FAILED', 422, null))).toBeNull()
    expect(placeOptions(new ApiError('x', 'VALIDATION_FAILED', 422, { options: [] }))).toBeNull()
    expect(placeOptions(new ApiError('x', 'VALIDATION_FAILED', 422, [{ field: 'place' }]))).toBeNull()
    expect(placeOptions(new TypeError('Failed to fetch'))).toBeNull()
  })
})
