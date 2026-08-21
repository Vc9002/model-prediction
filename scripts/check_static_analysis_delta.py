"""Require current static-analysis findings to be a subset of a baseline."""

from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

MYPY_LINE = re.compile(r"^(?P<path>.+?):\d+(?::\d+)?: error: (?P<message>.+?)\s+\[(?P<code>[^]]+)\]$")


def _relative(path: str, marker: str) -> str:
    normalized = path.replace("\\", "/")
    index = normalized.rfind(marker)
    return normalized[index + 1 :] if index >= 0 else normalized


def _ruff(path: Path) -> collections.Counter[tuple[str, str, str]]:
    findings = json.loads(path.read_text(encoding="utf-8") or "[]")
    return collections.Counter(
        (
            _relative(str(item["filename"]), "/src/")
            if "/src/" in str(item["filename"]).replace("\\", "/")
            else _relative(str(item["filename"]), "/tests/"),
            str(item["code"]),
            str(item["message"]),
        )
        for item in findings
    )


def _mypy(path: Path) -> collections.Counter[tuple[str, str, str]]:
    findings: collections.Counter[tuple[str, str, str]] = collections.Counter()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = MYPY_LINE.match(line)
        if not match:
            continue
        findings[
            (
                _relative(match.group("path"), "/src/"),
                match.group("code"),
                match.group("message"),
            )
        ] += 1
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", choices=("ruff", "mypy"), required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    args = parser.parse_args()

    loader = _ruff if args.tool == "ruff" else _mypy
    baseline = loader(args.baseline)
    current = loader(args.current)
    additions = current - baseline
    if not additions:
        print(
            f"{args.tool}: no new findings "
            f"(baseline={sum(baseline.values())}, current={sum(current.values())})"
        )
        return 0

    print(f"{args.tool}: {sum(additions.values())} new finding(s):")
    for (path, code, message), count in sorted(additions.items()):
        suffix = f" x{count}" if count > 1 else ""
        print(f"- {path}: [{code}] {message}{suffix}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
