---
name: puppyrun-agent-workflow
description: Use when working in PuppyRun on scoped task handoffs, implementation controller prompts, read-only code reviews, narrow unblockers, accepted debt handling, Phase 1 verification/documentation closure, or questions about the next task. Trigger for requests such as "handoff the next task", "review this PuppyRun change", "fix this unblocker only", "draft a controller prompt", "Ready to proceed?", or "what should the next PuppyRun thread do?"
---

# PuppyRun Agent Workflow

## Overview

Use this skill to keep PuppyRun work scoped, evidence-backed, and reviewable. It routes each request into the smallest workflow that fits: handoff prompt, read-only review, narrow unblocker, implementation, or next-task lookup.

## Required Reads

Before acting, read only the relevant files:

- `AGENTS.md` for repository operating rules.
- `docs/accepted-debt.md` before changing clarification, extraction, recommendation, or workflow behavior.
- The relevant plan/spec section under `docs/superpowers/` when the request references a task, phase, or next step.

Always verify current state before answering "what next", editing files, reviewing a diff, or writing a handoff:

```bash
git status --short --branch
git rev-parse HEAD
git log --oneline --decorate -n 8
```

## Workflow Router

### Handoff Prompt

Use when the user asks for another Codex thread's prompt.

Produce a ready-to-paste controller prompt with:

- Repo path.
- Branch.
- Starting commit.
- Exact task.
- Allowed files.
- Out-of-scope files and next tasks.
- Required verification commands.
- Stop condition.
- Expected final report format.

Keep the prompt aimed at the main/controller agent. If subagents are expected, tell the controller to dispatch one implementer, one spec-compliance reviewer, and one code-quality reviewer sequentially.

### Read-Only Review

Use when the user asks to review a commit, diff range, or uncommitted change.

Stay read-only unless the user explicitly asks for fixes. Report:

1. Findings first, ordered by severity, with file:line references.
2. Verification commands and results.
3. Open questions or assumptions.
4. Explicit gate when requested, such as `Ready to proceed to Task 8? Yes / No / With fixes.`

Review for behavioral regressions, edge cases, async/stale-response races, persistence and rollback issues, Docker/runtime/package-discovery mismatch, missing tests, and scope creep.

### Narrow Unblocker

Use when the user names one failing command, one allowed file, or says this is only an unblocker.

Change only the allowed file(s). Do not fold in docs, next-task work, or accepted debt fixes. Verify the original failure and run one targeted regression check plus `git diff --check`.

### Implementation

Use when the user asks to implement a scoped task.

Preserve the file and task boundary. Start with the smallest failing test unless the task is documentation-only. Run the narrowest verification that covers the risk, then broaden only when the change crosses API, worker, frontend, Docker, package-discovery, or deployment boundaries.

### Next-Task Lookup

Use when the user asks what to do next.

Do not answer from memory alone. Confirm branch, `HEAD`, and the relevant plan section before naming the next task.

## Hard Rules

- Do not fix accepted debt unless the user explicitly reopens it.
- Do not introduce live LLM calls or later-phase PuppyRun behavior during Phase 1 work unless explicitly requested.
- Do not commit real public hosts, raw IPs, SSH targets, tokens, credentials, or secrets.
- Do not claim tests passed unless they ran in the current turn.
- If verification is skipped, say exactly what was skipped and why.
