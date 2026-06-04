# PuppyRun Agent Instructions

These instructions apply to AI agents working in this repository.

## Repository Identity

- Project: PuppyRun
- Repo path used by the owner: `/Users/jianghuilai/.codex/worktrees/2079/puppy-run`
- Current primary branch for Phase 1 work: `codex/phase1`
- Product goal: an evidence-grounded Agent workbench for technical stack and architecture decisions.

## Required Start-of-Task Checks

Before answering "what is next", editing files, reviewing a diff, or drafting a handoff, verify the current repo state instead of relying on memory:

```bash
git status --short --branch
git rev-parse HEAD
git log --oneline --decorate -n 8
```

When the task references a plan section, reread that section before acting:

```bash
rg -n "<task name or task number>" docs/superpowers/plans README.md
```

## Scope Rules

- Treat each user request as a scoped engineering task.
- Do not implement the next plan task unless the user explicitly asks for it.
- Preserve explicit scope locks such as "only Task 5", "frontend only", "do not modify backend files", or "allowed file: backend/Dockerfile".
- For narrow unblockers, change only the allowed file(s) and do not fold in documentation, feature work, or deployment closure.
- If the user asks for a prompt for another thread, provide a ready-to-paste controller or reviewer prompt, not high-level advice.

## Controller Prompt Pattern

When drafting a prompt for a new implementation thread, include:

- Repo path.
- Branch.
- Starting commit.
- Exact task.
- Allowed files.
- Out-of-scope files and next tasks.
- Required verification commands.
- Expected final report format.
- Stop condition.

Prefer this delegation flow for non-trivial implementation tasks:

1. One implementer agent.
2. One spec-compliance reviewer agent.
3. One code-quality reviewer agent.
4. Fix only approved issues inside the declared scope.
5. Re-review until accepted or genuinely blocked.

When a task is about PuppyRun handoffs, reviews, unblockers, accepted debt, or next-task lookup, use the repo-local `puppyrun-agent-workflow` skill if available.

## Read-Only Review Rules

When the user asks for a review:

- Do not modify files unless the user explicitly asks for fixes.
- Findings come first, ordered by severity.
- Use file:line references for actionable issues.
- After findings, list verification commands and results.
- End with an explicit gate when requested, such as `Ready to proceed to Task 8? Yes / No / With fixes.`

Review focus should include:

- Behavioral regressions.
- Edge cases and failure paths.
- Async, race, stale-response, and state-overwrite bugs.
- Persistence and rollback correctness.
- Docker/runtime/package-discovery mismatches.
- Missing tests or weak verification.

## Accepted Debt

Read `docs/accepted-debt.md` before changing clarification, extraction, recommendation, or workflow behavior.

Current accepted debt:

- `AD-001`: deterministic constraint parsing does not understand negation.

Do not fix accepted debt as part of unrelated work. Reopen it only when the user explicitly makes it part of the task.

## Phase 1 Boundary

Phase 1 intentionally avoids live LLM calls. Do not introduce live LLM calls, LLM-based synthesis, eval dashboards, community risk verification, MCP adapters, user accounts, billing, RBAC, private repository access, SSE/WebSocket streaming, or export jobs unless the user explicitly starts a later-phase task.

## Verification Tiers

Use the narrowest verification that actually covers the risk, then broaden when the change crosses boundaries.

Narrow checks:

- Relevant backend test file(s).
- Relevant frontend test file(s).
- Targeted lint/build command.
- `git diff --check`.

Integration checks:

- Full backend `pytest -q` when touching backend contracts, persistence, worker behavior, or shared packages.
- Full frontend test/build when touching web state, API types, or UI behavior.
- Docker smoke when touching Dockerfiles, environment variables, runtime dependencies, package discovery, or Compose services.

Release checks:

```bash
cd backend && ruff check .
cd backend && pytest -q
cd apps/web && npm test -- --run
cd apps/web && npm run build
cp .env.example .env
docker compose up --build -d
curl http://localhost:8000/health
docker compose ps
git diff --check
```

For browser-facing work, verify the actual browser flow in addition to tests and builds.

## Docker and Packaging Pitfalls

- Local Python tests passing does not prove the Docker image contains every backend package.
- If containers fail with `ModuleNotFoundError` after local tests pass, inspect Dockerfile copy coverage and package discovery before chasing app logic.
- When adding a new top-level backend package under editable setuptools install, confirm `backend/pyproject.toml` package discovery includes it.
- Test the exact command named by the user; do not replace `pytest` with `python -m pytest` and treat that as equivalent when package discovery is under investigation.

## Documentation and Secret Boundaries

- Do not commit real public hosts, raw IPs, SSH targets, tokens, credentials, or secrets.
- Repo docs may describe topology, verification steps, placeholders, and environment variable names.
- Real VPS details belong in VPS-local env files or private notes, not in committed docs.

## Repo Hooks

This repository includes lightweight Codex hooks under `.codex/hooks.json`.

- Hooks are reminders and risk checks, not a replacement for review.
- Hook warnings should be investigated before finalizing.
- Hook output does not prove verification passed.
- If hooks are newly added or changed, a future Codex session may require hook trust review before they run.

## Final Response Expectations

When completing work, report:

1. What changed.
2. Files changed.
3. Verification commands and results.
4. What scope was intentionally not touched.
5. Residual risks or skipped checks.

Do not claim tests passed unless they were run in the current turn.
