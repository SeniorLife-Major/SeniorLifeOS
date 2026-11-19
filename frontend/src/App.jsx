import { Route, Routes } from 'react-router-dom'
import { AuthProvider } from './auth/AuthProvider'
import { RequireAuth, SignedOutOnly } from './auth/RequireAuth'
import { AddDocumentPage } from './pages/AddDocumentPage'
import { DocumentPage } from './pages/DocumentPage'
import { DocumentsPage } from './pages/DocumentsPage'
import { HomeLocationPage } from './pages/HomeLocationPage'
import { HomePage } from './pages/HomePage'
import { NotFoundPage } from './pages/NotFoundPage'
import { RegisterPage } from './pages/RegisterPage'
import { SettingsPage } from './pages/SettingsPage'
import { SignInPage } from './pages/SignInPage'
import { ProfileProvider } from './profile/ProfileProvider'

/** Every screen of the app and its address. The router itself is added in main.jsx (tests use a memory router). */
export function App() {
  return (
    <AuthProvider>
      <ProfileProvider>
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
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/settings/home" element={<HomeLocationPage />} />
            <Route path="/documents" element={<DocumentsPage />} />
            <Route path="/documents/add" element={<AddDocumentPage />} />
            <Route path="/documents/:documentId" element={<DocumentPage />} />
          </Route>
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </ProfileProvider>
    </AuthProvider>
  )
}
