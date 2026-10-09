#!/usr/bin/env python3
"""Check publishable tracked and untracked files for common credential shapes."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


PATTERNS = {
    "model-api-key": re.compile(rb"sk-[A-Za-z0-9_-]{28,}"),
    "google-api-key": re.compile(rb"AIza[0-9A-Za-z_-]{35}", re.IGNORECASE),
    "github-token": re.compile(rb"gh[pousr]_[A-Za-z0-9_]{20,}"),
    "aws-access-key": re.compile(rb"AKIA[0-9A-Z]{16}"),
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def source_files(root: Path) -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    return [root / item.decode("utf-8") for item in completed.stdout.split(b"\0") if item]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    findings: list[tuple[str, str]] = []
    for path in source_files(root):
        try:
            if path.stat().st_size > 2_000_000:
                continue
            content = path.read_bytes()
        except OSError:
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(content):
                findings.append((str(path.relative_to(root)), label))
    if findings:
        for path, label in findings:
            print(f"credential pattern detected: {label} in {path}")
        return 1
    print("security preflight passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
