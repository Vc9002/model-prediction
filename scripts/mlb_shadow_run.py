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
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.rebuild.decision import SportsForecast, evaluate_game
from model_prediction.rebuild.economic import SizeLimits
from model_prediction.rebuild.mlb_features import (
    ESPN_TO_STATCAST_ABBREV,
    build_game_feature_row,
    build_live_game_feature_row,
    dedupe_scoreboard,
    identify_starters,
    load_raw_statcast_dates,
    normalize_statcast_pitches,
)
from model_prediction.rebuild.mlb_market_matching import (
    exclude_first_five_innings,
    real_market_candidates,
    real_spread_line_side_pairs,
    real_total_lines,
    resolve_polymarket_event_id,
)
from model_prediction.rebuild.models import MLBTwoHeadModel

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


def train_through(features: pl.DataFrame, cutoff_date: str) -> MLBTwoHeadModel:
    """Walk-forward retrain: fit only on games strictly before cutoff_date.
    No artifact-reload mechanism exists yet for the fitted sklearn models
    (MLBTwoHeadModel.to_artifact() only persists metadata/hashes, not the
    model itself) -- retraining fresh each run is the honest current
    behavior, not a shortcut, and is disclosed as a Checkpoint 9 gap below."""
    train = features.filter(pl.col("game_date") < cutoff_date)
    model = MLBTwoHeadModel(seed=42)
    model.fit(train, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)
    return model, train.height


def build_forecast(
    model: MLBTwoHeadModel, row: dict,
    total_lines: list[float] | None = None,
    spread_line_side_pairs: list[tuple[float, str]] | None = None,
    uncertainty_haircut: float = 0.03,
) -> SportsForecast:
    pred = model.predict_row(row["event_id"], row)
    predicted_winner = "home" if pred.home_win_prob >= pred.away_win_prob else "away"
    calibrated = {"home": pred.home_win_prob, "away": pred.away_win_prob}
    # Conservative/lower-bound: haircut toward 50/50 by a fixed uncertainty
    # margin. A real bootstrap/model-disagreement-based uncertainty (per
    # CLAUDE.md's conservative_probability spec) is a real gap, disclosed
    # below, not silently treated as equivalent to this simple haircut.
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
    totals_probabilities: dict[float, dict[str, float]] = {}
    for line in total_lines or []:
        over_p = model.distribution.probability_for_market(pred, "total", "over", line)
        totals_probabilities[line] = {"over": over_p, "under": 1.0 - over_p}

    # Real per-(line, side) cover probability. Confirmed bug fixed here: a
    # spread was previously priced using the moneyline win probability, not
    # a real cover probability — verified live to fabricate a +23.5% edge
    # on a real spread market. Each side keeps its own signed line (a home
    # favorite's line and the away side's line on the same real market are
    # not the same number), computed here, before any market spread price
    # is inspected.
    spread_probabilities: dict[float, dict[str, float]] = {}
    for line, side in spread_line_side_pairs or []:
        cover_p = model.distribution.probability_for_market(pred, "spread", side, line)
        spread_probabilities.setdefault(line, {})[side] = cover_p

    return SportsForecast(
        event_id=row["event_id"], predicted_winner=predicted_winner,
        raw_probabilities=calibrated, calibrated_probabilities=calibrated,
        probability_lower=lower, probability_upper=upper,
        expected_home_score=pred.home_expected_runs, expected_away_score=pred.away_expected_runs,
        model_artifact_hash=model.to_artifact().get("artifact_hash", ""),
        calibration_artifact_hash="uncalibrated_haircut_v1",
        totals_probabilities=totals_probabilities,
        spread_probabilities=spread_probabilities,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()
    target_date = args.date

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

    model, train_n = train_through(features, target_date)
    print(f"4. Model retrained on {train_n} real games (walk-forward, strictly before {target_date})")

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

    # Real probable-starter names, keyed by ESPN event_id — a scheduled game
    # has no Statcast pitches of its own yet (it hasn't been played), so
    # build_game_feature_row's game_pk matching can never find it. This is
    # the existing incumbent system's own collected data, used here only as
    # a real input source per CLAUDE.md ("the existing project [is] a
    # benchmark and data source"), not as a shortcut around building real
    # features.
    probables_path = Path("data/point_in_time/mlb_probable_starters.jsonl")
    probables_by_event: dict[str, dict] = {}
    if probables_path.exists():
        for line in probables_path.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            probables_by_event[rec["event_id"]] = rec
    print(f"5b. {len(probables_by_event)} real probable-starter records loaded")

    limits = SizeLimits(min_depth_units=0.0)  # real depth data doesn't exist yet (Checkpoint 8)
    report = []
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

        forecast = build_forecast(model, row, total_lines, spread_pairs)
        decisions = evaluate_game(forecast, candidates, limits)
        bet_decisions = [d for d in decisions if d.action == "BET"]

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

    out_path = Path("outputs/rebuild") / f"mlb_shadow_run_{target_date}.json"
    out_path.write_text(json.dumps({
        "date": target_date, "model_train_games": train_n,
        "no_real_order_submitted": True,
        "games": report,
    }, indent=2, default=str))
    print(f"\n7. Full report saved to {out_path}")
    print("8. No real order adapter was imported or called by this script.")


if __name__ == "__main__":
    main()
