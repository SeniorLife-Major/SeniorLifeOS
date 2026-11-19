// What the doctor search screens say, from what the backend sends (api/doctors.js).

/** The stages shown while searching (the mockup's Searching screen), each done when one of the
 *  backend's steps (graphs/health_graph.py) finishes. */
export const STAGES = [
  'Checking for emergency signs',
  'Choosing the right kind of doctor',
  'Looking for doctors nearby',
  'Checking registrations',
  'Picking the best three',
]

// The step that finishes each stage. The other steps (validate_request, understand_request,
// confirm_emergency, ask_clarification, locate, ask_location, rank) happen within a stage.
const FINISHES = { safety_check: 0, map_specialty: 1, search_doctors: 2, verify: 3, explain: 4 }

/** How many stages are done once this step has finished (never fewer than before). */
export function stagesDone(before, step) {
  return step in FINISHES ? Math.max(before, FINISHES[step] + 1) : before
}

/** The stages already done when a search stopped to ask this question. */
export function doneWhenAsked(status) {
  if (status === 'needs_location') return 2
  if (status === 'needs_clarification' || status === 'needs_emergency_confirmation') return 1
  return 0
}

export const QUESTIONS = new Set(['needs_location', 'needs_clarification', 'needs_emergency_confirmation'])

/** The heading and the sentence for a search that ended without doctors to show. */
export function endWords(status) {
  switch (status) {
    case 'no_doctors_nearby':
      return { heading: 'No doctor found nearby', text: 'We looked, but found no doctor of this kind near you.' }
    case 'not_understood':
      return { heading: 'We could not understand', text: 'Please start again and say what is wrong in your own words.' }
    case 'not_available':
      return { heading: 'We can only find doctors here', text: 'This part of SeniorLife can help you find a doctor.' }
    case 'rejected':
      return { heading: 'That was too long', text: 'Please start again with a shorter message.' }
    case 'search_unavailable':
      return { heading: 'Doctor search is not working', text: 'Please try again in a little while.' }
    case 'location_not_found':
      return { heading: 'We could not find that place', text: 'Please start again with the area and the city.' }
    case 'expired':
      return { heading: 'This question has expired', text: 'It was not answered within a day. Please start again.' }
    case 'running':
      return { heading: 'Still looking', text: 'This search is not finished yet. Please check again in a moment.' }
    case 'failed':
    default:
      return { heading: 'Something went wrong', text: 'This search did not finish. Please try again.' }
  }
}

/** A word for a search in the list of recent searches. */
export function searchChip(status) {
  if (status === 'doctors_found') return { kind: 'ready', word: 'Doctors found' }
  if (status === 'emergency') return { kind: 'problem', word: 'Emergency' }
  if (QUESTIONS.has(status)) return { kind: 'check', word: 'Waiting for your answer' }
  if (status === 'running') return { kind: 'reading', word: 'Still looking' }
  return { kind: 'missing', word: 'No doctors shown' }
}

// The emergency signs the backend's rules know (backend/tools/red_flags.py), in plain words.
const SIGNS = {
  chest_pain: 'Chest pain',
  stroke_signs: 'Signs of a stroke',
  breathlessness: 'Hard to breathe',
  heavy_bleeding: 'Heavy bleeding',
  fainting: 'Fainting',
  seizure: 'A fit (seizure)',
  self_harm: 'Thoughts of hurting yourself',
  severe_allergy: 'Swelling from an allergy',
  fever_confusion: 'Fever with confusion',
}

/** The signs to show: the rules' ones in words, then the model's own (already in words), once each. */
export function signsOf(emergency) {
  const words = (emergency?.redFlags ?? []).map((flag) => SIGNS[flag]).filter(Boolean)
  const model = (emergency?.modelSigns ?? []).filter((sign) => typeof sign === 'string' && sign.trim())
  return [...new Set([...words, ...model])]
}

export function isSelfHarm(emergency) {
  return (emergency?.redFlags ?? []).includes('self_harm')
}

/** "1.2 km away", "350 m away"; null when the distance is not known. */
export function distanceWords(km) {
  if (typeof km !== 'number' || !Number.isFinite(km) || km < 0) return null
  if (km < 1) return `${Math.max(50, Math.round((km * 1000) / 50) * 50)} m away`
  return `${km < 10 ? km.toFixed(1) : Math.round(km)} km away`
}

/** The clinic in one line: "Demo Bone and Joint Clinic, Andheri West". */
export function clinicLine(clinic) {
  const parts = [clinic?.name, clinic?.locality ?? clinic?.city].filter((part) => typeof part === 'string' && part)
  return parts.join(', ') || null
}

/** Directions on OpenStreetMap (as in the mockup); null without the clinic's place. */
export function directionsLink(clinic) {
  const { lat, lon } = clinic ?? {}
  if (typeof lat !== 'number' || typeof lon !== 'number') return null
  return `https://www.openstreetmap.org/directions?to=${lat}%2C${lon}`
}

/** The doctor's page, only when it is a web address (never a javascript: one). */
export function safeLink(url) {
  if (typeof url !== 'string') return null
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.href : null
  } catch {
    return null
  }
}

/** The heading over the doctors: "3 doctors near your home". */
export function resultsHeading(result) {
  const count = result.doctors.length
  const place = result.searchLocation?.label
  const doctors = `${count} ${count === 1 ? 'doctor' : 'doctors'}`
  return place ? `${doctors} near ${place}` : doctors
}

const SEARCH_DATE = new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short' })

/** "19 Nov" in this phone's time zone; null for an odd date. */
export function searchDate(value) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : SEARCH_DATE.format(date)
}
