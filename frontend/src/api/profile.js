import { api } from './client'

// backend/api/profile.py — the profile (ProfileDto):
//   language           "en", "hi", …
//   homeLocation       { label, lat, lon } or null. Kept rounded to about 100 m. label is null when
//                      the home was set from the phone's location without a name.
//   accessibility      { fontScale (0.8 to 2.0), highContrast, voice }
//   consents           { useLlm, medicalCloud, cloudOcr }: what may go to AI services
//   consentsUpdatedAt  when the consents last changed, or null
//
// A change (PATCH) holds only what changes:
//   { accessibility: { fontScale: 1.25 } }
//   { consents: { cloudOcr: true } }
//   { homeLocation: { place: "Andheri West, Mumbai" } }        looked up by the backend
//   { homeLocation: { lat, lon, place } }                       the phone's location, or a chosen place
//   { homeLocation: null }                                      forget the home

export const PLACE_MAX = 200

export function getProfile(signal) {
  return api('/me/profile', { signal })
}

/** Saves a change; gives back the whole profile as saved. */
export function updateProfile(changes) {
  return api('/me/profile', { method: 'PATCH', body: changes })
}

/** The places to choose from when a place name fits more than one: a 422 whose details hold
 *  options ({ label, lat, lon }). Null for any other error. */
export function placeOptions(error) {
  const options = error?.details?.options
  if (!Array.isArray(options)) return null
  const usable = options.filter((option) => typeof option?.lat === 'number' && typeof option?.lon === 'number')
  return usable.length ? usable : null
}
