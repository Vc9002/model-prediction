"""Fail CI when tracked operational source/config references a retired local path.

Companion to `check_rebuild_isolation.py`'s incumbent-boundary check --
this one guards against a different real risk: a hardcoded reference to the
legacy `/Users/vincentc9002/model prediction` checkout surviving inside code
or config after the runtime/dashboard cutover, silently reintroducing the
exact filesystem coupling `runtime_paths.py` exists to remove.

Allowed exceptions:
- Markdown/docs files (migration history is meant to mention the old path).
- This script itself, and any test file whose name says it's expressly
  testing obsolete-path detection.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

DEFAULT_OBSOLETE_PATH = "/Users/vincentc9002/model prediction"
ALLOWED_SUFFIXES = (".md", ".txt")
ALLOWED_NAME_FRAGMENTS = ("check_obsolete_paths", "test_obsolete_path")


def _git_tracked_files(repo: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-files"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _is_allowed(relative_path: str) -> bool:
    path = Path(relative_path)
    if path.suffix in ALLOWED_SUFFIXES:
        return True
    return any(fragment in path.name for fragment in ALLOWED_NAME_FRAGMENTS)


def find_obsolete_path_references(repo: Path, obsolete_path: str) -> list[str]:
    errors: list[str] = []
    needle = obsolete_path.encode("utf-8")
    for relative in _git_tracked_files(repo):
        if _is_allowed(relative):
            continue
        full = repo / relative
        if not full.is_file():
            continue
        try:
            body = full.read_bytes()
        except OSError:
            continue
        if needle in body:
            for lineno, line in enumerate(body.decode("utf-8", errors="replace").splitlines(), start=1):
                if obsolete_path in line:
                    errors.append(f"{relative}:{lineno}: references obsolete path {obsolete_path!r}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--obsolete-path", default=DEFAULT_OBSOLETE_PATH)
    args = parser.parse_args()

    repo = args.repo_root.resolve()
    errors = find_obsolete_path_references(repo, args.obsolete_path)
    if errors:
        print("Obsolete-path check failed:")
        for error in errors:
            print(f"- {error}")
    else:
        print("Obsolete-path check passed.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
