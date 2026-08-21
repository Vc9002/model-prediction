from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from model_prediction.data_sources import mlb_injuries
from model_prediction.features import mlb_player_availability


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(
        self, roster_payload: dict[str, Any] | None = None, transactions_payload: dict[str, Any] | None = None
    ) -> None:
        self._roster_payload = roster_payload or {"roster": []}
        self._transactions_payload = transactions_payload or {"transactions": []}
        self.requested: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, params: dict[str, Any] | None = None) -> _Response:
        self.requested.append((url, params or {}))
        if "roster" in url:
            return _Response(self._roster_payload)
        return _Response(self._transactions_payload)


def _write_txn_snapshot(
    tmp_path,
    *,
    team_ids: list[int],
    entries: list[dict[str, Any]],
    observed_at_utc: str,
    start_date: str = "2026-06-01",
    end_date: str = "2026-08-01",
    day: str = "2026-08-01",
) -> None:
    snapshot_dir = tmp_path / "availability" / "mlb" / "snapshots" / day
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1",
        "sport": "mlb",
        "source": "mlb_statsapi_transactions",
        "observed_at_utc": observed_at_utc,
        "start_date": start_date,
        "end_date": end_date,
        "team_ids": team_ids,
        "entries": entries,
    }
    (snapshot_dir / "txn-fixture.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_roster_snapshot(
    tmp_path,
    *,
    team_ids: list[int],
    entries: list[dict[str, Any]],
    observed_at_utc: str,
    day: str = "2026-08-01",
) -> None:
    snapshot_dir = tmp_path / "availability" / "mlb" / "snapshots" / day
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1",
        "sport": "mlb",
        "source": "mlb_statsapi_roster_40man",
        "observed_at_utc": observed_at_utc,
        "report_at_utc": observed_at_utc,
        "team_ids": team_ids,
        "entries": entries,
    }
    (snapshot_dir / "roster-fixture.json").write_text(json.dumps(payload), encoding="utf-8")


def _roster_entry(
    team_id: int, player_name: str, current_status: str, position_type: str = "Outfielder"
) -> dict[str, Any]:
    return {
        "team_id": team_id,
        "player_id": 1,
        "player_name": player_name,
        "current_status": current_status,
        "position_type": position_type,
    }


def _entry(
    team_id: int, player_name: str, reported_date: str, type_desc: str, effective_date: str | None = None
) -> dict[str, Any]:
    return {
        "team_id": team_id,
        "player_id": 1,
        "player_name": player_name,
        "reported_date": reported_date,
        "effective_date": effective_date or reported_date,
        "type_desc": type_desc,
        "description": type_desc,
    }


def _stub_starters(monkeypatch, *, home: str = "Gerrit Cole", away: str = "Zack Wheeler") -> None:
    monkeypatch.setattr(
        mlb_player_availability,
        "point_in_time_probable_starters",
        lambda event_id, decision_at: {"home_starter": home, "away_starter": away},
    )


# ── data_sources/mlb_injuries.py ────────────────────────────────────────


def test_team_id_for_name_resolves_known_teams() -> None:
    assert mlb_injuries.team_id_for_name("New York Yankees") == 147
    assert mlb_injuries.team_id_for_name("Los Angeles Dodgers") == 119


def test_team_id_for_name_fails_closed_for_unknown_team() -> None:
    with pytest.raises(ValueError, match="NO_CALL_MLB_AVAILABILITY_UNKNOWN_TEAM"):
        mlb_injuries.team_id_for_name("Springfield Isotopes")


def test_capture_roster_snapshot_writes_raw_and_normalized_files(tmp_path) -> None:
    client = _FakeClient(
        roster_payload={
            "roster": [
                {
                    "person": {"id": 1, "fullName": "Gerrit Cole"},
                    "status": {"description": "Active"},
                    "position": {"type": "Pitcher"},
                },
                {
                    "person": {"id": 2, "fullName": "Some Reliever"},
                    "status": {"description": "Injured 15-Day"},
                    "position": {"type": "Pitcher"},
                },
            ]
        }
    )
    observed = datetime(2026, 8, 1, 12, tzinfo=UTC)

    payload = mlb_injuries.capture_roster_snapshot(tmp_path, [147], observed_at=observed, client=client)

    assert payload["entries"] == [
        {
            "team_id": 147,
            "player_id": 1,
            "player_name": "Gerrit Cole",
            "current_status": "Active",
            "position_type": "Pitcher",
        },
        {
            "team_id": 147,
            "player_id": 2,
            "player_name": "Some Reliever",
            "current_status": "Injured 15-Day",
            "position_type": "Pitcher",
        },
    ]
    written = list((tmp_path / "availability" / "mlb" / "snapshots" / "2026-08-01").glob("roster-*.json"))
    assert len(written) == 1
    assert json.loads(written[0].read_text())["entries"] == payload["entries"]
    raw = list((tmp_path / "availability" / "mlb" / "raw" / "2026-08-01").glob("roster-*.json"))
    assert len(raw) == 1


def test_capture_transactions_snapshot_stores_reported_and_effective_date_separately(tmp_path) -> None:
    client = _FakeClient(
        transactions_payload={
            "transactions": [
                {
                    "person": {"id": 9, "fullName": "Some Pitcher"},
                    "date": "2026-07-30",
                    "effectiveDate": "2026-07-25",
                    "typeDesc": "Status Change - Injured List (15-Day)",
                    "description": "placed on IL retroactive to 2026-07-25",
                }
            ]
        }
    )
    observed = datetime(2026, 8, 1, 12, tzinfo=UTC)

    payload = mlb_injuries.capture_transactions_snapshot(
        tmp_path, [147], "2026-07-01", "2026-08-01", observed_at=observed, client=client
    )

    entry = payload["entries"][0]
    assert entry["reported_date"] == "2026-07-30"
    assert entry["effective_date"] == "2026-07-25"
    written = list((tmp_path / "availability" / "mlb" / "snapshots" / "2026-08-01").glob("txn-*.json"))
    assert len(written) == 1


# ── features/mlb_player_availability.py ─────────────────────────────────


def test_no_il_history_defaults_to_active(tmp_path, monkeypatch) -> None:
    _stub_starters(monkeypatch)
    _write_txn_snapshot(
        tmp_path,
        team_ids=[147, 119],
        entries=[],
        observed_at_utc="2026-08-01T12:00:00Z",
    )

    result = mlb_player_availability.matchup_player_availability(
        data_root=tmp_path,
        home_team="New York Yankees",
        away_team="Los Angeles Dodgers",
        game_date="2026-08-01",
        observed_at=datetime(2026, 8, 1, 18, tzinfo=UTC),
        event_start=datetime(2026, 8, 1, 23, tzinfo=UTC),
        event_id="game-1",
    )

    assert result["probable_starter_unavailable_home"] == 0.0
    assert result["probable_starter_unavailable_away"] == 0.0


def test_fresh_roster_snapshot_is_preferred_over_transactions(tmp_path, monkeypatch) -> None:
    """A fresh live roster read is direct current status -- it should win
    over the transactions-based reconstruction even when the transactions
    snapshot would say something different, since it's the more authoritative
    signal for a near-live decision."""
    _stub_starters(monkeypatch)
    _write_txn_snapshot(
        tmp_path,
        team_ids=[147, 119],
        entries=[],  # transactions alone would say Active
        observed_at_utc="2026-08-01T12:00:00Z",
    )
    _write_roster_snapshot(
        tmp_path,
        team_ids=[147, 119],
        entries=[_roster_entry(147, "Gerrit Cole", "Injured 15-Day")],
        observed_at_utc="2026-08-01T17:00:00Z",
    )

    result = mlb_player_availability.matchup_player_availability(
        data_root=tmp_path,
        home_team="New York Yankees",
        away_team="Los Angeles Dodgers",
        game_date="2026-08-01",
        observed_at=datetime(2026, 8, 1, 18, tzinfo=UTC),
        event_start=datetime(2026, 8, 1, 23, tzinfo=UTC),
        event_id="game-1",
    )

    assert result["probable_starter_unavailable_home"] == 1.0
    assert result["home_probable_starter_source"] == "roster"
    assert result["away_probable_starter_source"] == "transactions"


def test_roster_beyond_an_old_il_placement_still_catches_unavailability(tmp_path, monkeypatch) -> None:
    """The scenario the roster fallback specifically exists for: an IL
    placement older than the transactions capture's own lookback window
    (so transactions alone would default to Active), caught anyway because
    a fresh live roster snapshot directly reports the current status."""
    _stub_starters(monkeypatch)
    _write_txn_snapshot(
        tmp_path,
        team_ids=[147, 119],
        entries=[],  # placement is outside this capture's coverage entirely
        observed_at_utc="2026-08-01T12:00:00Z",
        start_date="2026-07-15",
    )
    _write_roster_snapshot(
        tmp_path,
        team_ids=[147, 119],
        entries=[_roster_entry(147, "Gerrit Cole", "Injured 60-Day")],
        observed_at_utc="2026-08-01T17:00:00Z",
    )

    result = mlb_player_availability.matchup_player_availability(
        data_root=tmp_path,
        home_team="New York Yankees",
        away_team="Los Angeles Dodgers",
        game_date="2026-08-01",
        observed_at=datetime(2026, 8, 1, 18, tzinfo=UTC),
        event_start=datetime(2026, 8, 1, 23, tzinfo=UTC),
        event_id="game-1",
    )

    assert result["probable_starter_unavailable_home"] == 1.0


def test_stale_roster_snapshot_falls_back_to_transactions(tmp_path, monkeypatch) -> None:
    _stub_starters(monkeypatch)
    _write_txn_snapshot(
        tmp_path,
        team_ids=[147, 119],
        entries=[],
        observed_at_utc="2026-08-01T12:00:00Z",
    )
    _write_roster_snapshot(
        tmp_path,
        team_ids=[147, 119],
        entries=[_roster_entry(147, "Gerrit Cole", "Injured 15-Day")],
        observed_at_utc="2026-07-29T12:00:00Z",  # 3 days old, past the 24h default
        day="2026-07-29",
    )

    result = mlb_player_availability.matchup_player_availability(
        data_root=tmp_path,
        home_team="New York Yankees",
        away_team="Los Angeles Dodgers",
        game_date="2026-08-01",
        observed_at=datetime(2026, 8, 1, 18, tzinfo=UTC),
        event_start=datetime(2026, 8, 1, 23, tzinfo=UTC),
        event_id="game-1",
    )

    assert result["probable_starter_unavailable_home"] == 0.0
    assert result["home_probable_starter_source"] == "transactions"


def test_roster_snapshot_captured_after_the_decision_is_never_used(tmp_path, monkeypatch) -> None:
    """A roster snapshot captured after observed_at reflects knowledge that
    did not exist yet at decision time -- must never be used, even though
    it's well within the freshness window measured the other direction."""
    _stub_starters(monkeypatch)
    _write_txn_snapshot(
        tmp_path,
        team_ids=[147, 119],
        entries=[],
        observed_at_utc="2026-08-01T12:00:00Z",
    )
    _write_roster_snapshot(
        tmp_path,
        team_ids=[147, 119],
        entries=[_roster_entry(147, "Gerrit Cole", "Injured 15-Day")],
        observed_at_utc="2026-08-01T20:00:00Z",  # after observed_at below
    )

    result = mlb_player_availability.matchup_player_availability(
        data_root=tmp_path,
        home_team="New York Yankees",
        away_team="Los Angeles Dodgers",
        game_date="2026-08-01",
        observed_at=datetime(2026, 8, 1, 18, tzinfo=UTC),
        event_start=datetime(2026, 8, 1, 23, tzinfo=UTC),
        event_id="game-1",
    )

    assert result["probable_starter_unavailable_home"] == 0.0
    assert result["home_probable_starter_source"] == "transactions"


def test_unrecognized_roster_status_fails_closed(tmp_path, monkeypatch) -> None:
    _stub_starters(monkeypatch)
    _write_txn_snapshot(
        tmp_path,
        team_ids=[147, 119],
        entries=[],
        observed_at_utc="2026-08-01T12:00:00Z",
    )
    _write_roster_snapshot(
        tmp_path,
        team_ids=[147, 119],
        entries=[_roster_entry(147, "Gerrit Cole", "Some New Status Never Seen Before")],
        observed_at_utc="2026-08-01T17:00:00Z",
    )

    with pytest.raises(ValueError, match="NO_CALL_MLB_AVAILABILITY_STATUS_UNKNOWN"):
        mlb_player_availability.matchup_player_availability(
            data_root=tmp_path,
            home_team="New York Yankees",
            away_team="Los Angeles Dodgers",
            game_date="2026-08-01",
            observed_at=datetime(2026, 8, 1, 18, tzinfo=UTC),
            event_start=datetime(2026, 8, 1, 23, tzinfo=UTC),
            event_id="game-1",
        )


def test_recent_il_placement_flags_starter_unavailable(tmp_path, monkeypatch) -> None:
    _stub_starters(monkeypatch)
    _write_txn_snapshot(
        tmp_path,
        team_ids=[147, 119],
        entries=[
            _entry(147, "Gerrit Cole", "2026-07-28", "Injured List (15-Day)"),
        ],
        observed_at_utc="2026-08-01T12:00:00Z",
    )

    result = mlb_player_availability.matchup_player_availability(
        data_root=tmp_path,
        home_team="New York Yankees",
        away_team="Los Angeles Dodgers",
        game_date="2026-08-01",
        observed_at=datetime(2026, 8, 1, 18, tzinfo=UTC),
        event_start=datetime(2026, 8, 1, 23, tzinfo=UTC),
        event_id="game-1",
    )

    assert result["probable_starter_unavailable_home"] == 1.0
    assert result["probable_starter_unavailable_away"] == 0.0


def test_same_day_transaction_is_never_trusted_regardless_of_wall_clock_order(tmp_path, monkeypatch) -> None:
    """Real gap found by review 2026-08-02: MLB Stats API's transaction
    `date` field has no time-of-day component, only a calendar date. A
    transaction reported on the SAME day as the decision could have
    happened before or after the decision within that day -- there's no
    way to tell from the data. Must be excluded entirely (not just
    "excluded if literally later"), even when it happens to be an IL
    placement that, in real wall-clock time, actually occurred before this
    decision -- the point is we cannot safely know that from the data.
    """
    _stub_starters(monkeypatch)
    _write_txn_snapshot(
        tmp_path,
        team_ids=[147, 119],
        entries=[
            # Reported the same calendar day as the decision below --
            # ambiguous whether this happened before or after the decision.
            _entry(147, "Gerrit Cole", "2026-08-01", "Injured List (15-Day)"),
        ],
        observed_at_utc="2026-08-01T12:00:00Z",
    )

    result = mlb_player_availability.matchup_player_availability(
        data_root=tmp_path,
        home_team="New York Yankees",
        away_team="Los Angeles Dodgers",
        game_date="2026-08-01",
        observed_at=datetime(2026, 8, 1, 18, tzinfo=UTC),
        event_start=datetime(2026, 8, 1, 23, tzinfo=UTC),
        event_id="game-1",
    )

    assert result["probable_starter_unavailable_home"] == 0.0


def test_transaction_the_day_before_decision_is_still_trusted(tmp_path, monkeypatch) -> None:
    """Contrast case for the test above: a transaction reported on a
    strictly earlier calendar day than the decision remains genuinely
    safe and must still be used."""
    _stub_starters(monkeypatch)
    _write_txn_snapshot(
        tmp_path,
        team_ids=[147, 119],
        entries=[
            _entry(147, "Gerrit Cole", "2026-07-31", "Injured List (15-Day)"),
        ],
        observed_at_utc="2026-08-01T12:00:00Z",
    )

    result = mlb_player_availability.matchup_player_availability(
        data_root=tmp_path,
        home_team="New York Yankees",
        away_team="Los Angeles Dodgers",
        game_date="2026-08-01",
        observed_at=datetime(2026, 8, 1, 18, tzinfo=UTC),
        event_start=datetime(2026, 8, 1, 23, tzinfo=UTC),
        event_id="game-1",
    )

    assert result["probable_starter_unavailable_home"] == 1.0


def test_activation_after_placement_clears_the_flag(tmp_path, monkeypatch) -> None:
    _stub_starters(monkeypatch)
    _write_txn_snapshot(
        tmp_path,
        team_ids=[147, 119],
        entries=[
            _entry(147, "Gerrit Cole", "2026-07-10", "Injured List (15-Day)"),
            _entry(147, "Gerrit Cole", "2026-07-26", "Activated"),
        ],
        observed_at_utc="2026-08-01T12:00:00Z",
    )

    result = mlb_player_availability.matchup_player_availability(
        data_root=tmp_path,
        home_team="New York Yankees",
        away_team="Los Angeles Dodgers",
        game_date="2026-08-01",
        observed_at=datetime(2026, 8, 1, 18, tzinfo=UTC),
        event_start=datetime(2026, 8, 1, 23, tzinfo=UTC),
        event_id="game-1",
    )

    assert result["probable_starter_unavailable_home"] == 0.0


def test_rehab_assignment_flags_starter_unavailable_even_without_an_in_window_il_placement(
    tmp_path, monkeypatch
) -> None:
    """Real, common (291 distinct instances live-confirmed 2026-08-01):
    "sent RHP X on a rehab assignment to Y" means still recovering, not yet
    activated. This must be caught even when the original IL-placement
    transaction itself falls outside the capture window -- the exact
    scenario a 60-day rolling lookback (cli.py) can produce for a player
    whose injury predates it.
    """
    _stub_starters(monkeypatch)
    _write_txn_snapshot(
        tmp_path,
        team_ids=[147, 119],
        entries=[
            _entry(
                147,
                "Gerrit Cole",
                "2026-07-29",
                "Status Change",
            )
            | {
                "description": "New York Yankees sent RHP Gerrit Cole on a rehab assignment to Somerset Patriots."
            },
        ],
        observed_at_utc="2026-08-01T12:00:00Z",
    )

    result = mlb_player_availability.matchup_player_availability(
        data_root=tmp_path,
        home_team="New York Yankees",
        away_team="Los Angeles Dodgers",
        game_date="2026-08-01",
        observed_at=datetime(2026, 8, 1, 18, tzinfo=UTC),
        event_start=datetime(2026, 8, 1, 23, tzinfo=UTC),
        event_id="game-1",
    )

    assert result["probable_starter_unavailable_home"] == 1.0


def test_activation_after_rehab_assignment_still_clears_the_flag(tmp_path, monkeypatch) -> None:
    _stub_starters(monkeypatch)
    _write_txn_snapshot(
        tmp_path,
        team_ids=[147, 119],
        entries=[
            _entry(147, "Gerrit Cole", "2026-07-20", "Status Change")
            | {
                "description": "New York Yankees sent RHP Gerrit Cole on a rehab assignment to Somerset Patriots."
            },
            _entry(147, "Gerrit Cole", "2026-07-30", "Activated")
            | {"description": "New York Yankees activated RHP Gerrit Cole from the 15-day injured list."},
        ],
        observed_at_utc="2026-08-01T12:00:00Z",
    )

    result = mlb_player_availability.matchup_player_availability(
        data_root=tmp_path,
        home_team="New York Yankees",
        away_team="Los Angeles Dodgers",
        game_date="2026-08-01",
        observed_at=datetime(2026, 8, 1, 18, tzinfo=UTC),
        event_start=datetime(2026, 8, 1, 23, tzinfo=UTC),
        event_id="game-1",
    )

    assert result["probable_starter_unavailable_home"] == 0.0


def test_retroactively_backdated_effective_date_never_leaks_future_knowledge(tmp_path, monkeypatch) -> None:
    """The single highest-value correctness test for this feature.

    A real MLB Stats API transaction can be REPORTED on one date but marked
    "effective" (effectiveDate) on an earlier date -- e.g. announced 2026-08-05
    as "retroactive to 2026-07-20". A prediction decided on 2026-08-01 could
    not possibly have known about an announcement made 2026-08-05, no matter
    what earlier date that announcement claims to be effective from. Using
    effective_date instead of reported_date here would leak that future
    knowledge into a decision made days before it existed.
    """
    _stub_starters(monkeypatch)
    _write_txn_snapshot(
        tmp_path,
        team_ids=[147, 119],
        entries=[
            _entry(
                147,
                "Gerrit Cole",
                reported_date="2026-08-05",  # reported AFTER the decision date
                effective_date="2026-07-20",  # but claims to be effective BEFORE it
                type_desc="Injured List (15-Day)",
            ),
        ],
        observed_at_utc="2026-08-01T12:00:00Z",
        end_date="2026-08-10",
    )

    result = mlb_player_availability.matchup_player_availability(
        data_root=tmp_path,
        home_team="New York Yankees",
        away_team="Los Angeles Dodgers",
        game_date="2026-08-01",
        observed_at=datetime(2026, 8, 1, 18, tzinfo=UTC),
        event_start=datetime(2026, 8, 1, 23, tzinfo=UTC),
        event_id="game-1",
    )

    assert result["probable_starter_unavailable_home"] == 0.0


def test_missing_transactions_data_fails_closed(tmp_path, monkeypatch) -> None:
    _stub_starters(monkeypatch)
    with pytest.raises(ValueError, match="NO_CALL_MLB_AVAILABILITY_UNAVAILABLE"):
        mlb_player_availability.matchup_player_availability(
            data_root=tmp_path,
            home_team="New York Yankees",
            away_team="Los Angeles Dodgers",
            game_date="2026-08-01",
            observed_at=datetime(2026, 8, 1, 18, tzinfo=UTC),
            event_start=datetime(2026, 8, 1, 23, tzinfo=UTC),
            event_id="game-1",
        )


def test_stale_live_capture_fails_closed(tmp_path, monkeypatch) -> None:
    _stub_starters(monkeypatch)
    _write_txn_snapshot(
        tmp_path,
        team_ids=[147, 119],
        entries=[],
        # Captured 3 days before the decision, well past the 24h default.
        observed_at_utc="2026-07-29T12:00:00Z",
        day="2026-07-29",
    )

    with pytest.raises(ValueError, match="NO_CALL_MLB_AVAILABILITY_STALE"):
        mlb_player_availability.matchup_player_availability(
            data_root=tmp_path,
            home_team="New York Yankees",
            away_team="Los Angeles Dodgers",
            game_date="2026-08-01",
            observed_at=datetime(2026, 8, 1, 18, tzinfo=UTC),
            event_start=datetime(2026, 8, 1, 23, tzinfo=UTC),
            event_id="game-1",
        )


def test_retroactive_capture_after_the_decision_time_is_treated_as_fully_fresh(tmp_path, monkeypatch) -> None:
    """A backtest reconstructs a past decision using data captured much
    later (today). Because correctness comes from filtering each entry's own
    reported_date rather than from capture time, this must not be penalized
    as stale -- unlike a live pipeline that failed to refresh recently.
    """
    _stub_starters(monkeypatch)
    _write_txn_snapshot(
        tmp_path,
        team_ids=[147, 119],
        entries=[],
        observed_at_utc="2026-08-15T12:00:00Z",  # captured two weeks after the decision
        end_date="2026-08-15",
        day="2026-08-15",
    )

    result = mlb_player_availability.matchup_player_availability(
        data_root=tmp_path,
        home_team="New York Yankees",
        away_team="Los Angeles Dodgers",
        game_date="2026-08-01",
        observed_at=datetime(2026, 8, 1, 18, tzinfo=UTC),
        event_start=datetime(2026, 8, 1, 23, tzinfo=UTC),
        event_id="game-1",
    )

    assert result["availability_report_age_hours"] == 0.0


def test_il_irrelevant_transactions_are_ignored_not_treated_as_errors(tmp_path, monkeypatch) -> None:
    """Real MLB transaction history is dominated by non-IL moves (trades,
    options, recalls, waiver claims) that say nothing about availability --
    these must be silently ignored, not treated as an unrecognized/failed
    status, since only the IL-relevant subset determines the flag.
    """
    _stub_starters(monkeypatch)
    _write_txn_snapshot(
        tmp_path,
        team_ids=[147, 119],
        entries=[
            _entry(147, "Gerrit Cole", "2026-07-30", "Traded"),
            _entry(147, "Gerrit Cole", "2026-07-15", "Selected"),
        ],
        observed_at_utc="2026-08-01T12:00:00Z",
    )

    result = mlb_player_availability.matchup_player_availability(
        data_root=tmp_path,
        home_team="New York Yankees",
        away_team="Los Angeles Dodgers",
        game_date="2026-08-01",
        observed_at=datetime(2026, 8, 1, 18, tzinfo=UTC),
        event_start=datetime(2026, 8, 1, 23, tzinfo=UTC),
        event_id="game-1",
    )

    assert result["probable_starter_unavailable_home"] == 0.0


def test_real_statsapi_status_change_description_is_parsed_correctly(tmp_path, monkeypatch) -> None:
    """Regression for the live-verified real-world shape: MLB Stats API's
    typeDesc for IL moves is almost always the generic "Status Change" --
    the actual IL detail is free text in `description`.
    """
    _stub_starters(monkeypatch)
    _write_txn_snapshot(
        tmp_path,
        team_ids=[147, 119],
        entries=[
            {
                "team_id": 147,
                "player_id": 1,
                "player_name": "Gerrit Cole",
                "reported_date": "2026-07-28",
                "effective_date": "2026-07-28",
                "type_desc": "Status Change",
                "description": "New York Yankees placed RHP Gerrit Cole on the 15-day injured list. Right elbow inflammation.",
            },
        ],
        observed_at_utc="2026-08-01T12:00:00Z",
    )

    result = mlb_player_availability.matchup_player_availability(
        data_root=tmp_path,
        home_team="New York Yankees",
        away_team="Los Angeles Dodgers",
        game_date="2026-08-01",
        observed_at=datetime(2026, 8, 1, 18, tzinfo=UTC),
        event_start=datetime(2026, 8, 1, 23, tzinfo=UTC),
        event_id="game-1",
    )

    assert result["probable_starter_unavailable_home"] == 1.0


def test_postgame_observation_is_rejected(tmp_path, monkeypatch) -> None:
    _stub_starters(monkeypatch)
    _write_txn_snapshot(
        tmp_path,
        team_ids=[147, 119],
        entries=[],
        observed_at_utc="2026-08-01T12:00:00Z",
    )

    with pytest.raises(ValueError, match="NO_CALL_MLB_AVAILABILITY_INVALID_TIME"):
        mlb_player_availability.matchup_player_availability(
            data_root=tmp_path,
            home_team="New York Yankees",
            away_team="Los Angeles Dodgers",
            game_date="2026-08-01",
            observed_at=datetime(2026, 8, 2, tzinfo=UTC),
            event_start=datetime(2026, 8, 1, 23, tzinfo=UTC),
            event_id="game-1",
        )


# ── pitching-staff (bullpen) availability ───────────────────────────────


def test_pitching_staff_excludes_optioned_players_from_numerator_and_denominator(tmp_path) -> None:
    """The real bug caught by testing against live data: "Reassigned to
    Minors" is routine roster depth churn present for every team daily,
    not a health signal -- it must not inflate the unavailable share."""
    _write_roster_snapshot(
        tmp_path,
        team_ids=[147],
        entries=[
            _roster_entry(147, "Active Starter", "Active", position_type="Pitcher"),
            _roster_entry(147, "Injured Reliever", "Injured 15-Day", position_type="Pitcher"),
            _roster_entry(147, "AAA Depth", "Reassigned to Minors", position_type="Pitcher"),
            _roster_entry(147, "Some Catcher", "Active", position_type="Catcher"),
        ],
        observed_at_utc="2026-08-01T12:00:00Z",
    )

    result = mlb_player_availability.team_pitching_staff_availability(
        data_root=tmp_path,
        team="New York Yankees",
        observed_at=datetime(2026, 8, 1, 18, tzinfo=UTC),
    )

    # Denominator is 2 (Active Starter + Injured Reliever) -- the optioned
    # player and the non-pitcher are both excluded.
    assert result["pitching_staff_total"] == 2
    assert result["pitching_staff_unavailable"] == 1
    assert result["pitching_staff_unavailable_share"] == pytest.approx(0.5)


def test_matchup_pitching_staff_gap_favors_home_when_away_staff_is_worse(tmp_path) -> None:
    _write_roster_snapshot(
        tmp_path,
        team_ids=[147, 119],
        entries=[
            _roster_entry(147, "Yankees Pitcher A", "Active", position_type="Pitcher"),
            _roster_entry(147, "Yankees Pitcher B", "Active", position_type="Pitcher"),
            _roster_entry(119, "Dodgers Pitcher A", "Injured 60-Day", position_type="Pitcher"),
            _roster_entry(119, "Dodgers Pitcher B", "Active", position_type="Pitcher"),
        ],
        observed_at_utc="2026-08-01T12:00:00Z",
    )

    result = mlb_player_availability.matchup_pitching_staff_availability(
        data_root=tmp_path,
        home_team="New York Yankees",
        away_team="Los Angeles Dodgers",
        observed_at=datetime(2026, 8, 1, 18, tzinfo=UTC),
        event_start=datetime(2026, 8, 1, 23, tzinfo=UTC),
    )

    assert result["pitching_staff_unavailable_share_home"] == pytest.approx(0.0)
    assert result["pitching_staff_unavailable_share_away"] == pytest.approx(0.5)
    assert result["pitching_staff_unavailable_share_gap"] == pytest.approx(0.5)


def test_pitching_staff_no_pitchers_found_fails_closed(tmp_path) -> None:
    _write_roster_snapshot(
        tmp_path,
        team_ids=[147],
        entries=[_roster_entry(147, "Some Catcher", "Active", position_type="Catcher")],
        observed_at_utc="2026-08-01T12:00:00Z",
    )

    with pytest.raises(ValueError, match="NO_CALL_MLB_PITCHING_STAFF_UNAVAILABLE"):
        mlb_player_availability.team_pitching_staff_availability(
            data_root=tmp_path,
            team="New York Yankees",
            observed_at=datetime(2026, 8, 1, 18, tzinfo=UTC),
        )


def test_pitching_staff_no_roster_snapshot_fails_closed(tmp_path) -> None:
    with pytest.raises(ValueError, match="NO_CALL_MLB_PITCHING_STAFF_UNAVAILABLE"):
        mlb_player_availability.team_pitching_staff_availability(
            data_root=tmp_path,
            team="New York Yankees",
            observed_at=datetime(2026, 8, 1, 18, tzinfo=UTC),
        )


def test_pitching_staff_unrecognized_status_fails_closed(tmp_path) -> None:
    _write_roster_snapshot(
        tmp_path,
        team_ids=[147],
        entries=[
            _roster_entry(147, "Active Starter", "Active", position_type="Pitcher"),
            _roster_entry(147, "Mystery Pitcher", "Some New Status", position_type="Pitcher"),
        ],
        observed_at_utc="2026-08-01T12:00:00Z",
    )

    with pytest.raises(ValueError, match="NO_CALL_MLB_AVAILABILITY_STATUS_UNKNOWN"):
        mlb_player_availability.team_pitching_staff_availability(
            data_root=tmp_path,
            team="New York Yankees",
            observed_at=datetime(2026, 8, 1, 18, tzinfo=UTC),
        )


def test_matchup_pitching_staff_rejects_postgame_observation(tmp_path) -> None:
    _write_roster_snapshot(
        tmp_path,
        team_ids=[147, 119],
        entries=[
            _roster_entry(147, "Yankees Pitcher", "Active", position_type="Pitcher"),
            _roster_entry(119, "Dodgers Pitcher", "Active", position_type="Pitcher"),
        ],
        observed_at_utc="2026-08-01T12:00:00Z",
    )

    with pytest.raises(ValueError, match="NO_CALL_MLB_PITCHING_STAFF_INVALID_TIME"):
        mlb_player_availability.matchup_pitching_staff_availability(
            data_root=tmp_path,
            home_team="New York Yankees",
            away_team="Los Angeles Dodgers",
            observed_at=datetime(2026, 8, 2, tzinfo=UTC),
            event_start=datetime(2026, 8, 1, 23, tzinfo=UTC),
        )


# ── position-player (lineup) availability ───────────────────────────────


def test_position_players_excludes_pitchers_and_optioned_players(tmp_path) -> None:
    _write_roster_snapshot(
        tmp_path,
        team_ids=[147],
        entries=[
            _roster_entry(147, "Starting Pitcher", "Active", position_type="Pitcher"),
            _roster_entry(147, "Active Outfielder", "Active", position_type="Outfielder"),
            _roster_entry(147, "Injured Infielder", "Injured 10-Day", position_type="Infielder"),
            _roster_entry(147, "AAA Catcher", "Reassigned to Minors", position_type="Catcher"),
        ],
        observed_at_utc="2026-08-01T12:00:00Z",
    )

    result = mlb_player_availability.team_position_player_availability(
        data_root=tmp_path,
        team="New York Yankees",
        observed_at=datetime(2026, 8, 1, 18, tzinfo=UTC),
    )

    # Denominator is 2 (Active Outfielder + Injured Infielder) -- the
    # pitcher and the optioned catcher are both excluded.
    assert result["position_players_total"] == 2
    assert result["position_players_unavailable"] == 1
    assert result["position_players_unavailable_share"] == pytest.approx(0.5)


def test_position_players_excludes_missing_position_type(tmp_path) -> None:
    """A roster snapshot captured before position_type was added to the
    capture schema has entries with position_type missing/empty -- must
    not be silently counted as a position player."""
    _write_roster_snapshot(
        tmp_path,
        team_ids=[147],
        entries=[
            _roster_entry(147, "Active Outfielder", "Active", position_type="Outfielder"),
            {
                "team_id": 147,
                "player_id": 2,
                "player_name": "Legacy Entry",
                "current_status": "Injured 10-Day",
            },
        ],
        observed_at_utc="2026-08-01T12:00:00Z",
    )

    result = mlb_player_availability.team_position_player_availability(
        data_root=tmp_path,
        team="New York Yankees",
        observed_at=datetime(2026, 8, 1, 18, tzinfo=UTC),
    )

    assert result["position_players_total"] == 1
    assert result["position_players_unavailable"] == 0


def test_matchup_position_players_gap_favors_home_when_away_is_worse(tmp_path) -> None:
    _write_roster_snapshot(
        tmp_path,
        team_ids=[147, 119],
        entries=[
            _roster_entry(147, "Yankees Hitter A", "Active", position_type="Outfielder"),
            _roster_entry(147, "Yankees Hitter B", "Active", position_type="Infielder"),
            _roster_entry(119, "Dodgers Hitter A", "Injured 60-Day", position_type="Outfielder"),
            _roster_entry(119, "Dodgers Hitter B", "Active", position_type="Infielder"),
        ],
        observed_at_utc="2026-08-01T12:00:00Z",
    )

    result = mlb_player_availability.matchup_position_player_availability(
        data_root=tmp_path,
        home_team="New York Yankees",
        away_team="Los Angeles Dodgers",
        observed_at=datetime(2026, 8, 1, 18, tzinfo=UTC),
        event_start=datetime(2026, 8, 1, 23, tzinfo=UTC),
    )

    assert result["position_players_unavailable_share_home"] == pytest.approx(0.0)
    assert result["position_players_unavailable_share_away"] == pytest.approx(0.5)
    assert result["position_players_unavailable_share_gap"] == pytest.approx(0.5)


def test_position_players_no_matching_players_fails_closed(tmp_path) -> None:
    _write_roster_snapshot(
        tmp_path,
        team_ids=[147],
        entries=[_roster_entry(147, "Only A Pitcher", "Active", position_type="Pitcher")],
        observed_at_utc="2026-08-01T12:00:00Z",
    )

    with pytest.raises(ValueError, match="NO_CALL_MLB_POSITION_PLAYERS_UNAVAILABLE"):
        mlb_player_availability.team_position_player_availability(
            data_root=tmp_path,
            team="New York Yankees",
            observed_at=datetime(2026, 8, 1, 18, tzinfo=UTC),
        )


def test_matchup_position_players_rejects_postgame_observation(tmp_path) -> None:
    _write_roster_snapshot(
        tmp_path,
        team_ids=[147, 119],
        entries=[
            _roster_entry(147, "Yankees Hitter", "Active", position_type="Outfielder"),
            _roster_entry(119, "Dodgers Hitter", "Active", position_type="Outfielder"),
        ],
        observed_at_utc="2026-08-01T12:00:00Z",
    )

    with pytest.raises(ValueError, match="NO_CALL_MLB_POSITION_PLAYERS_INVALID_TIME"):
        mlb_player_availability.matchup_position_player_availability(
            data_root=tmp_path,
            home_team="New York Yankees",
            away_team="Los Angeles Dodgers",
            observed_at=datetime(2026, 8, 2, tzinfo=UTC),
            event_start=datetime(2026, 8, 1, 23, tzinfo=UTC),
        )
