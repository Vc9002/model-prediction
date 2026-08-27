"""Regression tests for the settlement no-ESPN-path guard.

2026-07-27 audit: a ledger row whose league has no entry in
``_LEDGER_LEAGUE_TO_ESPN`` (e.g. a retired WORLD_CUP row) would settle
through the ESPN branch with an empty league tuple -- which can never
match a result -- and sit ``open`` forever with no error anywhere.
The guard (settle.py) must turn that into a visible failure.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from model_prediction.cli import settle as settle_module


class _FakeLedger:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def rows(self) -> list[dict]:
        return list(self._rows)


class _FakeESPNClient:
    """Never called for an unmapped league -- instantiation is enough."""


class _FakeSnapshotStore:
    def __init__(self, path) -> None:
        self.path = path


def _open_row(league: str, event_start_utc: str) -> dict:
    return {
        "pick_id": f"p-{league}",
        "league": league,
        "status": "open",
        "event_start_utc": event_start_utc,
        "event_id": "ev1",
        "away_team": "Away",
        "original_away_team": "Away",
        "home_team": "Home",
        "original_home_team": "Home",
    }


def _run_settle(monkeypatch, tmp_path, rows: list[dict]) -> dict:
    monkeypatch.setattr(settle_module, "ESPNClient", _FakeESPNClient)
    monkeypatch.setattr(settle_module, "MarketOddsSnapshotStore", _FakeSnapshotStore)
    config = {
        "project": {
            "ledger_path": str(tmp_path / "ledgers.db"),
            "market_odds_snapshots": str(tmp_path / "market_odds"),
        }
    }
    args = argparse.Namespace(all_unsettled=True, void_postponed=True)
    return settle_module._settle_all_unsettled(args, config, _FakeLedger(rows))


def test_unmapped_league_fails_loudly_instead_of_pending_forever(monkeypatch, tmp_path) -> None:
    past = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    result = _run_settle(monkeypatch, tmp_path, [_open_row("WORLD_CUP", past)])

    assert result["still_open"] == []
    assert len(result["failures"]) == 1
    assert "no ESPN result path for league WORLD_CUP" in result["failures"][0]["reason"]


def test_mapped_league_does_not_trigger_the_guard(monkeypatch, tmp_path) -> None:
    past = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    result = _run_settle(monkeypatch, tmp_path, [_open_row("MLB", past)])

    # MLB is mapped: the guard must not fire; the row either settles or
    # stays pending on the (fake) ESPN lookup, but never reports the
    # no-path failure.
    assert all("no ESPN result path" not in f.get("reason", "") for f in result["failures"])
