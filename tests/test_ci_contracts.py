"""CI-contract tests (consolidation C-final, item 16).

The checks the operator's plan called out as the missing high-value CI
contracts. They run inside the ordinary pytest suite, so the existing CI
jobs execute them on every push:
- the checked-in production.yaml resolves every enabled model
  (tests/test_production_registry.py::test_real_production_yaml_resolves_every_model)
- stale canary -> DEGRADED (tests/test_production_canary.py)
- dashboard reads without mutating DB (tests/test_consolidation_c.py)
- shadow can never write production tables (below)
- SQLite migrations from zero (below)
- worker counters land on the run row (below)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from model_prediction.production_store import ProductionPredictionStore
from model_prediction.run_supervisor import RunSupervisor
from model_prediction.runtime_paths import RuntimePaths


def test_shadow_pipeline_has_no_import_path_to_production_persistence() -> None:
    """The rebuild/shadow package must never import the production store
    or the production ledger — a shadow process can't write production
    tables if it can't even name them."""
    text = "".join(p.read_text(errors="ignore") for p in Path("src/model_prediction/rebuild").rglob("*.py"))
    forbidden = ("production_store", "ProductionPredictionStore", "ProductionLedger")
    hits = [
        line
        for line in text.splitlines()
        if any(name in line for name in forbidden) and ("import" in line or "from " in line)
    ]
    assert not hits, f"rebuild imports production persistence:\n{chr(10).join(hits)}"


def test_store_migrations_run_from_zero() -> None:
    """A fresh production.db gets the full schema and works immediately
    (run all migrations from zero, not just against a migrated legacy db)."""
    import shutil
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    paths = RuntimePaths.for_test(tmp)
    with ProductionPredictionStore(paths) as store:
        tables = {r[0] for r in store._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"runs", "predictions", "decisions", "market_snapshots"} <= tables
        run_id = store.start_run()
        assert (
            store.append_prediction(
                run_id=run_id,
                prediction_id="p1",
                event_id="e1",
                sport="WNBA",
                market="moneyline",
                market_type="moneyline",
                model_id="wnba-elo-trend-lr-v4",
                probabilities={"home": 0.6, "away": 0.4},
                decision_time_utc="2026-08-14T12:00:00+00:00",
            )
            is not None
        )
    shutil.rmtree(tmp, ignore_errors=True)


def test_supervisor_stores_worker_metrics_on_the_run_row(tmp_path: Path) -> None:
    """Observability contract: a worker's structured counters (events,
    predictions, NO_BET) land on its run row — monitoring can tell 'no
    games existed' apart from 'provider broke'."""
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    sup = RunSupervisor(
        db_path=tmp_path / "runs.db",
        paths=RuntimePaths(repo_root=repo, runtime_root=tmp_path / "runtime"),
        heartbeat_interval_seconds=0.05,
    )
    worker = (
        "import json, os; "
        "json.dump({'events_seen': 5, 'predictions': 3, 'no_bet': 2}, "
        "open(os.environ['RUN_SUPERVISOR_METRICS_PATH'], 'w'))"
    )
    code = sup.run_worker("daily", command=[sys.executable, "-c", worker])
    assert code == 0

    row = sup.latest_runs(limit=1)[0]
    assert row["status"] == "completed"
    assert json.loads(row["counters"]) == {"events_seen": 5, "predictions": 3, "no_bet": 2}
    sup.close()


def test_ci_imports_of_checked_in_config_resolve() -> None:
    """The checked-in production.yaml resolves every enabled model (the
    registry contract — this test just pins that the CI-visible config
    file is the one the registry loads)."""
    from model_prediction.production_registry import ProductionModelRegistry

    registry = ProductionModelRegistry.load(Path(__file__).resolve().parents[1])
    assert len(registry.entries) == 14  # 13 + measured-edge-totals-v3 (MLB total promotion, 2026-08-18)
    assert len(registry.problem_entries()) == 0
