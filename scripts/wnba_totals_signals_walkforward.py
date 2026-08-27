"""WNBA totals walk-forward: before (hardcoded constants) vs after (real PIT signals).

Reuses the sanctioned splitter ``validation.chronological_split`` with the
date boundaries recorded in the ``wnba-total-margin-v1`` artifact's training
block (train end 2025-08-15, validation end 2026-05-27). ``build_walk_forward_rows``
cannot produce totals rows (its ValidationRow carries moneyline features
only), so totals rows come from the module's own PIT builder,
``total_score.build_total_score_rows``; the split itself is the harness's.

Ridge fit on train only; the residual sigma used for the over/under
probability transform is estimated on validation only; the locked holdout
is untouched until the final report. MAE on the full holdout, Brier +
calibration on the holdout subset with a settled total line (Polymarket
snapshots, timestamp-valid, matched like the slate: same start minute, both
team abbreviations in the market slug, main line = ask closest to 0.50).

``--std-only`` re-runs the whole pipeline on regular/post-season games only
(All-Star and preseason games -- which no odds market covers -- carry wild
totals like the 282-point 2025 All-Star game and distort the MAE table).

Research-only: no promotion, no ledger writes, no commits.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from scipy.stats import norm
from sklearn.linear_model import Ridge

from model_prediction.config import PROJECT_ROOT
from model_prediction.features.base import FeatureStore
from model_prediction.features.wnba_boxscores import load_wnba_boxscore_files
from model_prediction.roadmap_challenger import _cluster_bootstrap_brier_delta
from model_prediction.total_score import _paired_mae_gain_interval, build_total_score_rows
from model_prediction.validation import chronological_split

ARTIFACT_TRAIN_END = "2025-08-15"
ARTIFACT_VALIDATION_END = "2026-05-27"
MIN_TEAM_GAMES = 8
MIN_LEAGUE_GAMES = 40
ALPHA = 1.0
MAE_BOOTSTRAP_SAMPLES = 2_000

# Slug team abbreviations observed in data/odds/wnba/*/polymarket_snapshots.jsonl
WNBA_SLUG_TEAMS = {
    "atl": "Atlanta Dream",
    "chi": "Chicago Sky",
    "conn": "Connecticut Sun",
    "dal": "Dallas Wings",
    "gsv": "Golden State Valkyries",
    "ind": "Indiana Fever",
    "la": "Los Angeles Sparks",
    "lv": "Las Vegas Aces",
    "min": "Minnesota Lynx",
    "ny": "New York Liberty",
    "phx": "Phoenix Mercury",
    "por": "Portland Fire",
    "sea": "Seattle Storm",
    "tor": "Toronto Tempo",
    "wsh": "Washington Mystics",
}


@dataclass
class LineMatch:
    event_id: str
    date: str
    line: float


def _ridge(rows) -> Ridge:
    model = Ridge(alpha=ALPHA, random_state=42)
    X = np.asarray([list(r.features) for r in rows])
    model.fit(X, [r.actual_total for r in rows])
    return model


def _mae(predictions: list[float], rows) -> float:
    return mean(abs(p - r.actual_total) for p, r in zip(predictions, rows, strict=True))


def _paired_mae_delta_interval(
    new_predictions: list[float],
    old_predictions: list[float],
    rows,
    samples: int = MAE_BOOTSTRAP_SAMPLES,
) -> dict[str, float]:
    """Paired bootstrap on per-row MAE deltas (new minus old), same mechanics
    as total_score._paired_mae_gain_interval (seed 20260717)."""
    gains = [
        abs(old - r.actual_total) - abs(new - r.actual_total)
        for old, new, r in zip(old_predictions, new_predictions, rows, strict=True)
    ]
    gen = random.Random(20260717)
    boot = sorted(mean(gains[gen.randrange(len(gains))] for _ in gains) for _ in range(samples))
    return {
        "point_estimate": round(mean(gains), 6),
        "ci_95_low": round(boot[int(samples * 0.025)], 6),
        "ci_95_high": round(boot[int(samples * 0.975)], 6),
        "resamples": samples,
    }


def _main_line_for_game(game, snapshots_by_start: dict[str, list[dict]]) -> LineMatch | None:
    """Match a game to its main total line (slate conventions)."""
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
        try:
            line = float(row["line"])
        except (KeyError, TypeError, ValueError):
            continue
        ask = row.get("long", {}).get("ask")
        if ask is None:
            continue
        matched.append((line, float(ask)))
    if not matched:
        return None
    line, _ask = min(matched, key=lambda item: abs(item[1] - 0.5))
    return LineMatch(event_id=game.event_id, date=game.start.date().isoformat(), line=line)


def _load_snapshots_by_start(data_root: Path, dates: set[str]) -> dict[str, list[dict]]:
    """All timestamp-valid total-market snapshots keyed by start minute."""
    by_start: dict[str, list[dict]] = {}
    for day in sorted(dates):
        path = data_root / "odds" / "wnba" / day / "polymarket_snapshots.jsonl"
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("market_type") not in ("total", "over_under"):
                continue
            if row.get("timestamp_valid") is not True:
                continue
            key = str(row.get("event_start_utc") or "").replace("Z", "")[:16]
            by_start.setdefault(key, []).append(row)
    return by_start


def _walkforward(games, boxscores, data_root: Path, label: str) -> dict[str, object]:
    """One before/after walk-forward pass; returns the report block."""

    @dataclass
    class _Row:
        date: str
        outcome: int

    rows_after = build_total_score_rows(
        games,
        minimum_team_games=MIN_TEAM_GAMES,
        minimum_league_games=MIN_LEAGUE_GAMES,
        wnba_boxscores=boxscores,
    )
    rows_before = build_total_score_rows(
        games,
        minimum_team_games=MIN_TEAM_GAMES,
        minimum_league_games=MIN_LEAGUE_GAMES,
        wnba_legacy_signals=True,
    )
    after_ids = [r.event_id for r in rows_after]
    before_ids = [r.event_id for r in rows_before]
    assert after_ids == before_ids, "before/after row sets diverged"

    train_a, val_a, hold_a, split = chronological_split(
        rows_after, train_end_date=ARTIFACT_TRAIN_END, validation_end_date=ARTIFACT_VALIDATION_END
    )
    train_b, val_b, hold_b, _ = chronological_split(
        rows_before, train_end_date=ARTIFACT_TRAIN_END, validation_end_date=ARTIFACT_VALIDATION_END
    )
    assert [r.event_id for r in train_a] == [r.event_id for r in train_b]
    assert [r.event_id for r in val_a] == [r.event_id for r in val_b]
    assert [r.event_id for r in hold_a] == [r.event_id for r in hold_b]

    model_a = _ridge(train_a)
    model_b = _ridge(train_b)
    pred_a_val = model_a.predict(np.asarray([list(r.features) for r in val_a])).tolist()
    pred_b_val = model_b.predict(np.asarray([list(r.features) for r in val_b])).tolist()
    pred_a = model_a.predict(np.asarray([list(r.features) for r in hold_a])).tolist()
    pred_b = model_b.predict(np.asarray([list(r.features) for r in hold_b])).tolist()

    # Residual sigma per model, estimated on validation only (never holdout).
    sigma_a = float(
        np.sqrt(np.mean([(p - r.actual_total) ** 2 for p, r in zip(pred_a_val, val_a, strict=True)]))
    )
    sigma_b = float(
        np.sqrt(np.mean([(p - r.actual_total) ** 2 for p, r in zip(pred_b_val, val_b, strict=True)]))
    )

    baseline_mae = _mae([r.baseline_total for r in hold_a], hold_a)
    mae_before = _mae(pred_b, hold_b)
    mae_after = _mae(pred_a, hold_a)
    mae_delta_ci = _paired_mae_delta_interval(pred_a, pred_b, hold_a)
    baseline_gain_low, baseline_gain_high = _paired_mae_gain_interval(pred_a, hold_a)

    # ── Over/under evaluation on lined holdout games ─────────────────────────
    snapshots = _load_snapshots_by_start(data_root, {r.date for r in hold_a})
    game_by_id = {g.event_id: g for g in games}
    lines: list[LineMatch] = []
    for row in hold_a:
        game = game_by_id.get(row.event_id)
        if game is None:
            continue
        match = _main_line_for_game(game, snapshots)
        if match is not None:
            lines.append(match)

    pred_a_by_id = dict(zip([r.event_id for r in hold_a], pred_a, strict=True))
    pred_b_by_id = dict(zip([r.event_id for r in hold_b], pred_b, strict=True))
    brier_rows: list[_Row] = []
    probs_a: list[float] = []
    probs_b: list[float] = []
    for match in lines:
        row = next(r for r in hold_a if r.event_id == match.event_id)
        outcome = 1 if row.actual_total > match.line else 0
        p_a = 1.0 - float(norm.cdf((match.line - pred_a_by_id[match.event_id]) / sigma_a))
        p_b = 1.0 - float(norm.cdf((match.line - pred_b_by_id[match.event_id]) / sigma_b))
        brier_rows.append(_Row(date=row.date, outcome=outcome))
        probs_a.append(p_a)
        probs_b.append(p_b)

    brier_a = mean((p - r.outcome) ** 2 for p, r in zip(probs_a, brier_rows, strict=True))
    brier_b = mean((p - r.outcome) ** 2 for p, r in zip(probs_b, brier_rows, strict=True))
    brier_ci = _cluster_bootstrap_brier_delta(probs_b, probs_a, brier_rows, seed=20260826)

    # ── Calibration of the after-model over probabilities ────────────────────
    bins = np.linspace(0.0, 1.0, 6)
    calibration = []
    ece = 0.0
    for low, high in pairwise(bins):
        if high < 1.0:
            idxs = [i for i, p in enumerate(probs_a) if low <= p < high]
        else:
            idxs = [i for i, p in enumerate(probs_a) if low <= p <= high]
        if not idxs:
            continue
        predicted = mean(probs_a[i] for i in idxs)
        observed = mean(brier_rows[i].outcome for i in idxs)
        weight = len(idxs) / len(probs_a)
        ece += weight * abs(predicted - observed)
        calibration.append(
            {
                "bin": f"{low:.2f}-{high:.2f}",
                "n": len(idxs),
                "mean_predicted": round(predicted, 4),
                "observed_over_rate": round(observed, 4),
            }
        )

    # Shrunk pace rounds to exactly 79.5 (league prior) only when a team has
    # zero prior boxscore logs, so the fraction differing marks real coverage.
    pace_available = sum(1 for r in hold_a if abs(r.features[-1] - 79.5) > 0.001)

    return {
        "label": label,
        "split": {
            "method": split["method"],
            "train_end_date": ARTIFACT_TRAIN_END,
            "validation_end_date": ARTIFACT_VALIDATION_END,
            "train_rows": len(train_a),
            "validation_rows": len(val_a),
            "holdout_rows": len(hold_a),
            "holdout_start": hold_a[0].date,
            "holdout_end": hold_a[-1].date,
        },
        "holdout_mae": {
            "baseline_league_mean": round(baseline_mae, 6),
            "before_hardcoded_constants": round(mae_before, 6),
            "after_real_signals": round(mae_after, 6),
            "after_minus_before": round(mae_after - mae_before, 6),
            "after_minus_before_paired_bootstrap_95ci": mae_delta_ci,
            "after_vs_baseline_gain_95ci": {"ci_95_low": baseline_gain_low, "ci_95_high": baseline_gain_high},
        },
        "diagnostics": {
            "holdout_actual_mean": round(mean(r.actual_total for r in hold_a), 2),
            "holdout_actual_std": round(float(np.std([r.actual_total for r in hold_a], ddof=1)), 2),
            "holdout_pred_mean_after": round(mean(pred_a), 2),
            "train_actual_mean": round(mean(r.actual_total for r in train_a), 2),
            "validation_actual_mean": round(mean(r.actual_total for r in val_a), 2),
            "after_coef_level_net": round(
                float(model_a.coef_[0] + model_a.coef_[-3]), 4
            ),  # league_total_mean + last_10_total_avg (collinear level pair)
        },
        "over_under_holdout": {
            "lined_games": len(lines),
            "residual_sigma_before": round(sigma_b, 4),
            "residual_sigma_after": round(sigma_a, 4),
            "brier_before": round(brier_b, 6),
            "brier_after": round(brier_a, 6),
            "after_minus_before_brier_95ci": {
                "point_estimate": brier_ci["point_estimate"],
                "ci_95_low": brier_ci["ci_95_low"],
                "ci_95_high": brier_ci["ci_95_high"],
                "dates": brier_ci["dates"],
            },
            "observed_over_rate": round(mean(r.outcome for r in brier_rows), 4),
            "mean_line": round(mean(m.line for m in lines), 2),
            "mean_prediction_after": round(mean(probs_a), 4),
            "mean_prediction_before": round(mean(probs_b), 4),
        },
        "calibration_after": {"ece": round(ece, 4), "bins": calibration},
        "signal_coverage": {
            "pace_real_on_holdout_fraction": round(pace_available / len(hold_a), 4),
            "boxscore_files": len(boxscores),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--std-only", action="store_true", help="regular/post-season games only")
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
        "passes": [_walkforward(games, boxscores, store.data_root, "full_holdout")],
    }
    if args.std_only:
        report["passes"] = [_walkforward(games, boxscores, store.data_root, "std_only_holdout")]
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
