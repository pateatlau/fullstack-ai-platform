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

Reserved database migration contract (Stage D3, future-enabled):

- `STAGING_DB_MIGRATION_EXECUTOR_URL` (secret): migration runner webhook/endpoint
- `STAGING_DB_MIGRATION_TOKEN` (secret): bearer token or signed trigger credential
- `STAGING_DATABASE_URL` (secret): database connection string consumed only by migration runtime
- `STAGING_DB_MIGRATION_TIMEOUT_SECONDS` (variable): max runtime budget for migration step
- `STAGING_DB_MIGRATION_STRATEGY` (variable): rollout strategy label (for example `expand-contract`)

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

## Reserved DB Migration Insertion Point and Future Order

Current workflow includes a no-op reserved job named `Reserved Staging DB Migration Stage (No-Op)`.

Future database rollout order:

1. Preflight metadata and artifact selection.
2. DB migration stage runs before application deployment.
3. Backend deployment and backend health verification.
4. Frontend deployment and frontend health verification.
5. Publish `Staging Deployment` check-run.

This insertion point keeps app rollout deterministic by ensuring schema changes are applied before app versions that depend on them.

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

## Migration Rollback Policy (Future Phase)

If migration execution is enabled in a later phase:

1. Migration failure aborts deployment before backend/frontend deploy steps.
2. If app deploy fails after a successful migration, first redeploy last known-good app artifact compatible with the migrated schema.
3. Destructive down migrations are manual-only and require an approved, tested rollback script for the exact migration version.
4. Preserve migration and deployment logs for incident review and forward-fix planning.

This keeps rollback deterministic by restoring known-good immutable artifacts.
