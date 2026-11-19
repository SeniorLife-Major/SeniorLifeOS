import { useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ApiError, MESSAGES } from '../api/client'
import { replaceDocument, uploadDocument } from '../api/documents'
import { Button, ButtonLink } from '../components/Button'
import { Icon } from '../components/Icon'
import { Screen } from '../components/Screen'
import { Switch } from '../components/Switch'
import { checkPicked, FileProblem, prepareUpload } from '../documents/prepareFile'
import { DOCUMENT_TYPES, inSentence, isKnownType, MEDICAL_TYPES, typeLabel } from '../documents/types'
import { useWait } from '../hooks/useWait'
import { useProfile } from '../profile/ProfileProvider'
import { ProfileStatus } from '../profile/ProfileStatus'

const BACK = { to: '/documents', label: 'My documents' }
const UNKNOWN = 'unknown' // "I don't know": the backend works out the type
const CHOICES = [...DOCUMENT_TYPES, { key: UNKNOWN, label: "I don't know" }]

/**
 * /documents/add: a new document. With `renew` (a saved document, from NewCopyPage) it adds the
 * new copy of that document instead: its kind is already known, and the old copy stays in use
 * until the new one is checked.
 */
export function AddDocumentPage({ renew = null }) {
  const { profile } = useProfile()
  const title = renew ? 'Add the new copy' : 'Add a document'
  const back = renew ? { to: `/documents/${renew.documentId}`, label: typeLabel(renew.documentType) } : BACK
  if (!profile) {
    return (
      <Screen title={title} back={back}>
        <h1 className="page-title">{title}</h1>
        <ProfileStatus />
      </Screen>
    )
  }
  // The choices in Settings are where this screen starts.
  return <AddDocumentForm consents={profile.consents} renew={renew} title={title} back={back} />
}

function AddDocumentForm({ consents, renew, title, back }) {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  // "Add it again" opens this screen with ?type=… so the person does not have to choose again.
  const [picked, setPicked] = useState(() => {
    if (renew) return renew.documentType
    return isKnownType(params.get('type')) ? params.get('type') : null
  })
  const [file, setFile] = useState(null)
  const [fileProblem, setFileProblem] = useState(null)
  const [cloud, setCloud] = useState(consents.cloudOcr)
  const [medicalOk, setMedicalOk] = useState(consents.medicalCloud)
  const [sending, setSending] = useState(false)
  const [problem, setProblem] = useState(null)
  const { waiting, waitFor } = useWait()
  const cameraInput = useRef(null)
  const fileInput = useRef(null)

  const medical = MEDICAL_TYPES.has(picked)
  // The health-paper question matters only when something may go to the cloud at all.
  const askMedical = medical && (cloud || consents.useLlm)

  function choose(event) {
    const chosen = event.target.files?.[0]
    event.target.value = '' // so picking the same file again still counts
    if (!chosen) return
    const trouble = checkPicked(chosen)
    setProblem(null)
    setFileProblem(trouble)
    setFile(trouble ? null : chosen)
  }

  async function send(event) {
    event.preventDefault()
    if (!picked || !file || sending || waiting) return
    setSending(true)
    setProblem(null)
    try {
      const ready = await prepareUpload(file)
      const chosen = {
        cloudOcr: cloud,
        // For "I don't know" the reader may find a health paper: the choice in Settings counts.
        medicalCloud: medical ? medicalOk : consents.medicalCloud,
        useLlm: consents.useLlm,
      }
      const document = renew
        ? await replaceDocument(renew.documentId, ready, { consents: chosen })
        : await uploadDocument(ready, { documentType: picked === UNKNOWN ? null : picked, consents: chosen })
      navigate(`/documents/${document.documentId}`, { replace: true })
    } catch (error) {
      if (error instanceof FileProblem) {
        setFileProblem(error.message)
        setFile(null)
      } else if (error instanceof ApiError) {
        setProblem(error.message)
        if (error.retryAfterSeconds !== undefined) waitFor(error.retryAfterSeconds)
      } else {
        setProblem(MESSAGES.serverProblem)
      }
      setSending(false)
    }
  }

  let buttonText = 'Upload'
  if (sending) buttonText = 'Sending…'
  else if (waiting) buttonText = 'Please wait'
  else if (!picked) buttonText = 'Choose what it is first'
  else if (!file) buttonText = 'Add a photo or PDF first'

  return (
    <Screen title={title} back={back}>
      {renew ? (
        <div className="intro">
          <h1 className="page-title">{title}</h1>
          <p className="lead">
            Your {inSentence(typeLabel(renew.documentType))}. The one you have now stays in use until you check the new
            one.
          </p>
        </div>
      ) : (
        <h1 className="page-title">{title}</h1>
      )}

      <form className="form" onSubmit={send} noValidate>
        {!renew && (
          <section className="panel" aria-labelledby="what-is-it">
            <h2 id="what-is-it" className="panel__title">
              1. What is it?
            </h2>
            <div className="type-choices" role="group" aria-labelledby="what-is-it">
              {CHOICES.map((type) => (
                <button
                  key={type.key}
                  type="button"
                  className="type-choice"
                  aria-pressed={picked === type.key}
                  onClick={() => setPicked(type.key)}
                >
                  {type.choice ?? type.label}
                </button>
              ))}
            </div>
          </section>
        )}

        <section className="panel" aria-labelledby="add-file">
          <h2 id="add-file" className="panel__title">
            {renew ? 'Add a photo or a PDF of the new copy' : '2. Add a photo or a PDF'}
          </h2>
          <div className="pick-buttons">
            <Button variant="secondary" icon="camera" onClick={() => cameraInput.current?.click()}>
              Take a photo
            </Button>
            <Button variant="secondary" icon="folder" onClick={() => fileInput.current?.click()}>
              Choose a file
            </Button>
          </div>
          <input
            ref={cameraInput}
            type="file"
            accept="image/*"
            capture="environment"
            hidden
            aria-label="Photo from the camera"
            onChange={choose}
          />
          <input
            ref={fileInput}
            type="file"
            accept="image/*,application/pdf"
            hidden
            aria-label="Photo or PDF from this phone"
            onChange={choose}
          />
          <p className="picked" role="status">
            {file && (
              <>
                <Icon name="check" strokeWidth={2.8} />
                <span>
                  {file.type === 'application/pdf' ? 'PDF added' : 'Photo added'}: {file.name}
                </span>
              </>
            )}
          </p>
          {fileProblem && (
            <p className="alert" role="alert">
              {fileProblem}
            </p>
          )}
          <p className="panel__hint">One photo of the page, or a PDF of up to 20 pages. Lay the paper flat in good light.</p>
        </section>

        <section className="panel panel--list" aria-label="Help with reading">
          <Switch
            id="send-cloud"
            label="Let a cloud service read it more carefully"
            hint="Before it is sent, we black out the ID numbers, faces and QR codes we can find."
            checked={cloud}
            onChange={setCloud}
          />
          {askMedical && (
            <Switch
              id="send-medical"
              label="It is a health paper. May it go to the cloud too?"
              hint="Health papers need a second yes from you."
              checked={medicalOk}
              onChange={setMedicalOk}
            />
          )}
        </section>
        <p className="panel__hint">
          These choices are for this document only; your usual ones are in Settings. If anything is unclear, we ask
          you to check it before it is saved.
        </p>

        {problem && (
          <p className="alert" role="alert">
            {problem}
          </p>
        )}
        <Button type="submit" disabled={!picked || !file || sending || waiting}>
          {buttonText}
        </Button>
      </form>
      <ButtonLink to={typeInPath(renew, picked)} variant="quiet">
        {renew ? 'No photo? Type the new details in' : 'No photo? Type the details in'}
      </ButtonLink>
    </Screen>
  )
}

/** Where "No photo? Type the details in" goes: the kind already chosen comes along. */
function typeInPath(renew, picked) {
  if (renew) return `/documents/${renew.documentId}/new-copy/type-in`
  return isKnownType(picked) ? `/documents/type-in?type=${picked}` : '/documents/type-in'
}
