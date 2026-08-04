"""features/starter_history.py -- live per-starter rolling ERA.

Mirrors test_bullpen.py's fixture shape (real snapshot JSONL, real
point-in-time filtering) since the module deliberately copies bullpen.py's
design for a different key (starter name, not team).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from model_prediction.features import starter_history


def _write_snapshots(path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _start(game_start, player_id, name, innings="6.0", earned_runs=2, *, side="home") -> dict:
    other_side = "away" if side == "home" else "home"
    return {
        "game_start_utc": game_start,
        side: {
            "team_name": "Team A",
            "pitcher_order": [player_id],
            "players": [
                {
                    "player_id": player_id,
                    "name": name,
                    "pitching": {"inningsPitched": innings, "earnedRuns": earned_runs},
                }
            ],
        },
        other_side: {"team_name": "Team B", "pitcher_order": [], "players": []},
    }


class TestStarterRollingEra:
    def test_computes_real_rolling_era_from_last_five_starts(self, tmp_path) -> None:
        path = tmp_path / "snapshots.jsonl"
        rows = [
            _start(f"2026-05-{d:02d}T18:00:00Z", 100, "Grayson Rodriguez", innings="6.0", earned_runs=2)
            for d in range(1, 8)  # 7 starts, only last 5 should count
        ]
        _write_snapshots(path, rows)
        starter_history._STARTER_INDEX_CACHE.clear()

        result = starter_history.starter_rolling_era(
            "Grayson Rodriguez", datetime(2026, 5, 20, tzinfo=UTC), snapshot_path=path
        )

        assert result["status"] == "available"
        assert result["starts"] == 5
        # 5 starts * 6.0 IP * 2 ER each = 30 IP, 10 ER -> ERA = 9*10/30 = 3.0
        assert result["era"] == pytest.approx(3.0)

    def test_excludes_starts_at_or_after_decision_point_in_time(self, tmp_path) -> None:
        path = tmp_path / "snapshots.jsonl"
        rows = [
            _start("2026-05-01T18:00:00Z", 100, "Grayson Rodriguez", innings="6.0", earned_runs=1),
            _start("2026-05-10T18:00:00Z", 100, "Grayson Rodriguez", innings="6.0", earned_runs=6),  # future
        ]
        _write_snapshots(path, rows)
        starter_history._STARTER_INDEX_CACHE.clear()

        # Decision is BEFORE the second (bad) start -- only the first counts,
        # so there's only 1 prior start, below MINIMUM_PRIOR_STARTS=2.
        result = starter_history.starter_rolling_era(
            "Grayson Rodriguez", datetime(2026, 5, 5, tzinfo=UTC), snapshot_path=path
        )

        assert result["status"] == "insufficient_sample"

    def test_insufficient_prior_starts_reports_insufficient_sample(self, tmp_path) -> None:
        path = tmp_path / "snapshots.jsonl"
        rows = [_start("2026-05-01T18:00:00Z", 100, "Rookie Pitcher")]
        _write_snapshots(path, rows)
        starter_history._STARTER_INDEX_CACHE.clear()

        result = starter_history.starter_rolling_era(
            "Rookie Pitcher", datetime(2026, 5, 10, tzinfo=UTC), snapshot_path=path
        )

        assert result["status"] == "insufficient_sample"

    def test_unknown_starter_reports_unavailable_from_source(self, tmp_path) -> None:
        path = tmp_path / "snapshots.jsonl"
        _write_snapshots(path, [_start("2026-05-01T18:00:00Z", 100, "Someone Else")])
        starter_history._STARTER_INDEX_CACHE.clear()

        result = starter_history.starter_rolling_era(
            "Never Pitched Before", datetime(2026, 5, 10, tzinfo=UTC), snapshot_path=path
        )

        assert result["status"] == "unavailable_from_source"

    def test_scheduled_not_yet_played_snapshot_has_no_pitching_stats_and_is_skipped(self, tmp_path) -> None:
        """Real bug found 2026-08-04 while building this: the historical
        mlb_statsapi snapshot dump includes "Scheduled" (not yet played)
        games with an empty pitching dict for every player. Confirmed a real
        row has this shape live. Must not be treated as a real 0-ER start."""
        path = tmp_path / "snapshots.jsonl"
        rows = [
            {
                "game_start_utc": "2026-09-04T18:10:00Z",
                "status": "Scheduled",
                "home": {
                    "team_name": "Team A",
                    "pitcher_order": [100],
                    "players": [{"player_id": 100, "name": "Grayson Rodriguez", "pitching": {}}],
                },
                "away": {"team_name": "Team B", "pitcher_order": [], "players": []},
            }
        ]
        _write_snapshots(path, rows)
        starter_history._STARTER_INDEX_CACHE.clear()

        result = starter_history.starter_rolling_era(
            "Grayson Rodriguez", datetime(2026, 9, 10, tzinfo=UTC), snapshot_path=path
        )

        assert result["status"] == "unavailable_from_source"


class TestStarterEraGapLive:
    def test_returns_home_minus_away_era_when_both_available(self, tmp_path) -> None:
        path = tmp_path / "snapshots.jsonl"
        rows = [
            _start(f"2026-05-{d:02d}T18:00:00Z", 100, "Home Ace", innings="6.0", earned_runs=1, side="home")
            for d in range(1, 4)
        ] + [
            _start(f"2026-05-{d:02d}T18:00:00Z", 200, "Away Ace", innings="6.0", earned_runs=4, side="away")
            for d in range(1, 4)
        ]
        _write_snapshots(path, rows)
        starter_history._STARTER_INDEX_CACHE.clear()

        gap = starter_history.starter_era_gap_live(
            "Home Ace", "Away Ace", datetime(2026, 5, 20, tzinfo=UTC), snapshot_path=path
        )

        # home ERA = 9*1/6 = 1.5, away ERA = 9*4/6 = 6.0 -> gap = 1.5 - 6.0 = -4.5
        assert gap == pytest.approx(-4.5)

    def test_raises_when_either_starter_has_insufficient_history(self, tmp_path) -> None:
        path = tmp_path / "snapshots.jsonl"
        _write_snapshots(
            path,
            [
                _start(f"2026-05-{d:02d}T18:00:00Z", 100, "Home Ace", side="home")
                for d in range(1, 4)
            ],
        )
        starter_history._STARTER_INDEX_CACHE.clear()

        with pytest.raises(ValueError, match="NO_CALL_STARTER_ERA_GAP_INSUFFICIENT_HISTORY"):
            starter_history.starter_era_gap_live(
                "Home Ace", "Never Started", datetime(2026, 5, 20, tzinfo=UTC), snapshot_path=path
            )
