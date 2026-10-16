"""Tests for ESPN NFL real-time player injury ingestion and normalization."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

from model_prediction.data_sources.espn_nfl_injuries import (
    capture_espn_nfl_injuries,
    normalize_espn_nfl_injuries,
)

SAMPLE_ESPN_NFL_PAYLOAD = {
    "injuries": [
        {
            "id": "22",
            "displayName": "Arizona Cardinals",
            "abbreviation": "ARI",
            "injuries": [
                {
                    "athlete": {
                        "id": "4870808",
                        "displayName": "Jeremiyah Love",
                        "position": {"abbreviation": "RB"},
                        "team": {
                            "id": "22",
                            "displayName": "Arizona Cardinals",
                            "abbreviation": "ARI",
                        },
                        "notes": {
                            "items": [
                                {
                                    "headline": "Love (ankle) remained sidelined at practice Thursday.",
                                    "text": "Detailed practice report text.",
                                }
                            ]
                        },
                    },
                    "type": {
                        "id": "2",
                        "name": "INJURY_STATUS_QUESTIONABLE",
                        "description": "questionable",
                        "abbreviation": "Q",
                    },
                    "status": {"id": "1", "name": "Active"},
                    "details": {
                        "type": "Ankle",
                        "detail": "Ankle sprain",
                        "returnDate": "2026-10-18",
                    },
                },
                {
                    "athlete": {
                        "displayName": "Wydett Williams Jr.",
                        "position": {"abbreviation": "S"},
                        "links": [
                            {
                                "rel": ["playercard"],
                                "href": "https://www.espn.com/nfl/player/_/id/5099542/wydett-williams-jr",
                            }
                        ],
                    },
                    "type": {
                        "id": "1",
                        "name": "INJURY_STATUS_OUT",
                        "description": "out",
                        "abbreviation": "O",
                    },
                    "status": "Out",
                    "details": {
                        "type": "Concussion",
                        "returnDate": "2026-10-25",
                    },
                },
            ],
        }
    ]
}


def test_normalize_espn_nfl_injuries_handles_direct_and_regex_ids() -> None:
    now = datetime(2026, 10, 16, 5, 0, 0, tzinfo=UTC)
    normalized = normalize_espn_nfl_injuries(SAMPLE_ESPN_NFL_PAYLOAD, observed_at=now)

    assert normalized["sport"] == "nfl"
    assert normalized["source"] == "espn_nfl_injuries"
    assert normalized["total_injuries"] == 2

    e0 = normalized["entries"][0]
    assert e0["player_name"] == "Jeremiyah Love"
    assert e0["athlete_id"] == "4870808"
    assert e0["position"] == "RB"
    assert e0["status"] == "Questionable"
    assert e0["status_abbreviation"] == "Q"
    assert e0["injury_type"] == "Ankle"
    assert e0["detail"] == "Ankle sprain"
    assert e0["team"] == "Arizona Cardinals"
    assert e0["team_abbreviation"] == "ARI"
    assert e0["return_date"] == "2026-10-18"
    assert "Love (ankle)" in e0["latest_headline"]

    e1 = normalized["entries"][1]
    assert e1["player_name"] == "Wydett Williams Jr."
    assert e1["athlete_id"] == "5099542"  # Extracted via regex link
    assert e1["position"] == "S"
    assert e1["status"] == "Out"
    assert e1["status_abbreviation"] == "O"
    assert e1["injury_type"] == "Concussion"
    assert e1["team"] == "Arizona Cardinals"
    assert e1["team_abbreviation"] == "ARI"


def test_capture_espn_nfl_injuries_writes_valid_snapshot(tmp_path: Path) -> None:
    now = datetime(2026, 10, 16, 5, 0, 0, tzinfo=UTC)
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.json.return_value = SAMPLE_ESPN_NFL_PAYLOAD
    mock_client.get.return_value = mock_response

    result = capture_espn_nfl_injuries(tmp_path, client=mock_client, observed_at=now)

    assert result["status"] == "ok"
    assert result["total_injuries"] == 2
    snapshot_path = Path(result["snapshot_path"])
    assert snapshot_path.exists()

    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert data["sport"] == "nfl"
    assert data["total_injuries"] == 2
    assert "snapshot_sha256" in data
