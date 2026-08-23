from __future__ import annotations

import json
import os
import time
from pathlib import Path

from model_prediction.features.base import FeatureStore


def _write_raw_scoreboard(root: Path, sport: str, game_date: str, league: str, events: list[dict]) -> None:
    raw_dir = root / "raw" / sport / game_date
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"scores_{league}.json").write_text(json.dumps({"events": events}), encoding="utf-8")


def _event(event_id: str, season_type: str = "regular-season", season_year: int = 2026) -> dict:
    return {
        "id": event_id,
        "competitions": [{"type": {"abbreviation": "STD"}}],
        "season": {"slug": season_type, "year": season_year},
    }


def test_event_metadata_disk_cache_matches_uncached_scan(tmp_path: Path) -> None:
    _write_raw_scoreboard(tmp_path, "mlb", "2026-04-01", "mlb", [_event("1"), _event("2")])

    fresh = FeatureStore(tmp_path)._event_metadata("mlb")
    assert fresh == {
        "1": {"season_type": "regular-season", "season_year": 2026, "competition_type": "STD"},
        "2": {"season_type": "regular-season", "season_year": 2026, "competition_type": "STD"},
    }

    cache_file = tmp_path / "raw" / "mlb_event_metadata.cache"
    assert cache_file.exists()

    # A brand-new FeatureStore has no in-memory cache -- this must be served
    # from the disk cache and still match exactly.
    from_disk_cache = FeatureStore(tmp_path)._event_metadata("mlb")
    assert from_disk_cache == fresh


def test_event_metadata_disk_cache_invalidates_when_new_raw_date_added(tmp_path: Path) -> None:
    _write_raw_scoreboard(tmp_path, "mlb", "2026-04-01", "mlb", [_event("1")])
    first = FeatureStore(tmp_path)._event_metadata("mlb")
    assert set(first) == {"1"}

    # Force a distinguishable mtime step (some filesystems have 1s mtime
    # resolution) before adding the new raw date directory.
    time.sleep(1.05)
    _write_raw_scoreboard(tmp_path, "mlb", "2026-04-02", "mlb", [_event("2")])
    os.utime(tmp_path / "raw" / "mlb", None)  # belt-and-suspenders mtime bump

    second = FeatureStore(tmp_path)._event_metadata("mlb")
    assert set(second) == {"1", "2"}


def test_load_games_unaffected_by_stale_event_metadata_for_current_rows(tmp_path: Path) -> None:
    # Newer processed rows carry season_type directly and don't depend on
    # _event_metadata at all -- only pre-migration rows fall back to it.
    processed_dir = tmp_path / "processed" / "mlb"
    processed_dir.mkdir(parents=True)
    row = {
        "event_id": "1",
        "event_start_utc": "2026-05-01T18:00:00Z",
        "league": "MLB",
        "away_team": "A",
        "home_team": "B",
        "away_score": 3,
        "home_score": 4,
        "season_type": "regular-season",
    }
    (processed_dir / "games.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")

    games = FeatureStore(tmp_path).load_games("mlb")
    assert len(games) == 1
    assert games[0].season_type == "regular-season"
