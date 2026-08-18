"""MLB umpire strike-zone / over-under signal (accuracy-first queue item 3).

docs/RESEARCH_LITERATURE_DIVE_3_2026-08-17.md cites a peer-reviewed 0.3-0.5
runs/game home-plate-umpire class effect (Mills 2016). game_snapshots.jsonl
already records `officials` (home-plate umpire included) per game -- this is
a Stage 1 correlation/signal-detection backtest in the SAME spirit as the
line-movement shadow feature's first pass ("weak, directionally consistent,
nowhere near promotable. Revisit ... run real ablation on frozen feature
table"): walk-forward, credibility-shrunk per-umpire run factor vs actual
game totals, NOT a full gamma_poisson/simulate_game integration -- that's
the natural next step if this finds real signal, not before.

Formula mirrors features/park_factors_pit.py's park-factor shrinkage
exactly (same "credibility toward league-neutral 1.0" shape, different
grouping key): for each home-plate umpire, walk-forward (only PRIOR starts,
strictly point-in-time) credibility-shrunk factor =
    (n / (n + k)) * (observed_avg_total_at_ump_starts / league_avg_total)
    + (k / (n + k)) * 1.0
`k` (prior_strength) grid-searched on the validation split by how well the
umpire factor's sign matches the actual (total - park_expected) residual's
sign; evaluated once on the locked holdout with a date-cluster bootstrap on
squared-error delta of (park_factor-only baseline) vs (park_factor *
umpire_factor).
"""

from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, pstdev

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model_prediction.config import PROJECT_ROOT
from model_prediction.features.park_factors_pit import compute_park_factors_from_games

GAMES_PATH = PROJECT_ROOT / "data/historical/mlb_games_all.jsonl"
SNAPSHOTS_PATH = PROJECT_ROOT / "data/mlb_statsapi/game_snapshots.jsonl"
K_GRID = (10, 20, 30, 50, 80, 120)


class _GameRecord:
    __slots__ = ("away_score", "event_start_utc", "home_score", "home_team")

    def __init__(self, home_team: str, away_score: int, home_score: int, event_start_utc: str) -> None:
        self.home_team = home_team
        self.away_score = away_score
        self.home_score = home_score
        self.event_start_utc = event_start_utc


def _load_games() -> list[dict]:
    rows = []
    with GAMES_PATH.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("status") != "completed":
                continue
            if row.get("home_score") is None or row.get("away_score") is None:
                continue
            rows.append(row)
    return rows


def _load_home_plate_umpires() -> dict[str, str]:
    """snap_key (date_T_hh_mm|home_team|away_team) -> home-plate umpire name."""
    out: dict[str, str] = {}
    with SNAPSHOTS_PATH.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                snap = json.loads(line)
            except json.JSONDecodeError:
                continue
            officials = snap.get("officials") or []
            plate_ump = next((o.get("name") for o in officials if o.get("type") == "Home Plate"), None)
            if not plate_ump:
                continue
            home_name = (snap.get("home") or {}).get("team_name", "")
            away_name = (snap.get("away") or {}).get("team_name", "")
            key = str(snap.get("game_start_utc", ""))[:16] + "|" + home_name + "|" + away_name
            out[key] = str(plate_ump)
    return out


def main() -> int:
    games = _load_games()
    umpires = _load_home_plate_umpires()
    print(f"games={len(games)}, umpire snapshot keys={len(umpires)}")

    joined = []
    for game in games:
        key = str(game["event_start_utc"])[:16] + "|" + game["home_team"] + "|" + game["away_team"]
        ump = umpires.get(key)
        if ump is None:
            continue
        joined.append({**game, "_date": game["event_start_utc"][:10], "_umpire": ump})
    print(f"joined (has home-plate umpire)={len(joined)}")
    joined.sort(key=lambda r: r["_date"])

    dates = sorted({r["_date"] for r in joined})
    train_count = max(1, int(len(dates) * 0.60))
    validation_count = max(1, int(len(dates) * 0.20))
    holdout_start_index = min(train_count + validation_count, len(dates) - 1)
    validation_start = dates[train_count]
    holdout_start = dates[holdout_start_index]

    def _park_factors_before(cutoff_date: str) -> dict[str, float]:
        prior = [
            _GameRecord(g["home_team"], g["away_score"], g["home_score"], g["event_start_utc"])
            for g in joined
            if g["_date"] < cutoff_date
        ]
        return compute_park_factors_from_games(prior)

    def _walk_forward(min_date: str, max_date: str, k: int) -> list[dict]:
        """For each game in [min_date, max_date), compute (umpire_factor,
        park_expected_total, actual_total) using ONLY strictly-prior data.
        Park factors are recomputed once per distinct date (not per row) --
        they only need to reflect games strictly before that date, and many
        rows on the same date share the exact same "prior" set."""
        ump_totals: dict[str, list[int]] = defaultdict(list)
        league_totals: list[int] = []
        out = []
        park_factor_cache: dict[str, dict[str, float]] = {}
        for row in joined:
            if row["_date"] >= min_date:
                if row["_date"] >= max_date:
                    break
                league_avg = mean(league_totals) if league_totals else None
                prior = ump_totals.get(row["_umpire"], [])
                if league_avg and prior:
                    n = len(prior)
                    observed_avg = mean(prior)
                    credibility = n / (n + k)
                    umpire_factor = credibility * (observed_avg / league_avg) + (1 - credibility) * 1.0
                else:
                    umpire_factor = 1.0
                if row["_date"] not in park_factor_cache:
                    park_factor_cache[row["_date"]] = _park_factors_before(row["_date"])
                park_factor = park_factor_cache[row["_date"]].get(row["home_team"], 1.0)
                actual_total = int(row["home_score"]) + int(row["away_score"])
                out.append(
                    {
                        "date": row["_date"],
                        "umpire_factor": umpire_factor,
                        "park_factor": park_factor,
                        "league_avg": league_avg,
                        "actual_total": actual_total,
                        "n_ump_prior": len(prior),
                    }
                )
            actual_total = int(row["home_score"]) + int(row["away_score"])
            league_totals.append(actual_total)
            ump_totals[row["_umpire"]].append(actual_total)
        return out

    def _score(rows: list[dict]) -> dict:
        """MAE of park-only baseline vs park*umpire candidate, plus sign
        agreement between the umpire factor's direction and the actual
        residual's direction (only rows with real umpire history)."""
        usable = [r for r in rows if r["league_avg"] is not None and r["n_ump_prior"] >= 5]
        if not usable:
            return {"n": 0}
        baseline_err = []
        candidate_err = []
        sign_matches = 0
        for r in usable:
            baseline_pred = r["league_avg"] * r["park_factor"]
            candidate_pred = r["league_avg"] * r["park_factor"] * r["umpire_factor"]
            baseline_err.append((r["actual_total"] - baseline_pred) ** 2)
            candidate_err.append((r["actual_total"] - candidate_pred) ** 2)
            residual_sign = (
                1 if r["actual_total"] > baseline_pred else (-1 if r["actual_total"] < baseline_pred else 0)
            )
            ump_sign = 1 if r["umpire_factor"] > 1.0 else (-1 if r["umpire_factor"] < 1.0 else 0)
            if residual_sign != 0 and ump_sign != 0 and residual_sign == ump_sign:
                sign_matches += 1
        return {
            "n": len(usable),
            "baseline_mse": round(mean(baseline_err), 4),
            "candidate_mse": round(mean(candidate_err), 4),
            "mse_delta": round(mean(candidate_err) - mean(baseline_err), 6),
            "sign_agreement_rate": round(sign_matches / len(usable), 4),
        }

    k_results = {}
    for k in K_GRID:
        val_rows = _walk_forward(validation_start, holdout_start, k)
        k_results[k] = _score(val_rows)
        print(f"k={k}: validation {k_results[k]}")
    best_k = min(
        (k for k in K_GRID if k_results[k]["n"] > 0),
        key=lambda k: k_results[k]["mse_delta"],
        default=K_GRID[0],
    )
    print(f"selected k={best_k} (lowest validation MSE delta)")

    holdout_rows = _walk_forward(holdout_start, "9999-99-99", best_k)
    holdout_score = _score(holdout_rows)

    usable_holdout = [r for r in holdout_rows if r["league_avg"] is not None and r["n_ump_prior"] >= 5]
    by_date: dict[str, list[float]] = defaultdict(list)
    for r in usable_holdout:
        baseline_pred = r["league_avg"] * r["park_factor"]
        candidate_pred = baseline_pred * r["umpire_factor"]
        by_date[r["date"]].append(
            (r["actual_total"] - candidate_pred) ** 2 - (r["actual_total"] - baseline_pred) ** 2
        )
    rng = random.Random(20260818)
    dates_sorted = sorted(by_date)
    samples = []
    for _ in range(2000):
        sampled = [rng.choice(dates_sorted) for _ in dates_sorted]
        samples.append(mean(v for d in sampled for v in by_date[d]))
    samples.sort()
    p_better = sum(1 for s in samples if s < 0) / 2000 if samples else None

    # simple Pearson r between umpire deviation and actual residual (magnitude, not just sign)
    devs = [r["umpire_factor"] - 1.0 for r in usable_holdout]
    resids = [r["actual_total"] - r["league_avg"] * r["park_factor"] for r in usable_holdout]
    if len(devs) > 5 and pstdev(devs) > 0 and pstdev(resids) > 0:
        mean_d, mean_r = mean(devs), mean(resids)
        cov = mean((d - mean_d) * (rr - mean_r) for d, rr in zip(devs, resids, strict=True))
        pearson_r = cov / (pstdev(devs) * pstdev(resids))
    else:
        pearson_r = None

    report = {
        "n_games_joined": len(joined),
        "k_grid": list(K_GRID),
        "selected_k": best_k,
        "validation_by_k": k_results,
        "holdout": holdout_score,
        "holdout_pearson_r_umpire_dev_vs_residual": round(pearson_r, 4) if pearson_r is not None else None,
        "holdout_bootstrap_p_better": round(p_better, 4) if p_better is not None else None,
        "n_dates_bootstrap": len(dates_sorted),
        "verdict": (
            "weak_signal_not_promotable"
            if holdout_score.get("n", 0) > 0 and holdout_score["mse_delta"] < 0 and (p_better or 0) > 0.55
            else "no_signal_detected"
        ),
        "note": (
            "Stage 1 correlation backtest only -- MSE-of-mean comparison "
            "against a park-factor-only baseline, not a full gamma_poisson "
            "distribution/Brier evaluation. A promotable finding here would "
            "still need the same simulate_game-based Brier/log-loss "
            "evaluation the air-density and starter-IP challengers got "
            "before touching production."
        ),
    }
    out_dir = PROJECT_ROOT / "outputs/research/mlb_umpire"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "totals_signal.json"
    out_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    print(f"\nwritten to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
