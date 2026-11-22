import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { ApiError, MESSAGES } from '../api/client'
import { deleteDocument } from '../api/documents'
import { Button, ButtonLink } from '../components/Button'
import { FocusedTitle } from '../components/FocusedTitle'
import { Icon } from '../components/Icon'
import { Screen } from '../components/Screen'
import { FIELD_ORDER, fieldKind, fieldLabel, isBlank, showRow, showValue } from '../documents/fields'
import { canAddNewCopy, dateLine, formatDate, isExpired, isPdf, isWorking, newCopyState } from '../documents/status'
import { inSentence, isKnownType, typeLabel } from '../documents/types'
import { useDocument } from '../documents/useDocuments'

const BACK = { to: '/documents', label: 'My documents' }

function detailCount(document) {
  const count = Object.keys(document.fields ?? {}).length
  if (count === 0) return 'Please check the details before we save anything.'
  return `We found ${count} ${count === 1 ? 'detail' : 'details'}. Please check them before we save anything.`
}

/** The heading and the sentence under it, for each status (see api/documents.js). */
export function pageWords(document) {
  switch (newCopyState(document)) {
    case 'reading':
      return { heading: 'Reading the new copy', text: 'Reading it now. This can take a minute. Your old copy is still in use.' }
    case 'saving':
      return { heading: 'Saving the new copy', text: 'The details are checked. Saving them now.' }
    case 'check':
      return {
        heading: 'Ready for you to check',
        text: 'Please check the details of the new copy before we save it. Your old copy is still in use.',
      }
    default:
      break
  }
  switch (document.status) {
    case 'processing':
      return { heading: 'Reading your document', text: 'Reading it now. This can take a minute.' }
    case 'reviewed':
      return { heading: 'Saving your document', text: 'The details are checked. Saving them now.' }
    case 'needs_review':
      return { heading: 'Ready for you to check', text: detailCount(document) }
    case 'confirmed': {
      if (isExpired(document)) {
        return { heading: 'This document has ended', text: `It ended on ${formatDate(document.expiryDate)}.` }
      }
      const dates = dateLine(document)
      return { heading: 'Saved', text: `This document is saved and ready to use.${dates ? ` ${dates}.` : ''}` }
    }
    case 'needs_reupload':
      if (isPdf(document)) {
        return {
          heading: 'We could not read this PDF',
          text: 'Please add a clear PDF of 20 pages or fewer, or take a photo of the page instead.',
        }
      }
      return {
        heading: 'The photo was not clear enough',
        text: 'Please take a new photo: lay the paper flat, in good light, with the whole page showing.',
      }
    case 'needs_classification':
      return { heading: 'We could not tell what this is', text: 'Please add it again and choose what it is.' }
    case 'rejected':
      return { heading: 'This document was not kept', text: 'You can add it again with another photo or file.' }
    case 'failed_tool':
      return { heading: 'Something went wrong while reading it', text: 'Please add it again in a little while.' }
    default:
      return { heading: 'This document is not ready', text: 'Please add it again.' }
  }
}

const ADD_AGAIN = new Set(['needs_reupload', 'needs_classification', 'rejected', 'failed_tool'])

function addAgainPath(document) {
  return isKnownType(document.documentType) ? `/documents/add?type=${document.documentType}` : '/documents/add'
}

function subtitle(document) {
  if (document.entry !== 'upload') return `${typeLabel(document.documentType)}, typed in`
  return `${typeLabel(document.documentType)}, ${isPdf(document) ? 'PDF' : 'photo'}`
}

/** The saved details in the order they are checked, then any others; ID numbers come masked. */
function savedDetails(document) {
  const fields = document.fields ?? {}
  const order = FIELD_ORDER[document.documentType] ?? []
  const keys = [...order.filter((key) => key in fields), ...Object.keys(fields).filter((key) => !order.includes(key))]
  return keys.filter((key) => !isBlank(fields[key]?.value)).map((key) => ({ key, value: fields[key].value }))
}

function SavedDetails({ document }) {
  const details = savedDetails(document)
  if (details.length === 0) return null
  return (
    <section className="panel" aria-labelledby="saved-details">
      <h2 id="saved-details" className="panel__title">
        The details
      </h2>
      <dl className="details">
        {details.map(({ key, value }) => (
          <div key={key} className="details__item">
            <dt className="details__label">{fieldLabel(key)}</dt>
            <dd className="details__value">
              {fieldKind(key) === 'rows' && Array.isArray(value) ? (
                <ul className="summary__rows">
                  {value.map((row, index) => (
                    <li key={index}>{showRow(key, row)}</li>
                  ))}
                </ul>
              ) : (
                showValue(key, value)
              )}
            </dd>
          </div>
        ))}
      </dl>
      {document.entry === 'manual' && <p className="panel__hint">You typed these details in.</p>}
    </section>
  )
}

export function DocumentPage() {
  const { documentId } = useParams()
  const { document, problem, notFound, trouble, retry } = useDocument(documentId)
  // shown → ask (delete?) → deleted
  const [phase, setPhase] = useState('shown')
  const [deleting, setDeleting] = useState(false)
  const [deleteProblem, setDeleteProblem] = useState(null)

  if (phase === 'deleted') {
    return (
      <Screen title="Deleted" back={BACK}>
        <span className="outcome-icon outcome-icon--ready">
          <Icon name="check" size={40} strokeWidth={2.6} />
        </span>
        <FocusedTitle>Deleted</FocusedTitle>
        <p className="lead">The document and its photo are removed for good.</p>
        <ButtonLink to="/documents">Back to My documents</ButtonLink>
      </Screen>
    )
  }

  if (notFound) {
    return (
      <Screen title="Document not found" back={BACK}>
        <h1 className="page-title">We could not find this document</h1>
        <p className="lead">It may have been removed, or the link is old.</p>
        <ButtonLink to="/documents">Go to My documents</ButtonLink>
      </Screen>
    )
  }

  if (!document) {
    return (
      <Screen title="Document" back={BACK}>
        <h1 className="page-title">Your document</h1>
        {problem ? (
          <>
            <p className="alert" role="alert">
              We could not open this document. {problem}
            </p>
            <Button onClick={retry}>Try again</Button>
          </>
        ) : (
          <p className="lead" role="status">
            Opening your document…
          </p>
        )}
      </Screen>
    )
  }

  const name = inSentence(typeLabel(document.documentType))

  async function remove() {
    setDeleting(true)
    setDeleteProblem(null)
    try {
      await deleteDocument(document.documentId)
      setPhase('deleted')
    } catch (error) {
      setDeleteProblem(error instanceof ApiError ? error.message : MESSAGES.serverProblem)
    } finally {
      setDeleting(false)
    }
  }

  if (phase === 'ask') {
    return (
      <Screen title="Delete this document?" back={BACK}>
        <FocusedTitle>Delete this document?</FocusedTitle>
        <p className="lead">
          Your {name} and its photo will be removed for good, with every copy of it. This cannot be undone.
        </p>
        {deleteProblem && (
          <p className="alert" role="alert">
            {deleteProblem}
          </p>
        )}
        <div className="actions">
          <Button className="button--danger" onClick={remove} disabled={deleting}>
            {deleting ? 'Deleting…' : 'Yes, delete it'}
          </Button>
          <Button variant="secondary" onClick={() => setPhase('shown')} disabled={deleting}>
            No, keep it
          </Button>
        </div>
      </Screen>
    )
  }

  const words = pageWords(document)
  const working = isWorking(document)
  const copy = newCopyState(document)
  const expired = isExpired(document)
  const newCopyPath = `/documents/${document.documentId}/new-copy`

  return (
    <Screen title={words.heading} back={BACK}>
      <div className="intro">
        <h1 className="page-title">{words.heading}</h1>
        <p className="lead">{subtitle(document)}</p>
      </div>

      {/* Stays in place while the status changes, so screen readers say the new sentence. */}
      <p className={working ? 'status-box status-box--working' : 'status-box'} role="status">
        {working && <span className="status-box__dot" aria-hidden="true" />}
        <span>{words.text}</span>
      </p>

      {copy === 'refused' && (
        <p className="notice">The new copy could not be read, so it was not kept. Your old copy is still in use.</p>
      )}

      {working && (
        <>
          <section className="panel" aria-labelledby="what-happens">
            <h2 id="what-happens" className="panel__title">
              What happens now
            </h2>
            <ul className="steps">
              <li>We check the file is safe.</li>
              <li>We read the words and find the details.</li>
              <li>We hide ID numbers, except the last 4 digits.</li>
              <li>If anything is unclear, we ask you to check it.</li>
            </ul>
          </section>
          <div className="note">
            <span className="note__icon">
              <Icon name="clock" />
            </span>
            <p>You can leave this screen. We keep reading, and My documents shows when it is ready.</p>
          </div>
        </>
      )}
      {working && trouble && <p className="notice">We could not check just now. We will try again in a moment.</p>}

      {document.status === 'needs_review' && (
        <ButtonLink to={`/documents/${document.documentId}/review`}>Check the details</ButtonLink>
      )}
      {copy === 'check' && (
        <ButtonLink to={`/documents/${document.documentId}/review?version=${document.replacement.version}`}>
          Check the new copy
        </ButtonLink>
      )}
      {ADD_AGAIN.has(document.status) && (
        <ButtonLink to={addAgainPath(document)} icon="plus">
          Add it again
        </ButtonLink>
      )}
      {canAddNewCopy(document) && expired && (
        <ButtonLink to={newCopyPath} icon="plus">
          Add the new copy
        </ButtonLink>
      )}

      {document.status === 'confirmed' && <SavedDetails document={document} />}
      {document.status === 'confirmed' && document.documentType === 'lab_report' && (
        <ButtonLink to={`/doctor/lab?report=${encodeURIComponent(document.documentId)}`} icon="stethoscope">
          Find a doctor for this report
        </ButtonLink>
      )}

      {canAddNewCopy(document) && !expired && (
        <ButtonLink to={newCopyPath} variant="secondary" icon="plus">
          {copy === 'refused' ? 'Add the new copy again' : 'Got a renewed one? Add the new copy'}
        </ButtonLink>
      )}
      {!working && (
        <Button
          variant="quiet"
          className="button--muted"
          onClick={() => {
            setDeleteProblem(null)
            setPhase('ask')
          }}
        >
          Delete this document
        </Button>
      )}
    </Screen>
  )
}
