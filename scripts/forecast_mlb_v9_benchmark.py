"""MLB v9 Flat Benchmark Slate Forecaster (Roadmap Phase 21-22).

Generates flat 1.0 Unit benchmark forecasts using the frozen mlb-v9 candidate model
and records them into the dedicated benchmark ledger: data/flat_v9/mlb.xlsx.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/forecast_mlb_v9_benchmark.py [--date YYYYMMDD]
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import polars as pl
from mlb_evaluator import (
    V9_FEATURE_SETS,
    V9_MANIFEST_PATH,
    V9_PARQUET_PATH,
    predict_model,
    v9_research_fit,
    verify_dataset_contract,
)

from model_prediction.data_sources.espn import ESPNMLBClient
from model_prediction.data_sources.mlb_market_odds import MarketOddsSnapshotStore
from model_prediction.domain import League, MarketType, ModelOrigin, ModelState, PickRequest, RecordType
from model_prediction.eligibility import EligibilityResult
from model_prediction.ledger import DuplicatePickError, PickLedger
from model_prediction.model_ledger import ModelLedger, _event_settlement_key

FLAT_V9_PATH = Path("data/flat_v9/mlb.xlsx")
V9_MODEL_LEDGER_PATH = Path("data/model_ledgers/mlb-v9-candidate-1.xlsx")
MARKET_SNAPSHOT_PATH = Path("data/market_odds_snapshots.jsonl")

# Rows recorded by this benchmark must NOT use "mlb-v9-candidate-1" as their
# model_version: that exact name resolves to a VOID quarantined artifact
# (config/models/research/mlb-v9-candidate-1.json, status
# VOID_INVALID_FEATURE_PROVENANCE), and any exact-contract lookup of a row's
# recorded version would collide with it (2026-08-26 audit finding). The
# benchmark retrains from the frozen cohort parquet via mlb_evaluator and
# never loads that artifact, so the recorded identity is independent of the
# quarantine -- it just must not share the name. The ledger workbook keeps
# the family name for history continuity (ledger id and artifact version are
# different axes; historical rows keep their recorded version untouched).
V9_BENCHMARK_MODEL_VERSION = "mlb-v9-benchmark"


def _existing_event_ids(ledger: PickLedger) -> set[str]:
    """Return every already-recorded event from the ledger's public row API."""
    return {str(row["event_id"]) for row in ledger.rows() if row.get("event_id")}


def run_v9_flat_forecast(date_str: str | None = None) -> list[dict]:
    """Generate and write 1.0U flat benchmark picks for MLB v9."""
    if not date_str:
        date_str = datetime.now(UTC).strftime("%Y%m%d")

    print("[v9-benchmark] Loading immutable training matrix for candidate model ...")
    _, df = verify_dataset_contract(V9_MANIFEST_PATH, V9_PARQUET_PATH)
    df_train = df.filter(pl.col("split") == "train")
    v9_features = V9_FEATURE_SETS["mlb_v9_full"]
    v9_model = v9_research_fit(df_train, v9_features)

    client = ESPNMLBClient()
    sb = client.scoreboard(date_str)
    events = sb.get("events", [])
    print(f"[v9-benchmark] Found {len(events)} live MLB events for {date_str}")

    FLAT_V9_PATH.parent.mkdir(parents=True, exist_ok=True)
    ledger = PickLedger(path=FLAT_V9_PATH)

    from model_prediction.entities import EntityRegistry
    from model_prediction.features.base import FeatureStore
    from model_prediction.features.bullpen import bullpen_profile, team_recent_relief_lines
    from model_prediction.features.elo_ratings import build_elo
    from model_prediction.features.park_factors_pit import park_factor_at
    from model_prediction.features.schedule_load import matchup_schedule_load
    from model_prediction.features.starter_history import starter_rolling_era, starter_rolling_kbb
    from model_prediction.features.trends import TrendEngine

    store = FeatureStore(Path("data"))
    history = store.load_games("mlb")
    elo = build_elo(history, "mlb")
    trends = TrendEngine(history)
    registry = EntityRegistry.from_json(Path("data/entities/teams.json"))
    market_snapshots = MarketOddsSnapshotStore(MARKET_SNAPSHOT_PATH)

    # Append-only workbook with deduplication
    ledger = PickLedger(path=FLAT_V9_PATH)
    existing_event_ids = _existing_event_ids(ledger)

    generated_picks = []

    for ev in events:
        event_id = str(ev.get("id"))
        if event_id in existing_event_ids:
            print(f"[v9-benchmark] Skipping already recorded event {event_id}")
            continue

        comps = ev.get("competitions", [{}])[0].get("competitors", [])
        if len(comps) != 2:
            continue

        home_comp = next((c for c in comps if c.get("homeAway") == "home"), comps[0])
        away_comp = next((c for c in comps if c.get("homeAway") == "away"), comps[1])
        start_str = ev.get("date") or datetime.now(UTC).isoformat()
        try:
            start_dt = datetime.fromisoformat(start_str)
        except ValueError:
            start_dt = datetime.now(UTC)

        now_utc = datetime.now(UTC)
        # Enforce strict pre-game timing: Never fabricate a pre-game timestamp post-pitch
        if now_utc >= start_dt:
            print(
                f"[v9-benchmark] NO_CALL_EVENT_STARTED: Skipping {event_id} (start={start_str}, observed={now_utc.isoformat()})"
            )
            continue

        home_raw = home_comp.get("team", {}).get("displayName") or home_comp.get("team", {}).get("name")
        away_raw = away_comp.get("team", {}).get("displayName") or away_comp.get("team", {}).get("name")

        home_team = registry.resolve(League.MLB, home_raw, start_str).canonical_name
        away_team = registry.resolve(League.MLB, away_raw, start_str).canonical_name

        home_trend = trends.team_trend(home_team)
        away_trend = trends.team_trend(away_team)

        elo_prob = elo.expected_home_win(home_team, away_team)
        trend_gap = home_trend.offensive_momentum - away_trend.offensive_momentum
        park_obj = park_factor_at(home_team, date_str)
        park_val = park_obj.get("park_factor", 1.0) if isinstance(park_obj, dict) else float(park_obj or 1.0)
        weather = 1.00

        from model_prediction.data_sources.espn import _probable

        h_prob_obj = _probable(home_comp)
        a_prob_obj = _probable(away_comp)
        home_starter = (
            h_prob_obj.get("fullName") or h_prob_obj.get("name")
            if isinstance(h_prob_obj, dict)
            else (str(h_prob_obj) if h_prob_obj else None)
        )
        away_starter = (
            a_prob_obj.get("fullName") or a_prob_obj.get("name")
            if isinstance(a_prob_obj, dict)
            else (str(a_prob_obj) if a_prob_obj else None)
        )

        era_gap = 0.0
        kbb_gap = 0.0
        if home_starter and away_starter:
            h_era = starter_rolling_era(home_starter, start_dt)
            a_era = starter_rolling_era(away_starter, start_dt)
            if h_era.get("status") == "available" and a_era.get("status") == "available":
                era_gap = round(h_era["era"] - a_era["era"], 4)

            h_kbb = starter_rolling_kbb(home_starter, start_dt)
            a_kbb = starter_rolling_kbb(away_starter, start_dt)
            if h_kbb.get("status") == "available" and a_kbb.get("status") == "available":
                kbb_gap = round(h_kbb["kbb_pct"] - a_kbb["kbb_pct"], 4)

        h_relief = team_recent_relief_lines(home_team, start_dt)
        a_relief = team_recent_relief_lines(away_team, start_dt)
        home_bp = bullpen_profile(h_relief)
        away_bp = bullpen_profile(a_relief)
        bp_weakness = home_bp.get("bullpen_weakness_index", 1.0) - away_bp.get("bullpen_weakness_index", 1.0)

        schedule = matchup_schedule_load(history, home_team, away_team, start_dt)
        rest_disp = schedule.get("rest_disparity", 0.0)

        feat_row = {
            "elo_probability": float(elo_prob),
            "trend_gap": float(trend_gap),
            "park_factor": float(park_val),
            "weather_factor": float(weather),
            "starter_era_gap": float(era_gap),
            "starter_kbb_gap": float(kbb_gap),
            "bullpen_weakness_gap": float(bp_weakness),
            "bullpen_fatigue_gap": 0.0,
            "rest_disparity": float(rest_disp),
        }

        eval_df = pl.DataFrame([feat_row])
        prob_home = float(predict_model(v9_model, eval_df, v9_features)[0])
        prob_away = 1.0 - prob_home

        # Flat pick selection
        if prob_home >= 0.50:
            selection = "home"
            pick_prob = prob_home
            selected_team = home_team
        else:
            selection = "away"
            pick_prob = prob_away
            selected_team = away_team

        observed_at_str = now_utc.isoformat()
        decision_evidence = market_snapshots.decision_quote(
            event_id,
            now_utc,
            "moneyline",
            selection,
            provider="polymarket_us",
            maximum_age=None,
        )
        if decision_evidence is None:
            print(
                f"  [flat-v9] NO_CALL_MARKET_PRICE_UNAVAILABLE: {event_id} "
                f"{selected_team} has no authenticated pregame quote known by decision time"
            )
            continue
        snapshot = decision_evidence["snapshot"]
        quote = decision_evidence["quote"]
        decision_probability = float(quote["decision_probability"])
        american_odds = int(quote["american_odds"])

        req = PickRequest(
            event_start_utc=start_str,
            event_id=event_id,
            league=League.MLB,
            away_team=away_team,
            home_team=home_team,
            market_type=MarketType.MONEYLINE,
            selection=selection,
            line=None,
            sportsbook="polymarket_us",
            american_odds=american_odds,
            model_probability=round(pick_prob, 4),
            model_uncertainty=0.035,
            model_version=V9_BENCHMARK_MODEL_VERSION,
            rationale="MLB v9 Flat Benchmark Candidate Forecast",
            risks="Model candidate under prospective shadow evaluation",
            model_origin=ModelOrigin.STATISTICAL_MODEL,
            model_state=ModelState.RESEARCH,
            observed_at_utc=observed_at_str,
            market_quote_observed_at_utc=decision_evidence["observed_at_utc"],
            market_quote_timestamp_valid=True,
            market_quote_source="polymarket_us",
            market_quote_provenance="decision_time_executable_quote",
            market_quote_reconstructed=bool(snapshot.get("raw_response", {}).get("reconstructed", False)),
            market_snapshot_hash=str(snapshot["snapshot_hash"]),
            market_snapshot_archive_path=str(snapshot["snapshot_archive_path"]),
            market_snapshot_record_id=str(snapshot["snapshot_record_id"]),
            market_probability_at_decision=decision_probability,
            record_source="live_forecast",
            is_backfill=False,
        )

        away_canonical = registry.resolve(League.MLB, away_team, start_str)
        home_canonical = registry.resolve(League.MLB, home_team, start_str)

        elig = EligibilityResult(
            record_type=RecordType.QUALIFIED_SHADOW_CALL,
            decision="CALL",
            reason_code="FLAT_BENCHMARK_TRACK",
            units=1.0,
            confidence_score=int(pick_prob * 100),
            edge=round(pick_prob - decision_probability, 4),
            adjusted_edge=round(pick_prob - decision_probability, 4),
            away_team=away_canonical,
            home_team=home_canonical,
        )

        try:
            ledger.append_evaluated(req, elig, now=now_utc)
        except DuplicatePickError as error:
            # The pre-read makes normal reruns idempotent; this guard closes
            # the race if another process appends the same event afterward.
            existing_event_ids.add(event_id)
            print(f"[v9-benchmark] Skipping duplicate {event_id} ({error.pick_id})")
            continue
        generated_picks.append((req, now_utc))
        existing_event_ids.add(event_id)
        print(
            f"  [flat-v9] Logged {away_team} @ {home_team} -> {selected_team} "
            f"({selection.upper()}) (model={pick_prob:.3f}, entry={decision_probability:.3f}, 1.0U)"
        )

    # Also sync to data/model_ledgers/mlb-v9-candidate-1.xlsx so dashboard Evidence/Model Health tabs find it
    model_ledger = ModelLedger(V9_MODEL_LEDGER_PATH)
    existing_model_keys = {
        (row["event_id"], row["model_version"], row["observed_at_utc"], row["selection"])
        for row in model_ledger.rows()
    }
    for req, pregame_ts in generated_picks:
        key = (req.event_id, req.model_version, req.observed_at_utc or "", req.selection)
        if key in existing_model_keys:
            continue
        rec = {
            "model_id": "mlb-v9-candidate-1",
            "model_version": req.model_version,
            "event_id": req.event_id,
            "event_start_utc": req.event_start_utc,
            "observed_at_utc": req.observed_at_utc,
            "league": req.league.value,
            "away_team": req.away_team,
            "home_team": req.home_team,
            "market_type": req.market_type.value,
            "selection": req.selection,
            "line": req.line,
            "decision_price": req.market_probability_at_decision,
            "model_market_difference": round(
                req.model_probability - float(req.market_probability_at_decision), 6
            ),
            "model_probability": req.model_probability,
            "model_uncertainty": req.model_uncertainty,
            "status": "open",
        }
        model_ledger.append_prediction(rec)
        existing_model_keys.add(key)

    # Rebuild / Refresh Dashboard SQLite cache
    from model_prediction.dashboard_cache import DashboardCache

    dc = DashboardCache(Path("data"))
    dc.refresh(force=True)

    print(
        f"[v9-benchmark] Successfully recorded {len(generated_picks)} flat benchmark picks to "
        f"{FLAT_V9_PATH} and {V9_MODEL_LEDGER_PATH}"
    )
    return [req.__dict__ for req, _ in generated_picks]


def settle_v9_flat_ledger() -> dict[str, Any]:
    """Settle completed games in data/flat_v9/mlb.xlsx against ESPN MLB results."""
    from model_prediction.data_sources.espn import ESPNMLBClient

    ledger = PickLedger(path=FLAT_V9_PATH)
    client = ESPNMLBClient()
    open_rows = [r for r in ledger.rows() if r.get("status") == "open"]
    print(f"[v9-benchmark] Checking {len(open_rows)} open picks in {FLAT_V9_PATH} ...")

    settled_count = 0
    now = datetime.now(UTC)

    for r in open_rows:
        event_id = str(r.get("event_id", ""))
        pick_id = str(r.get("pick_id", ""))
        start_str = r.get("event_start_utc", "")
        try:
            start_dt = datetime.fromisoformat(start_str)
        except (ValueError, TypeError):
            start_dt = now

        # Only check games whose start time has arrived
        if start_dt > now:
            continue

        game_date = start_dt.strftime("%Y%m%d")
        sb = client.scoreboard(game_date)
        for ev in sb.get("events", []):
            if str(ev.get("id")) == event_id:
                status_type = ev.get("status", {}).get("type", {})
                if status_type.get("completed", False):
                    comps = ev.get("competitions", [{}])[0].get("competitors", [])
                    home_comp = next((c for c in comps if c.get("homeAway") == "home"), comps[0])
                    away_comp = next((c for c in comps if c.get("homeAway") == "away"), comps[1])
                    home_score = int(home_comp.get("score", 0))
                    away_score = int(away_comp.get("score", 0))

                    settled = ledger.settle(
                        pick_id=pick_id,
                        away_score=away_score,
                        home_score=home_score,
                    )
                    if V9_MODEL_LEDGER_PATH.exists():
                        ModelLedger(V9_MODEL_LEDGER_PATH).settle_event(
                            _event_settlement_key(settled),
                            result=settled["result"],
                            pnl_units=float(settled["pnl_units"]),
                            probability_clv=(
                                float(settled["probability_clv"]) if settled.get("probability_clv") else None
                            ),
                        )
                    settled_count += 1
                    print(
                        f"  [settled] Pick {pick_id} (event {event_id}): Away {away_score} - Home {home_score}"
                    )
                break

    print(f"[v9-benchmark] Settled {settled_count} picks in {FLAT_V9_PATH}")
    return {"settled": settled_count, "remaining_open": len(open_rows) - settled_count}


def main() -> int:
    parser = argparse.ArgumentParser(description="Forecast & Settle MLB Flat v9 Benchmark Ledger")
    parser.add_argument("--date", default=None, help="Date in YYYYMMDD format to forecast")
    parser.add_argument("--settle", action="store_true", help="Settle completed games in flat v9 ledger")
    args = parser.parse_args()

    if args.settle:
        settle_v9_flat_ledger()
    else:
        run_v9_flat_forecast(args.date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
