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


def test_soccer_forward_prices_moneyline_matching_side_by_team_name(tmp_path) -> None:
    """Polymarket prices soccer full-time result as three SEPARATE Yes/No
    team_win markets per event (home wins / draw / away wins), not one
    combined moneyline market -- verified live 2026-07-27 against the raw
    gateway payload. Verify matching finds the correct one of the (up to
    two) per-team markets for the model-favored team, by the snapshot's
    `team` field, not by market ordering."""
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
    rows = [
        {
            "event_id": "pm-1",
            "event_title": "Alpha FC vs Beta FC",
            "event_start_utc": "2026-07-27T20:00:00Z",
            "observed_at_utc": "2026-07-27T12:00:00Z",
            "timestamp_valid": True,
            "market_type": "team_win",
            "team": "Alpha FC",
            "line": None,
            "market_slug": "atc-mls-alpha-beta-2026-07-27-alpha",
            "long": {"description": "Yes", "ask": 0.4},
            "short": {"description": "No", "ask": 0.62},
        },
        {
            "event_id": "pm-1",
            "event_title": "Alpha FC vs Beta FC",
            "event_start_utc": "2026-07-27T20:00:00Z",
            "observed_at_utc": "2026-07-27T12:00:00Z",
            "timestamp_valid": True,
            "market_type": "team_win",
            "team": "Beta FC",
            "line": None,
            "market_slug": "atc-mls-alpha-beta-2026-07-27-beta",
            "long": {"description": "Yes", "ask": 0.58},
            "short": {"description": "No", "ask": 0.44},
        },
        {
            "event_id": "pm-1",
            "event_title": "Alpha FC vs Beta FC",
            "event_start_utc": "2026-07-27T20:00:00Z",
            "observed_at_utc": "2026-07-27T12:00:00Z",
            "timestamp_valid": True,
            "market_type": "team_win",
            "team": None,
            "line": None,
            "market_slug": "atc-mls-alpha-beta-2026-07-27-draw",
            "long": {"description": "Yes", "ask": 0.25},
            "short": {"description": "No", "ask": 0.78},
        },
    ]
    snapshot_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    class Client:
        @staticmethod
        def scoreboard(league, game_date):
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

    moneyline_contracts = [
        c for c in result["priced_contracts"] if c["market_type"] == "moneyline"
    ]
    assert len(moneyline_contracts) == 1
    contract = moneyline_contracts[0]
    assert contract["line"] is None
    assert contract["selection"] in {"home", "away"}
    # Whichever side the model favors, the priced ask must come from the
    # snapshot side matching that team's name, not a fixed slot.
    expected_ask = 0.58 if contract["selection"] == "home" else 0.4
    assert contract["executable_ask"] == expected_ask


def _lopsided_history(tmp_path) -> None:
    """40 games where Strong FC beats Weak FC 2-1 at home every time --
    strongly favors Strong FC (~81% home win) without being a near-certainty,
    so a market can plausibly price either side at negative or positive edge.
    """
    history_path = tmp_path / "processed" / "soccer" / "games.jsonl"
    history_path.parent.mkdir(parents=True)
    history = [
        {
            "event_id": f"history-{index}",
            "event_start_utc": f"2026-05-{index % 28 + 1:02d}T12:00:00Z",
            "league": "SOCCER",
            "away_team": "Weak FC",
            "home_team": "Strong FC",
            "away_score": 1,
            "home_score": 2,
        }
        for index in range(40)
    ]
    history_path.write_text(
        "".join(json.dumps(row) + "\n" for row in history), encoding="utf-8"
    )


class _StrongWeakClient:
    @staticmethod
    def scoreboard(league, game_date):
        return {
            "events": [
                {
                    "id": "espn-1",
                    "date": "2026-07-27T20:00:00Z",
                    "competitions": [
                        {
                            "competitors": [
                                {"homeAway": "away", "team": {"displayName": "Weak FC"}},
                                {"homeAway": "home", "team": {"displayName": "Strong FC"}},
                            ]
                        }
                    ],
                }
            ]
        }


def test_soccer_forward_totals_picks_the_positive_edge_side_not_the_model_favorite(
    tmp_path,
) -> None:
    """The model favors "over" here, but the market prices "over" rich
    (negative edge) and "under" cheap (positive edge). Picking by raw model
    probability alone (the pre-fix behavior) would log the losing-value
    "over" side and never even look at "under"'s real edge."""
    _lopsided_history(tmp_path)
    snapshot_path = tmp_path / "odds" / "soccer" / "2026-07-27" / "polymarket_snapshots.jsonl"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(
        json.dumps(
            {
                "event_id": "pm-1",
                "event_title": "Weak FC vs Strong FC",
                "event_start_utc": "2026-07-27T20:00:00Z",
                "observed_at_utc": "2026-07-27T12:00:00Z",
                "timestamp_valid": True,
                "market_type": "total",
                "line": 2.5,
                "market_slug": "tsc-mls-weak-strong-2026-07-27-2pt5",
                "long": {"description": "Over", "ask": 0.75},
                "short": {"description": "Under", "ask": 0.28},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = build_soccer_total_slate(
        data_root=tmp_path,
        game_date="2026-07-27",
        client=_StrongWeakClient(),
        leagues=("MLS",),
        observed_at=datetime(2026, 7, 27, 13, tzinfo=UTC),
    )

    totals_contracts = [c for c in result["priced_contracts"] if c["market_type"] == "total"]
    assert len(totals_contracts) == 1
    contract = totals_contracts[0]
    assert contract["selection"] == "under"
    assert contract["executable_ask"] == 0.28
    assert contract["edge_vs_executable_ask"] > 0


def test_soccer_forward_moneyline_picks_the_positive_edge_team_not_the_model_favorite(
    tmp_path,
) -> None:
    """The model heavily favors Strong FC to win outright, but Strong FC's
    own team_win market is priced rich (negative edge) while Weak FC's is
    priced cheap (positive edge) -- real live pattern confirmed 2026-07-30
    (Newell's Old Boys @ Independiente). Picking by raw model probability
    alone would log Strong FC's losing-value bet and miss Weak FC's edge."""
    _lopsided_history(tmp_path)
    snapshot_path = tmp_path / "odds" / "soccer" / "2026-07-27" / "polymarket_snapshots.jsonl"
    snapshot_path.parent.mkdir(parents=True)
    rows = [
        {
            "event_id": "pm-1",
            "event_title": "Weak FC vs Strong FC",
            "event_start_utc": "2026-07-27T20:00:00Z",
            "observed_at_utc": "2026-07-27T12:00:00Z",
            "timestamp_valid": True,
            "market_type": "team_win",
            "team": "Strong FC",
            "line": None,
            "market_slug": "atc-mls-weak-strong-2026-07-27-strong",
            "long": {"description": "Yes", "ask": 0.90},
            "short": {"description": "No", "ask": 0.12},
        },
        {
            "event_id": "pm-1",
            "event_title": "Weak FC vs Strong FC",
            "event_start_utc": "2026-07-27T20:00:00Z",
            "observed_at_utc": "2026-07-27T12:00:00Z",
            "timestamp_valid": True,
            "market_type": "team_win",
            "team": "Weak FC",
            "line": None,
            "market_slug": "atc-mls-weak-strong-2026-07-27-weak",
            "long": {"description": "Yes", "ask": 0.03},
            "short": {"description": "No", "ask": 0.98},
        },
    ]
    snapshot_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    result = build_soccer_total_slate(
        data_root=tmp_path,
        game_date="2026-07-27",
        client=_StrongWeakClient(),
        leagues=("MLS",),
        observed_at=datetime(2026, 7, 27, 13, tzinfo=UTC),
    )

    moneyline_contracts = [c for c in result["priced_contracts"] if c["market_type"] == "moneyline"]
    assert len(moneyline_contracts) == 1
    contract = moneyline_contracts[0]
    assert contract["selection"] == "away"  # Weak FC
    assert contract["executable_ask"] == 0.03
    assert contract["edge_vs_executable_ask"] > 0


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
