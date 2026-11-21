import { fieldLabel, rowWord, showValue } from './fields'

// The reader's notes ("flags") are written for developers, one per line, mostly as
// "<detail>: <what>, please check" (validate_candidates and confidence_gate in
// backend/graphs/document_graph.py). Here each becomes a sentence under its detail. A note this
// app doesn't know still marks its detail, with a general sentence.

const NOTES = [
  [/low confidence/, 'We were not sure we read this right.'],
  [/read by the AI vision model/, 'Read by an AI from the picture.'],
  [/found by AI reading the text/, 'Found by an AI reading the words.'],
  [/found by layout|taken from the page heading|taken from a line starting with Dr\./, 'Found by where it sits on the page.'],
  [/found by its check digit/, 'Found by its check digit; it had no label on the paper.'],
  [/read again by cloud OCR/, 'Read again by a cloud service.'],
  [/(cloud|local) reading differs/, 'Two readings of this were different.'],
  [/not found in the text read/, 'We could not find this in the words we read.'],
  [/look-alike characters corrected/, 'Some letters or digits that look alike were changed (like O and 0).'],
  [/extra text after the value was ignored/, 'Some words after it were left out.'],
  [/failed format or checksum check/, 'This number does not look right.'],
  [/may be cut off/, 'It may be cut off.'],
  [/a word may be misread/, 'A word may be misread.'],
  [/differs slightly from your earlier document/, 'It is a little different on your earlier document.'],
  [/is in the future/, 'This date is in the future.'],
  [/is after the issue date/, 'This is after the date the paper was issued.'],
  [/found as a word printed with no label/, 'Found as a word printed on the card, with no label.'],
  [/please check every row/, null], // every list is checked anyway
  [/missing or invalid/, null], // shown as "We could not find this"
]

const ROW_NOTES = {
  'value is not a number': 'the result is not a number',
  'no test name': 'the test has no name',
  'no medicine name': 'the medicine has no name',
  'amount is not readable': 'the amount could not be read',
  'no description': 'it does not say what the item is',
}

const GENERAL = 'Please check this one carefully.'

function sentenceFor(note) {
  for (const [pattern, sentence] of NOTES) {
    if (pattern.test(note)) return sentence
  }
  return GENERAL
}

function add(notes, key, sentence) {
  if (!sentence) return
  const list = notes[key] ?? (notes[key] = [])
  if (!list.includes(sentence)) list.push(sentence)
}

/** { detail: [sentences] } for the reader's flags; `known` is the list of details on this document. */
export function notesFor(flags, known) {
  const notes = {}
  for (const flag of flags ?? []) {
    if (typeof flag !== 'string') continue
    let match = /^items add up to ([\d.]+) but the total says ([\d.]+)/.exec(flag)
    if (match) {
      add(notes, 'total_amount', `The items add up to ${showValue('total_amount', match[1])}, but the total says ${showValue('total_amount', match[2])}.`)
      continue
    }
    if (flag.startsWith('document is expired')) {
      add(notes, 'expiry_date', 'This date has passed.')
      continue
    }
    if (flag.startsWith('name differs from the provided owner name')) {
      add(notes, 'name', 'This is different from the name you gave.')
      continue
    }
    match = /^([a-z_]+) row (\d+): (.+)$/.exec(flag)
    if (match && known.includes(match[1])) {
      const what = ROW_NOTES[match[3]] ?? 'please check it'
      add(notes, match[1], `${rowWord(match[1]).replace(/^./, (c) => c.toUpperCase())} ${match[2]}: ${what}.`)
      continue
    }
    match = /^([a-z_]+)(:| is )/.exec(flag)
    if (match && known.includes(match[1])) add(notes, match[1], sentenceFor(flag))
  }
  return notes
}

// FUTURE_DATE_LABELS in backend/graphs/document_graph.py
const FUTURE_DATES = {
  'Issue date': 'issue_date',
  'Report date': 'report_date',
  'Prescription date': 'prescription_date',
  'Bill date': 'bill_date',
  'Date of death': 'date_of_death',
}

// DATE_ORDER in backend/graphs/document_graph.py: the refusal and the details it names.
export const DATE_ORDER = {
  'Date of death cannot be after the date it was issued': {
    fields: ['date_of_death', 'issue_date'],
    sentence: 'The date of death cannot be after the date the certificate was issued.',
  },
}

/**
 * When the backend refuses the details (the document stays "needs_review" with errors), the
 * sentence to show and the details it names (human_field_review in document_graph.py).
 */
export function refusalOf(errors) {
  const fields = []
  const sentences = []
  for (const error of errors ?? []) {
    if (typeof error !== 'string') continue
    let match = /^Correct these required fields: (.+)$/.exec(error)
    if (match) {
      const names = match[1].split(',').map((name) => name.trim())
      fields.push(...names)
      sentences.push(`Please fill in: ${names.map(fieldLabel).join(', ')}.`)
      continue
    }
    match = /^Correct these fields: (.+)$/.exec(error)
    if (match) {
      const names = match[1].split(',').map((name) => name.trim())
      fields.push(...names)
      sentences.push(`Please check: ${names.map(fieldLabel).join(', ')}.`)
      continue
    }
    if (DATE_ORDER[error]) {
      fields.push(...DATE_ORDER[error].fields)
      sentences.push(DATE_ORDER[error].sentence)
      continue
    }
    match = /^(.+) cannot be in the future$/.exec(error)
    if (match) {
      const key = FUTURE_DATES[match[1]]
      if (key) fields.push(key)
      sentences.push(`${key ? fieldLabel(key) : match[1]}: this date cannot be in the future.`)
      continue
    }
    sentences.push('These details could not be saved. Please check them and try again.')
  }
  return { message: [...new Set(sentences)].join(' '), fields }
}
