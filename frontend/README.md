# Frontend

The SeniorLife web app (Senior Mode): large text, high contrast, one main action per screen and a
"Call 112" strip on every screen. React and TypeScript, built with Vite.

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
| `npm run typecheck` | Checks the TypeScript types |
| `npm run build` | Type check, then the production files in `dist/` |
| `npm run preview` | Serves `dist/` on port 4173, with the same `/api` forwarding |

## Layout

```
src/
  main.tsx            starts the app: fonts, styles, the router
  App.tsx             every screen and its address
  components/         shared pieces: Screen, BackLink, EmergencyStrip, Button, ButtonLink, Icon
  pages/              one file per screen
  styles/app.css      colours and sizes (CSS variables) and the shared styles
  test/setup.ts       test setup
```

The Mukta font (English and Hindi letters) is bundled from `@fontsource/mukta`, so users' phones
never ask Google for it.

## What works now

The home screen and the shared pieces. Screens that are not built yet show "We could not find
that page". Family Mode needs sharing in the backend first.
