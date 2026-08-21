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
    snapshot_path = tmp_path / "odds" / "soccer" / "2026-07-27" / "polymarket_snapshots.jsonl"
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


def test_soccer_forward_dedupes_same_event_returned_by_two_leagues(tmp_path) -> None:
    """Real bug found 2026-08-03: some competitions (e.g. a continental cup
    fixture also listed under a domestic league endpoint) return the SAME
    event_id from more than one configured league. Without deduping by
    event_id, that produced a real duplicate priced_contracts row (same
    event/market/selection, two separate pick_ids) for every affected match,
    which reached flat_picks.xlsx twice in production. Same fix shape as
    tennis_forward.py's combined ATP+WTA dedup."""
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

    same_event = {
        "id": "espn-shared-1",
        "date": "2026-07-27T20:00:00Z",
        "competitions": [
            {
                "competitors": [
                    {"homeAway": "away", "team": {"displayName": "Alpha FC"}},
                    {"homeAway": "home", "team": {"displayName": "Beta FC"}},
                ]
            }
        ],
    }

    class Client:
        @staticmethod
        def scoreboard(league, game_date):
            # Both leagues return the identical event_id -- exactly the
            # shared-fixture case this dedup guards against.
            return {"events": [same_event]}

    result = build_soccer_total_slate(
        data_root=tmp_path,
        game_date="2026-07-27",
        client=Client(),
        leagues=("MLS", "CLUB_FRIENDLIES"),
        observed_at=datetime(2026, 7, 27, 13, tzinfo=UTC),
    )

    assert result["scheduled_games"] == 1


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
    snapshot_path = tmp_path / "odds" / "soccer" / "2026-07-27" / "polymarket_snapshots.jsonl"
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

    moneyline_contracts = [c for c in result["priced_contracts"] if c["market_type"] == "moneyline"]
    assert len(moneyline_contracts) == 1
    contract = moneyline_contracts[0]
    assert contract["line"] is None
    assert contract["selection"] in {"home", "away"}
    # Whichever side the model favors, the priced ask must come from the
    # snapshot side matching that team's name, not a fixed slot.
    expected_ask = 0.58 if contract["selection"] == "home" else 0.4
    assert contract["executable_ask"] == expected_ask


def test_soccer_forward_rejects_partial_or_timestamp_invalid_totals(tmp_path) -> None:
    snapshot_path = tmp_path / "odds" / "soccer" / "2026-07-27" / "polymarket_snapshots.jsonl"
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


def test_soccer_forward_prices_btts_from_a_matched_bbo(tmp_path) -> None:
    """BTTS activates automatically once a real market_type=="btts" snapshot
    exists -- confirmed no such market has ever been observed live (see
    _latest_btts_snapshots's docstring), but the matching/pricing plumbing
    itself must work correctly for whenever one appears."""
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
    snapshot_path = tmp_path / "odds" / "soccer" / "2026-07-27" / "polymarket_snapshots.jsonl"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(
        json.dumps(
            {
                "event_id": "pm-1",
                "event_title": "Alpha FC vs Beta FC",
                "event_start_utc": "2026-07-27T20:00:00Z",
                "observed_at_utc": "2026-07-27T12:00:00Z",
                "timestamp_valid": True,
                "market_type": "btts",
                "market_slug": "btts-mls-alpha-beta-2026-07-27",
                "long": {"description": "Yes", "ask": 0.55},
                "short": {"description": "No", "ask": 0.46},
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

    btts_contracts = [c for c in result["priced_contracts"] if c["market_type"] == "btts"]
    assert len(btts_contracts) == 1
    contract = btts_contracts[0]
    assert contract["line"] is None
    assert contract["selection"] in {"yes", "no"}
    expected_ask = 0.55 if contract["selection"] == "yes" else 0.46
    assert contract["executable_ask"] == expected_ask
    assert contract["timestamp_valid"] is True


def test_team_matches_title_does_not_collide_same_city_derby_pairs() -> None:
    """Operator directive, 2026-07-31: stripping "city"/"united" as generic
    words collapsed same-city derby pairs onto the same "distinctive" token
    (e.g. both "Manchester United" and "Manchester City" -> {"manchester"}),
    a false match."""
    from model_prediction.soccer_forward import _team_matches_title

    assert _team_matches_title("Manchester United", "Manchester City") is False
    assert _team_matches_title("Manchester City", "Manchester United") is False
    # Corporate suffixes should still be strippable -- that's the real,
    # intended purpose of _GENERIC_TEAM_WORDS.
    assert _team_matches_title("Manchester United", "Manchester United FC") is True
    assert _team_matches_title("Manchester United FC", "Manchester United") is True
    # "AC"/"Inter" are too short to survive the word-length filter, so both
    # "AC Milan" and "Inter Milan" still fuzzy-match on the shared "Milan"
    # token at this raw function level -- that residual ambiguity is why the
    # real caller (build_soccer_total_slate's moneyline matching) also cross-
    # checks the opponent's name and refuses rather than guesses; see
    # test_moneyline_refuses_ambiguous_derby_match_against_opponent_snapshot.
    assert _team_matches_title("AC Milan", "Inter Milan") is True


def test_moneyline_refuses_ambiguous_derby_match_against_opponent_snapshot(tmp_path) -> None:
    """Operator directive, 2026-07-31: a same-city derby (here standing in
    for "AC Milan" vs "Inter Milan") must never have its pick priced against
    the OPPONENT's team_win snapshot, even though "AC Milan" still fuzzy-
    matches the title "Inter Milan" at the raw _team_matches_title level
    (the short "AC" prefix doesn't survive the word-length filter, leaving
    only the shared "Milan" token). This is the single-snapshot-present case
    (the opponent's snapshot missing/stale is a normal daily occurrence) --
    the len(matching) != 1 safety net alone can't catch it because there's
    only one candidate to begin with."""
    history_path = tmp_path / "processed" / "soccer" / "games.jsonl"
    history_path.parent.mkdir(parents=True)
    history = []
    for index in range(40):
        # AC Milan dominant, Inter Milan weak -- makes the model's favorite
        # deterministic so this test doesn't depend on Dixon-Coles internals.
        history.append(
            {
                "event_id": f"history-ac-{index}",
                "event_start_utc": f"2026-05-{index % 28 + 1:02d}T12:00:00Z",
                "league": "SOCCER",
                "away_team": "AC Milan" if index % 2 else "Some Other FC",
                "home_team": "Some Other FC" if index % 2 else "AC Milan",
                "away_score": 4 if index % 2 else 0,
                "home_score": 0 if index % 2 else 4,
            }
        )
        history.append(
            {
                "event_id": f"history-inter-{index}",
                "event_start_utc": f"2026-05-{index % 28 + 1:02d}T13:00:00Z",
                "league": "SOCCER",
                "away_team": "Inter Milan" if index % 2 else "Some Other FC",
                "home_team": "Some Other FC" if index % 2 else "Inter Milan",
                "away_score": 0 if index % 2 else 4,
                "home_score": 4 if index % 2 else 0,
            }
        )
    history_path.write_text(
        "".join(json.dumps(row) + "\n" for row in history),
        encoding="utf-8",
    )
    snapshot_path = tmp_path / "odds" / "soccer" / "2026-07-27" / "polymarket_snapshots.jsonl"
    snapshot_path.parent.mkdir(parents=True)
    # Only Inter Milan's team_win snapshot is present -- AC Milan's own
    # snapshot is missing (a stale/absent executable ask, a normal daily
    # occurrence), which is exactly the scenario that makes the false match
    # exploitable.
    snapshot_path.write_text(
        json.dumps(
            {
                "event_id": "pm-derby",
                "event_title": "AC Milan vs Inter Milan",
                "event_start_utc": "2026-07-27T20:00:00Z",
                "observed_at_utc": "2026-07-27T12:00:00Z",
                "timestamp_valid": True,
                "market_type": "team_win",
                "team": "Inter Milan",
                "market_slug": "tw-serie-a-inter-milan-2026-07-27",
                "long": {"description": "Yes", "ask": 0.60},
                "short": {"description": "No", "ask": 0.35},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    class Client:
        @staticmethod
        def scoreboard(league, game_date):
            return {
                "events": [
                    {
                        "id": "espn-derby",
                        "date": "2026-07-27T20:00:00Z",
                        "competitions": [
                            {
                                "competitors": [
                                    {"homeAway": "away", "team": {"displayName": "Inter Milan"}},
                                    {"homeAway": "home", "team": {"displayName": "AC Milan"}},
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
        leagues=("SERIE_A",),
        observed_at=datetime(2026, 7, 27, 13, tzinfo=UTC),
    )

    moneyline_contracts = [c for c in result["priced_contracts"] if c["market_type"] == "moneyline"]
    # AC Milan (the model's clear favorite here) must never get priced
    # against Inter Milan's snapshot -- either it's correctly unmatched, or
    # if priced, it must genuinely be Inter Milan's own pick.
    for contract in moneyline_contracts:
        if contract["selection"] == "home":
            assert contract["home_team"] == "AC Milan"
        assert not (
            contract["home_team"] == "AC Milan"
            and contract["market_slug"] == "tw-serie-a-inter-milan-2026-07-27"
        )
