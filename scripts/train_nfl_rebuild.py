"""Train NFL rebuild model on scoreboard features.

Usage: python scripts/train_nfl_rebuild.py
Requires: NFL collector to have been run first.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from model_prediction.rebuild.validation import log_loss, brier_score, ece  # noqa: E402


def build_features(df: pl.DataFrame) -> pl.DataFrame:
    """Build NFL features: rolling points scored/allowed, win pct."""
    df = df.sort("event_start_utc")
    N = 10
    rows = []
    team_history: dict[str, list[dict]] = {}

    for row in df.iter_rows(named=True):
        home = row["home_team"]
        away = row["away_team"]
        h_score = row["home_score"]
        a_score = row["away_score"]

        def rolling(team: str, stat: str, default: float = 0.0) -> float:
            hist = team_history.get(team, [])
            recent = hist[-N:] if len(hist) >= N else hist
            if not recent:
                return default
            return sum(h[stat] for h in recent) / len(recent)

        rows.append({
            "event_id": row["event_id"],
            "home_team": home, "away_team": away,
            "home_score": h_score, "away_score": a_score,
            "total_pts": h_score + a_score,
            "home_margin": h_score - a_score,
            "home_off_rolling": rolling(home, "pts_scored", 23.0),
            "home_def_rolling": rolling(home, "pts_allowed", 23.0),
            "away_off_rolling": rolling(away, "pts_scored", 23.0),
            "away_def_rolling": rolling(away, "pts_allowed", 23.0),
            "home_win_pct": rolling(home, "win", 0.5),
            "away_win_pct": rolling(away, "win", 0.5),
            "event_start_utc": row["event_start_utc"],
        })

        for team, pf, pa, w in [
            (home, h_score, a_score, 1.0 if h_score > a_score else 0.0),
            (away, a_score, h_score, 1.0 if a_score > h_score else 0.0),
        ]:
            if team not in team_history:
                team_history[team] = []
            team_history[team].append({"pts_scored": pf, "pts_allowed": pa, "win": w})

    return pl.DataFrame(rows)


def main() -> None:
    path = Path("data/rebuild/normalized/nfl/scoreboard.parquet")
    if not path.exists():
        print(f"No NFL data at {path}. Run the collector first.")
        sys.exit(0)

    raw = pl.read_parquet(path)
    completed = raw.filter(raw["status"] == "STATUS_FINAL").sort("event_start_utc")
    print(f"NFL: {raw.height} rows, {completed.height} completed")

    if completed.height < 10:
        print("Not enough games to train (need >= 10)")
        sys.exit(0)

    feats = build_features(completed)
    split = int(feats.height * 0.8)
    train_df = feats[:split]
    test_df = feats[split:]
    print(f"Train: {train_df.height}, Test: {test_df.height}")

    # Simple logistic baseline using sklearn
    from sklearn.linear_model import LogisticRegression
    feature_cols = ["home_off_rolling", "home_def_rolling", "away_off_rolling",
                    "away_def_rolling", "home_win_pct", "away_win_pct"]
    X_train = train_df.select(feature_cols).to_numpy()
    y_train = [1 if r["home_score"] > r["away_score"] else 0 for r in train_df.iter_rows(named=True)]

    model = LogisticRegression(max_iter=1000, random_state=42)
    model.fit(X_train, y_train)

    y_true = []
    y_prob = []
    correct = 0
    for row in test_df.iter_rows(named=True):
        X = [[row[c] for c in feature_cols]]
        prob = float(model.predict_proba(X)[0][1])
        actual = 1 if row["home_score"] > row["away_score"] else 0
        y_true.append(actual)
        y_prob.append(prob)
        if (prob >= 0.5) == (actual == 1):
            correct += 1

    n = len(y_true)
    ll = log_loss(y_true, y_prob) if n > 0 else 1.0
    br = brier_score(y_true, y_prob) if n > 0 else 0.5
    ec = ece(y_true, y_prob) if n > 0 else 0.5
    acc = correct / max(1, n)

    print(f"\nNFL Test ({n} games):")
    print(f"  Log loss: {ll:.4f}  Brier: {br:.4f}  ECE: {ec:.4f}  Acc: {acc:.3f}")

    artifact = {"model_id": "nfl-lr-v1", "sport": "NFL",
                "log_loss": ll, "brier_score": br, "ece": ec, "accuracy": acc,
                "train_games": train_df.height, "test_games": n,
                "total_completed": completed.height}
    out = Path("config/models/challengers/nfl-lr-v1.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2, default=str))
    print(f"Artifact: {out}")


if __name__ == "__main__":
    main()
