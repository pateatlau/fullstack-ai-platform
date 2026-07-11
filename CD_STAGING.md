# CD Staging Deployment Contract (Stage D1)

This document defines the staging deployment contract used by `.github/workflows/cd-staging.yml`.

## Trigger and Artifact Source

- Trigger: successful completion of `Build and Publish Images` on `main`
- Deploy source: immutable image tags from CI artifacts
  - `ghcr.io/<owner>/fullstack-ai-platform-backend-nodejs:sha-<commit_sha>`
  - `ghcr.io/<owner>/fullstack-ai-platform-frontend:sha-<commit_sha>`
- Deploy jobs do not rebuild images.

## Staging Environment Contract

Create a GitHub Environment named `staging` and configure:

Secrets:

- `STAGING_BACKEND_DEPLOY_WEBHOOK_URL`
- `STAGING_FRONTEND_DEPLOY_WEBHOOK_URL`

Variables:

- `STAGING_API_BASE_URL`
- `STAGING_BACKEND_HEALTHCHECK_URL`
- `STAGING_FRONTEND_HEALTHCHECK_URL`

Recommended URLs:

- `STAGING_BACKEND_HEALTHCHECK_URL`: `https://<staging-backend-host>/api/health`
- `STAGING_FRONTEND_HEALTHCHECK_URL`: `https://<staging-frontend-host>/health` (or equivalent frontend health URL)

## Deployment Sequence

1. Build/publish workflow completes on `main`.
2. Backend deploy hook receives immutable backend image reference.
3. Backend health probe validates `/api/health`.
4. Frontend deploy hook receives immutable frontend image reference and staging API base URL.
5. Frontend health probe validates reachability.
6. Workflow publishes a `Staging Deployment` check-run on the source commit.

## Verification Output Expectations

Workflow summary and check-run output include:

- environment (`staging`)
- source commit SHA
- immutable artifact references
- UTC deployment timestamp
- backend/frontend healthcheck endpoints
- overall verification result (`PASS`/`FAIL`)

## Rollback Guidance

If staging health checks fail after deployment:

1. Identify the last known-good immutable tags from previous successful `Staging Deployment` checks.
2. Re-trigger provider deploy hooks using those prior tags.
3. Re-run health probes for backend then frontend.
4. Keep failed deployment metadata for audit history; do not retag mutable channels as rollback mechanism.

This keeps rollback deterministic by restoring known-good immutable artifacts.
