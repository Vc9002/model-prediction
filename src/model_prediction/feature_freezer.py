"""Frozen point-in-time feature tables for model research (item 8).

Produce a frozen PIT feature table ONCE — every candidate feature plus
availability flags, computed by the SAME walk-forward builder the
validation harness uses — so ablations consume identical rows instead of
each reconstructing history independently:

    python -m model_prediction.feature_freezer freeze \
        --sport mlb --out data/features/pit_mlb.jsonl [--end-date YYYY-MM-DD]

Output: one JSONL row per game (features + availability flags + outcome,
exactly a serialized ValidationRow) plus a sidecar manifest
(``<out>.manifest.json``) carrying the dataset hash, feature-schema hash,
git sha, generation time, and row count — everything the experiment
registry needs to cite a dataset unambiguously.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT
from .features.base import FeatureStore
from .validation import build_walk_forward_rows


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def freeze_features(
    *,
    sport: str,
    out_path: Path,
    end_date: str | None = None,
    data_root: Path | None = None,
) -> dict[str, Any]:
    """Build the walk-forward rows once and freeze them to disk."""
    root = data_root or (PROJECT_ROOT / "data")
    store = FeatureStore(root)
    rows = build_walk_forward_rows(store, sport, end_date=end_date)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for row in rows:
        lines.append(
            json.dumps(dataclasses.asdict(row), sort_keys=True, separators=(",", ":"))
        )
    table = "\n".join(lines) + ("\n" if lines else "")
    out_path.write_text(table, encoding="utf-8")

    feature_names = sorted(field.name for field in dataclasses.fields(rows[0])) if rows else []
    manifest = {
        "sport": sport,
        "end_date": end_date,
        "rows": len(rows),
        "dataset_hash": _sha256_bytes(table.encode()),
        "feature_schema_hash": _sha256_bytes(
            json.dumps(feature_names, sort_keys=True, separators=(",", ":")).encode()
        ),
        "feature_names": feature_names,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "git_sha": _git_sha(),
        "builder": "validation.build_walk_forward_rows",
    }
    manifest_path = out_path.with_suffix(out_path.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=PROJECT_ROOT,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) < 1 or args[0] != "freeze":
        print(
            "usage: python -m model_prediction.feature_freezer freeze "
            "--sport S --out PATH [--end-date YYYY-MM-DD]",
            file=sys.stderr,
        )
        return 2

    def _arg(name: str, default: str | None = None) -> str | None:
        if name in args:
            idx = args.index(name)
            if idx == len(args) - 1:
                raise ValueError(f"{name} requires a value")
            return args[idx + 1]
        return default

    try:
        sport = _arg("--sport")
        out = _arg("--out")
        if not sport or not out:
            raise ValueError("--sport and --out are required")
        manifest = freeze_features(
            sport=sport, out_path=Path(out), end_date=_arg("--end-date")
        )
        print(json.dumps(manifest, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"FREEZE ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
