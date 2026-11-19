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
  api/                talking to the backend: client.js (every request), session.js (the token), auth.js,
                      profile.js, documents.js
  auth/               who is signed in (AuthProvider) and the guard for signed-in screens (RequireAuth)
  components/         shared pieces: Screen, BackLink, EmergencyStrip, Button, ButtonLink, Icon, Brand,
                      TextField, PasswordField, Switch, FocusedTitle
  documents/          document types and status words, getting a photo ready to send, loading (and
                      checking again) while a document is being read, the details and their checks,
                      typing a document in (typeIn.js), and what an office may ask for (proofs.js)
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
contrast, home location, and what may go to AI services).

My documents: the list (the ones that need you first), adding one photo or PDF, and following it
while the backend reads it. A photo over 5 MB, or one the backend can't take (WebP, or HEIC where
the browser can open it), is redrawn as a smaller JPEG on the phone before it is sent.

Checking a document's details: one at a time next to the photo (or a link to the PDF), changing
or adding any of them, the rows of a lab report, prescription or bill, then saving it or saying not
to keep it.

Typing a document in with no photo ("No photo? Type the details in"): one form with every detail
of that kind of document, checked on the phone and then by the backend, and saved straight away.

A saved document shows its details (ID numbers hidden). It can get a new copy when the paper is
renewed, as a photo or typed in; the old copy stays in use until the new one is checked. It can be
deleted, after a question.

"Do I have what I need?": tick what an office asked for (proof of age, income, address and so on)
and see which of your documents are ready, waiting for a check, ended or missing, with what to do
next.

Screens that are not built yet (the doctor search) show "We could not find that page". Family Mode
needs sharing in the backend first.

The text size and contrast are also kept in `localStorage` (only those two values), so the app
opens at the right size straight away.
