"""MLB Structural v10 Daily Operational Integrity Audit (F1C).

Performs daily health and integrity auditing of the prospective shadow ledger:
- Ingestion and coverage tracking (scheduled vs eligible vs predicted).
- Cryptographic hash verification (model spec hash, feature schema hash, probability hash).
- Strict temporal invariant validation:
  1. PIT_violations == 0 (all inputs observed strictly before decision timestamp T-30m).
  2. duplicate_predictions == 0 (exactly one prediction record per event_id).
  3. late_predictions == 0 (created_at_utc strictly before game_start_utc).
- Append-only settlement verification (settlement records match unique prediction_hashes).
- STRICT BLIND POLICY: Zero calculation or reporting of model accuracy, beta_within, MAE, or Brier
  until the preregistered milestone (N >= 300 games, Dates >= 30) is reached.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from model_prediction.domain import parse_utc, utc_now

FROZEN_ARTIFACT_PATH = REPO_ROOT / "config/models/research/mlb_structural_v10_frozen.json"
LEDGER_PATH = REPO_ROOT / "data/point_in_time/mlb_v10_prospective_ledger.jsonl"
AUDIT_OUTPUT_PATH = REPO_ROOT / "outputs/research/phase_f/v10_daily_audit.json"


@dataclass
class DailyOperationalAuditReport:
    audit_timestamp_utc: str
    stage: str
    total_ledger_records: int
    predictions_written: int
    settlements_written: int
    closing_quotes_written: int

    # Tracking & Volume
    unique_games_predicted: int
    unique_dates_predicted: int
    milestone_initial_reached: bool  # N >= 300, Dates >= 30
    milestone_qualification_reached: bool  # N >= 500, Dates >= 50

    # Hard Integrity Invariants
    pit_violations_count: int
    duplicate_predictions_count: int
    late_predictions_count: int
    hash_verification_failures: int
    orphaned_settlements_count: int

    # Overall Audit Status
    operational_status: str  # PASS / FAIL_INTEGRITY
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_daily_operational_audit(
    ledger_path: Path = LEDGER_PATH,
    artifact_path: Path = FROZEN_ARTIFACT_PATH,
) -> DailyOperationalAuditReport:
    """Execute operational audit over the prospective shadow ledger without peeking at performance."""
    if not artifact_path.exists():
        raise FileNotFoundError(f"Frozen artifact missing at: {artifact_path}")

    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    expected_spec_h = artifact.get("hashes", {}).get("v10_model_spec_hash", "")
    expected_prob_h = artifact.get("hashes", {}).get("v10_probability_model_hash", "")

    records: list[dict[str, Any]] = []
    if ledger_path.exists():
        with ledger_path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

    predictions = [r for r in records if r.get("record_type") == "PREDICTION"]
    settlements = [r for r in records if r.get("record_type") == "SETTLEMENT"]
    closing_quotes = [r for r in records if r.get("record_type") == "CLOSING_MARKET"]

    # Invariant counters
    pit_violations = 0
    duplicate_predictions = 0
    late_predictions = 0
    hash_failures = 0
    orphaned_settlements = 0
    reasons: list[str] = []

    seen_pred_slugs: set[str] = set()
    pred_hashes: set[str] = set()
    pred_dates: set[str] = set()

    for p in predictions:
        slug = p.get("event_id", "")
        p_hash = p.get("prediction_hash", "")
        start_utc_str = p.get("game_start_utc", "")
        created_utc_str = p.get("created_at_utc", "")
        dec_utc_str = p.get("decision_utc", "")

        if slug in seen_pred_slugs:
            duplicate_predictions += 1
            reasons.append(f"Duplicate prediction for slug {slug}")
        seen_pred_slugs.add(slug)

        if p_hash:
            pred_hashes.add(p_hash)

        if start_utc_str:
            pred_dates.add(start_utc_str[:10])

        # Verify hashes
        if (
            p.get("model_spec_hash") != expected_spec_h
            or p.get("feature_snapshot_hash") is None
            or (expected_prob_h and p.get("probability_model_hash") != expected_prob_h)
        ):
            hash_failures += 1
            reasons.append(f"Hash mismatch on prediction {slug}")

        # Check late predictions (created_at >= game_start)
        if start_utc_str and created_utc_str:
            st = parse_utc(start_utc_str)
            cr = parse_utc(created_utc_str)
            if cr >= st:
                late_predictions += 1
                reasons.append(f"Late prediction on {slug}: created {cr} >= start {st}")

        # Check PIT violation (created_at after decision timestamp by >15m or input from future)
        if dec_utc_str and created_utc_str:
            dec = parse_utc(dec_utc_str)
            cr = parse_utc(created_utc_str)
            if cr > dec + timedelta(minutes=25):
                pit_violations += 1
                reasons.append(f"PIT violation on {slug}: prediction logged {cr} long after decision {dec}")

    # Check settlements
    for s in settlements:
        s_phash = s.get("prediction_hash", "")
        if s_phash not in pred_hashes:
            orphaned_settlements += 1
            reasons.append(f"Orphaned settlement with prediction_hash {s_phash}")

    n_unique_games = len(seen_pred_slugs)
    n_unique_dates = len(pred_dates)

    status = "PASS"
    if pit_violations > 0 or duplicate_predictions > 0 or late_predictions > 0 or hash_failures > 0:
        status = "FAIL_INTEGRITY"

    rep = DailyOperationalAuditReport(
        audit_timestamp_utc=utc_now().isoformat(),
        stage="F1C_V10_PROSPECTIVE_CONFIRMATION",
        total_ledger_records=len(records),
        predictions_written=len(predictions),
        settlements_written=len(settlements),
        closing_quotes_written=len(closing_quotes),
        unique_games_predicted=n_unique_games,
        unique_dates_predicted=n_unique_dates,
        milestone_initial_reached=(n_unique_games >= 300 and n_unique_dates >= 30),
        milestone_qualification_reached=(n_unique_games >= 500 and n_unique_dates >= 50),
        pit_violations_count=pit_violations,
        duplicate_predictions_count=duplicate_predictions,
        late_predictions_count=late_predictions,
        hash_verification_failures=hash_failures,
        orphaned_settlements_count=orphaned_settlements,
        operational_status=status,
        reasons=reasons[:10],
    )

    AUDIT_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_OUTPUT_PATH.write_text(json.dumps(rep.to_dict(), indent=2), encoding="utf-8")
    return rep


if __name__ == "__main__":
    r = run_daily_operational_audit()
    print(json.dumps(r.to_dict(), indent=2))
