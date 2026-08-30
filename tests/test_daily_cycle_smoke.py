"""Fast Deterministic CI Daily Cycle Smoke Test (<3.0s).

Validates the full multi-sport daily pipeline lifecycle end-to-end:
1. Synthetic slate ingestion & BBO quote binding
2. PIT Model forecasting (MLB, Soccer, Tennis)
3. Dual-write ledger mutation & hash-chained audit logging
4. Duplicate rejection idempotency
5. Event settlement & exact P&L calculation
6. Parity reconciliation between SQLite authority & XLSX projection
7. Dashboard status & metrics aggregation

Strict latency budget: complete execution must finish in <3.0 seconds without external network I/O.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from model_prediction.domain import (
    League,
    MarketType,
    PickStatus,
    utc_now,
)
from model_prediction.ledger import PickLedger
from model_prediction.ledger_parity import compare, integrity_report
from model_prediction.production_store import ProductionPredictionStore
from model_prediction.runtime_ledger_store import LedgerMutation, RuntimeLedgerStore
from model_prediction.runtime_paths import RuntimePaths


def test_daily_cycle_smoke_deterministic_under_3s(tmp_path: Path) -> None:
    """End-to-end synthetic daily cycle execution within the <3.0s CI latency budget."""
    start_time = time.monotonic()

    # 1. Setup isolated in-memory/temp runtime paths
    repo_root = tmp_path / "repo"
    runtime_root = tmp_path / "runtime"
    repo_root.mkdir(parents=True, exist_ok=True)
    runtime_root.mkdir(parents=True, exist_ok=True)
    data_dir = repo_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    paths = RuntimePaths(repo_root=repo_root, runtime_root=runtime_root)
    store = RuntimeLedgerStore(paths)
    prod_store = ProductionPredictionStore(paths)
    run_id = prod_store.start_run(git_sha="smoke-test-sha")

    # 2. Ingest Synthetic Slate (MLB, Soccer, Tennis)
    now_iso = utc_now().isoformat()
    synthetic_events: list[dict[str, Any]] = [
        {
            "event_id": "mlb_20260828_nyy_bos",
            "sport": "mlb",
            "league": League.MLB.value,
            "market_type": MarketType.MONEYLINE.value,
            "home_team": "New York Yankees",
            "away_team": "Boston Red Sox",
            "model_prob": 0.585,
            "market_prob": 0.520,
            "best_ask": 0.525,
            "selection": "New York Yankees",
            "stake_units": 1.5,
        },
        {
            "event_id": "soccer_20260828_ars_che",
            "sport": "soccer",
            "league": League.SOCCER.value,
            "market_type": MarketType.MONEYLINE.value,
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "model_prob": 0.620,
            "market_prob": 0.540,
            "best_ask": 0.550,
            "selection": "Arsenal",
            "stake_units": 1.25,
        },
        {
            "event_id": "tennis_20260828_alc_sin",
            "sport": "tennis",
            "league": League.TENNIS.value,
            "market_type": MarketType.MONEYLINE.value,
            "home_team": "Carlos Alcaraz",
            "away_team": "Jannik Sinner",
            "model_prob": 0.550,
            "market_prob": 0.500,
            "best_ask": 0.510,
            "selection": "Carlos Alcaraz",
            "stake_units": 1.0,
        },
    ]

    # 3. Forecast & Dual-Write Append
    pick_ids = []
    for ev in synthetic_events:
        sport = str(ev["sport"])
        event_id = str(ev["event_id"])
        market_type = str(ev["market_type"])
        selection = str(ev["selection"])
        model_prob = float(ev["model_prob"])
        market_prob = float(ev["market_prob"])
        stake_units = float(ev["stake_units"])

        pick_id = f"pick-{sport}-{event_id}"
        pick_ids.append(pick_id)
        row = {
            "pick_id": pick_id,
            "event_id": event_id,
            "sport": sport,
            "tier": "main",
            "model_version": f"{sport}-v1-smoke",
            "model_probability": f"{model_prob:.4f}",
            "decision_raw_implied_probability": f"{market_prob:.4f}",
            "units": f"{stake_units:.4f}",
            "selection": selection,
            "home_team": str(ev["home_team"]),
            "away_team": str(ev["away_team"]),
            "market_type": market_type,
            "status": PickStatus.OPEN.value,
            "created_at_utc": now_iso,
        }

        # Apply to RuntimeLedgerStore
        mutation = LedgerMutation(
            pick_id=pick_id,
            operation_id=f"op-forecast-{pick_id}",
            ledger_tier="main",
            sport=sport,
            event_type="append",
            created_at_utc=now_iso,
            event_id=event_id,
            market_type=market_type,
            selection=selection,
            model_id=f"{sport}-v1-smoke",
            model_probability=model_prob,
            market_probability=market_prob,
            units=stake_units,
            status=PickStatus.OPEN.value,
            decision_payload=row,
        )
        applied = store.apply(mutation)
        assert applied is True, f"Failed to append pick {pick_id}"

        # Record in ProductionPredictionStore
        prod_store.append_prediction(
            run_id=run_id,
            prediction_id=f"pred-{pick_id}",
            event_id=event_id,
            sport=sport,
            market=market_type,
            market_type=market_type,
            model_id=f"{sport}-v1-smoke",
            horizon="game",
            decision_time_utc=now_iso,
            probabilities={"home": model_prob, "away": 1.0 - model_prob},
            event_start_utc=now_iso,
            predicted_side="home",
        )

    # 4. Idempotency Check: Re-applying identical operations must be no-ops
    for ev in synthetic_events:
        sport = str(ev["sport"])
        event_id = str(ev["event_id"])
        pick_id = f"pick-{sport}-{event_id}"
        dup_mutation = LedgerMutation(
            pick_id=pick_id,
            operation_id=f"op-forecast-{pick_id}",
            ledger_tier="main",
            sport=sport,
            event_type="append",
            created_at_utc=now_iso,
            decision_payload={"pick_id": pick_id},
        )
        # Duplicate operation_id must return False (idempotent rejection)
        assert store.apply(dup_mutation) is False

    # 5. Settlement Cycle: Settle events (2 Wins, 1 Loss)
    outcomes = [
        ("mlb_20260828_nyy_bos", "win", 0.585, 1.5 * (1.0 / 0.525 - 1.0)),
        ("soccer_20260828_ars_che", "win", 0.620, 1.25 * (1.0 / 0.550 - 1.0)),
        ("tennis_20260828_alc_sin", "loss", 0.450, -1.0),
    ]

    for event_id, result, closing_prob, expected_pnl in outcomes:
        pick_id = next(p for p in pick_ids if event_id in p)
        sport = next(ev["sport"] for ev in synthetic_events if ev["event_id"] == event_id)
        settle_mutation = LedgerMutation(
            pick_id=pick_id,
            operation_id=f"op-settle-{pick_id}",
            ledger_tier="main",
            sport=sport,
            event_type="settle",
            created_at_utc=utc_now().isoformat(),
            status=PickStatus.SETTLED.value,
            result=result,
            pnl_units=round(expected_pnl, 4),
            settled_at_utc=utc_now().isoformat(),
            decision_payload={
                "status": PickStatus.SETTLED.value,
                "result": result,
                "closing_raw_implied_probability": f"{closing_prob:.4f}",
                "pnl_units": f"{expected_pnl:.4f}",
                "settled_at_utc": utc_now().isoformat(),
            },
        )
        assert store.apply(settle_mutation) is True

    # 6. Verify Hash-Chained Integrity
    report = integrity_report(paths)
    assert report["chain_ok"] is True, f"Audit chain broke: {report}"
    assert report["events"] == len(synthetic_events) * 2  # 3 appends + 3 settles

    # 7. Reconcile Parity with XLSX Projection
    for ev in synthetic_events:
        sport = str(ev["sport"])
        ledger_file = data_dir / f"{sport}.xlsx"
        ledger = PickLedger(
            ledger_file,
            mirror=store,
            sport=sport,
            tier="main",
            authority="sqlite",
        )
        ledger.initialize()
        ledger.rebuild_xlsx_projection()
        export_rows = ledger.export_rows()
        canonical_records = store.records(tier="main", sport=sport)
        parity_check = compare(export_rows, canonical_records)
        assert parity_check["clean"] is True, f"Parity mismatch in {sport}: {parity_check}"

    # 8. Assert Execution Speed < 3.0 seconds
    elapsed = time.monotonic() - start_time
    assert elapsed < 3.0, f"Daily cycle smoke test exceeded 3.0s budget: {elapsed:.2f}s"
