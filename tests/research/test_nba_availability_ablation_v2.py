"""Micro-tests for the pure helpers in scripts/ablate_nba_real_availability_v2.py.

Tiny synthetic rows only: the helpers are the part of the ablation that can be
wrong in ways a 1.1 GB real-data run would not reveal, so each behaviour that
the v2 definition changed (minutes weighting, replacement-relative shrinkage,
played-only history, id-keyed names, the true-last-game absence rule, and the
selection tie-break) gets one test.
"""

from __future__ import annotations

import pytest

from model_prediction.features.nba_player_logs import box_plus_minus_proxy
from scripts.ablate_nba_real_availability_v2 import (
    ConfigResult,
    build_name_to_ids,
    build_player_index,
    inferred_absent,
    minutes_weighted_impact,
    played_impacts_before,
    replacement_relative_value,
    resolve_player_name,
    select_winner,
    team_losses,
    value_for_name,
)


def _row(
    pid: str,
    name: str,
    minutes: float,
    *,
    points: float = 0.0,
    assists: float = 0.0,
    rebounds: float = 0.0,
    steals: float = 0.0,
    blocks: float = 0.0,
    turnovers: float = 0.0,
    fga: float = 0.0,
    dnp: bool = False,
    reason: str | None = None,
) -> dict:
    return {
        "player_id": pid,
        "name": name,
        "minutes": minutes,
        "points": points,
        "assists": assists,
        "rebounds": rebounds,
        "steals": steals,
        "blocks": blocks,
        "turnovers": turnovers,
        "fga": fga,
        "fta": 0.0,
        "starter": False,
        "active": minutes > 0,
        "did_not_play": dnp,
        "dnp_reason": reason,
    }


def test_minutes_weighted_impact_ranks_starter_above_bench_scorer() -> None:
    bench = _row("b", "Bench", 15, points=20, assists=1, rebounds=2, turnovers=1, fga=14)
    starter = _row(
        "s", "Starter", 35, points=20, assists=5, rebounds=6, steals=1, blocks=1, turnovers=2, fga=16
    )
    # The old per-36 proxy over-rates the 15-minute scorer: that is the flaw.
    assert box_plus_minus_proxy(bench) > box_plus_minus_proxy(starter)
    assert minutes_weighted_impact(bench) < minutes_weighted_impact(starter)
    assert minutes_weighted_impact(_row("z", "Zero", 0.0, points=10)) == 0.0
    assert minutes_weighted_impact(_row("n", "Neg", -3.0, points=10)) == 0.0


def test_replacement_relative_value_shrinks_toward_replacement() -> None:
    value = replacement_relative_value([3.0, 2.0, 4.0], 1.0, prior_weight=5.0)
    assert value == pytest.approx(0.75)
    # Shrunk mean below the replacement level clamps to exactly zero.
    assert replacement_relative_value([0.2, 0.1], 1.0, prior_weight=5.0) == 0.0
    # Only the last `lookback` impacts count.
    assert replacement_relative_value([100.0, 3.0, 2.0, 4.0], 1.0, lookback=3, prior_weight=5.0) == (
        pytest.approx(0.75)
    )
    assert replacement_relative_value([], 1.0) is None


def test_build_player_index_ignores_dnp_rows_and_follows_player_across_teams() -> None:
    rows_by_event = {
        "e1": {
            "A": [_row("1", "One", 30, points=20), _row("2", "Two", 0.0, dnp=True, reason="INJURY")],
            "B": [_row("3", "Three", 20, points=10)],
        },
        "e2": {
            "A": [_row("2", "Two", 25, points=15)],
            # player 1 traded to B between e1 and e2
            "B": [_row("1", "One", 28, points=18), _row("3", "Three", 22, points=9)],
        },
    }
    event_start = {"e1": "2025-11-01T00:00Z", "e2": "2025-11-03T00:00Z"}
    index = build_player_index(rows_by_event, event_start)
    # Player 2's DNP row at e1 never enters the history.
    assert [s for s, _ in index.by_player["2"]] == ["2025-11-03T00:00Z"]
    # Player 1 keeps one history across both teams, sorted by start.
    assert [s for s, _ in index.by_player["1"]] == ["2025-11-01T00:00Z", "2025-11-03T00:00Z"]
    # Strictly-before cutoff.
    assert len(played_impacts_before(index, "1", "2025-11-03T00:00Z")) == 1
    assert len(played_impacts_before(index, "1", "2025-11-03T00:01Z")) == 2
    assert played_impacts_before(index, "missing", "2030-01-01T00:00Z") == []
    # Events without a schedule entry are skipped and counted.
    skipped = build_player_index({"x": {"A": [_row("9", "Nine", 10)]}}, {})
    assert skipped.skipped_events == 1 and skipped.by_player == {}


def test_name_resolution_is_pit_and_fails_closed() -> None:
    triples = [
        ("2025-11-01T00:00Z", "1", "Jo Smith"),
        ("2025-11-01T00:00Z", "5", "Solo Player"),
        ("2025-11-05T00:00Z", "2", "Jo  smith"),
    ]
    # A second id appearing AT the cutoff does not make the name ambiguous yet.
    early = build_name_to_ids(triples, "2025-11-05T00:00Z")
    assert resolve_player_name("jo smith", early) == ("1", "ok")
    late = build_name_to_ids(triples, "2025-11-06T00:00Z")
    assert resolve_player_name("Jo Smith", late) == (None, "ambiguous")
    assert resolve_player_name("Nobody Here", late) == (None, "no_match")
    assert resolve_player_name("Solo   Player", late) == ("5", "ok")

    values = {"5": 2.2}
    assert value_for_name("Solo Player", late, values, 1.1) == (2.2, "ok")
    assert value_for_name("Jo Smith", late, values, 1.1) == (1.1, "ambiguous")
    assert value_for_name("Nobody Here", late, values, 1.1) == (1.1, "no_match")


def _team_fixture(last_covered: bool):
    s = ["2025-11-01T00:00Z", "2025-11-03T00:00Z", "2025-11-05T00:00Z"]
    schedule = [(s[0], "e1"), (s[1], "e2"), (s[2], "e3")]
    rows = {
        "e1": {"T": [_row("1", "One", 30), _row("2", "Two", 30), _row("3", "Three", 2)]},
        "e2": {
            "T": [
                _row("1", "One", 30),
                _row("2", "Two", 30),
                # an injury DNP in an OLDER covered game must not count when the true
                # last game is uncovered
                _row("3", "Three", 0.0, dnp=True, reason="KNEE INJURY"),
            ]
        },
    }
    covered = [(s[0], "e1"), (s[1], "e2")]
    if last_covered:
        rows["e3"] = {
            "T": [
                _row("1", "One", 0.0, dnp=True, reason="LEFT ANKLE INJURY"),
                _row("2", "Two", 0.0, dnp=True, reason="COACH'S DECISION"),
                _row("3", "Three", 0.0, dnp=True, reason="ILLNESS"),
            ]
        }
        covered.append((s[2], "e3"))
    return s, schedule, covered, rows


def test_inferred_absent_uses_true_last_game_not_last_covered_game() -> None:
    _, schedule, covered, rows = _team_fixture(last_covered=False)
    result = inferred_absent("T", "2025-11-07T00:00Z", schedule, covered, rows)
    assert result.status == "uncovered"
    assert result.absent == ()


def test_inferred_absent_covered_returns_rotation_players_with_real_absence_only() -> None:
    s, schedule, covered, rows = _team_fixture(last_covered=True)
    result = inferred_absent("T", "2025-11-07T00:00Z", schedule, covered, rows)
    assert result.status == "ok"
    # Player 1 (rotation, injury DNP) is out; player 2 is COACH'S DECISION; player 3 is
    # below the 8-minute rotation floor.
    assert result.absent == (("1", "One"),)
    assert inferred_absent("T", s[0], schedule, covered, rows).status == "no_prior_game"


def test_team_losses_static_and_real_share_the_absent_set() -> None:
    rows_by_event = {
        "e1": {"T": [_row("1", "Nikola Jokic", 36, points=30, assists=9, rebounds=12, fga=18)]},
        "e2": {"T": [_row("1", "Nikola Jokic", 36, points=28, assists=8, rebounds=11, fga=17)]},
    }
    event_start = {"e1": "2025-11-01T00:00Z", "e2": "2025-11-03T00:00Z"}
    index = build_player_index(rows_by_event, event_start)
    absent = (("1", "Nikola Jokic"), ("99", "Unknown Bench"))
    loss = team_losses(absent, "2025-11-05T00:00Z", index, (0.5, 1.0))
    # Static: Jokic is an anchor tier (4.8), the unknown name falls to 1.1.
    assert loss.static_loss == pytest.approx(5.9)
    assert loss.static_tier_hits == {4.8: 1, 2.5: 0, 1.1: 1}
    # Real: player 99 has no played history so falls back to static 1.1 and is flagged.
    assert loss.real_fallback == 1
    assert loss.real_losses[0.5] > loss.real_losses[1.0] > 1.1


def test_select_winner_breaks_exact_mae_ties_on_brier() -> None:
    a = ConfigResult(key="a", n=10, mae_margin=10.0, brier=0.24)
    b = ConfigResult(key="b", n=10, mae_margin=10.0, brier=0.23)
    c = ConfigResult(key="c", n=10, mae_margin=10.0000001, brier=0.10)
    assert select_winner([a, b, c]).key == "b"
    assert select_winner([a, c]).key == "a"
