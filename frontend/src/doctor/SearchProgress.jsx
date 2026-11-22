import { Icon } from '../components/Icon'
import { STAGES } from './search'

/** The stages of a search, ticked off as the backend finishes them (the mockup's Searching screen).
 *  stages: STAGES, or LAB_STAGES for a search from a lab report. */
export function SearchProgress({ done, stages = STAGES }) {
  return (
    <>
      <ol className="search-steps" aria-label="What we are doing">
        {stages.map((label, index) => {
          const state = index < done ? 'done' : index === done ? 'now' : 'later'
          return (
            <li key={label} className={`search-step search-step--${state}`}>
              <span className="search-step__mark" aria-hidden="true">
                {state === 'done' && <Icon name="check" size={22} strokeWidth={3} />}
                {state === 'now' && <span className="search-step__dot" />}
              </span>
              <span className="search-step__label">
                {label}
                <span className="visually-hidden">{state === 'done' ? ', done' : state === 'now' ? ', doing now' : ''}</span>
              </span>
            </li>
          )
        })}
      </ol>
      {/* One short sentence for screen readers as each stage starts. */}
      <p className="visually-hidden" role="status">
        {done < stages.length ? `${stages[done]}…` : 'Almost done…'}
      </p>
    </>
  )
}
