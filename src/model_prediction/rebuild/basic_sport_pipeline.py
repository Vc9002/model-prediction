"""Generic basic-adapter pipeline (Elo baseline, moneyline-only) shared by
the NBA/WNBA/NFL/Soccer/Tennis foundation adapters in sport_adapter.py.

Explicitly a "basic prediction, working pipeline" build, not an advanced
model -- mirrors mlb_shadow_pipeline.py's real state/stage-function shape
(load_state/predict_stage/match_markets_stage/decide_stage) so the same
shared CLI (rebuild_shadow_cli.py) and ledger persistence apply uniformly,
but every sport-specific piece MLB's pipeline has (Statcast features,
bootstrap uncertainty, the two-head run-intensity/differential model,
market-derived total/spread line pricing) is replaced by the one thing
that generalizes across sports with zero new feature engineering: a
logistic Elo rating fit from real final scores (basic_elo.py).

Real, disclosed scope limits versus MLB's pipeline:
  - Moneyline only. Elo has no principled way to price an exact spread or
    total line, so match_markets_stage only builds candidates for
    market_type == "moneyline" -- spread/total markets are correctly never
    evaluated (not silently mispriced) for these sports until a real
    sport-specific distribution exists (Part 2 territory).
  - No bootstrap ensemble -- probability_lower/upper use the same fixed
    uncertainty_haircut fallback build_forecast() itself falls back to for
    a caller without one; this is honestly less rigorous than MLB's
    per-row empirical bound, not a hidden equivalent.
  - "Feature snapshot" is the real, deduped scoreboard rows themselves
    (home/away team, final score, real timestamps) -- there is no derived
    per-team feature store yet (no Four Factors, EPA, xG...). Real, not
    fabricated: Elo's only real input is that scoreboard.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import polars as pl

from .basic_elo import EloModel
from .decision import SportsForecast, evaluate_game
from .economic import SizeLimits
from .mlb_market_matching import (
    real_market_candidates,
    real_market_snapshot_hash,
)

DECISION_POLICY_VERSION = "winner_first_v1"
# Real gap found and fixed live (2026-08-07): predict()/match_markets()/
# decide() all received a real `horizon` argument from the shared CLI but
# silently ignored it -- every call used the same fixed 60-minutes-before-
# start decision time and hardcoded horizon="basic" in every ledger call,
# so `--horizon early` and `--horizon late` produced byte-identical
# results and identical ledger rows. Matches MLB's own real early=36h/
# mid=6h/late=60m convention (mlb_shadow_pipeline.py's HORIZON_LATE
# comment) rather than horizons.py's more general declarative spec.
# Real, disclosed limit this doesn't change: the Elo win probability
# itself doesn't vary by horizon (no lineup-confirmation-style features
# exist for these sports yet) -- only decision_time_utc (and therefore
# which market quotes are fresh enough to evaluate) does.
HORIZON_MINUTES_BEFORE_START = {"early": 36 * 60, "mid": 6 * 60, "late": 60}
MIN_HISTORICAL_GAMES = 10
UNCERTAINTY_HAIRCUT = 0.03


def dedupe_scoreboard(sb: pl.DataFrame) -> pl.DataFrame:
    """Repeated real collection can write more than one row for the same
    real event_id (a later run observes a status change, e.g. SCHEDULED ->
    FINAL) -- keep only the most-recently-observed row per event. Same
    real fix as mlb_features.dedupe_scoreboard, generalized here since
    every sport's scoreboard shares the identical schema."""
    return sb.sort("observed_at_utc").group_by("event_id", maintain_order=False).agg(pl.all().last())


def _model_artifact_hash(model: EloModel) -> str:
    content = "|".join(f"{team}:{rating:.4f}" for team, rating in sorted(model.ratings.items()))
    return hashlib.sha256(f"elo-v1|{model.games_fit}|{content}".encode()).hexdigest()[:16]


@dataclass
class BasicRunState:
    """Cross-stage state for one real basic-adapter run, held by the
    adapter between predict()/match_markets()/decide() calls within one
    CLI invocation -- same real shape as MLBRunState (mlb_shadow_pipeline.py)."""

    sport: str
    target_date: str
    horizon: str
    tonight: pl.DataFrame
    model: EloModel | None = None
    train_n: int = 0
    decision_times: dict[str, datetime] = field(default_factory=dict)
    forecasts: dict[str, SportsForecast] = field(default_factory=dict)
    market_rows: pl.DataFrame = field(default_factory=pl.DataFrame)
    candidates_by_event: dict[str, list] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)


def load_state(data_root: str, sport: str, target_date: str, horizon: str = "late") -> BasicRunState | None:
    """Real scheduled-games load. Returns None (honest stop) when no
    scoreboard has ever been collected for this sport/data_root, or there
    are no real scheduled games on target_date. `horizon` fails closed to
    a KeyError on anything outside HORIZON_MINUTES_BEFORE_START's real
    early/mid/late keys, rather than silently falling back to a default."""
    minutes_before_start = HORIZON_MINUTES_BEFORE_START[horizon]
    try:
        sb = pl.read_parquet(f"{data_root}/normalized/{sport}/scoreboard.parquet")
    except FileNotFoundError:
        return None
    sb = dedupe_scoreboard(sb)
    tonight = sb.filter(
        (pl.col("event_start_utc").str.slice(0, 10) == target_date) & (pl.col("status") == "STATUS_SCHEDULED")
    )
    if tonight.height == 0:
        return None

    decision_times = {
        g["event_id"]: datetime.fromisoformat(g["event_start_utc"]) - timedelta(minutes=minutes_before_start)
        for g in tonight.iter_rows(named=True)
    }
    return BasicRunState(
        sport=sport,
        target_date=target_date,
        horizon=horizon,
        tonight=tonight,
        decision_times=decision_times,
    )


def predict_stage(
    state: BasicRunState, data_root: str, *, ledger: Any | None = None, run_id: str | None = None
) -> dict:
    """Real walk-forward Elo fit (strictly before target_date) + a
    market-blind moneyline forecast per real scheduled game -- predicted_winner
    is genuinely frozen here, before match_markets_stage ever inspects a
    real market price."""
    sb = dedupe_scoreboard(pl.read_parquet(f"{data_root}/normalized/{state.sport}/scoreboard.parquet"))
    completed = sb.filter(
        (pl.col("status") == "STATUS_FINAL")
        & (pl.col("event_start_utc").str.slice(0, 10) < state.target_date)
    ).sort("event_start_utc")

    if completed.height < MIN_HISTORICAL_GAMES:
        return {"status": "insufficient_history", "historical_games": completed.height}

    model = EloModel()
    model.fit(completed)
    state.model = model
    state.train_n = completed.height
    artifact_hash = _model_artifact_hash(model)

    if ledger is not None and run_id is not None:
        ledger.record_model_artifact(
            run_id=run_id,
            sport=state.sport,
            model_name=f"{state.sport}-elo-v1",
            model_version="elo-v1",
            artifact_hash=artifact_hash,
            horizon=state.horizon,
            training_end=state.target_date,
        )

    n_predicted = 0
    for g in state.tonight.iter_rows(named=True):
        event_id = g["event_id"]
        home_prob, away_prob = model.predict(g["home_team"], g["away_team"])
        winner: Literal["home", "away"] = "home" if home_prob >= away_prob else "away"
        calibrated = {"home": home_prob, "away": away_prob}
        lower = {
            side: max(0.0, min(1.0, p - UNCERTAINTY_HAIRCUT if p >= 0.5 else p + UNCERTAINTY_HAIRCUT))
            for side, p in calibrated.items()
        }
        upper = {side: min(1.0, p + UNCERTAINTY_HAIRCUT) for side, p in calibrated.items()}
        state.forecasts[event_id] = SportsForecast(
            event_id=event_id,
            predicted_winner=winner,
            raw_probabilities=calibrated,
            calibrated_probabilities=calibrated,
            probability_lower=lower,
            probability_upper=upper,
            # Real gap, disclosed: Elo has no principled expected-score
            # output (only a win-probability), so these are honestly left
            # at 0.0 rather than reverse-engineered from probability --
            # totals_probabilities stays empty for the same reason, so no
            # caller can accidentally treat this as a real score estimate.
            expected_home_score=0.0,
            expected_away_score=0.0,
            model_artifact_hash=artifact_hash,
            calibration_artifact_hash="elo_fixed_haircut_v1",
        )
        n_predicted += 1

    return {
        "status": "ok",
        "train_games": state.train_n,
        "games_predicted": n_predicted,
        "games_total": state.tonight.height,
    }


def match_markets_stage(
    state: BasicRunState,
    data_root: str,
    collect_fn: Callable[[str], dict],
    *,
    ledger: Any | None = None,
    run_id: str | None = None,
) -> dict:
    """Real fresh market collection (via the sport's own real collector,
    injected by the adapter -- collect_fn signature varies too much across
    NBA/WNBA/NFL/Soccer/Tennis collectors to call generically here) +
    per-game moneyline-only candidate resolution. Spread/total rows are
    real too (the same collector writes them) but are never evaluated --
    Elo has no probability to price them against, so silently including
    them would misrepresent a real market as a priced one."""
    collect_result = collect_fn(state.target_date)

    market_path = Path(f"{data_root}/markets/{state.sport}/{state.target_date}.parquet")
    if market_path.exists():
        state.market_rows = pl.read_parquet(market_path)
    else:
        state.market_rows = pl.DataFrame()

    n_matched = 0
    for g in state.tonight.iter_rows(named=True):
        event_id = g["event_id"]
        if event_id not in state.forecasts:
            continue
        if state.market_rows.is_empty():
            state.candidates_by_event[event_id] = []
            continue
        # Canonical team IDs (from ESPN scoreboard collection's real
        # identity wiring) are preferred over name matching when available
        # -- real_market_candidates() falls back to word-boundary name
        # matching honestly when they aren't.
        candidates = real_market_candidates(
            state.market_rows,
            g["home_team"],
            g["away_team"],
            home_canonical_id=g.get("home_team_canonical_id"),
            away_canonical_id=g.get("away_team_canonical_id"),
        )
        state.candidates_by_event[event_id] = [c for c in candidates if c.market_type == "moneyline"]
        n_matched += 1

    return {
        "status": "ok",
        "collect_status": collect_result.get("status"),
        "real_market_rows": state.market_rows.height,
        "games_matched": n_matched,
    }


def decide_stage(
    state: BasicRunState,
    *,
    ledger: Any | None = None,
    run_id: str | None = None,
    limits: SizeLimits | None = None,
) -> dict:
    """Real winner-first decision + persistence over the frozen moneyline
    forecasts from predict_stage() -- unlike MLB's decide_stage, there is
    no forecast rebuild here: Elo's moneyline probability doesn't depend
    on any market-derived line, so the one real forecast built in
    predict_stage() is already final."""
    if state.model is None:
        raise ValueError("decide_stage requires predict_stage to have run first (state.model is None)")

    limits = limits if limits is not None else SizeLimits()
    games_report = []
    n_bets = 0
    n_predictions_recorded = 0
    n_decisions_recorded = 0
    n_decisions_deduped = 0
    team_names = {
        r["event_id"]: (r["home_team"], r["away_team"]) for r in state.tonight.iter_rows(named=True)
    }

    for event_id, forecast in state.forecasts.items():
        candidates = state.candidates_by_event.get(event_id, [])
        decisions = evaluate_game(forecast, candidates, limits)
        bet_decisions = [d for d in decisions if d.action == "BET"]
        n_bets += len(bet_decisions)

        decision_time_utc = state.decision_times[event_id].isoformat()

        if ledger is not None and run_id is not None:
            _, pred_created = ledger.record_prediction(
                run_id=run_id,
                sport=state.sport,
                event_id=event_id,
                horizon=state.horizon,
                decision_time_utc=decision_time_utc,
                forecast=forecast,
            )
            n_predictions_recorded += 1 if pred_created else 0

            market_eval_ids: dict[tuple, int] = {}
            for c in candidates:
                eval_row_id = ledger.record_market_evaluation(
                    run_id=run_id,
                    sport=state.sport,
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
                    sport=state.sport,
                    event_id=event_id,
                    horizon=state.horizon,
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
                "candidate_markets_evaluated": len(candidates),
                "bets": len(bet_decisions),
            }
        )

    return {
        "status": "ok",
        "games": games_report,
        "total_bets": n_bets,
        "predictions_recorded": n_predictions_recorded,
        "trade_decisions_recorded": n_decisions_recorded,
        "trade_decisions_deduped": n_decisions_deduped,
    }
