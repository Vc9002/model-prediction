"""Generate rebuild verification evidence from command-produced artifacts."""

from __future__ import annotations

import argparse
import collections
import json
import platform
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MYPY_LINE = re.compile(r"^(.+?):\d+(?::\d+)?: error: (.+?)\s+\[([^]]+)\]$")


def _revision(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _junit(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    tests = sum(int(suite.attrib.get("tests", 0)) for suite in suites)
    failures = sum(int(suite.attrib.get("failures", 0)) for suite in suites)
    errors = sum(int(suite.attrib.get("errors", 0)) for suite in suites)
    skipped = sum(int(suite.attrib.get("skipped", 0)) for suite in suites)
    return {
        "passed": tests - failures - errors - skipped,
        "failed": failures,
        "errors": errors,
        "skipped": skipped,
        "total": tests,
    }


def _mypy(path: Path) -> collections.Counter[tuple[str, str, str]]:
    result: collections.Counter[tuple[str, str, str]] = collections.Counter()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = MYPY_LINE.match(line)
        if match:
            normalized = match.group(1).replace("\\", "/")
            marker = normalized.rfind("/src/")
            normalized = normalized[marker + 1 :] if marker >= 0 else normalized
            result[(normalized, match.group(3), match.group(2))] += 1
    return result


def _receipts(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        if not separator:
            raise ValueError(f"invalid receipt {value!r}; expected NAME=PATH")
        status = Path(raw_path).read_text(encoding="utf-8").strip()
        if status not in {"passed", "failed", "unavailable"}:
            raise ValueError(f"invalid status {status!r} in {raw_path}")
        result[name] = status
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--full-pytest-junit", type=Path, required=True)
    parser.add_argument("--rebuild-pytest-junit", type=Path, required=True)
    parser.add_argument("--ruff-json", type=Path, required=True)
    parser.add_argument("--mypy-current", type=Path, required=True)
    parser.add_argument("--mypy-baseline", type=Path, required=True)
    parser.add_argument("--receipt", action="append", default=[])
    parser.add_argument("--output", type=Path, default=Path("outputs/rebuild/verification.json"))
    args = parser.parse_args()

    repo = args.repo_root.resolve()
    current_mypy = _mypy(args.mypy_current)
    baseline_mypy = _mypy(args.mypy_baseline)
    ruff = json.loads(args.ruff_json.read_text(encoding="utf-8") or "[]")
    if not isinstance(ruff, list):
        raise TypeError("Ruff JSON must be a list")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "code_revision": _revision(repo),
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "pytest": {
            "full_repository": _junit(args.full_pytest_junit),
            "rebuild": _junit(args.rebuild_pytest_junit),
        },
        "ruff": {"status": "passed" if not ruff else "failed", "findings": len(ruff)},
        "mypy": {
            "status": "passed" if not (current_mypy - baseline_mypy) else "failed",
            "errors": sum(current_mypy.values()),
            "baseline": sum(baseline_mypy.values()),
            "new_errors": sum((current_mypy - baseline_mypy).values()),
        },
        "checks": _receipts(args.receipt),
        "mode": "SHADOW_ONLY",
        "execution_enabled": False,
        "production_promotion": False,
    }
    output = args.output if args.output.is_absolute() else repo / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {output}")
    return 1 if payload["ruff"]["status"] != "passed" or payload["mypy"]["status"] != "passed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
