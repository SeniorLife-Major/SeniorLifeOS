import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { Button, ButtonLink } from './Button'
import { PasswordField, TextField } from './Field'
import { Icon } from './Icon'
import { Screen } from './Screen'

function inRouter(ui) {
  return render(<MemoryRouter>{ui}</MemoryRouter>)
}

describe('Button', () => {
  it('is a plain button by default, so it never submits a form by accident', async () => {
    const user = userEvent.setup()
    const onClick = vi.fn()
    render(<Button onClick={onClick}>Save</Button>)

    const button = screen.getByRole('button', { name: 'Save' })
    expect(button).toHaveAttribute('type', 'button')
    expect(button).toHaveClass('button', 'button--primary')
    await user.click(button)
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('does nothing while disabled', async () => {
    const user = userEvent.setup()
    const onClick = vi.fn()
    render(
      <Button variant="secondary" disabled onClick={onClick}>
        Upload
      </Button>,
    )

    await user.click(screen.getByRole('button', { name: 'Upload' }))
    expect(onClick).not.toHaveBeenCalled()
  })

  it('goes to another screen as a link', () => {
    inRouter(
      <ButtonLink to="/documents" variant="secondary">
        My documents
      </ButtonLink>,
    )
    const link = screen.getByRole('link', { name: 'My documents' })
    expect(link).toHaveAttribute('href', '/documents')
    expect(link).toHaveClass('button--secondary')
  })
})

describe('Screen', () => {
  it('shows a back link, the content and the emergency strip', () => {
    inRouter(
      <Screen title="Settings" back={{ to: '/', label: 'Home' }}>
        <h1>Settings</h1>
      </Screen>,
    )
    expect(screen.getByRole('link', { name: 'Home' })).toHaveAttribute('href', '/')
    expect(screen.getByRole('main')).toContainElement(screen.getByRole('heading', { name: 'Settings' }))
    expect(screen.getByRole('link', { name: 'Emergency? Call 112' })).toHaveAttribute('href', 'tel:112')
    expect(document.title).toBe('Settings · SeniorLife')
  })

  it('can leave out the emergency strip (the emergency screen has its own buttons)', () => {
    inRouter(
      <Screen title="Emergency" emergency={false}>
        <h1>Emergency</h1>
      </Screen>,
    )
    expect(screen.queryByRole('link', { name: /Call 112/ })).not.toBeInTheDocument()
  })
})

describe('TextField', () => {
  it('ties the label, hint and error to the box for screen readers', () => {
    const { rerender } = render(<TextField id="name" label="Your name" hint="We will greet you by this name." />)

    const box = screen.getByLabelText('Your name')
    expect(box).toHaveAccessibleDescription('We will greet you by this name.')
    expect(box).not.toHaveAttribute('aria-invalid')

    rerender(<TextField id="name" label="Your name" hint="We will greet you by this name." error="Please enter your name." />)
    expect(box).toHaveAttribute('aria-invalid', 'true')
    expect(box).toHaveAccessibleDescription('We will greet you by this name. Please enter your name.')
  })
})

describe('PasswordField', () => {
  it('hides the password until Show is pressed, and hides it again with Hide', async () => {
    const user = userEvent.setup()
    render(<PasswordField id="password" label="Password" />)

    const box = screen.getByLabelText('Password')
    expect(box).toHaveAttribute('type', 'password')

    await user.click(screen.getByRole('button', { name: 'Show password' }))
    expect(box).toHaveAttribute('type', 'text')

    await user.click(screen.getByRole('button', { name: 'Hide password' }))
    expect(box).toHaveAttribute('type', 'password')
  })

  it('does not send the form when Show is pressed', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn((event) => event.preventDefault())
    render(
      <form onSubmit={(event) => onSubmit(event.nativeEvent)}>
        <PasswordField id="password" label="Password" />
      </form>,
    )
    await user.click(screen.getByRole('button', { name: 'Show password' }))
    expect(onSubmit).not.toHaveBeenCalled()
  })
})

describe('Icon', () => {
  it('is hidden from screen readers unless it is given a label', () => {
    const { container } = render(
      <>
        <Icon name="phone" />
        <Icon name="settings" label="Settings" />
      </>,
    )
    const [hidden, named] = container.querySelectorAll('svg')
    expect(hidden).toHaveAttribute('aria-hidden', 'true')
    expect(named).not.toHaveAttribute('aria-hidden')
    expect(screen.getByRole('img', { name: 'Settings' })).toBe(named)
  })
})
