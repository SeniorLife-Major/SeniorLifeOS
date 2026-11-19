import { Icon } from '../components/Icon'

const ICONS = { ready: 'check', check: 'alertCircle', reading: 'clock', expired: 'alertCircle', problem: 'alertCircle' }

/** The small coloured label with a document's status, e.g. "Please check". Takes what statusOf() gives. */
export function StatusChip({ status }) {
  return (
    <span className={`chip chip--${status.kind}`}>
      <Icon name={ICONS[status.kind]} size={20} strokeWidth={2.6} />
      {status.chip}
    </span>
  )
}
