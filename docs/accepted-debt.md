# Accepted Debt

Last updated: 2026-06-08

This file tracks known project limitations that were intentionally accepted for the current phase. Do not fix these items as part of unrelated work. Reopen one explicitly when it becomes part of the task scope.

## Open Items

### AD-001: Deterministic constraint parser does not understand negation

- **Status:** Accepted for Phase 1.
- **Area:** `backend/puppyrun_agent/clarification.py`
- **Symptom:** The deterministic parser detects constraints through simple keyword matching. Prompts such as `No TypeScript; Python only` or `without human approval gates` can still treat `typescript` or `human_in_loop` as positive constraints because the parser checks whether the keyword appears anywhere in the prompt.
- **Why accepted:** Phase 1 intentionally avoids live LLM calls and keeps clarification deterministic, cheap, and reproducible for the public demo thin slice. Fixing negation robustly would either add broader natural-language parsing logic or change the Phase 1 behavior contract.
- **Risk:** Recommendations can over-weight a constraint the user meant to reject in prompts that use negated phrasing.
- **Phase 2 note:** Phase 2 does not reinterpret negated free-form text. It adds structured workbench controls for explicit constraints, candidate include/exclude decisions, and weight overrides. The raw deterministic parser debt remains open for free-form clarification text, while Phase 2 preference editing avoids the ambiguity through explicit controls.
- **Reopen when:** A task explicitly targets clarification quality, constraint extraction, preference editing, or the first LLM-backed interpretation layer.
- **Suggested future fix:** Add explicit tests for negated constraints first, then either implement a bounded deterministic negation parser or move constraint interpretation behind a later-phase extraction component with traceable outputs.

## Closed Items

No accepted debt items have been closed yet.
