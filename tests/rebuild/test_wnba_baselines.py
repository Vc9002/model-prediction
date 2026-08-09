from __future__ import annotations

import hashlib
import json
from pathlib import Path

import polars as pl
import pytest

from model_prediction.rebuild.wnba.baselines import (
    FEATURE_COLUMNS,
    chronological_date_folds,
    evaluate_research_baselines,
    fit_fold_models,
    load_research_baseline_dataset,
    predict_fold,
    write_research_baseline_artifacts,
)


def _baseline_frame() -> pl.DataFrame:
    rows = []
    teams = (("A", "B"), ("C", "D"))
    for day in range(1, 13):
        game_date = f"2026-05-{day:02d}"
        for game_index, (home, away) in enumerate(teams):
            home_wins = (day + game_index) % 2 == 0
            home_score = 88 + day + (6 if home_wins else 0)
            away_score = 88 + day + (0 if home_wins else 6)
            row = {
                "event_id": f"g-{day}-{game_index}",
                "game_date": game_date,
                "event_start_utc": f"{game_date}T22:00:00+00:00",
                "decision_time_utc": f"{game_date}T21:00:00+00:00",
                "home_team_id": home,
                "away_team_id": away,
                "home_score": home_score,
                "away_score": away_score,
                "availability_basis": "capture_time_only",
                "commercial_use_status": "unresolved",
                "production_allowed": False,
            }
            for feature_index, column in enumerate(FEATURE_COLUMNS):
                side_signal = 1.0 if column.startswith("home_") else -1.0
                row[column] = 1.0 + feature_index * 0.1 + day * 0.01 + side_signal * (
                    0.2 if home_wins else -0.2
                )
            rows.append(row)
    return pl.DataFrame(rows)


def test_chronological_folds_keep_dates_indivisible_and_strictly_ordered():
    dates = _baseline_frame()["game_date"].to_list()
    folds = chronological_date_folds(dates, n_splits=3, min_train_dates=3)
    assert len(folds) == 3
    for fold in folds:
        assert set(fold.train_dates).isdisjoint(fold.validation_dates)
        assert max(fold.train_dates) < min(fold.validation_dates)
        for date in set(dates):
            assert not (date in fold.train_dates and date in fold.validation_dates)


def test_oof_is_deterministic_same_sample_and_joint_scores_are_coherent():
    first = evaluate_research_baselines(_baseline_frame(), n_splits=3, min_train_dates=3)
    second = evaluate_research_baselines(_baseline_frame(), n_splits=3, min_train_dates=3)
    assert first.report == second.report
    assert first.oof.equals(second.oof)
    assert first.report["status"] == "RESEARCH_ONLY"
    assert first.report["qualification_status"] == "BLOCKED"
    assert first.report["production_allowed"] is False
    assert first.report["same_sample_n"] == first.oof.height
    assert set(first.report["models"]) == {
        "constant",
        "elo",
        "regularized_logistic",
        "linear_margin",
        "linear_total",
    }
    assert {model["n"] for model in first.report["models"].values()} == {first.oof.height}
    for row in first.oof.iter_rows(named=True):
        assert row["derived_home_score"] + row["derived_away_score"] == pytest.approx(
            row["linear_total_prediction"]
        )
        assert row["derived_home_score"] - row["derived_away_score"] == pytest.approx(
            row["linear_margin_prediction"]
        )


def test_future_outcome_change_cannot_change_any_earlier_oof_prediction():
    original = _baseline_frame()
    changed = original.with_columns(
        pl.when(pl.col("game_date") == "2026-05-12")
        .then(pl.col("home_score") + 50)
        .otherwise(pl.col("home_score"))
        .alias("home_score")
    )
    before = evaluate_research_baselines(original, n_splits=3, min_train_dates=3).oof
    after = evaluate_research_baselines(changed, n_splits=3, min_train_dates=3).oof
    prediction_columns = [
        column
        for column in before.columns
        if "probability" in column or column.endswith("_prediction")
    ]
    assert before.select(["event_id", *prediction_columns]).equals(
        after.select(["event_id", *prediction_columns])
    )


def test_fold_training_and_serving_use_the_exact_same_feature_transform():
    data = _baseline_frame()
    fold = chronological_date_folds(
        data["game_date"].to_list(), n_splits=3, min_train_dates=3,
    )[0]
    train = data.filter(pl.col("game_date").is_in(fold.train_dates))
    validation = data.filter(pl.col("game_date").is_in(fold.validation_dates)).sort(
        ["game_date", "event_start_utc", "event_id"]
    )
    served = predict_fold(fit_fold_models(train), validation)
    evaluated = evaluate_research_baselines(data, n_splits=3, min_train_dates=3).oof.filter(
        pl.col("fold") == 0
    )
    assert [row["logistic_home_win_probability"] for row in served] == pytest.approx(
        evaluated["logistic_home_win_probability"].to_list()
    )
    assert [row["linear_margin_prediction"] for row in served] == pytest.approx(
        evaluated["linear_margin_prediction"].to_list()
    )


def test_production_enabled_or_malformed_inputs_fail_closed():
    production = _baseline_frame().with_columns(pl.lit(True).alias("production_allowed"))
    with pytest.raises(ValueError, match="production-enabled"):
        evaluate_research_baselines(production)
    malformed = _baseline_frame().with_columns(pl.lit(float("nan")).alias(FEATURE_COLUMNS[0]))
    with pytest.raises(ValueError, match="missing or malformed"):
        evaluate_research_baselines(malformed)


def test_research_artifacts_are_immutable_idempotent_and_never_deployable(tmp_path):
    evaluation = evaluate_research_baselines(_baseline_frame(), n_splits=3, min_train_dates=3)
    first = write_research_baseline_artifacts(evaluation, tmp_path)
    second = write_research_baseline_artifacts(evaluation, tmp_path)
    assert first == second
    report = json.loads(Path(first["report"]).read_text())
    oof = pl.read_parquet(first["oof"])
    assert report["status"] == "RESEARCH_ONLY"
    assert report["qualification_status"] == "BLOCKED"
    assert report["commercial_use_status"] == "unresolved"
    assert oof["use_scope"].unique().to_list() == ["RESEARCH_ONLY"]
    assert oof["production_allowed"].unique().to_list() == [False]
    assert not list(tmp_path.rglob("*.pkl"))
    assert not list(tmp_path.rglob("*.joblib"))


def test_dataset_loader_uses_explicit_feature_hashes_and_normalized_final_labels(tmp_path):
    from model_prediction.rebuild.providers.base import canonical_json, dataframe_schema_hash
    from model_prediction.rebuild.storage import FeatureStore
    from model_prediction.rebuild.wnba.store import WNBANormalizedStore

    complete = _baseline_frame().filter(pl.col("game_date") == "2026-05-01")
    feature_rows = complete.drop("game_date", "home_score", "away_score").sort("event_id")
    snapshot_hash = hashlib.sha256(canonical_json({
        "game_date": "2026-05-01",
        "horizon": "late",
        "feature_schema_hash": dataframe_schema_hash(feature_rows),
        "rows": feature_rows.to_dicts(),
    })).hexdigest()
    FeatureStore(tmp_path / "features").write_snapshot(
        "wnba", "late", feature_rows, snapshot_hash,
    )
    game_rows = complete.select(
        "event_id",
        "event_start_utc",
        "home_team_id",
        "away_team_id",
        "home_score",
        "away_score",
    ).with_columns(
        pl.lit("2026-06-01T00:00:00+00:00").alias("observed_at_utc"),
        pl.lit(True).alias("completed"),
        pl.lit(True).alias("pit_eligible"),
    )
    WNBANormalizedStore(tmp_path / "normalized").write("games", 2026, game_rows)

    loaded = load_research_baseline_dataset(tmp_path, "late", [snapshot_hash])
    assert loaded.height == complete.height
    assert loaded["source_feature_snapshot_hash"].unique().to_list() == [snapshot_hash]
    assert loaded["home_score"].to_list() == complete["home_score"].to_list()
    with pytest.raises(ValueError, match="explicit feature snapshot hashes"):
        load_research_baseline_dataset(tmp_path, "late", [])
