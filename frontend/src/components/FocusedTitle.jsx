import { useEffect, useRef } from 'react'

/** A page heading that takes the focus when it appears, so screen readers start there. */
export function FocusedTitle({ children }) {
  const ref = useRef(null)
  useEffect(() => {
    ref.current?.focus()
  }, [])
  return (
    <h1 ref={ref} tabIndex={-1} className="page-title">
      {children}
    </h1>
  )
}
