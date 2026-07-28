---
epic: v2-NN
title: "{Epic Title}"
status: not_started
version: 1
depends_on: []
provides: []
feature_flags: []
packages: []
test_paths: []
---

# Post-MVP V2 Epic NN — {Epic Title}

> **Agents:** Read [_program-v2-execution-guide.md](./_program-v2-execution-guide.md). Implement **Part II** phase-by-phase; consult **Part I** for design questions only.

**Strategy:** [V2 architecture](../references/fullstack-ai-platform-v2-architecture-implementation-strategy.md)

---

# Part I — Design

## Objective

{What this epic builds; what it does not ship.}

## Principles

{Short list or reference strategy principles}

## Architecture

{Diagram + package structure}

## Components

| Component | Role | Key outputs |
| --------- | ---- | ------------- |
| {name}    | {role} | {types}     |

## Scope

**In:** {list}

**Out:** {Epic IDs only}

## Dependencies

| Requires | Provides to downstream |
| -------- | ---------------------- |
| {prior}  | {APIs, modules, flags} |

**Future consumers:** {Epic IDs}

## Locked decisions

| Topic | Decision | Deferred to |
| ----- | -------- | ----------- |
| {topic} | {choice} | {epic or —} |

## {Domain-specific design sections}

{Retry rules, streaming strategy, etc. — keep concise tables}

## Public APIs (stable after Phase {n})

| API | Kind |
| --- | ---- |
| `{Name}` | Protocol / model |

## Configuration defaults

{Key defaults}

## Design acceptance

{Behavioural criteria for epic completion}

## Architectural invariants

These rules must remain true throughout this epic. Violations require explicit user approval and Part I update.

- {Invariant 1 — e.g. core packages remain provider-agnostic}
- {Invariant 2 — e.g. no framework-specific imports in core modules}
- {Invariant 3 — epic-specific long-lived rule}
- Future-epic behaviour is not implemented early; use `TODO(epic-N):` only.

---

# Part II — Execution

## Reuse existing components

**DO NOT REIMPLEMENT:**

| Component | Location |
| --------- | -------- |
| {name}    | `{path}` |

## Not allowed

- Refactor unrelated code
- Rename packages or move modules fixed in Part I
- Add dependencies without user approval
- Change existing API contracts (except documented additive paths)
- Implement functionality scoped to other epics
- {epic-specific}

## Baseline

_Copy from previous epic Phase 12 completion record._

| Area | State |
| ---- | ----- |
| Backend tests / coverage | |

## Phase status

| Phase | Name | Effort | Status |
| ----- | ---- | ------ | ------ |
| 0 | Baseline Audit | XS | Not Started |
| 1 | {name} | S | Not Started |
| … | … | … | … |
| N | Validation & Release | S | Not Started |

---

## Phase 0 — Baseline Audit

**Effort:** XS

**Deliverables:** `docs/audits/post-mvp-v2-epic{N}-phase-0-baseline-audit.md`

**Steps:**

- [ ] Confirm prerequisite complete
- [ ] Run quality gates (program guide)
- [ ] Inventory paths from Part I
- [ ] Write audit doc; record metrics
- [ ] Phase 0 complete — user confirmed

**Verify:** `make lint && make typecheck && make test-cov && make eval`

**Acceptance:**

- All gates pass; no code changes

**Exit criteria:**

- Audit published; user confirmed Phase 0

**Completion record:**

| Metric | Result |
| ------ | ------ |
| Backend tests / coverage | |
| Eval CLI | |

---

## Phase 1 — {Title}

**Effort:** S

**Deliverables:** {paths}

**Steps:**

- [ ] …
- [ ] Phase 1 complete — user confirmed

**Verify:** `pytest {path}`

**Acceptance:**

- {behavioural outcome}

**Exit criteria:**

- Tests pass; user confirmed Phase 1

---

<!-- Repeat phases 2…N−2: Effort, Deliverables, Steps, Verify, Acceptance, Exit criteria -->

## Phase N−1 — Integration (optional)

**Effort:** M

**Deliverables:** {adapters}

**Steps:**

- [ ] …
- [ ] Phase N−1 complete — user confirmed

**Verify:** `pytest {parity_tests}`

**Acceptance:**

- Flag off: legacy unchanged; flag on: parity

**Exit criteria:**

- Parity tests pass; user confirmed

**Rollback:**

- Disable flag; remove DI/adapter branches; re-run flag-off regression

---

## Phase N — Validation & Release

**Effort:** S

**Steps:**

- [ ] Full regression; Docker smoke; `make eval`
- [ ] Release summary; tick DoD; set Phase status to Completed
- [ ] Phase N complete — user confirmed

**Verify:** `make test-cov && make eval`

**Acceptance:**

- Part I design acceptance met

**Exit criteria:**

- User authorizes next epic

**Completion record:**

| Metric | Result |
| ------ | ------ |
| Backend tests / coverage | |
| Epic package coverage | |
| Eval CLI | |
| Flag-off regression | |
| Epic parity | |

---

## Files index

| Path | Action | Owner | Phase |
| ---- | ------ | ----- | ----- |
| `{path}` | create / modify / delete | Core / Adapter / Tests / Docs | {n} |

## PR map

One PR per phase; branch `v2/epic-{nn}/phase-{pp}-{slug}`.

## Risks

| Risk | Mitigation |
| ---- | ---------- |
| Accidental provider coupling | Protocol-only; no SDK in epic package |
| {risk} | {mitigation} |

## Observability

| Field | Purpose |
| ----- | ------- |
| `{field}` | {purpose} |

## Definition of done

- [ ] Part I delivered; design acceptance met
- [ ] Program DoD: [_program-v2-execution-guide.md](./_program-v2-execution-guide.md)
- [ ] User confirmed final phase

## Changelog

| Date | Change |
| ---- | ------ |
| {YYYY-MM-DD} | Initial plan |
