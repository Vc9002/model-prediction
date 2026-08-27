"""WNBA totals: OOF stacking + game-specific sigma probe (plan Phase E).

Inside the 6-week snapshot window only (the only window with market
lines): three components — the four-factors structural projection, the
incumbent 12-feature ridge, and the market line itself — are stacked
with a ridge meta-model fitted on out-of-fold validation predictions,
and a per-game sigma model (ridge on |residual|) is compared against
the fixed validation sigma. Everything is judged against the market
line on the holdout. 49/24/25 rows: a mechanism probe, not a
promotion case — report the honest n with every number.

Research-only: no promotion, no ledger writes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from scipy.stats import norm
from sklearn.linear_model import Ridge
from wnba_totals_market_residual_walkforward import (
    MARKET_TRAIN_END,
    MARKET_VAL_END,
    MARKET_WINDOW_START,
    _load_snapshots_by_start,
    _main_line_snapshot,
    _side_market,
)
from wnba_totals_signals_walkforward import ALPHA, MIN_LEAGUE_GAMES, MIN_TEAM_GAMES

from model_prediction.config import PROJECT_ROOT
from model_prediction.features.base import FeatureStore
from model_prediction.features.wnba_boxscores import load_wnba_boxscore_files
from model_prediction.features.wnba_player_logs import load_wnba_player_boxscores
from model_prediction.total_score import build_total_score_rows

STRUCTURAL_IDX = -1  # structural_total is the challenger block's last feature
INCUMBENT_N_FEATURES = 12


def _fit(rows, targets) -> Ridge:
    model = Ridge(alpha=ALPHA, random_state=42)
    model.fit(np.asarray([list(r.features) for r in rows]), targets)
    return model


def _predict(model: Ridge, rows) -> list[float]:
    return model.predict(np.asarray([list(r.features) for r in rows])).tolist()


def _brier(probs: list[float], outcomes: list[int]) -> float:
    return float(np.mean([(p - y) ** 2 for p, y in zip(probs, outcomes, strict=True)]))


def _over_probs(preds: list[float], sigmas: list[float], lines: list[float]) -> list[float]:
    return [
        1.0 - float(norm.cdf((line - p) / sigma)) for p, sigma, line in zip(preds, sigmas, lines, strict=True)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--std-only", action="store_true")
    args = parser.parse_args()

    store = FeatureStore(PROJECT_ROOT / "data")
    games = store.load_games("wnba")
    if args.std_only:
        games = [g for g in games if g.season_type in ("regular-season", "post-season")]
    boxscores = load_wnba_boxscore_files(store.data_root / "availability" / "wnba" / "espn_boxscores")
    player_boxscores = load_wnba_player_boxscores(
        store.data_root / "availability" / "wnba" / "espn_boxscores"
    )
    rows = build_total_score_rows(
        games,
        minimum_team_games=MIN_TEAM_GAMES,
        minimum_league_games=MIN_LEAGUE_GAMES,
        wnba_boxscores=boxscores,
        wnba_player_boxscores=player_boxscores,
        include_player_impact=True,
    )
    in_window = [r for r in rows if r.date >= MARKET_WINDOW_START]
    snapshots = _load_snapshots_by_start(store.data_root, {r.date for r in in_window})
    games_by_id = {g.event_id: g for g in games}

    lined: list[tuple] = []
    for row in in_window:
        game = games_by_id.get(row.event_id)
        if game is None:
            continue
        snap = _main_line_snapshot(game, snapshots)
        if snap is None:
            continue
        market = _side_market(snap)
        if market is not None:
            lined.append((row, market.line))

    by_date: dict[str, list[tuple]] = {}
    for row, line in lined:
        by_date.setdefault(row.date, []).append((row, line))

    def _split_rows(lo: str | None, hi: str) -> list[tuple]:
        return [
            (r, l) for d in sorted(by_date) if (lo is None or d > lo) and d <= hi for (r, l) in by_date[d]
        ]

    train_j = _split_rows(None, MARKET_TRAIN_END)
    val_j = _split_rows(MARKET_TRAIN_END, MARKET_VAL_END)
    hold_j = _split_rows(MARKET_VAL_END, "9999-12-31")
    if not train_j or not hold_j:
        print(json.dumps({"status": "window_too_thin"}))
        return 0

    train_r = [r for r, _l in train_j]
    val_r, val_lines = [r for r, _l in val_j], [l for _r, l in val_j]
    hold_r, hold_lines = [r for r, _l in hold_j], [l for _r, l in hold_j]

    # Components (fitted on train only, OOF on val).
    ml_model = _fit(train_r, [r.actual_total for r in train_r])
    ml_train = _predict(ml_model, train_r)
    ml_val = _predict(ml_model, val_r)
    ml_hold = _predict(ml_model, hold_r)

    struct_val = [r.features[STRUCTURAL_IDX] for r in val_r]
    struct_hold = [r.features[STRUCTURAL_IDX] for r in hold_r]

    # Stack meta-model: ridge on the three component predictions. Fitted
    # on the components' out-of-fold validation predictions (components
    # themselves trained on train only); the holdout is untouched.
    meta_val = np.column_stack([struct_val, ml_val, val_lines])
    meta_hold = np.column_stack([struct_hold, ml_hold, hold_lines])
    meta_model = Ridge(alpha=ALPHA, random_state=42)
    meta_model.fit(meta_val, [r.actual_total for r in val_r])
    stack_hold = meta_model.predict(meta_hold).tolist()

    fixed_sigma = float(
        np.sqrt(np.mean([(p - r.actual_total) ** 2 for p, r in zip(ml_val, val_r, strict=True)]))
    )
    sigma_model = _fit(train_r, [abs(p - r.actual_total) for p, r in zip(ml_train, train_r, strict=True)])
    sigma_hold = [max(4.0, s) for s in _predict(sigma_model, hold_r)]

    outcomes = [1 if r.actual_total > l else 0 for r, l in hold_j]
    fixed_sigmas = [fixed_sigma] * len(hold_r)

    def _brier_of(preds: list[float], sigmas: list[float]) -> float:
        return _brier(_over_probs(preds, sigmas, hold_lines), outcomes)

    mae = {
        "structural": round(
            float(np.mean([abs(p - r.actual_total) for p, r in zip(struct_hold, hold_r, strict=True)])), 4
        ),
        "ridge": round(
            float(np.mean([abs(p - r.actual_total) for p, r in zip(ml_hold, hold_r, strict=True)])), 4
        ),
        "stack": round(
            float(np.mean([abs(p - r.actual_total) for p, r in zip(stack_hold, hold_r, strict=True)])), 4
        ),
        "market_line": round(
            float(np.mean([abs(l - r.actual_total) for l, r in zip(hold_lines, hold_r, strict=True)])), 4
        ),
    }
    brier = {
        "ridge_fixed_sigma": round(_brier_of(ml_hold, fixed_sigmas), 6),
        "ridge_per_game_sigma": round(_brier_of(ml_hold, sigma_hold), 6),
        "stack_fixed_sigma": round(_brier_of(stack_hold, fixed_sigmas), 6),
    }
    # The market's own O/U probability from the BBO (not a sigma model).
    market_brier_rows = []
    for r, l in hold_j:
        game = games_by_id.get(r.event_id)
        snap = _main_line_snapshot(game, snapshots)
        if snap is not None:
            sm = _side_market(snap)
            if sm is not None:
                market_brier_rows.append((sm.p_over, 1 if r.actual_total > l else 0))
    brier["market"] = (
        round(float(np.mean([(p - y) ** 2 for p, y in market_brier_rows])), 6) if market_brier_rows else None
    )

    report = {
        "status": "ok",
        "note": "thin snapshot window; mechanism probe only",
        "split": {"train": len(train_j), "validation": len(val_j), "holdout": len(hold_j)},
        "mae": mae,
        "brier": brier,
        "stack_meta_coefs": [round(float(c), 4) for c in meta_model.coef_],
        "fixed_sigma": round(fixed_sigma, 4),
        "per_game_sigma_range_holdout": [
            round(min(sigma_hold), 2),
            round(max(sigma_hold), 2),
        ],
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
