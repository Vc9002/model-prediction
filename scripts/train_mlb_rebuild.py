"""Train MLB two-head model on real scoreboard features.

Replaces the smoke-test (np.random.randn) with actual rolling team
statistics computed from completed games.  Still a baseline — the full
Statcast/weather/lineup/pitcher feature set requires the corresponding
collectors to be completed first (Part 1-F of the rebuild spec).

Usage:
    python scripts/train_mlb_rebuild.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import polars as pl

# ── Allow running from repo root ──────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.rebuild.models import MLBTwoHeadModel  # noqa: E402
from model_prediction.rebuild.validation import log_loss, brier_score, ece  # noqa: E402


# ── Feature engineering ───────────────────────────────────────────────────

def build_features(df: pl.DataFrame) -> pl.DataFrame:
    """Compute rolling team statistics for every completed game.

    For each game, look back at the team's previous N completed games
    (excluding the current row) and compute rolling averages.  This is
    a true point-in-time computation — no future leakage.
    """
    # Ensure chronological order.
    df = df.sort("event_start_utc")

    N = 10  # rolling window size

    rows: list[dict] = []
    # Team-level rolling caches keyed by team name.
    team_history: dict[str, list[dict]] = {}

    for row in df.iter_rows(named=True):
        home = row["home_team"]
        away = row["away_team"]
        home_score = row["home_score"]
        away_score = row["away_score"]

        # ── Look back at previous games ──────────────────────────────
        def rolling(team: str, stat: str, default: float = 0.0) -> float:
            history = team_history.get(team, [])
            recent = history[-N:] if len(history) >= N else history
            if not recent:
                return default
            return sum(h[stat] for h in recent) / len(recent)

        home_rs = rolling(home, "runs_scored", 4.5)
        home_ra = rolling(home, "runs_allowed", 4.5)
        away_rs = rolling(away, "runs_scored", 4.5)
        away_ra = rolling(away, "runs_allowed", 4.5)
        home_rd = rolling(home, "run_diff", 0.0)
        away_rd = rolling(away, "run_diff", 0.0)
        home_wp = rolling(home, "win", 0.5)
        away_wp = rolling(away, "win", 0.5)

        total_runs = home_score + away_score
        home_margin = home_score - away_score

        rows.append({
            "event_id": row["event_id"],
            "home_team": home,
            "away_team": away,
            "total_runs": total_runs,
            "home_margin": home_margin,
            "home_score": home_score,
            "away_score": away_score,
            # Intensity features
            "home_rolling_runs_scored": home_rs,
            "away_rolling_runs_scored": away_rs,
            "home_rolling_runs_allowed": home_ra,
            "away_rolling_runs_allowed": away_ra,
            "home_avg_total": home_rs + home_ra,
            "away_avg_total": away_rs + away_ra,
            # Differential features
            "home_rolling_run_diff": home_rd,
            "away_rolling_run_diff": away_rd,
            "home_win_pct": home_wp,
            "away_win_pct": away_wp,
            "event_start_utc": row["event_start_utc"],
        })

        # Update team histories AFTER predicting (no leakage).
        for team, runs_for, runs_against, win in [
            (home, home_score, away_score, 1.0 if home_score > away_score else 0.0),
            (away, away_score, home_score, 1.0 if away_score > home_score else 0.0),
        ]:
            if team not in team_history:
                team_history[team] = []
            team_history[team].append({
                "runs_scored": runs_for,
                "runs_allowed": runs_against,
                "run_diff": runs_for - runs_against,
                "win": win,
            })

    return pl.DataFrame(rows)


# ── Main pipeline ─────────────────────────────────────────────────────────

def main() -> None:
    data_path = Path("data/rebuild/normalized/mlb/scoreboard.parquet")
    if not data_path.exists():
        print(f"ERROR: {data_path} not found. Run the MLB collector first.")
        sys.exit(1)

    raw = pl.read_parquet(data_path)
    completed = raw.filter(raw["status"] == "STATUS_FINAL").sort("event_start_utc")
    print(f"Loaded {raw.height} rows, {completed.height} completed games")

    if completed.height < 20:
        print("Not enough completed games to train (need ≥20)")
        sys.exit(0)

    features = build_features(completed)

    # ── Chronological train/test split (80/20) ───────────────────────
    split_idx = int(features.height * 0.8)
    train = features[:split_idx]
    test = features[split_idx:]

    print(f"Train: {train.height} games, Test: {test.height} games")

    intensity_features = [
        "home_rolling_runs_scored", "away_rolling_runs_scored",
        "home_rolling_runs_allowed", "away_rolling_runs_allowed",
        "home_avg_total", "away_avg_total",
    ]
    differential_features = [
        "home_rolling_run_diff", "away_rolling_run_diff",
        "home_win_pct", "away_win_pct",
    ]

    # ── Train ─────────────────────────────────────────────────────────
    model = MLBTwoHeadModel(seed=42)
    model.fit(
        train,
        intensity_features=intensity_features,
        differential_features=differential_features,
    )
    print(f"Trained MLBTwoHeadModel on {train.height} games")

    # ── Evaluate on test set ──────────────────────────────────────────
    y_true: list[int] = []
    y_prob: list[float] = []
    correct = 0

    for row in test.iter_rows(named=True):
        pred = model.predict_row(row["event_id"], row)
        home_win_prob = pred.home_win_prob
        actual_home_win = 1 if row["home_score"] > row["away_score"] else 0
        y_true.append(actual_home_win)
        y_prob.append(home_win_prob)
        if (home_win_prob >= 0.5) == (actual_home_win == 1):
            correct += 1

    ll = log_loss(y_true, y_prob)
    br = brier_score(y_true, y_prob)
    ec = ece(y_true, y_prob)
    acc = correct / len(y_true)

    print(f"\nTest set evaluation ({len(y_true)} games):")
    print(f"  Log loss:    {ll:.4f}")
    print(f"  Brier score: {br:.4f}")
    print(f"  ECE:         {ec:.4f}")
    print(f"  Accuracy:    {acc:.3f}")

    # ── Save artifact ─────────────────────────────────────────────────
    artifact = model.to_artifact()
    artifact.update({
        "log_loss": ll,
        "brier_score": br,
        "ece": ec,
        "accuracy": acc,
        "train_games": train.height,
        "test_games": test.height,
        "total_completed": completed.height,
    })

    artifact_dir = Path("config/models/challengers")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / "mlb-two-head-v1.json"
    with open(artifact_path, "w") as f:
        json.dump(artifact, f, indent=2, default=str)

    print(f"\nArtifact saved to {artifact_path}")
    print(f"  Model ID: {artifact['model_id']}")
    print(f"  Fitted:   {artifact['fitted']}")


if __name__ == "__main__":
    main()
