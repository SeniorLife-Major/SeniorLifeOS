import { useState } from 'react'

/**
 * A labelled text box with an optional hint and error message.
 *   hint   — shown under the label, before the person types
 *   error  — shown under the box in red; the box is marked as wrong for screen readers too
 *   action — something next to the box, such as the password's Show button
 *   multiline — a box of several lines (a <textarea>), for words in the person's own words
 * Everything else (value, onChange, ref, autoComplete, …) goes to the <input>.
 */
export function TextField({ id, label, hint, error, action, multiline = false, ...input }) {
  const Box = multiline ? 'textarea' : 'input'
  const hintId = hint ? `${id}-hint` : undefined
  const errorId = error ? `${id}-error` : undefined
  const describedBy = [hintId, errorId].filter(Boolean).join(' ') || undefined

  const box = (
    <Box
      id={id}
      className={multiline ? 'input input--multiline' : 'input'}
      aria-invalid={error ? true : undefined}
      aria-describedby={describedBy}
      {...input}
    />
  )

  return (
    <div className="field">
      <label htmlFor={id} className="field__label">
        {label}
      </label>
      {hint && (
        <p id={hintId} className="field__hint">
          {hint}
        </p>
      )}
      {action ? (
        <div className="input-row">
          {box}
          {action}
        </div>
      ) : (
        box
      )}
      {error && (
        <p id={errorId} className="field__error">
          {error}
        </p>
      )}
    </div>
  )
}

/** A password box with a Show / Hide button, so the person can check what they typed. */
export function PasswordField(props) {
  const [showing, setShowing] = useState(false)
  return (
    <TextField
      {...props}
      type={showing ? 'text' : 'password'}
      autoCapitalize="none"
      autoCorrect="off"
      spellCheck={false}
      action={
        <button
          type="button"
          className="input-action"
          aria-controls={props.id}
          aria-label={showing ? 'Hide password' : 'Show password'}
          onClick={() => setShowing((now) => !now)}
        >
          {showing ? 'Hide' : 'Show'}
        </button>
      }
    />
  )
}
