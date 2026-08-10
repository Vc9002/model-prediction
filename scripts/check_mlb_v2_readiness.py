"""Sealed, outcome-blind readiness check for ``mlb_moneyline_v2``.

Only committed predictions from the exact frozen v2 cohort count.  The script
does not read scoreboard results, settlements, labels, probabilities, or any
aggregate performance field.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.rebuild.mlb_v2_artifact import (
    MLB_V2_CANDIDATE_VERSION,
    MLB_V2_TEST_ID,
    FrozenMLBV2Anchor,
    parse_frozen_mlb_v2_anchor,
)

REGISTRY_PATH = Path("outputs/rebuild/test_consumption_registry.json")
SHADOW_DB_PATH = Path("data/rebuild/shadow.db")


@dataclass(frozen=True)
class ReadinessContract:
    test_start: str
    test_end: str | None
    consumed: bool
    minimum_predictions: int
    minimum_real_games: int
    candidate_version: str
    horizon: str
    anchor: FrozenMLBV2Anchor


def load_readiness_contract(registry: Mapping[str, Any]) -> ReadinessContract:
    """Select only unsealed cohort metadata; never touch outcome metrics."""
    active_tests = registry.get("active_tests")
    if not isinstance(active_tests, Mapping):
        raise TypeError("active_tests is missing from the test registry")
    raw = active_tests.get(MLB_V2_TEST_ID)
    if not isinstance(raw, Mapping):
        raise TypeError(f"{MLB_V2_TEST_ID} is missing from the test registry")
    minimum = raw.get("minimum_sample_before_evaluation")
    if not isinstance(minimum, Mapping):
        raise TypeError("minimum_sample_before_evaluation is missing")
    n = minimum.get("n_prospective_predictions")
    if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
        raise ValueError("n_prospective_predictions must be a positive predeclared integer")
    n_real_games = minimum.get("n_real_games")
    if not isinstance(n_real_games, int) or isinstance(n_real_games, bool) or n_real_games <= 0:
        raise ValueError("n_real_games must be a positive predeclared integer")
    test_start = raw.get("test_start")
    if not isinstance(test_start, str) or not test_start:
        raise ValueError("mlb_moneyline_v2 test_start is missing")
    candidate_version = raw.get("candidate_version")
    if candidate_version != MLB_V2_CANDIDATE_VERSION:
        raise ValueError("mlb_moneyline_v2 candidate_version does not match the frozen cohort")
    consumed = raw.get("consumed")
    if not isinstance(consumed, bool):
        # ValueError, not TypeError: untrusted registry JSON content,
        # consistent with the sibling checks in this function.
        raise ValueError("mlb_moneyline_v2 consumed must be a boolean")  # noqa: TRY004
    test_end = raw.get("test_end")
    if test_end is not None and not isinstance(test_end, str):
        raise ValueError("mlb_moneyline_v2 test_end must be a string or null")
    return ReadinessContract(
        test_start=test_start,
        test_end=test_end,
        consumed=consumed,
        minimum_predictions=n,
        minimum_real_games=n_real_games,
        candidate_version=candidate_version,
        horizon="late",
        anchor=parse_frozen_mlb_v2_anchor(dict(raw)),
    )


def count_committed_predictions(db_path: str | Path, contract: ReadinessContract) -> int:
    """Count distinct, on-time predictions for only the exact sealed cohort."""
    path = Path(db_path)
    if not path.exists():
        return 0
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        where = [
            "p.sport = ?",
            "p.horizon = ?",
            "p.test_id = ?",
            "p.candidate_version = ?",
            "p.model_artifact_hash = ?",
            "p.calibration_artifact_hash = ?",
            "ma.model_name = ?",
            "ma.model_version = ?",
            "ma.manifest_sha256 = ?",
            "ma.primary_content_sha256 = ?",
            "ma.primary_artifact_sha256 = ?",
            "ma.calibrator_artifact_sha256 = ?",
            "ma.source_tree_sha256 = ?",
            "julianday(p.prediction_observed_at_utc) >= julianday(?)",
            "julianday(p.created_at) >= julianday(?)",
            "julianday(p.prediction_observed_at_utc) <= julianday(p.decision_time_utc)",
            "julianday(p.created_at) <= julianday(p.decision_time_utc)",
        ]
        anchor = contract.anchor
        params: list[Any] = [
            "mlb",
            contract.horizon,
            MLB_V2_TEST_ID,
            contract.candidate_version,
            anchor.bundle_hash,
            anchor.calibrator_hash,
            MLB_V2_TEST_ID,
            contract.candidate_version,
            anchor.bundle_manifest_sha256,
            anchor.primary_content_sha256,
            anchor.primary_artifact_sha256,
            anchor.calibrator_artifact_sha256,
            anchor.source_tree_sha256,
            contract.test_start,
            contract.test_start,
        ]
        if contract.test_end is not None:
            where.extend([
                "julianday(p.prediction_observed_at_utc) <= julianday(?)",
                "julianday(p.created_at) <= julianday(?)",
            ])
            params.extend([contract.test_end, contract.test_end])
        row = conn.execute(
            f"""WITH current_predictions AS (
                    SELECT p.* FROM predictions p
                    WHERE NOT EXISTS (
                        SELECT 1 FROM predictions correction
                        WHERE correction.supersedes_id = p.id
                    )
                )
                SELECT COUNT(DISTINCT p.event_id)
                FROM current_predictions p
                JOIN model_artifacts ma
                  ON ma.sport = p.sport AND ma.artifact_hash = p.model_artifact_hash
                JOIN calibration_artifacts ca
                  ON ca.sport = p.sport
                 AND ca.model_artifact_hash = p.model_artifact_hash
                 AND ca.calibration_hash = p.calibration_artifact_hash
                WHERE {' AND '.join(where)}""",
            params,
        ).fetchone()
    except sqlite3.Error as exc:
        raise ValueError("shadow ledger is missing the prospective v2 prediction schema") from exc
    finally:
        conn.close()
    return int(row[0]) if row is not None else 0


def count_completed_cohort_events(db_path: str | Path, contract: ReadinessContract) -> int:
    """Count completion evidence without selecting outcomes or performance fields."""
    path = Path(db_path)
    if not path.exists():
        return 0
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        row = conn.execute(
            """WITH current_predictions AS (
                    SELECT p.* FROM predictions p
                    WHERE NOT EXISTS (
                        SELECT 1 FROM predictions correction
                        WHERE correction.supersedes_id = p.id
                    )
                )
                SELECT COUNT(DISTINCT p.event_id)
                FROM current_predictions p
                WHERE p.sport='mlb' AND p.horizon=? AND p.test_id=?
                  AND p.candidate_version=? AND p.model_artifact_hash=?
                  AND p.calibration_artifact_hash=?
                  AND EXISTS (
                      SELECT 1
                      FROM trade_decisions td
                      JOIN settlements s ON s.trade_decision_id = td.id
                      WHERE td.sport=p.sport AND td.event_id=p.event_id
                        AND td.horizon=p.horizon
                        AND td.decision_time_utc=p.decision_time_utc
                        AND td.model_artifact_hash=p.model_artifact_hash
                        AND NOT EXISTS (
                            SELECT 1 FROM trade_decisions correction
                            WHERE correction.supersedes_id=td.id
                        )
                  )""",
            (
                contract.horizon, MLB_V2_TEST_ID, contract.candidate_version,
                contract.anchor.bundle_hash, contract.anchor.calibrator_hash,
            ),
        ).fetchone()
    except sqlite3.Error as exc:
        raise ValueError("shadow ledger is missing the prospective v2 completion schema") from exc
    finally:
        conn.close()
    return int(row[0]) if row is not None else 0


def main() -> None:
    if not REGISTRY_PATH.exists():
        print(f"ERROR: {REGISTRY_PATH} not found.")
        raise SystemExit(1)
    try:
        contract = load_readiness_contract(json.loads(REGISTRY_PATH.read_text()))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"ERROR: invalid sealed-test registry metadata: {exc}")
        raise SystemExit(1) from exc

    if contract.consumed:
        print(f"{MLB_V2_TEST_ID} is already consumed. A consumed test is never re-evaluated.")
        return

    try:
        count = count_committed_predictions(SHADOW_DB_PATH, contract)
        completed = count_completed_cohort_events(SHADOW_DB_PATH, contract)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1) from exc

    print(
        f"1. {MLB_V2_TEST_ID} cohort: candidate_version={contract.candidate_version} "
        f"test_start={contract.test_start} test_end={contract.test_end or '(open)'}"
    )
    print(f"2. Predeclared minimum committed prospective predictions: {contract.minimum_predictions}")
    print(f"3. On-time committed predictions in the sealed cohort: {count}")
    print(f"4. Predeclared minimum completed real games: {contract.minimum_real_games}")
    print(f"5. Completed cohort events (existence only; outcomes not selected): {completed}")
    ready = count >= contract.minimum_predictions and completed >= contract.minimum_real_games
    print(
        f"\nVerdict: {'READY_FOR_DELIBERATE_CONSUMPTION' if ready else 'NOT_READY'} "
        f"({count}/{contract.minimum_predictions} predictions; "
        f"{completed}/{contract.minimum_real_games} completed games)"
    )
    print("No outcome or aggregate performance metric was read or computed.")


if __name__ == "__main__":
    main()
