/**
 * Capture Phase 6 README screenshots from the local dev app (localhost:5173).
 * Requires backend + frontend running and backend-python/scripts/prepare_screenshot_session.py.
 */

import { execFileSync } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.resolve(__dirname, '..')
const require = createRequire(path.join(repoRoot, 'frontend/package.json'))
const { chromium } = require('playwright')
const screenshotsDir = path.join(repoRoot, 'docs/assets/screenshots')
const gifsDir = path.join(repoRoot, 'docs/assets/gifs')
const appUrl = 'http://localhost:5173'
const desktopViewport = { width: 1440, height: 900 }
const mobileViewport = { width: 390, height: 844 }

mkdirSync(screenshotsDir, { recursive: true })
mkdirSync(gifsDir, { recursive: true })

function prepareSession() {
  const raw = execFileSync(
    'uv',
    ['run', 'python', 'scripts/prepare_screenshot_session.py'],
    {
      cwd: path.join(repoRoot, 'backend-python'),
      encoding: 'utf8',
      env: { ...process.env, PYTHONPATH: '.' },
    },
  )
  return JSON.parse(raw.trim())
}

async function seedAuth(page, session) {
  await page.addInitScript(
    ({ accessToken, user }) => {
      window.localStorage.setItem('auth.accessToken', accessToken)
      window.localStorage.setItem('auth.user', JSON.stringify(user))
    },
    { accessToken: session.access_token, user: session.user },
  )
}

async function waitForChatReady(page) {
  await page.getByRole('textbox', { name: 'Message input' }).waitFor({ state: 'visible' })
}

async function capture(page, filename) {
  await page.screenshot({
    path: path.join(screenshotsDir, filename),
    type: 'png',
    animations: 'disabled',
  })
}

async function mockDocumentsApi(page) {
  await page.route('**/api/documents', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.continue()
      return
    }

    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        documents: [
          {
            id: 'doc-1',
            filename: 'product-overview-demo.md',
            mime_type: 'text/markdown',
            status: 'ready',
            created_at: '2026-07-28T10:15:00.000Z',
            updated_at: '2026-07-28T10:16:00.000Z',
          },
          {
            id: 'doc-2',
            filename: 'architecture-notes-sample.txt',
            mime_type: 'text/plain',
            status: 'ready',
            created_at: '2026-07-27T14:30:00.000Z',
            updated_at: '2026-07-27T14:31:00.000Z',
          },
        ],
      }),
    })
  })
}

async function captureArchitecturePreview(page) {
  const svgPath = path.join(repoRoot, 'docs/architecture/system-overview.svg')
  const svg = await import('node:fs/promises').then((fs) => fs.readFile(svgPath, 'utf8'))
  await page.setContent(
    `<!doctype html><html><head><meta charset="utf-8"><style>
      body { margin: 0; background: #fff; display: flex; justify-content: center; padding: 24px; }
      svg { max-width: 1200px; width: 100%; height: auto; }
    </style></head><body>${svg}</body></html>`,
    { waitUntil: 'networkidle' },
  )
  await page.setViewportSize({ width: 1280, height: 720 })
  await capture(page, 'architecture-preview.png')
}

async function captureRemaining(page, session) {
  await mockDocumentsApi(page)
  await seedAuth(page, session)
  await page.setViewportSize(desktopViewport)
  await page.goto(`${appUrl}/documents`, { waitUntil: 'networkidle' })
  await page.getByRole('heading', { name: 'Your documents' }).waitFor({ state: 'visible' })
  await page.getByText('product-overview-demo.md').waitFor({ state: 'visible', timeout: 10000 })
  await capture(page, 'documents-page.png')

  await page.goto(appUrl, { waitUntil: 'networkidle' })
  await waitForChatReady(page)
  const voiceToggle = page.getByRole('checkbox', { name: 'Voice mode' })
  if (await voiceToggle.count()) {
    await voiceToggle.check()
    await page.getByRole('button', { name: 'Hold to speak' }).waitFor({
      state: 'visible',
      timeout: 15000,
    })
    await capture(page, 'voice-mode.png')
  }

  await captureArchitecturePreview(page)
}

async function main() {
  const session = prepareSession()
  const browser = await chromium.launch({ headless: true })
  const context = await browser.newContext()
  const page = await context.newPage()

  await page.setViewportSize(desktopViewport)
  await page.goto(appUrl, { waitUntil: 'networkidle' })
  await waitForChatReady(page)
  await capture(page, 'chat-desktop.png')

  await page.setViewportSize(mobileViewport)
  await page.goto(appUrl, { waitUntil: 'networkidle' })
  await waitForChatReady(page)
  await capture(page, 'chat-mobile.png')

  await captureRemaining(page, session)

  await browser.close()
  console.log(`Screenshots saved to ${screenshotsDir}`)
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
