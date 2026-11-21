import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { App } from '../App'
import { saveSession } from '../api/session'
import { fail, fakeBackend, makeProfile, MEERA, ok, serveProfile } from '../test/fakeBackend'
import REAL from '../test/realSchemeChecks.json'

// The answers are the real backend's (graphs/benefits_graph.py through api/benefits.py), recorded
// for Kamla Devi's papers in backend/tests/test_benefits_schemes.py: before and after she adds her
// husband's death certificate, without and with her answers, and the package approved.

const CHECKS = 'POST /api/v1/benefits/checks'
const APPROVE = 'POST /api/v1/benefits/checks/check-widow-2/approve'

function signedIn(routes = {}) {
  saveSession('token-kamla', 3600)
  const backend = fakeBackend({ 'GET /api/v1/auth/me': () => ok(MEERA), ...routes })
  serveProfile(backend, makeProfile())
  return backend
}

function renderApp(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

const card = (name) => screen.getByRole('link', { name: new RegExp(`^${name}`) })

describe('Schemes for me', () => {
  it('is on the home screen', async () => {
    signedIn()
    renderApp('/')
    expect(await screen.findByRole('link', { name: /^Schemes for me/ })).toHaveAttribute('href', '/benefits')
  })

  it('groups the schemes and says the first thing to add', async () => {
    const backend = signedIn({ [CHECKS]: () => ok(REAL.screenBefore, 201) })
    renderApp('/benefits')

    expect(await screen.findByRole('heading', { level: 2, name: 'You may get these' })).toBeInTheDocument()
    expect(backend.sent(CHECKS)[0].body).toEqual({})
    const groups = screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent)
    expect(groups).toEqual(['You may get these', 'Add papers to know', 'Not for you now'])

    const widow = card('Widow Pension \\(demo\\)')
    expect(widow).toHaveAttribute('href', '/benefits/widow_pension')
    expect(widow).toHaveTextContent('Add papers to know')
    expect(widow).toHaveTextContent("Add your husband's death certificate.")
    expect(card('Old Age Pension \\(demo\\)')).toHaveTextContent('You may get this')
    expect(card('Senior Health Cover 70\\+ \\(demo\\)')).toHaveTextContent('Not for you now')
    expect(screen.getByText(/made-up schemes for this demonstration/)).toBeInTheDocument()
  })

  it('says only one pension can be had when two are likely', async () => {
    signedIn({ [CHECKS]: () => ok(REAL.screenAfter, 201) })
    renderApp('/benefits')
    expect(await screen.findByText('You can get only one monthly pension. Choose the one that suits you best.')).toBeInTheDocument()
    expect(card('Widow Pension \\(demo\\)')).toHaveTextContent('You may get this')
  })

  it('shows the problem and tries again', async () => {
    const user = userEvent.setup()
    let calls = 0
    signedIn({
      [CHECKS]: () => {
        calls += 1
        return calls === 1 ? fail(503, 'STORAGE_UNAVAILABLE', 'The scheme check tables are missing') : ok(REAL.screenBefore, 201)
      },
    })
    renderApp('/benefits')
    expect(await screen.findByRole('alert')).toHaveTextContent('We could not look at your papers.')
    expect(screen.getByRole('alert')).not.toHaveTextContent('tables') // the server's words stay out
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('heading', { level: 2, name: 'You may get these' })).toBeInTheDocument()
  })
})

describe('One scheme', () => {
  it('says what to add, with a link to add it', async () => {
    const backend = signedIn({ [CHECKS]: () => ok(REAL.widowNoCertificate, 201) })
    renderApp('/benefits/widow_pension')

    expect(await screen.findByRole('heading', { level: 1, name: 'Widow Pension (demo)' })).toBeInTheDocument()
    expect(backend.sent(CHECKS)[0].body).toEqual({ schemeId: 'widow_pension', declarations: {} })
    const todo = screen.getByRole('list', { name: 'What to do' })
    expect(todo).toHaveTextContent("Add your husband's death certificate.")
    expect(within(todo).getByRole('link', { name: 'Add the death certificate' })).toHaveAttribute(
      'href',
      '/documents/add?type=death_certificate',
    )
    const rules = within(screen.getByRole('region', { name: 'What it asks for' }))
    expect(rules.getByText('Aged 40 or more').closest('li')).toHaveTextContent('Your papers: 68 years')
    expect(rules.getByText('A woman').closest('li')).toHaveTextContent('Your papers: Woman')
    expect(rules.getByText(/Her husband has died/).closest('li')).toHaveTextContent('Not known yet')
  })

  it('asks her answers, then prepares the package, and she approves it', async () => {
    const user = userEvent.setup()
    const backend = signedIn({
      [CHECKS]: ({ body }) => ok(Object.keys(body.declarations).length ? REAL.widowPackage : REAL.widowNeedsAnswers, 201),
      [APPROVE]: () => ok(REAL.widowReady),
    })
    renderApp('/benefits/widow_pension')

    const answers = await screen.findByRole('group', { name: 'Your answers' })
    expect(screen.queryByRole('button', { name: 'Check again with my answers' })).toBeNull()
    await user.click(within(answers).getByRole('button', { name: 'I have not married again' }))
    await user.click(within(answers).getByRole('button', { name: 'I do not get another government pension' }))
    await user.click(screen.getByRole('button', { name: 'Check again with my answers' }))

    expect(await screen.findByRole('heading', { level: 2, name: 'Ready to apply' })).toBeInTheDocument()
    expect(backend.sent(CHECKS)[1].body).toEqual({
      schemeId: 'widow_pension',
      declarations: { not_remarried: true, no_other_pension: true },
    })
    const papers = screen.getByRole('region', { name: 'Ready to apply' })
    for (const paper of ['ID card', 'Death certificate', 'Ration card', 'Bank passbook']) {
      expect(papers).toHaveTextContent(paper)
    }
    expect(papers).toHaveTextContent('Sign the declarations: I have not married again; I do not get another government pension')

    await user.click(screen.getByRole('button', { name: 'Yes, get it ready' }))
    expect(await screen.findByRole('heading', { level: 2, name: /Your application is ready to take/ })).toBeInTheDocument()
    expect(backend.sent(APPROVE)[0].body).toEqual({ actionId: REAL.widowPackage.package.actionId })
    expect(screen.queryByRole('button', { name: 'Yes, get it ready' })).toBeNull()
  })

  it('sends a changed reminder with the approval', async () => {
    const user = userEvent.setup()
    const backend = signedIn({ [CHECKS]: () => ok(REAL.widowPackage, 201), [APPROVE]: () => ok(REAL.widowReady) })
    renderApp('/benefits/widow_pension')
    const reminder = await screen.findByLabelText('Your reminder')
    await user.clear(reminder)
    await user.type(reminder, 'Ward office on Monday')
    await user.click(screen.getByRole('button', { name: 'Yes, get it ready' }))
    await screen.findByRole('heading', { level: 2, name: /ready to take/ })
    expect(backend.sent(APPROVE)[0].body).toEqual({
      actionId: REAL.widowPackage.package.actionId,
      taskTitle: 'Ward office on Monday',
    })
  })

  it('checks again when the papers changed before the approval', async () => {
    const user = userEvent.setup()
    const backend = signedIn({
      [CHECKS]: () => ok(REAL.widowPackage, 201),
      [APPROVE]: () => fail(409, 'CONFLICT', 'Your papers or answers have changed since this check, or a day has passed. Please check again.'),
    })
    renderApp('/benefits/widow_pension')
    await user.click(await screen.findByRole('button', { name: 'Yes, get it ready' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Your papers or answers have changed since this check')
    expect(screen.queryByRole('button', { name: 'Yes, get it ready' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Check again' }))
    expect(await screen.findByRole('button', { name: 'Yes, get it ready' })).toBeInTheDocument()
    expect(backend.sent(CHECKS)[1].body.declarations).toEqual({ not_remarried: true, no_other_pension: true })
  })

  it('sends a near-miss name to be checked on the certificate', async () => {
    signedIn({ [CHECKS]: () => ok(REAL.widowNameCheck, 201) })
    renderApp('/benefits/widow_pension')
    const todo = await screen.findByRole('list', { name: 'What to do' })
    expect(todo).toHaveTextContent('spelt a little differently from your name')
    expect(within(todo).getByRole('link', { name: 'Look at it' })).toHaveAttribute('href', '/documents/death')
    expect(screen.getByText('Please check', { selector: '.chip' })).toBeInTheDocument()
  })

  it('says when the scheme is not in the list', async () => {
    signedIn({ [CHECKS]: () => fail(404, 'NOT_FOUND', 'This scheme is not in the demo list') })
    renderApp('/benefits/made_up')
    expect(await screen.findByRole('heading', { level: 1, name: 'Scheme not found' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'See the schemes' })).toHaveAttribute('href', '/benefits')
  })
})
