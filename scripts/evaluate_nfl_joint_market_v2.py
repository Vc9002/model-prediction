"""Read-only replay of market-anchored joint NFL score probabilities."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path

from model_prediction.domain import parse_utc
from model_prediction.learned_replay import replay_snapshot as replay_learned
from model_prediction.models.nfl_joint_market_v2 import forecast_with_market_anchor
from model_prediction.models.nfl_market_anchor_v1 import asof_market_weight
from model_prediction.production_registry import compute_artifact_hash
from model_prediction.scalar_artifact_replay import replay_snapshot as replay_scalar
from scripts.evaluate_nfl_market_anchor_v1 import _valid_row


def evaluate(db_path: Path) -> dict:
    artifact_path = Path(__file__).resolve().parents[1] / "config/models/challengers/nfl-joint-market-v2.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if artifact["artifact_hash"] != compute_artifact_hash(artifact):
        raise ValueError("joint_market_v2_artifact_hash_mismatch")
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT event_id, market_type, selection, line, model_probability, result, "
        "status, created_at_utc, decision_payload_json FROM ledger_records "
        "WHERE sport='nfl' AND ledger_tier='flat' AND market_type IN "
        "('moneyline','spread','total') ORDER BY created_at_utc"
    ).fetchall()
    connection.close()
    latest = {(row["event_id"], row["market_type"]): row for row in rows}
    by_event: dict[str, dict[str, sqlite3.Row]] = defaultdict(dict)
    moneyline_history = []
    for (event, market), row in latest.items():
        by_event[event][market] = row
        if market == "moneyline":
            try:
                valid = _valid_row(row)
            except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                valid = None
            if valid is not None:
                moneyline_history.append((event, valid))
    metrics: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    skipped: dict[str, int] = defaultdict(int)
    examples = {}
    for event, markets in by_event.items():
        if not {"moneyline", "spread", "total"} <= set(markets):
            skipped["incomplete_triad"] += 1
            continue
        if any(
            row["status"] != "settled" or row["result"] not in ("win", "loss") for row in markets.values()
        ):
            skipped["unsettled_or_push"] += 1
            continue
        valid = next((item for candidate_event, item in moneyline_history if candidate_event == event), None)
        if valid is None:
            skipped["no_valid_moneyline_quote"] += 1
            continue
        _, quote, created, _ = valid
        try:
            weight = asof_market_weight(
                [previous for _, (_, previous, _, _) in moneyline_history],
                quote.event_date,
                created,
            )
        except ValueError:
            skipped["no_asof_training_history"] += 1
            continue
        try:
            snapshots = {
                market: json.loads(json.loads(row["decision_payload_json"])["model_input_snapshot_json"])
                for market, row in markets.items()
            }
            for market in ("moneyline", "spread", "total"):
                if snapshots[market]["artifact_hash"] != artifact["inputs"][f"{market}_artifact_hash"]:
                    raise ValueError("parent_artifact_hash_mismatch")
                observed = parse_utc(snapshots[market]["observed_at_utc"])
                decision = parse_utc(markets[market]["created_at_utc"])
                start = parse_utc(snapshots[market]["event_start_utc"])
                if not observed <= decision < start:
                    raise ValueError("snapshot_not_available_at_decision")
            learned_home = replay_learned(snapshots["moneyline"])
            learned_selected = (
                learned_home if markets["moneyline"]["selection"] == "home" else 1 - learned_home
            )
            if abs(learned_selected - markets["moneyline"]["model_probability"]) > 1e-5:
                raise ValueError("moneyline_replay_probability_mismatch")
            for market in ("spread", "total"):
                replayed = replay_scalar(snapshots[market])
                if abs(replayed[markets[market]["selection"]] - markets[market]["model_probability"]) > 1e-5:
                    raise ValueError("scalar_replay_probability_mismatch")
            margin = snapshots["spread"]["inference_inputs"]
            total = snapshots["total"]["inference_inputs"]
            market_home = (
                quote.market_probability
                if markets["moneyline"]["selection"] == "home"
                else 1 - quote.market_probability
            )
            forecast = forecast_with_market_anchor(
                learned_home,
                market_home,
                weight,
                float(margin["mean"]),
                float(margin["sd"]),
                float(total["mean"]),
                float(total["sd"]),
                float(snapshots["total"]["artifact"]["league_avg_total"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            skipped[f"invalid_snapshot:{exc}"] += 1
            continue
        spread = markets["spread"]
        total_row = markets["total"]
        candidate = {
            "moneyline": forecast.home_win_probability()
            if markets["moneyline"]["selection"] == "home"
            else 1 - forecast.home_win_probability(),
            "spread": forecast.home_cover_probability(float(spread["line"]))
            if spread["selection"] == "home"
            else forecast.away_cover_probability(float(spread["line"])),
            "total": forecast.over_probability(float(total_row["line"]))
            if total_row["selection"] == "over"
            else forecast.under_probability(float(total_row["line"])),
        }
        for market, row in markets.items():
            y = float(row["result"] == "win")
            old = float(row["model_probability"])
            new = candidate[market]
            metrics[market]["n"] += 1
            metrics[market]["incumbent_brier_sum"] += (old - y) ** 2
            metrics[market]["challenger_brier_sum"] += (new - y) ** 2
            metrics[market]["incumbent_log_loss_sum"] -= math.log(old if y else 1 - old)
            metrics[market]["challenger_log_loss_sum"] -= math.log(new if y else 1 - new)
        if event == "401873009":
            examples[event] = {
                "market_weight": weight,
                "philadelphia_win": forecast.home_win_probability(),
                "dallas_minus_4_5": forecast.away_cover_probability(-4.5),
                "over_43_5": forecast.over_probability(43.5),
            }
    summary = {}
    for market, sums in metrics.items():
        n = int(sums["n"])
        summary[market] = {
            "n": n,
            **{key.removesuffix("_sum"): value / n for key, value in sums.items() if key.endswith("_sum")},
        }
    return {
        "markets": summary,
        "skipped": dict(skipped),
        "eagles_cowboys": examples.get("401873009"),
        "scope": "retrospective research; market weight from previously settled game dates only; no orders",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.database), indent=2, sort_keys=True))
