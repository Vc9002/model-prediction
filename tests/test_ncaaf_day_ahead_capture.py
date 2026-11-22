"""NCAAF day-ahead Polymarket capture (quick 261121-dac): wiring, fail-soft, budget, filing."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from model_prediction.cli import commands, daily
from model_prediction.cli.commands import capture_ncaaf_polymarket_day_ahead


# --------------------------------------------------------------------------- daily wiring
def test_disabled_ncaaf_skips_without_calling_capture(monkeypatch):
    monkeypatch.setattr(daily, "is_sport_daily_enabled", lambda config, sport: False)

    def must_not_run(*args, **kwargs):
        raise AssertionError("day-ahead capture ran for a disabled sport")

    monkeypatch.setattr(commands, "capture_ncaaf_polymarket_day_ahead", must_not_run)
    result = daily._run_ncaaf_day_ahead_capture({}, "2030-09-05")
    assert result == {"status": "skipped", "reason": "ncaaf_daily_forecast_disabled"}


def test_capture_failure_is_returned_not_raised_and_is_not_material(monkeypatch):
    monkeypatch.setattr(daily, "is_sport_daily_enabled", lambda config, sport: True)

    def boom(*args, **kwargs):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(commands, "capture_ncaaf_polymarket_day_ahead", boom)
    result = daily._run_ncaaf_day_ahead_capture({}, "2030-09-05")
    assert result == {"status": "error"}
    assert daily._material_daily_errors({"step1n_ncaaf_day_ahead_quotes": result}) == []


def test_serializer_runs_later_captures_after_the_slate_and_isolates_each():
    order: list[str] = []

    def slate():
        order.append("slate")
        return {"captured": 3}

    def nfl():
        order.append("nfl")
        raise RuntimeError("nfl capture bug")

    def ncaaf():
        order.append("ncaaf")

    assert daily._serialized_polymarket_captures(slate, nfl, ncaaf) == {"captured": 3}
    assert order == ["slate", "nfl", "ncaaf"]


# --------------------------------------------------------------------------- capture
class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _FakePoly:
    """Gateway stand-in: per-date NCAAF slates, and slate-complete snapshots."""

    def __init__(self, slates: dict[str, list[dict]], clock: _Clock | None = None, slate_cost: float = 0.0):
        self.slates = slates
        self.clock = clock
        self.slate_cost = slate_cost
        self.slate_calls: list[tuple[str, date]] = []
        self.book_calls: list[str] = []

    def slate(self, league: str, game_date: date, timezone_name: str) -> list[dict]:
        self.slate_calls.append((league, game_date))
        if self.clock is not None:
            self.clock.now += self.slate_cost
        return self.slates.get(game_date.isoformat(), [])

    def snapshot_from_slate(self, event: dict, market: dict) -> dict:
        self.book_calls.append(market["market_slug"])
        return {
            "provider": "polymarket_us",
            "market_slug": market["market_slug"],
            "market_type": market["market_type"],
            "event_start_utc": event["event_start_utc"],
            "observed_at_utc": "2030-09-05T15:00:00Z",
            "market_state": "MARKET_STATE_OPEN",
            "reconstructed": False,
            "long": {"ask": 0.52, "bid": 0.48},
            "short": {"ask": 0.52, "bid": 0.48},
        }

    def snapshot(self, slug: str) -> dict:
        raise AssertionError("snapshot() must not be used when the slate market is complete")


def _market(slug: str, market_type: str, price: float | None = None, line: float | None = None) -> dict:
    return {
        "market_id": slug,
        "market_slug": slug,
        "market_type": market_type,
        "line": line,
        "sides": [
            {"description": "A", "team": "A", "is_long": True, "line": line, "price_probability": price},
            {"description": "B", "team": "B", "is_long": False, "line": line},
        ],
    }


def _ncaaf_event(event_id: str, kickoff: str) -> dict:
    return {
        "event_id": event_id,
        "event_start_utc": kickoff,
        "markets": [
            _market(f"{event_id}-ml", "moneyline", 0.6),
            _market(f"{event_id}-sp-main", "spread", 0.51, -3.5),
            _market(f"{event_id}-sp-alt", "spread", 0.80, -10.5),
            _market(f"{event_id}-tot-main", "total", 0.49, 52.5),
            _market(f"{event_id}-tot-alt", "total", 0.25, 45.5),
        ],
    }


def test_day_ahead_files_each_game_under_its_own_date_with_main_rungs_only(tmp_path):
    poly = _FakePoly(
        {
            "2030-09-06": [_ncaaf_event("fri", "2030-09-07T00:00:00Z")],
            "2030-09-07": [_ncaaf_event("sat", "2030-09-07T19:30:00Z")],
        }
    )
    out = capture_ncaaf_polymarket_day_ahead({}, "2030-09-05", client=poly, snapshot_root=tmp_path)

    assert [call[1].isoformat() for call in poly.slate_calls] == ["2030-09-06", "2030-09-07"]
    assert {call[0] for call in poly.slate_calls} == {"NCAAF"}
    assert out["status"] == "ok" and list(out["dates"]) == ["2030-09-06", "2030-09-07"]
    # Moneyline plus the most balanced spread and total; alternates are not booked.
    assert sorted(poly.book_calls) == sorted(
        f"{event}-{kind}" for event in ("fri", "sat") for kind in ("ml", "sp-main", "tot-main")
    )
    # Nothing is filed under the capture date: today's forecast never sees these rows.
    assert not (tmp_path / "odds" / "ncaaf" / "2030-09-05").exists()
    for game_date, event_id in (("2030-09-06", "fri"), ("2030-09-07", "sat")):
        path = tmp_path / "odds" / "ncaaf" / game_date / "polymarket_snapshots.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        assert {row["event_id"] for row in rows} == {event_id}
        for row in rows:
            # Same writer, same schema as the game-day capture.
            assert row["observed_at_utc"] == "2030-09-05T15:00:00Z"
            assert row["timestamp_valid"] is True
            assert row["usage"] == "prospective_executable_bbo"
            assert row["league"] == "NCAAF"


def test_one_deadline_covers_every_date_and_reaches_the_capture():
    clock = _Clock()
    poly = _FakePoly(
        {"2030-09-06": [{"event_id": "fri"}], "2030-09-07": [{"event_id": "sat"}]},
        clock=clock,
        slate_cost=5.0,
    )
    seen: list[tuple[str, dict]] = []

    def fake_capture(client, events_by_league, root, game_date, **kwargs):
        seen.append((game_date, kwargs))
        clock.now += kwargs["max_capture_seconds"]  # spends everything it was given
        return {"captured": 3}

    out = capture_ncaaf_polymarket_day_ahead(
        {},
        "2030-09-05",
        max_seconds=100.0,
        client=poly,
        capture_fn=fake_capture,
        snapshot_root=Path("unused"),
        clock=clock,
    )
    assert [game_date for game_date, _ in seen] == ["2030-09-06"]
    kwargs = seen[0][1]
    # The slate fetch counts against the budget: 100 s minus its 5 s.
    assert kwargs["max_capture_seconds"] == pytest.approx(95.0)
    assert kwargs["horizon_hours"] is None
    assert kwargs["ladder_keep"] == {"NCAAF": 1}
    assert kwargs["priority_leagues"] == frozenset({"NCAAF"})
    # D+2 is neither fetched nor captured once the budget is spent.
    assert [call[1].isoformat() for call in poly.slate_calls] == ["2030-09-06"]
    assert out["skipped_after_budget"] == ["2030-09-07"]
    assert out["status"] == "ok"


def test_a_failed_date_does_not_stop_the_next():
    class _Failing(_FakePoly):
        def slate(self, league, game_date, timezone_name):
            if game_date.isoformat() == "2030-09-06":
                raise httpx.ConnectError("boom")
            return super().slate(league, game_date, timezone_name)

    poly = _Failing({"2030-09-07": [{"event_id": "sat"}]})
    out = capture_ncaaf_polymarket_day_ahead(
        {},
        "2030-09-05",
        client=poly,
        capture_fn=lambda *a, **k: {"captured": 1},
        snapshot_root=Path("unused"),
    )
    assert out["status"] == "partial"
    assert list(out["errors"]) == ["2030-09-06"] and list(out["dates"]) == ["2030-09-07"]
