"""Fail CI when the clean-slate rebuild crosses an incumbent boundary."""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
from pathlib import Path

REBUILD_SOURCE_ROOTS = (
    Path("src/model_prediction/rebuild"),
)
REBUILD_SCRIPT_PATTERNS = (
    "rebuild_*.py",
    "mlb_shadow_*.py",
    "mlb_settle_*.py",
    "train_mlb_*.py",
    "build_mlb_*.py",
    "check_mlb_v2_readiness.py",
    "generate_coverage_report.py",
    "generate_economic_report.py",
    "generate_*rebuild*.py",
)
DENIED_IMPORTS = (
    "model_prediction.cli",
    "model_prediction.data_sources.polymarket_execute",
    "model_prediction.ledger",
    "model_prediction.main_ledgers",
    "model_prediction.model_ledger",
    "model_prediction.research_ledgers",
    "model_prediction.xlsx_ledger",
)
MLB_V3_DENIED_IMPORTS = (
    "model_prediction.rebuild.mlb_shadow_pipeline",
    "model_prediction.rebuild.shadow_ledger",
    "model_prediction.rebuild.dashboard_status",
)
MLB_V3_SEALED_PATH_MARKERS = (
    "data/rebuild/shadow.db",
    "data/rebuild/metadata.db",
    "outputs/rebuild/test_consumption_registry.json",
    "outputs/rebuild/verification.json",
    "outputs/rebuild/economic_report",
    "outputs/rebuild/runtime/",
)
PROTECTED_DATA_PREFIXES = (
    "data/main/",
    "data/flat/",
    "data/gated_research/",
    "data/research/",
    "data/availability/",
    "data/odds/",
    "data/logs/",
    "data/esports/",
    "data/international_baseball/",
    "data/point_in_time/",
)
PATH_LITERAL_ALLOWLIST = {
    # The safety module must name the roots it rejects. It exposes no I/O and
    # remains subject to the prohibited-import and real-order-call checks.
    "src/model_prediction/rebuild/safety.py": (
        "data/main/",
        "data/flat/",
        "data/gated_research/",
        "data/research/",
    ),
    # Coverage evidence reads an incumbent PIT snapshot without writing it.
    "scripts/generate_coverage_report.py": ("data/point_in_time/",),
}
DENIED_ORDER_CALLS = {
    "create_order",
    "execute_order",
    "place_order",
    "submit_order",
}
EXECUTION_IMPORT_PARTS = ("execute", "execution", "order")
TRACKED_REBUILD_RUNTIME_PATTERNS = (
    re.compile(r"^data/rebuild/(?:raw|normalized|features|markets|resume_state|logs)/"),
    re.compile(r"^data/rebuild/.*\.(?:db|sqlite)(?:-.+)?$"),
    re.compile(r"^outputs/rebuild/runtime/"),
    re.compile(r"^outputs/rebuild/(?:verification\.json|main_integration_verification\.md)$"),
)
CHANGED_FORBIDDEN_PATTERNS = (
    re.compile(r"^data/(?:main|flat|gated_research|research|availability|odds|logs|esports|international_baseball)/"),
    re.compile(r"^data/rebuild/(?:raw|normalized|features|markets|resume_state|logs)/"),
    re.compile(r"^data/rebuild/.*\.(?:db|sqlite)(?:-.+)?$"),
    re.compile(r"^outputs/rebuild/runtime/"),
    re.compile(r"^dashboard/(?:server\.log|jobs\.json)$"),
)


def _git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=check, capture_output=True, text=True
    )
    return result.stdout


def _python_files(repo: Path) -> list[Path]:
    files: list[Path] = []
    for relative in REBUILD_SOURCE_ROOTS:
        path = repo / relative
        if path.is_dir():
            files.extend(sorted(path.rglob("*.py")))
        elif path.is_file():
            files.append(path)
    for pattern in REBUILD_SCRIPT_PATTERNS:
        files.extend(sorted((repo / "scripts").glob(pattern)))
    this_script = Path(__file__).resolve()
    return sorted({path.resolve() for path in files if path.resolve() != this_script})


def _docstring_nodes(tree: ast.AST) -> set[int]:
    nodes: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not body or not isinstance(body, list):
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            nodes.add(id(first.value))
    return nodes


def _scan_python(repo: Path) -> list[str]:
    errors: list[str] = []
    for path in _python_files(repo):
        relative = path.relative_to(repo).as_posix()
        is_mlb_v3 = relative.startswith("src/model_prediction/rebuild/mlb_v3/")
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        except (OSError, SyntaxError) as exc:
            errors.append(f"{relative}: cannot inspect Python source: {exc}")
            continue

        docstrings = _docstring_nodes(tree)
        allowed_prefixes = PATH_LITERAL_ALLOWLIST.get(relative, ())
        for node in ast.walk(tree):
            imported: str | None = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    parts = set(alias.name.lower().replace("-", "_").split("."))
                    if (
                        alias.name in DENIED_IMPORTS
                        or alias.name.startswith(tuple(f"{name}." for name in DENIED_IMPORTS))
                        or (
                            alias.name.startswith("model_prediction.")
                            and parts.intersection(EXECUTION_IMPORT_PARTS)
                            and not alias.name.startswith("model_prediction.rebuild")
                        )
                    ):
                        errors.append(
                            f"{relative}:{node.lineno}: prohibited incumbent import {alias.name}"
                        )
                    if is_mlb_v3 and (
                        alias.name in MLB_V3_DENIED_IMPORTS
                        or alias.name.startswith(tuple(f"{name}." for name in MLB_V3_DENIED_IMPORTS))
                    ):
                        errors.append(
                            f"{relative}:{node.lineno}: prohibited MLB v3 sealed-evidence import {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom):
                imported = node.module
                parts = set((imported or "").lower().replace("-", "_").split("."))
                if imported and (
                    imported in DENIED_IMPORTS
                    or imported.startswith(tuple(f"{name}." for name in DENIED_IMPORTS))
                    or (
                        imported.startswith("model_prediction.")
                        and parts.intersection(EXECUTION_IMPORT_PARTS)
                        and not imported.startswith("model_prediction.rebuild")
                    )
                ):
                    errors.append(
                        f"{relative}:{node.lineno}: prohibited incumbent import {imported}"
                    )
                if is_mlb_v3 and imported and (
                    imported in MLB_V3_DENIED_IMPORTS
                    or imported.startswith(tuple(f"{name}." for name in MLB_V3_DENIED_IMPORTS))
                ):
                    errors.append(
                        f"{relative}:{node.lineno}: prohibited MLB v3 sealed-evidence import {imported}"
                    )
            elif isinstance(node, ast.Call):
                function = node.func
                name = function.attr if isinstance(function, ast.Attribute) else (
                    function.id if isinstance(function, ast.Name) else None
                )
                if name in DENIED_ORDER_CALLS:
                    errors.append(
                        f"{relative}:{node.lineno}: prohibited real-order call {name}()"
                    )
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
            ):
                normalized = node.value.replace("\\", "/").lstrip("./")
                if is_mlb_v3 and any(marker in normalized for marker in MLB_V3_SEALED_PATH_MARKERS):
                    errors.append(
                        f"{relative}:{getattr(node, 'lineno', '?')}: prohibited MLB v3 sealed-evidence path {node.value!r}"
                    )
                for prefix in PROTECTED_DATA_PREFIXES:
                    if prefix in allowed_prefixes:
                        continue
                    if normalized == prefix.rstrip("/") or normalized.startswith(prefix):
                        errors.append(
                            f"{relative}:{getattr(node, 'lineno', '?')}: prohibited incumbent path {node.value!r}"
                        )
    return errors


def _scan_tracked_files(repo: Path) -> list[str]:
    errors: list[str] = []
    for tracked in _git(repo, "ls-files").splitlines():
        if any(pattern.search(tracked) for pattern in TRACKED_REBUILD_RUNTIME_PATTERNS):
            errors.append(f"tracked rebuild runtime file: {tracked}")
    return errors


def _scan_diff(repo: Path, base_ref: str | None) -> list[str]:
    if not base_ref:
        return []
    result = subprocess.run(
        ["git", "rev-parse", "--verify", base_ref],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return [f"cannot verify isolation base ref {base_ref!r}"]
    errors: list[str] = []
    changed = _git(repo, "diff", "--name-only", f"{base_ref}...HEAD").splitlines()
    for path in changed:
        if path.startswith("config/models/") and not path.startswith(
            "config/models/challengers/"
        ):
            errors.append(f"incumbent model changed: {path}")
        if any(pattern.search(path) for pattern in CHANGED_FORBIDDEN_PATTERNS):
            errors.append(f"forbidden changed path: {path}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--base-ref", default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    repo = args.repo_root.resolve()
    errors = sorted(set(_scan_python(repo) + _scan_tracked_files(repo) + _scan_diff(repo, args.base_ref)))
    payload = {"status": "pass" if not errors else "fail", "errors": errors}
    if args.as_json:
        print(json.dumps(payload, indent=2))
    elif errors:
        print("Rebuild isolation check failed:")
        for error in errors:
            print(f"- {error}")
    else:
        print("Rebuild isolation check passed.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
