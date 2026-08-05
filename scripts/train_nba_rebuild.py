"""Train NBA/WNBA rebuild model using the BasketballModel architecture.

Usage:
    python scripts/train_nba_rebuild.py nba   # train NBA model
    python scripts/train_nba_rebuild.py wnba  # train WNBA model

Requires the rebuild collector to have been run first:
    python -c "from model_prediction.rebuild.collectors import NBACollector; ..."
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.rebuild.models.basketball import BasketballModel  # noqa: E402
from model_prediction.rebuild.validation import log_loss, brier_score, ece  # noqa: E402


def build_features(df: pl.DataFrame, sport: str) -> pl.DataFrame:
    """Build basketball features from completed scoreboard data.

    Features:
      - Rolling offensive efficiency (points per game, last N)
      - Rolling defensive efficiency (points allowed per game, last N)
      - Rolling pace (possessions estimate from total points)
      - Home/away splits
      - Recent form (win pct last N)
    """
    df = df.sort("event_start_utc")
    N = 10  # rolling window

    rows: list[dict] = []
    team_history: dict[str, list[dict]] = {}

    for row in df.iter_rows(named=True):
        home = row["home_team"]
        away = row["away_team"]
        home_score = row["home_score"]
        away_score = row["away_score"]

        def rolling(team: str, stat: str, default: float = 0.0) -> float:
            history = team_history.get(team, [])
            recent = history[-N:] if len(history) >= N else history
            if not recent:
                return default
            return sum(h[stat] for h in recent) / len(recent)

        home_off = rolling(home, "pts_scored", 110.0)
        home_def = rolling(home, "pts_allowed", 110.0)
        away_off = rolling(away, "pts_scored", 110.0)
        away_def = rolling(away, "pts_allowed", 110.0)
        home_pace = rolling(home, "pace", 100.0)
        away_pace = rolling(away, "pace", 100.0)
        home_wp = rolling(home, "win", 0.5)
        away_wp = rolling(away, "win", 0.5)

        total_pts = home_score + away_score
        home_margin = home_score - away_score

        rows.append({
            "event_id": row["event_id"],
            "home_team": home,
            "away_team": away,
            "home_score": home_score,
            "away_score": away_score,
            "total_pts": total_pts,
            "home_margin": home_margin,
            "event_start_utc": row["event_start_utc"],
            # Pace features
            "home_pace": home_pace,
            "away_pace": away_pace,
            # Home offense features
            "home_off_rolling": home_off,
            "home_off_vs_avg": home_off - 110.0,
            # Away offense features
            "away_off_rolling": away_off,
            "away_off_vs_avg": away_off - 110.0,
            # Home defense features
            "home_def_rolling": home_def,
            "home_def_vs_avg": home_def - 110.0,
            # Away defense features
            "away_def_rolling": away_def,
            "away_def_vs_avg": away_def - 110.0,
            # Form
            "home_win_pct": home_wp,
            "away_win_pct": away_wp,
        })

        # Update history AFTER predicting (no leakage)
        for team, pts_for, pts_against, win in [
            (home, home_score, away_score, 1.0 if home_score > away_score else 0.0),
            (away, away_score, home_score, 1.0 if away_score > home_score else 0.0),
        ]:
            if team not in team_history:
                team_history[team] = []
            team_history[team].append({
                "pts_scored": pts_for,
                "pts_allowed": pts_against,
                "pace": pts_for + pts_against,
                "win": win,
            })

    return pl.DataFrame(rows)


def train(sport: str, data_path: str | None = None) -> dict:
    """Train the basketball model for a given sport."""
    sport_lower = sport.lower()
    sport_upper = sport.upper()

    # Use rebuild normalized data if available, fall back to production data
    rebuild_path = Path(f"data/rebuild/normalized/{sport_lower}/scoreboard.parquet")

    if rebuild_path.exists():
        raw = pl.read_parquet(rebuild_path)
    elif data_path:
        raw = pl.read_parquet(data_path)
    else:
        print(f"ERROR: No data found at {rebuild_path}. Run the collector first:")
        print(f"  collector = NBACollector('data/rebuild', meta)")
        print(f"  collector.collect_date('2026-08-05', sport='{sport_lower}')")
        sys.exit(1)

    completed = raw.filter(raw["status"] == "STATUS_FINAL").sort("event_start_utc")
    print(f"{sport_upper}: {raw.height} total rows, {completed.height} completed games")

    if completed.height < 20:
        print(f"Not enough completed games for {sport_upper} (need ≥20)")
        sys.exit(0)

    features = build_features(completed, sport_lower)

    split_idx = int(features.height * 0.8)
    train_df = features[:split_idx]
    test_df = features[split_idx:]
    print(f"Train: {train_df.height}, Test: {test_df.height}")

    # Prepare data arrays for the BasketballModel
    pace_features = ["home_pace", "away_pace"]
    off_features = ["home_off_rolling", "home_off_vs_avg"]
    def_features = ["home_def_rolling", "home_def_vs_avg"]

    data = {
        "pace_X": train_df.select(pace_features).to_numpy(),
        "pace_y": train_df["total_pts"].to_numpy(),
        "home_off_X": train_df.select(off_features).to_numpy(),
        "home_off_y": train_df["home_score"].to_numpy(),
        "away_off_X": train_df.select(["away_off_rolling", "away_off_vs_avg"]).to_numpy(),
        "away_off_y": train_df["away_score"].to_numpy(),
        "home_def_X": train_df.select(def_features).to_numpy(),
        "home_def_y": train_df["away_score"].to_numpy(),
        "away_def_X": train_df.select(["away_def_rolling", "away_def_vs_avg"]).to_numpy(),
        "away_def_y": train_df["home_score"].to_numpy(),
    }

    model = BasketballModel(sport=sport_upper.lower(), seed=42)
    model.fit(data)
    print(f"Trained {sport_upper} BasketballModel on {train_df.height} games")

    # Evaluate
    y_true: list[int] = []
    y_prob: list[float] = []
    correct = 0

    for row in test_df.iter_rows(named=True):
        pred = model.predict(
            row["event_id"],
            np.array([row["home_pace"], row["away_pace"]]),
            np.array([row["home_off_rolling"], row["home_off_vs_avg"]]),
            np.array([row["away_off_rolling"], row["away_off_vs_avg"]]),
            np.array([row["home_def_rolling"], row["home_def_vs_avg"]]),
            np.array([row["away_def_rolling"], row["away_def_vs_avg"]]),
        )
        actual_home_win = 1 if row["home_score"] > row["away_score"] else 0
        y_true.append(actual_home_win)
        y_prob.append(pred.home_win_prob)
        if (pred.home_win_prob >= 0.5) == (actual_home_win == 1):
            correct += 1

    n_test = len(y_true)
    ll = log_loss(y_true, y_prob) if n_test > 0 else 1.0
    br = brier_score(y_true, y_prob) if n_test > 0 else 0.5
    ec = ece(y_true, y_prob) if n_test > 0 else 0.5
    acc = correct / max(1, n_test)

    print(f"\n{sport_upper} Test set ({n_test} games):")
    print(f"  Log loss:    {ll:.4f}")
    print(f"  Brier score: {br:.4f}")
    print(f"  ECE:         {ec:.4f}")
    print(f"  Accuracy:    {acc:.3f}")

    # Save artifact
    artifact = {
        "model_id": f"{sport_lower}-possessions-v1",
        "sport": sport_upper,
        "log_loss": ll,
        "brier_score": br,
        "ece": ec,
        "accuracy": acc,
        "train_games": train_df.height,
        "test_games": n_test,
        "total_completed": completed.height,
        "features": {
            "pace": pace_features,
            "offense": off_features,
            "defense": def_features,
        },
    }

    artifact_dir = Path("config/models/challengers")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"{sport_lower}-possessions-v1.json"
    with open(artifact_path, "w") as f:
        json.dump(artifact, f, indent=2, default=str)
    print(f"\nArtifact saved to {artifact_path}")

    return artifact


if __name__ == "__main__":
    sport = sys.argv[1] if len(sys.argv) > 1 else "nba"
    train(sport)
