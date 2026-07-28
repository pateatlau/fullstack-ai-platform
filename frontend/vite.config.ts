import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Same-origin /api in dev — avoids recurring localhost CORS mismatches.
    // Target 127.0.0.1 (not "localhost"): on macOS, localhost often resolves to
    // ::1 first. Docker Compose publishes *:8000 on IPv6 while `make backend`
    // binds only 127.0.0.1:8000 — so proxying to localhost can hit a stale
    // compose backend without GOOGLE_CLIENT_ID (503 auth_not_configured).
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        // Required for voice mode WebSocket (/api/voice/ws); without this the
        // browser handshake stays "pending" and mic capture never starts.
        ws: true,
      },
    },
  },
  test: {
    environment: 'node',
  },
})
