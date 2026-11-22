"""One-time, idempotent append of the FBS-involved ESPN backfill to the live NCAAF history.

Operator waived the pinned ``cfb-v5-history-impact-rule-1`` REJECT on 2026-11-15 (holdout Brier delta
+0.001065 against a +0.001 limit, 95% CI [-0.000815, +0.002998] spans zero). The waiver is recorded
here and in the SUMMARY; this script does not pretend the rule passed.

Writes ONLY data/processed/ncaaf/games.jsonl (append, binary). data/historical/* is protected and is
never opened for writing; ``_load_cfb_history`` reads both files and dedups by event id, so the
processed file alone is enough. Refuses to run while a daily row is 'started' in runs.db.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "src"))

import cfb_v5_history_impact as imp

RUNS_DB = Path("E:/model-prediction-runtime/runs.db")


def _daily_started() -> int:
    con = sqlite3.connect(f"file:{RUNS_DB.as_posix()}?mode=ro", uri=True)
    try:
        return con.execute("select count(*) from runs where worker='daily' and status='started'").fetchone()[0]
    finally:
        con.close()


def _ids(path: Path) -> list[str]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(str(json.loads(line)["event_id"]))
    return out


def main() -> int:
    processed, historical = imp.LIVE_PROCESSED, imp.LIVE_HISTORICAL
    rows = imp.build_append_set(imp.BACKFILL_PATH, [processed, historical])
    if not rows:
        print("already applied: no backfill event is missing from the live files")
        return 0
    if _daily_started() != 0:
        print("refusing: a daily run is 'started'")
        return 2
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = REPO / "data" / "research" / "ncaaf_v6" / f"live_backup_{stamp}"
    backup.mkdir(parents=True)
    manifest = {}
    for p in (processed, historical):
        shutil.copy2(p, backup / p.name)
        manifest[str(p.relative_to(REPO))] = {"sha256": imp.file_sha256(p), "bytes": p.stat().st_size}
    (backup / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    pre_size = processed.stat().st_size
    pre_ids = _ids(processed)
    hist_sha = imp.file_sha256(historical)
    payload = imp.serialize(rows).encode("utf-8")
    if _daily_started() != 0:
        print("refusing: a daily run started during preparation")
        return 2
    with processed.open("ab") as f:
        f.write(payload)
    # verification
    with processed.open("rb") as f:
        prefix = hashlib.sha256(f.read(pre_size)).hexdigest()
    assert prefix == manifest[str(processed.relative_to(REPO))]["sha256"], "prefix changed"
    assert imp.file_sha256(historical) == hist_sha, "historical file changed"
    post_ids = _ids(processed)
    new_ids = [str(r["event_id"]) for r in rows]
    assert len(post_ids) == len(pre_ids) + len(new_ids)
    assert all(post_ids.count(i) == 1 for i in new_ids), "appended id not unique"
    for line in payload.decode("utf-8").splitlines():
        assert set(json.loads(line)) == set(imp.LIVE_ROW_KEYS)
    from model_prediction.models.college_football import _load_cfb_history

    hist = _load_cfb_history(REPO / "data")
    eids = [g.event_id for g in hist] if hist and hasattr(hist[0], "event_id") else None
    if eids is not None:
        assert len(eids) == len(set(eids)), "duplicate event ids in loaded history"
    print(
        json.dumps(
            {
                "appended": len(rows),
                "append_set_sha256": imp.append_set_sha256(rows),
                "processed_before_sha256": manifest[str(processed.relative_to(REPO))]["sha256"],
                "processed_after_sha256": imp.file_sha256(processed),
                "historical_sha256_unchanged": hist_sha,
                "loaded_history_rows": len(hist),
                "backup": str(backup),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
