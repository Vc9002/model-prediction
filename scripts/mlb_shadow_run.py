"""One-command MLB production-ready shadow run (CLAUDE.md Checkpoint 9).

collect (already run separately) -> normalize -> real features -> retrain
through yesterday -> predict tonight's real slate -> load real Polymarket
books -> winner-first decision -> persist -> operator report.

No real order is ever submitted — this script only reads collected data and
writes a paper decision log. It does not import or call anything under
dashboard_server.py's order-execution routes.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/mlb_shadow_run.py --date 2026-08-06
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.rebuild.collectors import MLBCollector
from model_prediction.rebuild.decision import SportsForecast, evaluate_game
from model_prediction.rebuild.economic import SizeLimits
from model_prediction.rebuild.metadata import MetadataDB
from model_prediction.rebuild.mlb_features import (
    ESPN_TO_STATCAST_ABBREV,
    build_game_feature_row,
    build_live_game_feature_row,
    dedupe_scoreboard,
    identify_starters,
    load_raw_statcast_dates,
    normalize_statcast_pitches,
    point_in_time_probable_starters,
)
from model_prediction.rebuild.mlb_market_matching import (
    exclude_first_five_innings,
    real_market_candidates,
    real_market_snapshot_hash,
    real_spread_line_side_pairs,
    real_total_lines,
    resolve_polymarket_event_id,
)
from model_prediction.rebuild.models import BootstrapMLBEnsemble, MLBTwoHeadModel
from model_prediction.rebuild.shadow_ledger import ShadowLedger

# Only the "late" horizon (CLAUDE.md: start minus 60 minutes) is implemented
# by this script today -- early/mid horizons are a disclosed future gap.
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
    predicted_winner = "home" if pred.home_win_prob >= pred.away_win_prob else "away"
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()
    target_date = args.date

    ledger = ShadowLedger("data/rebuild/shadow.db")
    run_id = ledger.record_run("mlb", run_type="shadow", horizon=HORIZON_LATE, params={"date": target_date})
    print(f"0. Shadow ledger run {run_id} started (data/rebuild/shadow.db)")

    sb = dedupe_scoreboard(pl.read_parquet("data/rebuild/normalized/mlb/scoreboard.parquet"))
    tonight = sb.filter(
        (pl.col("event_start_utc").str.slice(0, 10) == target_date)
        & (pl.col("status") == "STATUS_SCHEDULED")
    )
    print(f"1. {tonight.height} real scheduled MLB games for {target_date}")
    if tonight.height == 0:
        print("No scheduled games. Stopping honestly.")
        sys.exit(0)

    completed = sb.filter(pl.col("status") == "STATUS_FINAL").sort("event_start_utc")
    backfill_dates = sorted({r["event_start_utc"][:10] for r in completed.iter_rows(named=True)})
    raw = load_raw_statcast_dates("data/rebuild", backfill_dates)
    pitches = normalize_statcast_pitches(raw)
    starters = identify_starters(pitches)
    print(f"2. {pitches.height} real Statcast pitches, {starters.height} starter-game entries")

    rows = [build_game_feature_row(g, pitches, starters, "data/rebuild") for g in completed.iter_rows(named=True)]
    rows = [r for r in rows if r is not None]
    features = pl.DataFrame(rows).sort("game_date") if rows else pl.DataFrame()
    print(f"3. {features.height} historical games with real matched features")

    if features.height < 30:
        print("Not enough historical games to train. Stopping honestly.")
        sys.exit(0)

    model, bootstrap, train_n = train_through(features, target_date)
    print(f"4. Model retrained on {train_n} real games (walk-forward, strictly before {target_date}), "
          f"{bootstrap.n_bootstrap} bootstrap replicates fit for conservative_probability")

    # Real bug this fixes: fixing quote_age_seconds to be real (see
    # mlb_market_matching.py) immediately exposed that this script never
    # actually re-collected market data itself — it just read whatever
    # parquet happened to already be on disk, which could be hours stale by
    # the time a decision was made, silently defeating the freshness gate
    # that fabricated 0.0 had been masking. A real one-command shadow run
    # must collect its own fresh quotes, not assume someone else already
    # did recently enough.
    meta = MetadataDB("data/rebuild/metadata.db")
    collector = MLBCollector("data/rebuild", meta)
    collect_result = collector.collect_polymarket_books(target_date)
    print(f"4b. Live Polymarket collection: {collect_result.get('status')} "
          f"({collect_result.get('books', 0)} books)")

    market_path = Path(f"data/rebuild/markets/mlb/{target_date}.parquet")
    if market_path.exists():
        raw_market_rows = pl.read_parquet(market_path)
        # This model only predicts full-game outcomes, so first-5-innings
        # markets are dropped upstream of every consumer below, rather than
        # risk one call site filtering it and another forgetting to (see
        # mlb_market_matching.py's module docstring for the real bug this
        # fixes).
        market_rows = exclude_first_five_innings(raw_market_rows)
        f5_count = raw_market_rows.height - market_rows.height
        print(f"5. {market_rows.height} real full-game Polymarket rows loaded for {target_date} "
              f"({f5_count} first-5-innings rows excluded — no full-game model to compare them against)")
    else:
        market_rows = pl.DataFrame()
        print(f"5. No Polymarket data collected for {target_date}")

    # Each game's real "late" decision_time_utc (start minus 60 minutes),
    # computed once here and reused both for the point-in-time probable-
    # starter join below and for ledger persistence later in this loop.
    decision_times: dict[str, datetime] = {
        g["event_id"]: datetime.fromisoformat(g["event_start_utc"]) - timedelta(minutes=60)
        for g in tonight.iter_rows(named=True)
    }

    # Real probable-starter names, keyed by ESPN event_id — a scheduled game
    # has no Statcast pitches of its own yet (it hasn't been played), so
    # build_game_feature_row's game_pk matching can never find it. This is
    # the existing incumbent system's own collected data, used here only as
    # a real input source per CLAUDE.md ("the existing project [is] a
    # benchmark and data source"), not as a shortcut around building real
    # features.
    #
    # Real point-in-time gap fixed here (FOUNDATION_COMPLETION.md Phase 3):
    # this file carries real revisions over time for the same event_id (152
    # of 163 real events have more than one record, confirmed live) but was
    # previously read with `probables_by_event[rec["event_id"]] = rec`,
    # which keeps whichever record happens to be *last in the file* — not
    # necessarily the newest observation strictly before this game's real
    # decision_time_utc. A revision observed after the "late" horizon's
    # T-60m cutoff could silently leak into a decision that shouldn't have
    # seen it yet. point_in_time_probable_starters() uses the shared
    # point_in_time_join() utility (asof.py) — fixed and tested in Phase 3
    # but dead code with no real caller in this repo until this.
    probables_path = Path("data/point_in_time/mlb_probable_starters.jsonl")
    probables_by_event: dict[str, dict] = {}
    if probables_path.exists() and decision_times:
        records = [
            json.loads(line) for line in probables_path.read_text().splitlines() if line.strip()
        ]
        probables_by_event = point_in_time_probable_starters(decision_times, records)
    print(f"5b. {len(probables_by_event)} real probable-starter records loaded (point-in-time filtered)")

    # Real depth data doesn't exist yet (Checkpoint 8). This previously set
    # min_depth_units=0.0 to work around that -- which is exactly the
    # fabrication CLAUDE.md Part 3 SS2 forbids ("do not describe the price
    # as depth-checked executable; fail economic qualification"). Every real
    # candidate now sets depth_available=False (mlb_market_matching.py), so
    # decide_team_market()/decide_total() correctly return NO_BET/
    # INSUFFICIENT_DEPTH regardless of min_depth_units until a real
    # depth-providing source is integrated -- default limits, no workaround.
    limits = SizeLimits()
    report = []
    n_predictions_recorded = 0
    n_decisions_recorded = 0
    n_decisions_deduped = 0
    for g in tonight.iter_rows(named=True):
        home_abbrev = ESPN_TO_STATCAST_ABBREV.get(g["home_team"])
        away_abbrev = ESPN_TO_STATCAST_ABBREV.get(g["away_team"])
        if home_abbrev is None or away_abbrev is None:
            continue

        probable = probables_by_event.get(g["event_id"])
        if probable is None:
            report.append({
                "event_id": g["event_id"], "home_team": g["home_team"], "away_team": g["away_team"],
                "status": "no_probable_starters_available", "decision": None,
            })
            continue

        row = build_live_game_feature_row(
            g, probable["home_starter"], probable["away_starter"], pitches, starters, "data/rebuild",
        )
        if row is None:
            report.append({
                "event_id": g["event_id"], "home_team": g["home_team"], "away_team": g["away_team"],
                "status": "starter_name_not_resolved_to_real_statcast_id",
                "home_starter": probable["home_starter"], "away_starter": probable["away_starter"],
                "decision": None,
            })
            continue

        if market_rows.is_empty():
            total_lines, spread_pairs, candidates = [], [], []
        else:
            resolved_event_id = resolve_polymarket_event_id(market_rows, g["home_team"], g["away_team"])
            total_lines = real_total_lines(market_rows, resolved_event_id) if resolved_event_id else []
            spread_pairs = real_spread_line_side_pairs(market_rows, resolved_event_id) if resolved_event_id else []
            candidates = real_market_candidates(market_rows, g["home_team"], g["away_team"])

        forecast = build_forecast(model, row, total_lines, spread_pairs, bootstrap=bootstrap)
        decisions = evaluate_game(forecast, candidates, limits)
        bet_decisions = [d for d in decisions if d.action == "BET"]

        # decision_time_utc is derived from the event's own start time and
        # the "late" horizon definition (start minus 60 minutes), not
        # wall-clock "now" -- this is what makes an identical rerun of this
        # script against the same slate genuinely idempotent in the ledger
        # (same event + same horizon always yields the same decision
        # timestamp), rather than every invocation minting a fresh row.
        # Reuses the same value already computed above for the probable-
        # starter point-in-time join, rather than recomputing it.
        decision_time_utc = decision_times[g["event_id"]].isoformat()

        _, pred_created = ledger.record_prediction(
            run_id=run_id, sport="mlb", event_id=g["event_id"], horizon=HORIZON_LATE,
            decision_time_utc=decision_time_utc, forecast=forecast,
        )
        n_predictions_recorded += 1 if pred_created else 0

        # One market_evaluation row per real candidate this game actually
        # saw, keyed by (market_id, market_type, team_or_side, line) so each
        # decision below can look up the ledger row id of the exact
        # candidate it selected/evaluated without relying on Python object
        # identity surviving through evaluate_game().
        market_eval_ids: dict[tuple, int] = {}
        for c in candidates:
            eval_row_id = ledger.record_market_evaluation(
                run_id=run_id, sport="mlb", event_id=g["event_id"],
                evaluation=c, decision_time_utc=decision_time_utc,
            )
            market_eval_ids[(c.market_id, c.market_type, c.team_or_side, c.line)] = eval_row_id

        # A real content hash of exactly the market evidence this decision
        # was made from -- part of trade_decisions' required idempotency key
        # (sport, event_id, horizon, decision_time_utc, model_artifact_hash,
        # market_snapshot_hash, decision_policy_version), so a rerun against
        # unchanged books is a no-op, and a rerun after the book moves
        # appends a new, distinguishable decision instead of silently
        # colliding with the stale one. (quote_age_seconds is deliberately
        # excluded from this hash -- see real_market_snapshot_hash().)
        market_snapshot_hash = real_market_snapshot_hash(g["event_id"], candidates)

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
                run_id=run_id, sport="mlb", event_id=g["event_id"], horizon=HORIZON_LATE,
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

        report.append({
            "event_id": g["event_id"], "home_team": g["home_team"], "away_team": g["away_team"],
            "predicted_winner": forecast.predicted_winner,
            "home_win_prob": forecast.calibrated_probabilities["home"],
            "away_win_prob": forecast.calibrated_probabilities["away"],
            "expected_home_score": forecast.expected_home_score,
            "expected_away_score": forecast.expected_away_score,
            "candidate_markets_evaluated": len(candidates),
            # evaluated_market (not selected_market) is used here so a
            # NO_BET row still shows the exact market/side/line/ask that
            # was rejected -- previously every NO_BET showed null for all
            # of these (selected_market is always None on NO_BET by
            # design), so the report couldn't distinguish "evaluated the
            # away spread at 45c and rejected it" from having evaluated
            # nothing at all. Real audit-trail gap, fixed in decision.py.
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

    print(f"\n6. Evaluated {len(report)} of {tonight.height} real scheduled games")
    for g in report:
        status = g.get("status")
        if status:
            print(f"   {g['away_team']} @ {g['home_team']}: SKIPPED ({status})")
            continue
        print(f"   {g['away_team']} @ {g['home_team']}: predicted={g['predicted_winner']} "
              f"(home {g['home_win_prob']:.1%}/away {g['away_win_prob']:.1%}), "
              f"{g['candidate_markets_evaluated']} markets evaluated, {g['bets']} BET")

    print(f"\n6b. Shadow ledger: {n_predictions_recorded} new predictions, "
          f"{n_decisions_recorded} new trade decisions, "
          f"{n_decisions_deduped} trade decisions deduped as idempotent reruns")

    out_path = Path("outputs/rebuild") / f"mlb_shadow_run_{target_date}.json"
    out_path.write_text(json.dumps({
        "date": target_date, "model_train_games": train_n,
        "no_real_order_submitted": True,
        "shadow_ledger_run_id": run_id,
        "shadow_ledger_db": "data/rebuild/shadow.db",
        "predictions_recorded": n_predictions_recorded,
        "trade_decisions_recorded": n_decisions_recorded,
        "trade_decisions_deduped": n_decisions_deduped,
        "games": report,
    }, indent=2, default=str))
    print(f"\n7. Full report saved to {out_path}")
    print("8. No real order adapter was imported or called by this script.")
    ledger.close()


if __name__ == "__main__":
    main()
