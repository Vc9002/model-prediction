"""Train Tennis rebuild model on scoreboard features.

Usage: python scripts/train_tennis_rebuild.py
Requires: Tennis collector to have been run first.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from model_prediction.rebuild.validation import log_loss, brier_score, ece  # noqa: E402


def build_features(df: pl.DataFrame) -> pl.DataFrame:
    df = df.sort("event_start_utc")
    N, rows = 15, []
    player_history: dict[str, list[dict]] = {}

    for row in df.iter_rows(named=True):
        home, away = row["home_team"], row["away_team"]
        h_score, a_score = row["home_score"], row["away_score"]

        def rolling(player: str, stat: str, default: float = 0.0) -> float:
            hist = player_history.get(player, [])
            recent = hist[-N:] if len(hist) >= N else hist
            return sum(h[stat] for h in recent) / len(recent) if recent else default

        rows.append({
            "event_id": row["event_id"], "home_player": home, "away_player": away,
            "home_score": h_score, "away_score": a_score,
            "home_margin": h_score - a_score,
            "home_win_rate": rolling(home, "win", 0.5),
            "away_win_rate": rolling(away, "win", 0.5),
            "home_recent_wins": rolling(home, "recent", 0.5),
            "away_recent_wins": rolling(away, "recent", 0.5),
            "event_start_utc": row["event_start_utc"],
        })

        for player, won in [(home, 1.0 if h_score > a_score else 0.0),
                             (away, 1.0 if a_score > h_score else 0.0)]:
            if player not in player_history:
                player_history[player] = []
            player_history[player].append({"win": won, "recent": won})

    return pl.DataFrame(rows)


def main() -> None:
    path = Path("data/rebuild/normalized/tennis/scoreboard.parquet")
    if not path.exists():
        print(f"No tennis data at {path}. Run the collector first.")
        sys.exit(0)

    raw = pl.read_parquet(path)
    completed = raw.filter(raw["status"] == "STATUS_FINAL").sort("event_start_utc")
    print(f"Tennis: {raw.height} rows, {completed.height} completed")

    if completed.height < 10:
        print("Not enough matches to train (need >= 10)")
        sys.exit(0)

    feats = build_features(completed)
    split = int(feats.height * 0.8)
    train_df, test_df = feats[:split], feats[split:]
    print(f"Train: {train_df.height}, Test: {test_df.height}")

    from sklearn.linear_model import LogisticRegression
    feature_cols = ["home_win_rate", "away_win_rate", "home_recent_wins", "away_recent_wins"]
    X_train = train_df.select(feature_cols).to_numpy()
    y_train = [1 if r["home_score"] > r["away_score"] else 0 for r in train_df.iter_rows(named=True)]
    model = LogisticRegression(max_iter=1000, random_state=42).fit(X_train, y_train)

    y_true, y_prob, correct = [], [], 0
    for row in test_df.iter_rows(named=True):
        prob = float(model.predict_proba([[row[c] for c in feature_cols]])[0][1])
        actual = 1 if row["home_score"] > row["away_score"] else 0
        y_true.append(actual); y_prob.append(prob)
        if (prob >= 0.5) == (actual == 1): correct += 1

    n = len(y_true)
    ll = log_loss(y_true, y_prob) if n > 0 else 1.0
    br = brier_score(y_true, y_prob) if n > 0 else 0.5
    ec = ece(y_true, y_prob) if n > 0 else 0.5
    print(f"\nTennis Test ({n} matches): Log loss: {ll:.4f}  Brier: {br:.4f}  ECE: {ec:.4f}  Acc: {correct/max(1,n):.3f}")

    artifact = {"model_id": "tennis-lr-v1", "sport": "TENNIS", "log_loss": ll,
                "brier_score": br, "ece": ec, "accuracy": correct / max(1, n),
                "train_games": train_df.height, "test_games": n, "total_completed": completed.height}
    out = Path("config/models/challengers/tennis-lr-v1.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2, default=str))
    print(f"Artifact: {out}")


if __name__ == "__main__":
    main()
