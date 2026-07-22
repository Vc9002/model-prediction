"""Stamp ``migrated_from_version`` on ledger rows whose model_version was
rewritten in place by scripts/migrate_mlb_v3_to_v4.py.

Detection: a row's pick_created/research_observation_created audit event
recorded the version at decision time; when it differs from the row's current
model_version, the original is stamped into ``migrated_from_version`` so
per-version reports can distinguish native v4 decisions from relabeled v3 ones.

Run from the project root after merging:
  PYTHONPATH=src:. .venv/bin/python scripts/stamp_migrated_provenance.py
"""

from __future__ import annotations

import json
from pathlib import Path

from model_prediction.ledger import FIELDNAMES
from model_prediction.xlsx_ledger import read_xlsx_rows, write_xlsx_rows_atomic


def stamp(ledger_path: Path, events_path: Path) -> dict:
    decision_version: dict[str, str] = {}
    if events_path.exists():
        with events_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("event_type") in (
                    "pick_created",
                    "research_observation_created",
                ):
                    version = (event.get("payload") or {}).get("model_version")
                    if version:
                        decision_version[str(event.get("subject_id"))] = str(version)
    headers, rows = read_xlsx_rows(ledger_path)
    if not set(headers).issubset(set(FIELDNAMES)):
        raise SystemExit(f"{ledger_path}: unexpected columns; aborting")
    normalized = [{field: row.get(field, "") or "" for field in FIELDNAMES} for row in rows]
    stamped = 0
    for row in normalized:
        original = decision_version.get(row["pick_id"])
        if original and original != row["model_version"] and not row["migrated_from_version"]:
            row["migrated_from_version"] = original
            stamped += 1
    if stamped:
        write_xlsx_rows_atomic(ledger_path, FIELDNAMES, normalized)
    return {"ledger": str(ledger_path), "rows": len(normalized), "stamped": stamped}


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    events = root / "data" / "events.jsonl"
    results = [
        stamp(root / "data" / "picks.xlsx", events),
        stamp(root / "data" / "flat_picks.xlsx", events),
    ]
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
