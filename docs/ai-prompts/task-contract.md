# Task Contract Snippet

Paste this at the top of a Codex thread when you want the agent to stay tightly scoped.

```text
Goal:
<one concrete outcome>

Repo:
/Users/jianghuilai/.codex/worktrees/2079/puppy-run

Branch:
<branch>

Starting commit:
<commit>

Allowed files:
- <file or directory>

Do not modify:
- <file or directory>
- Do not implement <next task>.
- Do not fix accepted debt unless explicitly reopened.

Verification:
- <command 1>
- <command 2>
- `git diff --check`

Stop condition:
Stop after <definition of done>. Report results and do not continue into adjacent work.
```
