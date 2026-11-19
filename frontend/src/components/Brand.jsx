import { Icon } from './Icon'
import { APP_NAME } from './Screen'

/** The app's mark and name, at the top of the sign-in screens. */
export function Brand() {
  return (
    <div className="brand">
      <span className="brand__mark">
        <Icon name="shieldCheck" size={28} />
      </span>
      <span className="brand__name">{APP_NAME}</span>
    </div>
  )
}
