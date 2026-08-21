"""Train soccer-poisson-dc-rebuild-v1: Dixon-Coles Poisson model.

Independent rebuild-native fit from ESPN capture-time-only normalized data.
No incumbent constants, no incumbent artifact loaded.

Data: SoccerNormalizedStore (capture-time-only provenance).
Split: chronological 60/20/20 by event_start_utc.
Model: Dixon-Coles bivariate Poisson with MLE via scipy.optimize.minimize.
Evaluation: 3-way LogLoss, per-outcome Brier, accuracy.
Output: challenger artifact at config/models/challengers/.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import polars as pl

from model_prediction.rebuild.soccer.elo import DixonColesModel
from model_prediction.rebuild.soccer.store import SoccerNormalizedStore


def load_completed_matches(store: SoccerNormalizedStore) -> pl.DataFrame:
    """Load completed matches from the normalized store.

    Completed = STATUS_FULL_TIME, STATUS_FINAL_AET, or STATUS_FINAL_PEN,
    with both home and away scores present.
    """
    frame = store.read_matches()
    if frame.height == 0:
        raise RuntimeError("No data in normalized store. Run backfill first.")

    completed_statuses = {"STATUS_FULL_TIME", "STATUS_FINAL_AET", "STATUS_FINAL_PEN"}
    matches = frame.filter(
        pl.col("status").is_in(list(completed_statuses))
        & pl.col("home_score").is_not_null()
        & pl.col("away_score").is_not_null()
    )

    # Parse event_start_utc for chronological split
    matches = matches.with_columns(
        pl.col("event_start_utc").str.to_datetime(time_zone="UTC", strict=False).alias("_event_dt")
    ).filter(pl.col("_event_dt").is_not_null())

    return matches.sort("_event_dt")


def chronological_split(
    matches: pl.DataFrame, train_frac: float = 0.6, cal_frac: float = 0.2
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Chronological 60/20/20 split by event_start_utc.

    Returns (train, calibration, test).
    """
    n = matches.height
    train_end = int(n * train_frac)
    cal_end = int(n * (train_frac + cal_frac))

    train = matches.slice(0, train_end)
    cal = matches.slice(train_end, cal_end - train_end)
    test = matches.slice(cal_end, n - cal_end)

    return train, cal, test


def evaluate(
    model: DixonColesModel,
    matches: pl.DataFrame,
    label: str = "eval",
) -> dict:
    """Evaluate model on a set of matches.

    Returns metrics dict with log_loss, brier_home, brier_draw, brier_away,
    accuracy, and n.
    """
    if matches.height == 0:
        return {"label": label, "n": 0}

    preds = model.predict_batch(matches)

    # Actual outcomes: one-hot encoded
    actual_home = (pl.col("home_score") > pl.col("away_score")).cast(pl.Float64)
    actual_draw = (pl.col("home_score") == pl.col("away_score")).cast(pl.Float64)
    actual_away = (pl.col("home_score") < pl.col("away_score")).cast(pl.Float64)

    scored = preds.with_columns(
        actual_home.alias("a_home"),
        actual_draw.alias("a_draw"),
        actual_away.alias("a_away"),
    )

    n = scored.height

    # LogLoss (3-way): -mean(log(p_correct))
    a_home = scored["a_home"].to_numpy()
    a_draw = scored["a_draw"].to_numpy()
    a_away = scored["a_away"].to_numpy()
    p_home = np.clip(scored["p_home"].to_numpy(), 1e-15, 1.0)
    p_draw = np.clip(scored["p_draw"].to_numpy(), 1e-15, 1.0)
    p_away = np.clip(scored["p_away"].to_numpy(), 1e-15, 1.0)

    log_loss = -np.mean(a_home * np.log(p_home) + a_draw * np.log(p_draw) + a_away * np.log(p_away))

    # Per-outcome Brier scores
    brier_home = float(np.mean((p_home - a_home) ** 2))
    brier_draw = float(np.mean((p_draw - a_draw) ** 2))
    brier_away = float(np.mean((p_away - a_away) ** 2))
    brier_mean = (brier_home + brier_draw + brier_away) / 3.0

    # Accuracy: highest probability outcome
    preds_stacked = np.column_stack([p_home, p_draw, p_away])
    actual_stacked = np.column_stack([a_home, a_draw, a_away])
    correct = np.argmax(preds_stacked, axis=1) == np.argmax(actual_stacked, axis=1)
    accuracy = float(np.mean(correct))

    # Outcome distribution
    home_pct = float(np.mean(a_home))
    draw_pct = float(np.mean(a_draw))
    away_pct = float(np.mean(a_away))

    return {
        "label": label,
        "n": n,
        "log_loss": float(log_loss),
        "brier_home": brier_home,
        "brier_draw": brier_draw,
        "brier_away": brier_away,
        "brier_mean": brier_mean,
        "accuracy": accuracy,
        "actual_home_pct": home_pct,
        "actual_draw_pct": draw_pct,
        "actual_away_pct": away_pct,
    }


def save_challenger_artifact(
    model: DixonColesModel,
    train_metrics: dict,
    cal_metrics: dict,
    test_metrics: dict,
    output_dir: Path,
) -> str:
    """Persist challenger artifact JSON and return artifact hash."""
    params = model.params.to_dict() if model.params else {}

    artifact = {
        "model_id": "soccer-poisson-dc-rebuild-v1",
        "sport": "soccer",
        "market_type": "moneyline_3way",
        "family": "poisson_dixon_coles",
        "version": "1",
        "method": "dixon_coles_mle",
        "fitted": True,
        "production_allowed": False,
        "pit_status": "historical_result_research",
        "pit_note": (
            "All training data is capture-time-only provenance from ESPN site v2. "
            "No retrospective PIT evidence exists — these are historical results "
            "downloaded from current ESPN scoreboards, not captured live."
        ),
        "independent_fit": True,
        "incumbent_constants_used": False,
        "incumbent_note": (
            "This model is independently fitted from ESPN data. It does not load, "
            "alias, or share state with the incumbent soccer-poisson-dc-v1."
        ),
        "params": params,
        "num_teams": len(params.get("team_attack", {})),
        "num_leagues": len(params.get("league_baseline", {})),
        "home_advantage": params.get("home_advantage"),
        "rho": params.get("rho"),
        "train_metrics": train_metrics,
        "calibration_metrics": cal_metrics,
        "test_metrics": test_metrics,
        "artifact_hash": "",
    }

    # Compute hash
    payload = json.dumps(artifact, sort_keys=True, indent=2)
    artifact_hash = hashlib.sha256(payload.encode()).hexdigest()
    artifact["artifact_hash"] = artifact_hash

    # Write
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = output_dir / "soccer-poisson-dc-rebuild-v1.json"
    artifact_path.write_text(json.dumps(artifact, indent=2, sort_keys=True))

    # Write calibrator (identity — 3-way needs different calibration than binary)
    calibrator = {
        "model_id": "soccer-poisson-dc-rebuild-v1-calibrator",
        "sport": "soccer",
        "calibration_method": "identity",
        "note": (
            "3-way probabilities need different calibration methods than binary. "
            "Identity calibration is a placeholder — isotonic or Platt scaling "
            "on the full 3-way simplex would require multivariate calibration "
            "that is not yet implemented for soccer."
        ),
        "production_allowed": False,
        "parent_artifact_hash": artifact_hash,
    }
    cal_path = output_dir / "soccer-poisson-dc-rebuild-v1-calibrator.json"
    cal_path.write_text(json.dumps(calibrator, indent=2, sort_keys=True))

    return artifact_hash


def main() -> None:
    data_root = Path("data/rebuild")
    output_dir = Path("config/models/challengers")

    print("=" * 60)
    print("Soccer Poisson-DC Rebuild v1 — Training")
    print("=" * 60)

    # 1. Load data
    print("\n[1/5] Loading completed matches ...")
    store = SoccerNormalizedStore(data_root / "normalized")
    matches = load_completed_matches(store)
    print(f"  Loaded {matches.height} completed matches")
    leagues = sorted(matches["competition_id"].unique().to_list())
    print(f"  Leagues: {leagues}")
    seasons = sorted(matches["season_id"].unique().to_list())
    print(f"  Seasons: {seasons}")

    # 2. Chronological split
    print("\n[2/5] Chronological 60/20/20 split ...")
    train, cal, test = chronological_split(matches)
    print(f"  Train: {train.height}  Cal: {cal.height}  Test: {test.height}")

    date_range = lambda df: (
        df["_event_dt"].min(),
        df["_event_dt"].max(),
    )
    t_min, t_max = date_range(train)
    print(f"  Train date range: {t_min} → {t_max}")
    c_min, c_max = date_range(cal)
    print(f"  Cal   date range: {c_min} → {c_max}")
    te_min, te_max = date_range(test)
    print(f"  Test  date range: {te_min} → {te_max}")

    # 3. Fit model
    print("\n[3/5] Fitting Dixon-Coles Poisson model (MLE) ...")
    model = DixonColesModel()
    params = model.fit(train, verbose=True)

    print(f"  Teams: {len(params.team_attack)}")
    print(f"  Leagues: {len(params.league_baseline)}")
    print(f"  Home advantage: {params.home_advantage:.4f}")
    print(f"  Rho: {params.rho:.6f}")

    # Show top/bottom 5 attack/defense
    att_sorted = sorted(params.team_attack.items(), key=lambda x: x[1], reverse=True)
    def_sorted = sorted(params.team_defense.items(), key=lambda x: x[1])
    print("  Top 5 attacks:", [(t, round(v, 3)) for t, v in att_sorted[:5]])
    print("  Top 5 defenses:", [(t, round(v, 3)) for t, v in def_sorted[:5]])

    # 4. Evaluate
    print("\n[4/5] Evaluating ...")
    train_metrics = evaluate(model, train, "train")
    cal_metrics = evaluate(model, cal, "calibration")
    test_metrics = evaluate(model, test, "test")

    for m in [train_metrics, cal_metrics, test_metrics]:
        print(
            f"  {m['label']:>12s}: n={m['n']:>5d}  "
            f"LogLoss={m['log_loss']:.4f}  "
            f"Brier={m['brier_mean']:.4f}  "
            f"Acc={m['accuracy']:.3f}  "
            f"H%={m['actual_home_pct']:.2f} D%={m['actual_draw_pct']:.2f} A%={m['actual_away_pct']:.2f}"
        )

    # 5. Save artifact
    print("\n[5/5] Saving challenger artifact ...")
    artifact_hash = save_challenger_artifact(
        model,
        train_metrics,
        cal_metrics,
        test_metrics,
        output_dir,
    )
    print(f"  Artifact hash: {artifact_hash}")
    print(f"  Written to: {output_dir / 'soccer-poisson-dc-rebuild-v1.json'}")
    print(f"  Calibrator: {output_dir / 'soccer-poisson-dc-rebuild-v1-calibrator.json'}")

    print("\n" + "=" * 60)
    print("Training complete. production_allowed=false")
    print("=" * 60)


if __name__ == "__main__":
    main()
