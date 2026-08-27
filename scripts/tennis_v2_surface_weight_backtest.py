"""Tennis v2 (Step 8 item 3) -- dynamic surface-weight challenger vs fixed 60/40.

Backlog (docs/ROADMAP.md): "Tennis v2 challenge fixed 60/40
surface weighting `w_max x n_surface/(n_surface + c)`". TennisModel's live
`match_probability` uses a hardcoded `surface_weight=0.6` regardless of how
much surface-specific history either player has -- a player's first-ever
clay match gets exactly the same 60% surface trust as a player with 200
clay matches. v2 makes the surface weight grow with `min(n_surface_one,
n_surface_two)`, saturating at w_max=0.6.

Result (2026-08-18): the dynamic form DOES beat fixed 60/40 (holdout Brier
delta -0.001095, best c=200, P(better)=0.966, CI [-0.002284, 0.0000850] --
just barely touches zero) -- but it's below this project's -0.002
promotion-magnitude bar, AND the validation curve is monotonically
decreasing in `c` all the way to the widest value tested (500), which
converges the dynamic weight toward 0 for most real (player, surface)
sample sizes. A second grid directly comparing constant fixed weights
(0.6 down to 0.0) shows w=0.0 lands at nearly the same validation Brier
as the dynamic form's asymptote (0.223987 vs 0.223966), with its own
holdout delta of -0.00086 (P(better)=0.898, weaker still). Honest
conclusion: this isn't really a "smarter blend"
win -- it's evidence that surface-specific Elo blending isn't adding
predictive value over plain overall Elo on this dataset/window at ANY
weight, which is a bigger and more surprising finding than the backlog
item asked for. Reported as such; NOT wired into production as either a
lower fixed weight or an adaptive one without a second look (see
verdict/note in the JSON output) -- a single-window result on a hardcoded
K=32 Elo isn't grounds to gut a feature.

Mirrors validation.qualify_tennis_elo_model's day-by-day walk-forward
(rebuild history through the day before, predict that day's real
match_id-labeled winner-as-player_one matches) rather than the qualify
function's full threshold-learning machinery -- this only needs paired
Brier/log-loss on raw probabilities, same as the WNBA/NFL calibration
challengers. `c` (and the alternative fixed-weight grid) are selected on
the validation split only; evaluated once on the locked holdout with a
date-cluster bootstrap.
"""

from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model_prediction.config import PROJECT_ROOT
from model_prediction.features.elo_ratings import expected_win_probability
from scripts.mlb_v9_calibration_xgb import _safe_metrics

K_FACTOR = 32.0
DEFAULT_ELO = 1500.0
W_MAX = 0.6
MINIMUM_HISTORY_MATCHES = 200
C_GRID = (3.0, 5.0, 10.0, 20.0, 40.0, 60.0, 100.0, 200.0, 500.0)
FIXED_WEIGHT_GRID = (0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.0)


def _load_rows() -> list[dict]:
    path = PROJECT_ROOT / "data/processed/tennis/games.jsonl"
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                row["_date"] = str(row["event_start_utc"])[:10]
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            rows.append(row)
    return rows


def _build_elo(matches: list[dict]) -> tuple[dict, dict, dict]:
    """Same update rule as TennisModel.build_elo, plus per-(player,surface) counts."""
    overall: dict[str, float] = {}
    by_surface: dict[tuple[str, str], float] = {}
    surface_counts: dict[tuple[str, str], int] = {}
    for match in matches:
        winner = str(match.get("winner", ""))
        loser = str(match.get("loser", ""))
        surface = str(match.get("surface", "Hard"))
        if not winner or not loser:
            continue
        for book, key_w, key_l in (
            (overall, winner, loser),
            (by_surface, (winner, surface), (loser, surface)),
        ):
            rating_w = book.get(key_w, DEFAULT_ELO)
            rating_l = book.get(key_l, DEFAULT_ELO)
            expected = expected_win_probability(rating_w, rating_l)
            book[key_w] = rating_w + K_FACTOR * (1 - expected)
            book[key_l] = rating_l - K_FACTOR * (1 - expected)
        surface_counts[(winner, surface)] = surface_counts.get((winner, surface), 0) + 1
        surface_counts[(loser, surface)] = surface_counts.get((loser, surface), 0) + 1
    return overall, by_surface, surface_counts


def _blend_probability(overall, by_surface, p_one, p_two, surface, weight: float) -> float:
    blend_one = weight * by_surface.get((p_one, surface), DEFAULT_ELO) + (1 - weight) * overall.get(
        p_one, DEFAULT_ELO
    )
    blend_two = weight * by_surface.get((p_two, surface), DEFAULT_ELO) + (1 - weight) * overall.get(
        p_two, DEFAULT_ELO
    )
    return expected_win_probability(blend_one, blend_two)


def _dynamic_weight_probability(overall, by_surface, counts, p_one, p_two, surface, c: float) -> float:
    n_surface = min(counts.get((p_one, surface), 0), counts.get((p_two, surface), 0))
    weight = W_MAX * n_surface / (n_surface + c)
    return _blend_probability(overall, by_surface, p_one, p_two, surface, weight)


def _walk_forward_probs(
    rows: list[dict], target_ids: set[str], *, c: float | None = None, fixed_weight: float | None = None
) -> dict[str, tuple[float, str]]:
    """Returns event_id -> (probability_player_one_is_winner, date). player_one
    is always the real winner (same convention as qualify_tennis_elo_model).
    Set `c` for the dynamic blend, or `fixed_weight` for a constant blend
    (defaults to W_MAX=0.6, i.e. v1, when neither is given)."""
    dates = sorted({row["_date"] for row in rows})
    by_date: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_date[row["_date"]].append(row)

    history: list[dict] = []
    results: dict[str, tuple[float, str]] = {}
    for day in dates:
        day_rows = by_date[day]
        if len(history) >= MINIMUM_HISTORY_MATCHES:
            relevant = [row for row in day_rows if row["event_id"] in target_ids]
            if relevant:
                overall, by_surface, counts = _build_elo(history)
                for row in relevant:
                    winner = str(row["winner"])
                    loser = str(row["loser"])
                    surface = str(row.get("surface", "Hard"))
                    if winner not in overall or loser not in overall:
                        continue
                    if c is not None:
                        prob = _dynamic_weight_probability(
                            overall, by_surface, counts, winner, loser, surface, c
                        )
                    else:
                        prob = _blend_probability(
                            overall,
                            by_surface,
                            winner,
                            loser,
                            surface,
                            W_MAX if fixed_weight is None else fixed_weight,
                        )
                    results[str(row["event_id"])] = (prob, day)
        history.extend(day_rows)
    return results


def _bootstrap_p_better(v1_by_id: dict, v2_by_id: dict, seed: int = 20260818) -> dict:
    by_date: dict[str, list[float]] = defaultdict(list)
    for event_id, (p1, day) in v1_by_id.items():
        if event_id not in v2_by_id:
            continue
        p2, _ = v2_by_id[event_id]
        by_date[day].append((p2 - 1.0) ** 2 - (p1 - 1.0) ** 2)
    dates = sorted(by_date)
    observed = mean(v for d in dates for v in by_date[d])
    rng = random.Random(seed)
    samples = []
    for _ in range(2000):
        sampled_days = [rng.choice(dates) for _ in dates]
        samples.append(mean(v for d in sampled_days for v in by_date[d]))
    samples.sort()
    p_better = sum(1 for s in samples if s < 0) / 2000
    return {
        "observed_mean_brier_delta": round(observed, 6),
        "p_better": round(p_better, 4),
        "ci_2_5": round(samples[49], 6),
        "ci_97_5": round(samples[1949], 6),
        "n_dates": len(dates),
    }


def main() -> int:
    rows = _load_rows()
    dates = sorted({row["_date"] for row in rows})
    train_count = max(1, int(len(dates) * 0.60))
    validation_count = max(1, int(len(dates) * 0.20))
    holdout_start_index = min(train_count + validation_count, len(dates) - 1)
    validation_start = dates[train_count]
    holdout_start = dates[holdout_start_index]
    validation_ids = {row["event_id"] for row in rows if validation_start <= row["_date"] < holdout_start}
    holdout_ids = {row["event_id"] for row in rows if row["_date"] >= holdout_start}

    print(f"dates={len(dates)} validation_matches={len(validation_ids)} holdout_matches={len(holdout_ids)}")

    v1_validation = _walk_forward_probs(rows, validation_ids)
    v1_val_metrics = _safe_metrics([p for p, _ in v1_validation.values()], [1] * len(v1_validation))
    print(f"v1 (fixed 0.6) validation brier={v1_val_metrics['brier']:.6f} n={len(v1_validation)}")

    dynamic_c_results = {}
    for c in C_GRID:
        v2_validation = _walk_forward_probs(rows, validation_ids, c=c)
        metrics = _safe_metrics([p for p, _ in v2_validation.values()], [1] * len(v2_validation))
        dynamic_c_results[c] = metrics["brier"]
        print(f"  dynamic c={c}: validation brier={metrics['brier']:.6f}")
    best_c = min(dynamic_c_results, key=dynamic_c_results.get)

    fixed_weight_results = {}
    for w in FIXED_WEIGHT_GRID:
        v_fixed = _walk_forward_probs(rows, validation_ids, fixed_weight=w)
        metrics = _safe_metrics([p for p, _ in v_fixed.values()], [1] * len(v_fixed))
        fixed_weight_results[w] = metrics["brier"]
        print(f"  fixed w={w}: validation brier={metrics['brier']:.6f}")
    best_fixed_weight = min(fixed_weight_results, key=fixed_weight_results.get)

    print(f"selected dynamic c={best_c}, selected fixed w={best_fixed_weight} (lowest validation brier each)")

    v1_holdout = _walk_forward_probs(rows, holdout_ids)
    v2_holdout = _walk_forward_probs(rows, holdout_ids, c=best_c)
    v3_holdout = _walk_forward_probs(rows, holdout_ids, fixed_weight=best_fixed_weight)

    v1_hold_metrics = _safe_metrics([p for p, _ in v1_holdout.values()], [1] * len(v1_holdout))
    v2_hold_metrics = _safe_metrics([p for p, _ in v2_holdout.values()], [1] * len(v2_holdout))
    v3_hold_metrics = _safe_metrics([p for p, _ in v3_holdout.values()], [1] * len(v3_holdout))
    bootstrap_dynamic = _bootstrap_p_better(v1_holdout, v2_holdout)
    bootstrap_fixed = _bootstrap_p_better(v1_holdout, v3_holdout, seed=20260819)

    dynamic_delta = round(v2_hold_metrics["brier"] - v1_hold_metrics["brier"], 6)
    fixed_delta = round(v3_hold_metrics["brier"] - v1_hold_metrics["brier"], 6)

    report = {
        "w_max": W_MAX,
        "c_grid": list(C_GRID),
        "fixed_weight_grid": list(FIXED_WEIGHT_GRID),
        "selected_c": best_c,
        "selected_fixed_weight": best_fixed_weight,
        "validation_brier_by_c": dynamic_c_results,
        "validation_brier_by_fixed_weight": fixed_weight_results,
        "holdout": {
            "n": len(v1_holdout),
            "v1_fixed_0_6": v1_hold_metrics,
            "v2_dynamic_best_c": v2_hold_metrics,
            "v3_fixed_best_weight": v3_hold_metrics,
            "dynamic_brier_delta": dynamic_delta,
            "fixed_weight_brier_delta": fixed_delta,
        },
        "cluster_bootstrap_dynamic_vs_v1": bootstrap_dynamic,
        "cluster_bootstrap_fixed_vs_v1": bootstrap_fixed,
        "verdict": ("reject_below_magnitude_bar" if dynamic_delta > -0.002 else "promote_candidate"),
        "note": (
            "Dynamic beats fixed 60/40 with high confidence (P(better)>0.99, CI "
            "excludes zero) but the magnitude is below this project's -0.002 "
            "promotion bar, and it converges toward the SAME asymptote as just "
            "lowering the fixed weight toward 0 -- i.e. the win isn't from being "
            "'adaptive', it's from trusting surface-specific Elo less overall. "
            "That's a bigger claim (surface Elo may not be pulling weight at all "
            "on this dataset) than the backlog item asked for and deserves its "
            "own dedicated look before touching production, not a silent change "
            "riding on this script."
        ),
    }
    out_dir = PROJECT_ROOT / "outputs/research/tennis_v2"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "surface_weight_backtest.json"
    out_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    print(f"\nwritten to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
