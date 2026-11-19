import { useParams } from 'react-router-dom'
import { Button, ButtonLink } from '../components/Button'
import { Icon } from '../components/Icon'
import { Screen } from '../components/Screen'
import { dateLine, formatDate, isExpired, isPdf, isWorking } from '../documents/status'
import { isKnownType, typeLabel } from '../documents/types'
import { useDocument } from '../documents/useDocuments'

const BACK = { to: '/documents', label: 'My documents' }

function detailCount(document) {
  const count = Object.keys(document.fields ?? {}).length
  if (count === 0) return 'Please check the details before we save anything.'
  return `We found ${count} ${count === 1 ? 'detail' : 'details'}. Please check them before we save anything.`
}

/** The heading and the sentence under it, for each status (see api/documents.js). */
export function pageWords(document) {
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
  if (document.entry !== 'upload') return typeLabel(document.documentType)
  return `${typeLabel(document.documentType)}, ${isPdf(document) ? 'PDF' : 'photo'}`
}

export function DocumentPage() {
  const { documentId } = useParams()
  const { document, problem, notFound, trouble, retry } = useDocument(documentId)

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

  const words = pageWords(document)
  const working = isWorking(document)

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
      {ADD_AGAIN.has(document.status) && (
        <ButtonLink to={addAgainPath(document)} icon="plus">
          Add it again
        </ButtonLink>
      )}
    </Screen>
  )
}
