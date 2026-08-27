"""Tests for point-in-time reliever talent x availability feature engine."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from model_prediction.features.reliever_availability import (
    calculate_reliever_availability,
    get_team_bullpen_state,
    reliever_availability_matchup_gaps,
)


def _write_snapshots(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_calculate_reliever_availability() -> None:
    # 0 days rest + heavy pitch count -> severe penalty
    assert calculate_reliever_availability(40, 0, 0, 1) <= 0.15
    # Back to back appearances with high workload -> very low availability
    assert calculate_reliever_availability(20, 20, 0, 2) <= 0.20
    # 3 consecutive days -> virtually zero
    assert calculate_reliever_availability(10, 10, 10, 3) <= 0.10
    # Fully fresh (0 pitches in trailing 3 days) -> 1.0
    assert calculate_reliever_availability(0, 0, 0, 0) == 1.0


def test_team_bullpen_state_and_matchup(tmp_path: Path) -> None:
    snapshot_path = tmp_path / "game_snapshots.jsonl"
    sample_games = [
        {
            "game_date": "2026-05-30T19:00:00Z",
            "home_team": "New York Yankees",
            "away_team": "Boston Red Sox",
            "home_pitchers": [
                {
                    "id": 100,
                    "name": "Starter A",
                    "ip": "6.0",
                    "pitches": 90,
                    "k": 7,
                    "bb": 1,
                    "hr": 0,
                    "bf": 24,
                },
                {
                    "id": 101,
                    "name": "Reliever X",
                    "ip": "1.0",
                    "pitches": 14,
                    "k": 2,
                    "bb": 0,
                    "hr": 0,
                    "bf": 3,
                },
                {
                    "id": 102,
                    "name": "Closer Y",
                    "ip": "1.0",
                    "pitches": 16,
                    "k": 1,
                    "bb": 0,
                    "hr": 0,
                    "bf": 4,
                },
            ],
            "away_pitchers": [
                {
                    "id": 200,
                    "name": "Starter B",
                    "ip": "4.0",
                    "pitches": 80,
                    "k": 3,
                    "bb": 3,
                    "hr": 1,
                    "bf": 19,
                },
                {
                    "id": 201,
                    "name": "Reliever Z",
                    "ip": "2.0",
                    "pitches": 38,
                    "k": 1,
                    "bb": 2,
                    "hr": 1,
                    "bf": 10,
                },
            ],
        }
    ]
    _write_snapshots(snapshot_path, sample_games)

    as_of = datetime(2026, 6, 1, 18, 0, tzinfo=UTC)
    state = get_team_bullpen_state("New York Yankees", as_of, snapshot_path=snapshot_path)
    assert state.effective_bullpen_fip > 0.0
    assert state.overall_freshness_score > 0.0

    gaps = reliever_availability_matchup_gaps(
        "New York Yankees", "Boston Red Sox", as_of, snapshot_path=snapshot_path
    )
    assert "bullpen_fip_advantage" in gaps
    assert "bullpen_freshness_advantage" in gaps
    assert "bullpen_hl_advantage" in gaps
