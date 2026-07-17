/** Mirrors backend-python `app/schemas/auth.py` (`AuthenticatedUser` / `TokenResponse`). */

export interface AuthenticatedUser {
  id: string
  email: string | null
  display_name: string | null
  picture_url: string | null
}

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in: number
  user: AuthenticatedUser
}
