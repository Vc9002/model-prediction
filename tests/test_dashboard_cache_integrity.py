from __future__ import annotations

from model_prediction.dashboard_cache import DashboardCache
from model_prediction.runtime_ledger_store import LedgerMutation, RuntimeLedgerStore
from model_prediction.runtime_paths import RuntimePaths
from model_prediction.xlsx_ledger import write_xlsx_rows_atomic


def test_sqlite_cache_uses_canonical_rows_and_preserves_clv_odds(tmp_path) -> None:
    paths = RuntimePaths.for_test(tmp_path)
    store = RuntimeLedgerStore(paths)
    store.apply(
        LedgerMutation(
            pick_id="sqlite-only",
            operation_id="op-sqlite-only",
            ledger_tier="main",
            sport="tennis",
            event_type="settle",
            created_at_utc="2026-08-23T20:00:00Z",
            event_id="event-1",
            event_start_utc="2026-08-23T23:00:00Z",
            market_type="moneyline",
            selection="home",
            model_id="tennis-surface-elo-v1",
            model_probability=0.61,
            market_probability=0.52,
            edge=0.09,
            confidence=70,
            units=1.0,
            decision="CALL",
            reason_code="QUALIFIED",
            status="settled",
            result="win",
            pnl_units=0.91,
            settled_at_utc="2026-08-24T01:00:00Z",
            decision_payload={
                "pick_id": "sqlite-only",
                "league": "tennis",
                "away_team": "Away",
                "home_team": "Home",
                "american_odds": "-110",
                "decision_american_odds": "-110",
                "decision_decimal_odds": "1.909091",
                "closing_american_odds": "-120",
                "closing_decimal_odds": "1.833333",
                "probability_clv": "0.021645",
                "record_type": "QUALIFIED_SHADOW_CALL",
            },
        )
    )
    store.apply(
        LedgerMutation(
            pick_id="removed-row",
            operation_id="op-removed-row",
            ledger_tier="main",
            sport="tennis",
            event_type="remove",
            created_at_utc="2026-08-23T20:00:00Z",
            status="removed",
        )
    )

    cache = DashboardCache(
        tmp_path / "repo-data",
        db_path=tmp_path / "dashboard-cache.db",
        authority="sqlite",
        ledger_store=store,
    )
    assert cache.refresh(force=True) == {
        "main": 1,
        "flat": 0,
        "research": 0,
        "gated_research": 0,
    }

    assert cache.read_picks("flat") == []
    [row] = cache.read_picks("main")
    assert row["pick_id"] == "sqlite-only"
    assert row["sport"] == "tennis"
    assert row["decision_american_odds"] == "-110"
    assert row["closing_american_odds"] == "-120"
    assert row["decision_decimal_odds"] == "1.909091"
    assert row["closing_decimal_odds"] == "1.833333"
    assert row["probability_clv"] == "0.021645"
    store.close()


def test_empty_sqlite_cache_tier_does_not_fall_back_to_stale_xlsx(tmp_path, monkeypatch) -> None:
    from model_prediction.dashboard import picks
    from model_prediction.ledger import FIELDNAMES

    stale_path = tmp_path / "data" / "main" / "mlb.xlsx"
    stale = {field: "" for field in FIELDNAMES}
    stale.update({"pick_id": "stale-xlsx", "status": "open", "league": "MLB"})
    write_xlsx_rows_atomic(stale_path, FIELDNAMES, [stale])

    class _EmptyCanonicalCache:
        authority = "sqlite"

        def refresh(self) -> dict[str, int]:
            return {"main": 0}

        def read_picks(self, tier: str) -> list[dict]:
            assert tier == "main"
            return []

    monkeypatch.setattr(picks, "ROOT", tmp_path)
    monkeypatch.setattr(picks, "_get_dashboard_cache", lambda: _EmptyCanonicalCache())

    rows = picks._read_split_picks([stale_path], {"mtime": None, "rows": []})
    assert rows == []


def test_special_xlsx_ledgers_are_not_misrouted_to_main_cache(tmp_path, monkeypatch) -> None:
    from model_prediction.dashboard import picks
    from model_prediction.ledger import FIELDNAMES

    special_path = tmp_path / "data" / "flat_v9" / "mlb.xlsx"
    row = {field: "" for field in FIELDNAMES}
    row.update({"pick_id": "v9-only", "status": "open", "league": "MLB"})
    write_xlsx_rows_atomic(special_path, FIELDNAMES, [row])

    class _CanonicalCache:
        authority = "sqlite"

        def refresh(self) -> dict[str, int]:
            raise AssertionError("special XLSX ledger must not use the canonical cache")

    monkeypatch.setattr(picks, "ROOT", tmp_path)
    monkeypatch.setattr(picks, "_get_dashboard_cache", lambda: _CanonicalCache())

    rows = picks._read_split_picks([special_path], {"mtime": None, "rows": []})
    assert [item["pick_id"] for item in rows] == ["v9-only"]
