# Documentation Index

Navigation for the **Fullstack AI Platform** repository. Start at the root [README](../README.md) for setup; use this index to find deeper material.

## Architecture

| Document | Description |
| -------- | ----------- |
| [architecture/system-overview.md](architecture/system-overview.md) | Layered architecture, Mermaid diagram, package map, happy-path sequence |
| [architecture/system-overview.svg](architecture/system-overview.svg) | Static diagram export |

## Releases

User-facing release summaries (sources for [CHANGELOG.md](../CHANGELOG.md)):

| Release | Summary |
| ------- | ------- |
| Post-MVP V1 | [post-mvp-v1-release-summary.md](releases/post-mvp-v1-release-summary.md) |
| Post-MVP V1.1 | [post-mvp-v1.1-release-summary.md](releases/post-mvp-v1.1-release-summary.md) |
| Post-MVP V1.1.1 | [post-mvp-v1.1.1-release-summary.md](releases/post-mvp-v1.1.1-release-summary.md) |
| V2 Epic 01 — Agent | [post-mvp-v2-epic1-release-summary.md](releases/post-mvp-v2-epic1-release-summary.md) |
| V2 Epic 02 — Advanced RAG | [post-mvp-v2-epic2-release-summary.md](releases/post-mvp-v2-epic2-release-summary.md) |
| V2 Epic 03 — MCP | [post-mvp-v2-epic3-release-summary.md](releases/post-mvp-v2-epic3-release-summary.md) |
| V2 Epic 04 — Voice | [post-mvp-v2-epic4-release-summary.md](releases/post-mvp-v2-epic4-release-summary.md) |

## Plans

Implementation plans and program guides in [plans/](plans/). Highlights:

| Document | Topic |
| -------- | ----- |
| [plans/mvp-completion-implementation-plan.md](plans/mvp-completion-implementation-plan.md) | MVP engineering track |
| [plans/post-mvp-v1-implementation-plan.md](plans/post-mvp-v1-implementation-plan.md) | Knowledge platform and RAG |
| [plans/_program-v2-execution-guide.md](plans/_program-v2-execution-guide.md) | V2 epic orchestration |
| [plans/post-mvp-v2-epic-04-voice-interfaces.md](plans/post-mvp-v2-epic-04-voice-interfaces.md) | Voice interfaces (latest epic) |
| [plans/public-release-readiness-implementation-plan.md](plans/public-release-readiness-implementation-plan.md) | Public release documentation track |

## Ops

| Document | Description |
| -------- | ----------- |
| [ops/public-demo-protection.md](ops/public-demo-protection.md) | Guest quotas, `DEMO_MODE_STRICT`, and public demo cost controls |
| [../CD_STAGING.md](../CD_STAGING.md) | Staging CD contract |
| [../CD_PRODUCTION.md](../CD_PRODUCTION.md) | Production promotion contract |
| [ci-image-tagging.md](ci-image-tagging.md) | GHCR image tagging convention |

## Audits

Phase 0 baseline audits and epic completion records in [audits/](audits/). These are engineering history, not required for first-time setup.

| Document | Topic |
| -------- | ----- |
| [audits/public-release-readiness-phase-0-baseline-audit.md](audits/public-release-readiness-phase-0-baseline-audit.md) | Public release baseline audit |

## Tech references

| Document | Description |
| -------- | ----------- |
| [tech-references/local-google-oauth.md](tech-references/local-google-oauth.md) | Debugging Google sign-in on localhost |
| [tech-references/github-actions-ci-implementation.md](tech-references/github-actions-ci-implementation.md) | CI workflow design notes |
| [tech-references/staging-hosting-setup-vercel-railway-render.md](tech-references/staging-hosting-setup-vercel-railway-render.md) | Staging hosting setup |

## App READMEs

| Path | Scope |
| ---- | ----- |
| [backend-python/README.md](../backend-python/README.md) | API routes, feature flags, eval CLI |
| [frontend/README.md](../frontend/README.md) | Frontend dev guide |
| [backend-nodejs/README.md](../backend-nodejs/README.md) | Reference Node backend (paused) |
