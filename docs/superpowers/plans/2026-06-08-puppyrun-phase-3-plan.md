# PuppyRun Phase 3 Evidence And Risk Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The intended controller flow is one implementer, one spec-compliance reviewer, and one code-quality reviewer per task.

**Goal:** Implement full Phase 3 evidence and risk verification for PuppyRun: external evidence collection, claim extraction, risk clustering, verification tasks, credibility scoring, conservative score impact, and workbench UI visibility.

**Architecture:** Extend the existing modular monolith with a foundation-first sequence: versioned risk data models, Tool Runtime, source adapters, LLM provider abstraction, workflow/scoring integration, and workbench UI extensions. Preserve the existing Phase 1 and Phase 2 decision-version workflow, stale-response protections, and failure semantics. Do not add MCP adapters, eval dashboard, auth/RBAC/billing, private repo access, SSE/WebSockets, or export jobs in this phase.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, arq, httpx, PostgreSQL, Redis, Pydantic, OpenAI Responses API with Structured Outputs, Tavily Search API, Stack Exchange API, arXiv API, React 19, TypeScript, Vite, Vitest, Testing Library, Docker Compose.

---

## Current Planning Baseline

This plan is intended to be implemented from `main` on a new `codex/phase3` branch. Before starting implementation, the controller must verify current repo truth instead of relying on this planning snapshot:

```bash
git status --short --branch
git rev-parse HEAD
git log --oneline --decorate -n 8
git rev-parse main
git merge-base --is-ancestor codex/phase2 main
```

At plan-writing time, Phase 2 is locally documented as closed in `docs/superpowers/plans/2026-06-04-puppyrun-phase-2-plan.md`, and `main` already contains the Phase 2 merge. Re-verify that before creating `codex/phase3`.

## Scope

In scope:

- Tool Runtime foundation with registered tools, timeout, retry, idempotency key, normalized results, and persisted tool-call traces.
- Versioned `ToolCall`, `Claim`, `RiskSignal`, and `VerificationTask` models.
- Source profiles for built-in candidates.
- Built-in candidate catalog expansion to LangGraph, OpenAI Agents SDK, CrewAI, AutoGen, and Dify.
- GitHub issue/release signals, direct official-docs fetch, Tavily docs/blog/HN search, Stack Exchange advanced search, arXiv search, and gated Reddit adapter.
- Provider-abstracted LLM extraction with deterministic fallback and OpenAI Responses API adapter.
- Risk verification pipeline and conservative risk score adjustment.
- Phase 1 and Phase 2 workflow integration.
- Existing single-page workbench extensions for risk, claims, verification tasks, source filters, and risk details.
- README, `.env.example`, and plan closure documentation.

Out of scope:

- MCP adapters.
- Eval dashboard or regression-eval UI.
- User accounts, billing, RBAC, organization workspaces, or private repository access.
- SSE/WebSocket streaming.
- Export jobs.
- Full raw community-thread storage.
- Real public hosts, raw IPs, SSH targets, tokens, credentials, or secrets in repo docs.

## Interfaces And Defaults

Add these settings to `backend/puppyrun_api/config.py` and `.env.example`:

```text
PUPPYRUN_LLM_PROVIDER=deterministic
PUPPYRUN_OPENAI_MODEL=gpt-5.5
PUPPYRUN_OPENAI_API_KEY=
PUPPYRUN_OPENAI_BASE_URL=
PUPPYRUN_TAVILY_API_KEY=
PUPPYRUN_ENABLE_REDDIT=false
PUPPYRUN_TOOL_TIMEOUT_SECONDS=10
PUPPYRUN_TOOL_RETRY_COUNT=1
PUPPYRUN_PHASE3_MAX_RESULTS_PER_SOURCE=5
```

Supported LLM provider values:

- `deterministic`: default, no external LLM calls, stable local tests and demos.
- `openai`: uses the OpenAI Responses API with Structured Outputs.

Risk score policy:

- Only `confirmed` risks affect scores.
- `low` severity: `-2`.
- `medium` severity: `-5`.
- `high` severity: `-8`.
- Maximum candidate-level risk adjustment per version: `-15`.
- `contradicted`, `unresolved`, and `unverified` risks have no score impact.

External content storage policy:

- Store canonical URL, source metadata, short citation or snippet, normalized summary, credibility, hash, status, and timestamps.
- Do not store complete raw community threads.
- Do not persist secrets or authorization headers in `ToolCall` payloads.

Workspace API extension:

- Keep `GET /api/v1/sessions/{session_id}/workspace`.
- Preserve `version_id` filtering behavior.
- Add `claims`, `risk_signals`, `verification_tasks`, and `tool_calls` to `WorkspaceResponse`.

## Source And Provider Decisions

Candidate source profiles:

- Add `official_docs_urls`, `docs_domains`, `blog_queries`, `stackexchange_tags`, `arxiv_queries`, `hn_queries`, and `reddit_queries` to built-in candidate profiles.
- Built-in candidates: LangGraph, OpenAI Agents SDK, CrewAI, AutoGen, and Dify.
- Custom candidates keep working. Generate a minimal source profile from the custom candidate name, slug, and repo.

Source adapters:

- GitHub: extend the existing client to collect issue and release signals in addition to repository summaries.
- Official docs: fetch curated profile URLs directly.
- Tavily: use for docs/blog discovery and HN site-restricted search.
- HN: search via Tavily restricted to `news.ycombinator.com`, because the HN Firebase API is item-oriented and lacks practical keyword search.
- Stack Exchange: use `/search/advanced`.
- arXiv: use the arXiv query API.
- Reddit: implement a gated adapter and tests, but default live calls to disabled unless env and policy configuration are explicitly present.

LLM providers:

- `DeterministicLLMProvider` is required and must be the default.
- `OpenAILLMProvider` must use provider abstraction and Structured Outputs through Pydantic schemas.
- Provider schemas cover claim extraction, risk clustering, verification verdicts, and final risk summaries.
- Missing LLM credentials must not break local test or Docker smoke paths.

## Task 1: Branch, Plan Header, And Baseline Audit

**Files:**

- Modify later only if needed: `docs/superpowers/plans/2026-06-08-puppyrun-phase-3-plan.md`

- [ ] **Step 1: Verify repo state**

Run:

```bash
git status --short --branch
git rev-parse HEAD
git log --oneline --decorate -n 8
git rev-parse main
git merge-base --is-ancestor codex/phase2 main
```

Expected:

- Worktree is clean.
- Phase 2 is merged into `main`.
- Current implementation branch is created from `main`, not from stale detached state.

- [ ] **Step 2: Create or switch to Phase 3 branch**

Run:

```bash
git switch main
git pull --ff-only
git switch -c codex/phase3
```

If `codex/phase3` already exists, inspect it first:

```bash
git branch --list codex/phase3 --format='%(refname:short) %(objectname:short) %(worktreepath)'
git log --oneline --decorate main..codex/phase3
```

Do not overwrite existing Phase 3 work. If the branch is already checked out in another worktree, work in that worktree or ask the controller to resolve branch ownership.

- [ ] **Step 3: Reread relevant specs**

Run:

```bash
rg -n "Phase 3|Evidence And Risk Verification|Tool Runtime|risk_signals|verification_tasks|claims|Reddit" docs/superpowers/specs docs/superpowers/plans docs/accepted-debt.md
```

Expected:

- Phase 3 scope is grounded in `docs/superpowers/specs/2026-05-21-puppyrun-design.md`.
- Phase 2 live-LLM and broad-search exclusions are treated as Phase 2 boundaries, not Phase 3 blockers.

- [ ] **Step 4: Commit planning baseline only if changed**

If the implementation agent updates this plan during execution:

```bash
git add docs/superpowers/plans/2026-06-08-puppyrun-phase-3-plan.md
git commit -m "docs: add phase3 evidence risk plan"
```

## Task 2: Data Model And Migration

**Files:**

- Modify: `backend/puppyrun_api/models.py`
- Modify: `backend/puppyrun_api/schemas.py`
- Modify: `backend/puppyrun_api/repositories/workspace.py`
- Create: `backend/migrations/versions/0004_phase3_evidence_risk.py`
- Modify tests: `backend/tests/test_phase2_workspace_api.py` or create `backend/tests/test_phase3_workspace_api.py`

- [ ] **Step 1: Write failing workspace/API tests**

Add tests proving the workspace returns version-filtered `tool_calls`, `claims`, `risk_signals`, and `verification_tasks`.

Required assertions:

- All four collections exist in the JSON response.
- Rows for the active version are returned.
- `version_id` returns only rows for the requested version.
- Older completed versions remain readable.

Run:

```bash
cd backend
pytest -q tests/test_phase3_workspace_api.py
```

Expected: FAIL because the models and schema fields do not exist.

- [ ] **Step 2: Add SQLAlchemy models**

Add `ToolCall`, `Claim`, `RiskSignal`, and `VerificationTask`.

Model requirements:

- All tables include `id`, `session_id`, `decision_version_id`, `created_at`, and `updated_at` where updates are expected.
- `ToolCall` stores `tool_name`, `status`, `idempotency_key`, `source_type`, `source_url`, `request_summary`, `response_summary`, `payload`, `error`, `started_at`, and `completed_at`.
- `Claim` stores `candidate_id`, `criterion_id`, `source_evidence_item_id`, `source_type`, `source_url`, `title`, `summary`, `citation_text`, `credibility`, `confidence`, `content_hash`, `payload`.
- `RiskSignal` stores `candidate_id`, `risk_key`, `title`, `summary`, `severity`, `status`, `credibility`, `score_impact`, `supporting_claim_ids`, `verification_task_ids`, `payload`.
- `VerificationTask` stores `candidate_id`, `risk_signal_id`, `status`, `verification_question`, `stronger_source_type`, `stronger_source_url`, `verdict`, `rationale`, `payload`.
- Use JSON columns with the repo's existing recursive mutable JSON helpers where mutation tracking is needed.
- Add relationships back to `DecisionSession` and `DecisionVersion`.

- [ ] **Step 3: Add Alembic migration**

Create `0004_phase3_evidence_risk.py`.

Upgrade order:

1. `tool_calls`
2. `claims`
3. `risk_signals`
4. `verification_tasks`

Downgrade order:

1. `verification_tasks`
2. `risk_signals`
3. `claims`
4. `tool_calls`

- [ ] **Step 4: Add Pydantic response schemas and workspace fields**

Add response models:

- `ToolCallResponse`
- `ClaimResponse`
- `RiskSignalResponse`
- `VerificationTaskResponse`

Extend `WorkspaceResponse` with:

```python
tool_calls: list[ToolCallResponse]
claims: list[ClaimResponse]
risk_signals: list[RiskSignalResponse]
verification_tasks: list[VerificationTaskResponse]
```

- [ ] **Step 5: Load versioned rows in workspace repository**

Extend `Workspace` dataclass and `get_workspace()` to include the four new collections.

Version filtering rule:

- If `active_version` exists, return rows with that `decision_version_id`.
- If no version exists, return legacy rows with `decision_version_id IS NULL`.

- [ ] **Step 6: Verify migration and API tests**

Run:

```bash
cd backend
pytest -q tests/test_phase3_workspace_api.py
pytest -q tests/test_phase2_workspace_api.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/puppyrun_api/models.py backend/puppyrun_api/schemas.py backend/puppyrun_api/repositories/workspace.py backend/migrations/versions/0004_phase3_evidence_risk.py backend/tests/test_phase3_workspace_api.py backend/tests/test_phase2_workspace_api.py
git commit -m "feat: add phase3 risk verification models"
```

## Task 3: Tool Runtime Foundation

**Files:**

- Create: `backend/puppyrun_agent/tool_runtime.py`
- Create tests: `backend/tests/test_phase3_tool_runtime.py`
- Modify if needed: `backend/puppyrun_api/models.py`

- [ ] **Step 1: Write failing runtime tests**

Test behaviors:

- Successful tool call persists `ToolCall(status="completed")`.
- Missing credentials or disabled adapter persists `status="skipped"`.
- A transient failure retries once by default.
- A final failure persists `status="failed"` without storing secrets.
- The persisted payload does not include `Authorization`, API keys, tokens, or raw full community content.

Run:

```bash
cd backend
pytest -q tests/test_phase3_tool_runtime.py
```

Expected: FAIL because runtime does not exist.

- [ ] **Step 2: Implement runtime primitives**

Add:

- `ToolResult`
- `ToolContext`
- `ToolRuntime`
- `RegisteredTool`
- `sanitize_payload()`
- `content_hash()`

Runtime behavior:

- Executes registered async tool functions.
- Applies `PUPPYRUN_TOOL_TIMEOUT_SECONDS`.
- Retries up to `PUPPYRUN_TOOL_RETRY_COUNT`.
- Uses deterministic idempotency keys from tool name, session, version, candidate, query, and URL inputs.
- Persists normalized summaries, not raw secrets.

- [ ] **Step 3: Verify runtime tests**

Run:

```bash
cd backend
pytest -q tests/test_phase3_tool_runtime.py
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/puppyrun_agent/tool_runtime.py backend/tests/test_phase3_tool_runtime.py backend/puppyrun_api/models.py
git commit -m "feat: add phase3 tool runtime"
```

## Task 4: Candidate Source Profiles

**Files:**

- Modify: `backend/puppyrun_agent/catalog.py`
- Modify tests: create or extend `backend/tests/test_phase3_catalog.py`

- [ ] **Step 1: Write failing catalog tests**

Test behaviors:

- Built-in registry contains LangGraph, OpenAI Agents SDK, CrewAI, AutoGen, and Dify.
- Each built-in candidate has non-empty source profile fields.
- Custom candidate profiles derive reasonable default queries from name, slug, and repo.
- Existing Phase 1/Phase 2 candidate selection remains deterministic.

Run:

```bash
cd backend
pytest -q tests/test_phase3_catalog.py tests/test_phase1_workflow.py tests/test_phase2_agent.py
```

Expected: new catalog tests FAIL before implementation.

- [ ] **Step 2: Extend candidate profile type**

Add source profile fields:

```python
official_docs_urls: tuple[str, ...]
docs_domains: tuple[str, ...]
blog_queries: tuple[str, ...]
stackexchange_tags: tuple[str, ...]
arxiv_queries: tuple[str, ...]
hn_queries: tuple[str, ...]
reddit_queries: tuple[str, ...]
```

- [ ] **Step 3: Add AutoGen and Dify**

Add built-in candidates:

- AutoGen: repository `microsoft/autogen`.
- Dify: repository `langgenius/dify`.

Keep existing candidates and ordering stable enough for existing tests. If selection remains capped at three candidates for Phase 1, preserve that behavior unless the tests explicitly target the expanded registry rather than default selection count.

- [ ] **Step 4: Add custom profile helper**

Add a helper that returns a source profile for custom candidates using:

- Candidate name.
- Candidate slug.
- `repo_full_name`.
- Generic query strings such as `<name> agent framework`, `<repo> issues`, and `<name> risk`.

- [ ] **Step 5: Verify catalog tests**

Run:

```bash
cd backend
pytest -q tests/test_phase3_catalog.py tests/test_phase1_workflow.py tests/test_phase2_agent.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/puppyrun_agent/catalog.py backend/tests/test_phase3_catalog.py
git commit -m "feat: add phase3 source profiles"
```

## Task 5: Source Adapters

**Files:**

- Modify: `backend/puppyrun_agent/github_client.py`
- Create: `backend/puppyrun_agent/source_adapters.py`
- Create tests: `backend/tests/test_phase3_source_adapters.py`
- Modify: `backend/puppyrun_api/config.py`
- Modify: `.env.example`

- [ ] **Step 1: Write failing adapter tests**

Test behaviors:

- GitHub issue/release adapter normalizes mocked GitHub responses.
- Direct docs adapter stores only URL, title, summary/snippet, hash, and metadata.
- Tavily adapter skips without API key and completes with mocked API key.
- HN adapter uses Tavily site-restricted search.
- Stack Exchange adapter normalizes `/search/advanced` results.
- arXiv adapter normalizes Atom feed results.
- Reddit adapter skips by default and does not live-call when `PUPPYRUN_ENABLE_REDDIT=false`.

Run:

```bash
cd backend
pytest -q tests/test_phase3_source_adapters.py
```

Expected: FAIL because adapters do not exist.

- [ ] **Step 2: Extend settings**

Add settings and env examples:

```text
PUPPYRUN_TAVILY_API_KEY=
PUPPYRUN_ENABLE_REDDIT=false
PUPPYRUN_PHASE3_MAX_RESULTS_PER_SOURCE=5
```

Also add tool timeout/retry and LLM fields if not already added by earlier tasks.

- [ ] **Step 3: Implement normalized evidence result type**

Add a small typed structure for source results containing:

- `source_type`
- `source_url`
- `title`
- `summary`
- `citation_text`
- `credibility`
- `candidate_slug`
- `metadata`
- `content_hash`

- [ ] **Step 4: Implement adapters**

Adapters:

- `GitHubIssueReleaseAdapter`
- `DirectDocsAdapter`
- `TavilySearchAdapter`
- `HackerNewsSearchAdapter`
- `StackExchangeAdapter`
- `ArxivAdapter`
- `RedditAdapter`

All adapters must run through Tool Runtime and return skipped results instead of raising for missing optional credentials.

- [ ] **Step 5: Verify adapter tests**

Run:

```bash
cd backend
pytest -q tests/test_phase3_source_adapters.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/puppyrun_agent/github_client.py backend/puppyrun_agent/source_adapters.py backend/puppyrun_api/config.py backend/tests/test_phase3_source_adapters.py .env.example
git commit -m "feat: add phase3 source adapters"
```

## Task 6: LLM Provider Abstraction And Deterministic Extraction

**Files:**

- Create: `backend/puppyrun_agent/llm_providers.py`
- Create tests: `backend/tests/test_phase3_llm_providers.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/puppyrun_api/config.py`
- Modify: `.env.example`

- [ ] **Step 1: Write failing provider tests**

Test behaviors:

- Deterministic provider returns stable claims, risks, verification tasks, and verdicts from fixed evidence snippets.
- OpenAI provider builds a Responses API request with a JSON schema structured-output shape.
- OpenAI provider does not run when `PUPPYRUN_OPENAI_API_KEY` is missing.
- Provider outputs validate against Pydantic schemas.

Run:

```bash
cd backend
pytest -q tests/test_phase3_llm_providers.py
```

Expected: FAIL because providers do not exist.

- [ ] **Step 2: Add dependency and settings**

Add the OpenAI Python SDK dependency if using the official SDK:

```toml
"openai>=1.0.0"
```

Add settings:

```text
PUPPYRUN_LLM_PROVIDER=deterministic
PUPPYRUN_OPENAI_MODEL=gpt-5.5
PUPPYRUN_OPENAI_API_KEY=
PUPPYRUN_OPENAI_BASE_URL=
```

- [ ] **Step 3: Add Pydantic output schemas**

Schemas:

- `ExtractedClaim`
- `ExtractedClaims`
- `RiskCluster`
- `RiskClusters`
- `VerificationPlan`
- `VerificationVerdict`
- `RiskSynthesis`

Use bounded string/list sizes where practical.

- [ ] **Step 4: Implement deterministic provider**

Rules:

- Claims are generated from source summaries and citation snippets.
- Low-trust source types may create risk candidates but not confirmed risks.
- Verification verdicts are deterministic from stronger evidence source types.
- Outputs are stable for tests.

- [ ] **Step 5: Implement OpenAI provider**

Rules:

- Use provider abstraction so workflow code does not directly depend on OpenAI.
- Use Responses API Structured Outputs.
- Use stable prompt prefixes and dynamic evidence at the end.
- Do not log or persist API keys.
- Convert refusals/errors into provider-level skipped or failed results with sanitized messages.

- [ ] **Step 6: Verify provider tests**

Run:

```bash
cd backend
pytest -q tests/test_phase3_llm_providers.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/puppyrun_agent/llm_providers.py backend/tests/test_phase3_llm_providers.py backend/pyproject.toml backend/puppyrun_api/config.py .env.example
git commit -m "feat: add phase3 llm providers"
```

## Task 7: Risk Verification Pipeline

**Files:**

- Create: `backend/puppyrun_agent/phase3.py`
- Create tests: `backend/tests/test_phase3_agent.py`

- [ ] **Step 1: Write failing pure-function tests**

Test behaviors:

- Evidence normalization maps source type to credibility tier.
- Community evidence creates unverified or unresolved risk signals.
- Stronger official docs, GitHub issues/releases, or credible technical writeups can confirm or contradict a risk.
- Confirmed risks get conservative score impact.
- Risk impact caps at `-15` per candidate.
- Unverified, unresolved, and contradicted risks do not affect score.
- Full raw community content is not returned from pipeline outputs.

Run:

```bash
cd backend
pytest -q tests/test_phase3_agent.py
```

Expected: FAIL because pipeline does not exist.

- [ ] **Step 2: Implement normalized evidence helpers**

Helpers should produce stable dictionaries from source adapter results and existing `EvidenceItem` rows.

- [ ] **Step 3: Implement claim extraction orchestration**

Use LLM provider outputs when available. Use deterministic provider by default.

- [ ] **Step 4: Implement risk clustering and verification task planning**

Rules:

- Risks are grouped by candidate and normalized risk key.
- Low-trust claims generate `RiskSignal(status="unverified")`.
- Verification tasks must point to stronger evidence targets when available.

- [ ] **Step 5: Implement verification verdict mapping**

Statuses:

- `confirmed`
- `contradicted`
- `unresolved`
- `unverified`

Only `confirmed` gets non-zero score impact.

- [ ] **Step 6: Implement score adjustment helper**

Use the policy:

```text
low=-2
medium=-5
high=-8
cap=-15
```

- [ ] **Step 7: Verify pure-function tests**

Run:

```bash
cd backend
pytest -q tests/test_phase3_agent.py
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/puppyrun_agent/phase3.py backend/tests/test_phase3_agent.py
git commit -m "feat: add phase3 risk verification pipeline"
```

## Task 8: Workflow And Scoring Integration

**Files:**

- Modify: `backend/puppyrun_agent/workflow.py`
- Modify: `backend/puppyrun_agent/recommendation.py`
- Modify tests: `backend/tests/test_phase1_workflow.py`
- Modify tests: `backend/tests/test_phase2_workflow.py`
- Create tests if needed: `backend/tests/test_phase3_workflow.py`

- [ ] **Step 1: Write failing workflow tests**

Test behaviors:

- Phase 1 creates claims, risk signals, verification tasks, tool calls, and Phase 3 events.
- Phase 2 creates versioned Phase 3 rows for the new version.
- Phase 2 reuses unchanged evidence where possible.
- Missing Tavily/OpenAI/Reddit credentials skip gracefully.
- Confirmed high risk changes candidate score and recommendation rationale.
- Failed Phase 3 source or provider work records sanitized failure detail without hiding prior completed versions.

Run:

```bash
cd backend
pytest -q tests/test_phase3_workflow.py
```

Expected: FAIL before integration.

- [ ] **Step 2: Add Phase 3 planning after GitHub evidence**

Persist event:

- `phase3_sources_planned`

Include counts by source type in payload.

- [ ] **Step 3: Collect source evidence through Tool Runtime**

Create `EvidenceItem` rows for normalized source results. Credibility tiers:

- `high`: official docs, source code, official releases.
- `medium`: GitHub issues/PRs, technical blogs, papers, benchmarks.
- `low`: Reddit, Hacker News, Stack Overflow comments, community discussion.

- [ ] **Step 4: Extract claims and risks**

Persist events:

- `claims_extracted`
- `risks_clustered`
- `verification_tasks_created`

- [ ] **Step 5: Verify risks**

Persist event:

- `risk_verification_completed`

Write `Claim`, `RiskSignal`, and `VerificationTask` rows for the current version.

- [ ] **Step 6: Apply score adjustments**

Persist event:

- `risk_adjusted_scores`

Update candidate scores and recommendation rationale. Do not allow risk adjustment to make score negative.

- [ ] **Step 7: Preserve Phase 2 failure semantics**

If Phase 3 fails during a Phase 2 rerun:

- Mark only the new version failed.
- Preserve prior completed versions.
- Keep source workspace readable.
- Store sanitized failure in version gap analysis or event payload.

- [ ] **Step 8: Verify workflow tests**

Run:

```bash
cd backend
pytest -q tests/test_phase3_workflow.py tests/test_phase1_workflow.py tests/test_phase2_workflow.py
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/puppyrun_agent/workflow.py backend/puppyrun_agent/recommendation.py backend/tests/test_phase3_workflow.py backend/tests/test_phase1_workflow.py backend/tests/test_phase2_workflow.py
git commit -m "feat: integrate phase3 risk verification workflow"
```

## Task 9: Recommendation, Score Cells, And ADR

**Files:**

- Modify: `backend/puppyrun_agent/recommendation.py`
- Modify: `backend/puppyrun_agent/phase2.py`
- Modify: `backend/puppyrun_agent/phase3.py`
- Modify tests: `backend/tests/test_phase2_agent.py`
- Modify tests: `backend/tests/test_phase3_agent.py`

- [ ] **Step 1: Write failing recommendation tests**

Test behaviors:

- Recommendation rationale includes risk adjustments.
- Score-cell explanations include confirmed risk impact when relevant.
- ADR risk section includes confirmed, contradicted, and unresolved risks.
- Unverified risks appear in risk panel data but do not change scores.

Run:

```bash
cd backend
pytest -q tests/test_phase3_agent.py tests/test_phase2_agent.py
```

Expected: FAIL where risk-aware outputs are missing.

- [ ] **Step 2: Add risk adjustment to weighted recommendation**

Rationale should include:

- `base_weighted_score`
- `risk_adjustment`
- `weighted_score`
- `confirmed_risks`
- `unresolved_risks`
- `contradicted_risks`

- [ ] **Step 3: Extend score-cell explanation**

Only append risk impact when a confirmed risk relates to the same candidate and criterion or to the candidate overall.

- [ ] **Step 4: Extend ADR**

ADR must include:

- Confirmed risks and their evidence.
- Contradicted risks and why they were contradicted.
- Unresolved risks and what evidence remains missing.
- Score impact summary.

- [ ] **Step 5: Verify recommendation tests**

Run:

```bash
cd backend
pytest -q tests/test_phase3_agent.py tests/test_phase2_agent.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/puppyrun_agent/recommendation.py backend/puppyrun_agent/phase2.py backend/puppyrun_agent/phase3.py backend/tests/test_phase2_agent.py backend/tests/test_phase3_agent.py
git commit -m "feat: surface phase3 risk impact in decisions"
```

## Task 10: Workbench UI Extension

**Files:**

- Modify: `apps/web/src/types.ts`
- Modify: `apps/web/src/workbench.ts`
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/App.css`
- Modify tests: `apps/web/src/App.test.tsx`

- [ ] **Step 1: Write failing frontend tests**

Test behaviors:

- Workspace types include `claims`, `risk_signals`, `verification_tasks`, and `tool_calls`.
- Risk panel renders confirmed, contradicted, unresolved, and unverified risk statuses.
- Source filters filter by source type.
- Clicking a risk opens supporting claims and evidence.
- Skipped provider/tool state is visible.
- Existing stale workspace/version response protections still pass.

Run:

```bash
cd apps/web
npm test -- --run
```

Expected: FAIL before UI changes.

- [ ] **Step 2: Extend TypeScript types**

Add:

- `ToolCall`
- `Claim`
- `RiskSignal`
- `VerificationTask`

Extend `Workspace`.

- [ ] **Step 3: Add workbench helper functions**

Add helpers for:

- Active risk signals by selected version.
- Claims supporting a risk.
- Verification tasks for a risk.
- Tool calls grouped by status/source.
- Risk summary counts.

- [ ] **Step 4: Extend single-page UI**

Add to existing workbench, without adding routes:

- Risk panel.
- Claim list.
- Verification task status list.
- Source filters.
- Risk detail view.
- Skipped tool/provider state.

Keep compact, work-focused styling consistent with current UI.

- [ ] **Step 5: Preserve stale-response protections**

Do not remove or weaken existing request-id guards, selected-version refs, or stale workspace handling.

- [ ] **Step 6: Verify frontend tests**

Run:

```bash
cd apps/web
npm test -- --run
```

Expected: PASS.

- [ ] **Step 7: Verify frontend build**

Run:

```bash
cd apps/web
npm run build
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/types.ts apps/web/src/workbench.ts apps/web/src/App.tsx apps/web/src/App.css apps/web/src/App.test.tsx
git commit -m "feat: add phase3 risk workbench"
```

## Task 11: Documentation And Final Verification

**Files:**

- Modify: `README.md`
- Modify: `.env.example`
- Modify: `docs/superpowers/plans/2026-06-08-puppyrun-phase-3-plan.md`

- [ ] **Step 1: Update README**

Add Phase 3 local smoke instructions:

- Deterministic no-key smoke.
- Optional OpenAI + Tavily smoke.
- Expected risk panel, claims, verification tasks, tool calls, adjusted rationale, and ADR risk output.
- Reminder that Reddit is gated and disabled by default.

- [ ] **Step 2: Update `.env.example`**

Document env var names only. Do not include real API keys or hosts.

- [ ] **Step 3: Mark plan closure status**

After implementation and verification, add near the top:

```markdown
**Closure status, YYYY-MM-DD:** Phase 3 implemented and verified locally with backend tests, frontend tests, production build, Docker Compose, and browser smoke test. Live OpenAI/Tavily smoke status is recorded separately because credentials are private operational details.
```

Use the actual completion date.

- [ ] **Step 4: Run backend verification**

```bash
cd backend
ruff check .
pytest -q
```

Expected: PASS.

- [ ] **Step 5: Run frontend verification**

```bash
cd apps/web
npm test -- --run
npm run build
```

Expected: PASS.

- [ ] **Step 6: Run Docker smoke**

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

Expected services: `api`, `worker`, `web`, `postgres`, and `redis` are running or healthy.

- [ ] **Step 7: Run manual browser smoke**

At `http://localhost:5173`:

1. Create a session comparing LangGraph, OpenAI Agents SDK, CrewAI, AutoGen, and Dify.
2. Answer clarification with checkpointing, human approval, Python preference, observability priority, and concern about maintenance/community risk.
3. Run Phase 1.
4. Confirm version 1 exists.
5. Confirm risk panel, claims, verification tasks, tool calls, source filters, and ADR risk section appear.
6. Make a Phase 2 edit that triggers targeted rerun.
7. Run targeted re-research.
8. Confirm version 2 contains versioned Phase 3 rows and previous version remains readable.

- [ ] **Step 8: Run diff check**

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 9: Commit docs and closure**

```bash
git add README.md .env.example docs/superpowers/plans/2026-06-08-puppyrun-phase-3-plan.md
git commit -m "docs: document phase3 verification"
```

## Full Verification Before Merge

Run from repo root after Task 11:

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

Then run browser smoke at `http://localhost:5173` and confirm:

- Phase 1 creates claims, risks, verification tasks, tool calls, and Phase 3 events.
- Risk panel shows confirmed, contradicted, unresolved, and unverified statuses when fixtures or live data produce them.
- Confirmed risks affect score conservatively.
- Unverified, unresolved, and contradicted risks do not affect score.
- ADR includes risk reasoning.
- Phase 2 targeted rerun creates versioned Phase 3 rows.
- Prior completed versions remain readable after a failed or skipped Phase 3 source/provider path.

## Self-Review

Before merge, run this review yourself:

1. **Spec coverage:** Confirm every Phase 3 scope item maps to a task:
   - Official docs tool: Tasks 5, 8.
   - Blog and paper search: Tasks 5, 8.
   - arXiv integration: Tasks 5, 8.
   - HN and Stack Exchange integrations: Tasks 5, 8.
   - Gated Reddit adapter: Task 5.
   - Claim extraction: Tasks 6, 7, 8.
   - Risk signal clustering: Tasks 6, 7, 8.
   - Verification tasks: Tasks 2, 7, 8.
   - Credibility scoring: Tasks 7, 8.
   - Risk panel and UI visibility: Task 10.
2. **Phase boundary:** Confirm no MCP adapters, eval dashboard, auth/RBAC/billing, private repo access, SSE/WebSockets, or export jobs were added.
3. **Secret boundary:** Confirm no real public hosts, raw IPs, SSH targets, tokens, credentials, or secrets are committed.
4. **Raw-content boundary:** Confirm full raw community threads are not persisted.
5. **Type consistency:** Confirm backend schemas and frontend types agree on `tool_calls`, `claims`, `risk_signals`, and `verification_tasks`.
6. **Version semantics:** Confirm `version_id` filters Phase 3 rows consistently with Phase 2 rows.
7. **Failure semantics:** Confirm failed Phase 3 work in a Phase 2 rerun does not hide prior completed versions.
8. **LLM fallback:** Confirm no-key local tests and Docker smoke use deterministic provider behavior.

## Execution Recommendation

Use `superpowers:subagent-driven-development` with one task at a time.

For each task:

1. Dispatch one implementer agent with only that task's scope.
2. Require TDD: failing test first, red result, implementation, green result.
3. Dispatch a spec-compliance reviewer.
4. Fix only approved spec issues inside that task's scope.
5. Dispatch a code-quality reviewer.
6. Fix only approved quality issues inside that task's scope.
7. Commit before moving to the next task.

Do not dispatch multiple implementers in parallel unless their write sets are disjoint and the controller can reconcile them safely. The default should be serial tasks because this phase touches shared models, workflow, and API contracts.

## External References

- OpenAI Structured Outputs: <https://developers.openai.com/api/docs/guides/structured-outputs>
- OpenAI latest model guidance: <https://developers.openai.com/api/docs/guides/latest-model.md>
- Tavily Search API: <https://docs.tavily.com/documentation/api-reference/endpoint/search>
- Stack Exchange advanced search: <https://api.stackexchange.com/docs/advanced-search>
- arXiv API manual: <https://info.arxiv.org/help/api/user-manual.html>
- HN API: <https://github.com/hackernews/api>
- Reddit API overview: <https://developers.reddit.com/docs/capabilities/server/reddit-api>
