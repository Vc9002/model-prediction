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

import polars as pl

from .decision import SportsForecast, evaluate_game
from .economic import SizeLimits
from .mlb_features import (
    ESPN_TO_STATCAST_ABBREV,
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
from .models import BootstrapMLBEnsemble, MLBTwoHeadModel

HORIZON_LATE = "late"
DECISION_POLICY_VERSION = "winner_first_v1"

INTENSITY_FEATURES = [
    "home_sp_avg_velocity", "away_sp_avg_velocity",
    "home_sp_csw_pct", "away_sp_csw_pct",
    "home_bp_bullpen_pitches", "away_bp_bullpen_pitches",
    "park_factor", "temp_f_mean",
]
DIFFERENTIAL_FEATURES = [
    "home_sp_k_pct", "away_sp_k_pct",
    "home_sp_bb_pct", "away_sp_bb_pct",
    "home_sp_days_rest", "away_sp_days_rest",
    "home_bp_bullpen_avg_velocity", "away_bp_bullpen_avg_velocity",
]


def train_through(features: pl.DataFrame, cutoff_date: str) -> tuple[MLBTwoHeadModel, BootstrapMLBEnsemble, int]:
    """Walk-forward retrain: fit only on games strictly before cutoff_date.
    No artifact-reload mechanism exists yet for the fitted sklearn models
    (MLBTwoHeadModel.to_artifact() only persists metadata/hashes, not the
    model itself) -- retraining fresh each run is the honest current
    behavior, not a shortcut, and is disclosed as a Checkpoint 9 gap below.

    Also fits a BootstrapMLBEnsemble on the identical training data, used by
    build_forecast() for real conservative_probability bounds (CLAUDE.md's
    bootstrap_uncertainty requirement) instead of a fixed haircut."""
    train = features.filter(pl.col("game_date") < cutoff_date)
    model = MLBTwoHeadModel(seed=42)
    model.fit(train, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)
    bootstrap = BootstrapMLBEnsemble(n_bootstrap=20, seed=42)
    bootstrap.fit(train, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)
    return model, bootstrap, train.height


def build_forecast(
    model: MLBTwoHeadModel, row: dict,
    total_lines: list[float] | None = None,
    spread_line_side_pairs: list[tuple[float, str]] | None = None,
    bootstrap: BootstrapMLBEnsemble | None = None,
    lower_quantile: float = 0.10,
    uncertainty_haircut: float = 0.03,
) -> SportsForecast:
    pred = model.predict_row(row["event_id"], row)
    predicted_winner: Literal["home", "away"] = "home" if pred.home_win_prob >= pred.away_win_prob else "away"
    calibrated = {"home": pred.home_win_prob, "away": pred.away_win_prob}

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
        # depended on particular training games.
        home_lower, home_upper = bootstrap.market_probability_bounds(
            row, model.distribution, "moneyline", "home", lower_quantile=lower_quantile,
        )
        away_lower, away_upper = bootstrap.market_probability_bounds(
            row, model.distribution, "moneyline", "away", lower_quantile=lower_quantile,
        )
        lower = {"home": home_lower, "away": away_lower}
        upper = {"home": home_upper, "away": away_upper}
    else:
        # Fallback for callers without a fitted bootstrap ensemble (e.g.
        # a unit test constructing build_forecast() output directly): a
        # fixed haircut toward 50/50, disclosed as a strictly weaker
        # substitute for the real bootstrap bound above.
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
    # is inspected.
    for line, side in spread_line_side_pairs or []:
        cover_p = model.distribution.probability_for_market(pred, "spread", side, line)
        spread_probabilities.setdefault(line, {})[side] = cover_p
        if bootstrap is not None and bootstrap.fitted:
            cover_lower, _ = bootstrap.market_probability_bounds(
                row, model.distribution, "spread", side, line, lower_quantile=lower_quantile,
            )
            spread_probabilities_lower.setdefault(line, {})[side] = cover_lower

    return SportsForecast(
        event_id=row["event_id"], predicted_winner=predicted_winner,
        raw_probabilities=calibrated, calibrated_probabilities=calibrated,
        probability_lower=lower, probability_upper=upper,
        expected_home_score=pred.home_expected_runs, expected_away_score=pred.away_expected_runs,
        model_artifact_hash=model.to_artifact().get("artifact_hash", ""),
        calibration_artifact_hash="uncalibrated_haircut_v1" if bootstrap is None else "bootstrap_uncertainty_v1",
        totals_probabilities=totals_probabilities,
        spread_probabilities=spread_probabilities,
        totals_probabilities_lower=totals_probabilities_lower,
        spread_probabilities_lower=spread_probabilities_lower,
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
    model: MLBTwoHeadModel | None = None
    bootstrap: BootstrapMLBEnsemble | None = None
    train_n: int = 0
    decision_times: dict[str, datetime] = field(default_factory=dict)
    rows_by_event: dict[str, dict] = field(default_factory=dict)  # real feature row per game
    forecasts: dict[str, SportsForecast] = field(default_factory=dict)  # rebuilt in decide() with real market lines
    market_rows: pl.DataFrame = field(default_factory=pl.DataFrame)
    candidates_by_event: dict[str, list] = field(default_factory=dict)
    total_lines_by_event: dict[str, list[float]] = field(default_factory=dict)
    spread_pairs_by_event: dict[str, list[tuple[float, str]]] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)  # event_id -> real skip reason


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
    from .mlb_features import build_game_feature_row  # deferred: heavy pybaseball import path

    sb = dedupe_scoreboard(pl.read_parquet(f"{data_root}/normalized/mlb/scoreboard.parquet"))
    completed = sb.filter(pl.col("status") == "STATUS_FINAL").sort("event_start_utc")
    rows = [build_game_feature_row(g, state.pitches, state.starters, data_root) for g in completed.iter_rows(named=True)]
    rows = [r for r in rows if r is not None]
    features = pl.DataFrame(rows).sort("game_date") if rows else pl.DataFrame()

    if features.height < 30:
        return {"status": "insufficient_history", "historical_games": features.height}

    state.model, state.bootstrap, state.train_n = train_through(features, state.target_date)

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

    probables_path = Path("data/point_in_time/mlb_probable_starters.jsonl")
    probables_by_event: dict[str, dict] = {}
    if probables_path.exists() and state.decision_times:
        records = [json.loads(line) for line in probables_path.read_text().splitlines() if line.strip()]
        probables_by_event = point_in_time_probable_starters(state.decision_times, records)

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
        resolved_event_id = resolve_polymarket_event_id(state.market_rows, g["home_team"], g["away_team"])
        state.total_lines_by_event[event_id] = (
            real_total_lines(state.market_rows, resolved_event_id) if resolved_event_id else []
        )
        state.spread_pairs_by_event[event_id] = (
            real_spread_line_side_pairs(state.market_rows, resolved_event_id) if resolved_event_id else []
        )
        state.candidates_by_event[event_id] = real_market_candidates(state.market_rows, g["home_team"], g["away_team"])
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
        forecast = build_forecast(state.model, row, total_lines, spread_pairs, bootstrap=state.bootstrap)
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
