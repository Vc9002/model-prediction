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


class _CountingESPNClient:
    def __init__(self, scoreboard: dict) -> None:
        self.payload = scoreboard
        self.calls: list[tuple[str, str]] = []

    def scoreboard(self, league: str, game_day: str) -> dict:
        self.calls.append((league, game_day))
        return self.payload


class _FailingESPNClient(_CountingESPNClient):
    def scoreboard(self, league: str, game_day: str) -> dict:
        self.calls.append((league, game_day))
        raise RuntimeError("provider rejected date")


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


def test_missing_edge_ledger_gate_fails_closed(monkeypatch, tmp_path) -> None:
    result = _run_settle(monkeypatch, tmp_path, [])
    assert result["polymarket_edge_settlement"] == {
        "status": "disabled",
        "reason": "operator_disabled",
    }


def test_scoreboards_are_cached_across_rows_and_ledgers() -> None:
    from model_prediction.cli.settle import _find_espn_result

    scoreboard = {
        "events": [
            {
                "id": "ev1",
                "competitions": [
                    {
                        "status": {"type": {"name": "STATUS_FINAL", "completed": True}},
                        "competitors": [
                            {"homeAway": "away", "score": "2", "team": {"displayName": "Away"}},
                            {"homeAway": "home", "score": "3", "team": {"displayName": "Home"}},
                        ],
                    }
                ],
            }
        ]
    }
    espn = _CountingESPNClient(scoreboard)
    cache: dict[tuple[str, str], dict | Exception] = {}
    row = _open_row("MLB", "2026-08-31T23:00:00+00:00")

    assert _find_espn_result(espn, ("MLB",), "2026-08-31", row, scoreboard_cache=cache)
    assert _find_espn_result(espn, ("MLB",), "2026-08-31", row, scoreboard_cache=cache)
    assert espn.calls == [("MLB", "2026-08-31")]


def test_cached_scoreboard_failure_fetches_and_logs_once(caplog) -> None:
    from model_prediction.cli.settle import _find_espn_result

    espn = _FailingESPNClient({})
    cache: dict[tuple[str, str], dict | Exception] = {}
    row = _open_row("MLB", "2026-08-31T23:00:00+00:00")

    assert _find_espn_result(espn, ("MLB",), "2026-08-31", row, scoreboard_cache=cache) is None
    assert _find_espn_result(espn, ("MLB",), "2026-08-31", row, scoreboard_cache=cache) is None
    assert espn.calls == [("MLB", "2026-08-31")]
    assert caplog.text.count("ESPN scoreboard fetch failed for MLB on 2026-08-31") == 1


class _FakeSoccerESPN:
    """Minimal stand-in for ESPNClient.summary over the soccer/all path."""

    def __init__(self, payload: dict | Exception) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str]] = []

    def summary(self, league: str, event_id: str) -> dict:
        self.calls.append((league, event_id))
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _soccer_summary(status: str, home: str | int, away: str | int) -> dict:
    return {
        "header": {
            "competitions": [
                {
                    "status": {"type": {"name": status}},
                    "competitors": [
                        {"homeAway": "home", "score": home},
                        {"homeAway": "away", "score": away},
                    ],
                }
            ]
        }
    }


def test_a_finished_soccer_match_resolves_by_event_id_without_any_credential() -> None:
    """Soccer results went 7 days stale to 2026-08-24 because the provider path
    returns no_api_key whenever API_FOOTBALL_KEY is unset -- every daily run
    skipped it and still exited 0. Soccer rows carry ESPN event ids, so the
    result is resolvable by identity with no credential at all. ESPN spells soccer's
    terminal state STATUS_FULL_TIME, not STATUS_FINAL; the rest of the settle path
    only knows the latter, so this must normalise rather than report 'not completed'.
    """
    from model_prediction.cli.settle import _find_espn_soccer_result_by_event_id

    espn = _FakeSoccerESPN(_soccer_summary("STATUS_FULL_TIME", 1, 2))

    match = _find_espn_soccer_result_by_event_id(espn, {"event_id": "401905968"})

    assert match == {
        "status_name": "STATUS_FINAL",
        "completed": True,
        "home_score": 1,
        "away_score": 2,
    }
    # Resolved by identity on the cross-league path -- no league guessing.
    assert espn.calls == [("SOCCER_ALL", "401905968")]


def test_an_unfinished_or_unresolvable_soccer_match_never_invents_a_result() -> None:
    """Three ways this must decline instead of guessing: the match is still in
    progress, the payload has no usable score, or ESPN itself fails. A settlement
    invented from a missing score is unrecoverable evidence damage, where a pending
    row is merely unfinished work."""
    from model_prediction.cli.settle import _find_espn_soccer_result_by_event_id

    in_progress = _find_espn_soccer_result_by_event_id(
        _FakeSoccerESPN(_soccer_summary("STATUS_FIRST_HALF", 0, 0)), {"event_id": "1"}
    )
    assert in_progress is not None and in_progress["completed"] is False

    no_score = _find_espn_soccer_result_by_event_id(
        _FakeSoccerESPN(_soccer_summary("STATUS_FULL_TIME", None, None)), {"event_id": "1"}
    )
    assert no_score is None

    unreachable = _find_espn_soccer_result_by_event_id(
        _FakeSoccerESPN(RuntimeError("espn down")), {"event_id": "1"}
    )
    assert unreachable is None

    # A row with no event id must not reach the network at all.
    espn = _FakeSoccerESPN(_soccer_summary("STATUS_FULL_TIME", 1, 0))
    assert _find_espn_soccer_result_by_event_id(espn, {"event_id": ""}) is None
    assert espn.calls == []
