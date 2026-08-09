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
)

REGISTRY_PATH = Path("outputs/rebuild/test_consumption_registry.json")
SHADOW_DB_PATH = Path("data/rebuild/shadow.db")


@dataclass(frozen=True)
class ReadinessContract:
    test_start: str
    test_end: str | None
    consumed: bool
    minimum_predictions: int
    candidate_version: str


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
    test_start = raw.get("test_start")
    if not isinstance(test_start, str) or not test_start:
        raise ValueError("mlb_moneyline_v2 test_start is missing")
    candidate_version = raw.get("candidate_version")
    if candidate_version != MLB_V2_CANDIDATE_VERSION:
        raise ValueError("mlb_moneyline_v2 candidate_version does not match the frozen cohort")
    return ReadinessContract(
        test_start=test_start,
        test_end=raw.get("test_end") if isinstance(raw.get("test_end"), str) else None,
        consumed=bool(raw.get("consumed")),
        minimum_predictions=n,
        candidate_version=candidate_version,
    )


def count_committed_predictions(db_path: str | Path, contract: ReadinessContract) -> int:
    """Count distinct, on-time predictions for only the exact sealed cohort."""
    path = Path(db_path)
    if not path.exists():
        return 0
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        where = [
            "test_id = ?",
            "candidate_version = ?",
            "supersedes_id IS NULL",
            "julianday(prediction_observed_at_utc) >= julianday(?)",
            "julianday(created_at) >= julianday(?)",
            "julianday(prediction_observed_at_utc) <= julianday(decision_time_utc)",
            "julianday(created_at) <= julianday(decision_time_utc)",
        ]
        params: list[Any] = [
            MLB_V2_TEST_ID,
            contract.candidate_version,
            contract.test_start,
            contract.test_start,
        ]
        if contract.test_end is not None:
            where.extend([
                "julianday(prediction_observed_at_utc) <= julianday(?)",
                "julianday(created_at) <= julianday(?)",
            ])
            params.extend([contract.test_end, contract.test_end])
        row = conn.execute(
            f"SELECT COUNT(DISTINCT event_id) FROM predictions WHERE {' AND '.join(where)}",
            params,
        ).fetchone()
    except sqlite3.Error as exc:
        raise ValueError("shadow ledger is missing the prospective v2 prediction schema") from exc
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
    except ValueError as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1) from exc

    print(
        f"1. {MLB_V2_TEST_ID} cohort: candidate_version={contract.candidate_version} "
        f"test_start={contract.test_start} test_end={contract.test_end or '(open)'}"
    )
    print(f"2. Predeclared minimum committed prospective predictions: {contract.minimum_predictions}")
    print(f"3. On-time committed predictions in the sealed cohort: {count}")
    ready = count >= contract.minimum_predictions
    print(
        f"\nVerdict: {'READY_FOR_DELIBERATE_CONSUMPTION' if ready else 'NOT_READY'} "
        f"({count}/{contract.minimum_predictions} predictions)"
    )
    print("No outcome or aggregate performance metric was read or computed.")


if __name__ == "__main__":
    main()
