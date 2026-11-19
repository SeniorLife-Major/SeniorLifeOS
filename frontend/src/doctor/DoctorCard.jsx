import { Icon } from '../components/Icon'
import { clinicLine, directionsLink, distanceWords, safeLink } from './search'

/** One doctor (the mockup's Results card): what is best about them, the clinic, how far, when
 *  open, the reasons, any warnings, and links for directions and their page. */
export function DoctorCard({ doctor, id }) {
  const name = doctor.name || 'Name not listed'
  const clinic = clinicLine(doctor.clinic)
  const distance = distanceWords(doctor.distanceKm)
  const hours = doctor.clinic?.hours
  const directions = directionsLink(doctor.clinic)
  const page = safeLink(doctor.profileUrl)
  const labels = doctor.labels ?? []
  const reasons = doctor.reasons ?? []
  const flags = doctor.flags ?? []

  return (
    <article className="doctor" aria-labelledby={id}>
      {labels.length > 0 && (
        <p className="doctor__labels">
          {labels.map((label) => (
            <span key={label} className="chip chip--best">
              <Icon name="check" size={18} strokeWidth={3} />
              {label}
            </span>
          ))}
        </p>
      )}
      <div className="doctor__who">
        <h2 id={id} className="doctor__name">
          {name}
        </h2>
        {clinic && <p className="doctor__clinic">{clinic}</p>}
      </div>
      {(distance || hours) && (
        <p className="doctor__facts">
          {distance && (
            <span className="fact">
              <Icon name="mapPin" size={20} />
              {distance}
            </span>
          )}
          {hours && (
            <span className="fact">
              <Icon name="clock" size={20} />
              {hours}
            </span>
          )}
        </p>
      )}
      {reasons.length > 0 && (
        <ul className="doctor__points" aria-label="Why this doctor">
          {reasons.map((reason) => (
            <li key={reason}>
              <Icon name="check" size={20} strokeWidth={2.8} />
              <span>{reason}</span>
            </li>
          ))}
        </ul>
      )}
      {flags.length > 0 && (
        <ul className="doctor__points doctor__points--flags" aria-label="Please note">
          {flags.map((flag) => (
            <li key={flag}>
              <Icon name="alertCircle" size={20} />
              <span>{flag}</span>
            </li>
          ))}
        </ul>
      )}
      {doctor.registrationNote && <p className="doctor__note">{doctor.registrationNote}</p>}
      {(directions || page) && (
        <div className="doctor__links">
          {directions && (
            <a className="button button--primary" href={directions} target="_blank" rel="noreferrer">
              <Icon name="navigation" size={22} />
              <span>
                Directions <span className="visually-hidden">to {name}</span>
              </span>
            </a>
          )}
          {page && (
            <a className="button button--secondary" href={page} target="_blank" rel="noreferrer">
              <span>
                Doctor's page<span className="visually-hidden">: {name}</span>
              </span>
            </a>
          )}
        </div>
      )}
    </article>
  )
}
