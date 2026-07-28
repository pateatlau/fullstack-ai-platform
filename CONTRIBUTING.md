# Contributing

## Welcome

Thank you for your interest in this project. Contributions are welcome but not required — this repository is primarily a **portfolio and reference implementation** of a production-style full-stack AI platform. Issues and pull requests help improve clarity and fix bugs; there is no expectation of ongoing maintenance or a large contributor community.

## Getting started

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Node.js 20+ (24 works)
- npm
- Docker and Docker Compose (local PostgreSQL for the Python backend)

### Local setup

Follow the [Quick Start](README.md#quick-start) in the root README to clone the repo, configure environment files, start Postgres, run migrations, and launch the backend and frontend.

Install dependencies for the areas you plan to change:

```bash
cd frontend && npm install
cd backend-python && uv sync
# Optional reference backend:
cd backend-nodejs && npm install
```

## Development workflow

1. **Branch from `main`** — use a descriptive branch name (e.g. `fix/chat-stream-retry`, `docs/contributing-update`).
2. **Keep PRs small and focused** — one logical change per pull request when possible.
3. **One app area per PR when practical** — `frontend/`, `backend-python/`, and `backend-nodejs/` have separate CI jobs; scoped changes review and land faster.
4. **Rebase or squash before merge** — `main` requires linear history; merge commits are disabled.

## Quality gates

### Pre-commit hooks

This repo uses [pre-commit](https://pre-commit.com) for fast local checks before each commit.

```bash
pip install pre-commit
# or: uv tool install pre-commit

pre-commit install
pre-commit run --all-files   # verify everything passes
```

Hooks cover formatting (Prettier, Ruff), linting (ESLint, Ruff), Python type checking (Pyright), and basic file validation. Full test suites and builds run in CI, not on every commit.

**Bypass policy:** Do not use `git commit --no-verify` routinely. If you must bypass hooks, document it in the PR description:

```text
[hook-bypass] Reason: <brief reason> | Follow-up: <issue or PR link>
```

All bypassed issues must be resolved before merge.

### Manual checks (before opening a PR)

Run the commands for each app area you touched:

**Python backend** (`backend-python/`):

```bash
make lint
make format-check
make typecheck
make test-cov
```

**Frontend** (`frontend/`):

```bash
npm run lint
npm run format:check
npm test -- --run
npm run build
```

**Node backend** (`backend-nodejs/`, optional reference implementation):

```bash
npm run lint
npm run format:check
npm test
npm run build
```

**All areas** (full pre-PR smoke):

```bash
cd backend-python && make lint && make format-check && make typecheck && make test-cov
cd frontend && npm run lint && npm run format:check && npm test -- --run && npm run build
cd backend-nodejs && npm run lint && npm run format:check && npm test && npm run build
```

### Pre-commit troubleshooting

| Problem | Solution |
| --- | --- |
| `pre-commit not found` | `pip install pre-commit` or `uv tool install pre-commit` |
| Hooks not running | Run `pre-commit install` from the repo root |
| ESLint / Prettier failures | Fix in code or re-run `pre-commit run --all-files` and re-stage auto-fixes |
| Ruff / Pyright failures | `cd backend-python && uv run ruff check --fix app tests` then `make typecheck` |

For persistent hook issues: `pre-commit clean`, then `pre-commit uninstall && pre-commit install`, then `pre-commit run --all-files`.

## Pull request expectations

### Required CI checks

PRs trigger [PR Quality Checks](.github/workflows/pr-quality.yml) for changed app areas:

| Job | Commands |
| --- | --- |
| Frontend PR Checks | lint, format check, test, build |
| Backend Node.js PR Checks | lint, format check, test, build |
| Backend Python PR Checks | lint, format check, typecheck, test with 80% coverage minimum on `app/` |

Your branch must be up to date with `main` and all relevant checks must pass.

### PR checklist

- [ ] Branch is up to date with `main`
- [ ] Relevant required checks passed for changed app areas
- [ ] Pre-commit hooks pass (or bypass is documented with follow-up)
- [ ] Scope stays within the intended app area or change
- [ ] No secrets, API keys, or personal data in commits
- [ ] Merge will use squash or rebase (not a merge commit)

### Scope discipline

- Describe **what** changed and **why** in the PR description.
- Link related issues when applicable.
- Avoid drive-by refactors or unrelated formatting in the same PR.

## Commit message style

Follow the existing git history. Use conventional prefixes:

| Prefix | Use for |
| --- | --- |
| `feat:` | New behavior or capability |
| `fix:` | Bug fixes |
| `docs:` | Documentation only |
| `refactor:` | Code change without behavior change |
| `test:` | Tests only |
| `chore:` | Tooling, deps, CI |

Examples from this repo:

```text
feat(voice): add VoiceSessionManager with lifecycle and timeout handling
fix: resolve chat stream retry on connection drop
docs: backfill CHANGELOG and mark Epic 03/04 release documentation complete
```

Optional scope in parentheses (`feat(voice):`, `docs(mcp):`) is encouraged when the change is localized.

## What we are not accepting (for now)

Without prior discussion via a GitHub Issue, please do not open PRs for:

- **Large cross-cutting refactors** — especially those that touch multiple app areas at once
- **New epics or major features** — agent, RAG, MCP, voice, and similar platform work follows an internal roadmap; propose the idea in an issue first
- **New dependencies** — library additions need justification and an issue before implementation
- **Breaking API or schema changes** — coordinate before investing in a large diff

Small bug fixes, documentation improvements, test coverage gaps, and clear typo fixes are always welcome.

## Questions

Open a [GitHub Issue](https://github.com/pateatlau/fullstack-ai-platform/issues) for bugs, questions, or feature proposals. This is a low-traffic repository; there is **no guaranteed response time or SLA**.

## Code of conduct

Be respectful and constructive. Assume good intent. Disagree on technical merits without personal attacks. Harassment, discrimination, and abusive language are not tolerated. Maintainers may close or lock threads that violate these expectations.

A formal Contributor Covenant may be adopted later if external contributions become more frequent.
