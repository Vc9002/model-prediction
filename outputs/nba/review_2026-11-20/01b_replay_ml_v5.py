"""EXPLORATORY, READ-ONLY: replay the served NBA moneyline champion (nba-elo-trend-lr-v5) with
validation.build_walk_forward_rows (the construction its coefficients were fit on and that
learned_forward serves with, per its parity comment). Writes ml_rows.jsonl here only."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

REPO = Path("e:/MP")
sys.path.insert(0, str(REPO / "src"))

from model_prediction.features.base import FeatureStore
from model_prediction.validation import build_walk_forward_rows

art = json.loads((REPO / "config/models/nba-elo-trend-lr-v5.json").read_text())["market_models"]["moneyline"]
rows = build_walk_forward_rows(FeatureStore(REPO / "data"), "nba", minimum_history_games=200)
out = Path(__file__).resolve().parent / "ml_rows.jsonl"
with out.open("w", encoding="utf-8") as fh:
    for r in rows:
        z = art["intercept"] + sum(
            c * float(getattr(r, f)) for c, f in zip(art["coefficients"], art["feature_names"], strict=True)
        )
        p = 1 / (1 + math.exp(-z))
        fh.write(
            json.dumps(
                {
                    "date": r.date,
                    "event_id": r.event_id,
                    "outcome": r.outcome,
                    "p_v5": p,
                    "elo_probability": r.elo_probability,
                    "trend_gap": r.trend_gap,
                    "defensive_trend_gap": r.defensive_trend_gap,
                }
            )
            + "\n"
        )
print("ml rows", len(rows))
