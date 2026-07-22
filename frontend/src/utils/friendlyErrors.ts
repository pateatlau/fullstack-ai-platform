const PROVIDER_ERROR_MESSAGES: Record<string, string> = {
  provider_timeout: 'The AI service took too long. Please try again.',
  provider_rate_limited: "We're busy right now. Please wait a moment and retry.",
  provider_error: 'Something went wrong with the AI service. Please try again.',
  empty_provider_response: 'The model returned an empty response. Please try again.',
}

export function friendlyErrorMessage(code: string | undefined, fallback?: string): string {
  if (code && code in PROVIDER_ERROR_MESSAGES) {
    return PROVIDER_ERROR_MESSAGES[code]
  }
  return fallback ?? 'Something went wrong. Please try again.'
}
