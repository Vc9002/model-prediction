from __future__ import annotations

import json

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
    assert snap["first_observed_at_utc"] == "2026-08-18T22:00:00Z"
    assert snap["last_observed_at_utc"] == "2026-08-18T22:00:00Z"
    assert snap["capture_count"] == 1
    assert snap["content_hash"]
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


def test_capture_never_fetches_a_boxscore_for_a_started_game() -> None:
    """Schedule-aware: a started game's order is recoverable from its final
    boxscore forever, so it is filtered out BEFORE any request. This is what
    makes an hourly run cost one request when there is nothing to do."""
    fetched = []

    class CountingClient:
        def schedule(self, game_date):
            return [
                _game(game_pk=1, state="Pre-Game"),
                _game(game_pk=2, state="In Progress"),
                _game(game_pk=3, state="Final"),
            ]

        def boxscore(self, game_pk):
            fetched.append(game_pk)
            return _boxscore()

    snaps = capture_date("2026-08-18", client=CountingClient())

    assert fetched == [1]
    assert [s["game_pk"] for s in snaps] == [1]


def test_capture_no_ops_when_nothing_is_unstarted() -> None:
    class AllDoneClient:
        def schedule(self, game_date):
            return [_game(game_pk=1, state="Final"), _game(game_pk=2, state="In Progress")]

        def boxscore(self, game_pk):
            raise AssertionError("must not fetch a boxscore when nothing is pregame")

    assert capture_date("2026-08-18", client=AllDoneClient()) == []


def test_a_game_with_no_lineup_posted_yet_is_not_recorded() -> None:
    """Before MLB posts an order the boxscore returns empty lists. Storing
    those would add a row per game per hour saying only 'not announced
    yet' -- observed live when a post-midnight run captured 15 rows with
    zero decision-grade lineups."""

    class NotPostedClient:
        def schedule(self, game_date):
            return [_game(game_pk=1)]

        def boxscore(self, game_pk):
            return _boxscore(away_order=[], home_order=[])

    assert capture_date("2026-08-18", client=NotPostedClient()) == []


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

    assert store.merge([first]) == {"written": 1, "confirmed": 0}
    assert store.merge([an_hour_later]) == {"written": 0, "confirmed": 1}

    rows = store.rows()
    assert len(rows) == 1
    # The row keeps BOTH ends: when the lineup was announced, and when we
    # last saw it still standing. A decision at 21:30 is 30 minutes from
    # the confirmation, not 90 from the announcement.
    assert rows[0]["first_observed_at_utc"] == "2026-08-18T20:00:00Z"
    assert rows[0]["last_observed_at_utc"] == "2026-08-18T21:00:00Z"
    assert rows[0]["capture_count"] == 2


def test_last_observed_never_moves_backwards(tmp_path) -> None:
    """The daily job and the hourly collector can both write, and a retry
    can land late. An out-of-order capture must still count as a
    confirmation without rewinding the freshness of the row."""
    store = LineupStore(tmp_path / "lineups.jsonl")
    store.merge([build_lineup_snapshot(_game(), _boxscore(), observed_at_utc="2026-08-18T21:00:00Z")])
    store.merge([build_lineup_snapshot(_game(), _boxscore(), observed_at_utc="2026-08-18T19:00:00Z")])

    row = store.rows()[0]
    assert row["last_observed_at_utc"] == "2026-08-18T21:00:00Z"
    assert row["capture_count"] == 2


def test_rows_written_before_content_hash_existed_are_still_readable(tmp_path) -> None:
    """A schema-v1 archive predates content_hash. Reading one must not
    require a migration pass, or an old file silently becomes unreadable
    evidence."""
    path = tmp_path / "lineups.jsonl"
    legacy = build_lineup_snapshot(_game(), _boxscore(), observed_at_utc="2026-08-18T20:00:00Z")
    del legacy["content_hash"]
    path.write_text(json.dumps(legacy, sort_keys=True) + "\n")

    store = LineupStore(path)
    same_lineup = build_lineup_snapshot(_game(), _boxscore(), observed_at_utc="2026-08-18T21:00:00Z")

    assert store.merge([same_lineup]) == {"written": 0, "confirmed": 1}
    assert len(store.rows()) == 1


def test_store_is_idempotent_but_records_a_real_lineup_change(tmp_path) -> None:
    """A changed order is a late scratch -- real signal, appended as a new
    row, never an overwrite of what we believed earlier."""
    store = LineupStore(tmp_path / "lineups.jsonl")
    first = build_lineup_snapshot(_game(), _boxscore(), observed_at_utc="2026-08-18T22:00:00Z")

    assert store.merge([first]) == {"written": 1, "confirmed": 0}
    assert store.merge([first]) == {"written": 0, "confirmed": 1}

    scratched = build_lineup_snapshot(
        _game(),
        _boxscore(away_order=[99, 2, 3, 4, 5, 6, 7, 8, 9]),
        observed_at_utc="2026-08-18T23:30:00Z",
    )
    assert store.merge([scratched]) == {"written": 1, "confirmed": 0}

    rows = store.rows()
    assert len(rows) == 2
    assert rows[0]["away"]["batting_order"][0]["player_id"] == 1
    assert rows[1]["away"]["batting_order"][0]["player_id"] == 99
    assert rows[0]["content_hash"] != rows[1]["content_hash"]


def test_capture_reads_the_previous_utc_card_for_late_local_games() -> None:
    """The schedule endpoint buckets by venue-LOCAL date: a 21:40 PDT game
    (04:40Z the next UTC day) lives on the PREVIOUS day's card. A
    post-midnight run that asked only for today-UTC would silently miss
    exactly the late west-coast games the overnight wake planner exists to
    cover -- found by code review 2026-08-19 and verified against the live
    API."""

    class BucketedClient:
        def __init__(self) -> None:
            self.requested: list[str] = []

        def schedule(self, game_date: str) -> list[dict]:
            self.requested.append(game_date)
            if game_date == "2026-08-18":
                return [_game(game_pk=9001, state="Pre-Game", start="2026-08-19T04:40:00Z")]
            return []

        def boxscore(self, game_pk):
            return _boxscore()

    client = BucketedClient()
    snaps = capture_date("2026-08-19", client=client)

    assert client.requested == ["2026-08-18", "2026-08-19"]
    assert [s["game_pk"] for s in snaps] == [9001]


def test_capture_dedupes_a_game_seen_on_both_cards() -> None:
    class OverlappingClient:
        def schedule(self, game_date: str) -> list[dict]:
            return [_game(game_pk=9002, state="Pre-Game", start="2026-08-19T04:40:00Z")]

        def boxscore(self, game_pk):
            return _boxscore()

    snaps = capture_date("2026-08-19", client=OverlappingClient())

    assert len(snaps) == 1
