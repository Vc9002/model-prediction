"""Research-only, same-selection replay of college model challengers.

No ledger writes, no orders, and no opposite-side price reconstruction.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from scipy.stats import norm

from model_prediction.domain import parse_utc
from model_prediction.features.base import FeatureStore
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.basketball_leagues import ncaab_v3_model
from model_prediction.runtime_paths import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]
TRAINING = ROOT / "scratch/ncaab_overhaul_training.jsonl"
OUTPUT = ROOT / "outputs/research/college_challengers_2026-11-04"


def _fit(rows: list[dict], input_key: str, outcome_key: str) -> dict:
    x = np.array([r[input_key] for r in rows], dtype=float)
    y = np.array([r[outcome_key] for r in rows], dtype=float)
    intercept, slope = np.linalg.lstsq(np.column_stack([np.ones(len(x)), x]), y, rcond=None)[0]
    sigma = float(np.std(y - (intercept + slope * x), ddof=2))
    return {"intercept": float(intercept), "slope": float(slope), "sd": sigma}


def _historical_score(rows: list[dict], input_key: str, outcome_key: str, fit: dict) -> dict:
    old = np.array([r[input_key] for r in rows], dtype=float)
    actual = np.array([r[outcome_key] for r in rows], dtype=float)
    new = fit["intercept"] + fit["slope"] * old
    return {
        "n": len(rows),
        "old_mae": float(np.mean(np.abs(actual - old))),
        "new_mae": float(np.mean(np.abs(actual - new))),
    }


def _score(rows: list[dict]) -> dict:
    graded = [r for r in rows if r["result"] in ("win", "loss")]
    if not graded:
        return {"n": 0}
    y = np.array([float(r["result"] == "win") for r in graded])
    p_old = np.clip([r["old_probability"] for r in graded], 1e-6, 1 - 1e-6)
    p_new = np.clip([r["new_probability"] for r in graded], 1e-6, 1 - 1e-6)
    return {
        "n": len(graded),
        "wins": int(sum(y)),
        "old_brier": float(np.mean((p_old - y) ** 2)),
        "new_brier": float(np.mean((p_new - y) ** 2)),
        "old_logloss": float(np.mean(-y * np.log(p_old) - (1 - y) * np.log(1 - p_old))),
        "new_logloss": float(np.mean(-y * np.log(p_new) - (1 - y) * np.log(1 - p_new))),
        "original_pnl_units": round(sum(r["pnl_units"] for r in graded), 4),
    }


def _ncaab_probability(
    market: str,
    selection: str,
    line: float | None,
    margin: float,
    total: float,
    margin_sd: float,
    total_sd: float,
) -> float:
    if market == "moneyline":
        home = float(norm.cdf(margin / margin_sd))
        return home if selection == "home" else 1 - home
    if market == "spread":
        assert line is not None
        away = (
            float(norm.cdf((line - margin) / margin_sd))
            if selection == "away"
            else float(norm.cdf((-line - margin) / margin_sd))
        )
        return away if selection == "away" else 1 - away
    assert line is not None
    over = float(norm.sf((line - total) / total_sd))
    return over if selection == "over" else 1 - over


def _ncaaf_probability(row: sqlite3.Row, snapshot: dict) -> float:
    inference = snapshot["inference_inputs"]
    mu_margin = float(inference["mu_home"]) - float(inference["mu_away"])
    mu_total = float(inference["mu_home"]) + float(inference["mu_away"])
    margin_sd = float(inference["parameters"]["margin_sd"])
    total_sd = float(inference["parameters"]["total_sd"])
    if row["market_type"] == "moneyline":
        p_home = float(norm.cdf(mu_margin / margin_sd))
        return p_home if row["selection"] == "home" else 1 - p_home
    if row["market_type"] == "spread":
        line = float(row["line"])
        p_away = (
            float(norm.cdf((line - mu_margin) / margin_sd))
            if row["selection"] == "away"
            else float(norm.cdf((-line - mu_margin) / margin_sd))
        )
        return p_away if row["selection"] == "away" else 1 - p_away
    line = float(row["line"])
    p_over = float(norm.sf((line - mu_total) / total_sd))
    return p_over if row["selection"] == "over" else 1 - p_over


def main() -> None:
    training_bytes = TRAINING.read_bytes()
    training = [json.loads(x) for x in training_bytes.splitlines() if x]
    dates = sorted({r["date"] for r in training})
    train_end = dates[int(0.6 * len(dates))]
    val_end = dates[int(0.8 * len(dates))]
    fit_rows = [r for r in training if r["date"] < train_end]
    margin_fit = _fit(fit_rows, "expected_margin", "actual_margin")
    total_fit = _fit(fit_rows, "expected_total", "actual_total")
    artifact = {
        "model_id": "ncaab-calibrated-margin-v4-research",
        "training_source_sha256": hashlib.sha256(training_bytes).hexdigest(),
        "train_end_exclusive": train_end,
        "validation_end_exclusive": val_end,
        "train_games": len(fit_rows),
        "margin": margin_fit,
        "total": total_fit,
    }
    historical_splits = {
        "train": fit_rows,
        "validation": [r for r in training if train_end <= r["date"] < val_end],
        "holdout": [r for r in training if r["date"] >= val_end],
    }
    historical_metrics = {
        split: {
            "margin": _historical_score(rows, "expected_margin", "actual_margin", margin_fit),
            "total": _historical_score(rows, "expected_total", "actual_total", total_fit),
        }
        for split, rows in historical_splits.items()
    }
    database = RuntimePaths.resolve(repo_root=ROOT).ledgers_db
    conn = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    result: dict = {
        "artifact": artifact,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "database": str(database),
        "historical_metrics": historical_metrics,
        "decision": "REJECT_NCAAB_MARGIN_AND_NCAAF_TOTAL_CHALLENGERS",
        "limitations": [
            "NCAAB original ledger has no feature snapshots; recomputation uses current historical store.",
            "NCAAF rows without pregame inference snapshots are excluded, not reconstructed.",
            "All available NCAAF snapshot records mark historical source observation times unverified.",
            "The ledger records only incumbent-selected sides; opposite-side prices were not invented.",
            "This is retrospective paired scoring, not prospective evidence or executable P&L.",
        ],
        "cohorts": {},
    }
    for sport in ("ncaab", "ncaaf"):
        records = conn.execute(
            "SELECT * FROM ledger_records WHERE sport=? AND ledger_tier='flat' AND status='settled' ORDER BY event_start_utc,event_id,market_type",
            (sport,),
        ).fetchall()
        replayed: list[dict] = []
        excluded: list[dict] = []
        by_event: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in records:
            by_event[row["event_id"]].append(row)
        for event_id, event_rows in by_event.items():
            first = event_rows[0]
            if sport == "ncaab":
                decision = json.loads(first["decision_payload_json"] or "{}")
                event_start = parse_utc(first["event_start_utc"])
                history = FeatureStore(ROOT / "data").games_before("ncaab", event_start.date().isoformat())
                # A score is observable only after the previous game has finished.
                history = [g for g in history if g.start + timedelta(hours=4) < event_start]
                spread_row = next((r for r in event_rows if r["market_type"] == "spread"), None)
                total_row = next((r for r in event_rows if r["market_type"] == "total"), None)
                spread_away = None
                if spread_row is not None:
                    spread_away = (
                        float(spread_row["line"])
                        if spread_row["selection"] == "away"
                        else -float(spread_row["line"])
                    )
                game = UpcomingGame(
                    event_id,
                    first["event_start_utc"],
                    decision["away_team"],
                    decision["home_team"],
                    spread_away_line=spread_away,
                    total_line=None if total_row is None else float(total_row["line"]),
                    league="NCAAB",
                )
                predicted = ncaab_v3_model().predict_games(history, [game])
                features = predicted[0].feature_basis
                incumbent = {prediction.market_type: prediction for prediction in predicted}
                margin = margin_fit["intercept"] + margin_fit["slope"] * features["expected_margin"]
                total = total_fit["intercept"] + total_fit["slope"] * features["expected_total"]
                for row in event_rows:
                    p = _ncaab_probability(
                        row["market_type"],
                        row["selection"],
                        row["line"],
                        margin,
                        total,
                        margin_fit["sd"],
                        total_fit["sd"],
                    )
                    recalculated = incumbent[row["market_type"]].probabilities[row["selection"]]
                    replayed.append(
                        {
                            "event_id": event_id,
                            "event_start_utc": row["event_start_utc"],
                            "model_id": row["model_id"],
                            "market": row["market_type"],
                            "selection": row["selection"],
                            "line": row["line"],
                            "old_probability": row["model_probability"],
                            "recalculated_incumbent_probability": recalculated,
                            "new_probability": p,
                            "market_probability": row["market_probability"],
                            "units": row["units"],
                            "result": row["result"],
                            "pnl_units": row["pnl_units"] or 0.0,
                        }
                    )
            else:
                for row in event_rows:
                    payload = json.loads(row["feature_payload_json"] or "{}")
                    snapshot = payload.get("model_input_snapshot")
                    if not isinstance(snapshot, dict) or not isinstance(
                        snapshot.get("inference_inputs"), dict
                    ):
                        excluded.append(
                            {
                                "event_id": event_id,
                                "model_id": row["model_id"],
                                "market": row["market_type"],
                                "reason": "no_pregame_replay_snapshot",
                            }
                        )
                        continue
                    p = _ncaaf_probability(row, snapshot)
                    replayed.append(
                        {
                            "event_id": event_id,
                            "event_start_utc": row["event_start_utc"],
                            "model_id": row["model_id"],
                            "market": row["market_type"],
                            "selection": row["selection"],
                            "line": row["line"],
                            "old_probability": row["model_probability"],
                            "new_probability": p,
                            "market_probability": row["market_probability"],
                            "units": row["units"],
                            "result": row["result"],
                            "pnl_units": row["pnl_units"] or 0.0,
                        }
                    )
        result["cohorts"][sport] = {
            "total_settled_rows": len(records),
            "total_settled_events": len(by_event),
            "replayed_rows": len(replayed),
            "replayed_events": len({r["event_id"] for r in replayed}),
            "excluded": excluded,
            "markets": {
                market: _score([r for r in replayed if r["market"] == market])
                for market in ("moneyline", "spread", "total")
            },
        }
        OUTPUT.mkdir(parents=True, exist_ok=True)
        (OUTPUT / f"{sport}_paired_rows.jsonl").write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in replayed), encoding="utf-8"
        )
    (OUTPUT / "ncaab_training_rows.jsonl").write_bytes(training_bytes)
    (OUTPUT / "report.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                sport: {
                    "coverage": (cohort["replayed_rows"], cohort["total_settled_rows"]),
                    "markets": cohort["markets"],
                }
                for sport, cohort in result["cohorts"].items()
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
