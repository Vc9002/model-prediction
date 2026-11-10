"""Read-only v8 diagnostic on every settled NFL spread ledger game.

The ledger contains selected-side prices, not complete two-sided closing quotes.
This replay is intentionally blocked from promotion when incumbent parity fails.
"""

from __future__ import annotations

import json
import random
import sqlite3
from collections import Counter
from pathlib import Path
from statistics import mean as average

from model_prediction.domain import parse_utc
from model_prediction.features.nfl_efficiency import NFLEfficiencyStore
from model_prediction.models.nfl_scoring_simulation import solve_bivariate_scoring
from model_prediction.models.nfl_spread_v7 import NFLSpreadModelV7
from model_prediction.models.nfl_spread_v8 import NFLSpreadModelV8, _historical_symmetric_margin_pmf
from model_prediction.models.nfl_structural_v5 import _tilt_distribution
from model_prediction.runtime_paths import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]


def replay() -> dict:
    paths = RuntimePaths.resolve(repo_root=ROOT)
    conn = sqlite3.connect(f"file:{paths.ledgers_db.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    ledger = conn.execute(
        """SELECT pick_id, ledger_tier, event_id, selection, line, model_id,
                  market_probability, result, decision_payload_json
           FROM ledger_records
           WHERE lower(sport)='nfl' AND market_type='spread' AND status='settled'
           ORDER BY event_id, CASE ledger_tier WHEN 'flat' THEN 0 ELSE 1 END"""
    ).fetchall()
    history = {
        str(row["event_id"]): row
        for line in (ROOT / "data/historical/nfl_games_all.jsonl").read_text(encoding="utf-8").splitlines()
        if (row := json.loads(line)).get("status") == "completed"
    }
    v7 = NFLSpreadModelV7.from_artifact(
        json.loads((ROOT / "config/models/nfl-spread-structural-v7.json").read_text(encoding="utf-8"))
    )
    v8 = NFLSpreadModelV8.from_artifact(
        json.loads((ROOT / "config/models/nfl-spread-structural-v8.json").read_text(encoding="utf-8"))
    )
    pmf_base = _historical_symmetric_margin_pmf()
    current_efficiency = NFLEfficiencyStore.get_default(ROOT / "data")
    efficiency_cache = {}
    rows = []
    missing = []
    for record in ledger:
        payload = json.loads(record["decision_payload_json"])
        event = history.get(str(record["event_id"]))
        if event is None:
            missing.append(str(record["event_id"]))
            continue
        selection = str(record["selection"]).lower()
        if selection not in ("home", "away") or record["line"] is None:
            missing.append(str(record["event_id"]))
            continue
        home_line = float(record["line"]) * (1 if selection == "home" else -1)
        snapshot_raw = payload.get("model_input_snapshot_json")
        snapshot = json.loads(snapshot_raw) if snapshot_raw else {}
        features = snapshot.get("features") or {}
        start = str(event["event_start_utc"])
        source = features.get("efficiency_source") or {}
        observed_at = source.get("observed_at_utc")
        if observed_at and observed_at not in efficiency_cache:
            efficiency_cache[observed_at] = NFLEfficiencyStore.get_default(
                ROOT / "data", observed_at=parse_utc(observed_at)
            )
        efficiency = efficiency_cache.get(observed_at, current_efficiency)
        source_verified = bool(
            observed_at
            and parse_utc(observed_at) < parse_utc(start)
            and efficiency.source_manifest
            and efficiency.source_manifest.get("manifest_sha256") == source.get("manifest_sha256")
        )
        eff = efficiency.get_matchup_efficiency(
            home_team=event["home_team"],
            away_team=event["away_team"],
            as_of_date=start[:10],
            home_qb=features.get("home_qb"),
            away_qb=features.get("away_qb"),
        )
        kwargs = {
            "spread_home_line": home_line,
            "home_rest_days": int(features.get("home_rest_days") or 7),
            "away_rest_days": int(features.get("away_rest_days") or 7),
            "is_neutral_site": bool(event.get("is_neutral_site", False)),
        }
        old = v7.forecast_from_efficiency(eff, **kwargs)
        new = v8.forecast_from_efficiency(eff, **kwargs)
        stored_mean = (snapshot.get("inference_inputs") or {}).get("mean")
        exact_mean_v8_probability = None
        if payload.get("model_version") == "nfl-spread-structural-v7" and stored_mean is not None:
            mean = float(stored_mean)
            sd = v8.margin_sd * (1.25 if abs(home_line) >= 8 else 1.0)
            pmf = _tilt_distribution({m: p ** (v8.margin_sd / sd) for m, p in pmf_base.items()}, mean)
            cutoff = -home_line
            home_cover = sum(p for margin, p in pmf.items() if margin > cutoff)
            away_cover = sum(p for margin, p in pmf.items() if margin < cutoff)
            sim = solve_bivariate_scoring(
                round(23.0 + mean / 2.0, 1),
                round(23.0 - mean / 2.0, 1),
                spread_home_line=home_line,
                rho=v8.rho,
            )
            home_cover = (1 - v8.simulation_weight) * home_cover + v8.simulation_weight * sim.prob_home_cover
            away_cover = (1 - v8.simulation_weight) * away_cover + v8.simulation_weight * sim.prob_away_cover
            exact_mean_v8_probability = home_cover if selection == "home" else away_cover
        margin = int(event["home_score"]) - int(event["away_score"])
        outcome = "push" if margin == -home_line else ("home" if margin > -home_line else "away")
        recorded_p = float(record["market_probability"]) if record["market_probability"] is not None else None
        stored_model_p = (
            float(payload["model_probability"])
            if payload.get("model_probability") not in (None, "")
            else None
        )
        rows.append(
            {
                "pick_id": record["pick_id"],
                "tier": record["ledger_tier"],
                "event_id": record["event_id"],
                "event_start_utc": start,
                "selection": selection,
                "home_line": home_line,
                "outcome": outcome,
                "stored_result": record["result"],
                "source_model": record["model_id"],
                "stored_model_probability": stored_model_p,
                "v7_probability": old.prob_home_cover if selection == "home" else old.prob_away_cover,
                "v8_probability": new.prob_home_cover if selection == "home" else new.prob_away_cover,
                "v8_probability_on_stored_mean": exact_mean_v8_probability,
                "v8_push_probability": new.prob_spread_push,
                "decision_market_probability": recorded_p,
                "recorded_clv": payload.get("probability_clv") or None,
                "qb_from_pregame_snapshot": bool(features.get("home_qb") and features.get("away_qb")),
                "efficiency_source_observed_at_utc": (features.get("efficiency_source") or {}).get(
                    "observed_at_utc"
                ),
                "efficiency_snapshot_verified": source_verified,
            }
        )
    conn.close()

    flat = [row for row in rows if row["tier"] == "flat"]
    v7_control = [row for row in rows if row["source_model"] == "nfl-spread-structural-v7"]
    v7_flat = [row for row in flat if row["source_model"] == "nfl-spread-structural-v7"]
    parity = [
        abs(row["stored_model_probability"] - row["v7_probability"])
        for row in v7_control
        if row["stored_model_probability"] is not None
    ]
    paired_brier_gain = [
        (row["stored_model_probability"] - float(row["outcome"] == row["selection"])) ** 2
        - (row["v8_probability_on_stored_mean"] - float(row["outcome"] == row["selection"])) ** 2
        for row in v7_flat
        if row["outcome"] != "push"
        and row["stored_model_probability"] is not None
        and row["v8_probability_on_stored_mean"] is not None
    ]
    rng = random.Random(20261110)
    bootstrap = sorted(
        average(rng.choices(paired_brier_gain, k=len(paired_brier_gain))) for _ in range(10000)
    )

    def score(cohort: list[dict], field: str) -> dict:
        binary = [row for row in cohort if row["outcome"] != "push" and row[field] is not None]
        if not binary:
            return {"n": 0, "brier": None}
        return {
            "n": len(binary),
            "brier": sum((row[field] - float(row["outcome"] == row["selection"])) ** 2 for row in binary)
            / len(binary),
        }

    report = {
        "settled_contract_rows": len(ledger),
        "scored_rows": len(rows),
        "unique_events": len({row["event_id"] for row in rows}),
        "unmatched_event_ids": sorted(set(missing)),
        "result_mismatches": [
            row["pick_id"]
            for row in rows
            if row["stored_result"]
            != (
                "push"
                if row["outcome"] == "push"
                else "win"
                if row["outcome"] == row["selection"]
                else "loss"
            )
        ],
        "tiers": dict(Counter(row["tier"] for row in rows)),
        "flat_selected_side_scores": {
            "market": score(flat, "decision_market_probability"),
            "v7": score(flat, "v7_probability"),
            "v8": score(flat, "v8_probability"),
        },
        "v7_control": {
            "rows": len(v7_control),
            "max_absolute_probability_error": max(parity) if parity else None,
            "within_one_probability_point": sum(error <= 0.01 for error in parity),
        },
        "stored_mean_distribution_ablation": {
            "stored_v7": score(v7_flat, "stored_model_probability"),
            "v8_pmf": score(v7_flat, "v8_probability_on_stored_mean"),
            "paired_brier_gain_v8": average(paired_brier_gain),
            "paired_bootstrap_95_interval": [bootstrap[249], bootstrap[9749]],
        },
        "recorded_clv_rows": sum(row["recorded_clv"] is not None for row in rows),
        "qb_snapshot_rows": sum(row["qb_from_pregame_snapshot"] for row in rows),
        "efficiency_snapshot_verified_rows": sum(row["efficiency_snapshot_verified"] for row in rows),
        "promotion_blocked": not parity or max(parity) > 0.01,
        "limitations": [
            "Current efficiency store is filtered by game date but its source snapshot may postdate older games.",
            "Selected-side market prices are not two-sided no-vig closing prices.",
            "Recorded CLV belongs to incumbent calls and cannot be assigned to hypothetical v8 calls.",
        ],
        "rows": rows,
    }
    output = ROOT / "outputs/research/nfl_v8_settled_replay_20261110.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    result = replay()
    print(json.dumps({key: value for key, value in result.items() if key != "rows"}, indent=2))
