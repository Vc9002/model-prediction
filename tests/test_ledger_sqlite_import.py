from model_prediction.ledger import PickLedger
from model_prediction.runtime_ledger_store import RuntimeLedgerStore
from model_prediction.runtime_paths import RuntimePaths


def test_import_is_durable_and_idempotent_under_sqlite_authority(tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_PREDICTION_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("MODEL_PREDICTION_REPO_ROOT", raising=False)
    store = RuntimeLedgerStore(RuntimePaths.resolve(repo_root=tmp_path))
    ledger = PickLedger(tmp_path / "nba.xlsx", mirror=store, tier="main", sport="nba", authority="sqlite")
    row = {
        "pick_id": "retrospective-fixture",
        "league": "NBA",
        "event_id": "fixture",
        "market_type": "moneyline",
        "selection": "home",
        "home_team": "Home",
        "away_team": "Away",
        "units": "0",
        "status": "settled",
        "result": "win",
        "model_version": "fixture-v1",
        "created_at_utc": "2026-10-21T02:00:00Z",
        "event_start_utc": "2026-10-20T19:00:00Z",
        "is_backfill": "True",
        "record_source": "retrospective_operator_requested",
        "reason_code": "UNPRICED_RETROSPECTIVE",
    }
    assert ledger.import_rows([row], source="fixture", reason="explicit retrospective tracking") == [
        row["pick_id"]
    ]
    assert ledger.rows()[0]["record_source"] == "retrospective_operator_requested"
    assert ledger.rows()[0]["units"] == "0"
    assert ledger.import_rows([row], source="fixture", reason="explicit retrospective tracking") == []
    assert len(store.records(tier="main", sport="nba")) == 1
