"""Audit every settled college call against a research challenger decision.

The challenger uses saved paired probabilities where available. Unreplayable
rows retain the incumbent probability and are counted explicitly. It only
keeps a recorded contract when its challenger probability exceeds the
recorded ask; it never invents an opposite-side price or changes a stake.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from replay_college_challengers import _ncaaf_probability

from model_prediction.runtime_paths import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]
PAIRED = ROOT / "outputs/research/college_challengers_2026-11-04"
OUTPUT = ROOT / "outputs/research/college_full_slate_2026-11-04.json"


def _key(row: dict | sqlite3.Row) -> tuple:
    return (
        str(row["event_id"]),
        str(row["market"] if isinstance(row, dict) else row["market_type"]),
        str(row["selection"]),
        None if row["line"] is None else float(row["line"]),
    )


def compare() -> dict:
    database = RuntimePaths.resolve(repo_root=ROOT).ledgers_db
    conn = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    sibling_snapshots = {}
    for row in conn.execute("SELECT * FROM ledger_records WHERE sport='ncaaf' AND status='settled'"):
        payload = json.loads(row["feature_payload_json"] or "{}")
        snapshot = payload.get("model_input_snapshot")
        if isinstance(snapshot, dict) and isinstance(snapshot.get("inference_inputs"), dict):
            sibling_snapshots[_key(row)] = snapshot
    result = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "database": str(database),
        "decision_rule": "retain recorded contract and stake iff challenger probability > recorded ask",
        "limitations": [
            "Retrospective decision-layer diagnostic; the challenger was not selected prospectively.",
            "Rows without reconstructable challenger inputs retain incumbent probability and are counted.",
            "Only incumbent-selected contracts have recorded prices; opposite sides and omitted games cannot be priced.",
            "Flat and Main overlap and must never be summed as independent wagers.",
        ],
        "tiers": {},
    }
    for sport in ("ncaab", "ncaaf"):
        paired = {}
        for line in (PAIRED / f"{sport}_paired_rows.jsonl").read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            key = _key(row)
            if key in paired:
                raise ValueError(f"ambiguous paired challenger: {sport} {key}")
            paired[key] = row
        for tier in ("flat", "main"):
            rows = conn.execute(
                "SELECT * FROM ledger_records WHERE sport=? AND ledger_tier=? AND status='settled'",
                (sport, tier),
            ).fetchall()
            markets = defaultdict(
                lambda: {
                    "settled": 0,
                    "replayed": 0,
                    "fallback": 0,
                    "incumbent_wins": 0,
                    "incumbent_losses": 0,
                    "incumbent_pushes": 0,
                    "incumbent_pnl_units": 0.0,
                    "challenger_calls": 0,
                    "challenger_wins": 0,
                    "challenger_losses": 0,
                    "challenger_pushes": 0,
                    "challenger_pnl_units": 0.0,
                    "replayed_scored": 0,
                    "incumbent_brier_sum": 0.0,
                    "challenger_brier_sum": 0.0,
                }
            )
            for row in rows:
                metric = markets[row["market_type"]]
                metric["settled"] += 1
                source = paired.get(_key(row))
                snapshot = sibling_snapshots.get(_key(row)) if sport == "ncaaf" else None
                if sport == "ncaaf" and snapshot is not None:
                    metric["replayed"] += 1
                    probability = _ncaaf_probability(row, snapshot)
                elif source is None:
                    metric["fallback"] += 1
                    probability = float(row["model_probability"])
                else:
                    metric["replayed"] += 1
                    probability = float(source["new_probability"])
                pnl = float(row["pnl_units"] or 0.0)
                metric["incumbent_pnl_units"] += pnl
                suffix = {"win": "wins", "loss": "losses", "push": "pushes"}[row["result"]]
                metric[f"incumbent_{suffix}"] += 1
                if (source is not None or snapshot is not None) and row["result"] != "push":
                    outcome = float(row["result"] == "win")
                    metric["replayed_scored"] += 1
                    metric["incumbent_brier_sum"] += (float(row["model_probability"]) - outcome) ** 2
                    metric["challenger_brier_sum"] += (probability - outcome) ** 2
                if probability > float(row["market_probability"]):
                    metric["challenger_calls"] += 1
                    metric["challenger_pnl_units"] += pnl
                    metric[f"challenger_{suffix}"] += 1
            for metric in markets.values():
                scored = metric["replayed_scored"]
                metric["incumbent_brier"] = metric.pop("incumbent_brier_sum") / scored if scored else None
                metric["challenger_brier"] = metric.pop("challenger_brier_sum") / scored if scored else None
                for field in ("incumbent_pnl_units", "challenger_pnl_units"):
                    metric[field] = round(metric[field], 4)
            result["tiers"][f"{sport}_{tier}"] = dict(markets)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(compare()["tiers"], indent=2))
