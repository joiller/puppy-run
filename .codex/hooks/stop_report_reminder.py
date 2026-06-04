#!/usr/bin/env python3
"""Remind agents to report verification and skipped checks when worktree has changes."""

from __future__ import annotations

import subprocess


def main() -> int:
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        text=True,
        capture_output=True,
        check=False,
    ).stdout.strip()
    if status:
        print(
            "Hook reminder: this repo has changes. Final response should include files "
            "changed, verification commands and results, scope intentionally not touched, "
            "and residual risk or skipped checks. Run git diff --check before claiming done."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
