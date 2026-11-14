"""Polymarket NFL week-ahead capture: one call per upcoming date, filed by game date, fail-soft."""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from model_prediction.cli.commands import capture_nfl_polymarket_week_ahead


class _FakePoly:
    def __init__(self, slates: dict[str, list[dict]], fail_dates: set[str] = frozenset()):
        self.slates = slates
        self.fail_dates = fail_dates
        self.calls: list[tuple[str, date, str]] = []

    def slate(self, league: str, game_date: date, timezone_name: str) -> list[dict]:
        self.calls.append((league, game_date, timezone_name))
        if game_date.isoformat() in self.fail_dates:
            raise httpx.ConnectError("boom")
        return self.slates.get(game_date.isoformat(), [])


def test_week_ahead_captures_each_upcoming_date_under_its_own_dir(tmp_path):
    poly = _FakePoly({"2026-11-12": [{"id": "a"}], "2026-11-15": [{"id": "b"}, {"id": "c"}]})
    seen: list[dict] = []

    def fake_capture(client, events_by_league, root, game_date, **kwargs):
        seen.append({"events": events_by_league, "root": root, "game_date": game_date, **kwargs})
        return {"captured": sum(len(v) for v in events_by_league.values())}

    out = capture_nfl_polymarket_week_ahead(
        {}, "2026-11-11", days=7, client=poly, capture_fn=fake_capture, snapshot_root=tmp_path
    )
    # Today (offset 0) belongs to the main slate capture; the week-ahead starts tomorrow.
    assert [c[1].isoformat() for c in poly.calls] == [f"2026-11-{d}" for d in range(12, 19)]
    assert all(c[0] == "NFL" and c[2] == "America/New_York" for c in poly.calls)
    assert [s["game_date"] for s in seen] == ["2026-11-12", "2026-11-15"]
    assert all(s["root"] == tmp_path and s["horizon_hours"] is None for s in seen)
    assert all(s["ladder_leagues"] == frozenset({"NFL"}) and s["ladder_keep"] == 2 for s in seen)
    assert out["status"] == "ok" and out["dates"]["2026-11-15"] == {"events": 2, "captured": 2}


def test_week_ahead_isolates_a_failing_date(tmp_path):
    poly = _FakePoly({"2026-11-12": [{"id": "a"}], "2026-11-15": [{"id": "b"}]}, fail_dates={"2026-11-12"})
    out = capture_nfl_polymarket_week_ahead(
        {},
        "2026-11-11",
        days=7,
        client=poly,
        capture_fn=lambda *a, **k: {"captured": 1},
        snapshot_root=tmp_path,
    )
    assert out["status"] == "partial"
    assert "2026-11-12" in out["errors"] and list(out["dates"]) == ["2026-11-15"]


def test_daily_runs_week_ahead_books_only_after_the_slate_capture():
    """Both captures share one IP-wide gateway limit; run side by side (2026-11-14) each
    one's 429s ended the other's queue. The week-ahead must also run when the slate
    capture raises, and the slate's outcome must still reach the caller."""
    from model_prediction.cli.daily import _serialized_polymarket_captures

    order: list[str] = []

    def slate_capture():
        order.append("slate")
        return {"captured": 7}

    def week_ahead_capture():
        order.append("week_ahead")

    assert _serialized_polymarket_captures(slate_capture, week_ahead_capture) == {"captured": 7}
    assert order == ["slate", "week_ahead"]

    def failing_slate_capture():
        order.append("slate_failed")
        raise httpx.ConnectError("gateway down")

    with pytest.raises(httpx.ConnectError):
        _serialized_polymarket_captures(failing_slate_capture, week_ahead_capture)
    assert order[-2:] == ["slate_failed", "week_ahead"]
