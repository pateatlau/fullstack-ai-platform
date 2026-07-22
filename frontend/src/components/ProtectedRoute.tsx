import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuthContext } from '../context/AuthContext'

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { status } = useAuthContext()

  if (status === 'authenticated') {
    return <>{children}</>
  }

  // Guest or locally detected expired session — send to public chat surface.
  return <Navigate to="/" replace />
}
