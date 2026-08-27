"""WNBA totals: structural challenger (possessions×PPP + player impact).

The challenger appends three features to the incumbent's 12
(``total_score.WNBA_CHALLENGER_FEATURE_NAMES``): lineup net advantage,
injury/absence impact gap, and the pure four-factors possessions×PPP
projection — all PIT from strictly-prior boxscore player logs. Both
models are judged against the market on lined holdout games with the
Phase B harness mechanics (market_eval economic battery + paired
MAE/Brier vs the no-vig Polymarket line), with the incumbent
reproduction gate re-run as the control.

Research-only: no promotion, no ledger writes.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from scipy.stats import norm
from sklearn.linear_model import Ridge
from wnba_totals_market_residual_walkforward import (  # committed Phase B helpers
    MARKET_TRAIN_END,
    MARKET_VAL_END,
    MARKET_WINDOW_START,
    REPRODUCTION_TARGET_MAE,
    REPRODUCTION_TOLERANCE,
    _load_snapshots_by_start,
    _main_line_snapshot,
    _side_market,
)
from wnba_totals_signals_walkforward import (  # matching conventions + split dates
    ALPHA,
    ARTIFACT_TRAIN_END,
    ARTIFACT_VALIDATION_END,
    MIN_LEAGUE_GAMES,
    MIN_TEAM_GAMES,
)

from model_prediction.config import PROJECT_ROOT
from model_prediction.features.base import FeatureStore
from model_prediction.features.wnba_boxscores import load_wnba_boxscore_files
from model_prediction.features.wnba_player_logs import load_wnba_player_boxscores
from model_prediction.market_eval import MarketEvalRow, decide_sides, market_relative_report
from model_prediction.total_score import build_total_score_rows
from model_prediction.validation import chronological_split


def _fit(rows, targets) -> Ridge:
    model = Ridge(alpha=ALPHA, random_state=42)
    model.fit(np.asarray([list(r.features) for r in rows]), targets)
    return model


def _predict(model: Ridge, rows) -> list[float]:
    return model.predict(np.asarray([list(r.features) for r in rows])).tolist()


def _gate(rows, label: str, target: float) -> dict:
    train, val, hold, _ = chronological_split(
        rows, train_end_date=ARTIFACT_TRAIN_END, validation_end_date=ARTIFACT_VALIDATION_END
    )
    model = _fit(train, [r.actual_total for r in train])
    sigma = float(
        np.sqrt(np.mean([(p - r.actual_total) ** 2 for p, r in zip(_predict(model, val), val, strict=True)]))
    )
    pred_hold = _predict(model, hold)
    mae = float(np.mean([abs(p - r.actual_total) for p, r in zip(pred_hold, hold, strict=True)]))
    drift = abs(mae - target)
    return {
        "label": label,
        "holdout_mae": round(mae, 4),
        "target": target,
        "drift": round(drift, 4),
        "gate": "PASS" if drift <= REPRODUCTION_TOLERANCE else "DRIFT",
        "holdout_rows": len(hold),
        "val_sigma": round(sigma, 4),
    }


def _vs_market(games, hold_rows, predictions, sigma, data_root: Path, min_edge: float, label: str) -> dict:
    """One model's market-relative report on lined holdout games."""
    snapshots = _load_snapshots_by_start(data_root, {r.date for r in hold_rows})
    game_by_id = {g.event_id: g for g in games}
    pred_by_id = dict(zip([r.event_id for r in hold_rows], predictions, strict=True))

    lined = []
    for row in hold_rows:
        game = game_by_id.get(row.event_id)
        if game is None or row.date < MARKET_WINDOW_START:
            continue
        snap = _main_line_snapshot(game, snapshots)
        if snap is None:
            continue
        market = _side_market(snap)
        if market is not None:
            lined.append((row, pred_by_id[row.event_id], market))

    outcomes = [1 if r.actual_total > m.line else 0 for r, _p, m in lined]
    mae_model = float(np.mean([abs(p - _r.actual_total) for _r, p, _m in lined]))
    mae_market = float(np.mean([abs(m.line - _r.actual_total) for _r, _p, m in lined]))
    gains = [abs(m.line - r.actual_total) - abs(p - r.actual_total) for (r, p, m) in lined]
    gen = random.Random(20260717)
    boot = sorted(sum(gains[gen.randrange(len(gains))] for _ in gains) / len(gains) for _ in range(2000))
    mae_ci = {
        "point_estimate": round(float(np.mean(gains)), 6),
        "ci_95_low": round(boot[50], 6),
        "ci_95_high": round(boot[-51], 6),
    }

    model_probs = [1.0 - float(norm.cdf((m.line - p) / sigma)) for _r, p, m in lined]
    market_probs = [m.p_over for _r, _p, m in lined]
    brier_model = float(np.mean([(p - o) ** 2 for p, o in zip(model_probs, outcomes, strict=True)]))
    brier_market = float(np.mean([(p - o) ** 2 for p, o in zip(market_probs, outcomes, strict=True)]))

    rows_by_event: dict[str, list[MarketEvalRow]] = {}
    for (r, _p, m), o, p_over in zip(lined, outcomes, model_probs, strict=True):
        rows_by_event[r.event_id] = [
            MarketEvalRow(
                event_id=r.event_id,
                decision_utc=r.date,
                market_type="total",
                line=m.line,
                model_prob=p_over,
                market_prob=m.p_over,
                bet_price=m.ask_over,
                outcome=o,
            ),
            MarketEvalRow(
                event_id=r.event_id,
                decision_utc=r.date,
                market_type="total",
                line=m.line,
                model_prob=1.0 - p_over,
                market_prob=1.0 - m.p_over,
                bet_price=m.ask_under,
                outcome=1 - o,
            ),
        ]
    econ = market_relative_report(decide_sides(rows_by_event, min_edge=min_edge))

    return {
        "label": label,
        "lined_games": len(lined),
        "mae": {
            "model": round(mae_model, 4),
            "market_line": round(mae_market, 4),
            "model_minus_market_paired_95ci": mae_ci,
        },
        "brier": {
            "model": round(brier_model, 6),
            "market": round(brier_market, 6),
            "observed_over_rate": round(float(np.mean(outcomes)), 4),
        },
        "economic": econ,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--std-only", action="store_true")
    parser.add_argument("--min-edge", type=float, default=0.02)
    args = parser.parse_args()

    store = FeatureStore(PROJECT_ROOT / "data")
    all_games = store.load_games("wnba")
    boxscores = load_wnba_boxscore_files(store.data_root / "availability" / "wnba" / "espn_boxscores")
    player_boxscores = load_wnba_player_boxscores(
        store.data_root / "availability" / "wnba" / "espn_boxscores"
    )
    games = all_games
    if args.std_only:
        games = [g for g in all_games if g.season_type in ("regular-season", "post-season")]

    incumbent_rows = build_total_score_rows(
        games,
        minimum_team_games=MIN_TEAM_GAMES,
        minimum_league_games=MIN_LEAGUE_GAMES,
        wnba_boxscores=boxscores,
    )
    challenger_rows = build_total_score_rows(
        games,
        minimum_team_games=MIN_TEAM_GAMES,
        minimum_league_games=MIN_LEAGUE_GAMES,
        wnba_boxscores=boxscores,
        wnba_player_boxscores=player_boxscores,
        include_player_impact=True,
    )
    # Feature vectors differ only in the appended challenger block.
    assert len(challenger_rows[0].features) == len(incumbent_rows[0].features) + 3

    train, val, hold, _ = chronological_split(
        incumbent_rows, train_end_date=ARTIFACT_TRAIN_END, validation_end_date=ARTIFACT_VALIDATION_END
    )
    c_rows_by_id = {r.event_id: r for r in challenger_rows}
    hold_c = [c_rows_by_id[r.event_id] for r in hold]

    inc_model = _fit(train, [r.actual_total for r in train])
    ch_model = _fit([c_rows_by_id[r.event_id] for r in train], [r.actual_total for r in train])
    inc_sigma = float(
        np.sqrt(
            np.mean([(p - r.actual_total) ** 2 for p, r in zip(_predict(inc_model, val), val, strict=True)])
        )
    )
    ch_sigma = float(
        np.sqrt(
            np.mean(
                [
                    (p - r.actual_total) ** 2
                    for p, r in zip(
                        _predict(ch_model, [c_rows_by_id[r.event_id] for r in val]), val, strict=True
                    )
                ]
            )
        )
    )
    inc_preds = _predict(inc_model, hold)
    ch_preds = _predict(ch_model, hold_c)

    # Paired challenger-vs-incumbent MAE on the full holdout.
    gains = [
        abs(i - r.actual_total) - abs(c - r.actual_total)
        for i, c, r in zip(inc_preds, ch_preds, hold, strict=True)
    ]
    gen = random.Random(20260717)
    boot = sorted(sum(gains[gen.randrange(len(gains))] for _ in gains) / len(gains) for _ in range(2000))
    paired = {
        "point_estimate": round(float(np.mean(gains)), 6),
        "ci_95_low": round(boot[50], 6),
        "ci_95_high": round(boot[-51], 6),
    }

    report = {
        "games_used": len(games),
        "std_only": bool(args.std_only),
        "player_boxscore_files": len(player_boxscores),
        "reproduction_gate": _gate(incumbent_rows, "incumbent", REPRODUCTION_TARGET_MAE),
        "challenger_full_holdout_mae": round(
            float(np.mean([abs(p - r.actual_total) for p, r in zip(ch_preds, hold_c, strict=True)])), 4
        ),
        "challenger_minus_incumbent_mae_paired_95ci": paired,
        "challenger_learnability_note": (
            "boxscore captures start ~2026-07, so under the artifact split "
            "(train <= 2025-08-15) the challenger features are constant in "
            "train and the ridge assigns them zero coefficients — the "
            "artifact-split challenger is unlearnable by construction; see "
            "market_window_probe for the honest data-depth-limited test."
        ),
        "model_vs_market": [
            _vs_market(games, hold, inc_preds, inc_sigma, store.data_root, args.min_edge, "incumbent"),
            _vs_market(
                games, hold_c, ch_preds, ch_sigma, store.data_root, args.min_edge, "structural_challenger"
            ),
        ],
        "market_window_probe": _window_probe(incumbent_rows, challenger_rows, store.data_root),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _window_probe(incumbent_rows, challenger_rows, data_root: Path) -> dict:
    """Fit both models inside the snapshot window (2026-07-17..) where the
    challenger features actually have variance, and compare holdout MAE
    against each other and the market line. 45/22/22 rows — a thin-sample
    direction probe, not a promotion case."""
    inc_in = [r for r in incumbent_rows if r.date >= MARKET_WINDOW_START]
    ch_by_id = {r.event_id: r for r in challenger_rows}

    by_date: dict[str, list] = {}
    for r in inc_in:
        by_date.setdefault(r.date, []).append(r)
    train = [r for d in sorted(by_date) if d <= MARKET_TRAIN_END for r in by_date[d]]
    val = [r for d in sorted(by_date) if MARKET_TRAIN_END < d <= MARKET_VAL_END for r in by_date[d]]
    hold = [r for d in sorted(by_date) if d > MARKET_VAL_END for r in by_date[d]]
    if not train or not hold:
        return {"status": "window_too_thin"}

    inc_model = _fit(train, [r.actual_total for r in train])
    ch_model = _fit([ch_by_id[r.event_id] for r in train], [r.actual_total for r in train])
    inc_hold_preds = _predict(inc_model, hold)
    ch_hold_preds = _predict(ch_model, [ch_by_id[r.event_id] for r in hold])

    snapshots = _load_snapshots_by_start(data_root, {r.date for r in hold})

    # Market line per holdout row via the same matcher conventions.
    store = FeatureStore(PROJECT_ROOT / "data")
    games = {g.event_id: g for g in store.load_games("wnba")}
    lined = []
    for row in hold:
        game = games.get(row.event_id)
        if game is None:
            continue
        snap = _main_line_snapshot(game, snapshots)
        if snap is not None:
            market = _side_market(snap)
            if market is not None:
                lined.append((row, market.line))

    mae_inc = float(np.mean([abs(p - r.actual_total) for p, r in zip(inc_hold_preds, hold, strict=True)]))
    mae_ch = float(np.mean([abs(p - r.actual_total) for p, r in zip(ch_hold_preds, hold, strict=True)]))
    mae_market = float(np.mean([abs(line - r.actual_total) for r, line in lined])) if lined else None
    return {
        "status": "ok",
        "note": "thin snapshot window; direction probe only",
        "train_rows": len(train),
        "validation_rows": len(val),
        "holdout_rows": len(hold),
        "lined_holdout_rows": len(lined),
        "mae": {
            "incumbent": round(mae_inc, 4),
            "structural_challenger": round(mae_ch, 4),
            "market_line": round(mae_market, 4) if mae_market is not None else None,
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
