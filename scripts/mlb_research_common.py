"""Shared pinned-cohort helpers for MLB v8/v9 research tooling.

The v8 contract is pinned here once: date boundaries, threshold, feature
order, and the post-freeze backfill exclusion. Every research script in
this workspace (parity report, evaluator, ablations) must consume this
module instead of re-deriving the cohort, so all comparisons sit on the
same rows.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np  # noqa: E402

from model_prediction.config import PROJECT_ROOT  # noqa: E402
from model_prediction.features.base import FeatureStore  # noqa: E402
from model_prediction.validation import (  # noqa: E402
    build_walk_forward_rows,
    chronological_split,
)

V8_ARTIFACT_PATH = PROJECT_ROOT / "config" / "models" / "mlb-elo-trend-lr-v8.json"


def sigmoid(z: float) -> float:
    return 1.0 / (1.0 + float(np.exp(-z)))


def v8_contract() -> dict:
    """The frozen v8 contract, verbatim from the shipped artifact."""
    artifact = json.loads(V8_ARTIFACT_PATH.read_text(encoding="utf-8"))
    moneyline = artifact["market_models"]["moneyline"]
    training = artifact["training"]
    return {
        "artifact": artifact,
        "feature_names": list(moneyline["feature_names"]),
        "coefficients": [float(c) for c in moneyline["coefficients"]],
        "intercept": float(moneyline["intercept"]),
        "threshold": float(moneyline["confidence_threshold"]),
        "positive_class": moneyline.get("positive_class"),
        "train_end": training["coefficient_fit"]["end"],
        "validation_end": training["threshold_selection"]["end"],
        "holdout_end": training["locked_holdout"]["end"],
        "holdout_observations": training["locked_holdout"]["observations"],
    }


def _identify_backfill_event_ids() -> set[str]:
    """Net-new event ids appended after v8's freeze (late 07-16..07-25
    ingest block after the 08-13 batch; identified via the last date
    descent in the ingest-ordered games file)."""
    days: list[tuple[int, str, str]] = []
    with open(PROJECT_ROOT / "data/historical/mlb_games_all.jsonl", encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            try:
                game = json.loads(line)
            except json.JSONDecodeError:
                continue
            days.append((i, str(game.get("event_start_utc") or "")[:10], game.get("event_id") or ""))
    descent = None
    for idx in range(len(days) - 1):
        if days[idx][1] > days[idx + 1][1]:
            descent = idx
    if descent is None:
        return set()
    late_positions = {i for i, day, _ in days[descent + 1 :] if day <= "2026-07-29"}
    first_position: dict[str, int] = {}
    for i, _, eid in days:
        first_position.setdefault(eid, i)
    return {eid for eid, pos in first_position.items() if pos in late_positions}


def pinned_cohort() -> dict:
    """Build the pinned split and return everything the research tools need.

    ``exact_holdout`` = harness holdout minus the post-freeze backfill
    (1,389 rows: the recorded 1,391 minus the 2 freeze-time rows lost
    from today's file — see docs/V8_REPRODUCTION.md).
    """
    contract = v8_contract()
    rows_end = (date.fromisoformat(contract["holdout_end"]) + timedelta(days=1)).isoformat()
    store = FeatureStore(PROJECT_ROOT / "data")
    rows = build_walk_forward_rows(store, "mlb", end_date=rows_end)
    train, validation, holdout, split_meta = chronological_split(
        rows,
        train_end_date=contract["train_end"],
        validation_end_date=contract["validation_end"],
    )
    backfill_ids = _identify_backfill_event_ids()
    exact_holdout = [r for r in holdout if r.event_id not in backfill_ids]
    return {
        "contract": contract,
        "train": train,
        "validation": validation,
        "holdout": holdout,
        "exact_holdout": exact_holdout,
        "backfill_ids": backfill_ids,
        "split_meta": split_meta,
    }


def shipped_probability(contract: dict, features: dict[str, float]) -> float:
    """v8's shipped decision function applied to a feature vector,
    honoring positive_class orientation."""
    z = contract["intercept"]
    for name, coef in zip(contract["feature_names"], contract["coefficients"], strict=True):
        z += coef * features[name]
    p = sigmoid(z)
    if contract["positive_class"] in ("away", "away_win", 0):
        return 1.0 - p
    return p
