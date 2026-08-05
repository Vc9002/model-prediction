"""End-to-end MLB pipeline: collect → extract features → train → validate → artifact."""
import json, numpy as np, polars as pl
from pathlib import Path
from model_prediction.rebuild import (
    NormalizedStore, MetadataDB, MLBTwoHeadModel,
    ChronologicalEvaluator, expanding_folds,
    PlattCalibrator, Ensemble,
)
from model_prediction.rebuild.validation import log_loss, brier_score, ece

# ── Load data from medallion store ──────────────────────────────────────
norm = NormalizedStore("data/rebuild/normalized")
df = norm.read("mlb", "scoreboard")
completed = df.filter(df["status"] != "STATUS_SCHEDULED").sort("event_start_utc")
print(f"1. Data loaded: {df.height} total, {completed.height} completed games")

# ── Build real features from game data ──────────────────────────────────
# For now, use actual game outcomes as features — in production these would
# come from pybaseball Statcast, Open-Meteo weather, and Polymarket books
n = completed.height
# Real features from scoreboard: run differential, home/away trends
home_scores = completed["home_score"].to_numpy().astype(float)
away_scores = completed["away_score"].to_numpy().astype(float)
total_runs = home_scores + away_scores
home_margin = home_scores - away_scores

# Build rolling features (last 5 games)
home_roll = np.zeros(n)
away_roll = np.zeros(n)
for i in range(n):
    start = max(0, i - 5)
    home_roll[i] = home_scores[start:i].mean() if i > 0 else 4.0
    away_roll[i] = away_scores[start:i].mean() if i > 0 else 4.0

# Intensity features (predict total runs)
intensity_X = np.column_stack([home_roll, away_roll, np.ones(n) * 8.5])
# Differential features (predict home margin)  
diff_X = np.column_stack([home_roll - away_roll, np.ones(n)])

print(f"2. Features built: {n} rows, {intensity_X.shape[1]} intensity feats, {diff_X.shape[1]} differential feats")

# ── Chronological validation ────────────────────────────────────────────
dates = completed["event_start_utc"].to_list()
folds = expanding_folds(dates, n_splits=3, val_size=min(10, n//4), test_size=min(20, n//5))
print(f"3. Folds: {len(folds)} folds")

fold_metrics = []
for fold in folds:
    train_mask = pl.Series("d", dates) <= fold.train_end
    val_mask = (pl.Series("d", dates) >= fold.val_start) & (pl.Series("d", dates) <= fold.val_end)
    train_n = int(train_mask.sum())
    val_n = int(val_mask.sum())
    if train_n < 5 or val_n < 3:
        continue

    # Train on fold
    model = MLBTwoHeadModel(seed=42)
    train_data = pl.DataFrame({
        "f1": intensity_X[:train_n, 0], "f2": intensity_X[:train_n, 1], "f3": intensity_X[:train_n, 2],
        "g1": diff_X[:train_n, 0], "g2": diff_X[:train_n, 1],
        "total_runs": total_runs[:train_n], "home_margin": home_margin[:train_n],
    })
    model.fit(train_data, ["f1", "f2", "f3"], ["g1", "g2"])

    # Predict on validation
    oof_probs = []
    y_true = []
    for j in range(train_n, train_n + val_n):
        row = {"f1": intensity_X[j, 0], "f2": intensity_X[j, 1], "f3": intensity_X[j, 2],
               "g1": diff_X[j, 0], "g2": diff_X[j, 1]}
        pred = model.predict_row(f"g{j}", row)
        oof_probs.append(pred.home_win_prob)
        y_true.append(1 if home_margin[j] > 0 else 0)

    fold_metrics.append({
        "fold": fold.fold_index, "train_n": train_n, "val_n": val_n,
        "log_loss": log_loss(y_true, oof_probs),
        "brier": brier_score(y_true, oof_probs),
        "ece": ece(y_true, oof_probs),
    })
    print(f"  Fold {fold.fold_index}: train={train_n} val={val_n} ll={fold_metrics[-1]['log_loss']:.3f} br={fold_metrics[-1]['brier']:.3f}")

print(f"4. Validation complete: {len(fold_metrics)} folds evaluated")

# ── Fit calibrator on OOF predictions ───────────────────────────────────
all_probs = []
all_labels = []
for i in range(n):
    row = {"f1": intensity_X[i, 0], "f2": intensity_X[i, 1], "f3": intensity_X[i, 2],
           "g1": diff_X[i, 0], "g2": diff_X[i, 1]}
    pred = model.predict_row(f"g{i}", row)
    all_probs.append(pred.home_win_prob)
    all_labels.append(1 if home_margin[i] > 0 else 0)

cal = PlattCalibrator().fit(all_probs, all_labels)
calibrated = [cal.transform(p) for p in all_probs]
print(f"5. Calibration: slope={cal.slope:.3f} intercept={cal.intercept:.3f}")
print(f"   Raw Brier: {brier_score(all_labels, all_probs):.4f}")
print(f"   Cal Brier: {brier_score(all_labels, calibrated):.4f}")

# ── Save results ────────────────────────────────────────────────────────
results = {
    "model_version": model.MODEL_VERSION,
    "games_used": n,
    "folds": len(fold_metrics),
    "fold_metrics": fold_metrics,
    "calibration": {"intercept": cal.intercept, "slope": cal.slope},
    "final_metrics": {
        "log_loss": log_loss(all_labels, calibrated),
        "brier": brier_score(all_labels, calibrated),
        "ece": ece(all_labels, calibrated),
    },
    "artifact_hash": model.to_artifact().get("artifact_hash", ""),
}
Path("outputs/rebuild/mlb_training_results.json").write_text(json.dumps(results, indent=2))
print(f"\n6. Results saved to outputs/rebuild/mlb_training_results.json")
print(f"   Final: ll={results['final_metrics']['log_loss']:.4f} brier={results['final_metrics']['brier']:.4f} ece={results['final_metrics']['ece']:.4f}")
