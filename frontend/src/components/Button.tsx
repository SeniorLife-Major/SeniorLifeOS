import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Icon, type IconName } from './Icon'

export type ButtonVariant = 'primary' | 'secondary' | 'quiet'

function classes(variant: ButtonVariant, extra?: string): string {
  return ['button', `button--${variant}`, extra].filter(Boolean).join(' ')
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  icon?: IconName
}

/** A big button that does something on this screen. */
export function Button({ variant = 'primary', icon, type = 'button', className, children, ...rest }: ButtonProps) {
  return (
    <button type={type} className={classes(variant, className)} {...rest}>
      {icon && <Icon name={icon} size={26} />}
      <span>{children}</span>
    </button>
  )
}

interface ButtonLinkProps {
  to: string
  variant?: ButtonVariant
  icon?: IconName
  children: ReactNode
}

/** A big button that goes to another screen. */
export function ButtonLink({ to, variant = 'primary', icon, children }: ButtonLinkProps) {
  return (
    <Link to={to} className={classes(variant)}>
      {icon && <Icon name={icon} size={26} />}
      <span>{children}</span>
    </Link>
  )
}
