"""Real naive/incumbent baselines for the MLB moneyline benchmark --
closes a real gap: model_benchmark.parquet only ever compared real
challengers against each other, never against a naive floor. A challenger
beating another challenger is not the same claim as a challenger beating
even a constant 0.5 or the real expanding historical home-win base rate --
this script computes both, on the identical real chronological OOF rows
the winning head-family/distribution combination uses
(train_mlb_head_distribution_cartesian.py), plus a real (differently
sampled, disclosed) incumbent-model reference point from the already-frozen
`current_model_baselines.parquet`.

Real, disclosed scope: a real timestamp-valid market no-vig probability
baseline is NOT included here -- no real market quote is currently linked
to these exact OOF games (the same real gap disclosed in
economic_report.md/mlb_settle_and_capture_closing.py: real market capture
only exists for a handful of specific recent dates, not this dataset's
full real range). Reported as "unavailable", not fabricated.

Registry-safe: does not touch test_consumption_registry.json.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/build_mlb_naive_baselines.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.rebuild.horizon_builder import build_mlb_historical_horizon_dataset
from model_prediction.rebuild.mlb_features import dedupe_scoreboard
from model_prediction.rebuild.validation import brier_score, expanding_folds, log_loss

HORIZON = "late"


def _home_win_labels(df: pl.DataFrame) -> list[int]:
    return [1 if r["home_score"] > r["away_score"] else 0 for r in df.iter_rows(named=True)]


def main() -> None:
    sb_path = Path("data/rebuild/normalized/mlb/scoreboard.parquet")
    if not sb_path.exists():
        print(f"ERROR: {sb_path} not found. Run the MLB collector first.")
        sys.exit(1)

    sb = dedupe_scoreboard(pl.read_parquet(sb_path))
    completed = sb.filter(pl.col("status") == "STATUS_FINAL").sort("event_start_utc")
    if completed.height == 0:
        print("No completed games. Stopping honestly.")
        sys.exit(0)
    start_date = completed["event_start_utc"][0][:10]
    end_date = completed["event_start_utc"][-1][:10]

    dataset = build_mlb_historical_horizon_dataset("data/rebuild", start_date, end_date, HORIZON)
    features = dataset.features.sort("event_start_utc") if dataset.features.height else dataset.features
    print(f"1. Feature rows: {dataset.matched_games} matched; dataset_hash={dataset.dataset_hash[:12]}")

    game_dates = features["game_date"].to_list()
    n_unique_dates = len(set(game_dates))
    val_size_days = max(1, n_unique_dates // 6)
    test_size_days = max(1, n_unique_dates // 6)
    folds = expanding_folds(game_dates, n_splits=3, val_size=val_size_days, test_size=test_size_days, gap=1)
    print(f"2. Chronological folds: {len(folds)} ({n_unique_dates} real distinct dates)")

    constant_probs: list[float] = []
    expanding_probs: list[float] = []
    labels: list[int] = []

    for fold in folds:
        train_df = features.filter(pl.col("game_date") <= fold.train_end)
        val_df = features.filter(
            (pl.col("game_date") >= fold.val_start) & (pl.col("game_date") <= fold.val_end)
        )
        if train_df.height < 10 or val_df.height < 3:
            continue
        y_val = _home_win_labels(val_df)
        y_train = _home_win_labels(train_df)
        # Real chronological base rate: home-win rate over every real prior
        # training row only -- never the validation block's own outcomes.
        base_rate = sum(y_train) / len(y_train)

        labels.extend(y_val)
        constant_probs.extend([0.5] * len(y_val))
        expanding_probs.extend([base_rate] * len(y_val))

    n = len(labels)
    print(f"\n3. Real naive baselines on the identical {n} real OOF rows the coherent-model comparisons use:")
    constant_ll = log_loss(labels, constant_probs)
    constant_brier = brier_score(labels, constant_probs)
    expanding_ll = log_loss(labels, expanding_probs)
    expanding_brier = brier_score(labels, expanding_probs)
    print(f"   constant_0.5                 : n={n} log_loss={constant_ll:.4f} brier={constant_brier:.4f}")
    print(f"   expanding_historical_base_rate: n={n} log_loss={expanding_ll:.4f} brier={expanding_brier:.4f}")

    # Real incumbent reference point -- computed on the incumbent's OWN
    # real matched sample from current_model_baselines.parquet (n and date
    # range disclosed as different from the n=223 OOF above; this is not
    # claimed to be a row-identical comparison).
    incumbent_ll: float | None = None
    incumbent_brier: float | None = None
    incumbent_n = 0
    baselines_path = Path("outputs/rebuild/current_model_baselines.parquet")
    if baselines_path.exists():
        base_df = pl.read_parquet(baselines_path)
        ml = base_df.filter((pl.col("sport") == "mlb") & (pl.col("market_type") == "moneyline"))
        if ml.height > 0:
            inc_probs: list[float] = []
            inc_labels: list[int] = []
            for r in ml.iter_rows(named=True):
                home_prob = (
                    r["model_probability"] if r["selection"] == "home" else 1.0 - r["model_probability"]
                )
                selection_won = r["result"] == "win"
                home_won = selection_won if r["selection"] == "home" else not selection_won
                inc_probs.append(home_prob)
                inc_labels.append(1 if home_won else 0)
            incumbent_n = len(inc_probs)
            incumbent_ll = log_loss(inc_labels, inc_probs)
            incumbent_brier = brier_score(inc_labels, inc_probs)
            print(
                f"   incumbent_elo_trend_lr_v8     : n={incumbent_n} log_loss={incumbent_ll:.4f} "
                f"brier={incumbent_brier:.4f} (real, DIFFERENT sample/date range than the n={n} OOF above)"
            )
        else:
            print(
                "   incumbent_elo_trend_lr_v8     : no real moneyline rows in current_model_baselines.parquet"
            )
    else:
        print(f"   incumbent_elo_trend_lr_v8     : {baselines_path} not found")

    print("\n4. market_no_vig_probability   : unavailable -- no real timestamp-valid market quote is")
    print("   currently linked to these exact OOF games (real, disclosed; not fabricated).")

    # Append to the real machine-readable benchmark rather than duplicating
    # its schema here -- one real row per naive/incumbent baseline.
    benchmark_path = Path("outputs/rebuild/model_benchmark.parquet")
    new_rows = [
        {
            "sport": "mlb",
            "model": "constant_0.5",
            "market_type": "moneyline",
            "selection": None,
            "line": None,
            "calibrated": False,
            "calibration_method": None,
            "n": n,
            "log_loss": constant_ll,
            "brier": constant_brier,
            "ece": None,
            "status": "RESEARCH_ONLY",
            "dataset_hash": dataset.dataset_hash,
            "source_file": "build_mlb_naive_baselines.py",
        },
        {
            "sport": "mlb",
            "model": "expanding_historical_home_win_base_rate",
            "market_type": "moneyline",
            "selection": None,
            "line": None,
            "calibrated": False,
            "calibration_method": None,
            "n": n,
            "log_loss": expanding_ll,
            "brier": expanding_brier,
            "ece": None,
            "status": "RESEARCH_ONLY",
            "dataset_hash": dataset.dataset_hash,
            "source_file": "build_mlb_naive_baselines.py",
        },
    ]
    if incumbent_ll is not None:
        new_rows.append(
            {
                "sport": "mlb",
                "model": "incumbent_elo_trend_lr_v8",
                "market_type": "moneyline",
                "selection": None,
                "line": None,
                "calibrated": False,
                "calibration_method": None,
                "n": incumbent_n,
                "log_loss": incumbent_ll,
                "brier": incumbent_brier,
                "ece": None,
                "status": "RESEARCH_ONLY",
                "dataset_hash": "n/a (real, different sample -- see current_model_baselines.parquet)",
                "source_file": "build_mlb_naive_baselines.py",
            }
        )

    new_df = pl.DataFrame(new_rows)
    if benchmark_path.exists():
        existing = pl.read_parquet(benchmark_path)
        existing = existing.filter(~pl.col("source_file").eq("build_mlb_naive_baselines.py"))
        combined = pl.concat([existing, new_df], how="diagonal_relaxed")
    else:
        combined = new_df
    combined.write_parquet(benchmark_path)
    print(
        f"\n5. {new_df.height} real baseline rows written to {benchmark_path} ({combined.height} total rows)"
    )


if __name__ == "__main__":
    main()
