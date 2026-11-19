import { Route, Routes } from 'react-router-dom'
import { AuthProvider } from './auth/AuthProvider'
import { RequireAuth, SignedOutOnly } from './auth/RequireAuth'
import { HomePage } from './pages/HomePage'
import { NotFoundPage } from './pages/NotFoundPage'
import { RegisterPage } from './pages/RegisterPage'
import { SignInPage } from './pages/SignInPage'

/** Every screen of the app and its address. The router itself is added in main.jsx (tests use a memory router). */
export function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route
          path="/sign-in"
          element={
            <SignedOutOnly>
              <SignInPage />
            </SignedOutOnly>
          }
        />
        <Route
          path="/register"
          element={
            <SignedOutOnly>
              <RegisterPage />
            </SignedOutOnly>
          }
        />
        <Route element={<RequireAuth />}>
          <Route path="/" element={<HomePage />} />
        </Route>
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </AuthProvider>
  )
}
