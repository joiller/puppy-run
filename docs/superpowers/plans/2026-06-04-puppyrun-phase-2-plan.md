# PuppyRun Phase 2 Interactive Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Before each task, turn that task into a scoped controller prompt with exact file scope, verification commands, and review gates.

**Goal:** Build a versioned interactive decision workbench where users can edit candidates, explicit constraints, and criteria weights, inspect gap analysis before rerunning, trigger targeted GitHub-only re-research, compare recommendation versions, inspect an evidence matrix and drawer, and read an ADR view for each version.

**Architecture:** Extend the existing Phase 1 modular monolith. Keep the deterministic Phase 1 workflow intact, but stop treating candidates, criteria, evidence, and recommendations as one overwriteable session result. Add explicit version records, score cells, a draft edit contract, and a Phase 2 worker path that reuses unchanged evidence and fetches only missing GitHub repository evidence. Phase 2 still avoids live LLM calls, broad web search, accounts, private repository access, streaming, and export jobs.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, arq, httpx, PostgreSQL, Redis, React 19, TypeScript, Vite, Vitest, Testing Library, Docker Compose.

---

## Merge Note

This is the canonical merged Phase 2 plan.

It combines:

- The June 4 architecture/product plan: stronger versioning model, explicit draft contract, `ScoreCell`, gap analysis before rerun, and owner-facing learning context.
- The June 6 independent implementation plan: task sequencing, test-first execution style, concrete file map, worker/API/frontend split, release verification, and self-review gates.

The merged plan intentionally corrects the weaker parts of the June 6 draft:

- Do not hide the evidence matrix only inside `EvidenceItem` payloads or recommendation rationale. Add explicit `ScoreCell` rows.
- Do not create Phase 2 versions without provenance. Store `source_version_id`, `label`, `created_at`, and `completed_at`.
- Do not wait until after rerun to explain gaps. `PATCH /draft` must return deterministic gap analysis before the user starts targeted research.
- Do not lose failed version attempts through rollback. A Phase 2 run should persist a `failed` version record while keeping the previous completed version readable.
- Prefer a single `phase2_draft` API contract over many backend mutation endpoints. The frontend can expose small controls, but those controls should update the same draft shape.

## Current Repository Baseline

Verified before this plan merge:

```text
Branch: codex/phase2
HEAD: f9f4fbcbe1e92fa40ac7ed5d0847efadf58f9c97
Dirty state before merge: two untracked Phase 2 plan files
```

Phase 1 is implemented and locally verified. It supports session creation, deterministic clarification, fixed Agent-framework candidates, public GitHub repository analysis, basic criteria, evidence, recommendation, and trace events.

Important current constraint: Phase 1 currently deletes session-level recommendations, evidence, criteria, and candidates during a run. Phase 2 must change persistence so old versioned results are not erased.

## Product Context

PuppyRun is an evidence-grounded Agent decision workbench for technical stack and architecture decisions. The first workflow focuses on Agent framework selection, such as LangGraph, OpenAI Agents SDK, CrewAI, and custom candidate repositories.

The intended product loop is:

1. The user asks a technical decision question.
2. PuppyRun clarifies the context and constraints.
3. PuppyRun selects or adds candidate technologies.
4. PuppyRun creates criteria and weights.
5. PuppyRun collects evidence.
6. PuppyRun scores and recommends.
7. PuppyRun keeps the process inspectable through trace, evidence, score cells, ADRs, and versions.
8. The user adjusts the decision and reruns only the necessary parts.

The important product idea is: PuppyRun should turn a technical decision into a versioned, inspectable, replayable workflow.

## Phase 2 Scope

In scope:

- Candidate add, remove, include, exclude, and lock controls.
- Explicit must-include and must-exclude constraint controls.
- Criteria weight editing.
- Draft edits that do not erase completed results.
- Gap analysis before rerun.
- Targeted GitHub-only re-research.
- Decision versions with provenance.
- Score cells for the evidence matrix.
- Evidence matrix and evidence drawer.
- ADR view per version.
- Backend, frontend, worker, tests, Docker smoke, and browser verification.

Out of scope:

- Live LLM calls.
- LLM-based synthesis.
- MCP adapters.
- Official docs crawling.
- Blog or paper search.
- Community risk verification.
- Eval dashboard.
- User accounts, billing, RBAC, or private repository access.
- SSE or WebSocket streaming.
- Export jobs.
- Real public host, raw IP, SSH target, token, credential, or secret documentation.

Phase 2 success criterion from the design spec:

> Changing a constraint such as "must support checkpoint and human-in-the-loop" triggers targeted re-research and creates a new recommendation version.

## Terms

### Decision Session

One decision problem, represented by `decision_sessions`.

### Decision Version

One saved recommendation snapshot for a session. Old versions must remain readable after later reruns.

Example:

- Version 1: Initial Phase 1 baseline recommends LangGraph.
- Version 2: User increases human-in-the-loop weight and excludes CrewAI.
- Version 3: User adds AutoGen and PuppyRun fetches only that new GitHub repository.

### Phase 2 Draft

The user's in-progress workbench edits before creating a new version. Draft edits should not create a new version for every UI click.

### Score Cell

One auditable candidate-by-criterion evaluation cell. It stores candidate, criterion, version, score, status, explanation, and evidence references.

### Gap Analysis

Deterministic analysis of what changed and what work is required before rerun. It should answer:

- Did candidates change?
- Did constraints change?
- Did weights change?
- Can existing evidence be reused?
- Which candidates need GitHub research?
- Is this only a score recomputation?

### Targeted Re-Research

Rerunning only the affected parts:

- Weight-only change: no GitHub calls, reuse evidence, recompute score cells and ranking.
- Candidate excluded: no GitHub calls for that candidate in the new version.
- Candidate added: fetch GitHub evidence only for that candidate.
- Constraint changed: recompute criteria and score cells, fetch only missing evidence.
- Worker failure: mark the new version and run failed; keep the prior completed version readable.

## Architecture Decisions

### Versioned Data Model

Phase 2 should introduce this relationship:

```text
decision_sessions
  -> decision_versions
    -> decision_candidates
    -> decision_criteria
    -> evidence_items
    -> score_cells
    -> recommendations
```

`DecisionVersion` stores:

- `id`
- `session_id`
- `version_number`
- `label`
- `status`
- `source_version_id`
- `change_summary`
- `gap_analysis`
- `adr`
- `created_at`
- `completed_at`

Existing Phase 1 rows may keep nullable `decision_version_id` for migration compatibility. New Phase 1 and Phase 2 runs must write versioned rows.

### Score Cells

Add `ScoreCell` instead of representing matrix cells only as `EvidenceItem` payloads.

Each score cell connects:

- one version,
- one candidate,
- one criterion,
- one score,
- one status such as `supported`, `weak`, `gap`, or `missing`,
- one explanation,
- zero or more evidence references.

The UI can still show evidence drawer content from `EvidenceItem`, but matrix identity should come from `score_cells`.

### Draft Contract

Store in-progress edits as `decision_context["phase2_draft"]` on the session.

Draft shape:

```json
{
  "source_version_id": "uuid",
  "candidate_overrides": {
    "crewai": {
      "action": "must_exclude",
      "reason": "Team does not want role-based orchestration."
    }
  },
  "custom_candidates": {
    "autogen": {
      "name": "AutoGen",
      "slug": "autogen",
      "repo_full_name": "microsoft/autogen",
      "reason": "Team asked to compare AutoGen."
    }
  },
  "must_include_constraints": {
    "checkpointing": {
      "enabled": true,
      "reason": "Checkpointing is mandatory."
    }
  },
  "must_exclude_constraints": {
    "typescript": {
      "enabled": true,
      "reason": "Team wants Python-first tooling."
    }
  },
  "weight_overrides": {
    "Runtime control and state": {
      "weight": 40,
      "reason": "Recovery matters most."
    }
  }
}
```

This structured draft does not claim to fix accepted debt `AD-001`. Raw free-form negation remains accepted debt; explicit Phase 2 controls avoid that ambiguity.

### Public API

Workspace read:

```http
GET /api/v1/sessions/{session_id}/workspace?version_id={version_id}
```

Response adds:

- `versions`
- `active_version`
- `draft`
- `gap_analysis`
- `score_cells`
- version-filtered candidates, criteria, evidence, recommendations

Draft update:

```http
PATCH /api/v1/sessions/{session_id}/draft
```

Behavior:

- Stores the normalized `phase2_draft`.
- Sets `workflow_stage` to `context_changed`.
- Returns the updated workspace with deterministic gap analysis.
- Does not enqueue worker jobs.
- Does not create a new decision version.

Create version and enqueue targeted research:

```http
POST /api/v1/sessions/{session_id}/versions
```

Behavior:

- Requires a non-empty draft.
- Creates a new `DecisionVersion` with `status="queued"` and `source_version_id`.
- Creates an `AgentRun`.
- Enqueues `run_phase2_agent_job`.
- Returns the new version, run, and updated workspace metadata.

### UI Shape

The first screen remains the actual workbench, not a landing page.

Add:

- Version selector.
- Candidate controls: include, exclude, lock, remove, add custom GitHub repo.
- Constraint include/exclude controls.
- Weight editor.
- Gap analysis panel.
- Evidence matrix backed by `score_cells`.
- Evidence drawer backed by evidence references.
- ADR tab or section.

Keep the existing stale-response and polling protections from Phase 1. Phase 2 adds more async paths, so stale workspace updates remain a real risk.

## File Structure

Backend data and API:

- Modify `backend/puppyrun_api/models.py`: add `DecisionVersion`, `ScoreCell`, version/control fields, and relationships.
- Create `backend/migrations/versions/0003_phase2_versions.py`: add Phase 2 tables and columns.
- Modify `backend/puppyrun_api/schemas.py`: add version, draft, gap, score-cell, ADR, and response schemas.
- Modify `backend/puppyrun_api/repositories/workspace.py`: add version-aware workspace reads, draft helpers, and gap analysis access.
- Modify `backend/puppyrun_api/routes/sessions.py`: expose `PATCH /draft` and `POST /versions`.
- Modify `backend/puppyrun_api/repositories/sessions.py`: support Phase 2 run creation without changing Phase 1 start behavior.

Backend agent/runtime:

- Modify `backend/puppyrun_agent/catalog.py`: add lookup helpers and custom candidate construction.
- Modify `backend/puppyrun_agent/criteria.py`: apply explicit constraints and weight overrides.
- Modify `backend/puppyrun_agent/recommendation.py`: score with editable weights and versioned rationale.
- Create `backend/puppyrun_agent/phase2.py`: draft normalization, candidate selection, research planning, gap analysis, score-cell generation, ADR rendering, and workflow orchestration.
- Modify `backend/puppyrun_agent/workflow.py`: create version 1 for completed Phase 1 runs and stop deleting historical versioned rows.
- Modify `backend/puppyrun_worker/jobs.py`: add `run_phase2_agent_job`.
- Modify `backend/puppyrun_worker/main.py`: register the Phase 2 job.

Backend tests:

- Create `backend/tests/test_phase2_workspace_api.py`: workspace shape, draft update, gap analysis, and version enqueue tests.
- Create `backend/tests/test_phase2_agent.py`: pure helper tests for selection, criteria overrides, research plans, score cells, gap analysis, and ADR.
- Create `backend/tests/test_phase2_workflow.py`: version persistence, targeted research, evidence reuse, failure preservation.
- Modify `backend/tests/test_phase1_workflow.py`: Phase 1 version 1 regression.
- Modify `backend/tests/test_sessions.py`: Phase 2 route and enqueue behavior.
- Modify `backend/tests/test_worker_jobs.py`: Phase 2 job registration.

Frontend:

- Modify `apps/web/src/types.ts`: add Phase 2 types.
- Modify `apps/web/src/api.ts`: add draft and version API calls.
- Create `apps/web/src/workbench.ts`: client-side helpers for active version, recommendation, score cells, and evidence lookup.
- Modify `apps/web/src/App.tsx`: integrate the interactive workbench.
- Modify `apps/web/src/App.css`: layout for version rail, controls, matrix, drawer, and ADR view.
- Modify `apps/web/src/App.test.tsx`: test the editable Phase 2 workflow and stale-response guard.

Docs:

- Modify `README.md`: add Phase 2 local smoke instructions after implementation.
- Modify `docs/accepted-debt.md`: add a Phase 2 note under `AD-001`.
- Modify this plan only when marking task progress or closure.

---

## Task 1: Versioned Data Model, Migration, And Workspace Shape

**Files:**

- Modify: `backend/puppyrun_api/models.py`
- Modify: `backend/puppyrun_api/schemas.py`
- Create: `backend/migrations/versions/0003_phase2_versions.py`
- Modify: `backend/puppyrun_api/repositories/workspace.py`
- Modify: `backend/puppyrun_api/routes/sessions.py`
- Test: `backend/tests/test_phase2_workspace_api.py`

- [ ] **Step 1: Write failing workspace version and score-cell tests**

Create tests that prove:

- `DecisionVersion` exists.
- `ScoreCell` exists.
- `GET /workspace` returns `versions`, `active_version`, `draft`, `gap_analysis`, and `score_cells`.
- Version-filtered workspace reads return only rows for the selected version.
- A legacy session with no versions still returns a readable workspace.

Run:

```bash
cd backend
pytest tests/test_phase2_workspace_api.py::test_workspace_returns_versions_active_version_and_score_cells -q
pytest tests/test_phase2_workspace_api.py::test_workspace_without_versions_remains_readable -q
```

Expected: FAIL because Phase 2 models and schema fields do not exist.

- [ ] **Step 2: Add models and migration**

Add:

- `DecisionVersion`
- `ScoreCell`
- nullable `decision_version_id` on candidates, criteria, evidence items, and recommendations
- candidate `selection_state`
- candidate `is_locked`
- criterion `is_locked`

Migration rules:

- `decision_versions.session_id + version_number` must be unique.
- `score_cells` must reference version, candidate, and criterion.
- `decision_version_id` columns should be nullable so existing Phase 1 data remains readable.
- Downgrade should remove Phase 2 constraints and tables in reverse dependency order.

- [ ] **Step 3: Add response schemas and workspace repository shape**

Add schemas for:

- `DecisionVersionResponse`
- `ScoreCellResponse`
- `Phase2DraftResponse`
- `GapAnalysisResponse`
- updated candidate, criterion, evidence, and recommendation responses with `decision_version_id`
- updated `WorkspaceResponse`

Update `get_workspace(db, session_id, version_id=None)` so it:

- lists all versions in ascending version number,
- chooses `version_id`, latest completed/running version, or `None`,
- returns `active_version`,
- filters versioned rows when an active version exists,
- returns legacy session rows when no versions exist,
- returns draft and current gap analysis.

- [ ] **Step 4: Run targeted checks**

Run:

```bash
cd backend
pytest tests/test_phase2_workspace_api.py -q
pytest tests/test_sessions.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/puppyrun_api/models.py backend/puppyrun_api/schemas.py backend/puppyrun_api/repositories/workspace.py backend/puppyrun_api/routes/sessions.py backend/migrations/versions/0003_phase2_versions.py backend/tests/test_phase2_workspace_api.py
git commit -m "feat: add phase2 versioned workspace model"
```

## Task 2: Draft API And Pre-Rerun Gap Analysis

**Files:**

- Modify: `backend/puppyrun_api/schemas.py`
- Modify: `backend/puppyrun_api/repositories/workspace.py`
- Modify: `backend/puppyrun_api/routes/sessions.py`
- Create or modify: `backend/puppyrun_agent/phase2.py`
- Test: `backend/tests/test_phase2_workspace_api.py`
- Test: `backend/tests/test_phase2_agent.py`

- [ ] **Step 1: Write failing draft and gap tests**

Tests must prove:

- `PATCH /api/v1/sessions/{session_id}/draft` stores one normalized `phase2_draft`.
- Draft updates set `workflow_stage` to `context_changed`.
- Gap analysis is returned before rerun.
- Weight-only changes produce `requires_github_fetch=false`.
- Added custom candidate produces a gap item requiring GitHub fetch for only that repo.
- Excluded candidate is absent from the next-version candidate set.
- Explicit include/exclude controls override raw parser ambiguity without claiming `AD-001` is fixed.

Run:

```bash
cd backend
pytest tests/test_phase2_workspace_api.py::test_patch_draft_returns_gap_analysis_before_rerun -q
pytest tests/test_phase2_agent.py::test_gap_analysis_distinguishes_weight_only_from_added_candidate -q
```

Expected: FAIL because draft schemas, route, and helpers do not exist.

- [ ] **Step 2: Add draft request schemas**

Add:

- `Phase2DraftRequest`
- `CandidateOverrideRequest`
- `CustomCandidateRequest`
- `ConstraintOverrideRequest`
- `WeightOverrideRequest`

Validation:

- candidate slug: 2 to 80 trimmed characters
- repo full name: owner/repo style string, 3 to 200 trimmed characters
- weight: integer 0 to 100
- reasons: trimmed, 3 to 400 characters

- [ ] **Step 3: Add pure Phase 2 helper functions**

In `backend/puppyrun_agent/phase2.py`, add deterministic helpers:

- `normalize_phase2_draft(raw, source_version_id)`
- `apply_phase2_constraints(context, draft)`
- `build_phase2_candidates(context, draft)`
- `apply_phase2_criteria(criteria, draft, context)`
- `build_research_plan(candidates, previous_evidence)`
- `build_gap_analysis(draft, candidates, criteria, previous_evidence)`

Gap output must include:

- `requires_research`
- `score_only`
- `changed_candidates`
- `changed_constraints`
- `changed_weights`
- `research_tasks`
- `reuse_tasks`
- `items`

- [ ] **Step 4: Add `PATCH /draft` route**

Add:

```http
PATCH /api/v1/sessions/{session_id}/draft
```

Route behavior:

- 404 if session is missing.
- 409 if no completed source version exists and no legacy baseline exists.
- stores normalized draft under `decision_context["phase2_draft"]`,
- stores latest gap analysis under `decision_context["phase2_gap_analysis"]`,
- sets `workflow_stage="context_changed"`,
- returns full workspace.

- [ ] **Step 5: Run targeted checks**

Run:

```bash
cd backend
pytest tests/test_phase2_workspace_api.py tests/test_phase2_agent.py -q
pytest tests/test_sessions.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/puppyrun_api/schemas.py backend/puppyrun_api/repositories/workspace.py backend/puppyrun_api/routes/sessions.py backend/puppyrun_agent/phase2.py backend/tests/test_phase2_workspace_api.py backend/tests/test_phase2_agent.py
git commit -m "feat: add phase2 draft and gap analysis api"
```

## Task 3: Phase 2 Scoring, Score Cells, And ADR Helpers

**Files:**

- Modify: `backend/puppyrun_agent/catalog.py`
- Modify: `backend/puppyrun_agent/criteria.py`
- Modify: `backend/puppyrun_agent/recommendation.py`
- Modify: `backend/puppyrun_agent/phase2.py`
- Test: `backend/tests/test_phase2_agent.py`

- [ ] **Step 1: Write failing pure helper tests**

Tests must prove:

- custom candidates are included from draft,
- `must_exclude` removes a candidate,
- `lock` keeps a candidate in the next version,
- explicit constraints affect criteria generation,
- weight overrides affect weighted score totals,
- score-cell builder emits one cell per candidate and criterion,
- each score cell has status, score, explanation, and evidence reference list,
- ADR builder returns title and body with context, decision, options, rationale, tradeoffs, risks, and evidence links.

Run:

```bash
cd backend
pytest tests/test_phase2_agent.py -q
```

Expected: FAIL for missing helpers or incomplete scoring.

- [ ] **Step 2: Add candidate and criteria helpers**

Implement:

- `registry_by_slug()`
- `custom_candidate_from_draft(slug, payload)`
- `apply_weight_overrides(criteria, overrides)`

Keep this deterministic and GitHub-only. Do not add live LLM behavior.

- [ ] **Step 3: Add weighted recommendation helpers**

Add functions that:

- score candidate fit using selected criteria and weights,
- produce `Recommended vN: ...` summaries,
- include ranked candidates,
- include score breakdown by criterion,
- preserve previous recommendation rationale shape where possible for UI compatibility.

- [ ] **Step 4: Add score-cell and ADR builders**

Add:

- `build_score_cells(candidates, criteria, repos, evidence_by_candidate)`
- `build_adr(version_number, summary, rationale, gap_analysis, score_cells)`

The score-cell builder should not write database rows. It should return pure dicts or dataclasses that Task 4 persists.

- [ ] **Step 5: Run regression checks**

Run:

```bash
cd backend
pytest tests/test_phase2_agent.py -q
pytest tests/test_phase1_clarification.py tests/test_phase1_github_client.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/puppyrun_agent/catalog.py backend/puppyrun_agent/criteria.py backend/puppyrun_agent/recommendation.py backend/puppyrun_agent/phase2.py backend/tests/test_phase2_agent.py
git commit -m "feat: add phase2 scoring and adr helpers"
```

## Task 4: Versioned Phase 1 Baseline And Phase 2 Targeted Workflow

**Files:**

- Modify: `backend/puppyrun_agent/workflow.py`
- Modify: `backend/puppyrun_agent/phase2.py`
- Modify: `backend/puppyrun_api/repositories/sessions.py`
- Modify: `backend/puppyrun_api/routes/sessions.py`
- Modify: `backend/puppyrun_worker/jobs.py`
- Modify: `backend/puppyrun_worker/main.py`
- Test: `backend/tests/test_phase1_workflow.py`
- Test: `backend/tests/test_phase2_workflow.py`
- Test: `backend/tests/test_sessions.py`
- Test: `backend/tests/test_worker_jobs.py`

- [ ] **Step 1: Write failing workflow tests**

Tests must prove:

- a completed Phase 1 run creates version 1,
- Phase 1 rows receive `decision_version_id`,
- Phase 1 rerun does not delete completed version rows from prior runs,
- `POST /versions` requires draft changes,
- `POST /versions` creates queued version and enqueues `run_phase2_agent_job`,
- Phase 2 weight-only rerun performs no GitHub fetches,
- Phase 2 added-candidate rerun fetches only the added candidate's GitHub repo,
- Phase 2 successful rerun creates version 2 with score cells, recommendation, ADR, and trace events,
- Phase 2 failure marks the new version failed and leaves the previous completed version readable.

Run:

```bash
cd backend
pytest tests/test_phase1_workflow.py::test_phase1_completed_run_creates_version_one -q
pytest tests/test_phase2_workflow.py -q
pytest tests/test_sessions.py::test_create_phase2_version_enqueues_targeted_job -q
```

Expected: FAIL because the workflow is still session-overwrite oriented.

- [ ] **Step 2: Version Phase 1 output**

Modify Phase 1 workflow:

- create version 1 if no versions exist for the session,
- set `decision_version_id` on candidates, criteria, evidence, score cells if generated, and recommendation,
- set `version.adr`,
- avoid deleting rows belonging to existing completed versions,
- keep legacy cleanup only for unversioned current-session rows if needed for compatibility.

- [ ] **Step 3: Add `POST /versions` route**

Add:

```http
POST /api/v1/sessions/{session_id}/versions
```

Behavior:

- 404 if session is missing.
- 409 if no draft or no source version exists.
- creates `DecisionVersion(status="queued")`.
- creates `AgentRun`.
- enqueues `run_phase2_agent_job`.
- returns `StartAgentRunResponse` plus version/workspace metadata if the schema is extended.

- [ ] **Step 4: Implement Phase 2 workflow**

Implement `run_phase2_workflow(db, run_id, github_transport=None)` so it:

- loads the queued version and draft,
- commits `status="running"` before external calls,
- emits `phase2_started`,
- builds effective context,
- builds candidates and criteria,
- plans fetch/reuse tasks,
- emits `targeted_research_planned`,
- fetches only missing GitHub evidence,
- reuses unchanged GitHub evidence,
- persists candidates, criteria, evidence, score cells, recommendation, gap analysis, and ADR for the new version,
- marks version and run completed,
- clears or archives `phase2_draft`,
- sets session `workflow_stage="completed"`.

Failure behavior:

- rollback only the current failed unit of work,
- reload run and version,
- mark run failed,
- mark version failed,
- store failure detail on version gap analysis or ADR payload,
- set session `workflow_stage="failed"` only for the active run state,
- preserve previous completed versions.

- [ ] **Step 5: Register worker job**

Add `run_phase2_agent_job` to:

- `backend/puppyrun_worker/jobs.py`
- `backend/puppyrun_worker/main.py`
- `backend/tests/test_worker_jobs.py`

- [ ] **Step 6: Run backend verification**

Run:

```bash
cd backend
ruff check .
pytest tests/test_phase1_workflow.py tests/test_phase2_workflow.py tests/test_sessions.py tests/test_worker_jobs.py -q
pytest -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/puppyrun_agent/workflow.py backend/puppyrun_agent/phase2.py backend/puppyrun_api/repositories/sessions.py backend/puppyrun_api/routes/sessions.py backend/puppyrun_worker/jobs.py backend/puppyrun_worker/main.py backend/tests/test_phase1_workflow.py backend/tests/test_phase2_workflow.py backend/tests/test_sessions.py backend/tests/test_worker_jobs.py
git commit -m "feat: add phase2 targeted version workflow"
```

## Task 5: Frontend Types, API, And Workbench Helpers

**Files:**

- Modify: `apps/web/src/types.ts`
- Modify: `apps/web/src/api.ts`
- Create: `apps/web/src/workbench.ts`
- Modify: `apps/web/src/App.test.tsx`

- [ ] **Step 1: Write failing frontend helper tests**

Tests must prove:

- `latestVersion(workspace)` selects the active/latest version correctly,
- `activeRecommendation(workspace)` selects recommendation for active version,
- `scoreCellFor(workspace, candidateId, criterionId)` returns a matrix cell,
- `evidenceForScoreCell(workspace, scoreCell)` resolves evidence references,
- draft API functions call `PATCH /draft`,
- version creation calls `POST /versions`.

Run:

```bash
cd apps/web
npm test -- --run App.test.tsx
```

Expected: FAIL because types and helpers do not exist.

- [ ] **Step 2: Add TypeScript types**

Add:

- `DecisionVersion`
- `ScoreCell`
- `Phase2Draft`
- `GapAnalysis`
- updated workspace fields
- updated candidate, criterion, evidence, and recommendation `decision_version_id`

- [ ] **Step 3: Add API calls**

Add:

- `updateDraft(sessionId, draft)`
- `createDecisionVersion(sessionId)`
- `getWorkspace(sessionId, versionId?)`

Keep existing `startRun(sessionId)` for Phase 1.

- [ ] **Step 4: Add workbench helpers**

Create `apps/web/src/workbench.ts` with pure helpers for active version, recommendation, score-cell lookup, evidence lookup, and gap summary.

- [ ] **Step 5: Run frontend checks**

Run:

```bash
cd apps/web
npm test -- --run App.test.tsx
npm run build
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/types.ts apps/web/src/api.ts apps/web/src/workbench.ts apps/web/src/App.test.tsx
git commit -m "feat: add phase2 frontend workspace helpers"
```

## Task 6: Interactive Workbench UI

**Files:**

- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/App.css`
- Modify: `apps/web/src/App.test.tsx`

- [ ] **Step 1: Write failing UI interaction tests**

Tests must prove:

- completed Phase 1 workspace shows version 1,
- user can edit candidate controls, constraints, custom candidate, and weights,
- each edit updates the draft and shows gap analysis without creating a version,
- `Run targeted re-research` creates a version only when draft changes exist,
- stale workspace responses cannot overwrite the selected session/version,
- evidence matrix cells open the evidence drawer,
- ADR view changes with active version.

Run:

```bash
cd apps/web
npm test -- --run App.test.tsx
```

Expected: FAIL because UI controls do not exist.

- [ ] **Step 2: Add workbench state and handlers**

Add local UI state for:

- selected version,
- draft edits,
- custom candidate form,
- weight drafts,
- selected score cell or evidence item,
- busy/error state for draft and version actions.

Handlers should call:

- `updateDraft`
- `createDecisionVersion`
- `getWorkspace`

Keep the existing stale-response guard pattern from Phase 1.

- [ ] **Step 3: Render controls and review surfaces**

Render:

- version rail or selector,
- recommendation summary,
- candidate controls,
- explicit constraint controls,
- weight editor,
- custom candidate form,
- gap analysis panel,
- evidence matrix from `score_cells`,
- evidence drawer,
- ADR view,
- trace events.

- [ ] **Step 4: Add focused CSS**

CSS must keep the app workbench-oriented:

- no landing page,
- no nested cards,
- no hero treatment,
- stable matrix layout,
- responsive controls,
- no overlapping text,
- readable drawer and ADR text.

- [ ] **Step 5: Run frontend verification**

Run:

```bash
cd apps/web
npm test -- --run App.test.tsx
npm run build
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/App.tsx apps/web/src/App.css apps/web/src/App.test.tsx
git commit -m "feat: add phase2 interactive workbench ui"
```

## Task 7: End-To-End Verification And Documentation

**Files:**

- Modify: `README.md`
- Modify: `docs/accepted-debt.md`
- Modify: `docs/superpowers/plans/2026-06-04-puppyrun-phase-2-plan.md`

- [ ] **Step 1: Run backend verification**

Run:

```bash
cd backend
ruff check .
pytest -q
```

Expected: PASS.

- [ ] **Step 2: Run frontend verification**

Run:

```bash
cd apps/web
npm test -- --run
npm run build
```

Expected: PASS.

- [ ] **Step 3: Run Docker smoke**

Run:

```bash
cp .env.example .env
docker compose up --build -d
curl http://localhost:8000/health
docker compose ps
```

Expected health response:

```json
{"status":"ok","service":"puppyrun-api"}
```

Expected `docker compose ps`: `api`, `worker`, `web`, `postgres`, and `redis` are running or healthy.

- [ ] **Step 4: Verify browser flow**

Open `http://localhost:5173` and verify:

1. Create a session with:

```text
Compare LangGraph, OpenAI Agents SDK, CrewAI, and AutoGen for a Python web Agent runtime that must support checkpointing, human approval, and traceable tool calls.
```

2. Answer clarification:

```text
Must support checkpointing and human approval. Python is preferred. Observability matters more than popularity.
```

3. Run Phase 1 Agent.
4. Wait for `completed`.
5. Confirm version 1 exists.
6. Require checkpointing from workbench controls.
7. Set `Runtime control and state` weight to `40`.
8. Add custom candidate:

```text
Name: AutoGen
Slug: autogen
Repository: microsoft/autogen
```

9. Confirm gap analysis appears before rerun and names only necessary GitHub fetches.
10. Click `Run targeted re-research`.
11. Wait for `completed`.

Expected browser result:

- Version selector shows `v1` and `v2`.
- `v2` recommendation summary starts with `Recommended v2:`.
- Evidence matrix has clickable score cells.
- Clicking a matrix cell opens the evidence drawer.
- Gap analysis lists changed inputs and research/reuse tasks.
- ADR view title starts with `ADR 0002:`.
- Trace includes `phase2_started`, `targeted_research_planned`, and `recommendation_version_created`.

- [ ] **Step 5: Run diff check**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 6: Update README**

Add Phase 2 local smoke instructions and browser acceptance steps. Do not document real public hosts, raw IPs, SSH targets, tokens, credentials, or secrets.

- [ ] **Step 7: Update accepted debt**

Under `AD-001`, add:

```markdown
- **Phase 2 note:** Phase 2 does not reinterpret negated free-form text. It adds structured workbench controls for explicit constraints, candidate include/exclude decisions, and weight overrides. The raw deterministic parser debt remains open for free-form clarification text, while Phase 2 preference editing avoids the ambiguity through explicit controls.
```

Update `Last updated` to the actual completion date.

- [ ] **Step 8: Mark plan closure status**

After implementation and verification, add this under `Tech Stack`:

```markdown
**Closure status, YYYY-MM-DD:** Phase 2 implemented and verified locally with backend tests, frontend tests, production build, Docker Compose, and browser smoke test. Public VPS redeployment status is recorded separately because real public hosts and SSH targets are private operational details.
```

Use the actual completion date.

- [ ] **Step 9: Commit docs and closure**

```bash
git add README.md docs/accepted-debt.md docs/superpowers/plans/2026-06-04-puppyrun-phase-2-plan.md
git commit -m "docs: document phase2 workbench verification"
```

## Full Verification Before Merge

Run from repo root after Task 7:

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

Then verify browser flow at `http://localhost:5173`:

- Create a session.
- Answer clarification.
- Run Phase 1.
- Confirm version 1 exists.
- Apply at least one explicit constraint.
- Change at least one criterion weight.
- Add or exclude one candidate.
- Review pre-rerun gap analysis.
- Run targeted re-research.
- Confirm `v2`, evidence drawer, evidence matrix, gap analysis, ADR, and Phase 2 trace events.

## Self-Review

Before merge, run this review yourself:

1. **Spec coverage:** Confirm every Phase 2 scope item maps to a task:
   - Candidate add/remove/lock: Tasks 2, 3, 6.
   - Must include / must exclude: Tasks 2, 3, 6.
   - Weight editing: Tasks 2, 3, 6.
   - Evidence drawer: Task 6.
   - Evidence matrix: Tasks 1, 3, 4, 6.
   - Decision versions: Tasks 1, 4, 6.
   - Gap analysis: Tasks 2, 3, 4, 6.
   - Targeted re-research: Tasks 2, 4, 7.
   - ADR view: Tasks 3, 4, 6.
2. **Placeholder scan:** Search changed code and this plan for unresolved placeholder markers.
3. **Type consistency:** Confirm `decision_version_id`, `source_version_id`, `active_version`, `phase2_draft`, `gap_analysis`, `score_cells`, `selection_state`, `is_locked`, and `adr` names match across models, schemas, TypeScript types, tests, and UI.
4. **Phase boundary:** Confirm no live LLM calls, broad web search, auth, billing, RBAC, SSE/WebSockets, export jobs, or private repository access were added.
5. **Failure semantics:** Confirm a failed Phase 2 run leaves prior completed versions readable and records failed status on the new version/run.
6. **Accepted debt:** Confirm `AD-001` remains raw-text parser debt and Phase 2 uses explicit structured controls instead of silently claiming natural-language negation is fixed.

## Execution Recommendation

Use subagent-driven execution:

1. Task 1 is data model and workspace shape.
2. Task 2 is draft API and pre-rerun gap analysis.
3. Task 3 is pure deterministic Phase 2 logic.
4. Task 4 is workflow, worker, and persistence.
5. Task 5 is frontend types, API, and helpers.
6. Task 6 is frontend UX.
7. Task 7 is release verification and docs.

Keep one implementer and two reviewers per task: one spec-compliance reviewer and one code-quality reviewer. Do not begin the next task until the prior task has passing targeted checks and an explicit review gate.

## Controller Prompt Skeleton

Use this skeleton when starting a task-specific Codex thread:

```text
Repo: /Users/jianghuilai/.codex/worktrees/2079/puppy-run
Branch: codex/phase2
Starting commit: <current HEAD before task>

Task: Implement Phase 2 Task <N>: <task name> from docs/superpowers/plans/2026-06-04-puppyrun-phase-2-plan.md.

Scope:
- Implement only this task.
- Preserve Phase 1 behavior unless this task explicitly changes it.
- Do not introduce live LLM calls.
- Do not implement MCP, community risk verification, eval dashboard, accounts, RBAC, billing, private repo access, SSE/WebSocket streaming, or export jobs.
- Do not commit real hosts, IPs, SSH targets, tokens, credentials, or secrets.
- Do not fix accepted debt unless this task explicitly says to update the Phase 2 note under AD-001.

Required start checks:
- git status --short --branch
- git rev-parse HEAD
- git log --oneline --decorate -n 8
- read docs/superpowers/plans/2026-06-04-puppyrun-phase-2-plan.md
- read docs/accepted-debt.md if touching clarification, extraction, recommendation, or workflow behavior

Required verification:
- Run the narrow tests named in the task.
- Run broader backend/frontend/Docker checks only when the task crosses those boundaries.
- Always run git diff --check.

Expected final report:
1. What changed.
2. Files changed.
3. Verification commands and results.
4. What scope was intentionally not touched.
5. Residual risks or skipped checks.

Stop condition:
- Stop after this task is implemented and verified, or when genuinely blocked.
```
