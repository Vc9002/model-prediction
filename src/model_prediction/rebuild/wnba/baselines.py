"""Deterministic research-only WNBA baseline comparison.

This module evaluates controls; it does not create a deployable challenger.
SportsDataverse historical captures are capture-time-only and upstream data
rights remain unresolved, so every result is qualification-blocked.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np
import polars as pl
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from model_prediction.rebuild.basic_elo import EloModel
from model_prediction.rebuild.providers.base import canonical_json, dataframe_schema_hash
from model_prediction.rebuild.storage import FeatureStore
from model_prediction.rebuild.validation import brier_score, log_loss

from .store import WNBANormalizedStore

FEATURE_COLUMNS = (
    "home_season_ortg",
    "home_season_drtg",
    "home_season_netrtg",
    "home_season_pace",
    "home_season_efg",
    "home_season_tov_pct",
    "home_season_orb_pct",
    "home_season_ft_rate",
    "away_season_ortg",
    "away_season_drtg",
    "away_season_netrtg",
    "away_season_pace",
    "away_season_efg",
    "away_season_tov_pct",
    "away_season_orb_pct",
    "away_season_ft_rate",
)
REQUIRED_COLUMNS = (
    "event_id",
    "sports_event_date",
    "event_start_utc",
    "decision_time_utc",
    "horizon",
    "home_team_id",
    "away_team_id",
    "home_team_canonical_id",
    "away_team_canonical_id",
    "home_score",
    "away_score",
    "source_feature_snapshot_hash",
    "source_manifest_hash",
    "availability_basis",
    "commercial_use_status",
    "production_allowed",
    *FEATURE_COLUMNS,
)
RESEARCH_SCOPE = "RESEARCH_ONLY"
QUALIFICATION_STATUS = "BLOCKED"


@dataclass(frozen=True)
class DateFold:
    fold_index: int
    train_dates: tuple[str, ...]
    validation_dates: tuple[str, ...]

    @property
    def train_end(self) -> str:
        return self.train_dates[-1]

    @property
    def validation_start(self) -> str:
        return self.validation_dates[0]


@dataclass
class FoldModels:
    constant_probability: float
    expanding_home_base_probability: float
    elo: EloModel
    logistic: Pipeline
    margin: Pipeline
    total: Pipeline
    margin_residual_std: float
    margin_total_residual_covariance: tuple[tuple[float, float], tuple[float, float]]
    use_scope: str = RESEARCH_SCOPE
    qualification_status: str = QUALIFICATION_STATUS
    production_allowed: bool = False


@dataclass(frozen=True)
class BaselineEvaluation:
    report: dict[str, Any]
    oof: pl.DataFrame


def load_research_baseline_dataset(
    data_root: str | Path,
    horizon: str,
    feature_snapshot_hashes: list[str],
) -> pl.DataFrame:
    """Join explicit immutable feature snapshots to later final-score labels.

    Snapshot hashes are mandatory; this never trains from a moving ``latest``
    pointer. Labels come from the normalized game store only after features
    have already been frozen at their decision cutoffs.
    """

    if not feature_snapshot_hashes:
        raise ValueError("WNBA baseline research requires explicit feature snapshot hashes")
    root = Path(data_root)
    feature_store = FeatureStore(root / "features")
    frames: list[pl.DataFrame] = []
    for snapshot_hash in feature_snapshot_hashes:
        frame = feature_store.read_version("wnba", horizon, snapshot_hash).sort("event_id")
        if "sports_event_date" not in frame.columns:
            raise ValueError("WNBA feature snapshot is missing canonical sports_event_date")
        snapshot_dates = frame["sports_event_date"].unique().to_list()
        if len(snapshot_dates) != 1:
            raise ValueError("WNBA feature snapshot must contain exactly one event date")
        computed_hash = hashlib.sha256(canonical_json({
            "game_date": snapshot_dates[0],
            "horizon": horizon,
            "feature_schema_hash": dataframe_schema_hash(frame),
            "rows": frame.to_dicts(),
        })).hexdigest()
        if computed_hash != snapshot_hash:
            raise ValueError(f"WNBA feature snapshot failed dataset-hash verification: {snapshot_hash}")
        frames.append(frame.with_columns(pl.lit(snapshot_hash).alias("source_feature_snapshot_hash")))
    features = pl.concat(frames, how="vertical_relaxed")
    if features["event_id"].n_unique() != features.height:
        raise ValueError("WNBA baseline feature snapshots contain duplicate events")
    seasons = sorted({int(value[:4]) for value in features["sports_event_date"].to_list()})
    normalized = WNBANormalizedStore(root / "normalized")
    labels: list[pl.DataFrame] = []
    for season in seasons:
        games = normalized.read_latest("games", season)
        if games.is_empty():
            continue
        labels.append(
            games.filter(pl.col("completed")).select(
                "event_id",
                pl.col("home_team_id").alias("label_home_team_id"),
                pl.col("away_team_id").alias("label_away_team_id"),
                pl.col("home_team_canonical_id").alias("label_home_team_canonical_id"),
                pl.col("away_team_canonical_id").alias("label_away_team_canonical_id"),
                pl.col("event_start_utc").alias("label_event_start_utc"),
                pl.col("observed_at_utc").alias("label_observed_at_utc"),
                pl.col("raw_snapshot_hash").alias("label_raw_snapshot_hash"),
                pl.col("availability_basis").alias("label_availability_basis"),
                pl.col("commercial_use_status").alias("label_commercial_use_status"),
                pl.col("production_allowed").alias("label_production_allowed"),
                "home_score",
                "away_score",
            )
        )
    if not labels:
        raise ValueError("WNBA baseline feature snapshots have no completed normalized labels")
    label_frame = pl.concat(labels, how="vertical_relaxed")
    if label_frame["event_id"].n_unique() != label_frame.height:
        raise ValueError("WNBA normalized labels contain duplicate latest events")
    joined = features.join(label_frame, on="event_id", how="left")
    if joined.filter(pl.col("home_score").is_null() | pl.col("away_score").is_null()).height:
        raise ValueError("WNBA baseline feature snapshots are missing completed score labels")
    if joined.filter(
        (pl.col("home_team_id") != pl.col("label_home_team_id"))
        | (pl.col("away_team_id") != pl.col("label_away_team_id"))
        | (pl.col("home_team_canonical_id") != pl.col("label_home_team_canonical_id"))
        | (pl.col("away_team_canonical_id") != pl.col("label_away_team_canonical_id"))
        | (pl.col("event_start_utc") != pl.col("label_event_start_utc"))
    ).height:
        raise ValueError("WNBA feature/label event identities disagree")
    label_gate_columns = (
        "label_observed_at_utc",
        "label_raw_snapshot_hash",
        "label_availability_basis",
        "label_commercial_use_status",
        "label_production_allowed",
    )
    if any(joined[column].null_count() for column in label_gate_columns):
        raise ValueError("WNBA final labels have missing provenance")
    if joined.filter(
        (pl.col("label_availability_basis") != "capture_time_only")
        | (pl.col("label_commercial_use_status") != "unresolved")
        | (pl.col("label_production_allowed") != False)
        | (pl.col("label_raw_snapshot_hash").cast(pl.String).str.len_chars() == 0)
    ).height:
        raise ValueError("WNBA final labels violate research-only provenance")
    label_observed = joined["label_observed_at_utc"].str.to_datetime(time_zone="UTC", strict=False)
    label_starts = joined["label_event_start_utc"].str.to_datetime(time_zone="UTC", strict=False)
    if label_observed.null_count() or label_starts.null_count() or any(
        observed <= start for observed, start in zip(label_observed, label_starts, strict=True)
    ):
        raise ValueError("WNBA final labels must be observed after event start")
    return joined.drop(
        "label_home_team_id",
        "label_away_team_id",
        "label_home_team_canonical_id",
        "label_away_team_canonical_id",
        "label_event_start_utc",
        "label_availability_basis",
        "label_commercial_use_status",
        "label_production_allowed",
    ).sort(["sports_event_date", "event_start_utc", "event_id"])


def chronological_date_folds(
    dates: list[str],
    *,
    n_splits: int = 4,
    min_train_dates: int = 3,
) -> list[DateFold]:
    """Expanding folds over indivisible, complete calendar dates."""

    unique_dates = sorted(set(dates))
    if n_splits < 1 or min_train_dates < 1:
        raise ValueError("WNBA fold parameters must be positive")
    if len(unique_dates) <= min_train_dates:
        return []
    validation_dates = unique_dates[min_train_dates:]
    blocks = [list(block) for block in np.array_split(validation_dates, n_splits) if len(block)]
    folds: list[DateFold] = []
    prior_dates = unique_dates[:min_train_dates]
    for index, block in enumerate(blocks):
        folds.append(DateFold(index, tuple(prior_dates), tuple(block)))
        prior_dates = [*prior_dates, *block]
    return folds


def _validate_frame(frame: pl.DataFrame) -> pl.DataFrame:
    missing = sorted(set(REQUIRED_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"WNBA baseline frame missing required columns: {missing}")
    if frame.is_empty():
        raise ValueError("WNBA baseline frame is empty")
    ordered = frame.sort(["sports_event_date", "event_start_utc", "event_id"])
    if ordered["event_id"].n_unique() != ordered.height:
        raise ValueError("WNBA baseline frame contains duplicate events")
    if ordered.select(pl.col("sports_event_date").str.to_date(strict=False).is_null().any()).item():
        raise ValueError("WNBA baseline frame contains malformed sports event dates")
    gate_columns = (
        "production_allowed",
        "commercial_use_status",
        "availability_basis",
        "source_feature_snapshot_hash",
        "source_manifest_hash",
        "home_team_canonical_id",
        "away_team_canonical_id",
        "horizon",
    )
    if any(ordered[column].null_count() for column in gate_columns):
        raise ValueError("WNBA baseline frame contains null rights, identity, or provenance")
    if ordered.filter(pl.col("production_allowed") != False).height:
        raise ValueError("WNBA research baselines refuse production-enabled source rows")
    if ordered.filter(pl.col("commercial_use_status") != "unresolved").height:
        raise ValueError("WNBA research baselines require unresolved commercial-use status")
    if ordered.filter(pl.col("availability_basis") != "capture_time_only").height:
        raise ValueError("WNBA research baselines require capture-time-only provenance")
    for column in (
        "source_feature_snapshot_hash",
        "source_manifest_hash",
        "home_team_canonical_id",
        "away_team_canonical_id",
        "horizon",
    ):
        if ordered.filter(pl.col(column).cast(pl.String).str.len_chars() == 0).height:
            raise ValueError(f"WNBA baseline frame contains empty provenance: {column}")
    for column in (*FEATURE_COLUMNS, "home_score", "away_score"):
        values = ordered[column].cast(pl.Float64, strict=False)
        if values.null_count() or not all(math.isfinite(value) for value in values.to_list()):
            raise ValueError(f"WNBA baseline column is missing or malformed: {column}")
    if ordered.filter(pl.col("home_score") == pl.col("away_score")).height:
        raise ValueError("WNBA moneyline labels cannot contain tied scores")
    observed_decisions = ordered["decision_time_utc"].str.to_datetime(
        time_zone="UTC", strict=False,
    )
    starts = ordered["event_start_utc"].str.to_datetime(time_zone="UTC", strict=False)
    if observed_decisions.null_count() or starts.null_count():
        raise ValueError("WNBA baseline timestamps must be valid UTC-aware values")
    if any(decision >= start for decision, start in zip(observed_decisions, starts, strict=True)):
        raise ValueError("WNBA baseline decision times must precede event starts")
    derived_dates = starts.dt.convert_time_zone("America/New_York").dt.strftime("%Y-%m-%d")
    if derived_dates.to_list() != ordered["sports_event_date"].to_list():
        raise ValueError("WNBA sports_event_date disagrees with league-local event start")
    return ordered


def feature_matrix(frame: pl.DataFrame) -> np.ndarray:
    """The one fixed transformation used in fold training and inference."""

    missing = sorted(set(FEATURE_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"WNBA serve row missing baseline features: {missing}")
    matrix = frame.select(FEATURE_COLUMNS).to_numpy().astype(float)
    if matrix.ndim != 2 or matrix.shape[1] != len(FEATURE_COLUMNS):
        raise ValueError("WNBA baseline feature matrix has the wrong shape")
    if not np.isfinite(matrix).all():
        raise ValueError("WNBA baseline feature matrix contains non-finite values")
    return matrix


def _pipeline(model: LogisticRegression | Ridge) -> Pipeline:
    return Pipeline([("scale", StandardScaler()), ("model", model)])


def fit_fold_models(train: pl.DataFrame) -> FoldModels:
    train = _validate_frame(train)
    X = feature_matrix(train)
    home_win = (train["home_score"].to_numpy() > train["away_score"].to_numpy()).astype(int)
    if len(set(home_win.tolist())) < 2:
        raise ValueError("WNBA logistic baseline needs both moneyline outcome classes")
    margin_target = train["home_score"].to_numpy().astype(float) - train["away_score"].to_numpy().astype(float)
    total_target = train["home_score"].to_numpy().astype(float) + train["away_score"].to_numpy().astype(float)

    logistic = _pipeline(LogisticRegression(
        C=1.0, solver="lbfgs", max_iter=2000, random_state=42,
    ))
    margin = _pipeline(Ridge(alpha=10.0))
    total = _pipeline(Ridge(alpha=10.0))
    logistic.fit(X, home_win)
    margin.fit(X, margin_target)
    total.fit(X, total_target)
    residuals = margin_target - margin.predict(X)
    residual_std = max(float(np.std(residuals, ddof=1)), 1.0) if len(residuals) > 1 else 1.0
    total_residuals = total_target - total.predict(X)
    if len(residuals) > 1:
        covariance = np.cov(np.column_stack([residuals, total_residuals]), rowvar=False, ddof=1)
        eigenvalues, eigenvectors = np.linalg.eigh(covariance)
        covariance = eigenvectors @ np.diag(np.maximum(eigenvalues, 1e-6)) @ eigenvectors.T
        covariance[0, 0] = max(float(covariance[0, 0]), 1.0)
        covariance[1, 1] = max(float(covariance[1, 1]), 1.0)
    else:
        covariance = np.eye(2)

    elo_games = train.select(
        pl.col("home_team_canonical_id").alias("home_team"),
        pl.col("away_team_canonical_id").alias("away_team"),
        "home_score",
        "away_score",
    )
    elo = EloModel().fit(elo_games)
    return FoldModels(
        0.5,
        float(home_win.mean()),
        elo,
        logistic,
        margin,
        total,
        residual_std,
        (
            (float(covariance[0, 0]), float(covariance[0, 1])),
            (float(covariance[1, 0]), float(covariance[1, 1])),
        ),
    )


def predict_fold(models: FoldModels, validation: pl.DataFrame) -> list[dict[str, Any]]:
    """Predict all baselines from the exact same validated feature rows."""

    validation = _validate_frame(validation)
    X = feature_matrix(validation)
    logistic_prob = models.logistic.predict_proba(X)[:, 1]
    margin = models.margin.predict(X)
    total = models.total.predict(X)
    normal = NormalDist()
    output: list[dict[str, Any]] = []
    for index, row in enumerate(validation.iter_rows(named=True)):
        elo_prob = models.elo.predict(
            str(row["home_team_canonical_id"]), str(row["away_team_canonical_id"]),
        )[0]
        predicted_margin = float(margin[index])
        predicted_total = float(total[index])
        home_points = (predicted_total + predicted_margin) / 2.0
        away_points = (predicted_total - predicted_margin) / 2.0
        covariance = models.margin_total_residual_covariance
        margin_variance = covariance[0][0]
        total_variance = covariance[1][1]
        margin_total_covariance = covariance[0][1]
        home_variance = (total_variance + margin_variance + 2.0 * margin_total_covariance) / 4.0
        away_variance = (total_variance + margin_variance - 2.0 * margin_total_covariance) / 4.0
        home_away_covariance = (total_variance - margin_variance) / 4.0
        home_std = math.sqrt(max(home_variance, 0.0))
        away_std = math.sqrt(max(away_variance, 0.0))
        home_away_correlation = (
            home_away_covariance / (home_std * away_std)
            if home_std > 0.0 and away_std > 0.0
            else 0.0
        )
        margin_probability = normal.cdf(predicted_margin / math.sqrt(margin_variance))
        output.append({
            "constant_home_win_probability": models.constant_probability,
            "expanding_home_base_probability": models.expanding_home_base_probability,
            "elo_home_win_probability": elo_prob,
            "logistic_home_win_probability": float(logistic_prob[index]),
            "linear_margin_home_win_probability": float(margin_probability),
            "linear_margin_prediction": predicted_margin,
            "linear_total_prediction": predicted_total,
            "derived_home_score": home_points,
            "derived_away_score": away_points,
            "joint_residual_family": "bivariate_gaussian_margin_total",
            "margin_residual_std": math.sqrt(margin_variance),
            "total_residual_std": math.sqrt(total_variance),
            "margin_total_residual_covariance": margin_total_covariance,
            "derived_home_score_std": home_std,
            "derived_away_score_std": away_std,
            "derived_home_away_correlation": home_away_correlation,
            "use_scope": RESEARCH_SCOPE,
            "qualification_status": QUALIFICATION_STATUS,
            "production_allowed": False,
        })
    return output


def _moneyline_metrics(oof: pl.DataFrame, probability_column: str) -> dict[str, Any]:
    labels = oof["home_win"].to_list()
    probabilities = oof[probability_column].to_list()
    return {
        "n": oof.height,
        "log_loss": log_loss(labels, probabilities),
        "brier": brier_score(labels, probabilities),
        "accuracy": float(np.mean((np.asarray(probabilities) >= 0.5) == np.asarray(labels))),
    }


def _regression_metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, Any]:
    errors = predicted - actual
    return {
        "n": len(actual),
        "mae": float(np.mean(np.abs(errors))),
        "rmse": float(np.sqrt(np.mean(errors**2))),
    }


def evaluate_research_baselines(
    frame: pl.DataFrame,
    *,
    n_splits: int = 4,
    min_train_dates: int = 3,
) -> BaselineEvaluation:
    data = _validate_frame(frame)
    folds = chronological_date_folds(
        data["sports_event_date"].to_list(),
        n_splits=n_splits,
        min_train_dates=min_train_dates,
    )
    if not folds:
        raise ValueError("WNBA baseline dataset has too few complete dates for OOF evaluation")

    records: list[dict[str, Any]] = []
    fold_records: list[dict[str, Any]] = []
    for fold in folds:
        train = data.filter(pl.col("sports_event_date").is_in(fold.train_dates))
        validation = data.filter(pl.col("sports_event_date").is_in(fold.validation_dates))
        if train.is_empty() or validation.is_empty():
            raise ValueError("WNBA chronological fold unexpectedly has an empty side")
        if fold.train_end >= fold.validation_start:
            raise ValueError("WNBA chronological fold leaks validation dates into training")
        models = fit_fold_models(train)
        predictions = predict_fold(models, validation)
        for row, prediction in zip(validation.iter_rows(named=True), predictions, strict=True):
            home_score = float(row["home_score"])
            away_score = float(row["away_score"])
            records.append({
                "event_id": str(row["event_id"]),
                "sports_event_date": str(row["sports_event_date"]),
                "horizon": str(row["horizon"]),
                "home_team_canonical_id": str(row["home_team_canonical_id"]),
                "away_team_canonical_id": str(row["away_team_canonical_id"]),
                "source_feature_snapshot_hash": str(row["source_feature_snapshot_hash"]),
                "source_manifest_hash": str(row["source_manifest_hash"]),
                "fold": fold.fold_index,
                "home_win": int(home_score > away_score),
                "actual_margin": home_score - away_score,
                "actual_total": home_score + away_score,
                **prediction,
                "use_scope": RESEARCH_SCOPE,
                "qualification_status": QUALIFICATION_STATUS,
                "production_allowed": False,
            })
        fold_records.append({
            "fold": fold.fold_index,
            "train_dates": list(fold.train_dates),
            "validation_dates": list(fold.validation_dates),
            "train_n": train.height,
            "validation_n": validation.height,
        })

    oof = pl.DataFrame(records).sort(["sports_event_date", "event_id"])
    dataset_hash = hashlib.sha256(canonical_json(data.to_dicts())).hexdigest()
    oof_hash = hashlib.sha256(canonical_json(oof.to_dicts())).hexdigest()
    moneyline_columns = {
        "constant_0_5": "constant_home_win_probability",
        "expanding_home_base_rate": "expanding_home_base_probability",
        "elo": "elo_home_win_probability",
        "regularized_logistic": "logistic_home_win_probability",
    }
    same_sample_n = oof.height
    margin_actual = oof["actual_margin"].to_numpy()
    total_actual = oof["actual_total"].to_numpy()
    model_reports: dict[str, dict[str, Any]] = {
        **{
            name: {**_moneyline_metrics(oof, column), "use_scope": RESEARCH_SCOPE}
            for name, column in moneyline_columns.items()
        },
        "linear_margin": {
            **_moneyline_metrics(oof, "linear_margin_home_win_probability"),
            **_regression_metrics(
                margin_actual, oof["linear_margin_prediction"].to_numpy(),
            ),
            "use_scope": RESEARCH_SCOPE,
        },
        "linear_total": {
            **_regression_metrics(
                total_actual, oof["linear_total_prediction"].to_numpy(),
            ),
            "use_scope": RESEARCH_SCOPE,
        },
    }
    report: dict[str, Any] = {
        "sport": "wnba",
        "status": RESEARCH_SCOPE,
        "qualification_status": QUALIFICATION_STATUS,
        "production_allowed": False,
        "commercial_use_status": "unresolved",
        "availability_basis": "capture_time_only",
        "qualification_blockers": [
            "historical source captures are capture_time_only, not retrospective PIT evidence",
            "SportsDataverse/ESPN upstream commercial-use rights are unresolved",
        ],
        "dataset_hash": dataset_hash,
        "oof_hash": oof_hash,
        "same_sample_n": same_sample_n,
        "horizons": sorted(set(data["horizon"].to_list())),
        "sports_date_start": min(data["sports_event_date"].to_list()),
        "sports_date_end": max(data["sports_event_date"].to_list()),
        "feature_snapshot_hashes": sorted(set(data["source_feature_snapshot_hash"].to_list())),
        "source_manifest_hashes": sorted(set(data["source_manifest_hash"].to_list())),
        "model_parameters": {
            "elo": {"k_factor": 20.0, "home_advantage": 65.0, "initial_rating": 1500.0},
            "logistic": {"C": 1.0, "solver": "lbfgs", "max_iter": 2000},
            "ridge": {"alpha": 10.0},
        },
        "folds": fold_records,
        "models": model_reports,
    }
    if any(model["n"] != same_sample_n for model in model_reports.values()):
        raise RuntimeError("WNBA baseline comparison did not use one common OOF sample")
    return BaselineEvaluation(report, oof)


def write_research_baseline_artifacts(
    evaluation: BaselineEvaluation,
    output_root: str | Path,
) -> dict[str, str]:
    """Persist immutable report/OOF evidence; never serialize a deployable model."""

    root = Path(output_root) / "wnba_baselines"
    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(canonical_json({
        "report": evaluation.report,
        "oof_hash": evaluation.report["oof_hash"],
    })).hexdigest()
    report_path = root / f"{digest}.json"
    oof_path = root / f"{digest}.parquet"
    report_bytes = json.dumps(evaluation.report, indent=2, sort_keys=True).encode("utf-8")
    oof_buffer = io.BytesIO()
    evaluation.oof.write_parquet(oof_buffer)

    def write_once(path: Path, payload: bytes) -> None:
        if path.exists():
            if path.read_bytes() != payload:
                raise ValueError(f"conflicting immutable WNBA baseline artifact: {path}")
            return
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with tmp.open("wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(tmp, path)
            except FileExistsError:
                if path.read_bytes() != payload:
                    raise ValueError(f"conflicting immutable WNBA baseline artifact: {path}")
        finally:
            tmp.unlink(missing_ok=True)

    write_once(report_path, report_bytes)
    write_once(oof_path, oof_buffer.getvalue())
    return {"report": str(report_path), "oof": str(oof_path)}
