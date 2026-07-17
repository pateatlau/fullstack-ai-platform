/**
 * Loads the Google Identity Services (GIS) script exactly once and resolves
 * when `window.google.accounts.id` is available. Safe to call repeatedly
 * (e.g. across remounts); concurrent calls share the same in-flight promise.
 */

const GIS_SCRIPT_SRC = 'https://accounts.google.com/gsi/client'

let loadPromise: Promise<void> | null = null

export function loadGoogleIdentityScript(): Promise<void> {
  if (typeof window === 'undefined' || typeof document === 'undefined') {
    return Promise.reject(new Error('Google Identity Services requires a browser environment.'))
  }

  if (window.google?.accounts?.id) {
    return Promise.resolve()
  }

  if (loadPromise) {
    return loadPromise
  }

  loadPromise = new Promise<void>((resolve, reject) => {
    const onLoad = () => resolve()
    const onError = () => {
      loadPromise = null
      reject(new Error('Failed to load Google Identity Services.'))
    }

    const existing = document.querySelector<HTMLScriptElement>(`script[src="${GIS_SCRIPT_SRC}"]`)
    if (existing) {
      existing.addEventListener('load', onLoad, { once: true })
      existing.addEventListener('error', onError, { once: true })
      return
    }

    const script = document.createElement('script')
    script.src = GIS_SCRIPT_SRC
    script.async = true
    script.defer = true
    script.addEventListener('load', onLoad, { once: true })
    script.addEventListener('error', onError, { once: true })
    document.head.appendChild(script)
  })

  return loadPromise
}
