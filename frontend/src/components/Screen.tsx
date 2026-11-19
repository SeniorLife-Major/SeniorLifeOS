import { useEffect, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Icon } from './Icon'

export const APP_NAME = 'SeniorLife'
export const EMERGENCY_NUMBER = '112'

export function BackLink({ to, label }: { to: string; label: string }) {
  return (
    <Link to={to} className="back-link">
      <Icon name="arrowLeft" size={26} strokeWidth={2.4} />
      {label}
    </Link>
  )
}

/** Calls the national emergency number. Shown at the bottom of every screen unless turned off. */
export function EmergencyStrip() {
  return (
    <a className="emergency-strip" href={`tel:${EMERGENCY_NUMBER}`}>
      <Icon name="phone" />
      Emergency? Call {EMERGENCY_NUMBER}
    </a>
  )
}

interface ScreenProps {
  /** Names the screen in the browser tab and for screen readers. */
  title: string
  back?: { to: string; label: string }
  emergency?: boolean
  children: ReactNode
}

export function Screen({ title, back, emergency = true, children }: ScreenProps) {
  useEffect(() => {
    document.title = `${title} · ${APP_NAME}`
  }, [title])

  return (
    <div className="screen">
      {back && (
        <header className="screen__top">
          <BackLink to={back.to} label={back.label} />
        </header>
      )}
      <main className="screen__body">{children}</main>
      {emergency && <EmergencyStrip />}
    </div>
  )
}
