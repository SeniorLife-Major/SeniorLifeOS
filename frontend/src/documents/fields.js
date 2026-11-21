import { formatDate, todayISO } from './status'

// The details on each kind of document, as the backend names them (REQUIRED_FIELDS and
// OPTIONAL_MANUAL_FIELDS in backend/graphs/document_graph.py), in the order they are checked.
export const FIELD_ORDER = {
  identity: ['name', 'date_of_birth', 'gender', 'aadhaar_number', 'pan_number'],
  income_certificate: ['name', 'annual_income', 'issue_date', 'issuing_authority', 'expiry_date'],
  residence_proof: ['name', 'residence_state', 'issue_date', 'issuing_authority', 'expiry_date'],
  health_insurance: ['name', 'insurer', 'policy_number', 'sum_insured', 'expiry_date'],
  bank_passbook: ['name', 'bank_name', 'account_number', 'ifsc'],
  disability_certificate: ['name', 'disability_percent', 'issuing_authority', 'issue_date', 'expiry_date'],
  lab_report: ['name', 'lab_name', 'report_date', 'tests'],
  prescription: ['name', 'doctor_name', 'prescription_date', 'medicines'],
  medical_bill: ['name', 'provider', 'bill_date', 'total_amount', 'items'],
  death_certificate: [
    'deceased_name',
    'date_of_death',
    'spouse_name',
    'age_at_death',
    'place_of_death',
    'registration_number',
    'issuing_authority',
    'issue_date',
  ],
  ration_card: ['head_of_family', 'card_category', 'residence_state', 'family_members', 'ration_card_number', 'issue_date'],
}

// The details each kind of document must have (REQUIRED_FIELDS in backend/graphs/document_graph.py).
export const REQUIRED_FIELDS = {
  identity: ['name', 'date_of_birth'],
  income_certificate: ['name', 'annual_income', 'issue_date'],
  residence_proof: ['name', 'residence_state', 'issue_date'],
  health_insurance: ['name', 'insurer', 'policy_number', 'expiry_date', 'sum_insured'],
  bank_passbook: ['name', 'bank_name', 'account_number', 'ifsc'],
  disability_certificate: ['name', 'issuing_authority', 'issue_date', 'disability_percent'],
  lab_report: ['name', 'lab_name', 'report_date', 'tests'],
  prescription: ['name', 'doctor_name', 'prescription_date', 'medicines'],
  medical_bill: ['name', 'provider', 'bill_date', 'total_amount'],
  death_certificate: ['deceased_name', 'date_of_death', 'issue_date', 'issuing_authority'],
  ration_card: ['head_of_family', 'card_category', 'residence_state'],
}

// kind decides how a value is shown, typed and checked (normalize_field in backend/tools/demo_tools.py).
const FIELDS = {
  name: { label: 'Name', kind: 'text' },
  date_of_birth: { label: 'Date of birth', kind: 'date', past: true },
  aadhaar_number: { label: 'Aadhaar number', kind: 'aadhaar' },
  pan_number: { label: 'PAN number', kind: 'pan' },
  annual_income: { label: 'Yearly income', kind: 'amount' },
  issue_date: { label: 'Date it was issued', kind: 'date', past: true },
  issuing_authority: { label: 'Office that issued it', kind: 'text' },
  expiry_date: { label: 'Valid till', kind: 'date' },
  residence_state: { label: 'State', kind: 'state' },
  insurer: { label: 'Insurance company', kind: 'text' },
  policy_number: { label: 'Policy number', kind: 'policy' },
  sum_insured: { label: 'Amount covered', kind: 'amount' },
  bank_name: { label: 'Bank', kind: 'text' },
  account_number: { label: 'Account number', kind: 'account' },
  ifsc: { label: 'IFSC code', kind: 'ifsc' },
  disability_percent: { label: 'Disability (%)', kind: 'percent' },
  lab_name: { label: 'Lab', kind: 'text' },
  report_date: { label: 'Report date', kind: 'date', past: true },
  tests: { label: 'Tests', kind: 'rows' },
  doctor_name: { label: 'Doctor', kind: 'text' },
  prescription_date: { label: 'Prescription date', kind: 'date', past: true },
  medicines: { label: 'Medicines', kind: 'rows' },
  provider: { label: 'Hospital or shop', kind: 'text' },
  bill_date: { label: 'Bill date', kind: 'date', past: true },
  total_amount: { label: 'Total amount', kind: 'money' },
  items: { label: 'Bill items', kind: 'rows' },
  gender: { label: 'Gender', kind: 'gender' },
  deceased_name: { label: 'Name of the person who died', kind: 'text' },
  date_of_death: { label: 'Date of death', kind: 'date', past: true },
  spouse_name: { label: 'Name of husband or wife', kind: 'text' },
  age_at_death: { label: 'Age at death', kind: 'age' },
  place_of_death: { label: 'Place of death', kind: 'text' },
  registration_number: { label: 'Registration number', kind: 'code' },
  head_of_family: { label: 'Head of the family', kind: 'text' },
  card_category: { label: 'Card type', kind: 'category' },
  family_members: { label: 'Family members (names, with commas)', kind: 'text' },
  ration_card_number: { label: 'Ration card number', kind: 'code' },
}

export const TEXT_MAX = 200

/** "annual_income" → "Yearly income"; a detail this app doesn't know gets its name in words. */
export function fieldLabel(key) {
  if (FIELDS[key]) return FIELDS[key].label
  const words = key.replace(/_/g, ' ').trim()
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export function fieldKind(key) {
  return FIELDS[key]?.kind ?? 'text'
}

/** A date that can't be after today (a birth date, or the day a paper was issued). */
export function mustBePast(key) {
  return Boolean(FIELDS[key]?.past)
}

export function isBlank(value) {
  return value === null || value === undefined || value === '' || (Array.isArray(value) && value.length === 0)
}

// States and union territories (INDIAN_STATES in backend/tools/demo_tools.py), by their code.
export const STATES = [
  ['AN', 'Andaman and Nicobar Islands'],
  ['AP', 'Andhra Pradesh'],
  ['AR', 'Arunachal Pradesh'],
  ['AS', 'Assam'],
  ['BR', 'Bihar'],
  ['CH', 'Chandigarh'],
  ['CT', 'Chhattisgarh'],
  ['DH', 'Dadra and Nagar Haveli and Daman and Diu'],
  ['DL', 'Delhi'],
  ['GA', 'Goa'],
  ['GJ', 'Gujarat'],
  ['HR', 'Haryana'],
  ['HP', 'Himachal Pradesh'],
  ['JK', 'Jammu and Kashmir'],
  ['JH', 'Jharkhand'],
  ['KA', 'Karnataka'],
  ['KL', 'Kerala'],
  ['LA', 'Ladakh'],
  ['LD', 'Lakshadweep'],
  ['MP', 'Madhya Pradesh'],
  ['MH', 'Maharashtra'],
  ['MN', 'Manipur'],
  ['ML', 'Meghalaya'],
  ['MZ', 'Mizoram'],
  ['NL', 'Nagaland'],
  ['OD', 'Odisha'],
  ['PY', 'Puducherry'],
  ['PB', 'Punjab'],
  ['RJ', 'Rajasthan'],
  ['SK', 'Sikkim'],
  ['TN', 'Tamil Nadu'],
  ['TG', 'Telangana'],
  ['TR', 'Tripura'],
  ['UP', 'Uttar Pradesh'],
  ['UK', 'Uttarakhand'],
  ['WB', 'West Bengal'],
]
const STATE_NAMES = new Map(STATES)

// GENDERS in backend/tools/demo_tools.py (parse_gender), as the backend stores them.
export const GENDERS = [
  ['female', 'Woman'],
  ['male', 'Man'],
  ['other', 'Transgender or other'],
]
const GENDER_NAMES = new Map(GENDERS)

// CARD_CATEGORIES in backend/tools/demo_tools.py: the ration card's type, as one code.
export const CARD_CATEGORIES = [
  ['AAY', 'Antyodaya (AAY)'],
  ['PHH', 'Priority household (PHH)'],
  ['BPL', 'Below poverty line (BPL)'],
  ['APL', 'Above poverty line (APL)'],
  ['NPHH', 'Non-priority household (NPHH)'],
]
const CATEGORY_NAMES = new Map(CARD_CATEGORIES)

// The details chosen from a list: the list and what the empty choice says.
const CHOICES = {
  state: { options: STATES, empty: 'Choose the state', problem: 'Please choose the state.' },
  gender: { options: GENDERS, empty: 'Choose one', problem: 'Please choose one.' },
  category: { options: CARD_CATEGORIES, empty: 'Choose the card type', problem: 'Please choose the card type.' },
}

/** For a detail chosen from a list (state, gender, card type): { options: [[code, name]], empty, problem }; else undefined. */
export function choicesFor(kind) {
  return CHOICES[kind]
}

const RUPEES = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 })

/** A detail as the person reads it: "₹2,56,000", "23 Dec 2025", "Maharashtra", "40%". */
export function showValue(key, value) {
  if (isBlank(value)) return ''
  switch (fieldKind(key)) {
    case 'date':
      return formatDate(String(value)) ?? String(value)
    case 'amount':
    case 'money':
      return Number.isFinite(Number(value)) ? `₹${RUPEES.format(Number(value))}` : String(value)
    case 'percent':
      return `${value}%`
    case 'state':
      return STATE_NAMES.get(String(value)) ?? String(value)
    case 'gender':
      return GENDER_NAMES.get(String(value)) ?? String(value)
    case 'category':
      return CATEGORY_NAMES.get(String(value)) ?? String(value)
    case 'age':
      return `${value} years`
    case 'aadhaar':
      return String(value).replace(/(\d{4})(?=\d)/g, '$1 ')
    case 'rows':
      return Array.isArray(value) ? value.map((row) => showRow(key, row)).join('; ') : ''
    default:
      return String(value)
  }
}

/** The text to start the typing box with. */
export function toInput(key, value) {
  if (isBlank(value)) return ''
  return String(value)
}

// ---------- checking what the person typed ----------

function realDate(text) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text)
  if (!match) return false
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])]
  const date = new Date(year, month - 1, day)
  return date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day
}

const WITHOUT_RUPEES = (text) =>
  text
    .replace(/^(₹|rs\.?|inr)\s*/i, '')
    .replace(/\s*(\/-|\/=)$/, '')
    .replace(/[,\s]/g, '')

const CHECKS = {
  text: (text) => ({ value: text.replace(/\s+/g, ' ') }),
  date: (text, field, now) => {
    if (!realDate(text)) return { problem: 'Please choose a real date.' }
    if (field?.past && text > now) return { problem: 'This date cannot be in the future.' }
    return { value: text }
  },
  amount: (text) => {
    const plain = WITHOUT_RUPEES(text).replace(/\.0{1,2}$/, '')
    return /^\d+$/.test(plain) ? { value: plain } : { problem: 'Please type the amount in numbers, like 256000.' }
  },
  money: (text) => {
    const plain = WITHOUT_RUPEES(text)
    return /^\d+(\.\d{1,2})?$/.test(plain) ? { value: plain } : { problem: 'Please type the amount in numbers, like 1250.50.' }
  },
  percent: (text) => {
    const plain = text.replace(/\s*%$/, '')
    return /^\d{1,3}$/.test(plain) && Number(plain) <= 100
      ? { value: String(Number(plain)) }
      : { problem: 'Please type a number from 0 to 100.' }
  },
  state: (text) => (STATE_NAMES.has(text) ? { value: text } : { problem: 'Please choose the state.' }),
  gender: (text) => (GENDER_NAMES.has(text) ? { value: text } : { problem: 'Please choose one.' }),
  category: (text) => (CATEGORY_NAMES.has(text) ? { value: text } : { problem: 'Please choose the card type.' }),
  age: (text) => {
    const plain = text.replace(/\s*(years?|yrs?)$/i, '')
    return /^\d{1,3}$/.test(plain) && Number(plain) <= 130
      ? { value: String(Number(plain)) }
      : { problem: 'Please type the age in years, like 74.' }
  },
  code: (text) => {
    const code = text.replace(/\s/g, '').toUpperCase()
    return /^[A-Z0-9/:-]{4,40}$/.test(code) && /\d/.test(code)
      ? { value: code }
      : { problem: 'Please type the number as printed: letters and digits.' }
  },
  aadhaar: (text) => {
    const digits = text.replace(/[\s-]/g, '')
    return /^\d{12}$/.test(digits) ? { value: digits } : { problem: 'An Aadhaar number has 12 digits.' }
  },
  pan: (text) => {
    const pan = text.replace(/\s/g, '').toUpperCase()
    return /^[A-Z]{5}\d{4}[A-Z]$/.test(pan) ? { value: pan } : { problem: 'A PAN has 10 letters and digits, like ABCDE1234F.' }
  },
  account: (text) => {
    const digits = text.replace(/[\s-]/g, '')
    return /^\d{9,18}$/.test(digits) ? { value: digits } : { problem: 'An account number has 9 to 18 digits.' }
  },
  ifsc: (text) => {
    const ifsc = text.replace(/\s/g, '').toUpperCase()
    return /^[A-Z]{4}0[A-Z0-9]{6}$/.test(ifsc)
      ? { value: ifsc }
      : { problem: 'An IFSC code has 11 letters and digits, like SBIN0001234.' }
  },
  policy: (text) => {
    const policy = text.replace(/\s/g, '').toUpperCase()
    return /^[A-Z0-9/-]{6,30}$/.test(policy) && /\d/.test(policy)
      ? { value: policy }
      : { problem: 'Please type the policy number as printed: 6 to 30 letters and digits.' }
  },
}

/**
 * Checks one typed detail. Gives back { value } (what to send) or { problem } (what to say).
 * A detail that is not required may be left empty: { value: '' } removes what was read.
 */
export function checkTyped(key, text, { required, now = todayISO() } = {}) {
  const typed = String(text ?? '').trim()
  const kind = fieldKind(key)
  if (typed === '') {
    if (!required) return { value: '' }
    if (CHOICES[kind]) return { problem: CHOICES[kind].problem }
    if (kind === 'date') return { problem: 'Please choose the date.' }
    return { problem: 'Please type this in.' }
  }
  return (CHECKS[kind] ?? CHECKS.text)(typed, FIELDS[key], now)
}

// ---------- lists: lab tests, medicines, bill items ----------

// The parts of each row (normalize_rows in backend/tools/medical.py). The first one must be filled.
export const ROW_PARTS = {
  tests: [
    ['name', 'Test'],
    ['value', 'Result'],
    ['unit', 'Unit'],
    ['ref_range', 'Normal range'],
  ],
  medicines: [
    ['name', 'Medicine'],
    ['strength', 'Strength'],
    ['form', 'Form (tablet, syrup…)'],
    ['frequency', 'How often'],
    ['duration', 'For how long'],
  ],
  items: [
    ['description', 'Item'],
    ['amount', 'Amount'],
  ],
}

const ROW_WORD = { tests: 'test', medicines: 'medicine', items: 'item' }

export function rowWord(listKey) {
  return ROW_WORD[listKey] ?? 'row'
}

/** One row in a line: "Haemoglobin: 13.2 g/dL (normal 12.0 - 15.5)", "Metformin 500 mg, tablet, 1-0-1, 30 days". */
export function showRow(listKey, row) {
  const part = (name) => (isBlank(row?.[name]) ? '' : String(row[name]))
  if (listKey === 'tests') {
    const result = [part('value'), part('unit')].filter(Boolean).join(' ')
    const range = part('ref_range') ? ` (normal ${part('ref_range')})` : ''
    return `${part('name') || 'No name'}: ${result || 'no result'}${range}`
  }
  if (listKey === 'items') {
    const amount = part('amount') ? showValue('total_amount', part('amount')) : 'no amount'
    return `${part('description') || 'No description'}: ${amount}`
  }
  const first = [part('name') || 'No name', part('strength')].filter(Boolean).join(' ')
  return [first, part('form'), part('frequency'), part('duration')].filter(Boolean).join(', ')
}

/** Rows as editable text: every part a string ('' when missing); the reader's notes are left out. */
export function toInputRows(listKey, rows) {
  if (!Array.isArray(rows)) return []
  return rows.map((row) => Object.fromEntries(ROW_PARTS[listKey].map(([name]) => [name, toInput(name, row?.[name])])))
}

export function emptyRow(listKey) {
  return Object.fromEntries(ROW_PARTS[listKey].map(([name]) => [name, '']))
}

const FIRST_PART_MISSING = {
  tests: 'Please type the name of the test.',
  medicines: 'Please type the name of the medicine.',
  items: 'Please type what the item is.',
}

/**
 * Checks typed rows. A row left completely empty is dropped. Gives back { value } (the rows, every
 * part a trimmed string, to send) or { problems }: { <row's place on screen>: message }, or
 * { list: message } when a required list has no rows.
 */
export function checkRows(listKey, rows, { required }) {
  const parts = ROW_PARTS[listKey]
  const problems = {}
  const value = []
  rows.forEach((row, index) => {
    const clean = Object.fromEntries(parts.map(([name]) => [name, String(row?.[name] ?? '').trim()]))
    if (Object.values(clean).every((text) => text === '')) return
    if (clean[parts[0][0]] === '') {
      problems[index] = FIRST_PART_MISSING[listKey]
      return
    }
    if (listKey === 'items') {
      const amount = CHECKS.money(clean.amount)
      if (amount.problem) {
        problems[index] = amount.problem
        return
      }
      clean.amount = amount.value
    }
    value.push(clean)
  })
  if (Object.keys(problems).length > 0) return { problems }
  if (value.length === 0 && required) return { problems: { list: `Please add at least one ${rowWord(listKey)}.` } }
  return { value }
}
