import { Link } from 'react-router-dom'
import { formatDate } from '../documents/status'
import { Icon } from '../components/Icon'
import { labFlagWord, unjudgedWords } from './search'

/** "Please see a doctor today": a result on the lab report is far outside the normal range. */
export function SeeToday({ labReport }) {
  if (!labReport?.seeToday) return null
  return (
    <p className="alert see-today" role="alert">
      <Icon name="alertCircle" size={28} />
      <span>Some results are far outside the normal range. Please see a doctor today.</span>
    </p>
  )
}

/**
 * For a search from a lab report: why this kind of doctor, and each result outside its range by
 * name with high or low (the backend sends no values). Nothing for a search from words.
 */
export function LabReportPanel({ labReport }) {
  if (!labReport) return null
  const findings = labReport.findings ?? []
  const date = formatDate(labReport.reportDate)
  const unjudged = unjudgedWords(labReport.unjudged)
  const from = [labReport.labName, date].filter(Boolean).join(', ')
  return (
    <section className="panel lab-panel" aria-labelledby="from-lab-report">
      <h2 id="from-lab-report" className="panel__title">
        From your lab report
      </h2>
      {from && <p className="panel__hint">{from}</p>}
      {labReport.summary && <p className="lab-panel__summary">{labReport.summary}</p>}
      {findings.length > 0 && (
        <ul className="lab-findings" aria-label="Results outside the normal range">
          {findings.map((finding) => (
            <li key={finding.test} className="lab-finding">
              <span className="lab-finding__test">{finding.test}</span>
              <span className="chip chip--check">{labFlagWord(finding.flag)}</span>
              {finding.specialtyLabel && <span className="lab-finding__doctor">{finding.specialtyLabel}</span>}
            </li>
          ))}
        </ul>
      )}
      {unjudged && <p className="panel__hint">{unjudged}</p>}
      <p className="panel__hint">
        This only says which kind of doctor to see first. It is not a diagnosis.
        {labReport.documentId && (
          <>
            {' '}
            <Link to={`/documents/${encodeURIComponent(labReport.documentId)}`}>See the report</Link>
          </>
        )}
      </p>
    </section>
  )
}
