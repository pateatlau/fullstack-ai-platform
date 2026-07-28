# V2 Program Execution Guide

Shared rules for all V2 epic documents (`post-mvp-v2-epic-*.md`). Each epic file contains **Part I (Design)** and **Part II (Execution)**.

## Document hierarchy

```text
Architecture Strategy (references/)
        │
        ▼
Epic document (plans/post-mvp-v2-epic-NN-*.md)
  Part I — Design
  Part II — Execution
        │
        ▼
Baseline Audit (audits/) → Phase work → Release Summary (releases/)
```

| Document | Answers |
| -------- | ------- |
| Strategy | Why V2, epic order, platform principles |
| Epic doc Part I | What to build, behaviour, locked design, **architectural invariants** |
| Epic doc Part II | What to implement, in what order, how to verify |
| `_template-implementation-plan.md` | Skeleton for authoring new epics (not read during implementation) |
| Audit / release | Baseline metrics, validation evidence |

## Instructions for coding agents

1. **Read frontmatter** (`epic`, `depends_on`, `provides`, `feature_flags`, `packages`) before any code change.
2. **Read [_program-v2-execution-guide.md](./_program-v2-execution-guide.md)** (this file) once per session.
3. **Implement from Part II only** — one phase at a time unless the user batches phases.
4. **Consult Part I** only for behaviour, scope, locked-design, or **architectural invariant** questions.
5. **Do not skip steps** or tick checkboxes before **Verify** and **Acceptance** pass.
6. **Stop at** `Phase N complete — user confirmed` and wait for user approval.
7. **Reuse** Part II **Reuse existing components** — do not reimplement.
8. **Never implement functionality from future epics.** If work suggests a future abstraction, leave `TODO(epic-N): …` and stop; ask the user.
9. **Obey Part II Not allowed** — no unrelated refactors, package moves, or API breaks.
10. **Update the epic doc** after Phase 0 and Phase 12: completion records, Phase status rows, Changelog.

## Human developer notes

Use **Files index** (path, action, owner, phase) as the wiring checklist. Review **Part I § Public APIs** before changing interfaces frozen in early phases.

## Phase shape (required)

Every phase in Part II uses this order:

```markdown
**Effort:** XS | S | M | L | XL

**Deliverables:** …

**Steps:** …

**Verify:** `command`

**Acceptance:** behavioural outcomes

**Exit criteria:** completion gates including user approval

**Rollback:** integration phases only
```

Effort guide (planning only — **not** a code-size target):

| Size | Relative scope |
| ---- | -------------- |
| **XS** | Audit, docs, or config-only |
| **S** | Single module + tests |
| **M** | Multiple modules or non-trivial integration |
| **L** | Core loop or large cross-module wiring |
| **XL** | Rare; split into smaller phases if possible |

Do not pad or trim code to match effort labels. Satisfy **Acceptance** and **Exit criteria** only.

## Phase status values (fixed)

| Value | Meaning |
| ----- | ------- |
| Not Started | Phase not begun |
| In Progress | Active work |
| Blocked | Waiting on user/decision/dependency |
| Completed | Verified; user confirmed |

Do not introduce other status values.

## Execution workflow

```text
Phase 0 (audit, no code) → Build phases → Integration phase? → Validation & release
```

Per phase: Deliverables → Steps → Verify → Acceptance → Exit criteria → tick → update Phase status.

## Quality gates

From `backend-python/`:

```bash
make lint && make format-check && make typecheck
pytest <phase-test-path>
make test-cov    # final phase; ≥ 80% on app/ and epic package
make eval        # final phase
```

Frontend gates: Phase 0 and final phase unless epic touches frontend.

## Feature flags

- Pattern: `{CAPABILITY}_ENABLED`; default **`false`**.
- Flag off = prior release behaviour on hot paths unchanged.
- Integration phase **Rollback** must disable flag and restore legacy path.

## Coverage

≥ **80%** on `app/` and on epic package(s) in frontmatter.

## Git / PR conventions

- One PR per phase unless user requests otherwise.
- Branch: `v2/epic-{nn}/phase-{pp}-{slug}`
- Do not commit unless the user asks.

## Integration phase rollback (required)

```markdown
**Rollback:** If verification fails — disable feature flag; remove DI wiring;
revert adapter branches; re-run flag-off regression tests.
```

## Completion records

Fill in Phase 0 and final phase only. Next epic **Baseline** = previous epic final completion record.

## Standard Definition of Done

- [ ] All phases **Completed**; user confirmed each.
- [ ] Phase status table uses only allowed values.
- [ ] Coverage and `make eval` gates met.
- [ ] Flag-off regression passes (if applicable).
- [ ] Release summary in `docs/releases/`.
- [ ] User authorizes next epic.

## Changelog policy

Append dated entries to the epic doc **Changelog**. Note whether the change affects Part I (design) or Part II (execution).
