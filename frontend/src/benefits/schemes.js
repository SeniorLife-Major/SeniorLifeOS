import { CARD_CATEGORIES, GENDERS } from '../documents/fields'
import { canAdd, inSentence, typeLabel } from '../documents/types'

// The words for a scheme check (backend/api/benefits.py), kept apart from the screens so they can
// be tested on their own.

/** How each scheme's answer looks: the chip's kind (styles/app.css) and its words. */
export const SCHEME_LOOK = {
  likely: { kind: 'ready', word: 'You may get this' },
  please_check: { kind: 'problem', word: 'Please check' },
  add_documents: { kind: 'check', word: 'Add papers to know' },
  not_eligible: { kind: 'missing', word: 'Not for you now' },
}

/** The groups on "Schemes for me", in the order they are shown. */
export const GROUPS = [
  { status: 'likely', title: 'You may get these' },
  { status: 'please_check', title: 'Please check these' },
  { status: 'add_documents', title: 'Add papers to know' },
  { status: 'not_eligible', title: 'Not for you now' },
]

export function groupSchemes(schemes) {
  return GROUPS.map((group) => ({ ...group, schemes: (schemes ?? []).filter((item) => item.status === group.status) })).filter(
    (group) => group.schemes.length > 0,
  )
}

/** How each rule's outcome looks next to it. */
export const CRITERION_LOOK = {
  met: { kind: 'ready', icon: 'check', word: 'Yes' },
  unmet: { kind: 'missing', icon: 'alertCircle', word: 'No' },
  unknown: { kind: 'check', icon: 'alertCircle', word: 'Not known yet' },
  conflict: { kind: 'expired', icon: 'alertCircle', word: 'Please check' },
}

const GENDER_WORDS = new Map(GENDERS)
const CATEGORY_WORDS = new Map(CARD_CATEGORIES)
const RUPEES = new Intl.NumberFormat('en-IN')

/** What the papers say for a rule ("68 years", "Woman", "Priority household (PHH)"), or null. */
export function factValue(fact, value) {
  if (value === null || value === undefined || value === '') return null
  switch (fact) {
    case 'age':
      return `${value} years`
    case 'gender':
      return GENDER_WORDS.get(value) ?? String(value)
    case 'ration_card_category':
      return CATEGORY_WORDS.get(value) ?? String(value)
    case 'disability_percent':
      return `${value}%`
    case 'widowed':
      return value === true ? 'Yes, the certificate names you' : 'No'
    case 'annual_income':
      return Number.isFinite(Number(value)) ? `₹${RUPEES.format(Number(value))} a year` : String(value)
    default:
      return String(value)
  }
}

/** Where one thing to do leads: { to, label }, or null when there is nowhere to go. */
export function gapAction(gap) {
  const id = gap.documentId ? encodeURIComponent(gap.documentId) : null
  switch (gap.reason) {
    case 'missing':
      return canAdd(gap.documentType)
        ? { to: `/documents/add?type=${gap.documentType}`, label: `Add the ${inSentence(typeLabel(gap.documentType))}` }
        : null
    case 'expired':
      return id ? { to: `/documents/${id}/new-copy`, label: 'Add the new copy' } : null
    case 'unconfirmed':
      return id ? { to: `/documents/${id}`, label: 'See it' } : null
    case 'detail_missing':
      return id ? { to: `/documents/${id}`, label: 'Open it' } : null
    case 'check':
      return id ? { to: `/documents/${id}`, label: 'Look at it' } : null
    default:
      return null
  }
}

/** The first thing to do for a scheme, to show on its card in the list. */
export function firstStep(scheme) {
  return scheme.gaps?.[0]?.message ?? null
}

/** The package's papers in words: "ID card", "Death certificate", … */
export function packagePapers(pack) {
  return (pack?.documents ?? []).map((item) => ({ ...item, label: typeLabel(item.documentType) }))
}
