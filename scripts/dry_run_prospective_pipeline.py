"""End-to-End Dry Run: MLB Structural v10 Prospective Pipeline Verification.

Simulates pregame prediction generation, closing quote capture, and post-settlement append
on sample games to verify hashes, timing invariants, and ledger join integrity.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from model_prediction.domain import utc_now
from scripts.mlb_v10_daily_operational_audit import run_daily_operational_audit
from scripts.mlb_v10_prospective_shadow import (
    MLBPersistentShadowRunner,
    ProspectiveClosingMarketRecord,
    ProspectivePredictionRecord,
    ProspectiveSettlementRecord,
    append_ledger_record,
)


def run_pipeline_dry_run() -> bool:
    test_ledger = REPO_ROOT / "outputs/research/phase_f/dry_run_prospective_ledger.jsonl"
    if test_ledger.exists():
        test_ledger.unlink()

    runner = MLBPersistentShadowRunner()

    from scripts.phase_f_runner import build_mlb_slug_edt

    pred_records: list[ProspectivePredictionRecord] = []

    # 1. Generate PREDICTION records
    data_path = REPO_ROOT / "data/historical/mlb_games_all.jsonl"
    if data_path.exists():
        with open(data_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                g = json.loads(line)
                away_team = g.get("away_team") or (g.get("away") or {}).get("team_name") or ""
                home_team = g.get("home_team") or (g.get("home") or {}).get("team_name") or ""
                start_utc = g.get("event_start_utc") or g.get("game_start_utc") or ""
                if not (away_team and home_team and start_utc):
                    continue
                slug = build_mlb_slug_edt(away_team, home_team, start_utc)

                rec = runner.generate_pregame_prediction(
                    event_id=slug,
                    home_team=home_team,
                    away_team=away_team,
                    game_start_utc=start_utc,
                    snapshot=g,
                )
                if rec is not None:
                    # Simulate pregame creation timestamp (decision time)
                    rec.created_at_utc = rec.decision_utc
                    rec.prediction_hash = rec.compute_prediction_hash()
                    append_ledger_record(rec, ledger_path=test_ledger)
                    pred_records.append(rec)
                    if len(pred_records) >= 5:
                        break

    assert len(pred_records) > 0, "Failed to generate dry run predictions"

    # 2. Append CLOSING_MARKET records
    for pred in pred_records:
        closing = ProspectiveClosingMarketRecord(
            record_type="CLOSING_MARKET",
            prediction_hash=pred.prediction_hash,
            event_id=pred.event_id,
            closing_line=pred.market_line,
            closing_price=0.52,
            closing_market_hash="close_hash_123",
            closing_quote_observed_at_utc=pred.game_start_utc,
            captured_at_utc=utc_now().isoformat(),
        )
        append_ledger_record(closing, ledger_path=test_ledger)

    # 3. Append SETTLEMENT records
    for pred in pred_records:
        settlement = ProspectiveSettlementRecord(
            record_type="SETTLEMENT",
            prediction_hash=pred.prediction_hash,
            event_id=pred.event_id,
            actual_away=4.0,
            actual_home=5.0,
            actual_total=9.0,
            actual_margin=1.0,
            settled_at_utc=utc_now().isoformat(),
        )
        append_ledger_record(settlement, ledger_path=test_ledger)

    # 4. Execute Operational Integrity Audit over Dry Run Ledger
    audit = run_daily_operational_audit(ledger_path=test_ledger)

    print("Dry run audit report:")
    print(json.dumps(audit.to_dict(), indent=2))

    assert audit.operational_status == "PASS", f"Audit failed: {audit.reasons}"
    assert audit.pit_violations_count == 0, "PIT violations must be 0"
    assert audit.duplicate_predictions_count == 0, "Duplicate predictions must be 0"
    assert audit.late_predictions_count == 0, "Late predictions must be 0"
    assert audit.hash_verification_failures == 0, "Hash failures must be 0"
    assert audit.orphaned_settlements_count == 0, "Orphaned settlements must be 0"
    assert audit.predictions_written == len(pred_records)
    assert audit.settlements_written == len(pred_records)
    assert audit.closing_quotes_written == len(pred_records)

    # Clean up test ledger
    test_ledger.unlink()
    return True


if __name__ == "__main__":
    success = run_pipeline_dry_run()
    if success:
        print("PIPELINE DRY RUN PASSED WITH ZERO ERRORS!")
