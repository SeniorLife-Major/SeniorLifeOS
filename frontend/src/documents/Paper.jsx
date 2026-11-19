import { useEffect, useState } from 'react'
import { getDocumentFile } from '../api/documents'
import { Icon } from '../components/Icon'

/**
 * The document's photo or PDF, to compare the details with. The file needs the sign-in token, so
 * it is fetched here and shown from a temporary blob: address, which is let go when the screen
 * closes. Gives back { status: 'loading' | 'ready' | 'failed', url, isPdf }.
 */
export function usePaper(documentId) {
  const [paper, setPaper] = useState({ status: 'loading', url: null, isPdf: false })

  useEffect(() => {
    const controller = new AbortController()
    let url = null
    setPaper({ status: 'loading', url: null, isPdf: false })
    getDocumentFile(documentId, controller.signal).then(
      (blob) => {
        if (controller.signal.aborted) return
        if (typeof URL.createObjectURL !== 'function') {
          setPaper({ status: 'failed', url: null, isPdf: false })
          return
        }
        url = URL.createObjectURL(blob)
        setPaper({ status: 'ready', url, isPdf: blob.type === 'application/pdf' })
      },
      () => {
        if (!controller.signal.aborted) setPaper({ status: 'failed', url: null, isPdf: false })
      },
    )
    return () => {
      controller.abort()
      if (url) URL.revokeObjectURL(url)
    }
  }, [documentId])

  return paper
}

export function Paper({ paper }) {
  if (paper.status === 'loading') {
    return (
      <p className="paper paper--note" role="status">
        Loading the photo…
      </p>
    )
  }
  if (paper.status === 'failed') {
    return (
      <p className="paper paper--note">
        The photo could not be shown here. You can still check the details against the paper itself.
      </p>
    )
  }
  if (paper.isPdf) {
    return (
      <div className="paper paper--note">
        <Icon name="document" size={28} />
        <a className="small-button" href={paper.url} target="_blank" rel="noreferrer">
          Open the PDF
        </a>
      </div>
    )
  }
  return (
    <figure className="paper">
      <img className="paper__image" src={paper.url} alt="Your document" />
      <a className="paper__bigger" href={paper.url} target="_blank" rel="noreferrer">
        See it bigger
      </a>
    </figure>
  )
}
