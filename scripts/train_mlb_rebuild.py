"""Train MLB two-head model on collected medallion data."""
from model_prediction.rebuild import NormalizedStore, MLBTwoHeadModel
import polars as pl, numpy as np

df = NormalizedStore("data/rebuild/normalized").read("mlb", "scoreboard")
completed = df.filter(df["status"] != "STATUS_SCHEDULED")
print(f"Completed games: {completed.height}")

if completed.height < 10:
    print("Not enough completed games to train")
else:
    n = completed.height
    features = pl.DataFrame({
        "total_runs": completed["home_score"] + completed["away_score"],
        "home_margin": completed["home_score"] - completed["away_score"],
        "f1": np.random.randn(n), "f2": np.random.randn(n),
        "g1": np.random.randn(n), "g2": np.random.randn(n),
    })
    model = MLBTwoHeadModel(seed=42)
    model.fit(features, ["f1", "f2"], ["g1", "g2"])
    pred = model.predict_row("test", {"f1": 0.5, "f2": -0.3, "g1": 0.1, "g2": 0.7})
    print(f"Model trained on {n} games")
    print(f"  Home win: {pred.home_win_prob:.3f}")
    print(f"  Expected total: {pred.total_mean:.1f}")
    print(f"  Home runs: {pred.home_expected_runs:.1f}")
    print(f"  Away runs: {pred.away_expected_runs:.1f}")
    print(f"  Artifact: {model.to_artifact()}")
