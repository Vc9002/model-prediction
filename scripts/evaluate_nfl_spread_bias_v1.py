"""Read-only, date-forward diagnostic of NFL spread mean bias correction."""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from model_prediction.domain import parse_utc
from model_prediction.models.nfl_structural_v5 import discrete_spread_prob
from model_prediction.scalar_artifact_replay import replay_snapshot
from scripts.evaluate_nfl_market_anchor_v1 import _valid_row

EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class SpreadCase:
    event_id: str
    event_date: date
    decision_at: datetime
    settled_at: datetime
    artifact_hash: str
    mean: float
    sd: float
    home_line: float
    selection: str
    incumbent_probability: float
    outcome: int
    actual_margin: float
    quote_probability: float | None


def _selected_probability(case: SpreadCase, bias: float) -> float:
    home, away, _ = discrete_spread_prob(case.home_line, case.mean + bias, case.sd)
    total = home + away
    if total <= 0:
        raise ValueError("zero_nonpush_probability")
    return (home if case.selection == "home" else away) / total


def walkforward(cases: list[SpreadCase]) -> list[dict]:
    """Fit an intercept only on same-artifact, prior-date, already-settled calls."""
    evaluated = []
    for current in sorted(cases, key=lambda item: (item.event_date, item.decision_at, item.event_id)):
        prior = [
            item
            for item in cases
            if item.artifact_hash == current.artifact_hash
            and item.event_date < current.event_date
            and item.settled_at <= current.decision_at
        ]
        if not prior:
            continue
        bias = sum(item.actual_margin - item.mean for item in prior) / len(prior)
        corrected = _selected_probability(current, bias)
        evaluated.append(
            {
                "event_id": current.event_id,
                "event_date": current.event_date.isoformat(),
                "training_games": len(prior),
                "bias_points": bias,
                "outcome": current.outcome,
                "incumbent_probability": current.incumbent_probability,
                "corrected_probability": corrected,
                "quote_probability": current.quote_probability,
            }
        )
    return evaluated


def load_cases(database: Path) -> tuple[list[SpreadCase], dict[str, int]]:
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT * FROM ledger_records WHERE sport='nfl' AND ledger_tier='flat' "
        "AND market_type='spread' AND status='settled' ORDER BY created_at_utc"
    ).fetchall()
    connection.close()
    latest = {row["event_id"]: row for row in rows}
    cases = []
    excluded: dict[str, int] = defaultdict(int)
    for row in latest.values():
        try:
            if row["result"] not in ("win", "loss") or row["selection"] not in ("home", "away"):
                raise ValueError("unscorable_result_or_side")
            payload = json.loads(row["decision_payload_json"])
            snapshot = json.loads(payload["model_input_snapshot_json"])
            decision = parse_utc(row["created_at_utc"])
            start = parse_utc(snapshot["event_start_utc"])
            settled = parse_utc(row["settled_at_utc"])
            if not parse_utc(snapshot["observed_at_utc"]) <= decision < start <= settled:
                raise ValueError("invalid_decision_timing")
            if snapshot["event_id"] != row["event_id"] or snapshot["artifact_hash"] != row["model_artifact_hash"]:
                raise ValueError("snapshot_identity_mismatch")
            replayed = replay_snapshot(snapshot)[row["selection"]]
            if abs(replayed - row["model_probability"]) > 1e-5:
                raise ValueError("incumbent_replay_mismatch")
            inputs = snapshot["inference_inputs"]
            home_line = -float(inputs["line"])
            expected_line = home_line if row["selection"] == "home" else -home_line
            if abs(float(row["line"]) - expected_line) > 1e-6:
                raise ValueError("spread_line_orientation_mismatch")
            margin = float(payload["home_score"]) - float(payload["away_score"])
            selected_margin = margin + home_line if row["selection"] == "home" else -margin - home_line
            if selected_margin == 0 or int(selected_margin > 0) != int(row["result"] == "win"):
                raise ValueError("settlement_contradiction")
            quote = None
            try:
                valid = _valid_row(row)
                archive = Path(payload["market_snapshot_archive_path"])
                if valid is not None and archive.is_file():
                    quote = valid[1].market_probability
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                pass
            cases.append(
                SpreadCase(
                    event_id=row["event_id"],
                    event_date=start.astimezone(EASTERN).date(),
                    decision_at=decision,
                    settled_at=settled,
                    artifact_hash=snapshot["artifact_hash"],
                    mean=float(inputs["mean"]),
                    sd=float(inputs["sd"]),
                    home_line=home_line,
                    selection=row["selection"],
                    incumbent_probability=float(row["model_probability"]),
                    outcome=int(row["result"] == "win"),
                    actual_margin=margin,
                    quote_probability=quote,
                )
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            excluded[str(exc)] += 1
    return cases, dict(excluded)


def _brier(rows: list[dict], key: str) -> float:
    return sum((row[key] - row["outcome"]) ** 2 for row in rows) / len(rows)


def evaluate(database: Path) -> dict:
    cases, excluded = load_cases(database)
    rows = walkforward(cases)
    if not rows:
        return {"eligible_cases": len(cases), "walkforward_cases": 0, "excluded": excluded}
    dates = sorted({row["event_date"] for row in rows})
    baseline = _brier(rows, "incumbent_probability")
    candidate = _brier(rows, "corrected_probability")
    quote_rows = [row for row in rows if row["quote_probability"] is not None]
    rng = random.Random(20261029)
    gains = []
    for _ in range(2000):
        sampled_dates = rng.choices(dates, k=len(dates))
        sample = [row for day in sampled_dates for row in rows if row["event_date"] == day]
        gains.append(_brier(sample, "incumbent_probability") - _brier(sample, "corrected_probability"))
    gains.sort()
    return {
        "scope": "retrospective selected-call diagnostic; prior complete dates and settled outcomes only; no orders",
        "eligible_cases": len(cases),
        "walkforward_cases": len(rows),
        "walkforward_dates": dates,
        "incumbent_brier": baseline,
        "corrected_brier": candidate,
        "brier_gain": baseline - candidate,
        "date_bootstrap_gain_95": [gains[50], gains[1949]],
        "quote_backed_cases": len(quote_rows),
        "quote_paired_incumbent_brier": _brier(quote_rows, "incumbent_probability") if quote_rows else None,
        "quote_paired_corrected_brier": _brier(quote_rows, "corrected_probability") if quote_rows else None,
        "quote_raw_ask_brier": _brier(quote_rows, "quote_probability") if quote_rows else None,
        "excluded": excluded,
        "per_game": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.database), indent=2, sort_keys=True))
