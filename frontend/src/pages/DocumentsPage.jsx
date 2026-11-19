import { Link } from 'react-router-dom'
import { Button, ButtonLink } from '../components/Button'
import { Icon } from '../components/Icon'
import { Screen } from '../components/Screen'
import { inAttentionOrder, statusOf, todayISO } from '../documents/status'
import { StatusChip } from '../documents/StatusChip'
import { typeLabel } from '../documents/types'
import { useDocumentList } from '../documents/useDocuments'

function DocumentCard({ document, today }) {
  const status = statusOf(document, today)
  return (
    <li>
      <Link to={`/documents/${document.documentId}`} className="doc-card">
        <span className="doc-card__text">
          <span className="doc-card__title">{typeLabel(document.documentType)}</span>
          <StatusChip status={status} />
          <span className="doc-card__detail">{status.detail}</span>
        </span>
        <Icon name="chevronRight" size={28} />
      </Link>
    </li>
  )
}

export function DocumentsPage() {
  const { documents, problem, trouble, retry } = useDocumentList()
  const today = todayISO()

  let list
  if (problem) {
    list = (
      <>
        <p className="alert" role="alert">
          We could not open your documents. {problem}
        </p>
        <Button onClick={retry}>Try again</Button>
      </>
    )
  } else if (!documents) {
    list = (
      <p className="lead" role="status">
        Opening your documents…
      </p>
    )
  } else if (documents.length === 0) {
    list = <p className="lead">No documents yet. Add your first one: a photo of the paper, or a PDF.</p>
  } else {
    list = (
      <ul className="doc-list" aria-label="My documents">
        {inAttentionOrder(documents, today).map((document) => (
          <DocumentCard key={document.documentId} document={document} today={today} />
        ))}
      </ul>
    )
  }

  return (
    <Screen title="My documents" back={{ to: '/', label: 'Home' }}>
      <h1 className="page-title">My documents</h1>
      <ButtonLink to="/documents/add" icon="plus">
        Add a document
      </ButtonLink>
      <ButtonLink to="/documents/checklist" variant="secondary" icon="check">
        Do I have what I need?
      </ButtonLink>
      {trouble && <p className="notice">We could not check for news just now. We will try again in a moment.</p>}
      {list}
      <div className="note">
        <span className="note__icon">
          <Icon name="lock" />
        </span>
        <p>Only you can open these. ID numbers are kept hidden; we show just the last 4 digits.</p>
      </div>
    </Screen>
  )
}
