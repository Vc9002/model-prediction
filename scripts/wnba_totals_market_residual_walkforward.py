"""WNBA totals: the market is the benchmark, not the afterthought.

Three sections, in the plan's priority order:

1. **Incumbent reproduction gate** — rebuild today's std-only holdout MAE
   (~19.9, scripts/wnba_totals_signals_walkforward.py 2026-08-26) with the
   incumbent's exact configuration. If this drifts, everything below is void.
2. **Model vs market on lined holdout games** — the plan's central question:
   does the incumbent add information *beyond the market*? Delta Brier /
   logloss vs the no-vig Polymarket probability, market-line MAE benchmark,
   and the full economic battery from ``market_eval.market_relative_report``
   (CLV, ROI at executable asks, profit factor, drawdown, bootstrap CI).
3. **Market-residual probe** — a ridge trained on ``actual - market_line``
   over the snapshot window (2026-07-17..2026-08-26, the only window with
   market lines), compared against the market line alone. The window is
   thin (~6 weeks), so this is a direction probe, not a promotion case.

Split discipline unchanged: PIT rows from ``total_score.build_total_score_rows``,
ridge fitted on train only, residual sigma on validation only, locked
holdout untouched until the final report. Research-only: no promotion, no
ledger writes.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from scipy.stats import norm
from sklearn.linear_model import Ridge

# Single source of truth for the matching conventions and the slug-team
# map: today's signals harness (same research family, same data).
from wnba_totals_signals_walkforward import (
    ALPHA,
    ARTIFACT_TRAIN_END,
    ARTIFACT_VALIDATION_END,
    MIN_LEAGUE_GAMES,
    MIN_TEAM_GAMES,
    WNBA_SLUG_TEAMS,
    _load_snapshots_by_start,
    _main_line_for_game,
)

from model_prediction.config import PROJECT_ROOT
from model_prediction.features.base import FeatureStore
from model_prediction.features.wnba_boxscores import load_wnba_boxscore_files
from model_prediction.market_eval import MarketEvalRow, decide_sides, market_relative_report, no_vig
from model_prediction.roadmap_challenger import _cluster_bootstrap_brier_delta
from model_prediction.total_score import build_total_score_rows
from model_prediction.validation import chronological_split

# Snapshot window with real market lines (census: wnba odds 2026-07-17..).
MARKET_WINDOW_START = "2026-07-17"
MARKET_TRAIN_END = "2026-08-08"
MARKET_VAL_END = "2026-08-16"

# The std-only incumbent holdout MAE this harness must reproduce (obs 76).
REPRODUCTION_TARGET_MAE = 19.9
REPRODUCTION_TOLERANCE = 0.5


@dataclass(frozen=True)
class SideMarket:
    """Market probability and executable price for one total-market side."""

    p_over: float
    ask_over: float
    ask_under: float
    line: float


def _side_market(snap: dict) -> SideMarket | None:
    """No-vig over probability + executable asks from one snapshot row.

    Polymarket US totals rows carry ``long.description``/``short.description``
    as "Over"/"Under"; the long side is not guaranteed to be Over, so the
    description decides which midpoint is which.
    """
    try:
        line = float(snap["line"])
        long_side = snap["long"]
        short_side = snap["short"]
        long_mid = float(long_side["midpoint"])
        short_mid = float(short_side["midpoint"])
        ask_long = float(long_side["ask"])
        ask_short = float(short_side["ask"])
    except (KeyError, TypeError, ValueError):
        return None
    p_long = no_vig(long_mid, short_mid)
    if str(long_side.get("description") or "").casefold() == "over":
        return SideMarket(p_over=p_long, ask_over=ask_long, ask_under=ask_short, line=line)
    if str(short_side.get("description") or "").casefold() == "over":
        return SideMarket(p_over=1.0 - p_long, ask_over=ask_short, ask_under=ask_long, line=line)
    return None


def _main_line_snapshot(game, snapshots_by_start: dict[str, list[dict]]) -> dict | None:
    """The main-line snapshot row for a game (same conventions as
    ``_main_line_for_game``: same start minute, both team abbreviations in
    the slug, main line = long ask closest to 0.50)."""
    start = game.event_start_utc.replace("Z", "").replace("+00:00", "")[:16]
    candidates = snapshots_by_start.get(start, [])
    team_abbrevs = {
        abbr for abbr, name in WNBA_SLUG_TEAMS.items() if name in (game.home_team, game.away_team)
    }
    if not team_abbrevs:
        return None
    matched = []
    for row in candidates:
        slug = str(row.get("market_slug") or "")
        parts = set(slug.split("-"))
        if len(team_abbrevs & parts) < 2:
            continue
        ask = row.get("long", {}).get("ask")
        if ask is None:
            continue
        matched.append((abs(float(ask) - 0.5), row))
    if not matched:
        return None
    return min(matched, key=lambda item: item[0])[1]


def _over_probs(predictions: list[float], sigma: float, lines: list[float]) -> list[float]:
    return [1.0 - float(norm.cdf((line - p) / sigma)) for p, line in zip(predictions, lines, strict=True)]


def _brier(probs: list[float], outcomes: list[int]) -> float:
    return float(np.mean([(p - y) ** 2 for p, y in zip(probs, outcomes, strict=True)]))


def _fit_ridge(rows, targets: list[float]) -> Ridge:
    model = Ridge(alpha=ALPHA, random_state=42)
    model.fit(np.asarray([list(r.features) for r in rows]), targets)
    return model


def _predict(model: Ridge, rows) -> list[float]:
    return model.predict(np.asarray([list(r.features) for r in rows])).tolist()


def _incumbent_gate(games, boxscores) -> dict:
    """Section 1: reproduce the incumbent's shipped std-only holdout MAE."""
    rows = build_total_score_rows(
        games,
        minimum_team_games=MIN_TEAM_GAMES,
        minimum_league_games=MIN_LEAGUE_GAMES,
        wnba_boxscores=boxscores,
    )
    train, val, hold, _ = chronological_split(
        rows, train_end_date=ARTIFACT_TRAIN_END, validation_end_date=ARTIFACT_VALIDATION_END
    )
    model = _fit_ridge(train, [r.actual_total for r in train])
    sigma = float(
        np.sqrt(np.mean([(p - r.actual_total) ** 2 for p, r in zip(_predict(model, val), val, strict=True)]))
    )
    pred_hold = _predict(model, hold)
    mae = float(np.mean([abs(p - r.actual_total) for p, r in zip(pred_hold, hold, strict=True)]))
    drift = abs(mae - REPRODUCTION_TARGET_MAE)
    return {
        "holdout_mae": round(mae, 4),
        "target": REPRODUCTION_TARGET_MAE,
        "drift": round(drift, 4),
        "gate": "PASS" if drift <= REPRODUCTION_TOLERANCE else "DRIFT — comparison void",
        "holdout_rows": len(hold),
        "val_sigma": round(sigma, 4),
    }


def _model_vs_market(games, boxscores, data_root: Path, min_edge: float) -> dict:
    """Section 2: incumbent vs the market on lined holdout games."""
    rows = build_total_score_rows(
        games,
        minimum_team_games=MIN_TEAM_GAMES,
        minimum_league_games=MIN_LEAGUE_GAMES,
        wnba_boxscores=boxscores,
    )
    train, val, hold, _ = chronological_split(
        rows, train_end_date=ARTIFACT_TRAIN_END, validation_end_date=ARTIFACT_VALIDATION_END
    )
    model = _fit_ridge(train, [r.actual_total for r in train])
    sigma = float(
        np.sqrt(np.mean([(p - r.actual_total) ** 2 for p, r in zip(_predict(model, val), val, strict=True)]))
    )
    pred_hold = _predict(model, hold)

    snapshots = _load_snapshots_by_start(data_root, {r.date for r in hold})
    game_by_id = {g.event_id: g for g in games}

    lined: list[tuple] = []  # (row, prediction, SideMarket)
    for row, pred in zip(hold, pred_hold, strict=True):
        game = game_by_id.get(row.event_id)
        if game is None or row.date < MARKET_WINDOW_START:
            continue
        snap = _main_line_snapshot(game, snapshots)
        if snap is None:
            continue
        market = _side_market(snap)
        if market is not None:
            lined.append((row, pred, market))

    outcomes = [1 if r.actual_total > m.line else 0 for r, _p, m in lined]
    lines = [m.line for _r, _p, m in lined]
    model_probs = _over_probs([p for _r, p, _m in lined], sigma, lines)
    market_probs = [m.p_over for _r, _p, m in lined]

    mae_model = float(np.mean([abs(p - r.actual_total) for (r, p, _m) in lined]))
    mae_market = float(np.mean([abs(m.line - r.actual_total) for (r, _p, m) in lined]))

    # Paired MAE delta (model minus market, positive = model better) with
    # the same bootstrap mechanics as _paired_mae_delta_interval.
    gains = [abs(m.line - r.actual_total) - abs(p - r.actual_total) for (r, p, m) in lined]
    gen = random.Random(20260717)
    boot = sorted(sum(gains[gen.randrange(len(gains))] for _ in gains) / len(gains) for _ in range(2000))
    mae_ci = {
        "point_estimate": round(float(np.mean(gains)), 6),
        "ci_95_low": round(boot[50], 6),
        "ci_95_high": round(boot[-51], 6),
    }

    brier_model = _brier(model_probs, outcomes)
    brier_market = _brier(market_probs, outcomes)

    @dataclass
    class _BrierRow:
        date: str
        outcome: int

    brier_delta_ci = _cluster_bootstrap_brier_delta(
        market_probs,
        model_probs,
        [_BrierRow(date=r.date, outcome=o) for (r, _p, _m), o in zip(lined, outcomes, strict=True)],
        seed=20260826,
    )

    # Economic battery: bet the model's side where edge ≥ min_edge.
    rows_by_event: dict[str, list[MarketEvalRow]] = {}
    for (r, pred, m), o in zip(lined, outcomes, strict=True):
        p_over_model = 1.0 - float(norm.cdf((m.line - pred) / sigma))
        rows_by_event[r.event_id] = [
            MarketEvalRow(
                event_id=r.event_id,
                decision_utc=r.date,
                market_type="total",
                line=m.line,
                model_prob=p_over_model,
                market_prob=m.p_over,
                bet_price=m.ask_over,
                outcome=o,
            ),
            MarketEvalRow(
                event_id=r.event_id,
                decision_utc=r.date,
                market_type="total",
                line=m.line,
                model_prob=1.0 - p_over_model,
                market_prob=1.0 - m.p_over,
                bet_price=m.ask_under,
                outcome=1 - o,
            ),
        ]
    bets = decide_sides(rows_by_event, min_edge=min_edge)
    econ = market_relative_report(bets)

    return {
        "lined_games": len(lined),
        "mae": {
            "model": round(mae_model, 4),
            "market_line": round(mae_market, 4),
            "model_minus_market_paired_95ci": mae_ci,
        },
        "brier": {
            "model": round(brier_model, 6),
            "market": round(brier_market, 6),
            "model_minus_market_95ci": brier_delta_ci,
            "observed_over_rate": round(float(np.mean(outcomes)), 4),
        },
        "economic": econ,
        "min_edge": min_edge,
    }


def _market_residual_probe(games, boxscores, data_root: Path) -> dict:
    """Section 3: residual = actual - market line, fitted inside the
    snapshot window. Thin-sample direction probe, not a promotion case."""
    rows = build_total_score_rows(
        games,
        minimum_team_games=MIN_TEAM_GAMES,
        minimum_league_games=MIN_LEAGUE_GAMES,
        wnba_boxscores=boxscores,
    )
    in_window = [r for r in rows if r.date >= MARKET_WINDOW_START]
    snapshots = _load_snapshots_by_start(data_root, {r.date for r in in_window})
    game_by_id = {g.event_id: g for g in games}

    joined: list[tuple] = []
    for row in in_window:
        game = game_by_id.get(row.event_id)
        if game is None:
            continue
        match = _main_line_for_game(game, snapshots)
        if match is not None:
            joined.append((row, match.line))
    if not joined:
        return {"status": "no_lined_rows", "window": MARKET_WINDOW_START}

    by_date: dict[str, list[tuple]] = {}
    for row, line in joined:
        by_date.setdefault(row.date, []).append((row, line))

    def _split_rows(max_date: str) -> list[tuple]:
        return [(r, l) for d in sorted(by_date) if d <= max_date for (r, l) in by_date[d]]

    train_j = _split_rows(MARKET_TRAIN_END)
    val_j = [t for d in sorted(by_date) if MARKET_TRAIN_END < d <= MARKET_VAL_END for t in by_date[d]]
    hold_j = [t for d in sorted(by_date) if d > MARKET_VAL_END for t in by_date[d]]
    if not train_j or not hold_j:
        return {"status": "window_too_thin", "train": len(train_j), "holdout": len(hold_j)}

    resid_model = _fit_ridge([r for r, _l in train_j], [r.actual_total - l for r, l in train_j])
    resid_val_preds = _predict(resid_model, [r for r, _l in val_j])
    resid_sigma = float(
        np.sqrt(
            np.mean(
                [(p - (r.actual_total - l)) ** 2 for p, (r, l) in zip(resid_val_preds, val_j, strict=True)]
            )
        )
    )

    hold_rows = [r for r, _l in hold_j]
    resid_preds = _predict(resid_model, hold_rows)
    market_preds = [l for _r, l in hold_j]
    resid_total_preds = [l + p for p, l in zip(resid_preds, market_preds, strict=True)]

    mae_market = float(np.mean([abs(l - r.actual_total) for r, l in hold_j]))
    mae_resid = float(
        np.mean([abs(p - r.actual_total) for p, r in zip(resid_total_preds, hold_rows, strict=True)])
    )
    outcomes = [1 if r.actual_total > l else 0 for r, l in hold_j]
    brier_market = _brier(_over_probs(market_preds, resid_sigma, market_preds), outcomes)
    brier_resid = _brier(_over_probs(resid_total_preds, resid_sigma, market_preds), outcomes)

    return {
        "status": "ok",
        "window": f"{MARKET_WINDOW_START}..",
        "train_rows": len(train_j),
        "validation_rows": len(val_j),
        "holdout_rows": len(hold_j),
        "mae": {"market_line": round(mae_market, 4), "market_plus_residual": round(mae_resid, 4)},
        "brier": {"market": round(brier_market, 6), "market_plus_residual": round(brier_resid, 6)},
        "note": "thin snapshot window (6 weeks); direction probe only",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--std-only", action="store_true", help="regular/post-season games only")
    parser.add_argument(
        "--min-edge", type=float, default=0.02, help="model edge required to bet (prob space)"
    )
    args = parser.parse_args()

    store = FeatureStore(PROJECT_ROOT / "data")
    all_games = store.load_games("wnba")
    boxscores = load_wnba_boxscore_files(store.data_root / "availability" / "wnba" / "espn_boxscores")
    games = all_games
    if args.std_only:
        games = [g for g in all_games if g.season_type in ("regular-season", "post-season")]

    report = {
        "games_used": len(games),
        "games_total_on_disk": len(all_games),
        "std_only": bool(args.std_only),
        "incumbent_reproduction_gate": _incumbent_gate(games, boxscores),
        "model_vs_market": _model_vs_market(games, boxscores, store.data_root, args.min_edge),
        "market_residual_probe": _market_residual_probe(games, boxscores, store.data_root),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
