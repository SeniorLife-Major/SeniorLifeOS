import { describe, expect, it } from 'vitest'
import { makeDocument } from '../test/fakeBackend'
import { formatDate, inAttentionOrder, isWorking, statusOf, todayISO } from './status'

const TODAY = '2026-11-19'

describe('statusOf', () => {
  it('names every status the backend can give in a few plain words', () => {
    const says = (changes) => statusOf(makeDocument(changes), TODAY)

    expect(says({ status: 'processing' })).toEqual({
      kind: 'reading',
      chip: 'Reading',
      detail: 'Being read. You can leave this screen.',
    })
    expect(says({ status: 'reviewed' })).toMatchObject({ kind: 'reading', chip: 'Saving' })
    expect(says({ status: 'needs_review' })).toMatchObject({ kind: 'check', chip: 'Please check' })
    expect(says({ status: 'needs_reupload' })).toMatchObject({ kind: 'problem', chip: 'Photo not clear' })
    expect(says({ status: 'needs_reupload', mimeType: 'application/pdf' })).toMatchObject({
      kind: 'problem',
      chip: 'Could not read',
    })
    expect(says({ status: 'needs_classification' })).toMatchObject({ kind: 'problem', chip: 'Type not known' })
    expect(says({ status: 'rejected' })).toMatchObject({ kind: 'problem', chip: 'Not kept' })
    expect(says({ status: 'failed_tool' })).toMatchObject({ kind: 'problem', chip: 'Could not read' })
    expect(says({ status: 'something_new' })).toMatchObject({ kind: 'problem', chip: 'Not ready' })
  })

  it('tells a saved document from one that has ended', () => {
    const says = (changes) => statusOf(makeDocument({ status: 'confirmed', ...changes }), TODAY)

    expect(says({ expiryDate: '2029-08-28' })).toEqual({ kind: 'ready', chip: 'Ready', detail: 'Valid till 28 Aug 2029' })
    expect(says({ expiryDate: TODAY })).toMatchObject({ kind: 'ready' }) // still valid on its last day
    expect(says({ expiryDate: '2026-03-31' })).toEqual({ kind: 'expired', chip: 'Expired', detail: 'Ended 31 Mar 2026' })
    // Only a saved document counts as ended; one waiting for a check is still to be checked.
    expect(says({ status: 'needs_review', expiryDate: '2026-03-31' })).toMatchObject({ kind: 'check' })
  })

  it('gives a saved document the best date it has', () => {
    const detail = (changes) => statusOf(makeDocument({ status: 'confirmed', ...changes }), TODAY).detail

    expect(detail({ issueDate: '2025-04-02' })).toBe('Issued 2 Apr 2025')
    expect(detail({})).toBe('Added 1 Nov 2026')
    expect(detail({ createdAt: null })).toBe('Saved and ready to use')
  })
})

describe('isWorking', () => {
  it('is true only while the backend is still reading or saving', () => {
    expect(isWorking(makeDocument({ status: 'processing' }))).toBe(true)
    expect(isWorking(makeDocument({ status: 'reviewed' }))).toBe(true)
    for (const status of ['needs_review', 'confirmed', 'needs_reupload', 'rejected', 'failed_tool']) {
      expect(isWorking(makeDocument({ status }))).toBe(false)
    }
    expect(isWorking(null)).toBe(false)
  })
})

describe('inAttentionOrder', () => {
  it('puts what needs the person first, and keeps the newest first within each group', () => {
    const documents = [
      makeDocument({ documentId: 'ready-new', status: 'confirmed' }),
      makeDocument({ documentId: 'expired', status: 'confirmed', expiryDate: '2026-01-01' }),
      makeDocument({ documentId: 'problem', status: 'failed_tool' }),
      makeDocument({ documentId: 'check-new', status: 'needs_review' }),
      makeDocument({ documentId: 'reading', status: 'processing' }),
      makeDocument({ documentId: 'ready-old', status: 'confirmed' }),
      makeDocument({ documentId: 'check-old', status: 'needs_review' }),
    ]

    expect(inAttentionOrder(documents, TODAY).map((document) => document.documentId)).toEqual([
      'check-new',
      'check-old',
      'reading',
      'problem',
      'expired',
      'ready-new',
      'ready-old',
    ])
  })
})

describe('dates', () => {
  it('are written the Indian way, and a missing or odd date gives nothing', () => {
    expect(formatDate('2029-08-28')).toBe('28 Aug 2029')
    expect(formatDate('2026-11-01T23:30:00Z')).toBe('1 Nov 2026') // the date as written, whatever the time zone
    expect(formatDate(null)).toBeNull()
    expect(formatDate('soon')).toBeNull()
  })

  it("know today's date on this phone's calendar", () => {
    expect(todayISO(new Date(2026, 10, 19, 23, 59))).toBe('2026-11-19')
    expect(todayISO(new Date(2027, 0, 5))).toBe('2027-01-05')
  })
})
