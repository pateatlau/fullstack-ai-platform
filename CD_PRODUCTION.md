# CD Production Promotion Contract (Stage D2)

This document defines the production promotion contract used by `.github/workflows/cd-production.yml`.

## Trigger and Artifact Source

- Trigger: manual `workflow_dispatch`
- Required input: `source_sha` (full 40-char commit SHA)
- Deploy source: immutable image tags from CI artifacts
  - `ghcr.io/<owner>/fullstack-ai-platform-backend-nodejs:sha-<source_sha>`
  - `ghcr.io/<owner>/fullstack-ai-platform-frontend:sha-<source_sha>`
- Promotion guard: selected `source_sha` must have a successful `Staging Deployment` check-run.

## Production Environment Contract

Create a GitHub Environment named `production` and configure approval protection rules.

Optional secret (frontend deploy webhook):

- `PRODUCTION_FRONTEND_DEPLOY_WEBHOOK_URL` (environment secret), or
- `PRODUCTION_FRONTEND_DEPLOY_WEBHOOK_URL_FALLBACK` (repository secret)

If neither secret is configured, the frontend deploy step is skipped gracefully and Vercel Git auto-deploy is treated as authoritative (mirroring the Railway auto-deploy model used for the backend).

Variables:

- `PRODUCTION_API_BASE_URL`
- `PRODUCTION_BACKEND_HEALTHCHECK_URL`
- `PRODUCTION_FRONTEND_HEALTHCHECK_URL`

Reserved database migration contract (Stage D3, future-enabled):

- `PRODUCTION_DB_MIGRATION_EXECUTOR_URL` (environment secret): migration runner webhook/endpoint
- `PRODUCTION_DB_MIGRATION_TOKEN` (environment secret): bearer token or signed trigger credential
- `PRODUCTION_DATABASE_URL` (environment secret): database connection string consumed only by migration runtime
- `PRODUCTION_DB_MIGRATION_TIMEOUT_SECONDS` (environment variable): max runtime budget for migration step
- `PRODUCTION_DB_MIGRATION_STRATEGY` (environment variable): rollout strategy label (for example `expand-contract`)

Recommended URLs:

- `PRODUCTION_BACKEND_HEALTHCHECK_URL`: `https://<prod-backend-host>/api/health`
- `PRODUCTION_FRONTEND_HEALTHCHECK_URL`: `https://<prod-frontend-host>/health` (or equivalent frontend health URL)

## Deployment Sequence

1. Operator runs `CD Production Promote` and provides `source_sha` from a successful staging deployment.
2. Workflow validates `source_sha` format and enforces successful `Staging Deployment` check-run.
3. Backend deploy hook step is skipped when Railway auto-deploy is the backend mode.
4. Backend health probe validates production `/api/health`.
5. Frontend deploy hook receives immutable frontend image reference and production API base URL when a webhook secret is configured; otherwise the step is skipped and Vercel Git auto-deploy is authoritative.
6. Frontend health probe validates reachability.
7. Workflow publishes a `Production Deployment` check-run on `source_sha`.

## Reserved DB Migration Insertion Point and Future Order

Current workflow includes a no-op reserved job named `Reserved Production DB Migration Stage (No-Op)`.

Future production rollout order:

1. Preflight validation and immutable artifact resolution.
2. DB migration stage runs before application deployment.
3. Backend deployment and backend health verification.
4. Frontend deployment and frontend health verification.
5. Publish `Production Deployment` check-run.

This insertion point keeps promotion deterministic by applying schema changes before app versions that require them.

## Verification Output Expectations

Workflow summary and check-run output include:

- environment (`production`)
- source commit SHA
- immutable artifact references
- UTC deployment timestamp
- backend/frontend healthcheck endpoints
- overall verification result (`PASS`/`FAIL`)

## Rollback Guidance

If production health checks fail after deployment:

1. Identify the last known-good `source_sha` from successful `Production Deployment` checks.
2. Re-run `CD Production Promote` with that known-good `source_sha`.
3. Re-run health probes for backend then frontend.
4. Keep failed deployment metadata for audit history; do not retag mutable channels as rollback mechanism.

## Migration Rollback Policy (Future Phase)

If migration execution is enabled in a later phase:

1. Migration failure aborts production deployment before backend/frontend deploy steps.
2. If app deploy fails after successful migration, redeploy last known-good app artifact compatible with the migrated schema.
3. Down migrations are manual-only, require change approval, and must use a tested migration-specific rollback script.
4. Keep migration and deployment metadata immutable for auditability and post-incident analysis.
