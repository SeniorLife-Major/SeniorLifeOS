import { Icon } from '../components/Icon'

// Used when the backend sends no numbers (it always should): India's 112 and 108.
const FALLBACK = [
  { number: '112', for: 'Any emergency (police, fire, ambulance)' },
  { number: '108', for: 'Ambulance' },
]

/** Big buttons that start a phone call, one per number the backend gave (14416 first when it is there). */
export function CallButtons({ numbers }) {
  const valid = (numbers ?? []).filter((entry) => /^[0-9+]{3,15}$/.test(String(entry?.number ?? '')))
  const list = valid.length > 0 ? valid : FALLBACK
  const ordered = [...list.filter((entry) => entry.number === '14416'), ...list.filter((entry) => entry.number !== '14416')]
  return (
    <div className="call-buttons">
      {ordered.map((entry) => (
        <a key={entry.number} className="call-button" href={`tel:${entry.number}`}>
          <Icon name="phone" size={30} strokeWidth={2.4} />
          <span className="call-button__text">
            <span className="call-button__number">Call {entry.number}</span>
            {entry.for && <span className="call-button__for">{entry.for}</span>}
          </span>
        </a>
      ))}
    </div>
  )
}
