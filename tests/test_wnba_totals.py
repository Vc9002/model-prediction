"""Tests for WNBA Total model full pipeline wiring into Flat and Main ledgers."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock

from model_prediction.cli import forecast as cli_forecast
from model_prediction.cli.forecast import _forecast_wnba_total_slate
from model_prediction.domain import (
    MarketType,
    PickResult,
    utc_now,
)
from model_prediction.pricing import grade_pick


def test_wnba_total_grade_pick() -> None:
    # Over 165.5: 85 + 82 = 167 > 165.5 => WIN
    assert grade_pick(MarketType.TOTAL, "over", 165.5, 82, 85) == PickResult.WIN
    # Over 165.5: 80 + 82 = 162 < 165.5 => LOSS
    assert grade_pick(MarketType.TOTAL, "over", 165.5, 82, 80) == PickResult.LOSS
    # Under 165.5: 80 + 82 = 162 < 165.5 => WIN
    assert grade_pick(MarketType.TOTAL, "under", 165.5, 82, 80) == PickResult.WIN
    # Under 165.5: 85 + 82 = 167 > 165.5 => LOSS
    assert grade_pick(MarketType.TOTAL, "under", 165.5, 82, 85) == PickResult.LOSS


def test_forecast_wnba_total_fails_closed_without_exact_artifact(monkeypatch, tmp_path: Path) -> None:
    # The fail-closed path is exercised directly: the loader is forced to
    # report a missing artifact, so the test doesn't depend on which file
    # happens to be checked in today.
    monkeypatch.setattr(
        cli_forecast,
        "_load_exact_artifact_contract",
        lambda model_version: (None, f"exact serving artifact missing for {model_version}"),
    )
    data_root = tmp_path / "data"
    future_dt = utc_now() + timedelta(days=2)
    args_date = future_dt.date().isoformat()
    future_str = future_dt.isoformat().replace("+00:00", "Z")

    odds_dir = data_root / "odds" / "wnba" / args_date
    odds_dir.mkdir(parents=True, exist_ok=True)

    snapshot_file = odds_dir / "polymarket_snapshots.jsonl"
    snapshot_row = {
        "event_id": "poly-wnba-1",
        "market_slug": "wnba-total-score-over-under-165-5",
        "team": "New York Liberty",
        "market_type": "total",
        "line": 165.5,
        "event_start_utc": future_str,
        "long": {"ask": 0.52},
        "short": {"ask": 0.48},
    }
    snapshot_file.write_text(json.dumps(snapshot_row) + "\n", encoding="utf-8")

    # Mock ESPN client
    mock_client = MagicMock()
    mock_client.scoreboard.return_value = {
        "events": [
            {
                "id": "401500001",
                "date": future_str,
                "competitions": [
                    {
                        "competitors": [
                            {
                                "homeAway": "home",
                                "team": {"displayName": "New York Liberty", "abbreviation": "NY"},
                            },
                            {
                                "homeAway": "away",
                                "team": {"displayName": "Las Vegas Aces", "abbreviation": "LV"},
                            },
                        ]
                    }
                ],
            }
        ]
    }

    # Historical games
    hist_dir = data_root / "historical"
    hist_dir.mkdir(parents=True, exist_ok=True)
    hist_file = hist_dir / "wnba_games_all.jsonl"
    sample_game = {
        "event_id": "past_1",
        "date": "2026-06-01",
        "home_team": "New York Liberty",
        "away_team": "Las Vegas Aces",
        "home_score": 88,
        "away_score": 84,
        "event_start_utc": "2026-06-01T23:00:00Z",
    }
    hist_file.write_text(json.dumps(sample_game) + "\n", encoding="utf-8")

    slate = _forecast_wnba_total_slate(data_root, args_date, mock_client)
    assert slate["sport"] == "wnba_total"
    assert slate["status"] == "blocked"
    assert slate["model_version"] == "wnba-total-margin-v1"
    assert slate["priced_contracts"] == []
    assert "exact serving artifact" in slate["reason"]
    mock_client.scoreboard.assert_not_called()


def test_wnba_total_artifact_resolves_under_slate_model_version() -> None:
    """Regression: the checked-in artifact must resolve under the slate's
    exact model_version (wnba-total-margin-v1) with a self-verifying hash.

    The artifact file, its embedded model_version, and the slate's
    model_version drifted apart once before, blocking the WNBA total step
    at artifact resolution with scheduled_games=0.
    """
    artifact, error = cli_forecast._load_exact_artifact_contract("wnba-total-margin-v1")
    assert error is None
    assert artifact is not None
    assert artifact["model_version"] == "wnba-total-margin-v1"
