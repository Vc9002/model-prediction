from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from model_prediction.tennis_forward import build_tennis_slate


def _write_history(tmp_path, count=60):
    history_path = tmp_path / "processed" / "tennis" / "games.jsonl"
    history_path.parent.mkdir(parents=True)
    rows = []
    for index in range(count):
        rows.append(
            {
                "event_id": f"history-{index}",
                "event_start_utc": f"2026-05-{index % 28 + 1:02d}T12:00:00Z",
                "league": "WTA",
                "winner": "Alpha Player" if index % 2 else "Beta Player",
                "loser": "Beta Player" if index % 2 else "Alpha Player",
                "surface": "Hard",
                "match_date": f"2026-05-{index % 28 + 1:02d}T12:00:00Z",
            }
        )
    history_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_snapshot(tmp_path, **overrides):
    snapshot_path = tmp_path / "odds" / "tennis" / "2026-07-27" / "polymarket_snapshots.jsonl"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "event_start_utc": "2026-07-27T20:00:00Z",
        "observed_at_utc": "2026-07-27T12:00:00Z",
        "timestamp_valid": True,
        "market_type": "moneyline",
        "league": "WTA",
        "market_slug": "wta-alpha-beta-2026-07-27",
        "long": {"description": "Alpha Player", "ask": 0.42},
        "short": {"description": "Beta Player", "ask": 0.6},
    }
    row.update(overrides)
    with snapshot_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def _write_atp_history(tmp_path, count=60):
    history_path = tmp_path / "processed" / "tennis" / "games.jsonl"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for index in range(count):
        rows.append(
            {
                "event_id": f"atp-history-{index}",
                "event_start_utc": f"2026-05-{index % 28 + 1:02d}T12:00:00Z",
                "league": "ATP",
                "winner": "Men Player One" if index % 2 else "Men Player Two",
                "loser": "Men Player Two" if index % 2 else "Men Player One",
                "surface": "Hard",
                "match_date": f"2026-05-{index % 28 + 1:02d}T12:00:00Z",
            }
        )
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write("".join(json.dumps(row) + "\n" for row in rows))


class Client:
    @staticmethod
    def scoreboard(league, game_date):
        assert league in ("WTA", "ATP")
        if league == "ATP":
            return {"events": []}
        return {
            "events": [
                {
                    "id": "espn-1",
                    "date": "2026-07-27T20:00:00Z",
                    "name": "Test Open",
                    "groupings": [
                        {
                            "grouping": {"displayName": "Women's Singles"},
                            "competitions": [
                                {
                                    "id": "c1",
                                    "date": "2026-07-27T20:00:00Z",
                                    "type": {"slug": "womens-singles"},
                                    "status": {"type": {"completed": False}},
                                    "competitors": [
                                        {
                                            "homeAway": "away",
                                            "athlete": {"displayName": "Alpha Player"},
                                        },
                                        {
                                            "homeAway": "home",
                                            "athlete": {"displayName": "Beta Player"},
                                        },
                                    ],
                                }
                            ],
                        },
                        {
                            "grouping": {"displayName": "Women's Doubles"},
                            "competitions": [
                                {
                                    "id": "c2",
                                    "date": "2026-07-27T20:00:00Z",
                                    "type": {"slug": "womens-doubles"},
                                    "status": {"type": {"completed": False}},
                                    "competitors": [
                                        {
                                            "homeAway": "away",
                                            "roster": {
                                                "athletes": [
                                                    {"displayName": "Gamma Player"},
                                                    {"displayName": "Delta Player"},
                                                ]
                                            },
                                        },
                                        {
                                            "homeAway": "home",
                                            "roster": {
                                                "athletes": [
                                                    {"displayName": "Epsilon Player"},
                                                    {"displayName": "Zeta Player"},
                                                ]
                                            },
                                        },
                                    ],
                                }
                            ],
                        },
                    ],
                }
            ]
        }


class _CombinedTournamentClient(Client):
    """Combined ATP+WTA tournaments return the SAME event -- with BOTH the
    Men's and Women's Singles groupings -- from BOTH the ATP and WTA
    site-API paths (verified live 2026-07-27). Tour is derived per match
    from the competition's own type.slug, so the Men's Singles match must
    surface as an ATP match, and calling both endpoints for the same event
    must not double-count either match."""

    @staticmethod
    def scoreboard(league, game_date):
        payload = Client.scoreboard("WTA", game_date)
        payload["events"][0]["groupings"].append(
            {
                "grouping": {"displayName": "Men's Singles"},
                "competitions": [
                    {
                        "id": "c3",
                        "date": "2026-07-27T20:00:00Z",
                        "type": {"slug": "mens-singles"},
                        "status": {"type": {"completed": False}},
                        "competitors": [
                            {"homeAway": "away", "athlete": {"displayName": "Men Player One"}},
                            {"homeAway": "home", "athlete": {"displayName": "Men Player Two"}},
                        ],
                    }
                ],
            }
        )
        return payload


def test_tennis_forward_prices_both_tours_and_dedupes_the_combined_event(tmp_path) -> None:
    _write_history(tmp_path)
    _write_atp_history(tmp_path)
    _write_snapshot(tmp_path)
    _write_snapshot(
        tmp_path,
        league="ATP",
        market_slug="atp-menone-mentwo-2026-07-27",
        long={"description": "Men Player One", "ask": 0.55},
        short={"description": "Men Player Two", "ask": 0.47},
    )

    result = build_tennis_slate(
        data_root=tmp_path,
        game_date="2026-07-27",
        client=_CombinedTournamentClient(),
        observed_at=datetime(2026, 7, 27, 13, tzinfo=UTC),
    )

    # One WTA match (c1) + one ATP match (c3), each counted once despite
    # both the ATP and WTA endpoint calls returning the identical event.
    assert result["scheduled_games"] == 2
    assert result["priced_count"] == 2
    event_ids = {c["event_id"] for c in result["priced_contracts"]}
    assert len(event_ids) == 2
    atp_contract = next(c for c in result["priced_contracts"] if "Men Player" in c["away_team"])
    assert {atp_contract["away_team"], atp_contract["home_team"]} == {
        "Men Player One",
        "Men Player Two",
    }


def test_tennis_forward_prices_singles_moneyline_and_excludes_doubles(tmp_path) -> None:
    _write_history(tmp_path)
    _write_snapshot(tmp_path)

    result = build_tennis_slate(
        data_root=tmp_path,
        game_date="2026-07-27",
        client=Client(),
        observed_at=datetime(2026, 7, 27, 13, tzinfo=UTC),
    )

    assert result["model_version"] == "tennis-surface-elo-v1"
    assert result["scheduled_games"] == 1
    assert result["priced_count"] == 1
    contract = result["priced_contracts"][0]
    assert contract["market_type"] == "moneyline"
    assert contract["line"] is None
    assert contract["away_team"] == "Alpha Player"
    assert contract["home_team"] == "Beta Player"
    assert contract["selection"] in {"away", "home"}
    expected_ask = 0.42 if contract["selection"] == "away" else 0.6
    assert contract["executable_ask"] == expected_ask
    snapshot_path = tmp_path / "odds" / "tennis" / "2026-07-27" / "polymarket_snapshots.jsonl"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    expected_snapshot_hash = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert contract["market_snapshot_hash"] == expected_snapshot_hash
    assert contract["market_snapshot_archive_path"] == str(snapshot_path.resolve())
    assert contract["market_snapshot_record_id"] == expected_snapshot_hash


def test_tennis_forward_reports_no_op_when_no_moneyline_snapshot_exists(tmp_path) -> None:
    _write_history(tmp_path)

    result = build_tennis_slate(
        data_root=tmp_path,
        game_date="2026-07-27",
        client=Client(),
        observed_at=datetime(2026, 7, 27, 13, tzinfo=UTC),
    )

    assert result["scheduled_games"] == 1
    assert result["priced_count"] == 0
    assert len(result["unmatched"]) == 1


class _ClayTournamentClient(Client):
    @staticmethod
    def scoreboard(league, game_date):
        if league == "ATP":
            return {"events": []}
        payload = Client.scoreboard("WTA", game_date)
        payload["events"][0]["name"] = "Roland Garros"
        return payload


def test_tennis_forward_infers_surface_from_tournament_name_for_live_matches(tmp_path) -> None:
    """A live/upcoming match must not always default to Hard -- surface
    should be inferred from the tournament name the same way the historical
    Elo-build path already does (data_sources/espn.py::_infer_tennis_surface),
    otherwise surface-blending is inert at forecast time."""
    _write_history(tmp_path)
    _write_snapshot(tmp_path)

    result = build_tennis_slate(
        data_root=tmp_path,
        game_date="2026-07-27",
        client=_ClayTournamentClient(),
        observed_at=datetime(2026, 7, 27, 13, tzinfo=UTC),
    )

    assert result["priced_count"] == 1
    assert result["priced_contracts"][0]["feature_basis"]["surface"] == "Clay"
