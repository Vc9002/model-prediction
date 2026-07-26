from __future__ import annotations

import json
from datetime import UTC, datetime

from model_prediction.soccer_forward import build_soccer_total_slate


def test_soccer_forward_prices_draw_aware_full_game_total_from_exact_bbo(tmp_path) -> None:
    history_path = tmp_path / "processed" / "soccer" / "games.jsonl"
    history_path.parent.mkdir(parents=True)
    history = []
    for index in range(60):
        history.append(
            {
                "event_id": f"history-{index}",
                "event_start_utc": f"2026-05-{index % 28 + 1:02d}T12:00:00Z",
                "league": "SOCCER",
                "away_team": "Alpha FC" if index % 2 else "Beta FC",
                "home_team": "Beta FC" if index % 2 else "Alpha FC",
                "away_score": index % 3,
                "home_score": (index + 1) % 4,
            }
        )
    history_path.write_text(
        "".join(json.dumps(row) + "\n" for row in history),
        encoding="utf-8",
    )
    snapshot_path = (
        tmp_path
        / "odds"
        / "soccer"
        / "2026-07-27"
        / "polymarket_snapshots.jsonl"
    )
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(
        json.dumps(
            {
                "event_id": "pm-1",
                "event_title": "Alpha FC vs Beta FC",
                "event_start_utc": "2026-07-27T20:00:00Z",
                "observed_at_utc": "2026-07-27T12:00:00Z",
                "timestamp_valid": True,
                "market_type": "total",
                "line": 2.5,
                "market_slug": "tsc-mls-alpha-beta-2026-07-27-2pt5",
                "long": {"description": "Over", "ask": 0.55},
                "short": {"description": "Under", "ask": 0.46},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    class Client:
        @staticmethod
        def scoreboard(league, game_date):
            assert league == "MLS"
            return {
                "events": [
                    {
                        "id": "espn-1",
                        "date": "2026-07-27T20:00:00Z",
                        "competitions": [
                            {
                                "competitors": [
                                    {
                                        "homeAway": "away",
                                        "team": {"displayName": "Alpha FC"},
                                    },
                                    {
                                        "homeAway": "home",
                                        "team": {"displayName": "Beta FC"},
                                    },
                                ]
                            }
                        ],
                    }
                ]
            }

    result = build_soccer_total_slate(
        data_root=tmp_path,
        game_date="2026-07-27",
        client=Client(),
        leagues=("MLS",),
        observed_at=datetime(2026, 7, 27, 13, tzinfo=UTC),
    )

    assert result["model_version"] == "soccer-poisson-dc-v1"
    assert result["scheduled_games"] == 1
    assert result["priced_count"] == 1
    contract = result["priced_contracts"][0]
    assert contract["market_type"] == "total"
    assert contract["line"] == 2.5
    assert contract["selection"] in {"over", "under"}
    assert contract["timestamp_valid"] is True


def test_soccer_forward_rejects_partial_or_timestamp_invalid_totals(tmp_path) -> None:
    snapshot_path = (
        tmp_path
        / "odds"
        / "soccer"
        / "2026-07-27"
        / "polymarket_snapshots.jsonl"
    )
    snapshot_path.parent.mkdir(parents=True)
    rows = [
        {
            "event_title": "Alpha FC vs Beta FC",
            "event_start_utc": "2026-07-27T20:00:00Z",
            "observed_at_utc": "2026-07-27T12:00:00Z",
            "timestamp_valid": True,
            "market_type": "total",
            "line": 2.5,
            "market_slug": "tsc-mls-alpha-beta-fh-2pt5",
        },
        {
            "event_title": "Alpha FC vs Beta FC",
            "event_start_utc": "2026-07-27T20:00:00Z",
            "observed_at_utc": "2026-07-27T12:00:00Z",
            "timestamp_valid": False,
            "market_type": "total",
            "line": 2.5,
            "market_slug": "tsc-mls-alpha-beta-2pt5",
        },
    ]
    snapshot_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    from model_prediction.soccer_forward import _latest_total_snapshots

    assert _latest_total_snapshots(tmp_path, "2026-07-27") == []
