# DevOps Implementation Plan

## 1) Goal and Scope

### Objective

Establish a low-risk, incremental DevOps baseline for this monorepo in four ordered stages:

1. Pre-commit hooks
2. Dockerization
3. GitHub Actions CI
4. Continuous Delivery (CD)

### In Scope

- Pre-commit quality gates for `frontend/`, `backend-nodejs/`, and `backend-python/`
- Container images for each runnable app
- GitHub Actions workflows for lint, test, build, and image publish
- CD flow for staged deployment with rollback playbooks
- CI/CD decisions compatible with upcoming database phase

### Out of Scope (Current Phase)

- Kubernetes platform rollout
- Full security program (SAST/SCA/image scan enforcement) on day one
- Database migration runtime implementation
- Feature-level app refactoring unrelated to DevOps enablement

### Assumptions

- Development model remains trunk-based with PRs merged to protected `main`
- Existing local commands remain source of truth for quality checks
- Deploy targets continue to be managed services (frontend static host + backend web service host)
- Secrets are never committed and are injected by runtime/CI secret stores

### Constraints

- Minimize local developer friction
- Keep setup realistic for a solo portfolio project
- Preserve production deploy checks for the Python backend (Node backend is paused until post-MVP)
- Keep growth path explicit for database and security maturity

## 2) Baseline and Gap Analysis

### Current State Signals to Verify First

- [x] Monorepo app boundaries are clear: `frontend/`, `backend-nodejs/`, `backend-python/`
- [x] Quality scripts exist per app:
- [x] `frontend`: `lint`, `test`, `build`, `format:check`
- [x] `backend-nodejs`: `lint`, `test`, `build`, `format:check`
- [x] `backend-python`: `make lint`, `make format-check`, `make typecheck`, `make test-cov` (80% coverage on `app/`)
- [x] Env templates exist:
- [x] `frontend/.env.example`
- [x] `backend-nodejs/.env.example`
- [x] `backend-python/.env.example`
- [x] GitHub Actions workflows present (`.github/workflows/pr-quality.yml`, CD workflows)
- [x] No current Dockerfiles or Compose files
- [x] Deployment is currently manual (documented runbook)

### Key Gaps

- [x] No standardized pre-commit enforcement
- [ ] No containerized local parity workflow
- [x] CI checks as required PR merge gates (path-filtered PR Quality Checks)
- [ ] No automated artifact build/publish pipeline
- [ ] No formal CD promotion workflow
- [ ] No provenance/tagging strategy for release artifacts

### Unknowns to Resolve Before Execution

- [ ] Final hosting targets and accounts for each app (staging/prod)
- [x] MVP decision: Python backend is the default deployment target; Node backend deployment is deferred until post-MVP
- [ ] Registry choice for images (GHCR recommended baseline)
- [ ] Branch protection required checks naming convention
- [ ] Environments policy: auto-deploy to staging vs manual approval
- [ ] Future database hosting choice (affects network, secrets, migration jobs)

## 3) Stage Map (High-Level)

### Stage A: Pre-Commit Hooks

- [x] Add fast local quality gates and consistent formatting
- [x] Keep hooks lightweight to maximize adoption

### Stage B: Dockerization

- [ ] Add per-app production-oriented Dockerfiles
- [ ] Add local orchestration for integrated smoke validation

### Stage C: CI (GitHub Actions)

- [ ] Add PR and push workflows with path filters
- [ ] Enforce required checks before merge to `main`
- [ ] Publish versioned images from trusted branch/tag events

### Stage D: CD

- [ ] Add staged deployment flow (dev/staging/prod)
- [ ] Add post-deploy health checks and rollback procedure

### Rationale for Ordering

- [ ] Hooks first: immediate feedback and lower CI noise
- [ ] Docker second: deterministic build/runtime baseline for CI and CD
- [ ] CI third: automate quality and artifact production
- [ ] CD last: automate release only after quality and artifacts are stable

## 4) Fine-Grained Phases (Execution Plan)

### Stage A — Pre-Commit Hooks

#### Phase A1 — Tooling Selection and Config Bootstrap

- Phase ID and name: `A1 - Hook Framework Baseline`
- Objective: Introduce a single hook framework and shared repo policy
- Exact directories affected: repository root, `frontend/`, `backend-nodejs/`, `backend-python/`
- Concrete tasks (checklist):
- [x] Choose `pre-commit` as orchestration layer
- [x] Define fast hooks only for first pass (format/lint/type-safe checks where cheap)
- [x] Add hook install instructions to root README
- Deliverables/artifacts:
- [x] `.pre-commit-config.yaml`
- [x] Updated developer onboarding section in root docs
- Dependencies/prerequisites:
- [x] Python available for `pre-commit`
- Acceptance criteria (verifiable):
- [x] `pre-commit run --all-files` passes on a clean branch
- Validation commands/tests:
- [x] `pre-commit --version`
- [x] `pre-commit run --all-files`
- Rollback strategy:
- [x] Revert hook config commit and remove git hook installation
- Estimated effort (S/M/L): `S`
- Risk level (Low/Med/High): `Low`

#### Phase A2 — App-Specific Fast Hook Wiring

- Phase ID and name: `A2 - Fast Quality Hooks by Area`
- Objective: Wire each app to fast, deterministic checks
- Exact directories affected: `frontend/`, `backend-nodejs/`, `backend-python/`
- Concrete tasks (checklist):
- [x] Frontend hook: changed-file formatting and ESLint on staged files
- [x] Node backend hook: changed-file formatting and ESLint on staged files
- [x] Python backend hook: `ruff check` and `black --check` on changed files
- [x] Add safe exclusions for generated/build outputs
- Deliverables/artifacts:
- [x] Hook entries per project area
- [x] Hook runtime < 20 seconds on typical staged diff
- Dependencies/prerequisites:
- [x] Dependencies installed per app (`npm install`, `uv sync`)
- Acceptance criteria (verifiable):
- [x] Intentionally malformed staged file is blocked with clear error
- [x] Auto-fix hooks rewrite formatting where allowed
- Validation commands/tests:
- [x] `git add <test-file> && pre-commit run`
- [x] `pre-commit run --all-files`
- Rollback strategy:
- [x] Temporarily switch slow/failing hook to `manual` stage or remove from required set
- Estimated effort (S/M/L): `M`
- Risk level (Low/Med/High): `Low`

#### Phase A3 — Policy Finalization

- Phase ID and name: `A3 - Hook Policy and Bypass Rules`
- Objective: Prevent friction while preserving quality floor
- Exact directories affected: root docs and contribution guidance
- Concrete tasks (checklist):
- [x] Document fast vs slow hook policy
- [x] Define bypass policy (`--no-verify`) as exceptional and tracked in PR notes
- [x] Add troubleshooting for missing runtimes/dependencies
- Deliverables/artifacts:
- [x] Contributor policy section in docs
- Dependencies/prerequisites:
- [x] A1 and A2 completed
- Acceptance criteria (verifiable):
- [x] New contributor can install and run hooks in under 10 minutes
- Validation commands/tests:
- [x] Fresh clone onboarding dry run
- Rollback strategy:
- [x] Keep policy docs but disable problematic hook subset temporarily
- Estimated effort (S/M/L): `S`
- Risk level (Low/Med/High): `Low`

### Stage B — Dockerization

#### Phase B1 — Per-App Dockerfiles

- Phase ID and name: `B1 - Runtime Images per Service`
- Objective: Provide production-oriented images for each app
- Exact directories affected: `frontend/`, `backend-nodejs/`, `backend-python/`
- Concrete tasks (checklist):
- [x] Add multi-stage Dockerfile for frontend build + static serve
- [x] Add multi-stage Dockerfile for Node backend (build TS then run JS)
- [x] Add Dockerfile for Python backend (`uv` sync, non-root runtime)
- [x] Add `.dockerignore` files for all apps
- Deliverables/artifacts:
- [x] Three buildable images
- [x] Documented runtime ports and health endpoints
- Dependencies/prerequisites:
- [x] App build/test scripts green locally
- Acceptance criteria (verifiable):
- [x] Each image builds without secrets baked in
- [x] Containers answer health/readiness endpoint checks
- Validation commands/tests:
- [x] `docker build -t chatbot-frontend:dev frontend`
- [x] `docker build -t chatbot-backend-node:dev backend-nodejs`
- [x] `docker build -t chatbot-backend-python:dev backend-python`
- Rollback strategy:
- [ ] Revert individual Dockerfile and isolate to unaffected services
- Estimated effort (S/M/L): `M`
- Risk level (Low/Med/High): `Med`

#### Phase B2 — Local Compose Orchestration

- Phase ID and name: `B2 - Integrated Local Stack`
- Objective: Standardize local multi-service runs and smoke tests
- Exact directories affected: repository root and app env docs
- Concrete tasks (checklist):
- [x] Add `docker-compose.yml` for frontend + selected backend profile
- [x] Support backend profile switch (`python` or `nodejs`)
- [x] Inject env via `.env` files and compose overrides
- [x] Add smoke test checklist using health/chat endpoints
- Deliverables/artifacts:
- [x] Working Compose workflow for integrated startup
- [x] Profile-driven backend selection
- Dependencies/prerequisites:
- [x] B1 complete
- Acceptance criteria (verifiable):
- [x] `docker compose up` runs full stack with frontend reachable
- [x] Backend health endpoint responds and frontend can send chat request
- Validation commands/tests:
- [x] `docker compose config`
- [x] `docker compose up --build`
- [x] Manual smoke: frontend chat request and backend `/api/health`
- Rollback strategy:
- [ ] Revert compose changes while preserving standalone Dockerfiles
- Estimated effort (S/M/L): `M`
- Risk level (Low/Med/High): `Med`

#### Phase B3 — Hardening and Size Pass

- Phase ID and name: `B3 - Image Hardening Baseline`
- Objective: Reduce image size and runtime risk
- Exact directories affected: all Dockerfiles
- Concrete tasks (checklist):
- [x] Use slim base images and pinned majors
- [x] Run as non-root user
- [x] Remove build toolchains from runtime layers
- [x] Add explicit healthcheck instructions where appropriate
- Deliverables/artifacts:
- [x] Hardened image baseline checklist signed off
- Dependencies/prerequisites:
- [x] B1 complete
- Acceptance criteria (verifiable):
- [x] Runtime images smaller than first draft and pass smoke tests
- Validation commands/tests:
- [x] `docker image ls`
- [x] `docker run` health checks per image
- Rollback strategy:
- [ ] Keep previous working image tags and revert optimization commit
- Estimated effort (S/M/L): `S`
- Risk level (Low/Med/High): `Low`

### Stage C — GitHub Actions CI

#### Phase C1 — PR Quality Workflow

- Phase ID and name: `C1 - Monorepo PR Checks`
- Objective: Enforce lint/test/build checks with path filters
- Exact directories affected: `.github/workflows/`, `frontend/`, `backend-nodejs/`, `backend-python/`
- Concrete tasks (checklist):
- [x] Create PR workflow with jobs split by app area
- [x] Add path filters so only impacted app jobs run
- [x] Add dependency caching (`npm`, `uv`)
- [x] Ensure Python and Node versions are pinned in workflow
- Deliverables/artifacts:
- [x] CI workflow for pull requests
- [x] Named checks suitable for branch protection
- Dependencies/prerequisites:
- [x] Stage A complete
- Acceptance criteria (verifiable):
- [x] PR touching only `frontend/` does not trigger backend jobs
- [x] PR checks fail on lint/test regressions
- Validation commands/tests:
- [x] Open test PRs with isolated path changes
- [x] Confirm check names and pass/fail behavior in GitHub UI
- Rollback strategy:
- [x] Disable problematic workflow path via temporary workflow dispatch/manual-only trigger
- Estimated effort (S/M/L): `M`
- Risk level (Low/Med/High): `Med`

**MVP completion update (2026-07-19):** The `backend-python-pr` job now runs, in order: `make lint`, `make format-check`, `make typecheck`, and `make test-cov` (pytest with `--cov-fail-under=80` on `app/`). Frontend and Node jobs include `npm run format:check`. See root `README.md` → PR Quality Gates.

#### Phase C2 — Main Branch Build and Artifact Workflow

- Phase ID and name: `C2 - Build and Image Publish`
- Objective: Build container artifacts from trusted branch/tag events
- Exact directories affected: `.github/workflows/`, Docker-related paths
- Concrete tasks (checklist):
- [x] Add workflow for push to `main` and release tags
- [x] Build images for changed apps and push to GHCR
- [x] Apply immutable tags (`sha`) and mutable channel tags (`main`, `staging`, `prod`)
- [x] Capture build metadata for provenance baseline
- Deliverables/artifacts:
- [x] Published images in registry
- [x] Standardized tag convention documentation
- Dependencies/prerequisites:
- [x] B1 complete
- [x] C1 stable
- Acceptance criteria (verifiable):
- [x] Push to `main` publishes expected tags for changed services
- Validation commands/tests:
- [x] Inspect GHCR packages/tags after workflow run
- Rollback strategy:
- [x] Revert workflow publish step; continue running quality-only CI
- Estimated effort (S/M/L): `M`
- Risk level (Low/Med/High): `Med`

#### Phase C3 — CI Governance and Required Checks

- Phase ID and name: `C3 - Branch Protection Enforcement`
- Objective: Ensure `main` remains releasable
- Exact directories affected: repository settings and docs
- Concrete tasks (checklist):
- [x] Configure required status checks from C1 jobs
- [x] Require up-to-date branch before merge
- [x] Enable linear history or squash strategy as preferred
- [x] Document expected PR checklist
- Deliverables/artifacts:
- [x] Branch protection policy and PR quality gates
- Dependencies/prerequisites:
- [x] C1 check names finalized
- Acceptance criteria (verifiable):
- [x] Direct merge blocked when required checks fail
- Validation commands/tests:
- [x] Create failing PR and verify merge restriction
- Rollback strategy:
- [x] Temporarily reduce required checks to unblock urgent fix
- Estimated effort (S/M/L): `S`
- Risk level (Low/Med/High): `Low`

### Stage D — Continuous Delivery (CD)

#### Phase D1 — Staging Deployment Automation

- Phase ID and name: `D1 - Auto Deploy to Staging`
- Objective: Deploy from `main` artifacts into staging automatically
- Exact directories affected: `.github/workflows/`, deployment docs, provider configs
- Concrete tasks (checklist):
- [x] Add deploy workflow consuming published image tags
- [x] Configure staging secrets and environment variables
- [x] Add post-deploy health checks for frontend and backend endpoints
- [x] Report deployment status back to GitHub checks
- Deliverables/artifacts:
- [x] Reproducible staging deployment pipeline
- Dependencies/prerequisites:
- [x] C2 complete
- Acceptance criteria (verifiable):
- [x] Merge to `main` deploys staging and health checks pass
- Validation commands/tests:
- [x] Workflow run logs with successful health probes
- [x] Manual sanity chat through staging frontend
- Rollback strategy:
- [x] Redeploy previous known-good image tag
- Estimated effort (S/M/L): `M`
- Risk level (Low/Med/High): `Med`

#### Phase D2 — Production Promotion with Approval Gate

- Phase ID and name: `D2 - Controlled Production Promotion`
- Objective: Promote known-good staging artifact to production safely
- Exact directories affected: `.github/workflows/`, GitHub Environments settings, deployment docs
- Concrete tasks (checklist):
- [x] Add manual approval step for production environment
- [x] Promote immutable image tag from staging-tested artifact
- [x] Run post-deploy health and smoke tests
- [x] Record release notes with artifact/tag mapping
- Deliverables/artifacts:
- [x] Production promotion workflow with auditable approval
- Dependencies/prerequisites:
- [x] D1 stable
- Acceptance criteria (verifiable):
- [x] Production deploy requires approval and publishes successful verification output
- Validation commands/tests:
- [x] Trigger manual promotion and verify checks
- [x] Validate `/api/health` and frontend chat on production URL
- Rollback strategy:
- [x] One-click redeploy to previous image digest/tag
- Estimated effort (S/M/L): `M`
- Risk level (Low/Med/High): `Med`

#### Phase D3 — Database-Ready CD Extension Points

- Phase ID and name: `D3 - Future DB Compatibility Layer`
- Objective: Prepare CD for upcoming database phase without implementing DB now
- Exact directories affected: deploy workflow docs, environment variable matrix
- Concrete tasks (checklist):
- [x] Reserve migration job stage in CD workflow (disabled/no-op initially)
- [x] Define DB secret names and environment contract
- [x] Define deployment order for app + migration in future phase
- Deliverables/artifacts:
- [x] Forward-compatible CD blueprint for DB migration rollout
- Dependencies/prerequisites:
- [x] D1 and D2 done
- Acceptance criteria (verifiable):
- [x] CD docs include explicit migration insertion point and rollback policy
- Validation commands/tests:
- [x] Documentation review checklist completed
- Rollback strategy:
- [x] Keep migration stage disabled until database phase starts
- Estimated effort (S/M/L): `S`
- Risk level (Low/Med/High): `Low`

## 5) CI/CD Architecture Decisions

### Branch Strategy Assumptions

- `main` is always releasable
- All changes enter via PR with required checks
- Feature branches are short-lived and rebased/updated before merge

### Environments

- `dev`: local and optional preview builds
- `staging`: automatic deploy from `main`
- `prod`: manual promotion from a staging-validated artifact

### Secrets Management Approach

- Use GitHub Environments and deployment platform secret stores
- No secrets in image layers, Git history, or workflow logs
- Separate secret sets for staging and production

### Build vs Deploy Separation

- CI builds and tests artifacts once
- CD deploys immutable artifact references (no rebuild on deploy)

### Required Status Checks and Merge Gates

- Frontend CI checks: lint, test, build
- Node backend CI checks: lint, test, build
- Python backend CI checks: lint, test
- Workflow-level check for changed paths only

### Artifact Strategy

- Registry: GHCR baseline
- Tags: `sha-<shortsha>` immutable, plus channel tags (`main`, `staging`, `prod`)
- Keep deploy history with artifact digest references
- Add provenance/SBOM later as maturity upgrade

### Incremental Security/Compliance Maturity Roadmap

- In scope now:
- [ ] Secret handling hygiene
- [ ] Pinned action versions
- [ ] Basic dependency caching and deterministic builds
- Later phases:
- [ ] SAST (CodeQL or equivalent)
- [ ] SCA and license policy checks
- [ ] Container vulnerability scanning gates
- [ ] Artifact signing and provenance attestation

## 6) Dockerization Strategy

### Image Strategy per App

- `frontend/`: static asset build image + lightweight serving runtime
- `backend-nodejs/`: build TypeScript in builder stage, run compiled app in runtime stage
- `backend-python/`: install dependencies with `uv`, run FastAPI via uvicorn in minimal runtime
- Keep Dockerfiles per-service (not monorepo single image)

### Multi-Stage Build Approach

- Builder stage contains compilers/toolchains
- Runtime stage contains only runtime dependencies and app artifacts
- Use explicit working directories and lockfile-based installs where available

### Local Orchestration and Developer Workflow

- Compose file at repo root with profiles to choose Python or Node backend
- Developer path:
- [ ] Build images
- [ ] `docker compose up`
- [ ] Run smoke checks (`/api/health`, chat request)
- [ ] Stop and iterate app-specific container changes

### Runtime Configuration and Env Injection

- Inject runtime env via compose and hosting platform settings
- Keep `.env.example` as contract only
- Distinguish frontend build-time var (`VITE_API_BASE_URL`) from backend runtime secrets

### Security and Size Optimization Checklist

- [ ] Non-root user in runtime containers
- [ ] Minimal base images
- [ ] `.dockerignore` excludes source control and test artifacts where appropriate
- [ ] No secrets copied into image
- [ ] Health endpoint support for readiness checks

## 7) Pre-Commit Design

### Hook Framework Recommendation

- Recommendation: `pre-commit`
- Why: language-agnostic orchestration for Python and Node tasks in one repo, easy install, reproducible hook versions

### Hook List by Repo Area

- `frontend/`:
- [ ] Prettier check or staged auto-fix
- [ ] ESLint on changed files
- `backend-nodejs/`:
- [ ] Prettier check or staged auto-fix
- [ ] ESLint on changed files
- `backend-python/`:
- [ ] `ruff check`
- [ ] `black --check`

### Fast vs Slow Hooks Policy

- Fast hooks run on every commit (format/lint basic checks)
- Slow hooks (full test suites) run in CI and optional manual pre-push

### Auto-Fix vs Blocking Policy

- Auto-fix allowed for formatting
- Blocking for lint/type/test failures that cannot be auto-fixed safely

### Developer Onboarding and Bypass Policy

- Onboarding:
- [ ] Install dependencies
- [ ] `pre-commit install`
- [ ] `pre-commit run --all-files`
- Bypass:
- [ ] `--no-verify` allowed only for urgent cases with PR explanation and follow-up fix

## 8) GitHub Actions Plan

### Workflow Split

- `ci-pr.yml`: lint/test/build with path filters
- `ci-main-images.yml`: build and publish images
- `cd-staging.yml`: deploy staging from immutable tags
- `cd-prod.yml`: manual promotion to production
- Optional later: `security.yml` for SAST/SCA/image scan

### Trigger Matrix

- PR: run `ci-pr.yml`
- Push to `main`: run image build/publish and staging deploy
- Tag/release: optional release-specific publish
- Manual dispatch: production promotion and emergency rollback workflows

### Caching Strategy

- Node: npm cache keyed by lockfiles
- Python: `uv`/pip cache keyed by lockfile and Python version
- Docker: layer cache via Buildx cache backend

### Path Filters in Monorepo

- Frontend jobs trigger on `frontend/**`
- Node backend jobs trigger on `backend-nodejs/**`
- Python backend jobs trigger on `backend-python/**`
- Shared docs-only changes do not trigger heavy build jobs

### Parallelization Strategy

- Run per-app jobs in parallel on PR
- Build/publish jobs parallelized by changed service
- Deploy jobs serialized by environment to avoid overlap

### Required Checks for PR Merge

- Required checks mapped to app areas and always include any shared workflow checks
- Protection policy blocks merge until all required checks are green

## 9) CD Plan

### Deployment Targets and Promotion Flow

- Frontend target: static hosting platform environment
- Backend target: managed web service environment
- Flow: `main` commit -> staging deploy -> manual promote same artifact to prod

### Environment-Specific Rollout Sequence

- Staging:
- [ ] Deploy backend
- [ ] Verify backend health
- [ ] Deploy frontend pointing to staging backend URL
- [ ] Verify end-to-end chat
- Production:
- [ ] Approval gate
- [ ] Deploy backend from staging-validated artifact
- [ ] Deploy frontend with production API base URL
- [ ] Verify health and chat

### Health Checks and Post-Deploy Verification

- Backend: `/api/health`
- Frontend: reachability + test chat request path
- Post-deploy checklist stores URL, artifact tag, timestamp, and verifier

### Failure Handling and Rollback

- If health checks fail, halt promotion and auto-mark deploy failed
- Rollback uses previous stable image tag/digest and prior frontend build
- Keep rollback playbook and runbook steps documented and tested quarterly

### Manual Approval Points

- Mandatory approval before production deployment
- Optional approval before staging if introducing high-risk infra changes

## 10) Execution Timeline

### Week/Sprint Order

- Week 1:
- [ ] A1, A2, A3
- Week 2:
- [ ] B1, B2
- Week 3:
- [ ] B3, C1
- Week 4:
- [ ] C2, C3
- Week 5:
- [x] D1, D2
- Week 6:
- [ ] D3 and stabilization

### Critical Path

- Hooks baseline -> Docker build reliability -> PR CI required checks -> image publish -> staging deploy -> production promotion

### Milestones and Go/No-Go Gates

- Gate 1: hooks stable and accepted by daily workflow
- Gate 2: all app images build/run with smoke checks
- Gate 3: PR checks enforceable without excessive false failures
- Gate 4: staging deployment repeatable from immutable artifacts
- Gate 5: production promotion and rollback both rehearsed

## 11) Risks, Tradeoffs, and Mitigations

### Top Risks

- Risk: Hook runtime too slow, causing bypass behavior
- Mitigation: keep commit hooks fast; move expensive checks to CI

- Risk: Multi-language CI complexity in monorepo
- Mitigation: strict path filters and per-app isolated jobs

- Risk: Image drift between local and CI
- Mitigation: same Dockerfiles for local, CI build, and deploy artifacts

- Risk: Secrets leakage in logs or images
- Mitigation: environment-based injection, masked logs, no secret build args

- Risk: CD instability without staging soak time
- Mitigation: mandatory staging verification before prod promotion

### Tradeoffs

- Tradeoff: Incremental baseline over exhaustive security controls now
- Mitigation: explicit roadmap for SAST/SCA/scanning in later stages

- Tradeoff: Managed platforms over Kubernetes flexibility
- Mitigation: keep container contracts portable for future platform migration

### Fallback Options

- If containerization blocks velocity, run CI quality checks first and defer image publish by one sprint
- If full CD is unstable, keep staging automated and production manual while hardening

## 12) Ready-to-Start Task Backlog

1. Task: Create `pre-commit` baseline config and install docs

- Owner profile: Full-stack developer
- Estimate: `S`
- Done criteria: hooks install/run documented and `pre-commit run --all-files` passes

2. Task: Add fast hooks for `frontend/` (format + lint)

- Owner profile: Frontend-focused developer
- Estimate: `S`
- Done criteria: malformed staged frontend file is blocked

3. Task: Add fast hooks for `backend-nodejs/` (format + lint)

- Owner profile: Node backend developer
- Estimate: `S`
- Done criteria: malformed staged Node file is blocked

4. Task: Add fast hooks for `backend-python/` (`ruff check`, `ruff format`, `pyright`)

- Owner profile: Python backend developer
- Estimate: `S`
- Done criteria: malformed staged Python file is blocked

5. Task: Add Dockerfiles and `.dockerignore` for all three apps

- Owner profile: DevOps/full-stack developer
- Estimate: `M`
- Done criteria: each app builds to a runnable image locally

6. Task: Add root Compose stack with backend profile switch

- Owner profile: DevOps/full-stack developer
- Estimate: `M`
- Done criteria: integrated stack boots and smoke checks pass

7. Task: Add PR CI workflow with per-app path filters and caching

- Owner profile: DevOps engineer
- Estimate: `M`
- Done criteria: affected jobs run correctly by changed path

8. Task: Configure branch protection with required checks

- Owner profile: Repository maintainer
- Estimate: `S`
- Done criteria: merge blocked when required check fails

9. Task: Add image publish workflow for `main` and tag strategy docs

- Owner profile: DevOps engineer
- Estimate: `M`
- Done criteria: GHCR receives expected immutable and channel tags

10. Task: Add staging deploy workflow with health checks and prod approval promotion

- Owner profile: DevOps engineer
- Estimate: `L`
- Done criteria: staging auto-deploy works, prod deploy requires approval, rollback tested
