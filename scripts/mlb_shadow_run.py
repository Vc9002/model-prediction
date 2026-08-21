"""One-command MLB production-ready shadow run (CLAUDE.md Checkpoint 9).

Thin wrapper only: every real step (load scheduled games + historical
features, train, predict, collect fresh markets, match candidates, decide)
is implemented once in mlb_shadow_pipeline.py's load_state/predict_stage/
match_markets_stage/decide_stage, and reused as-is by both this script and
the shared multi-sport CLI's MLBAdapter (sport_adapter.py) --
scripts/rebuild_shadow_cli.py --sport mlb --date ... --horizon late runs
the identical real pipeline through that adapter. This script exists only
to preserve the original one-command entry point and operator report
shape; it holds no orchestration logic of its own.

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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.rebuild.collectors import MLBCollector
from model_prediction.rebuild.economic import SizeLimits
from model_prediction.rebuild.metadata import MetadataDB
from model_prediction.rebuild.mlb_shadow_pipeline import (
    HORIZON_LATE,
    decide_stage,
    match_markets_stage,
    predict_stage,
)
from model_prediction.rebuild.mlb_shadow_pipeline import load_state as pipeline_load_state
from model_prediction.rebuild.shadow_ledger import ShadowLedger

DATA_ROOT = "data/rebuild"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()
    target_date = args.date

    ledger = ShadowLedger(f"{DATA_ROOT}/shadow.db")
    run_id = ledger.record_run("mlb", run_type="shadow", horizon=HORIZON_LATE, params={"date": target_date})
    print(f"0. Shadow ledger run {run_id} started ({DATA_ROOT}/shadow.db)")

    state = pipeline_load_state(DATA_ROOT, target_date)
    if state is None:
        print("No scheduled games (or no scoreboard ever collected). Stopping honestly.")
        ledger.close()
        sys.exit(0)
    print(f"1. {state.tonight.height} real scheduled MLB games for {target_date}")

    predict_result = predict_stage(state, DATA_ROOT, ledger=ledger, run_id=run_id)
    print(
        f"3-4. Frozen candidate {predict_result['candidate_version']} loaded "
        f"(bundle {predict_result['frozen_bundle_hash'][:12]}); "
        f"{predict_result['games_predicted']} of {predict_result['games_total']} real "
        f"scheduled games committed a feature row before the decision cutoff"
    )

    meta = MetadataDB(f"{DATA_ROOT}/metadata.db")
    collector = MLBCollector(DATA_ROOT, meta)
    markets_result = match_markets_stage(state, DATA_ROOT, collector, ledger=ledger, run_id=run_id)
    print(
        f"4b. Live Polymarket collection: {markets_result['polymarket_collect_status']} "
        f"({markets_result['real_market_rows']} real full-game rows matched across "
        f"{markets_result['games_matched']} games)"
    )

    limits = SizeLimits()
    decide_result = decide_stage(state, ledger=ledger, run_id=run_id, limits=limits)

    # Combine decide_stage's real per-game decisions with predict_stage's
    # real skip reasons, in the original scheduled order -- matches this
    # script's original report shape (one entry per scheduled game, decided
    # or skipped) even though the two real stage functions return them
    # separately.
    games_by_event = {g["event_id"]: g for g in decide_result["games"]}
    team_names = {
        g["event_id"]: (g["home_team"], g["away_team"]) for g in state.tonight.iter_rows(named=True)
    }
    report = []
    for g in state.tonight.iter_rows(named=True):
        event_id = g["event_id"]
        if event_id in games_by_event:
            report.append(games_by_event[event_id])
        elif event_id in state.skipped:
            home_team, away_team = team_names[event_id]
            report.append(
                {
                    "event_id": event_id,
                    "home_team": home_team,
                    "away_team": away_team,
                    "status": state.skipped[event_id],
                    "decision": None,
                }
            )

    print(f"\n6. Evaluated {len(report)} of {state.tonight.height} real scheduled games")
    for g in report:
        status = g.get("status")
        if status:
            print(f"   {g['away_team']} @ {g['home_team']}: SKIPPED ({status})")
            continue
        print(
            f"   {g['away_team']} @ {g['home_team']}: predicted={g['predicted_winner']} "
            f"(home {g['home_win_prob']:.1%}/away {g['away_win_prob']:.1%}), "
            f"{g['candidate_markets_evaluated']} markets evaluated, {g['bets']} BET"
        )

    print(
        f"\n6b. Shadow ledger: {decide_result['predictions_recorded']} new predictions, "
        f"{decide_result['trade_decisions_recorded']} new trade decisions, "
        f"{decide_result['trade_decisions_deduped']} trade decisions deduped as idempotent reruns"
    )

    out_path = Path("outputs/rebuild") / f"mlb_shadow_run_{target_date}.json"
    out_path.write_text(
        json.dumps(
            {
                "date": target_date,
                "model_train_games": predict_result["train_games"],
                "no_real_order_submitted": True,
                "shadow_ledger_run_id": run_id,
                "shadow_ledger_db": f"{DATA_ROOT}/shadow.db",
                "predictions_recorded": decide_result["predictions_recorded"],
                "trade_decisions_recorded": decide_result["trade_decisions_recorded"],
                "trade_decisions_deduped": decide_result["trade_decisions_deduped"],
                "games": report,
            },
            indent=2,
            default=str,
        )
    )
    print(f"\n7. Full report saved to {out_path}")
    print("8. No real order adapter was imported or called by this script.")
    ledger.close()


if __name__ == "__main__":
    main()
