"""One-time generator for the frozen v9 research cohort (event-ID sets).

``mlb_research_common.pinned_cohort()`` used to call
``_identify_backfill_event_ids()`` (a forensic reconstruction that scans the
ingest-ordered games file for a chronological-descent point) on every run.
That makes "pinned" cohort a misnomer -- the underlying historical file can
grow or be reordered between runs, silently changing which rows compose
train/validation/exact_holdout.

This script runs that reconstruction exactly once and freezes its output as
explicit event-ID lists plus a SHA-256 manifest under
``data/point_in_time/mlb_v9_cohort_v1/``. After this file exists,
``pinned_cohort()`` loads and filters by these frozen IDs instead of
re-deriving them -- see that function's docstring.

Usage (only re-run deliberately, e.g. to cut a new cohort version):
    PYTHONPATH=src:. .venv/bin/python scripts/freeze_mlb_v9_cohort.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mlb_research_common import (
    _identify_backfill_event_ids,
    pinned_cohort,
)

from model_prediction.config import PROJECT_ROOT

COHORT_DIR = PROJECT_ROOT / "data" / "point_in_time" / "mlb_v9_cohort_v1"


def _sha256_of_ids(event_ids: list[str]) -> str:
    canonical = json.dumps(sorted(event_ids), separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def main() -> int:
    cohort = pinned_cohort()

    splits = {
        "train": sorted(r.event_id for r in cohort["train"]),
        "validation": sorted(r.event_id for r in cohort["validation"]),
        "exact_holdout": sorted(r.event_id for r in cohort["exact_holdout"]),
    }

    COHORT_DIR.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name, event_ids in splits.items():
        path = COHORT_DIR / f"{name}_event_ids.json"
        path.write_text(json.dumps(event_ids, indent=2) + "\n", encoding="utf-8")
        hashes[name] = {"sha256": _sha256_of_ids(event_ids), "count": len(event_ids)}
        print(f"{name}: {len(event_ids)} event ids -> {path}")

    manifest = {
        "schema": "mlb-v9-cohort-manifest-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "backfill_ids_excluded": sorted(_identify_backfill_event_ids()),
        "splits": hashes,
        "source": "mlb_research_common.pinned_cohort() (one-time freeze)",
    }
    manifest_path = COHORT_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"manifest -> {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
