import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { ProtectedRoute } from './components/ProtectedRoute'
import { AuthProvider } from './context/AuthContext'
import { ChatPage } from './pages/ChatPage'
import { DocumentsPage } from './pages/DocumentsPage'
import { MemorySettingsPage } from './pages/MemorySettingsPage'
import { NotFoundPage } from './pages/NotFoundPage'
import { WorkflowsPage } from './pages/WorkflowsPage'
import { ObservabilityPage } from './pages/ObservabilityPage'
import { PluginsPage } from './pages/PluginsPage'
import { ApprovalsPage } from './pages/ApprovalsPage'

function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <div className="min-h-dvh bg-shell-100 text-shell-950">
          <Routes>
            <Route path="/" element={<ChatPage />} />
            <Route
              path="/documents"
              element={
                <ProtectedRoute>
                  <DocumentsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/settings/memory"
              element={
                <ProtectedRoute>
                  <MemorySettingsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/workflows"
              element={
                <ProtectedRoute>
                  <WorkflowsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/observability"
              element={
                <ProtectedRoute>
                  <ObservabilityPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/plugins"
              element={
                <ProtectedRoute>
                  <PluginsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/approvals"
              element={
                <ProtectedRoute>
                  <ApprovalsPage />
                </ProtectedRoute>
              }
            />
            <Route path="*" element={<NotFoundPage />} />
          </Routes>
        </div>
      </BrowserRouter>
    </AuthProvider>
  )
}

export default App
