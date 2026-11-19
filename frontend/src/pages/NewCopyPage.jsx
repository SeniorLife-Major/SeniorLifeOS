import { useParams } from 'react-router-dom'
import { Button, ButtonLink } from '../components/Button'
import { Screen } from '../components/Screen'
import { canAddNewCopy, newCopyState } from '../documents/status'
import { typeLabel } from '../documents/types'
import { useDocument } from '../documents/useDocuments'
import { AddDocumentPage } from './AddDocumentPage'

const BACK = { to: '/documents', label: 'My documents' }

/**
 * Loads the document a new copy is for, and shows children(document) only if a new copy can be
 * added now (canAddNewCopy); otherwise says why.
 */
export function NewCopyGate({ documentId, title, children }) {
  const { document, problem, notFound, retry } = useDocument(documentId)

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
      <Screen title={title} back={BACK}>
        <h1 className="page-title">{title}</h1>
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
  if (!canAddNewCopy(document)) {
    const waiting = newCopyState(document) !== null
    return (
      <Screen title={title} back={{ to: `/documents/${documentId}`, label: typeLabel(document.documentType) }}>
        <h1 className="page-title">A new copy can&apos;t be added now</h1>
        <p className="lead">
          {waiting
            ? 'A new copy is already being read or is waiting for your check. Please finish that one first.'
            : 'Only a saved document can get a new copy. You can add this one again as a new document.'}
        </p>
        <ButtonLink to={`/documents/${documentId}`}>See the document</ButtonLink>
      </Screen>
    )
  }
  return children(document)
}

/** /documents/:documentId/new-copy: a renewed paper, as a photo or PDF; the old copy stays in use until it is checked. */
export function NewCopyPage() {
  const { documentId } = useParams()
  return (
    <NewCopyGate documentId={documentId} title="Add the new copy">
      {(document) => <AddDocumentPage renew={document} />}
    </NewCopyGate>
  )
}
