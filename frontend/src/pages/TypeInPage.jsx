import { useRef, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { ApiError, MESSAGES } from '../api/client'
import { createManualDocument, replaceManually } from '../api/documents'
import { Button, ButtonLink } from '../components/Button'
import { FocusedTitle } from '../components/FocusedTitle'
import { Icon } from '../components/Icon'
import { Screen } from '../components/Screen'
import { DetailBox, RowsEditor, rowProblem } from '../documents/DetailStep'
import { rowWord } from '../documents/fields'
import { checkTypeIn, emptyTypeIn, typeInFields, typeInRefusal } from '../documents/typeIn'
import { DOCUMENT_TYPES, inSentence, isKnownType, typeLabel } from '../documents/types'
import { NewCopyGate } from './NewCopyPage'

const BACK = { to: '/documents', label: 'My documents' }
const ID_FIELDS = new Set(['aadhaar_number', 'pan_number', 'account_number', 'policy_number'])

/** /documents/type-in(?type=…): a new document typed in, with no photo. */
export function TypeInPage() {
  const [params] = useSearchParams()
  const [documentType, setDocumentType] = useState(() => (isKnownType(params.get('type')) ? params.get('type') : null))

  if (!documentType) {
    return (
      <Screen title="Type the details in" back={BACK}>
        <h1 className="page-title">Type the details in</h1>
        <p className="lead">No photo? You can type the details from the paper yourself.</p>
        <section className="panel" aria-labelledby="which-paper">
          <h2 id="which-paper" className="panel__title">
            What is it?
          </h2>
          <div className="type-choices" role="group" aria-labelledby="which-paper">
            {DOCUMENT_TYPES.map((type) => (
              <button key={type.key} type="button" className="type-choice" onClick={() => setDocumentType(type.key)}>
                {type.choice ?? type.label}
              </button>
            ))}
          </div>
        </section>
      </Screen>
    )
  }
  return (
    <TypeInForm
      key={documentType}
      documentType={documentType}
      back={BACK}
      send={(fields) => createManualDocument(documentType, fields)}
      onChangeType={() => setDocumentType(null)}
    />
  )
}

/** /documents/:documentId/new-copy/type-in: the new copy of a saved document, typed in. */
export function NewCopyTypeInPage() {
  const { documentId } = useParams()
  return (
    <NewCopyGate documentId={documentId} title="Type the new details in">
      {(document) => (
        <TypeInForm
          documentType={document.documentType}
          renewing
          back={{ to: `/documents/${documentId}`, label: typeLabel(document.documentType) }}
          send={(fields) => replaceManually(documentId, fields)}
        />
      )}
    </NewCopyGate>
  )
}

function TypeInForm({ documentType, renewing = false, back, send, onChangeType }) {
  const name = typeLabel(documentType)
  const fields = typeInFields(documentType)
  const [values, setValues] = useState(() => emptyTypeIn(documentType))
  const [problems, setProblems] = useState({})
  const [rows, setRows] = useState({})
  const [message, setMessage] = useState(null)
  const [sending, setSending] = useState(false)
  const [saved, setSaved] = useState(null)
  const boxes = useRef({})
  const alertRef = useRef(null)

  function change(key, value) {
    setValues((now) => ({ ...now, [key]: typeof value === 'function' ? value(now[key]) : value }))
    setProblems((now) => ({ ...now, [key]: undefined }))
    setRows((now) => ({ ...now, [key]: undefined }))
  }

  /** Marks what to fix, and moves to the first one (a list's problem is read out from the alert). */
  function show(found) {
    const clean = (map) => Object.fromEntries(Object.entries(map).filter(([, value]) => value))
    setProblems(clean(found.problems))
    setRows(clean(found.rows))
    setMessage(found.message)
    const first = fields.find((field) => found.problems[field.key] || found.rows[field.key])
    if (first && found.problems[first.key] && boxes.current[first.key]) boxes.current[first.key].focus()
    else alertRef.current?.focus()
  }

  async function submit(event) {
    event.preventDefault()
    if (sending) return
    const checked = checkTypeIn(documentType, values)
    if (!checked.ok) {
      show({ ...checked, message: 'Please check the details marked below.' })
      return
    }
    setSending(true)
    setMessage(null)
    try {
      const document = await send(checked.fields)
      setSaved({ document, hasId: Object.keys(checked.fields).some((key) => ID_FIELDS.has(key)) })
    } catch (error) {
      if (error instanceof ApiError && error.status === 422 && Array.isArray(error.details?.errors)) {
        show(typeInRefusal(error.details.errors, checked.places))
      } else {
        setMessage(error instanceof ApiError ? error.message : MESSAGES.serverProblem)
        alertRef.current?.focus()
      }
      setSending(false)
    }
  }

  if (saved) {
    return (
      <Screen title="Saved" back={BACK}>
        <span className="outcome-icon outcome-icon--ready">
          <Icon name="check" size={40} strokeWidth={2.6} />
        </span>
        <FocusedTitle>Saved</FocusedTitle>
        <p className="lead">
          {renewing
            ? `The new copy of your ${inSentence(name)} is saved. It is the one in use now.`
            : `Your ${inSentence(name)} is now in My documents. Only you can open it.`}
          {saved.hasId && ' ID numbers are hidden now; we show just the last 4 digits.'}
        </p>
        <ButtonLink to={`/documents/${saved.document.documentId}`}>See the document</ButtonLink>
        <ButtonLink to="/documents" variant="secondary">
          Back to My documents
        </ButtonLink>
      </Screen>
    )
  }

  const title = renewing ? 'Type the new details in' : 'Type the details in'
  return (
    <Screen title={title} back={back}>
      <div className="intro">
        <h1 className="page-title">{title}</h1>
        <p className="lead">{renewing ? `The new copy of your ${inSentence(name)}` : name}</p>
      </div>
      {onChangeType && (
        <Button variant="quiet" onClick={onChangeType}>
          Not a {inSentence(name)}? Choose again
        </Button>
      )}
      <div className="note">
        <span className="note__icon">
          <Icon name="lock" />
        </span>
        <p>
          Type each detail as it is printed on the paper. Typing them in counts as checking them, so the document is
          saved straight away. ID numbers are kept hidden; we show just the last 4 digits.
        </p>
      </div>

      <form className="form" onSubmit={submit} noValidate>
        {fields.map((field) => {
          const label = field.required ? field.label : `${field.label} (if it is on the paper)`
          if (field.kind === 'rows') {
            const word = rowWord(field.key)
            const Word = word.charAt(0).toUpperCase() + word.slice(1)
            const listProblem = rows[field.key] ? rowProblem(rows[field.key], Word) : null
            return (
              <fieldset key={field.key} className="panel type-in__list">
                <legend className="panel__title">{label}</legend>
                <RowsEditor
                  listKey={field.key}
                  idPrefix={`type-in-${field.key}`}
                  rows={values[field.key]}
                  problems={rows[field.key] ?? {}}
                  onRows={(update) => change(field.key, update)}
                  onType={(index, part, text) =>
                    change(field.key, (now) => now.map((row, at) => (at === index ? { ...row, [part]: text } : row)))
                  }
                />
                {listProblem && <p className="field__error">{listProblem}</p>}
              </fieldset>
            )
          }
          return (
            <DetailBox
              key={field.key}
              id={`type-in-${field.key}`}
              fieldKey={field.key}
              kind={field.kind}
              label={label}
              value={values[field.key]}
              onChange={(text) => change(field.key, text)}
              error={problems[field.key]}
              inputRef={(element) => {
                boxes.current[field.key] = element
              }}
            />
          )
        })}

        <p ref={alertRef} tabIndex={-1} className={message ? 'alert' : 'visually-hidden'} role="alert">
          {message}
        </p>
        <Button type="submit" disabled={sending}>
          {sending ? 'Saving…' : 'Save to my documents'}
        </Button>
      </form>
    </Screen>
  )
}
