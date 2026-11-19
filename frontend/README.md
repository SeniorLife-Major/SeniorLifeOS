# Frontend

The SeniorLife web app (Senior Mode): large text, high contrast, one main action per screen and a
"Call 112" strip on every screen. React in JavaScript, built with Vite.

## Run it

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

Start the backend as usual first (it listens on port 8000). In development Vite passes every
`/api` request on to `http://localhost:8000`, so the app and the API look like one site and the
browser needs no CORS. Without the backend, `/api` calls answer 502.

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Development server with live reload |
| `npm test` | Runs the tests once (Vitest, jsdom, Testing Library) |
| `npm run build` | The production files in `dist/` |
| `npm run preview` | Serves `dist/` on port 4173, with the same `/api` forwarding |

## Layout

```
src/
  main.jsx            starts the app: fonts, styles, the router
  App.jsx             every screen and its address
  api/                talking to the backend: client.js (every request), session.js (the token), auth.js
  auth/               who is signed in (AuthProvider) and the guard for signed-in screens (RequireAuth)
  components/         shared pieces: Screen, BackLink, EmergencyStrip, Button, ButtonLink, Icon, Brand,
                      TextField, PasswordField
  hooks/              useWait (the wait after too many tries)
  pages/              one file per screen
  profile/            the profile for every screen (ProfileProvider), text size and contrast (display.js)
  styles/app.css      colours and sizes (CSS variables) and the shared styles
  test/               test setup and fakeBackend.js, a pretend backend for tests
```

Files with JSX (screens and components) end in `.jsx`; plain JavaScript ends in `.js`.

The sign-in token is kept in `sessionStorage`, so it is gone when the tab closes.

The Mukta font (English and Hindi letters) is bundled from `@fontsource/mukta`, so users' phones
never ask Google for it.

## What works now

Signing in, creating an account, signing out, the home screen, and Settings (text size, stronger
contrast, home location, and what may go to AI services). Screens that are not built yet show
"We could not find that page". Family Mode needs sharing in the backend first.

The text size and contrast are also kept in `localStorage` (only those two values), so the app
opens at the right size straight away.
