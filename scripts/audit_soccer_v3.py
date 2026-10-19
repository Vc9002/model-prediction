"""Read-only post-hoc soccer correction diagnostic; never a promotion backtest.

The incumbent must reproduce saved probabilities AND numerical feature fields
for every row before the candidate's sensitivity results can be interpreted.
No candidate is selected from realized outcomes and no ledgers are written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path

from model_prediction.domain import EASTERN, parse_utc
from model_prediction.features.base import FeatureStore
from model_prediction.models.soccer import UpcomingMatch, soccer_model_v2
from model_prediction.models.soccer_v3 import soccer_model_v3
from model_prediction.runtime_paths import RuntimePaths


def audit(game_date: str) -> dict:
    date.fromisoformat(game_date)
    paths = RuntimePaths.resolve()
    connection = sqlite3.connect(
        f"file:{(paths.runtime_root / 'ledgers/ledgers.db').as_posix()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        records = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM ledger_records WHERE sport='soccer' AND ledger_tier='flat' "
                "AND model_id='soccer-poisson-dc-v2' AND status='settled'"
            )
            if parse_utc(row["event_start_utc"]).astimezone(EASTERN).date().isoformat() == game_date
        ]
    finally:
        connection.close()
    history = FeatureStore(paths.repo_root / "data").games_before("soccer", game_date)
    all_leagues = {game.league for game in history}
    rows = []
    incumbent = soccer_model_v2()
    candidate = soccer_model_v3()
    for record in records:
        payload = json.loads(record["decision_payload_json"])
        stored = json.loads(record["feature_payload_json"])
        # Resolve the competition using the exact event's source record, not a
        # team's most common league (which would misclassify cups/promotions).
        source = paths.repo_root / "data/raw/soccer" / game_date
        leagues = set()
        for path in source.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            for event in data.get("events", []):
                if str(event.get("id")) == record["event_id"]:
                    league = str(event.get("league") or path.stem.removeprefix("scores_")).upper()
                    if league in all_leagues:
                        leagues.add(league)
        if len(leagues) != 1:
            raise ValueError(f"Cannot prove unique event competition for {record['event_id']}: {leagues}")
        league = leagues.pop()
        match = UpcomingMatch(
            record["event_id"], record["event_start_utc"], payload["away_team"], payload["home_team"], league
        )
        control = next(p for p in incumbent.predict_games(history, [match]) if p.market_type == "moneyline")
        feature_deltas = {
            key: float(control.feature_basis[key]) - float(value)
            for key, value in stored["features"].items()
            if value not in (None, "") and key in control.feature_basis
        }
        probability_delta = control.probabilities[record["selection"]] - record["model_probability"]
        reproduced = abs(probability_delta) <= 1e-6 and all(
            abs(delta) <= 1e-4 for delta in feature_deltas.values()
        )
        corrected = next(p for p in candidate.predict_games(history, [match]) if p.market_type == "moneyline")
        rows.append(
            {
                "pick_id": record["pick_id"],
                "home_team": payload["home_team"],
                "away_team": payload["away_team"],
                "competition": league,
                "stored_probability": record["model_probability"],
                "incumbent_probability": control.probabilities[record["selection"]],
                "probability_delta": probability_delta,
                "feature_deltas": feature_deltas,
                "reproduction_class": "MATCH" if reproduced else "MISMATCH",
                "candidate_probability": corrected.probabilities[record["selection"]],
                "candidate_draw_probability": corrected.probabilities["draw"],
                "candidate_features": corrected.feature_basis,
                "original_payload_sha256": hashlib.sha256(
                    record["decision_payload_json"].encode()
                ).hexdigest(),
            }
        )
    return {
        "game_date_et": game_date,
        "scope": "POST_HOC_FIXED_PICKS_DIAGNOSTIC_NOT_PROSPECTIVE_QUALIFICATION",
        "incumbent_control": "soccer-poisson-dc-v2",
        "candidate": "soccer-poisson-dc-v3",
        "reproduction_gate": "PASS"
        if rows and all(r["reproduction_class"] == "MATCH" for r in rows)
        else "FAIL",
        "rows": rows,
        "automatic_orders_authorized": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.date)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps({"reproduction_gate": result["reproduction_gate"], "rows": len(result["rows"])}))
    raise SystemExit(0 if result["reproduction_gate"] == "PASS" else 1)
