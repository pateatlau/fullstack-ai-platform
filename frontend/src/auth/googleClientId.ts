/**
 * Reads the public Google OAuth Web client ID from the Vite build-time env.
 * Extracted into its own module (rather than a top-level `import.meta.env`
 * read in `LoginButton`) so tests can deterministically control it instead of
 * depending on whatever `.env` happens to be present locally.
 */
export function getGoogleClientId(): string | undefined {
  return import.meta.env.VITE_GOOGLE_CLIENT_ID as string | undefined
}
