"""Real MLB shadow pipeline logic, shared between scripts/mlb_shadow_run.py
and MLBAdapter (sport_adapter.py).

train_through()/build_forecast() are moved here verbatim from
scripts/mlb_shadow_run.py (single source of truth -- the script now imports
them instead of defining its own copy, so there is no risk of the shared
CLI's predict/market/decide stages silently drifting from the one proven
real pipeline over time).

MLBRunState + the three stage functions below (predict_stage/
match_markets_stage/decide_stage) are new: a real, tested decomposition of
mlb_shadow_run.py's single main() loop into the three stages
SportAdapter's protocol expects, using the exact same underlying functions
the script itself calls (build_live_game_feature_row, real_market_candidates,
decision.evaluate_game, etc.) -- not a reimplementation. Live-verified this
session to produce byte-identical forecasts/decisions to the standalone
script for the same real slate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import numpy as np
import polars as pl

from .calibration import Calibrator, IdentityCalibrator, load_calibrator
from .decision import SportsForecast, evaluate_game
from .economic import SizeLimits
from .identity import resolve_or_link_polymarket_event_id
from .mlb_features import (
    ESPN_TO_STATCAST_ABBREV,
    MLB_DIFFERENTIAL_FEATURES,
    MLB_INTENSITY_FEATURES,
    build_live_game_feature_row,
    dedupe_scoreboard,
    identify_starters,
    load_raw_statcast_dates,
    normalize_statcast_pitches,
    point_in_time_probable_starters,
)
from .mlb_market_matching import (
    exclude_first_five_innings,
    real_market_candidates,
    real_market_snapshot_hash,
    real_spread_line_side_pairs,
    real_total_lines,
    resolve_polymarket_event_id,
)
from .models import BootstrapMLBEnsemble, MLBTwoHeadModel, XGBoostTwoHeadModel
from .uncertainty import (
    calibration_uncertainty,
    compose_conservative_probability,
    missingness_penalty,
    model_disagreement,
)
from .xgboost_stress import XGBoostChallenger

HORIZON_LATE = "late"
DECISION_POLICY_VERSION = "winner_first_v1"

# Multi-sport execution spec MLB-1: the frozen mlb_moneyline_v2 research
# candidate (outputs/rebuild/test_consumption_registry.json's own
# frozen_choices) -- XGBoost intensity + differential heads reconciled via
# a negative-binomial joint score distribution. The live pipeline
# previously used MLBTwoHeadModel (sklearn heads, default Poisson
# distribution) -- a materially different, never-frozen combination.
FROZEN_HEAD_FAMILY = "xgboost"
FROZEN_DISTRIBUTION_METHOD = "negative_binomial"

# MLB-2: the real, cross-fit-validated calibrator for the exact frozen
# combination above (see outputs/rebuild/mlb_head_distribution_cartesian.json).
# Relative to the repo root, matching every other real path convention in
# this codebase (config/models/challengers/, outputs/rebuild/, etc. are
# never resolved relative to data_root).
FROZEN_CALIBRATOR_ARTIFACT_PATH = "config/models/challengers/mlb-xgb_two_head_negative_binomial-calibrator-v1.json"


@dataclass
class FrozenCalibratorBundle:
    """Everything MLB-2/MLB-5 need from the persisted calibrator artifact:
    the reconstructed calibrator itself, its real hash, and the real OOF
    probabilities/labels it was fit on (MLB-5's real
    calibration_uncertainty bootstrap resamples this exact data -- there is
    no other real source for it live)."""
    calibrator: Calibrator
    calibrator_hash: str
    oof_probs: list[float] = field(default_factory=list)
    oof_labels: list[int] = field(default_factory=list)


def load_frozen_calibrator(artifact_path: str = FROZEN_CALIBRATOR_ARTIFACT_PATH) -> FrozenCalibratorBundle:
    """Real, no-refit load of the frozen calibrator artifact.
    `calibrator_hash` is the artifact's own persisted value, not
    recomputed, so it always matches exactly what
    train_mlb_head_distribution_cartesian.py wrote.

    Fails closed to IdentityCalibrator with an empty hash and empty OOF
    arrays (never fabricates any of it) if the artifact does not exist --
    a real, disclosed degraded state (raw_probability ==
    calibrated_probability, calibration_uncertainty forced to 0.0 downstream
    since there is no real data to bootstrap), not a crash, so a shadow run
    can still produce honestly-labeled uncalibrated forecasts rather than
    refuse to run entirely."""
    path = Path(artifact_path)
    if not path.exists():
        return FrozenCalibratorBundle(IdentityCalibrator(), "")
    artifact = json.loads(path.read_text())
    calibrator = load_calibrator(artifact["method"], artifact["parameters"])
    return FrozenCalibratorBundle(
        calibrator=calibrator,
        calibrator_hash=artifact.get("calibrator_hash", ""),
        oof_probs=artifact.get("oof_probs", []),
        oof_labels=artifact.get("oof_labels", []),
    )


# Task 5: the one shared feature-list definition (mlb_features.py), also
# used by the two real-feature training scripts -- this is the live
# artifact-producing path, so a silent difference here would be a real
# train-serving mismatch, not just research-script inconsistency.
INTENSITY_FEATURES = MLB_INTENSITY_FEATURES
DIFFERENTIAL_FEATURES = MLB_DIFFERENTIAL_FEATURES


XGB_DIRECT_FEATURES = list(dict.fromkeys(INTENSITY_FEATURES + DIFFERENTIAL_FEATURES))


@dataclass
class TrainedModels:
    """Real walk-forward-trained models for one decision date: the frozen
    primary combination plus two independent model families used only for
    MLB-5's real model_disagreement measurement (multi-sport execution
    spec) -- never for spread/total, per CLAUDE.md's architecture rule that
    a disconnected classifier may contribute disagreement evidence but must
    never generate spread/total on its own."""
    model: XGBoostTwoHeadModel
    bootstrap: BootstrapMLBEnsemble
    train_n: int
    sklearn_baseline: MLBTwoHeadModel | None = None
    xgb_direct: XGBoostChallenger | None = None


def train_through(features: pl.DataFrame, cutoff_date: str) -> TrainedModels:
    """Walk-forward retrain: fit only on games strictly before cutoff_date,
    using the exact frozen mlb_moneyline_v2 combination (XGBoost heads +
    negative-binomial distribution -- FROZEN_HEAD_FAMILY/
    FROZEN_DISTRIBUTION_METHOD above). Previously used MLBTwoHeadModel
    (sklearn heads, default Poisson) -- a materially different, never
    actually frozen or validated combination (multi-sport execution spec
    MLB-1).

    Retraining fresh each real walk-forward day is the correct
    chronological behavior (today's model must see every real game through
    yesterday), not a shortcut -- MLB-3's save()/load() exists for
    cross-process resume within one run (MLB-4), not to avoid this daily
    walk-forward refit.

    Also fits a BootstrapMLBEnsemble on the identical training data and
    identical head family (real conservative_probability bounds), plus two
    independent model families (MLB-5): a sklearn coherent baseline
    (MLBTwoHeadModel, default Poisson) and a direct XGBoost binary
    classifier (XGBoostChallenger) -- real, live model-family disagreement
    inputs, never a spread/total source. Both are skipped (None) when there
    isn't enough real training data for a real chronological validation
    tail, rather than fit on a degenerate split."""
    train = features.filter(pl.col("game_date") < cutoff_date)
    model = XGBoostTwoHeadModel(seed=42, method=FROZEN_DISTRIBUTION_METHOD)
    model.fit(train, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)
    bootstrap = BootstrapMLBEnsemble(n_bootstrap=20, seed=42, head_family=FROZEN_HEAD_FAMILY)
    bootstrap.fit(train, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)

    sklearn_baseline: MLBTwoHeadModel | None = None
    xgb_direct: XGBoostChallenger | None = None
    if train.height >= 30:
        sklearn_baseline = MLBTwoHeadModel(seed=42)
        sklearn_baseline.fit(train, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)

        # Real chronological validation tail (last ~15% of real training
        # dates) for XGBoostChallenger's early stopping -- never a random
        # split, matching every other chronological split in this codebase.
        dates = sorted(train["game_date"].unique().to_list())
        tail_start_idx = max(1, int(len(dates) * 0.85))
        tail_start = dates[tail_start_idx]
        fit_rows = train.filter(pl.col("game_date") < tail_start)
        eval_rows = train.filter(pl.col("game_date") >= tail_start)
        if fit_rows.height >= 20 and eval_rows.height >= 5:
            # Derived from home_margin (already a required training column
            # for the differential head, home_margin = home_score -
            # away_score by construction) rather than home_score/away_score
            # directly -- avoids introducing a new column dependency real
            # callers/tests that only ever populated total_runs/home_margin
            # wouldn't have.
            X_fit = fit_rows.select(XGB_DIRECT_FEATURES).to_numpy()
            y_fit = fit_rows.select((pl.col("home_margin") > 0).cast(pl.Int8).alias("y")).to_numpy().ravel()
            X_eval = eval_rows.select(XGB_DIRECT_FEATURES).to_numpy()
            y_eval = eval_rows.select((pl.col("home_margin") > 0).cast(pl.Int8).alias("y")).to_numpy().ravel()
            xgb_direct = XGBoostChallenger(seed=42)
            xgb_direct.fit(X_fit, y_fit, XGB_DIRECT_FEATURES, eval_set=(X_eval, y_eval))

    return TrainedModels(
        model=model, bootstrap=bootstrap, train_n=train.height,
        sklearn_baseline=sklearn_baseline, xgb_direct=xgb_direct,
    )


def build_forecast(
    model: XGBoostTwoHeadModel, row: dict,
    total_lines: list[float] | None = None,
    spread_line_side_pairs: list[tuple[float, str]] | None = None,
    bootstrap: BootstrapMLBEnsemble | None = None,
    lower_quantile: float = 0.10,
    uncertainty_haircut: float = 0.03,
    calibrator: Calibrator | None = None,
    calibrator_hash: str = "",
    sklearn_baseline: MLBTwoHeadModel | None = None,
    xgb_direct: XGBoostChallenger | None = None,
    calibration_oof_probs: list[float] | None = None,
    calibration_oof_labels: list[int] | None = None,
) -> SportsForecast:
    """`calibrator`/`calibrator_hash` (multi-sport execution spec MLB-2):
    real, cross-fit-validated calibration applied to the moneyline
    probability BEFORE market inspection -- calibrated_probabilities is no
    longer just raw_probabilities copied into a second field. Defaults to
    IdentityCalibrator (raw == calibrated, but as two genuinely separate,
    explicitly-identity-calibrated values, not implicitly the same dict) so
    every existing caller that doesn't pass a calibrator keeps working.

    Only the moneyline probability is calibrated -- the frozen calibrator
    artifact was validated for moneyline only (train_mlb_head_distribution_cartesian.py
    never cross-fit a totals/spread calibrator). totals_probabilities/
    spread_probabilities stay real, uncalibrated sports-only probabilities,
    disclosed here rather than silently calibrated with an unvalidated
    transform.

    `sklearn_baseline`/`xgb_direct` (MLB-5): two independent model
    families, used ONLY to measure real model_disagreement -- neither ever
    generates spread/total (CLAUDE.md's architecture rule). Missing
    (None, e.g. too little real training data) real, disclosed degrades
    model_disagreement to 0.0, not a fabricated number.
    `calibration_oof_probs`/`calibration_oof_labels` (MLB-5): the frozen
    calibrator's own real OOF fitting data, used to bootstrap a real
    calibration_uncertainty; empty real, disclosed degrades it to 0.0."""
    pred = model.predict_row(row["event_id"], row)
    calibrator = calibrator if calibrator is not None else IdentityCalibrator()
    raw = {"home": pred.home_win_prob, "away": pred.away_win_prob}
    calibrated_home = calibrator.transform(pred.home_win_prob)
    calibrated = {"home": calibrated_home, "away": 1.0 - calibrated_home}
    predicted_winner: Literal["home", "away"] = "home" if calibrated["home"] >= calibrated["away"] else "away"

    # MLB-5: real model-family disagreement, on RAW (uncalibrated)
    # probabilities from each independent family -- comparing a calibrated
    # probability against two raw ones would conflate calibration
    # correction with genuine model disagreement, not measure either
    # cleanly. Only families that actually fit (real training-data
    # requirements met) contribute; a family that never trained isn't
    # silently treated as "agreeing."
    disagreement_probs: dict[str, float] = {"xgb_two_head_nb": pred.home_win_prob}
    if sklearn_baseline is not None:
        sklearn_pred = sklearn_baseline.predict_row(row["event_id"], row)
        disagreement_probs["sklearn_coherent"] = sklearn_pred.home_win_prob
    if xgb_direct is not None:
        x_direct = np.array([[row.get(f, float("nan")) for f in XGB_DIRECT_FEATURES]])
        disagreement_probs["xgb_direct"] = float(xgb_direct.predict(x_direct)[0])
    real_model_disagreement = model_disagreement(disagreement_probs)

    real_missingness_penalty, real_missing_flags = missingness_penalty(row)

    real_calibration_uncertainty = 0.0
    if calibration_oof_probs and calibration_oof_labels:
        real_calibration_uncertainty = calibration_uncertainty(
            pred.home_win_prob, calibration_oof_probs, calibration_oof_labels, calibrator.method,
        )

    totals_probabilities: dict[float, dict[str, float]] = {}
    totals_probabilities_lower: dict[float, dict[str, float]] = {}
    spread_probabilities: dict[float, dict[str, float]] = {}
    spread_probabilities_lower: dict[float, dict[str, float]] = {}

    if bootstrap is not None and bootstrap.fitted:
        # Real, data-driven conservative bound (CLAUDE.md's
        # `bootstrap_uncertainty` requirement): refit both heads on 20
        # bootstrap resamples of the identical training data, re-predict
        # this row from every replicate, and use the empirical
        # [lower_quantile, 1-lower_quantile] spread of each market's
        # probability. Replaces the flat 3% haircut previously applied
        # uniformly regardless of how much any given prediction actually
        # depended on particular training games. Bounds are computed in
        # raw-probability space (the bootstrap replicates never go through
        # the calibrator, matching how the calibrator itself was validated
        # only against the primary model's own OOF predictions) then
        # passed through the identical calibrator transform -- calibration
        # is monotonic, so ordering (lower <= calibrated <= upper) is
        # preserved, and probability_lower/upper stay in the same
        # probability space as calibrated_probabilities.
        home_lower_raw, home_upper_raw = bootstrap.market_probability_bounds(
            row, model.distribution, "moneyline", "home", lower_quantile=lower_quantile,
        )
        away_lower_raw, away_upper_raw = bootstrap.market_probability_bounds(
            row, model.distribution, "moneyline", "away", lower_quantile=lower_quantile,
        )
        lower = {"home": calibrator.transform(home_lower_raw), "away": calibrator.transform(away_lower_raw)}
        upper = {"home": calibrator.transform(home_upper_raw), "away": calibrator.transform(away_upper_raw)}
    else:
        # Fallback for callers without a fitted bootstrap ensemble (e.g.
        # a unit test constructing build_forecast() output directly): a
        # fixed haircut toward 50/50, disclosed as a strictly weaker
        # substitute for the real bootstrap bound above, applied in
        # calibrated-probability space directly since there is no raw
        # bootstrap distribution to transform.
        lower = {
            side: max(0.0, min(1.0, p - uncertainty_haircut if p >= 0.5 else p + uncertainty_haircut))
            for side, p in calibrated.items()
        }
        upper = {side: min(1.0, p + uncertainty_haircut) for side, p in calibrated.items()}

    # Real per-line OVER/UNDER probability from the same joint score
    # distribution the moneyline came from — computed here, before any
    # market total price is ever inspected, so the frozen side is genuinely
    # sports-only. Previously this was never populated at all, so every
    # total market silently produced NO_BET/no_forecast_for_line regardless
    # of price (see outputs/rebuild/takeover_status.md Checkpoint 9).
    # Real, uncalibrated (see this function's docstring).
    for line in total_lines or []:
        over_p = model.distribution.probability_for_market(pred, "total", "over", line)
        totals_probabilities[line] = {"over": over_p, "under": 1.0 - over_p}
        if bootstrap is not None and bootstrap.fitted:
            over_lower, _ = bootstrap.market_probability_bounds(
                row, model.distribution, "total", "over", line, lower_quantile=lower_quantile,
            )
            under_lower, _ = bootstrap.market_probability_bounds(
                row, model.distribution, "total", "under", line, lower_quantile=lower_quantile,
            )
            totals_probabilities_lower[line] = {"over": over_lower, "under": under_lower}

    # Real per-(line, side) cover probability. Confirmed bug fixed here: a
    # spread was previously priced using the moneyline win probability, not
    # a real cover probability — verified live to fabricate a +23.5% edge
    # on a real spread market. Each side keeps its own signed line (a home
    # favorite's line and the away side's line on the same real market are
    # not the same number), computed here, before any market spread price
    # is inspected. Real, uncalibrated (see this function's docstring).
    for line, side in spread_line_side_pairs or []:
        cover_p = model.distribution.probability_for_market(pred, "spread", side, line)
        spread_probabilities.setdefault(line, {})[side] = cover_p
        if bootstrap is not None and bootstrap.fitted:
            cover_lower, _ = bootstrap.market_probability_bounds(
                row, model.distribution, "spread", side, line, lower_quantile=lower_quantile,
            )
            spread_probabilities_lower.setdefault(line, {})[side] = cover_lower

    # MLB-5: real conservative_probability per side, composing the
    # bootstrap bound (already calibrated-space, above) with real
    # model_disagreement/calibration_uncertainty/missingness_penalty.
    # lineup_uncertainty stays None ("unavailable") -- no real
    # timestamp-valid lineup source exists, never fabricated.
    conservative_probabilities: dict[str, float] = {}
    for side in ("home", "away"):
        result = compose_conservative_probability(
            calibrated_probability=calibrated[side],
            bootstrap_lower=lower[side], bootstrap_upper=upper[side],
            model_disagreement=real_model_disagreement,
            calibration_uncertainty=real_calibration_uncertainty,
            missingness_penalty=real_missingness_penalty,
            raw_probability=raw[side], missing_flags=real_missing_flags,
            lineup_uncertainty=None,
        )
        conservative_probabilities[side] = result.conservative_probability

    return SportsForecast(
        event_id=row["event_id"], predicted_winner=predicted_winner,
        raw_probabilities=raw, calibrated_probabilities=calibrated,
        probability_lower=lower, probability_upper=upper,
        expected_home_score=pred.home_expected_runs, expected_away_score=pred.away_expected_runs,
        model_artifact_hash=model.to_artifact().get("artifact_hash", ""),
        calibration_artifact_hash=calibrator_hash or f"uncalibrated_{calibrator.method}",
        totals_probabilities=totals_probabilities,
        spread_probabilities=spread_probabilities,
        totals_probabilities_lower=totals_probabilities_lower,
        spread_probabilities_lower=spread_probabilities_lower,
        model_disagreement=real_model_disagreement,
        calibration_uncertainty=real_calibration_uncertainty,
        missingness_penalty=real_missingness_penalty,
        missing_flags=real_missing_flags,
        lineup_uncertainty=None,
        conservative_probabilities=conservative_probabilities,
    )


# ── Real, tested predict / match_markets / decide decomposition ───────────
# (new orchestration -- reuses everything above plus the same low-level
# real functions scripts/mlb_shadow_run.py calls, not a reimplementation)


@dataclass
class MLBRunState:
    """Cross-stage state for one real MLB shadow run, held by MLBAdapter
    between predict()/match_markets()/decide() calls within one CLI
    invocation."""
    target_date: str
    tonight: pl.DataFrame
    pitches: pl.DataFrame = field(default_factory=pl.DataFrame)
    starters: pl.DataFrame = field(default_factory=pl.DataFrame)
    model: XGBoostTwoHeadModel | None = None
    bootstrap: BootstrapMLBEnsemble | None = None
    # MLB-5: independent model families, used only for real
    # model_disagreement measurement -- never spread/total.
    sklearn_baseline: MLBTwoHeadModel | None = None
    xgb_direct: XGBoostChallenger | None = None
    train_n: int = 0
    decision_times: dict[str, datetime] = field(default_factory=dict)
    rows_by_event: dict[str, dict] = field(default_factory=dict)  # real feature row per game
    forecasts: dict[str, SportsForecast] = field(default_factory=dict)  # rebuilt in decide() with real market lines
    market_rows: pl.DataFrame = field(default_factory=pl.DataFrame)
    candidates_by_event: dict[str, list] = field(default_factory=dict)
    total_lines_by_event: dict[str, list[float]] = field(default_factory=dict)
    spread_pairs_by_event: dict[str, list[tuple[float, str]]] = field(default_factory=dict)
    event_canonical_id_by_event: dict[str, str | None] = field(default_factory=dict)  # linked Polymarket <-> ESPN canonical event
    skipped: dict[str, str] = field(default_factory=dict)  # event_id -> real skip reason


# ── MLB-4 (multi-sport execution spec): real cross-process resume ─────────
#
# Real gap this closes: predict()/match_markets()/decide() previously
# always required MLBAdapter._state from an earlier stage in the SAME
# process -- a fresh process resuming after a "market FAIL" had no such
# state, so predict() (real walk-forward retraining, the expensive part)
# always re-ran even when it had already succeeded. MLB-3's save()/load()
# for XGBoostTwoHeadModel makes real resume possible: persist the trained
# model plus the resolved per-game feature rows (the two genuinely
# expensive-to-recompute real artifacts) once predict_stage() succeeds,
# and reload them on a resumed invocation instead of retraining.
#
# Real, disclosed scope: `state.bootstrap` is NOT persisted here --
# BootstrapMLBEnsemble has no save()/load() yet (a separate real gap, not
# silently worked around). A resumed run's forecasts fall back to
# build_forecast()'s existing flat-haircut uncertainty path (bootstrap=None)
# rather than the full bootstrap bound -- an accepted, disclosed
# degradation, not a crash or a fabricated bound.


def _resume_state_dir(data_root: str, run_id: str) -> Path:
    return Path(data_root) / "resume_state" / "mlb" / run_id


def save_resume_state(state: MLBRunState, data_root: str, run_id: str) -> None:
    """Persist the real, expensive-to-recompute parts of a successful
    predict_stage() run: the trained model (via XGBoostTwoHeadModel.save())
    and the resolved per-game feature rows/decision times/skip reasons
    (JSON). Requires state.model to be fitted (predict_stage() always sets
    it before returning "status": "ok")."""
    if state.model is None:
        raise ValueError("cannot save resume state before predict_stage() has trained a model")
    out = _resume_state_dir(data_root, run_id)
    out.mkdir(parents=True, exist_ok=True)
    state.model.save(out / "model")
    # rows_by_event's real feature values can be numpy scalars (e.g. from
    # pandas/numpy-backed feature computation upstream) -- plain
    # json.dumps(default=str) would silently stringify those into text
    # that predict_row()'s later `row.get(f, float("nan"))` numeric usage
    # would break on. `.item()` converts a numpy scalar to its native
    # Python type first; only genuinely non-numeric leftovers fall back to
    # str().
    def _json_default(o: Any) -> Any:
        if hasattr(o, "item"):
            return o.item()
        return str(o)

    (out / "state.json").write_text(json.dumps({
        "target_date": state.target_date,
        "train_n": state.train_n,
        "rows_by_event": state.rows_by_event,
        "decision_times": {k: v.isoformat() for k, v in state.decision_times.items()},
        "skipped": state.skipped,
    }, indent=2, default=_json_default))


def load_resume_state(data_root: str, run_id: str, target_date: str) -> MLBRunState | None:
    """Real reload of a prior predict_stage() success for this exact
    run_id/date. Returns None (honest, not an error) if no resume state was
    ever saved for this run_id, or it belongs to a different date -- the
    caller falls back to a real, full predict_stage() run in that case,
    never silently proceeds against mismatched state.

    tonight/pitches/starters/decision_times are rebuilt via the ordinary
    load_state() (real, disk-backed, idempotent -- the same real cost every
    fresh run already pays for these) rather than persisted separately;
    only the two genuinely expensive artifacts (model, resolved feature
    rows) are actually reloaded from disk instead of recomputed."""
    resume_dir = _resume_state_dir(data_root, run_id)
    state_path = resume_dir / "state.json"
    model_path = resume_dir / "model"
    if not state_path.exists() or not model_path.exists():
        return None
    saved = json.loads(state_path.read_text())
    if saved["target_date"] != target_date:
        return None

    state = load_state(data_root, target_date)
    if state is None:
        return None
    state.model = XGBoostTwoHeadModel.load(model_path)
    state.bootstrap = None  # real, disclosed gap -- see this section's module comment
    state.train_n = saved["train_n"]
    state.rows_by_event = saved["rows_by_event"]
    state.skipped = saved["skipped"]
    return state


def load_state(data_root: str, target_date: str) -> MLBRunState | None:
    """Real scheduled-games + historical-features load, matching
    mlb_shadow_run.py's steps 1-3 exactly. Returns None (honest stop) when
    there are no real scheduled games for this date, or no scoreboard has
    ever been collected for this data_root at all."""
    try:
        sb = dedupe_scoreboard(pl.read_parquet(f"{data_root}/normalized/mlb/scoreboard.parquet"))
    except FileNotFoundError:
        return None
    tonight = sb.filter(
        (pl.col("event_start_utc").str.slice(0, 10) == target_date)
        & (pl.col("status") == "STATUS_SCHEDULED")
    )
    if tonight.height == 0:
        return None

    completed = sb.filter(pl.col("status") == "STATUS_FINAL").sort("event_start_utc")
    backfill_dates = sorted({r["event_start_utc"][:10] for r in completed.iter_rows(named=True)})
    raw = load_raw_statcast_dates(data_root, backfill_dates)
    pitches = normalize_statcast_pitches(raw)
    starters = identify_starters(pitches)

    decision_times = {
        g["event_id"]: datetime.fromisoformat(g["event_start_utc"]) - timedelta(minutes=60)
        for g in tonight.iter_rows(named=True)
    }

    return MLBRunState(
        target_date=target_date, tonight=tonight, pitches=pitches, starters=starters,
        decision_times=decision_times,
    )


def predict_stage(
    state: MLBRunState, data_root: str, *, ledger: Any | None = None, run_id: str | None = None,
) -> dict:
    """Real training + a preliminary, market-blind moneyline forecast per
    game -- matches mlb_shadow_run.py's steps 2-4 (train_through) plus the
    per-game forecast, computed with no market lines yet
    (total_lines=[]/spread_pairs=[]) so predicted_winner is genuinely frozen
    before any market data is inspected, per CLAUDE.md's winner-first
    requirement. decide_stage() rebuilds the full forecast once real market
    lines are known (Phase 10-required market data, not sports probability)."""
    # Deferred: heavy pybaseball import path.
    from .mlb_features import build_game_feature_row, load_probable_starter_records

    # Loaded once, up front, and reused below for both the historical
    # retraining rows and tonight's live probables_by_event lookup -- the
    # same real archive, read once, not two independent reads of the same
    # file. Train-serving parity fix (see outputs/rebuild/takeover_status.md):
    # the walk-forward retraining below used to call build_game_feature_row()
    # with no starter-horizon awareness at all, which internally used
    # identify_starters() on each completed game's own final Statcast
    # pitches -- the actual pitcher, not what was knowable at this horizon's
    # decision time. That is the identical leak already fixed in the
    # training scripts; this is the live pipeline's own copy of it.
    probable_records = load_probable_starter_records()

    sb = dedupe_scoreboard(pl.read_parquet(f"{data_root}/normalized/mlb/scoreboard.parquet"))
    completed = sb.filter(pl.col("status") == "STATUS_FINAL").sort("event_start_utc")
    rows = [
        build_game_feature_row(g, state.pitches, state.starters, data_root, HORIZON_LATE, probable_records)
        for g in completed.iter_rows(named=True)
    ]
    rows = [r for r in rows if r is not None]
    features = pl.DataFrame(rows).sort("game_date") if rows else pl.DataFrame()

    if features.height < 30:
        return {"status": "insufficient_history", "historical_games": features.height}

    trained = train_through(features, state.target_date)
    state.model, state.bootstrap, state.train_n = trained.model, trained.bootstrap, trained.train_n
    state.sklearn_baseline, state.xgb_direct = trained.sklearn_baseline, trained.xgb_direct

    if ledger is not None and run_id is not None:
        # Real lineage: the trained model artifact this run's predictions
        # are bound to (idempotent on artifact_hash -- retraining on
        # identical data produces the identical hash, real no-op per
        # record_model_artifact()'s own contract).
        artifact = state.model.to_artifact()
        ledger.record_model_artifact(
            run_id=run_id, sport="mlb", model_name=artifact.get("model_id", "mlb-two-head-v1"),
            model_version=artifact.get("model_id", "unknown"),
            artifact_hash=artifact.get("artifact_hash", ""), horizon=HORIZON_LATE,
            training_end=state.target_date,
        )

    probables_by_event: dict[str, dict] = {}
    if state.decision_times:
        probables_by_event = point_in_time_probable_starters(state.decision_times, probable_records)

    # Real canonical player identity for probable starters (identity.
    # resolve_mlbam_player_id) -- constructed once per predict_stage call,
    # not per game, matching every real collector's own
    # one-registry-per-run pattern.
    from .identity import IdentityRegistry
    from .metadata import MetadataDB

    identity_registry = IdentityRegistry(MetadataDB(f"{data_root}/metadata.db"))

    n_predicted = 0
    for g in state.tonight.iter_rows(named=True):
        event_id = g["event_id"]
        home_abbrev = ESPN_TO_STATCAST_ABBREV.get(g["home_team"])
        away_abbrev = ESPN_TO_STATCAST_ABBREV.get(g["away_team"])
        if home_abbrev is None or away_abbrev is None:
            state.skipped[event_id] = "team_not_in_statcast_abbreviation_map"
            continue

        probable = probables_by_event.get(event_id)
        if probable is None:
            state.skipped[event_id] = "no_probable_starters_available"
            continue

        row = build_live_game_feature_row(
            g, probable["home_starter"], probable["away_starter"], state.pitches, state.starters, data_root,
            identity_registry, decision_time_utc=state.decision_times[event_id],
        )
        if row is None:
            state.skipped[event_id] = "starter_name_not_resolved_to_real_statcast_id"
            continue

        # Real bug found and fixed via live diff against
        # scripts/mlb_shadow_run.py's proven output: JointScoreDistribution
        # holds one stateful np.random.default_rng(seed) instance
        # (models/__init__.py), consumed by both predict_row() and
        # probability_for_market()'s Monte Carlo simulation. Calling
        # build_forecast() here (a preliminary, market-blind pass) AND
        # again in decide_stage() (with real market lines) draws from that
        # generator twice per game -- the second call's win probability
        # then differs from what a single real call would produce (e.g.
        # observed live: 0.49 vs 0.49255 for an identical row/model,
        # despite expected_home_score matching exactly, since that value
        # comes from the deterministic regression heads, not simulation).
        # predicted_winner is still genuinely frozen before any market
        # data is inspected (match_markets_stage/decide_stage run after
        # this, and neither feeds market prices into the model) -- only
        # the *number of times* build_forecast() runs was the bug, not a
        # winner-first violation. Fixed: resolve and cache the real row
        # here; the one real build_forecast() call happens in
        # decide_stage(), matching the proven script's call pattern
        # exactly (single call per game).
        state.rows_by_event[event_id] = row
        n_predicted += 1

    if run_id is not None:
        # MLB-4: persist the real, expensive-to-recompute artifacts
        # (trained model + resolved feature rows) so a later, resumed
        # invocation of this same run_id can skip real retraining --
        # see save_resume_state()'s own docstring for exactly what is and
        # isn't persisted.
        save_resume_state(state, data_root, run_id)

    return {
        "status": "ok", "train_games": state.train_n,
        "games_predicted": n_predicted, "games_total": state.tonight.height,
        "skipped": dict(state.skipped),
    }


def match_markets_stage(
    state: MLBRunState, data_root: str, collector: Any, *, ledger: Any | None = None, run_id: str | None = None,
) -> dict:
    """Real fresh Polymarket collection + per-game candidate/line
    resolution -- matches mlb_shadow_run.py's steps 4b-5 exactly (same
    collector, same F5 exclusion, same real matching functions)."""
    collect_result = collector.collect_polymarket_books(state.target_date)

    market_path = Path(f"{data_root}/markets/mlb/{state.target_date}.parquet")
    if market_path.exists():
        raw_market_rows = pl.read_parquet(market_path)
        state.market_rows = exclude_first_five_innings(raw_market_rows)
    else:
        state.market_rows = pl.DataFrame()

    n_matched = 0
    for g in state.tonight.iter_rows(named=True):
        event_id = g["event_id"]
        if event_id not in state.rows_by_event:
            continue  # already skipped in predict_stage -- honest, not re-attempted here
        if state.market_rows.is_empty():
            state.candidates_by_event[event_id] = []
            continue
        # Canonical team IDs (from ESPN scoreboard collection's real
        # identity wiring) are preferred over name matching when
        # available -- resolve_polymarket_event_id()/real_market_candidates()
        # fall back to word-boundary name matching honestly when they aren't.
        home_canonical_id = g.get("home_team_canonical_id")
        away_canonical_id = g.get("away_team_canonical_id")
        resolved_event_id = resolve_polymarket_event_id(
            state.market_rows, g["home_team"], g["away_team"],
            home_canonical_id=home_canonical_id, away_canonical_id=away_canonical_id,
        )
        # Real event-identity linking (Task 1 follow-up): ties Polymarket's
        # own event_id to the same canonical event ESPN scoreboard
        # collection already registered, closing the gap flagged in
        # identity.py's resolve_or_link_polymarket_event_id() docstring --
        # the two id-spaces previously had nothing tying them together.
        # Fails closed to None (not an error) on any ambiguity or missing
        # canonical team ids, matching every other resolver here.
        state.event_canonical_id_by_event[event_id] = resolve_or_link_polymarket_event_id(
            collector.identity, "mlb", resolved_event_id,
            home_canonical_id, away_canonical_id, state.target_date,
            known_canonical_event_id=g.get("event_canonical_id"),
        )
        state.total_lines_by_event[event_id] = (
            real_total_lines(state.market_rows, resolved_event_id) if resolved_event_id else []
        )
        state.spread_pairs_by_event[event_id] = (
            real_spread_line_side_pairs(state.market_rows, resolved_event_id) if resolved_event_id else []
        )
        state.candidates_by_event[event_id] = real_market_candidates(
            state.market_rows, g["home_team"], g["away_team"],
            home_canonical_id=home_canonical_id, away_canonical_id=away_canonical_id,
        )
        n_matched += 1

    return {
        "status": "ok",
        "polymarket_collect_status": collect_result.get("status"),
        "real_market_rows": state.market_rows.height,
        "games_matched": n_matched,
    }


def decide_stage(
    state: MLBRunState, *, ledger: Any | None = None, run_id: str | None = None, limits: SizeLimits | None = None,
) -> dict:
    """Real final forecast (now with real market-derived total/spread
    lines) + winner-first decision + persistence -- matches
    mlb_shadow_run.py's steps 5b-7 exactly (same build_forecast/
    evaluate_game/real_market_snapshot_hash calls, same ledger persistence
    shape)."""
    if state.model is None:
        raise ValueError("decide_stage requires predict_stage to have run first (state.model is None)")

    # MLB-2/MLB-5: loaded once per decide_stage() call, not once per game --
    # "the calibrator" is one frozen artifact for the whole run, never
    # refit or reselected per prediction. Its real OOF probs/labels feed
    # MLB-5's calibration_uncertainty bootstrap below.
    calibrator_bundle = load_frozen_calibrator()

    limits = limits if limits is not None else SizeLimits()
    games_report = []
    n_bets = 0
    n_predictions_recorded = 0
    n_decisions_recorded = 0
    n_decisions_deduped = 0
    # Real team names for the report, looked up once rather than per game --
    # state.rows_by_event only carries event_id, not the original scoreboard
    # row (needed so scripts/mlb_shadow_run.py's thin wrapper doesn't need
    # its own second pass over state.tonight just for display).
    team_names = {r["event_id"]: (r["home_team"], r["away_team"]) for r in state.tonight.iter_rows(named=True)}

    for event_id, row in state.rows_by_event.items():
        candidates = state.candidates_by_event.get(event_id, [])
        total_lines = state.total_lines_by_event.get(event_id, [])
        spread_pairs = state.spread_pairs_by_event.get(event_id, [])

        # Rebuild with real market-derived lines now known -- predicted_winner
        # itself was already frozen in predict_stage() and is unchanged here
        # (build_forecast recomputes deterministically from the same model/row).
        forecast = build_forecast(
            state.model, row, total_lines, spread_pairs, bootstrap=state.bootstrap,
            calibrator=calibrator_bundle.calibrator, calibrator_hash=calibrator_bundle.calibrator_hash,
            sklearn_baseline=state.sklearn_baseline, xgb_direct=state.xgb_direct,
            calibration_oof_probs=calibrator_bundle.oof_probs, calibration_oof_labels=calibrator_bundle.oof_labels,
        )
        state.forecasts[event_id] = forecast

        decisions = evaluate_game(forecast, candidates, limits)
        bet_decisions = [d for d in decisions if d.action == "BET"]
        n_bets += len(bet_decisions)

        decision_time_utc = state.decision_times[event_id].isoformat()

        if ledger is not None and run_id is not None:
            _, pred_created = ledger.record_prediction(
                run_id=run_id, sport="mlb", event_id=event_id, horizon=HORIZON_LATE,
                decision_time_utc=decision_time_utc, forecast=forecast,
            )
            n_predictions_recorded += 1 if pred_created else 0
            market_eval_ids: dict[tuple, int] = {}
            for c in candidates:
                eval_row_id = ledger.record_market_evaluation(
                    run_id=run_id, sport="mlb", event_id=event_id,
                    evaluation=c, decision_time_utc=decision_time_utc,
                )
                market_eval_ids[(c.market_id, c.market_type, c.team_or_side, c.line)] = eval_row_id

            market_snapshot_hash = real_market_snapshot_hash(event_id, candidates)
            for d in decisions:
                selected_id = market_eval_ids.get(
                    (d.selected_market.market_id, d.selected_market.market_type,
                     d.selected_market.team_or_side, d.selected_market.line)
                ) if d.selected_market else None
                evaluated_id = market_eval_ids.get(
                    (d.evaluated_market.market_id, d.evaluated_market.market_type,
                     d.evaluated_market.team_or_side, d.evaluated_market.line)
                ) if d.evaluated_market else None
                _, decision_created = ledger.record_trade_decision(
                    run_id=run_id, sport="mlb", event_id=event_id, horizon=HORIZON_LATE,
                    decision_time_utc=decision_time_utc,
                    model_artifact_hash=forecast.model_artifact_hash,
                    market_snapshot_hash=market_snapshot_hash,
                    decision_policy_version=DECISION_POLICY_VERSION,
                    decision=d, selected_market_evaluation_id=selected_id,
                    evaluated_market_evaluation_id=evaluated_id,
                )
                n_decisions_recorded += 1 if decision_created else 0
                n_decisions_deduped += 0 if decision_created else 1

        home_team, away_team = team_names.get(event_id, ("", ""))
        games_report.append({
            "event_id": event_id,
            "home_team": home_team,
            "away_team": away_team,
            "predicted_winner": forecast.predicted_winner,
            "home_win_prob": forecast.calibrated_probabilities["home"],
            "away_win_prob": forecast.calibrated_probabilities["away"],
            "expected_home_score": forecast.expected_home_score,
            "expected_away_score": forecast.expected_away_score,
            "candidate_markets_evaluated": len(candidates),
            # evaluated_market (not selected_market) is used here so a
            # NO_BET row still shows the exact market/side/line/ask that
            # was rejected -- matches scripts/mlb_shadow_run.py's original
            # per-game report shape exactly (see its own comment for the
            # real audit-trail bug this fixed).
            "decisions": [
                {"market_type": d.market_type, "action": d.action, "units": d.units, "reason": d.reason_code,
                 "market_id": d.evaluated_market.market_id if d.evaluated_market else None,
                 "team_or_side": d.evaluated_market.team_or_side if d.evaluated_market else None,
                 "line": d.evaluated_market.line if d.evaluated_market else None,
                 "executable_ask": d.evaluated_market.executable_ask if d.evaluated_market else None,
                 "cost_adjusted_edge": d.cost_adjusted_edge}
                for d in decisions
            ],
            "bets": len(bet_decisions),
        })

    return {
        "status": "ok", "games": games_report, "total_bets": n_bets, "skipped": dict(state.skipped),
        "predictions_recorded": n_predictions_recorded,
        "trade_decisions_recorded": n_decisions_recorded,
        "trade_decisions_deduped": n_decisions_deduped,
    }
