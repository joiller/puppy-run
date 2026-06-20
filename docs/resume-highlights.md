# PuppyRun Resume and Interview Highlights

This document collects evidence-backed project highlights that may be useful for resumes, portfolio writeups, and interview discussion.

## How to Add an Entry

Add an entry when a change creates a concrete, explainable engineering highlight. Keep claims grounded in the repository state and current verification.

Each entry should include:

- **Highlight:** the resume or interview point in one sentence.
- **Why it matters:** the engineering judgment, system design, or product value behind it.
- **Evidence:** commit hash, files, tests, demo path, or verification command output.
- **Status:** implemented, verified, demoed, planned, or pending verification.
- **Interview angle:** the short story to explain tradeoffs, constraints, and what you owned.

Do not include real hosts, raw IPs, SSH targets, tokens, credentials, secrets, or private operational details.

## Highlights

### Phase 4 DeepSeek live eval gate

- **Highlight:** Built a manual live regression eval gate that exercises PuppyRun's real DeepSeek provider path and records release-gate evidence as structured JSON plus Markdown.
- **Why it matters:** The gate targets the failure class deterministic tests missed in Phase 3: provider wiring, strict schema compatibility, low-trust risk handling, workflow fallback masking, and report-safe error handling on the live LLM path.
- **Evidence:** `backend/puppyrun_eval/`, `backend/tests/test_phase4_eval.py`, `docs/superpowers/specs/2026-06-19-puppyrun-phase-4-live-eval-design.md`, local backend tests, Docker image import checks, and live report `.eval-reports/phase4-live-20260619-183552.md`.
- **Status:** Implemented and verified locally; live DeepSeek acceptance passed for all four required Phase 4 cases in the current release session.
- **Interview angle:** Explain how the eval runner separates blocked provider setup, provider response failures, quality regressions, and harness bugs while keeping reports useful for release decisions and safe for repository work.

### Phase 5 public live demo safety shell

- **What shipped:** Added Redis-backed public demo quotas, live-run kill switch, token-protected admin controls, frontend quota messaging, and VPS configuration for a no-login live DeepSeek demo.
- **Why it matters:** Turns PuppyRun from a local/live-eval prototype into a safer public demo by bounding cost, limiting abuse, and giving the operator a runtime shutoff without full RBAC.
- **Evidence:** Phase 5 backend and frontend tests, Docker Compose config check, Docker smoke, and local browser/admin acceptance from this Phase 5 release session.
- **Status:** Implemented and verified locally in Phase 5; public VPS release and real DeepSeek acceptance remain separate.
