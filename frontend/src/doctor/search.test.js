import { describe, expect, it } from 'vitest'
import { makeSearch } from '../test/fakeBackend'
import {
  directionsLink,
  distanceWords,
  doneWhenAsked,
  isSelfHarm,
  resultsHeading,
  safeLink,
  searchChip,
  signsOf,
  STAGES,
  stagesDone,
} from './search'

describe('the stages of a search', () => {
  it('are ticked off only when the step that finishes each one is done, never going back', () => {
    const steps = ['validate_request', 'safety_check', 'understand_request', 'map_specialty', 'locate', 'search_doctors']
    const seen = []
    steps.reduce((done, step) => {
      const next = stagesDone(done, step)
      seen.push(next)
      return next
    }, 0)
    expect(seen).toEqual([0, 1, 1, 2, 2, 3])
    expect(stagesDone(3, 'safety_check')).toBe(3) // after an answer the search runs some steps again
    expect(stagesDone(4, 'explain')).toBe(STAGES.length)
  })

  it('start where the question left them', () => {
    expect(doneWhenAsked('needs_location')).toBe(2)
    expect(doneWhenAsked('needs_clarification')).toBe(1)
    expect(doneWhenAsked('needs_emergency_confirmation')).toBe(1)
  })
})

describe('words for a search', () => {
  it('name the emergency signs, the rules in plain words and the model as it said them, once each', () => {
    const emergency = { redFlags: ['chest_pain', 'breathlessness', 'unknown_flag'], modelSigns: ['Chest pain', 'sweating', ' '] }
    expect(signsOf(emergency)).toEqual(['Chest pain', 'Hard to breathe', 'sweating'])
    expect(signsOf(null)).toEqual([])
    expect(isSelfHarm({ redFlags: ['self_harm'] })).toBe(true)
    expect(isSelfHarm({ redFlags: ['chest_pain'] })).toBe(false)
  })

  it('say how far, in metres when close', () => {
    expect([1.24, 12.6, 0.34, 0.01, null, -1].map(distanceWords)).toEqual([
      '1.2 km away',
      '13 km away',
      '350 m away',
      '50 m away',
      null,
      null,
    ])
  })

  it('link only to web pages, and to directions only with a place', () => {
    expect(safeLink('https://www.practo.com/x')).toBe('https://www.practo.com/x')
    expect(safeLink('javascript:alert(1)')).toBeNull()
    expect(safeLink('not a link')).toBeNull()
    expect(directionsLink({ lat: 19.1197, lon: 72.8464 })).toBe(
      'https://www.openstreetmap.org/directions?to=19.1197%2C72.8464',
    )
    expect(directionsLink({ lat: null, lon: 72.8 })).toBeNull()
  })

  it('count the doctors and name the place in the heading', () => {
    expect(resultsHeading(makeSearch())).toBe('2 doctors near your home')
    expect(resultsHeading(makeSearch({ doctors: [makeSearch().doctors[0]], searchLocation: null }))).toBe('1 doctor')
  })

  it('mark recent searches by how they ended', () => {
    expect(
      ['doctors_found', 'emergency', 'needs_location', 'running', 'no_doctors_nearby'].map(
        (status) => searchChip(status).word,
      ),
    ).toEqual(['Doctors found', 'Emergency', 'Waiting for your answer', 'Still looking', 'No doctors shown'])
  })
})
