# Post-MVP V2 Epic 11 Release Summary

**Release name:** Post-MVP V2 Epic 11 — Security & Governance (Phases 0–12)
**Release date:** 2026-08-21
**Validation:** Phase 12 final acceptance
**Git commit (validation base):** `3d2a9e9` — Phase 11 Security & Governance dashboard

---

## Summary

Epic 11 adds a feature-flagged security control layer: global RBAC, durable audit events, environment-backed secret resolution, shared redaction and policy primitives, heuristic guardrails, role-aware rate limits and quotas, security observability, REST administration, evaluation scenarios, and a frontend dashboard.

`SECURITY_GOVERNANCE_ENABLED` remains off by default. RBAC, audit logging, guardrails, and rate-limit extensions can be disabled independently while the master flag is on.

## Delivered

| Area          | Delivery                                                                                                      |
| ------------- | ------------------------------------------------------------------------------------------------------------- |
| RBAC          | Four system roles, permission registry, implicit member baseline, admin bootstrap, tool/HITL/jobs enforcement |
| Audit         | Canonical action taxonomy, trace-correlated durable events, retention cleanup job                             |
| Secrets       | `SecretResolver` protocol and env-backed implementation used by MCP credentials                               |
| Guardrails    | Shared rule engine plus RAG, tool-argument, and MCP-result scanning                                           |
| Limits        | Role multipliers, four per-minute buckets, and daily usage quota counters                                     |
| API/UI        | Role management, audit query, policy summary, health fields, and `/security` dashboard                        |
| Observability | Authorization, guardrail, role-assignment, and audit spans/metrics                                            |
| Evaluation    | Six deterministic security scenarios plus adversarial and concurrency coverage                                |

## Migrations

- `0016_security_rbac`
- `0017_security_audit_log`
- `0018_usage_quota_counters`

Run `make db-migrate` from `backend-python/` before enabling the feature.

## Closed Deferred Gaps

| Prior epic | Closure                                                                                      |
| ---------- | -------------------------------------------------------------------------------------------- |
| Epic 01    | Destructive-tool RBAC and tool invocation limits                                             |
| Epic 03    | MCP secret-resolution abstraction, invocation limits, permission auditing, result guardrails |
| Epic 06    | Unified RBAC-aware approval policy context                                                   |
| Epic 07    | Audit-to-trace correlation and security telemetry                                            |
| Epic 08    | Stable reserved plugin/workflow/MCP administration permissions and governance framing        |
| Epic 09    | Per-stage reviewer permission enforcement and genuine revocation race coverage               |
| Epic 10    | Jobs visibility/retry RBAC, enqueue limits, and audit retention handler                      |

## Configuration

All settings are documented in `backend-python/.env.example`. Bootstrap the first owner with `SECURITY_BOOTSTRAP_ADMIN_EMAILS`, restart the API, then assign ongoing roles through `/api/security/users/{user_id}/roles` or the Security dashboard.

**Rollback:** set `SECURITY_GOVERNANCE_ENABLED=false` and redeploy. Migrations are additive and unused while disabled.

## Breaking Changes

None while the master flag remains disabled. When RBAC enforcement is enabled, member-only users are intentionally denied destructive tools and Jobs administration endpoints.

## Verification

| Gate                                      | Result                                                           |
| ----------------------------------------- | ---------------------------------------------------------------- |
| Backend lint, format, typecheck           | Clean                                                            |
| Backend flag-off coverage suite           | 2,287 passed; 88.84% overall coverage                            |
| `app/ai/security/` coverage               | 88.68%                                                           |
| RBAC disabled full suite                  | 2,287 passed                                                     |
| Audit disabled full suite                 | 2,287 passed                                                     |
| Guardrails disabled full suite            | 2,287 passed                                                     |
| Rate-limit extensions disabled full suite | 2,287 passed                                                     |
| Standard eval                             | 15/15 passed                                                     |
| Security eval                             | 6/6 passed                                                       |
| Frontend tests                            | 326 passed across 54 files                                       |
| Frontend format, lint, build              | Clean; production build emitted the existing bundle-size warning |

Validation required stopping the local development API because its enabled Background Jobs worker shared the test database and consumed test jobs. Test dependency caches were also isolated so cached audit sessionmakers cannot cross pytest event loops.

## References

- Epic plan: [post-mvp-v2-epic-11-security-and-governance.md](../plans/post-mvp-v2-epic-11-security-and-governance.md)
- Phase 0 audit: [post-mvp-v2-epic11-phase-0-baseline-audit.md](../audits/post-mvp-v2-epic11-phase-0-baseline-audit.md)
- Prior release: [post-mvp-v2-epic10-release-summary.md](./post-mvp-v2-epic10-release-summary.md)
