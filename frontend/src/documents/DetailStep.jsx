import { useEffect, useRef, useState } from 'react'
import { Button } from '../components/Button'
import { Icon } from '../components/Icon'
import { TextField } from '../components/Field'
import {
  checkRows,
  checkTyped,
  emptyRow,
  mustBePast,
  rowWord,
  ROW_PARTS,
  showRow,
  showValue,
  STATES,
  TEXT_MAX,
  toInput,
  toInputRows,
} from './fields'
import { answerFor } from './reviewSteps'
import { todayISO } from './status'

// One detail on the review screen. The parent gives each step its own `key`, so a new detail
// starts fresh. onAnswer(answer) moves on (see reviewSteps.js for the answers).

const ASK = {
  missing: 'We could not find this on the paper. Please type it as printed.',
  optional: 'If this is on the paper, you may add it.',
  read: 'Type it as printed on the paper.',
}

// What the phone's keyboard should offer for each kind of detail.
const KEYBOARD = {
  amount: { inputMode: 'numeric' },
  money: { inputMode: 'decimal' },
  percent: { inputMode: 'numeric' },
  aadhaar: { inputMode: 'numeric', autoComplete: 'off' },
  account: { inputMode: 'numeric', autoComplete: 'off' },
  pan: { autoCapitalize: 'characters', autoComplete: 'off' },
  ifsc: { autoCapitalize: 'characters', autoComplete: 'off' },
  policy: { autoCapitalize: 'characters', autoComplete: 'off' },
}

/** The step's heading takes the focus, so a screen reader starts reading at the new detail. */
function useFocusFirst() {
  const ref = useRef(null)
  useEffect(() => {
    ref.current?.focus()
  }, [])
  return ref
}

function Notes({ notes }) {
  if (notes.length === 0) return null
  return (
    <ul className="detail__notes">
      {notes.map((note) => (
        <li key={note}>
          <Icon name="alertCircle" size={22} />
          <span>{note}</span>
        </li>
      ))}
    </ul>
  )
}

function Header({ step, position, headingRef }) {
  return (
    <div className="detail__top">
      <h2 id={`detail-${step.key}-title`} ref={headingRef} tabIndex={-1} className="panel__title">
        {step.label}
      </h2>
      <span className="detail__position">{position}</span>
    </div>
  )
}

function Box({ step, value, onChange, error, inputRef }) {
  const id = `detail-${step.key}`
  const label = ASK[step.state]
  if (step.kind === 'state') {
    return (
      <div className="field">
        <label htmlFor={id} className="field__label">
          {label}
        </label>
        <select
          id={id}
          ref={inputRef}
          className="input"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : undefined}
        >
          <option value="">Choose the state</option>
          {STATES.map(([code, name]) => (
            <option key={code} value={code}>
              {name}
            </option>
          ))}
        </select>
        {error && (
          <p id={`${id}-error`} className="field__error">
            {error}
          </p>
        )}
      </div>
    )
  }
  const extra =
    step.kind === 'date'
      ? { type: 'date', max: mustBePast(step.key) ? todayISO() : undefined }
      : { type: 'text', maxLength: TEXT_MAX }
  return (
    <TextField
      id={id}
      ref={inputRef}
      label={label}
      error={error}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      {...extra}
      {...KEYBOARD[step.kind]}
    />
  )
}

/** A single detail: a name, a date, an amount, a number… */
export function DetailStep({ step, answer, position, onAnswer }) {
  const headingRef = useFocusFirst()
  const inputRef = useRef(null)
  const typed = answer?.status === 'changed' || answer?.status === 'added'
  const [editing, setEditing] = useState(step.state !== 'read')
  const [draft, setDraft] = useState(typed ? answer.value : toInput(step.key, step.original))
  const [problem, setProblem] = useState(null)

  function save() {
    const result = checkTyped(step.key, draft, { required: step.required })
    if (result.problem) {
      setProblem(result.problem)
      inputRef.current?.focus()
      return
    }
    onAnswer(answerFor(step, result.value))
  }

  function cancel() {
    setDraft(typed ? answer.value : toInput(step.key, step.original))
    setProblem(null)
    setEditing(false)
  }

  const shown = typed ? showValue(step.key, answer.value) : showValue(step.key, step.original)

  return (
    <section className="panel detail" aria-labelledby={`detail-${step.key}-title`}>
      <Header step={step} position={position} headingRef={headingRef} />
      {!editing && (
        <>
          <p className="detail__value">{shown || 'Removed'}</p>
          {answer?.status === 'changed' && <p className="detail__mark">You changed this</p>}
          <Notes notes={step.notes} />
          <div className="actions">
            <Button icon="check" onClick={() => onAnswer(typed ? answer : { status: 'accepted' })}>
              Yes, this is right
            </Button>
            <Button variant="secondary" onClick={() => setEditing(true)}>
              Change it
            </Button>
          </div>
        </>
      )}
      {editing && (
        <>
          {step.state === 'read' && <p className="detail__was">Read as: {showValue(step.key, step.original)}</p>}
          <Notes notes={step.notes} />
          <Box
            step={step}
            value={draft}
            onChange={(text) => {
              setDraft(text)
              setProblem(null)
            }}
            error={problem}
            inputRef={inputRef}
          />
          <div className="actions actions--row">
            {step.state === 'read' && (
              <Button variant="secondary" onClick={cancel}>
                Cancel
              </Button>
            )}
            {step.state === 'optional' && (
              <Button variant="secondary" onClick={() => onAnswer({ status: 'skipped' })}>
                Skip
              </Button>
            )}
            <Button onClick={save}>{step.state === 'optional' ? 'Add' : 'Save'}</Button>
          </div>
        </>
      )}
    </section>
  )
}

/** One sentence for the rows to fix, said out loud: the list's own problem, or the first row's. */
function rowProblem(problems, Word) {
  if (problems.list) return problems.list
  const first = Object.keys(problems).map(Number).sort((a, b) => a - b)[0]
  return first === undefined ? null : `${Word} ${first + 1}: ${problems[first]}`
}

/** A list: lab tests, medicines or bill items. Every row is shown; changing them edits the whole list. */
export function RowsStep({ step, answer, position, onAnswer }) {
  const headingRef = useFocusFirst()
  const word = rowWord(step.key)
  const Word = word.charAt(0).toUpperCase() + word.slice(1)
  const typed = answer?.status === 'changed' || answer?.status === 'added'
  const startRows = () => {
    const rows = typed ? answer.value : toInputRows(step.key, step.original)
    return rows.length > 0 ? rows.map((row) => ({ ...row })) : [emptyRow(step.key)]
  }
  const [editing, setEditing] = useState(step.state !== 'read')
  const [rows, setRows] = useState(startRows)
  const [problems, setProblems] = useState({})

  function change(index, name, text) {
    setRows((now) => now.map((row, at) => (at === index ? { ...row, [name]: text } : row)))
    setProblems({})
  }

  function save() {
    const result = checkRows(step.key, rows, { required: step.required })
    if (result.problems) {
      setProblems(result.problems)
      return
    }
    onAnswer(answerFor(step, result.value))
  }

  const shownRows = typed ? answer.value : (step.original ?? [])

  return (
    <section className="panel detail" aria-labelledby={`detail-${step.key}-title`}>
      <Header step={step} position={position} headingRef={headingRef} />
      {!editing && (
        <>
          <ul className="detail__rows">
            {shownRows.map((row, index) => (
              <li key={index}>{showRow(step.key, row)}</li>
            ))}
          </ul>
          {answer?.status === 'changed' && <p className="detail__mark">You changed these</p>}
          <Notes notes={step.notes} />
          <div className="actions">
            <Button icon="check" onClick={() => onAnswer(typed ? answer : { status: 'accepted' })}>
              Yes, these are right
            </Button>
            <Button variant="secondary" onClick={() => setEditing(true)}>
              Change them
            </Button>
          </div>
        </>
      )}
      {editing && (
        <>
          <p className="detail__was">{ASK[step.state === 'read' ? 'read' : step.state]}</p>
          <Notes notes={step.notes} />
          {rows.map((row, index) => (
            <fieldset key={index} className="row-edit">
              <legend className="row-edit__title">
                {Word} {index + 1}
              </legend>
              {ROW_PARTS[step.key].map(([name, label]) => (
                <TextField
                  key={name}
                  id={`${step.key}-${index}-${name}`}
                  label={label}
                  value={row[name]}
                  maxLength={TEXT_MAX}
                  inputMode={name === 'amount' ? 'decimal' : undefined}
                  onChange={(event) => change(index, name, event.target.value)}
                />
              ))}
              {problems[index] && <p className="field__error">{problems[index]}</p>}
              <Button
                variant="quiet"
                className="button--muted"
                onClick={() => setRows((now) => now.filter((_, at) => at !== index))}
              >
                Remove {word} {index + 1}
              </Button>
            </fieldset>
          ))}
          <Button variant="secondary" icon="plus" onClick={() => setRows((now) => [...now, emptyRow(step.key)])}>
            Add a {word}
          </Button>
          {rowProblem(problems, Word) && (
            <p className="alert" role="alert">
              {rowProblem(problems, Word)}
            </p>
          )}
          <div className="actions actions--row">
            {step.state === 'read' && (
              <Button
                variant="secondary"
                onClick={() => {
                  setRows(startRows())
                  setProblems({})
                  setEditing(false)
                }}
              >
                Cancel
              </Button>
            )}
            {step.state === 'optional' && (
              <Button variant="secondary" onClick={() => onAnswer({ status: 'skipped' })}>
                Skip
              </Button>
            )}
            <Button onClick={save}>{step.state === 'optional' ? 'Add' : 'Save'}</Button>
          </div>
        </>
      )}
    </section>
  )
}
