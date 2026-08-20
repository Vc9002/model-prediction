"""features/batter_offense.py -- point-in-time batter PIT priors.

Mirrors test_starter_history.py's fixture shape (real snapshot JSONL, real
point-in-time filtering) since the module deliberately copies bullpen.py's
credibility-shrinkage design for a team-level offense composite.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from model_prediction.features import batter_offense


def _write_snapshots(path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _batting_line(pa, hits, walks, hbp, strikeouts, total_bases) -> dict:
    return {
        "plateAppearances": pa,
        "hits": hits,
        "baseOnBalls": walks,
        "hitByPitch": hbp,
        "strikeOuts": strikeouts,
        "totalBases": total_bases,
    }


def _game(game_start, home_players, away_players) -> dict:
    return {
        "game_start_utc": game_start,
        "home": {"team_name": "Home Team", "players": home_players},
        "away": {"team_name": "Away Team", "players": away_players},
    }


def _player(player_id, name, batting) -> dict:
    return {"player_id": player_id, "name": name, "batting": batting}


def _clear_caches() -> None:
    batter_offense._PLAYER_INDEX_CACHE.clear()
    batter_offense._TEAM_GAME_INDEX_CACHE.clear()
    batter_offense._LEAGUE_RATES_CACHE.clear()


class TestPlayerShrunkRates:
    def test_unknown_player_gets_pure_league_prior(self, tmp_path) -> None:
        path = tmp_path / "snapshots.jsonl"
        rows = [
            _game(
                "2026-05-01T18:00:00Z",
                [_player(100, "Someone", _batting_line(4, 1, 0, 0, 1, 1))],
                [],
            )
        ]
        _write_snapshots(path, rows)
        _clear_caches()

        league = batter_offense._league_rates(path)
        result = batter_offense.player_shrunk_rates(
            999, datetime(2026, 5, 10, tzinfo=UTC), snapshot_path=path
        )

        assert result["pa"] == 0
        assert result["production"] == pytest.approx(league["production"])
        assert result["discipline"] == pytest.approx(league["discipline"])
        assert result["power"] == pytest.approx(league["power"])

    def test_excludes_games_at_or_after_decision_point_in_time(self, tmp_path) -> None:
        path = tmp_path / "snapshots.jsonl"
        rows = [
            _game(
                "2026-05-01T18:00:00Z",
                [_player(100, "Batter", _batting_line(4, 4, 0, 0, 0, 4))],
                [],
            ),
            _game(
                "2026-05-10T18:00:00Z",  # future relative to decision below
                [_player(100, "Batter", _batting_line(4, 0, 0, 0, 4, 0))],
                [],
            ),
        ]
        _write_snapshots(path, rows)
        _clear_caches()

        result = batter_offense.player_shrunk_rates(100, datetime(2026, 5, 5, tzinfo=UTC), snapshot_path=path)

        assert result["pa"] == 4  # only the first game counts


class TestTeamOffensePitProfile:
    def test_unavailable_from_source_with_no_history(self, tmp_path) -> None:
        path = tmp_path / "snapshots.jsonl"
        _write_snapshots(path, [])
        _clear_caches()

        result = batter_offense.team_offense_pit_profile(
            "Home Team", datetime(2026, 5, 10, tzinfo=UTC), snapshot_path=path
        )

        assert result["status"] == "unavailable_from_source"

    def test_available_when_team_has_recent_games(self, tmp_path) -> None:
        path = tmp_path / "snapshots.jsonl"
        rows = [
            _game(
                f"2026-05-{d:02d}T18:00:00Z",
                [_player(100, "Slugger", _batting_line(4, 2, 1, 0, 0, 4))],
                [_player(200, "Away Bat", _batting_line(4, 1, 0, 0, 1, 1))],
            )
            for d in range(1, 6)
        ]
        _write_snapshots(path, rows)
        _clear_caches()

        result = batter_offense.team_offense_pit_profile(
            "Home Team", datetime(2026, 5, 20, tzinfo=UTC), snapshot_path=path
        )

        assert result["status"] == "available"
        assert result["composite"] is not None


class TestMatchupOffensePitGap:
    def test_returns_home_minus_away_gap_when_both_available(self, tmp_path) -> None:
        path = tmp_path / "snapshots.jsonl"
        rows = [
            _game(
                f"2026-05-{d:02d}T18:00:00Z",
                [_player(100, "Home Slugger", _batting_line(4, 3, 1, 0, 0, 6))],
                [_player(200, "Away Weak", _batting_line(4, 0, 0, 0, 3, 0))],
            )
            for d in range(1, 6)
        ]
        _write_snapshots(path, rows)
        _clear_caches()

        gap, available = batter_offense.matchup_offense_pit_gap(
            "Home Team", "Away Team", datetime(2026, 5, 20, tzinfo=UTC), snapshot_path=path
        )

        assert available is True
        assert gap > 0  # the stronger home hitter should produce a positive gap

    def test_unavailable_when_either_side_has_no_history(self, tmp_path) -> None:
        path = tmp_path / "snapshots.jsonl"
        rows = [
            _game(
                "2026-05-01T18:00:00Z",
                [_player(100, "Home Slugger", _batting_line(4, 3, 1, 0, 0, 6))],
                [],
            )
        ]
        _write_snapshots(path, rows)
        _clear_caches()

        gap, available = batter_offense.matchup_offense_pit_gap(
            "Home Team", "Away Team", datetime(2026, 5, 20, tzinfo=UTC), snapshot_path=path
        )

        assert available is False
        assert gap == 0.0
