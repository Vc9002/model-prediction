"""The daily forecast skips a sport's tasks only when its scoreboard proves no game is left (quick 261122-spd).

Every case where the answer is not provable must mean "run the task": a failed fetch, a malformed payload,
a missing or timezone-less start time. The rule must also never skip a game that has not started.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest

from model_prediction.cli import daily
from model_prediction.cli.commands import _evaluated_slots
from model_prediction.cli.event_horizon import (
    EventHorizon,
    horizon_skip_result,
    probe_event_horizon,
    scoreboard_horizon,
)

NOW = datetime(2026, 11, 22, 18, 0, tzinfo=UTC)


def _event(event_id: str, start: datetime | str | None) -> dict:
    event: dict = {"id": event_id}
    if start is not None:
        event["date"] = start.strftime("%Y-%m-%dT%H:%MZ") if isinstance(start, datetime) else start
    return event


def _config(**models: dict) -> dict:
    return {"models": {name: dict(cfg) for name, cfg in models.items()}}


def test_empty_scoreboard_has_nothing_to_forecast() -> None:
    horizon = scoreboard_horizon({"events": []}, "NFL", NOW)
    assert horizon == EventHorizon("NFL", 0, 0, ())
    assert horizon.nothing_to_forecast


def test_all_started_events_are_listed_and_nothing_remains() -> None:
    payload = {"events": [_event("a", NOW - timedelta(hours=2)), _event("b", NOW)]}
    horizon = scoreboard_horizon(payload, "NBA", NOW)
    assert horizon is not None and horizon.nothing_to_forecast
    assert horizon.started_event_ids == ("a", "b")  # start == now is started, as the tasks treat it


def test_a_game_about_to_start_is_never_skipped() -> None:
    payload = {"events": [_event("a", NOW - timedelta(hours=2)), _event("b", NOW + timedelta(minutes=1))]}
    horizon = scoreboard_horizon(payload, "NBA", NOW)
    assert horizon is not None and horizon.upcoming == 1 and not horizon.nothing_to_forecast
    assert horizon_skip_result({"NBA": horizon}, "NBA", "nba_spread") is None


@pytest.mark.parametrize(
    "event",
    [
        {"id": "no-date"},
        {"id": "bad", "date": "not a time"},
        {"id": "naive", "date": "2026-11-22T10:00:00"},  # parse_utc rejects a timezone-less stamp
        "not-a-mapping",
    ],
)
def test_an_event_without_a_usable_start_counts_as_upcoming(event) -> None:
    horizon = scoreboard_horizon({"events": [event]}, "NBA", NOW)
    assert horizon is not None and horizon.upcoming == 1
    assert horizon_skip_result({"NBA": horizon}, "NBA", "nba_total") is None


@pytest.mark.parametrize("payload", [None, [], "x", {}, {"events": None}, {"events": {"a": 1}}])
def test_a_malformed_scoreboard_is_unknown_not_empty(payload) -> None:
    assert scoreboard_horizon(payload, "NFL", NOW) is None


def test_a_failed_fetch_never_skips() -> None:
    def fetch(league: str, game_date: str):
        raise TimeoutError("espn down")

    horizons = probe_event_horizon(_config(NFL={}), "2026-11-22", NOW, fetch, leagues=("NFL",))
    assert horizons == {"NFL": None}
    assert horizon_skip_result(horizons, "NFL", "nfl_spread") is None


def test_the_probe_only_asks_for_enabled_single_scoreboard_leagues() -> None:
    asked: list[tuple[str, str]] = []

    def fetch(league: str, game_date: str) -> dict:
        asked.append((league, game_date))
        return {"events": []}

    config = _config(
        NFL={},
        NBA={"daily_forecast_enabled": False},
        WNBA={"season_active": False},
        MLB={"leagues": ["MLB", "OTHER"]},
    )
    horizons = probe_event_horizon(
        config, "2026-11-22", NOW, fetch, leagues=("NFL", "NBA", "WNBA", "MLB", "NCAAF")
    )
    assert sorted(asked) == [("NCAAF", "2026-11-22"), ("NFL", "2026-11-22")]
    assert set(horizons) == {"NFL", "NCAAF"}


def test_skip_result_reads_as_an_empty_forecast_and_not_as_an_error() -> None:
    horizon = scoreboard_horizon({"events": [_event("a", NOW - timedelta(hours=1))]}, "NBA", NOW)
    result = horizon_skip_result({"NBA": horizon}, "NBA", "nba_spread")
    assert result is not None
    assert result["status"] == "no_scheduled_events" and result["logged"] == 0
    assert result["skipped"] == [{"event_id": "a", "reason": "event_started"}]
    assert not daily._contains_error_status(result)
    # Nothing was evaluated, so the pre-forecast clear's retired rows are reopened by _restore_unreplaced.
    prior = {"main": [{"league": "NBA", "event_id": "g1", "market_type": "spread"}]}
    assert _evaluated_slots({"nba_spread": result}, prior) == []


def test_horizon_without_a_scoreboard_for_the_league_runs_the_task() -> None:
    assert horizon_skip_result({}, "NBA", "nba_spread") is None


def test_run_daily_probes_before_the_pool_and_gates_exactly_the_scoreboard_tasks() -> None:
    """run_daily is too heavy to execute here; pin the wiring."""
    source = inspect.getsource(daily.run_daily)
    clear = source.index("_clear_today_open(ledger, args.date")
    probe = source.index("probe_event_horizon(")
    pool = source.index("with ThreadPoolExecutor(max_workers=workers) as pool:")
    restore = source.index("unreplaced_restored = _restore_unreplaced(")
    assert clear < probe < pool < restore
    assert (
        "forecast_observed_at, _HorizonESPNClient().scoreboard" in source
    )  # decision time, not a later clock
    assert "horizon_skip_result(event_horizons, sport.upper(), sport)" in source

    for task, key, league in (
        ("_nba_spread_task", "nba_spread", "NBA"),
        ("_nba_total_task", "nba_total", "NBA"),
        ("_nfl_spread_task", "nfl_spread", "NFL"),
        ("_nfl_total_task", "nfl_total", "NFL"),
        ("_cfb_task", "ncaaf", "NCAAF"),
        ("_wnba_spread_task", "wnba_spread", "WNBA"),
        ("_wnba_total_task", "wnba_total", "WNBA"),
    ):
        body = source.split(f"def {task}() -> None:", 1)[1].split("\n    def ", 1)[0]
        assert f'_skip_for_horizon("{key}", "{league}")' in body, task
    # Not scoreboard-driven: week-ahead lines, other discovery paths, and settlement never consult the probe.
    for task in ("_nfl_early_task", "_tennis_task", "_mlb_totals_task", "_mlb_nrfi_task"):
        body = source.split(f"def {task}() -> None:", 1)[1].split("\n    def ", 1)[0]
        assert "_skip_for_horizon" not in body, task
    settle = source[source.index("if args.skip_settlement:") :]
    assert "horizon_skip_result" not in settle and "event_horizons" not in settle
