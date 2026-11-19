import { MAX_UPLOAD_BYTES, UPLOAD_TYPES } from '../api/documents'

// Gets a picked file ready for the backend, which takes one JPEG, PNG or PDF of at most 5 MB.
// A phone photo is often bigger, and some phones save other kinds (WebP, or HEIC on iPhones), so
// such a photo is redrawn here as a smaller JPEG. A PDF can't be made smaller here.

export const SAY = {
  notPhotoOrPdf: 'Please choose a photo or a PDF.',
  empty: 'This file is empty. Please choose another one.',
  pdfTooBig: 'This PDF is bigger than 5 MB. Please choose a smaller one, or take a photo of the page instead.',
  photoTooBig: 'This photo is too big to send, and we could not make it smaller. Please take the photo again.',
  photoKind: 'This kind of photo cannot be used. Please take the photo again, or choose a JPEG or PNG photo.',
}

export class FileProblem extends Error {}

// Tried in turn until the photo fits: the longest side in pixels, and the JPEG quality.
const STEPS = [
  [3000, 0.9],
  [2400, 0.85],
  [2000, 0.8],
  [1600, 0.75],
]

function jpegName(name) {
  const base = (name || 'photo').replace(/\.[^.]*$/, '')
  return `${base || 'photo'}.jpg`
}

/** The photo redrawn as a JPEG of at most 5 MB, or null if this browser can't (or it won't fit). */
export async function shrinkPhoto(file) {
  if (typeof createImageBitmap !== 'function' || typeof document === 'undefined') return null
  let bitmap
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' })
  } catch {
    return null // a kind of image this browser can't open (e.g. HEIC outside Safari)
  }
  try {
    for (const [side, quality] of STEPS) {
      const scale = Math.min(1, side / Math.max(bitmap.width, bitmap.height))
      const canvas = document.createElement('canvas')
      canvas.width = Math.max(1, Math.round(bitmap.width * scale))
      canvas.height = Math.max(1, Math.round(bitmap.height * scale))
      const context = canvas.getContext('2d')
      if (!context) return null
      context.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
      const blob = await new Promise((done) => canvas.toBlob(done, 'image/jpeg', quality))
      if (blob && blob.size > 0 && blob.size <= MAX_UPLOAD_BYTES) {
        return new File([blob], jpegName(file.name), { type: 'image/jpeg' })
      }
    }
    return null
  } finally {
    bitmap.close?.()
  }
}

/** A quick look when the file is picked: the message to show, or null if it can go ahead. */
export function checkPicked(file) {
  if (!file) return SAY.notPhotoOrPdf
  const isPdf = file.type === 'application/pdf'
  if (!isPdf && !file.type.startsWith('image/')) return SAY.notPhotoOrPdf
  if (file.size === 0) return SAY.empty
  if (isPdf && file.size > MAX_UPLOAD_BYTES) return SAY.pdfTooBig
  return null
}

/** The file to send: the one picked, or a smaller JPEG of it. Throws a FileProblem with a message. */
export async function prepareUpload(file, shrink = shrinkPhoto) {
  const problem = checkPicked(file)
  if (problem) throw new FileProblem(problem)
  if (UPLOAD_TYPES.includes(file.type) && file.size <= MAX_UPLOAD_BYTES) return file
  const smaller = await shrink(file)
  if (smaller) return smaller
  throw new FileProblem(UPLOAD_TYPES.includes(file.type) ? SAY.photoTooBig : SAY.photoKind)
}
