import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// In development the app and the API look like one site: Vite passes every /api request on to
// the backend (backend/config/application.yml, server.port 8000), so the browser needs no CORS.
const api = { '/api': { target: 'http://localhost:8000', changeOrigin: true } }

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true, proxy: api },
  preview: { port: 4173, strictPort: true, proxy: api },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
