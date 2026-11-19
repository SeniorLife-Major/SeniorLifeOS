import { Link } from 'react-router-dom'
import { Icon } from './Icon'

// variant: 'primary' (the default), 'secondary' or 'quiet'.
function classes(variant, extra) {
  return ['button', `button--${variant}`, extra].filter(Boolean).join(' ')
}

/** A big button that does something on this screen. */
export function Button({ variant = 'primary', icon, type = 'button', className, children, ...rest }) {
  return (
    <button type={type} className={classes(variant, className)} {...rest}>
      {icon && <Icon name={icon} size={26} />}
      <span>{children}</span>
    </button>
  )
}

/** A big button that goes to another screen. */
export function ButtonLink({ to, variant = 'primary', icon, children }) {
  return (
    <Link to={to} className={classes(variant)}>
      {icon && <Icon name={icon} size={26} />}
      <span>{children}</span>
    </Link>
  )
}
