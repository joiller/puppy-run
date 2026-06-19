# PuppyRun Phase 4 Live Eval Design

Date: 2026-06-19

## 1. Decision

Phase 4 v1 will establish a local, manually run DeepSeek live regression eval gate.

The first Phase 4 priority is eval quality, not an observability dashboard. The primary provider is DeepSeek, using `PUPPYRUN_LLM_PROVIDER=deepseek` with a private `PUPPYRUN_DEEPSEEK_API_KEY` supplied by the local operator. Deterministic behavior remains useful for development and failure isolation, but it is not the main Phase 4 v1 acceptance path.

The implementation should add an independent backend eval package named `puppyrun_eval`, with a command shaped like:

```bash
cd backend
PUPPYRUN_LLM_PROVIDER=deepseek \
PUPPYRUN_DEEPSEEK_API_KEY=<private value> \
.venv/bin/python -m puppyrun_eval run --suite phase4-live
```

The suite should produce structured machine-readable JSON and a human-readable Markdown report under `backend/.eval-reports/`.

## 2. Context

Phase 3 added live LLM-backed evidence and risk verification. It also exposed a class of defects that ordinary deterministic tests could miss: provider wiring and schema compatibility can be green locally while failing on the real provider path.

Phase 4 v1 should therefore prove that the real DeepSeek provider path can satisfy PuppyRun's core quality contracts:

- valid JSON output that passes Pydantic validation,
- required business fields such as claim and risk titles,
- conservative handling of low-trust community risk,
- usable verification tasks and verdicts,
- full workflow output that remains grounded in candidates, criteria, evidence, risk reasoning, recommendation, and ADR content.

## 3. Goals

- Provide a repeatable command that a local operator can run before release or merge decisions.
- Exercise the real DeepSeek chat-completions path instead of only fake clients or deterministic provider behavior.
- Keep eval cases small enough to run manually without turning Phase 4 into a large benchmark effort.
- Produce case-level failure reasons that distinguish provider availability, provider response, quality regression, and runner bugs.
- Keep reports safe for repository work by excluding secrets, raw credentials, and full raw community threads.
- Create a structure that can later feed an eval dashboard or optional CI workflow without redesigning the runner.

## 4. Non-Goals

Phase 4 v1 will not include:

- eval dashboard UI,
- GitHub Actions secrets or mandatory PR CI live evals,
- OpenAI versus DeepSeek provider comparison,
- production monitoring or online alerting,
- large-scale benchmark suites,
- UI/browser eval,
- private repository access,
- raw full community-thread storage,
- any committed real host, key, token, SSH target, or credential value.

## 5. Package Shape

Add a backend package:

```text
backend/
  puppyrun_eval/
    __init__.py
    __main__.py
    cases.py
    runner.py
    scoring.py
    reports.py
```

Responsibilities:

- `__main__.py`: CLI parsing and exit-code handling.
- `cases.py`: suite and case definitions.
- `runner.py`: provider setup, case execution, duration capture, exception classification.
- `scoring.py`: assertions and quality checks for provider-contract and workflow-regression outputs.
- `reports.py`: JSON and Markdown report writers.

Case definitions should use Python dataclasses in v1. This keeps the first implementation close to existing PuppyRun models and fixtures. YAML or JSON case files can be introduced later once the case library grows.

## 6. Eval Suites And Cases

The first suite is `phase4-live`.

### 6.1 Provider Contract Cases

Provider-contract cases call `DeepSeekLLMProvider` directly and should cover:

1. Claim extraction schema and field preservation.
2. Risk clustering with low-trust community-only risk demotion.
3. Verification plan, verdict, and risk synthesis on a small evidence set.

The cases use fixed evidence fixtures that resemble Phase 3 source evidence:

- high-trust official docs or release evidence,
- lower-trust community discussion evidence,
- at least one maintenance or stability risk,
- at least one source that can contradict or confirm a risk through stronger evidence.

Hard assertions:

- DeepSeek output parses as valid JSON.
- The parsed payload validates against the expected Pydantic model.
- Business fields such as `ExtractedClaim.title` and `RiskCluster.title` are present and non-empty.
- Community-only low-trust risk is not finally treated as `confirmed`.
- Verification tasks target stronger source types where available.
- Provider errors are sanitized and do not expose key, token, secret, or credential-like values.

### 6.2 Workflow Regression Cases

Workflow-regression cases run the backend workflow path with stable inputs. The first version should avoid UI, Redis, and browser dependencies. It should use fixture-backed GitHub/source inputs where possible, while keeping DeepSeek responsible for the Phase 3 extraction, risk clustering, verification, and synthesis path.

Start with one or two scenarios:

- Agent framework selection with checkpointing, human approval, Python preference, observability priority, and maintenance-risk concerns.
- A targeted rerun scenario may be added after the first Phase 4 runner is stable, but it should not block the initial suite if it makes v1 too large.

Hard assertions:

- The workflow completes rather than staying failed, queued, or researching.
- The workspace has at least three candidates and five criteria.
- GitHub evidence and Phase 3 source evidence exist.
- Claims, risk signals, verification tasks, and tool calls are produced.
- The recommendation references the winning candidate and risk adjustment facts.
- The ADR includes risk reasoning.
- DeepSeek provider failures are not silently hidden by deterministic fallback behavior.

The workflow assertions should not require exact model wording. They should verify structural quality and key facts, not brittle prose.

## 7. Result Model

Each case result should include:

- suite id,
- case id,
- case kind,
- status: `pass`, `fail`, or `blocked`,
- failure category when not passing,
- sanitized failure message,
- provider name,
- model name,
- duration,
- started and finished timestamps,
- optional token usage or cost metadata when the provider response exposes it.

The suite result passes only when all required cases pass.

Missing `PUPPYRUN_DEEPSEEK_API_KEY` is `blocked`, not `pass`. Because `phase4-live` is a live-first gate, a missing credential means the requested verification did not run.

Recommended CLI exit codes:

- `0`: all required cases passed.
- `1`: at least one case failed due to a provider response, quality regression, or runner error.
- `2`: the suite was blocked before required live verification could run.

## 8. Failure Categories

Use these categories consistently:

- `provider_unavailable`: missing API key, missing SDK, unavailable base URL, or configuration that prevents the provider from running.
- `provider_response`: request failure, invalid JSON, truncated response, schema validation failure, refusal, or provider stop condition.
- `quality_regression`: schema validation passed but the result violates PuppyRun's quality contract.
- `runner_error`: bug in the eval runner, fixture setup, report writing, or local harness.

The runner should sanitize messages before storing or printing them.

## 9. Reports

Write reports to:

```text
backend/.eval-reports/
  phase4-live-YYYYMMDD-HHMMSS.json
  phase4-live-YYYYMMDD-HHMMSS.md
```

The JSON report is the future integration surface for dashboards or CI. The Markdown report is the human-readable release-gate artifact.

Reports may include:

- suite summary,
- provider and model,
- case result table,
- duration,
- failure category,
- sanitized failure message,
- selected quality observations,
- token usage or cost metadata when available.

Reports must not include:

- API keys,
- bearer tokens,
- raw credentials,
- real private hosts or SSH targets,
- full raw community threads,
- unredacted provider exception text.

The implementation should ensure `backend/.eval-reports/` is ignored by git.

## 10. Verification Strategy

Normal local verification should remain deterministic and inexpensive:

```bash
cd backend && .venv/bin/ruff check .
cd backend && .venv/bin/pytest tests/test_phase3_llm_providers.py tests/test_phase3_workflow.py -q
cd backend && .venv/bin/pytest tests/test_phase4_eval.py -q
git diff --check
```

Live manual acceptance is separate:

```bash
cd backend
PUPPYRUN_LLM_PROVIDER=deepseek \
PUPPYRUN_DEEPSEEK_API_KEY=<private value> \
.venv/bin/python -m puppyrun_eval run --suite phase4-live
```

Do not claim Phase 4 live eval passed unless the live command ran in the current turn or current release session with a real DeepSeek key.

## 11. Implementation Plan Outline

The implementation plan should split Phase 4 v1 into these tasks:

1. Eval case and runner skeleton.
2. DeepSeek provider-contract live cases.
3. Small backend workflow-regression live cases.
4. JSON and Markdown report writer.
5. Documentation and manual release-gate instructions.

Each task should stay scoped. The first implementation should not add the dashboard, CI secrets, OpenAI comparison, browser automation, or production monitoring.

## 12. Future Extensions

After Phase 4 v1 is stable, later work can add:

- optional GitHub Actions manual-dispatch live eval,
- OpenAI provider compatibility suite,
- larger curated eval case library,
- eval dashboard consuming JSON reports,
- historical report comparison,
- cost and latency trend summaries,
- release checklist integration.
