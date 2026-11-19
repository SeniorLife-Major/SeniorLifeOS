/**
 * An on/off setting. The whole row is one big button: the words on the left, and on the right a
 * switch that also says On or Off, so the state never depends on colour alone.
 *   onChange(on) — called with the new state when the row is pressed
 */
export function Switch({ id, label, hint, checked, onChange, disabled }) {
  return (
    <button
      type="button"
      role="switch"
      id={id}
      className="switch"
      aria-checked={checked}
      aria-labelledby={`${id}-label`}
      aria-describedby={hint ? `${id}-hint` : undefined}
      disabled={disabled}
      onClick={() => onChange(!checked)}
    >
      <span className="switch__text">
        <span id={`${id}-label`} className="switch__label">
          {label}
        </span>
        {hint && (
          <span id={`${id}-hint`} className="switch__hint">
            {hint}
          </span>
        )}
      </span>
      <span className="switch__control" aria-hidden="true">
        <span className="switch__track">
          <span className="switch__knob" />
        </span>
        <span className="switch__state">{checked ? 'On' : 'Off'}</span>
      </span>
    </button>
  )
}
