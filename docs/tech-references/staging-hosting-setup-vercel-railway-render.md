# Staging Hosting Setup Guide

This guide gives practical, end-to-end setup steps for:

- Frontend on Vercel
- Backend on Railway (Python backend default)
- GitHub Environment values required by the staging CD workflow

You do not need custom domains for this stage. Provider-generated default URLs are enough.

## 1. Prerequisites

- GitHub repository connected to your Vercel, Railway, or Render account
- Existing CI image publishing working to GHCR on main
- Access to repository settings in GitHub

Current staging workflow file:

- [.github/workflows/cd-staging.yml](.github/workflows/cd-staging.yml)

Current D1 plan checkpoint:

- [docs/plans/devops-implementation-plan.md](docs/plans/devops-implementation-plan.md)

## 2. Values You Must Configure in GitHub Environment

Create or open GitHub Environment named staging and set the following.

Secrets:

- STAGING_BACKEND_DEPLOY_WEBHOOK_URL
- STAGING_FRONTEND_DEPLOY_WEBHOOK_URL

Variables:

- STAGING_API_BASE_URL
- STAGING_BACKEND_HEALTHCHECK_URL
- STAGING_FRONTEND_HEALTHCHECK_URL

Expected format:

- STAGING_API_BASE_URL: https://backend-hostname
- STAGING_BACKEND_HEALTHCHECK_URL: https://backend-hostname/api/health
- STAGING_FRONTEND_HEALTHCHECK_URL: https://frontend-hostname or https://frontend-hostname/health

## 3. Frontend Setup on Vercel

### 3.1 Create Project

1. Go to Vercel dashboard.
2. Click Add New Project.
3. Import your GitHub repository.
4. Set Root Directory to frontend.
5. Build settings should auto-detect Vite. If needed:

- Build command: npm run build
- Output directory: dist

### 3.2 Configure Runtime Variable

1. Open project settings.
2. Go to Environment Variables.
3. Add VITE_API_BASE_URL for Preview or Staging environment.
4. Use your backend staging URL value (from Railway or Render section).

### 3.3 Get Frontend URL and Deploy Hook

1. Open project overview and copy default domain, typically https://project-name.vercel.app.
2. Open Settings, then Git, then Deploy Hooks.
3. Create deploy hook for the staging branch/environment.
4. Copy generated hook URL.

Use these in GitHub:

- STAGING_FRONTEND_DEPLOY_WEBHOOK_URL: Vercel deploy hook URL
- STAGING_FRONTEND_HEALTHCHECK_URL: default Vercel URL or health route URL

## 4. Backend Setup (Default): Railway

Use this if you want managed deploys with minimal setup friction.

### 4.1 Create Backend Service

1. Go to Railway dashboard.
2. Create New Project.
3. Connect GitHub repo.
4. Add a service from your repo using Root Directory backend-python.
5. Set start/build as needed if Railway does not auto-detect:

- Build (Nixpacks): uv sync --no-dev
- Start (Nixpacks): uv run python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT

If using Dockerfile deploy mode, custom build/start commands should be left empty.

### 4.2 Configure Environment Variables

In Railway service variables, set required backend values:

- PORT (if needed by platform)
- LLM_PROVIDER
- OPENAI_API_KEY or GEMINI_API_KEY
- CORS_ALLOWED_ORIGINS
- APP_ENV=staging

Set CORS_ALLOWED_ORIGINS to your Vercel staging URL.

### 4.3 Get Backend URL and Deploy Hook

1. Open Railway service.
2. Copy public domain URL, typically https://service-name.up.railway.app.
3. Create a deploy hook in Railway project settings (or use provider webhook if available).
4. Copy deploy hook URL.

Use these in GitHub:

- STAGING_BACKEND_DEPLOY_WEBHOOK_URL: Railway deploy hook URL
- STAGING_API_BASE_URL: Railway public URL
- STAGING_BACKEND_HEALTHCHECK_URL: Railway public URL with /api/health

## 5. Backend Setup Option B: Render (Legacy/Optional)

Use this only if Railway is unavailable and you still need a Python backend host.

### 5.1 Create Web Service

1. Go to Render dashboard.
2. Click New and choose Web Service.
3. Connect GitHub repository.
4. Set Root Directory to backend-python.
5. Use Python runtime settings:

- Build command: uv sync --no-dev
- Start command: uv run python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT

### 5.2 Configure Environment Variables

In Render service Environment tab, add:

- LLM_PROVIDER
- OPENAI_API_KEY or GEMINI_API_KEY
- CORS_ALLOWED_ORIGINS
- APP_ENV=staging

Set CORS_ALLOWED_ORIGINS to your Vercel staging URL.

### 5.3 Get Backend URL and Deploy Hook

1. Copy Render service URL, typically https://service-name.onrender.com.
2. Create Deploy Hook in Render service settings.
3. Copy hook URL.

Use these in GitHub:

- STAGING_BACKEND_DEPLOY_WEBHOOK_URL: Render deploy hook URL
- STAGING_API_BASE_URL: Render service URL
- STAGING_BACKEND_HEALTHCHECK_URL: Render service URL with /api/health

## 6. Required Cross-Platform Wiring

After frontend and backend are live:

1. Set backend CORS_ALLOWED_ORIGINS to your Vercel URL.
2. Set frontend VITE_API_BASE_URL to your backend base URL.
3. Confirm backend health endpoint returns success.
4. Confirm frontend loads and can call backend.

## 7. GitHub Environment Setup Steps

1. Open repository settings in GitHub.
2. Go to Environments.
3. Open staging environment.
4. Add two secrets:

- STAGING_BACKEND_DEPLOY_WEBHOOK_URL
- STAGING_FRONTEND_DEPLOY_WEBHOOK_URL

5. Add three variables:

- STAGING_API_BASE_URL
- STAGING_BACKEND_HEALTHCHECK_URL
- STAGING_FRONTEND_HEALTHCHECK_URL

6. Save all values.

## 8. Validation Checklist for D1

1. Push or merge a commit to main.
2. Confirm Build and Publish Images succeeds.
3. Confirm CD Staging Deploy starts from workflow_run trigger.
4. Confirm Deploy Staging Backend step succeeds.
5. Confirm Verify Staging Backend Health passes.
6. Confirm Deploy Staging Frontend step succeeds.
7. Confirm Verify Staging Frontend Reachability passes.
8. Open frontend staging URL and run one manual chat sanity check.

## 9. Troubleshooting Quick Guide

Missing secret error:

- Ensure key name matches exactly.
- Ensure value was added as a secret, not variable.

Health check fails:

- Open health URL directly in browser.
- Confirm backend is live and route is /api/health.
- Check provider logs for startup failure.

Frontend reachable but chat fails:

- Recheck frontend VITE_API_BASE_URL.
- Recheck backend CORS_ALLOWED_ORIGINS.
- Confirm backend provider API key variables are present.

## 10. Suggested Minimal First Path

If you want the fastest path to green D1:

1. Backend on Railway (Python backend)
2. Frontend on Vercel
3. Use provider default domains
4. Fill GitHub staging secrets and variables
5. Re-run workflow and verify health checks
