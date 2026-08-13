# Post-MVP V2 Epic 11 Phase 0 — Baseline Audit

**Epic:** v2-11 Security & Governance
**Phase:** 0 — Baseline Audit
**Date:** 2026-08-13
**Auditor:** AI Agent
**Status:** Complete
**Validation base:** current workspace state, Epic 10 completion baseline, repo execution evidence

---

## Executive Summary

This audit establishes the verified baseline before implementing Epic 11 (Security & Governance). The repo is already in a mature Epic 10 state: background jobs, HITL, workflow, plugin, and observability scaffolding are present and stable. The security-specific package, feature flags, migration head, and RBAC/audit/guardrail infrastructure are all still absent, which is the expected Phase 1+ boundary.

Key findings:

- ✅ Background jobs and Epic 10 foundation are present and active.
- ✅ The platform is operational and already contains the extension points that Epic 11 must wire into.
- ✅ No `backend-python/app/ai/security/` package exists yet.
- ✅ No `SECURITY_GOVERNANCE_ENABLED` flag exists yet.
- ✅ Alembic head is `0015_document_upload_staging`; Epic 11 will introduce the next numbered migration(s) after this baseline.
- ✅ Lint and coverage gates are passing in the current workspace.
- ⚠️ `pyright` is blocked in this environment because its bootstrap step tries to download Node.js and receives an HTTP 403, so full typecheck validation is infrastructure-blocked rather than code-blocked.
- ✅ Phase 0 is complete as a baseline audit and is ready for explicit Phase 1 authorization.

---

## 1. Current Epic 10 State

The repo currently contains the expected Epic 10 implementation surface:

- `backend-python/app/ai/jobs/` exists and is wired through the jobs REST API.
- `backend-python/app/core/config.py` already contains `background_jobs_enabled` and the associated background jobs settings.
- `backend-python/app/routers/jobs.py` exists and enforces a feature-flag gate around the jobs API.
- `backend-python/alembic/versions/0015_document_upload_staging.py` is the current migration head.

This means the project is not at a pre-Epic-10 baseline; it is at the post-Epic-10 implementation baseline required for Epic 11 planning.

---

## 2. Security & Governance Baseline Status

### 2.1 Missing Security Package

The following security package path does not exist in the current codebase:

- `backend-python/app/ai/security/`

This is a required Phase 1+ artifact and is not present before implementation.

### 2.2 Missing Feature Flags

The expected Epic 11 master flag and RBAC/security sub-flags are not present in current config:

- `SECURITY_GOVERNANCE_ENABLED`
- `security_rbac_enforcement_enabled`
- `security_audit_log_enabled`
- `security_guardrails_enabled`
- `security_rate_limit_extensions_enabled`
- `security_bootstrap_admin_emails`

The current config remains the Epic 10 pattern. The security governance additions are intentionally absent.

### 2.3 Missing Security Migration Head

The current Alembic head is:

- `0015_document_upload_staging`

No Epic 11 migration exists yet. The next available revision numbers remain the expected next migration slots, consistent with the plan’s `0016`/`0017` numbering guidance.

---

## 3. Extension Point Inventory

The Phase 0 audit verified the exact extension points Epic 11 must wire into without redesigning unrelated logic.

### 3.1 Tool authorization

File: `backend-python/app/ai/tools/authorizer.py`

Current state:

- `ToolAuthorizer.authorize()` is intentionally minimal.
- It currently only permits authenticated callers and denies guests.
- This is the exact current authenticated-only baseline that Epic 11 will replace with RBAC-aware enforcement behind feature flags.

### 3.2 HITL rules and approval metadata

Files:

- `backend-python/app/ai/hitl/rules.py`
- `backend-python/app/ai/hitl/models.py`
- `backend-python/app/ai/hitl/service.py`

Current state:

- `PolicyContext.caller_role` is documented as intentionally limited to caller kind until Epic 11 real RBAC arrives.
- `ApprovalRule.required_stages` is present and recorded, but the model comment explicitly states that real per-role identity enforcement is deferred to Epic 11 RBAC.
- The `StageDecision` model exists as the multi-stage checklist scaffold.
- `AgentApprovalService` already supports the stage checklist pattern; it is the exact Epic 11 enforcement surface.

This is the primary closure target for Epic 11 Phase 2+ work.

### 3.3 Jobs API visibility and gating

File: `backend-python/app/routers/jobs.py`

Current state:

- The jobs routes are authenticated-only and feature-flag-gated by `background_jobs_enabled`.
- There is no RBAC-aware permission check such as `jobs:view_all` / `jobs:retry` yet.
- This is an exact Epic 11 Phase 2 integration point.

### 3.4 MCP credential resolution

File: `backend-python/app/ai/mcp/auth.py`

Current state:

- `resolve_credential_env_vars()` reads from `os.environ` directly.
- This is the exact contract Epic 11 will later wrap behind a `SecretResolver` abstraction.
- The model already masks secret values in serialization, but the actual environment resolution is still direct and unabstracted.

### 3.5 Rate limiting design

File: `backend-python/app/middleware/rate_limit.py`

Current state:

- `SlidingWindowRateLimiter` uses the in-memory anonymous/authenticated bucket model.
- `resolve_rate_limit_identity()` clearly distinguishes authenticated vs anonymous callers.
- This is the immediate foundation for Epic 11 role-aware rate-limit extension work.

### 3.6 Redaction/consolidation scope

The plan calls out several geographically separate redaction implementations that will later be consolidated. They are present in the current repo:

- `backend-python/app/core/logging.py`
- `backend-python/app/ai/mcp/auth.py`
- `backend-python/app/ai/hitl/models.py`
- `backend-python/app/schemas/jobs.py`

These are the expected Phase 4 consolidation targets.

---

## 4. Verified Quality Gates

### 4.1 Lint

Command run:

```bash
cd backend-python && source .venv/bin/activate && make lint
```

Observed result:

- `ruff check app tests` completed successfully.
- Output reported: `All checks passed!`

### 4.2 Test coverage

Command run:

```bash
cd backend-python && source .venv/bin/activate && make test-cov
```

Observed result:

- pytest completed successfully.
- Coverage report was emitted and the command succeeded without a failing exit.
- The run exceeded the repo’s required minimum coverage threshold.

### 4.3 Typecheck status

Command run:

```bash
cd backend-python && source .venv/bin/activate && make typecheck
```

Observed result:

- It did not complete successfully in this environment.
- The failure was due to `pyright` trying to bootstrap Node.js via `nodeenv`, which then failed with an HTTP 403 while downloading Node.
- This is an environment/toolchain issue, not a Python code error in the project itself.

This is important for the audit record: the code baseline is healthy, but one local typecheck gate is blocked by the sandboxed environment. The Epic 11 work itself should not be conflated with that infrastructure issue.

---

## 5. Architecture Review

### 5.1 Database

The current migration head and schema state are consistent with an Epic 10-complete baseline:

- `0015_document_upload_staging` is the current migration head.
- `users` table does not include the planned RBAC role/permission columns in the current repo baseline.

### 5.2 Security package absence

The following product boundary is still absent and must be created during Epic 11 implementation:

- `backend-python/app/ai/security/`
- Security router and health integration
- RBAC store/service implementation
- Audit logger and retention cleanup
- Guardrail engine and rule engine extraction
- Secret resolver abstraction

### 5.3 Dependency review

The repo does not currently include an RBAC/authorization dependency such as Casbin or Oso, which matches the locked decision in the Part I design: the platform will use hand-rolled RBAC logic rather than adding an external policy library.

---

## 6. Final Phase 0 Conclusion

Baseline audit complete.

Verified facts:

- Epic 10 implementation is present in the repo.
- Security & Governance is not implemented yet.
- The required extension points are identified and verified.
- The feature flags and package boundaries for Epic 11 are absent.
- Lint and coverage validation are green in the workspace.
- Full typecheck is blocked by an external Node bootstrap failure, not by app code defects.

This satisfies the Phase 0 objective and is the required gate before starting Phase 1.

**Recommended next action:** explicit user authorization to begin Phase 1 (RBAC Domain Model, Migration & Bootstrap).
