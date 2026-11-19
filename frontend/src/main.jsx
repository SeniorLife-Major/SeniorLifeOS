import '@fontsource/mukta/400.css'
import '@fontsource/mukta/600.css'
import '@fontsource/mukta/700.css'
import '@fontsource/mukta/800.css'
import './styles/app.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { App } from './App'
import { applyDisplay, rememberedDisplay } from './profile/display'

// The text size and contrast from the last visit, before the first screen is drawn.
applyDisplay(rememberedDisplay())

const root = document.getElementById('root')
if (!root) {
  throw new Error('index.html has no element with id "root"')
}

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)
