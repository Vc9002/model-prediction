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

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import numpy as np
import polars as pl

from .calibration import Calibrator, IdentityCalibrator
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
from .mlb_v2_artifact import (
    FROZEN_CALIBRATOR_ARTIFACT_NAME,  # noqa: F401 -- compatibility re-export
    FROZEN_DISTRIBUTION_METHOD,
    FROZEN_HEAD_FAMILY,
    MLB_V2_CANDIDATE_VERSION,
    MLB_V2_TEST_ID,
    FrozenCalibratorBundle,
    FrozenMLBV2Anchor,
    FrozenMLBV2Bundle,
    load_frozen_calibrator,  # noqa: F401 -- compatibility re-export
    load_frozen_mlb_v2_bundle,
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
DECISION_POLICY_VERSION = "winner_first_market_blend_v2"

FROZEN_CALIBRATOR_ARTIFACT_PATH = (
    "config/models/challengers/mlb-xgb_two_head_negative_binomial-calibrator-v1.json"
)


# Task 5: the one shared feature-list definition (mlb_features.py), also
# used by the two real-feature training scripts -- this is the live
# artifact-producing path, so a silent difference here would be a real
# train-serving mismatch, not just research-script inconsistency.
INTENSITY_FEATURES = MLB_INTENSITY_FEATURES
DIFFERENTIAL_FEATURES = MLB_DIFFERENTIAL_FEATURES


XGB_DIRECT_FEATURES = list(dict.fromkeys(INTENSITY_FEATURES + DIFFERENTIAL_FEATURES))


def _utc_now_dt() -> datetime:
    """One non-overridable runtime clock seam; tests patch this private helper."""
    return datetime.now(UTC)


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
        model=model,
        bootstrap=bootstrap,
        train_n=train.height,
        sklearn_baseline=sklearn_baseline,
        xgb_direct=xgb_direct,
    )


def build_forecast(
    model: XGBoostTwoHeadModel,
    row: dict,
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
    model_artifact_hash: str | None = None,
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
            pred.home_win_prob,
            calibration_oof_probs,
            calibration_oof_labels,
            calibrator.method,
        )

    totals_probabilities: dict[float, dict[str, float]] = {}
    totals_probabilities_lower: dict[float, dict[str, float]] = {}
    spread_probabilities: dict[float, dict[str, float]] = {}
    spread_probabilities_lower: dict[float, dict[str, float]] = {}
    totals_outcomes: dict[float, dict[str, float]] = {}
    spread_outcomes: dict[float, dict[str, float]] = {}

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
            row,
            model.distribution,
            "moneyline",
            "home",
            lower_quantile=lower_quantile,
        )
        away_lower_raw, away_upper_raw = bootstrap.market_probability_bounds(
            row,
            model.distribution,
            "moneyline",
            "away",
            lower_quantile=lower_quantile,
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
        breakdown = model.distribution.total_market_breakdown(pred, line)
        totals_probabilities[line] = {"over": breakdown["over"], "under": breakdown["under"]}
        totals_outcomes[line] = {
            "over_win": breakdown["over_win"],
            "under_win": breakdown["under_win"],
            "push": breakdown["push"],
        }
        if bootstrap is not None and bootstrap.fitted:
            over_lower, _ = bootstrap.market_probability_bounds(
                row,
                model.distribution,
                "total",
                "over",
                line,
                lower_quantile=lower_quantile,
            )
            under_lower, _ = bootstrap.market_probability_bounds(
                row,
                model.distribution,
                "total",
                "under",
                line,
                lower_quantile=lower_quantile,
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
        # Signed lines are stored per side. Outcome mass is explicit even
        # when the pricing probability splits a whole-line push.
        push_probe = model.distribution.spread_market_breakdown(
            pred,
            line if side == "home" else -line,
        )
        spread_outcomes.setdefault(line, {})[f"{side}_win"] = push_probe[f"{side}_win"]
        spread_outcomes[line]["push"] = push_probe["push"]
        if bootstrap is not None and bootstrap.fitted:
            cover_lower, _ = bootstrap.market_probability_bounds(
                row,
                model.distribution,
                "spread",
                side,
                line,
                lower_quantile=lower_quantile,
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
            bootstrap_lower=lower[side],
            bootstrap_upper=upper[side],
            model_disagreement=real_model_disagreement,
            calibration_uncertainty=real_calibration_uncertainty,
            missingness_penalty=real_missingness_penalty,
            raw_probability=raw[side],
            missing_flags=real_missing_flags,
            lineup_uncertainty=None,
        )
        conservative_probabilities[side] = result.conservative_probability

    return SportsForecast(
        event_id=row["event_id"],
        predicted_winner=predicted_winner,
        raw_probabilities=raw,
        calibrated_probabilities=calibrated,
        probability_lower=lower,
        probability_upper=upper,
        expected_home_score=pred.home_expected_runs,
        expected_away_score=pred.away_expected_runs,
        model_artifact_hash=model_artifact_hash or model.to_artifact().get("artifact_hash", ""),
        calibration_artifact_hash=calibrator_hash or f"uncalibrated_{calibrator.method}",
        totals_probabilities=totals_probabilities,
        spread_probabilities=spread_probabilities,
        totals_probabilities_lower=totals_probabilities_lower,
        spread_probabilities_lower=spread_probabilities_lower,
        totals_outcomes=totals_outcomes,
        spread_outcomes=spread_outcomes,
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
    calibrator_bundle: FrozenCalibratorBundle | None = None
    frozen_bundle_hash: str = ""
    dataset_hash: str = ""
    training_cutoff_utc: str = ""
    code_revision: str = ""
    dependency_hash: str = ""
    source_tree_sha256: str = ""
    manifest_sha256: str = ""
    primary_content_sha256: str = ""
    primary_artifact_sha256: str = ""
    calibrator_artifact_sha256: str = ""
    artifact_path: str = ""
    test_id: str = MLB_V2_TEST_ID
    candidate_version: str = MLB_V2_CANDIDATE_VERSION
    train_n: int = 0
    decision_times: dict[str, datetime] = field(default_factory=dict)
    prediction_observed_at_by_event: dict[str, str] = field(default_factory=dict)
    rows_by_event: dict[str, dict] = field(default_factory=dict)  # real feature row per game
    forecasts: dict[str, SportsForecast] = field(
        default_factory=dict
    )  # rebuilt in decide() with real market lines
    market_rows: pl.DataFrame = field(default_factory=pl.DataFrame)
    candidates_by_event: dict[str, list] = field(default_factory=dict)
    total_lines_by_event: dict[str, list[float]] = field(default_factory=dict)
    spread_pairs_by_event: dict[str, list[tuple[float, str]]] = field(default_factory=dict)
    event_canonical_id_by_event: dict[str, str | None] = field(
        default_factory=dict
    )  # linked Polymarket <-> ESPN canonical event
    skipped: dict[str, str] = field(default_factory=dict)  # event_id -> real skip reason


def _apply_frozen_bundle(state: MLBRunState, frozen: FrozenMLBV2Bundle) -> None:
    """Bind one run state to every exact component of the sealed candidate."""
    state.model = frozen.primary
    state.bootstrap = frozen.bootstrap
    state.sklearn_baseline = frozen.sklearn_baseline
    state.xgb_direct = frozen.xgb_direct
    state.calibrator_bundle = frozen.calibrator
    state.frozen_bundle_hash = frozen.bundle_hash
    state.dataset_hash = frozen.dataset_hash
    state.training_cutoff_utc = frozen.training_cutoff_utc
    state.code_revision = frozen.code_revision
    state.dependency_hash = frozen.dependency_hash
    state.source_tree_sha256 = frozen.source_tree_sha256
    state.manifest_sha256 = frozen.manifest_sha256
    state.primary_content_sha256 = frozen.primary_content_sha256
    state.primary_artifact_sha256 = frozen.primary_artifact_sha256
    state.calibrator_artifact_sha256 = frozen.calibrator.artifact_sha256
    state.artifact_path = str(frozen.bundle_path)
    state.test_id = frozen.test_id
    state.candidate_version = frozen.candidate_version
    state.train_n = frozen.training_rows


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
# Resume stores only run-specific feature rows. Every fitted component is
# reloaded from the same exact frozen candidate bundle, so a resumed process
# restores identical uncertainty rather than silently substituting a haircut.


def _resume_state_dir(data_root: str, run_id: str) -> Path:
    return Path(data_root) / "resume_state" / "mlb" / run_id


def save_resume_state(state: MLBRunState, data_root: str, run_id: str) -> None:
    """Persist run-specific rows bound to the immutable candidate hash."""
    if state.model is None or not state.frozen_bundle_hash:
        raise ValueError("cannot save resume state before the frozen candidate is loaded")
    out = _resume_state_dir(data_root, run_id)
    out.mkdir(parents=True, exist_ok=True)

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

    event_contracts: dict[str, dict[str, Any]] = {}
    tonight_by_event = {str(row["event_id"]): row for row in state.tonight.iter_rows(named=True)}
    for event_id in state.rows_by_event:
        game = tonight_by_event.get(event_id)
        if game is None:
            raise ValueError(f"resume feature row has no scheduled event contract: {event_id}")
        required = ("event_start_utc", "home_team", "away_team")
        if any(not game.get(key) for key in required):
            raise ValueError(f"resume scheduled event contract is incomplete: {event_id}")
        event_contracts[event_id] = {
            "event_start_utc": game["event_start_utc"],
            "home_team": game["home_team"],
            "away_team": game["away_team"],
            "decision_time_utc": state.decision_times[event_id].isoformat(),
        }
    payload = {
        "target_date": state.target_date,
        "train_n": state.train_n,
        "frozen_bundle_hash": state.frozen_bundle_hash,
        "manifest_sha256": state.manifest_sha256,
        "primary_content_sha256": state.primary_content_sha256,
        "primary_artifact_sha256": state.primary_artifact_sha256,
        "calibrator_artifact_sha256": state.calibrator_artifact_sha256,
        "source_tree_sha256": state.source_tree_sha256,
        "test_id": state.test_id,
        "candidate_version": state.candidate_version,
        "rows_by_event": state.rows_by_event,
        "event_contracts": event_contracts,
        "decision_times": {k: v.isoformat() for k, v in state.decision_times.items()},
        "prediction_observed_at_by_event": state.prediction_observed_at_by_event,
        "skipped": state.skipped,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_json_default)
    envelope = {**payload, "resume_state_sha256": hashlib.sha256(canonical.encode()).hexdigest()}
    temporary = out / "state.json.tmp"
    temporary.write_text(json.dumps(envelope, indent=2, default=_json_default))
    temporary.replace(out / "state.json")


def load_resume_state(
    data_root: str,
    run_id: str,
    target_date: str,
    *,
    challenger_root: str | Path | None = None,
    repo_root: str | Path | None = None,
    expected_anchor: FrozenMLBV2Anchor | None = None,
    _test_expected_source_tree_sha256: str | None = None,
) -> MLBRunState | None:
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
    if not state_path.exists():
        return None
    saved = json.loads(state_path.read_text())
    if not isinstance(saved, dict):
        # ValueError, not TypeError: this validates untrusted on-disk JSON
        # content, consistent with every sibling malformed-resume-state
        # check in this function (state_hash mismatch, candidate binding
        # mismatch, etc.), not a Python argument-type contract.
        raise ValueError("resume state must be a JSON object")  # noqa: TRY004
    state_hash = saved.get("resume_state_sha256")
    identity = {key: value for key, value in saved.items() if key != "resume_state_sha256"}
    actual_state_hash = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if not state_hash or state_hash != actual_state_hash:
        raise ValueError("resume state content hash mismatch")
    if saved.get("target_date") != target_date:
        return None

    state = load_state(data_root, target_date)
    if state is None:
        return None
    frozen = load_frozen_mlb_v2_bundle(
        challenger_root,
        repo_root=repo_root,
        expected_anchor=expected_anchor,
        _test_expected_source_tree_sha256=_test_expected_source_tree_sha256,
    )
    if saved.get("frozen_bundle_hash") != frozen.bundle_hash:
        raise ValueError("resume state is bound to a different frozen MLB v2 candidate")
    expected_bindings = {
        "manifest_sha256": frozen.manifest_sha256,
        "primary_content_sha256": frozen.primary_content_sha256,
        "primary_artifact_sha256": frozen.primary_artifact_sha256,
        "calibrator_artifact_sha256": frozen.calibrator.artifact_sha256,
        "source_tree_sha256": frozen.source_tree_sha256,
        "test_id": frozen.test_id,
        "candidate_version": frozen.candidate_version,
    }
    mismatches = [key for key, value in expected_bindings.items() if saved.get(key) != value]
    if mismatches:
        raise ValueError("resume state candidate binding mismatch: " + ", ".join(mismatches))
    rows = saved.get("rows_by_event")
    observed_by_event = saved.get("prediction_observed_at_by_event")
    event_contracts = saved.get("event_contracts")
    if (
        not isinstance(rows, dict)
        or not isinstance(observed_by_event, dict)
        or not isinstance(event_contracts, dict)
    ):
        raise ValueError("resume state event payload is malformed")  # noqa: TRY004 -- untrusted JSON, see note above
    tonight_by_event = {str(row["event_id"]): row for row in state.tonight.iter_rows(named=True)}
    for event_id, row in rows.items():
        game = tonight_by_event.get(event_id)
        contract = event_contracts.get(event_id)
        if not isinstance(row, dict) or row.get("event_id") != event_id:
            raise ValueError(f"resume feature row identity mismatch: {event_id}")
        if game is None or not isinstance(contract, dict):
            raise ValueError(f"resume event is no longer in the scheduled slate: {event_id}")
        actual_contract = {
            "event_start_utc": game.get("event_start_utc"),
            "home_team": game.get("home_team"),
            "away_team": game.get("away_team"),
            "decision_time_utc": state.decision_times[event_id].isoformat(),
        }
        if contract != actual_contract:
            raise ValueError(f"resume scheduled event contract changed: {event_id}")
        observed_raw = observed_by_event.get(event_id)
        if not isinstance(observed_raw, str):
            # Validating untrusted resume-state JSON, matching every other
            # ValueError-on-bad-shape raise in this function -- TypeError
            # would be inconsistent with the sibling checks around it.
            raise ValueError(f"resume prediction observation time is invalid: {event_id}")  # noqa: TRY004
        try:
            observed = datetime.fromisoformat(observed_raw)
        except ValueError as exc:
            raise ValueError(f"resume prediction observation time is invalid: {event_id}") from exc
        decision_time = state.decision_times[event_id]
        if observed.tzinfo is None or observed.utcoffset() != timedelta(0):
            raise ValueError(f"resume prediction observation time must be UTC: {event_id}")
        if observed > decision_time:
            raise ValueError(f"resume prediction observation is after cutoff: {event_id}")
        if _utc_now_dt() > decision_time:
            raise ValueError(f"resume prediction cutoff has passed: {event_id}")
    _apply_frozen_bundle(state, frozen)
    state.rows_by_event = rows
    state.prediction_observed_at_by_event = observed_by_event
    state.skipped = saved.get("skipped", {})
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
        (pl.col("event_start_utc").str.slice(0, 10) == target_date) & (pl.col("status") == "STATUS_SCHEDULED")
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
        target_date=target_date,
        tonight=tonight,
        pitches=pitches,
        starters=starters,
        decision_times=decision_times,
    )


def predict_stage(
    state: MLBRunState,
    data_root: str,
    *,
    ledger: Any | None = None,
    run_id: str | None = None,
    challenger_root: str | Path | None = None,
    repo_root: str | Path | None = None,
) -> dict:
    """Load the sealed candidate and commit point-in-time feature rows.

    No model fitting is permitted in this runtime path.  Each feature row is
    stamped with its real completion time and rejected if that time is later
    than the predeclared decision cutoff.
    """
    from .mlb_features import load_probable_starter_records

    frozen = load_frozen_mlb_v2_bundle(
        challenger_root,
        repo_root=repo_root,
    )
    _apply_frozen_bundle(state, frozen)

    probable_records = load_probable_starter_records(
        Path(data_root) / "raw" / "mlb" / "probable_starters.jsonl"
    )

    if ledger is not None and run_id is not None:
        ledger.record_model_artifact(
            run_id=run_id,
            sport="mlb",
            model_name=state.test_id,
            model_version=state.candidate_version,
            artifact_hash=state.frozen_bundle_hash,
            market_family="moneyline",
            horizon=HORIZON_LATE,
            training_end=state.training_cutoff_utc,
            dataset_hash=state.dataset_hash,
            code_revision=state.code_revision,
            dependency_lock_hash=state.dependency_hash,
            artifact_path=state.artifact_path,
            manifest_sha256=state.manifest_sha256,
            primary_content_sha256=state.primary_content_sha256,
            primary_artifact_sha256=state.primary_artifact_sha256,
            calibrator_artifact_sha256=state.calibrator_artifact_sha256,
            source_tree_sha256=state.source_tree_sha256,
        )
        assert state.calibrator_bundle is not None
        ledger.record_calibration_artifact(
            run_id=run_id,
            sport="mlb",
            model_artifact_hash=state.frozen_bundle_hash,
            calibration_hash=state.calibrator_bundle.calibrator_hash,
            method=state.calibrator_bundle.calibrator.method,
            fitted_on_hash=state.calibrator_bundle.dataset_hash,
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
        decision_time = state.decision_times[event_id]
        observed = _utc_now_dt()
        if observed > decision_time:
            state.skipped[event_id] = "prediction_cutoff_passed"
            continue
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
            g,
            probable["home_starter"],
            probable["away_starter"],
            state.pitches,
            state.starters,
            data_root,
            identity_registry,
            decision_time_utc=state.decision_times[event_id],
        )
        if row is None:
            state.skipped[event_id] = "starter_name_not_resolved_to_real_statcast_id"
            continue

        committed_at = _utc_now_dt()
        if committed_at > decision_time:
            state.skipped[event_id] = "prediction_cutoff_passed"
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
        state.prediction_observed_at_by_event[event_id] = committed_at.isoformat()
        n_predicted += 1

    if run_id is not None:
        save_resume_state(state, data_root, run_id)

    return {
        "status": "ok",
        "train_games": state.train_n,
        "frozen_bundle_hash": state.frozen_bundle_hash,
        "test_id": state.test_id,
        "candidate_version": state.candidate_version,
        "games_predicted": n_predicted,
        "games_total": state.tonight.height,
        "skipped": dict(state.skipped),
    }


def match_markets_stage(
    state: MLBRunState,
    data_root: str,
    collector: Any,
    *,
    ledger: Any | None = None,
    run_id: str | None = None,
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
            state.market_rows,
            g["home_team"],
            g["away_team"],
            home_canonical_id=home_canonical_id,
            away_canonical_id=away_canonical_id,
        )
        # Real event-identity linking (Task 1 follow-up): ties Polymarket's
        # own event_id to the same canonical event ESPN scoreboard
        # collection already registered, closing the gap flagged in
        # identity.py's resolve_or_link_polymarket_event_id() docstring --
        # the two id-spaces previously had nothing tying them together.
        # Fails closed to None (not an error) on any ambiguity or missing
        # canonical team ids, matching every other resolver here.
        state.event_canonical_id_by_event[event_id] = resolve_or_link_polymarket_event_id(
            collector.identity,
            "mlb",
            resolved_event_id,
            home_canonical_id,
            away_canonical_id,
            state.target_date,
            known_canonical_event_id=g.get("event_canonical_id"),
        )
        state.total_lines_by_event[event_id] = (
            real_total_lines(state.market_rows, resolved_event_id) if resolved_event_id else []
        )
        state.spread_pairs_by_event[event_id] = (
            real_spread_line_side_pairs(state.market_rows, resolved_event_id) if resolved_event_id else []
        )
        state.candidates_by_event[event_id] = real_market_candidates(
            state.market_rows,
            g["home_team"],
            g["away_team"],
            home_canonical_id=home_canonical_id,
            away_canonical_id=away_canonical_id,
        )
        n_matched += 1

    return {
        "status": "ok",
        "polymarket_collect_status": collect_result.get("status"),
        "real_market_rows": state.market_rows.height,
        "games_matched": n_matched,
    }


def decide_stage(
    state: MLBRunState,
    *,
    ledger: Any | None = None,
    run_id: str | None = None,
    limits: SizeLimits | None = None,
    challenger_root: str | Path | None = None,
) -> dict:
    """Real final forecast (now with real market-derived total/spread
    lines) + winner-first decision + persistence -- matches
    mlb_shadow_run.py's steps 5b-7 exactly (same build_forecast/
    evaluate_game/real_market_snapshot_hash calls, same ledger persistence
    shape)."""
    if state.model is None:
        raise ValueError("decide_stage requires predict_stage to have run first (state.model is None)")

    if not state.frozen_bundle_hash or state.calibrator_bundle is None:
        raise ValueError("decide_stage requires the exact frozen MLB v2 bundle loaded by predict_stage")
    calibrator_bundle = state.calibrator_bundle

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
    team_names = {
        r["event_id"]: (r["home_team"], r["away_team"]) for r in state.tonight.iter_rows(named=True)
    }

    for event_id, row in state.rows_by_event.items():
        prediction_created_at = _utc_now_dt()
        if prediction_created_at > state.decision_times[event_id]:
            state.skipped[event_id] = "prediction_cutoff_passed_before_commit"
            continue
        candidates = state.candidates_by_event.get(event_id, [])
        total_lines = state.total_lines_by_event.get(event_id, [])
        spread_pairs = state.spread_pairs_by_event.get(event_id, [])

        # Rebuild with real market-derived lines now known -- predicted_winner
        # itself was already frozen in predict_stage() and is unchanged here
        # (build_forecast recomputes deterministically from the same model/row).
        forecast = build_forecast(
            state.model,
            row,
            total_lines,
            spread_pairs,
            bootstrap=state.bootstrap,
            calibrator=calibrator_bundle.calibrator,
            calibrator_hash=calibrator_bundle.calibrator_hash,
            sklearn_baseline=state.sklearn_baseline,
            xgb_direct=state.xgb_direct,
            calibration_oof_probs=calibrator_bundle.oof_probs,
            calibration_oof_labels=calibrator_bundle.oof_labels,
            model_artifact_hash=state.frozen_bundle_hash,
        )
        state.forecasts[event_id] = forecast

        decisions = evaluate_game(forecast, candidates, limits)
        bet_decisions = [d for d in decisions if d.action == "BET"]
        n_bets += len(bet_decisions)

        decision_time_utc = state.decision_times[event_id].isoformat()

        if ledger is not None and run_id is not None:
            _, pred_created = ledger.record_prediction(
                run_id=run_id,
                sport="mlb",
                event_id=event_id,
                horizon=HORIZON_LATE,
                decision_time_utc=decision_time_utc,
                prediction_observed_at_utc=state.prediction_observed_at_by_event[event_id],
                test_id=state.test_id,
                candidate_version=state.candidate_version,
                forecast=forecast,
            )
            n_predictions_recorded += 1 if pred_created else 0
            market_eval_ids: dict[tuple, int] = {}
            for c in candidates:
                eval_row_id = ledger.record_market_evaluation(
                    run_id=run_id,
                    sport="mlb",
                    event_id=event_id,
                    evaluation=c,
                    decision_time_utc=decision_time_utc,
                )
                market_eval_ids[(c.market_id, c.market_type, c.team_or_side, c.line)] = eval_row_id

            market_snapshot_hash = real_market_snapshot_hash(event_id, candidates)
            for d in decisions:
                selected_id = (
                    market_eval_ids.get(
                        (
                            d.selected_market.market_id,
                            d.selected_market.market_type,
                            d.selected_market.team_or_side,
                            d.selected_market.line,
                        )
                    )
                    if d.selected_market
                    else None
                )
                evaluated_id = (
                    market_eval_ids.get(
                        (
                            d.evaluated_market.market_id,
                            d.evaluated_market.market_type,
                            d.evaluated_market.team_or_side,
                            d.evaluated_market.line,
                        )
                    )
                    if d.evaluated_market
                    else None
                )
                _, decision_created = ledger.record_trade_decision(
                    run_id=run_id,
                    sport="mlb",
                    event_id=event_id,
                    horizon=HORIZON_LATE,
                    decision_time_utc=decision_time_utc,
                    model_artifact_hash=forecast.model_artifact_hash,
                    market_snapshot_hash=market_snapshot_hash,
                    decision_policy_version=DECISION_POLICY_VERSION,
                    decision=d,
                    selected_market_evaluation_id=selected_id,
                    evaluated_market_evaluation_id=evaluated_id,
                )
                n_decisions_recorded += 1 if decision_created else 0
                n_decisions_deduped += 0 if decision_created else 1

        home_team, away_team = team_names.get(event_id, ("", ""))
        games_report.append(
            {
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
                    {
                        "market_type": d.market_type,
                        "action": d.action,
                        "units": d.units,
                        "reason": d.reason_code,
                        "market_id": d.evaluated_market.market_id if d.evaluated_market else None,
                        "team_or_side": d.evaluated_market.team_or_side if d.evaluated_market else None,
                        "line": d.evaluated_market.line if d.evaluated_market else None,
                        "executable_ask": d.evaluated_market.executable_ask if d.evaluated_market else None,
                        "cost_adjusted_edge": d.cost_adjusted_edge,
                        "model_probability": d.model_probability,
                        "market_probability": d.market_probability,
                        "serving_probability": d.serving_probability,
                        "model_conservative_probability": d.model_conservative_probability,
                        "serving_conservative_probability": d.serving_conservative_probability,
                        "blend_weight": d.blend_weight,
                        "blend_policy_artifact_hash": d.blend_policy_artifact_hash,
                        "blend_experiment_spec_hash": d.blend_experiment_spec_hash,
                        "blend_config_hash": d.blend_config_hash,
                        "serving_policy_block_reason": d.serving_policy_block_reason,
                    }
                    | {
                        "fee_rate": d.fee_rate,
                        "safety_margin": d.safety_margin,
                        "size_limits_version": d.size_limits_version,
                        "size_limits_json": d.size_limits_json,
                        "decision_economics_hash": d.decision_economics_hash,
                    }
                    for d in decisions
                ],
                "bets": len(bet_decisions),
            }
        )

    return {
        "status": "ok",
        "games": games_report,
        "total_bets": n_bets,
        "skipped": dict(state.skipped),
        "predictions_recorded": n_predictions_recorded,
        "trade_decisions_recorded": n_decisions_recorded,
        "trade_decisions_deduped": n_decisions_deduped,
    }
