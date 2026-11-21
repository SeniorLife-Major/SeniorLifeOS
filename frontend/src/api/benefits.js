import { api } from './client'

// backend/api/benefits.py — made-up demo schemes checked against the person's confirmed papers.
//
// A check (CheckDto): checkId, mode ("screen" or "apply"), status, asOf, schemeId, schemes, notes,
// declarations, package, createdAt.
//   schemes: [{ schemeId, name, summary, benefit, status, exclusiveGroup, criteria, documents,
//              gaps, toConfirm }]
//     status:    likely, add_documents, not_eligible or please_check
//     criteria:  [{ criterionId, label, fact, status (met, unmet, unknown, conflict), value, note }]
//     documents: [{ documentType, documentId, status (usable, unconfirmed, expired, missing) }]
//     gaps:      [{ documentType, documentId, reason, message }] — what to add or check; reason is
//                missing, unconfirmed, expired, detail_missing or check
//     toConfirm: [{ key, label, confirmed }] — what no paper shows, so the person says it
//   status, for one scheme: needs_input, not_eligible_in_demo, blocked_conflict, awaiting_review
//     (a package waits for the person's yes), ready_for_manual_handoff (approved);
//     a screening is "screened".
//   package: { actionId, schemeId, schemeName, documents, declarations, checklist, tasks, warning,
//              status ("proposed" or "approved_for_manual_handoff") } or null

/** The demo schemes and what each asks for (SchemeDto). */
export function listSchemes(signal) {
  return api('/benefits/schemes', { signal })
}

/** Every scheme against the person's papers ("Schemes for me"). */
export function screenSchemes(signal) {
  return api('/benefits/checks', { method: 'POST', body: {}, signal })
}

/**
 * One scheme, with the person's own answers ({ not_remarried: true }). When it passes, the answer's
 * package waits for approveCheck. 404 when the scheme is not in the list.
 */
export function checkScheme(schemeId, declarations = {}, signal) {
  return api('/benefits/checks', { method: 'POST', body: { schemeId, declarations }, signal })
}

/**
 * Yes to the package a check prepared (its actionId, as shown). 409 when a paper or an answer
 * changed since, or a day has passed: check again.
 */
export function approveCheck(checkId, actionId, taskTitle) {
  const body = { actionId, ...(taskTitle ? { taskTitle } : {}) }
  return api(`/benefits/checks/${encodeURIComponent(checkId)}/approve`, { method: 'POST', body })
}

export function getCheck(checkId, signal) {
  return api(`/benefits/checks/${encodeURIComponent(checkId)}`, { signal })
}
