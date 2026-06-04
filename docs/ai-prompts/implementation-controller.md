# Implementation Controller Prompt

Use this prompt when starting a new Codex thread that should implement one scoped PuppyRun task.

```text
You are the controller agent for a scoped PuppyRun implementation task.

Repo: /Users/jianghuilai/.codex/worktrees/2079/puppy-run
Branch: <branch>
Starting commit: <commit>

Read first:
- AGENTS.md
- docs/accepted-debt.md
- <relevant plan/spec section>

Task:
<one concrete task only>

Allowed files:
- <file or directory>

Do not modify:
- <file or directory>
- Do not implement <next task or adjacent scope>.
- Do not fix accepted debt unless explicitly reopened in this prompt.
- Do not write real public hosts, raw IPs, SSH targets, tokens, or secrets into repo docs.

Required start checks:
- `git status --short --branch`
- `git rev-parse HEAD`
- `git log --oneline --decorate -n 8`
- Re-read the relevant plan/spec section before editing.

Workflow:
1. Confirm the current branch and HEAD match the task baseline.
2. Restate the implementation boundary in one short update.
3. Start with the smallest failing test that proves the requested behavior, unless this is documentation-only.
4. Implement the minimal production change needed for the task.
5. Run the required verification commands.
6. Inspect the diff for scope creep.

If subagents are available:
1. Dispatch one implementer subagent for the scoped implementation.
2. Dispatch one spec-compliance reviewer subagent.
3. Dispatch one code-quality reviewer subagent.
4. Fix only approved issues inside the declared scope.
5. Re-review until accepted or genuinely blocked.

Required verification:
- <command 1>
- <command 2>
- `git diff --check`

Final report format:
1. Commit hash or working tree state.
2. Files changed.
3. Exact verification commands and results.
4. Scope intentionally not touched.
5. Residual risk or skipped checks.
```
