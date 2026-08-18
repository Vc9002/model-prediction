from __future__ import annotations

from model_prediction.data_sources.mlb_lineups import (
    EXPECTED_LINEUP_SIZE,
    LineupStore,
    build_lineup_snapshot,
    capture_date,
    classify_lineup_state,
)


def _game(game_pk=824319, state="Pre-Game", start="2026-08-19T00:40:00Z") -> dict:
    return {
        "gamePk": game_pk,
        "gameDate": start,
        "status": {"detailedState": state},
        "teams": {
            "away": {"team": {"name": "Arizona Diamondbacks"}},
            "home": {"team": {"name": "Colorado Rockies"}},
        },
    }


def _boxscore(away_order=None, home_order=None) -> dict:
    away_order = list(range(1, 10)) if away_order is None else away_order
    home_order = list(range(101, 110)) if home_order is None else home_order

    def side(order):
        return {
            "battingOrder": order,
            "players": {
                f"ID{pid}": {
                    "person": {"fullName": f"Player {pid}"},
                    "position": {"abbreviation": "CF"},
                }
                for pid in order
            },
        }

    return {"teams": {"away": side(away_order), "home": side(home_order)}}


def test_pregame_states_are_decision_grade_and_started_games_are_not() -> None:
    """The whole point: a started game has a CONFIRMED order, which is
    exactly the information a pregame decision may not have."""
    assert classify_lineup_state("Pre-Game") == "pregame"
    assert classify_lineup_state("Warmup") == "pregame"
    assert classify_lineup_state("Scheduled") == "pregame"
    assert classify_lineup_state("In Progress") == "in_game"
    assert classify_lineup_state("Final") == "final"


def test_unrecognized_status_is_never_optimistically_pregame() -> None:
    """An unknown status must fail toward unusable. Treating it as pregame
    is the direction that leaks a confirmed lineup into a pregame decision."""
    assert classify_lineup_state("Some Future Status MLB Invents") == "unknown"
    assert classify_lineup_state("") == "unknown"


def test_snapshot_captures_full_batting_order_with_slots() -> None:
    snap = build_lineup_snapshot(_game(), _boxscore(), observed_at_utc="2026-08-18T22:00:00Z")

    assert snap["lineup_state"] == "pregame"
    assert snap["lineup_complete"] is True
    assert snap["away"]["size"] == EXPECTED_LINEUP_SIZE
    assert [e["slot"] for e in snap["away"]["batting_order"]] == list(range(1, 10))
    assert snap["away"]["batting_order"][0]["player_id"] == 1
    assert snap["away"]["batting_order"][0]["player_name"] == "Player 1"
    assert snap["home"]["batting_order"][0]["player_id"] == 101
    assert snap["observed_at_utc"] == "2026-08-18T22:00:00Z"
    assert snap["game_start_utc"] == "2026-08-19T00:40:00Z"


def test_partial_lineup_is_recorded_but_flagged_incomplete() -> None:
    """A half-posted lineup must be visibly incomplete, not silently short
    -- a consumer that averages over 6 of 9 hitters would quietly produce a
    different quantity than the one it claims to compute."""
    snap = build_lineup_snapshot(
        _game(), _boxscore(away_order=[1, 2, 3]), observed_at_utc="2026-08-18T22:00:00Z"
    )

    assert snap["lineup_complete"] is False
    assert snap["away"]["size"] == 3
    assert snap["home"]["size"] == EXPECTED_LINEUP_SIZE


def test_capture_skips_a_failing_game_without_losing_the_rest_of_the_date() -> None:
    """Lineups cannot be re-captured later, so one bad boxscore must not
    cost the other games on the slate."""

    class FlakyClient:
        def schedule(self, game_date):
            return [_game(game_pk=1), _game(game_pk=2), _game(game_pk=3)]

        def boxscore(self, game_pk):
            if game_pk == 2:
                raise RuntimeError("boxscore unavailable")
            return _boxscore()

    snaps = capture_date("2026-08-18", client=FlakyClient())

    assert [s["game_pk"] for s in snaps] == [1, 3]


def test_recapturing_an_unchanged_lineup_later_writes_nothing(tmp_path) -> None:
    """Found on live data: every capture stamps a fresh observed_at_utc, so
    an identity containing it is unique every run and the store never
    dedupes -- three runs of an unchanged 15-game slate wrote 45 rows. At
    the intended hourly cadence that is ~360 duplicate rows a day. Dedupe
    must be on lineup CONTENT, so this test re-merges the same lineup with
    a LATER timestamp, which is what actually happens in production."""
    store = LineupStore(tmp_path / "lineups.jsonl")
    first = build_lineup_snapshot(_game(), _boxscore(), observed_at_utc="2026-08-18T20:00:00Z")
    an_hour_later = build_lineup_snapshot(_game(), _boxscore(), observed_at_utc="2026-08-18T21:00:00Z")

    assert store.merge([first]) == 1
    assert store.merge([an_hour_later]) == 0
    assert len(store.rows()) == 1
    # The retained row records when the lineup was FIRST seen.
    assert store.rows()[0]["observed_at_utc"] == "2026-08-18T20:00:00Z"


def test_store_is_idempotent_but_records_a_real_lineup_change(tmp_path) -> None:
    """A changed order is a late scratch -- real signal, appended as a new
    row, never an overwrite of what we believed earlier."""
    store = LineupStore(tmp_path / "lineups.jsonl")
    first = build_lineup_snapshot(_game(), _boxscore(), observed_at_utc="2026-08-18T22:00:00Z")

    assert store.merge([first]) == 1
    assert store.merge([first]) == 0  # idempotent re-run

    scratched = build_lineup_snapshot(
        _game(),
        _boxscore(away_order=[99, 2, 3, 4, 5, 6, 7, 8, 9]),
        observed_at_utc="2026-08-18T23:30:00Z",
    )
    assert store.merge([scratched]) == 1

    rows = store.rows()
    assert len(rows) == 2
    assert rows[0]["away"]["batting_order"][0]["player_id"] == 1
    assert rows[1]["away"]["batting_order"][0]["player_id"] == 99
