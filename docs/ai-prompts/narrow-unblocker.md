# Narrow Unblocker Prompt

Use this prompt when a thread should fix exactly one blocking failure, such as a Docker import failure, a packaging issue, or one failing verification command.

```text
You are fixing one narrow PuppyRun unblocker only.

Repo: /Users/jianghuilai/.codex/worktrees/2079/puppy-run
Branch: <branch>
Starting commit: <commit>

Read first:
- AGENTS.md
- docs/accepted-debt.md

Problem:
<exact failing command, symptom, or error>

Allowed file(s):
- <one file or minimal file list>

Do not modify:
- Any unrelated implementation files.
- Documentation, unless explicitly listed above.
- The next planned task.
- Accepted debt, unless explicitly reopened in this prompt.

Required start checks:
- `git status --short --branch`
- `git rev-parse HEAD`
- Inspect the failing command or failure evidence.

Expected investigation:
1. Reproduce or inspect the failure.
2. Identify the smallest root cause.
3. Patch only the allowed file(s).
4. Re-run the failing command.
5. Run one targeted regression check.
6. Run `git diff --check`.

Final report format:
1. Commit hash or working tree state.
2. Files changed.
3. Exact verification command results.
4. Whether the original blocker is cleared.
5. Residual risk or skipped checks.
```
