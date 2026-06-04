#!/usr/bin/env python3
"""Warn when the current diff appears to include private deployment details."""

from __future__ import annotations

import ipaddress
import re
import subprocess
from pathlib import Path


SECRET_ASSIGNMENT = re.compile(
    r"\b(?:OPENAI_API_KEY|GITHUB_TOKEN|PUPPYRUN_GITHUB_TOKEN|POSTGRES_PASSWORD|"
    r"[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PRIVATE_KEY)[A-Z0-9_]*)\s*=\s*['\"]?"
    r"(?!<|your-|example|change-me|changeme|placeholder|set-me|$)[^'\"\s#]+",
    re.IGNORECASE,
)
PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def run(args: list[str], cwd: Path | None = None) -> str:
    return subprocess.run(
        args,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    ).stdout


def repo_root() -> Path | None:
    out = run(["git", "rev-parse", "--show-toplevel"]).strip()
    return Path(out) if out else None


def changed_files(root: Path) -> list[Path]:
    names = set()
    for cmd in (
        ["git", "diff", "--name-only"],
        ["git", "diff", "--cached", "--name-only"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        for line in run(cmd, cwd=root).splitlines():
            if line:
                names.add(line)
    return [root / name for name in sorted(names)]


def is_public_ip(text: str) -> bool:
    for match in IPV4.findall(text):
        try:
            ip = ipaddress.ip_address(match)
        except ValueError:
            continue
        if not (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
            or ip.is_reserved
        ):
            return True
    return False


def suspicious(path: Path) -> list[str]:
    if ".codex/hooks/" in path.as_posix():
        return []
    if not path.is_file() or path.stat().st_size > 1_000_000:
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []

    reasons = []
    if SECRET_ASSIGNMENT.search(text) or PRIVATE_KEY.search(text):
        reasons.append("possible secret assignment or private key")
    if is_public_ip(text):
        reasons.append("public raw IPv4 address")
    return reasons


def main() -> int:
    root = repo_root()
    if root is None:
        return 0

    hits = []
    for path in changed_files(root):
        reasons = suspicious(path)
        if reasons:
            rel = path.relative_to(root)
            hits.append(f"- {rel}: {', '.join(reasons)}")

    if hits:
        print(
            "Hook warning: changed files may contain private deployment details. "
            "Review before finalizing:\n" + "\n".join(hits)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
