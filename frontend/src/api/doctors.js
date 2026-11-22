import { api } from './client'

// backend/api/health_requests.py — doctor search. The person describes the need in their own
// words; the backend checks for an emergency, picks the kind of doctor, finds where to look and
// ranks the doctors there. It may stop once to ask one question. The AI model reads the words
// only with the saved consents (useLlm and medicalCloud), so the app does not send them.
//
// A search (HealthResultDto):
//   requestId, status
//   status:  doctors_found | no_doctors_nearby | emergency
//            needs_location | needs_clarification | needs_emergency_confirmation   (a question waits)
//            not_understood | not_available | rejected | search_unavailable | location_not_found
//            failed | expired | running (still being worked on)
//   specialty        { key, label } once the kind of doctor is settled
//   doctorFallback   "general_physician" when none of that kind was near, so a GP is shown
//   searchLocation   { label, source }: "your home", "your current location" or the place's name
//   searchRadiusKm   how far it looked
//   doctors          the ones shown (at most 3), each with reasons, labels and flags in words
//   otherDoctors     the rest, best first
//   isTopChoice      false when none met the bar for a first choice (see the notes)
//   notes            sentences for the person
//   attribution      must be shown when the place came from place search
//   question, options                 the question that waits, and places to pick from
//   emergency        { numbers: [{ number, for }], message, redFlags, modelSigns, source }
//   labReport        a search from a lab report (else null):
//                    { documentId, reportDate, labName, summary, seeToday, unjudged,
//                      findings: [{ test, flag, specialty, specialtyLabel }] }
//                    flag is high, low, or the word found (positive, reactive, present); no values
//
// It is kept for 30 days; a question not answered within 24 hours expires.
//
// A search can start from a lab report instead of words (startLabSearch): one of the person's
// lab reports that has been checked and saved. Its results outside the normal range printed on
// it choose the kind of doctor by a fixed table in the backend; no AI reads them for this.

/** Starts a search. onStep(step) is called with each step's name as the backend finishes it. */
export function startSearch(query, { onStep, signal } = {}) {
  return api('/health-requests', { method: 'POST', body: { query }, onStep, signal })
}

/** Starts a search from a saved lab report (its documentId). Like startSearch otherwise. */
export function startLabSearch(labReportId, { onStep, signal } = {}) {
  return api('/health-requests', { method: 'POST', body: { labReportId }, onStep, signal })
}

/** Answers the question a search asked: { location: "Andheri West, Mumbai" | { lat, lon } },
 *  { clarification: "…" } or { callEmergency: true | false }. */
export function answerSearch(requestId, answer, { onStep, signal } = {}) {
  return api(`/health-requests/${encodeURIComponent(requestId)}/answer`, {
    method: 'POST',
    body: answer,
    onStep,
    signal,
  })
}

export function getSearch(requestId, signal) {
  return api(`/health-requests/${encodeURIComponent(requestId)}`, { signal })
}

/** The searches of the last 30 days, newest first: [{ requestId, status, specialty, createdAt, fromLabReport }]. */
export function listSearches(signal, limit = 5) {
  return api(`/health-requests?limit=${limit}`, { signal })
}
