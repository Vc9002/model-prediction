"""Read-only replay of the NFL joint-score research challenger on saved Flat calls."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from model_prediction.domain import parse_utc
from model_prediction.learned_replay import replay_snapshot as replay_learned
from model_prediction.models.nfl_joint_score_v1 import joint_score_forecast
from model_prediction.production_registry import compute_artifact_hash
from model_prediction.scalar_artifact_replay import replay_snapshot as replay_scalar


def evaluate(db_path: Path) -> dict:
    artifact_path = Path(__file__).resolve().parents[1] / "config/models/challengers/nfl-joint-score-v1.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if artifact["artifact_hash"] != compute_artifact_hash(artifact):
        raise ValueError("joint_score_challenger_artifact_hash_mismatch")
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT event_id, market_type, selection, line, model_probability, result, "
        "created_at_utc, status, decision_payload_json FROM ledger_records "
        "WHERE sport='nfl' AND ledger_tier='flat' AND market_type IN "
        "('moneyline','spread','total') ORDER BY created_at_utc"
    ).fetchall()
    connection.close()
    latest: dict[tuple[str, str], sqlite3.Row] = {}
    for row in rows:
        latest[(row["event_id"], row["market_type"])] = row
    by_event: dict[str, dict[str, sqlite3.Row]] = defaultdict(dict)
    for (event, market), row in latest.items():
        if row["status"] == "settled" and row["result"] in ("win", "loss"):
            by_event[event][market] = row
    metrics: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    examples: dict[str, dict] = {}
    skipped: dict[str, int] = defaultdict(int)
    for event, markets in by_event.items():
        if set(markets) != {"moneyline", "spread", "total"}:
            skipped["incomplete_triad"] += 1
            continue
        snapshots = {}
        payloads = {}
        try:
            for market, row in markets.items():
                payload = json.loads(row["decision_payload_json"])
                snapshot = json.loads(payload["model_input_snapshot_json"])
                if parse_utc(snapshot["observed_at_utc"]) >= parse_utc(snapshot["event_start_utc"]):
                    raise ValueError("not_pregame")
                if market == "moneyline":
                    learned_home = replay_learned(snapshot)
                    selected = learned_home if row["selection"] == "home" else 1 - learned_home
                    if abs(selected - row["model_probability"]) > 1e-5:
                        raise ValueError("moneyline_probability_mismatch")
                else:
                    replayed = replay_scalar(snapshot)
                    if abs(replayed[row["selection"]] - row["model_probability"]) > 1e-5:
                        raise ValueError("scalar_probability_mismatch")
                snapshots[market] = snapshot
                payloads[market] = payload
            spread = snapshots["spread"]
            total = snapshots["total"]
            for market in ("moneyline", "spread", "total"):
                if snapshots[market]["artifact_hash"] != artifact["inputs"][f"{market}_artifact_hash"]:
                    raise ValueError("joint_score_parent_artifact_hash_mismatch")
            forecast = joint_score_forecast(
                learned_home,
                float(spread["inference_inputs"]["mean"]),
                float(spread["inference_inputs"]["sd"]),
                float(total["inference_inputs"]["mean"]),
                float(total["inference_inputs"]["sd"]),
                float(total["artifact"]["league_avg_total"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            skipped[f"invalid_snapshot:{exc}"] += 1
            continue
        spread_row = markets["spread"]
        total_row = markets["total"]
        spread_line = float(spread_row["line"])
        candidate = {
            "moneyline": forecast.home_win_probability()
            if markets["moneyline"]["selection"] == "home"
            else 1 - forecast.home_win_probability(),
            "spread": forecast.home_cover_probability(spread_line)
            if spread_row["selection"] == "home"
            else forecast.away_cover_probability(spread_line),
            "total": forecast.over_probability(float(total_row["line"]))
            if total_row["selection"] == "over"
            else forecast.under_probability(float(total_row["line"])),
        }
        for market, row in markets.items():
            outcome = float(row["result"] == "win")
            incumbent_p = float(row["model_probability"])
            challenger_p = candidate[market]
            metrics[market]["n"] += 1
            metrics[market]["incumbent_brier_sum"] += (incumbent_p - outcome) ** 2
            metrics[market]["challenger_brier_sum"] += (challenger_p - outcome) ** 2
            metrics[market]["absolute_probability_change_sum"] += abs(challenger_p - incumbent_p)
        if event == "401873009":
            examples[event] = {
                "home_team": payloads["moneyline"]["home_team"],
                "away_team": payloads["moneyline"]["away_team"],
                "home_win_probability": forecast.home_win_probability(),
                "away_cover_probability": forecast.away_cover_probability(spread_line),
                "over_probability": forecast.over_probability(float(total_row["line"])),
                "observed_at_utc": snapshots["moneyline"]["observed_at_utc"],
            }
    summary = {}
    for market, sums in metrics.items():
        n = int(sums["n"])
        summary[market] = {
            "n": n,
            "incumbent_brier": sums["incumbent_brier_sum"] / n,
            "challenger_brier": sums["challenger_brier_sum"] / n,
            "mean_absolute_probability_change": sums["absolute_probability_change_sum"] / n,
        }
    return {
        "cohort_events": len(by_event)
        - skipped["incomplete_triad"]
        - sum(v for k, v in skipped.items() if k.startswith("invalid_snapshot")),
        "markets": summary,
        "eagles_cowboys": examples.get("401873009"),
        "skipped": dict(skipped),
        "scope": "retrospective development; saved pregame Flat calls; no new picks or orders",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.database), indent=2, sort_keys=True))
