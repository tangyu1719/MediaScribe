#!/usr/bin/env python3
"""Scan the tracked working tree without printing secret values."""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKIP_PREFIXES = ("frontend/vendor/",)
SKIP_NAMES = {"package-lock.json"}

RULES = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "provider-token": re.compile(
        r"(?:(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{20,}|AKLT[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{36}|"
        r"github_pat_[A-Za-z0-9_]{40,}|AIza[0-9A-Za-z_-]{35}|xox[baprs]-[0-9A-Za-z-]{10,})"
    ),
    "xhs-query-token": re.compile(
        r"xsec_token=(?!\$\{|REDACTED(?:_TEST_TOKEN)?(?:&|$)|example(?:&|$))[A-Za-z0-9_-]{16,}={0,2}",
        re.IGNORECASE,
    ),
}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
ALLOWED_EMAIL_DOMAINS = {
    "example.com",
    "example.net",
    "example.org",
    "test.invalid",
    "users.noreply.github.com",
}


def tracked_files() -> list[pathlib.Path]:
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    return [ROOT / item.decode("utf-8") for item in raw.split(b"\0") if item]


def main() -> int:
    findings: list[tuple[str, int, str]] = []
    for path in tracked_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel in SKIP_NAMES or rel.startswith(SKIP_PREFIXES):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            for rule, pattern in RULES.items():
                if pattern.search(line):
                    findings.append((rel, line_no, rule))
            for match in EMAIL.finditer(line):
                domain = match.group(1).lower()
                if domain not in ALLOWED_EMAIL_DOMAINS and not domain.endswith(".example.com"):
                    findings.append((rel, line_no, "personal-email"))
    if findings:
        for rel, line_no, rule in findings:
            print(f"{rel}:{line_no}: {rule} (value redacted)")
        print(f"secret scan failed: {len(findings)} finding(s)", file=sys.stderr)
        return 1
    print("secret scan passed: tracked working tree has no high-confidence findings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
