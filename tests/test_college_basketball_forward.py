import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from model_prediction.college_basketball_forward import (
    _event_team_names,
    _main_sample_eligible,
    _normalize_name,
    _resolve_ncaab_team,
    forecast_ncaab_sport,
)
from model_prediction.features.base import GameRecord
from model_prediction.main_ledgers import MultiSportPickLedger

# The served v6 model defines D1 as membership in its conference map (team_priors).
_D1_TEAMS = {name: 1500.0 for name in ("Away", "Home", "MajorAway", "MajorHome", "SmallCollege")}


def _registry(spread_id: str = "ncaab-spread-v6", moneyline_id: str | None = None):
    ids = {"moneyline": moneyline_id or spread_id, "spread": spread_id, "total": "ncaab-total-v6"}
    return lambda *a, **k: SimpleNamespace(
        champion=lambda sport, market: SimpleNamespace(available=True, model_id=ids[market])
    )


@pytest.fixture(autouse=True)
def _v6_spread_champion(monkeypatch):
    """These tests pin gate behaviour, not routing; route NCAAB to v6 as test_ncaab_main_totals does."""
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.ProductionModelRegistry.load", _registry()
    )


def _sides(market_type, away_team, away_desc, home_team, home_desc):
    return {
        "market_type": market_type,
        "sides": [
            {"selection": "away", "team": away_team, "description": away_desc},
            {"selection": "home", "team": home_team, "description": home_desc},
        ],
    }


def test_mascot_comes_from_moneyline_when_a_spread_market_is_listed_first():
    # Real 2026-11-21 Polymarket shape: 49 of 57 events listed a spread before the moneyline. Spread
    # descriptions are lines ("+15.50"), so taking the first team-bearing market dropped the mascot,
    # "Cornell" alone matched both "Cornell Big Red" and the non-D1 "Cornell (IA) Rams", and the D1
    # game was declined as NO_CALL_NON_D1.
    event = {
        "title": "Cornell vs. Morgan State",
        "markets": [
            _sides("spread", "Cornell", "+15.50", "Morgan State", "-15.50"),
            {
                "market_type": "total",
                "sides": [
                    {"selection": "over", "team": None, "description": "Over"},
                    {"selection": "under", "team": None, "description": "Under"},
                ],
            },
            _sides("moneyline", "Cornell", "Big Red", "Morgan State", "Bears"),
        ],
    }
    assert _event_team_names(event) == ("Cornell", "Morgan State", "Big Red", "Bears")

    names = {"Cornell Big Red", "Cornell (IA) Rams", "Morgan State Bears"}
    normalized = {_normalize_name(n): n for n in names}
    away, home, away_mascot, home_mascot = _event_team_names(event)
    assert _resolve_ncaab_team(away, away_mascot, names, normalized) == "Cornell Big Red"
    assert _resolve_ncaab_team(home, home_mascot, names, normalized) == "Morgan State Bears"


def test_polymarket_abbreviations_resolve_to_the_d1_map_names():
    # With the mascot found, "UCA"+"Bears" matched an old-name history record "UCA Bears" that is not in
    # the D1 map, so the game was still declined as non-D1 (2026-11-21 slate).
    names = {
        "UCA Bears",
        "Central Arkansas Bears",
        "UNCW Seahawks",
        "UNC Wilmington Seahawks",
        "NAU Lumberjacks",
        "Northern Arizona Lumberjacks",
    }
    normalized = {_normalize_name(n): n for n in names}
    for school, mascot, expected in (
        ("UCA", "Bears", "Central Arkansas Bears"),
        ("UNCW", "Seahawks", "UNC Wilmington Seahawks"),
        ("NAU", "Lumberjacks", "Northern Arizona Lumberjacks"),
    ):
        assert _resolve_ncaab_team(school, mascot, names, normalized) == expected


def test_event_team_names_falls_back_without_a_moneyline():
    spread_only = {"title": "A vs. B", "markets": [_sides("spread", "Alpha", "+3.50", "Beta", "-3.50")]}
    assert _event_team_names(spread_only) == ("Alpha", "Beta", "", "")
    descriptions_only = {"title": "A vs. B", "markets": [_sides("moneyline", None, "Alpha", None, "Beta")]}
    assert _event_team_names(descriptions_only) == ("Alpha", "Beta", "", "")
    title_only = {"title": "Alpha vs. Beta", "markets": []}
    assert _event_team_names(title_only) == ("Alpha", "Beta", "", "")
    assert _event_team_names({"title": "", "markets": []}) == (None, None, "", "")


def test_uconn_never_resolves_to_central_connecticut():
    # History and ESPN name UConn "UConn Huskies". The alias target "connecticut" matches no name
    # by prefix, and a substring test used to land on "Central Connecticut Blue Devils" instead:
    # five 2026-11 ledger games (incl. UConn @ Arizona) were priced as the wrong team.
    names = {
        "Central Connecticut Blue Devils",  # listed first on purpose: dict order picked the wrong one
        "Truett McConnell Bears",
        "UConn Huskies",
        "Arizona Wildcats",
    }
    normalized = {_normalize_name(n): n for n in names}

    def resolve(school, mascot=""):
        return _resolve_ncaab_team(school, mascot, names, normalized)

    for school, mascot in (
        ("UConn", "Huskies"),
        ("UConn", ""),
        ("Connecticut", "Huskies"),
        ("Connecticut", ""),
    ):
        assert resolve(school, mascot) == "UConn Huskies", (school, mascot)
    for school, mascot in (
        ("Central Connecticut", "Blue Devils"),
        ("Central Connecticut", ""),
        ("Central Connecticut State", ""),
    ):
        assert resolve(school, mascot) == "Central Connecticut Blue Devils", (school, mascot)
    assert resolve("Arizona", "Wildcats") == "Arizona Wildcats"


def test_umass_and_rutgers_never_resolve_to_lookalike_schools():
    # School-only "UMass" and "Rutgers" used to resolve to UMass Boston and Rutgers-Camden (non-D1
    # schools that outrank the flagship in token-subset matching), mispricing three 2026-11 games.
    names = {
        "UMass Boston Beacons",
        "UMass Lowell River Hawks",
        "Rutgers-Camden RUTGERS-CAMDEN",  # repeats its own token, so it outranks the flagship
        "Rutgers Scarlet Knights",
        "Massachusetts Minutemen",
    }
    normalized = {_normalize_name(n): n for n in names}

    def resolve(school, mascot=""):
        return _resolve_ncaab_team(school, mascot, names, normalized)

    for school, mascot in (
        ("UMass", ""),
        ("UMass", "Minutemen"),
        ("UMass Minutemen", ""),
        ("Massachusetts", "Minutemen"),
    ):
        assert resolve(school, mascot) == "Massachusetts Minutemen", (school, mascot)
    for school, mascot in (
        ("Rutgers", ""),
        ("Rutgers", "Scarlet Knights"),
        ("Rutgers Scarlet Knights", ""),
    ):
        assert resolve(school, mascot) == "Rutgers Scarlet Knights", (school, mascot)
    # The lookalikes keep resolving to themselves.
    assert resolve("UMass Boston", "") == "UMass Boston Beacons"
    assert resolve("UMass Lowell", "River Hawks") == "UMass Lowell River Hawks"
    assert resolve("Rutgers-Camden", "") == "Rutgers-Camden RUTGERS-CAMDEN"
    assert resolve("Rutgers Camden", "") == "Rutgers-Camden RUTGERS-CAMDEN"


def test_main_sample_gate_uses_current_season_and_fails_closed():
    assert not _main_sample_eligible(None, 8)
    assert not _main_sample_eligible(True, 8)
    assert not _main_sample_eligible(2, 8)
    assert _main_sample_eligible(8, 8)
    assert _main_sample_eligible(None, 0)


def test_ncaab_records_real_model_output_and_rerun_deduplicates(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_MIRROR", "0")
    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_AUTHORITY", "xlsx")
    now = datetime.now(UTC)
    start = (now + timedelta(days=1)).isoformat()
    games = [
        GameRecord(
            str(i), (now - timedelta(days=i + 1)).isoformat(), "NCAAB", "Away", "Home", 65 + i % 3, 72 + i % 3
        )
        for i in range(20)
    ]
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.ESPNClient.scoreboard",
        lambda *a: {"events": [{"id": "fixture"}]},
    )
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.FeatureStore.games_before", lambda *a: games
    )
    event = {
        "event_id": "fixture",
        "event_start_utc": start,
        "title": "Away vs. Home",
        "markets": [
            {
                "market_id": "fixture-ml",
                "market_slug": "fixture-ml",
                "market_type": "moneyline",
                "sides": [
                    {"selection": "away", "description": "Away", "price_probability": 0.45, "is_long": False},
                    {"selection": "home", "description": "Home", "price_probability": 0.55, "is_long": True},
                ],
            }
        ],
    }
    archive = tmp_path / "odds" / "ncaab" / now.date().isoformat() / "polymarket_snapshots.jsonl"
    archive.parent.mkdir(parents=True)
    archive.write_text(
        json.dumps(
            {
                "provider": "polymarket_us",
                "reconstructed": False,
                "timestamp_valid": True,
                "market_state": "MARKET_STATE_OPEN",
                "event_id": "fixture",
                "event_start_utc": start,
                "market_id": "fixture-ml",
                "market_slug": "fixture-ml",
                "market_type": "moneyline",
                "line": None,
                "observed_at_utc": (now - timedelta(minutes=1)).isoformat(),
                "transact_time_utc": (now - timedelta(minutes=2)).isoformat(),
                "long": {"bid": 0.54, "ask": 0.56},
                "short": {"bid": 0.44, "ask": 0.46},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.PolymarketUSClient.slate", lambda *a: [event]
    )
    flat_ledger = MultiSportPickLedger(tmp_path, flat=True)
    main_ledger = MultiSportPickLedger(tmp_path)
    args = {
        "data_root": tmp_path,
        "args_date": now.date().isoformat(),
        # Fixture teams are not in the real conference map; this test is about model output.
        "config": {"models": {"NCAAB": {"decline_non_d1": False}}},
        "flat_ledger": flat_ledger,
        "main_ledger": main_ledger,
    }
    report = forecast_ncaab_sport(**args)
    assert not report["errors"]
    assert report["flat_logged"] == 1
    assert report["main_logged"] == 1
    assert report["candidate_quote_coverage"]["quote_matched"] == 1
    flat_rows = flat_ledger.rows()
    main_rows = main_ledger.rows()
    assert len(flat_rows) == len(main_rows) == 1
    assert flat_rows[0]["selection"] == main_rows[0]["selection"] == "home"
    assert all(r["model_version"] == "ncaab-spread-v6" for r in flat_rows + main_rows)
    assert all(r["sportsbook"] == "polymarket_us_executable_ask" for r in flat_rows + main_rows)
    assert all(abs(float(r["market_implied_probability"]) - 0.56) < 0.001 for r in flat_rows + main_rows)
    assert all(r["market_snapshot_hash"] for r in flat_rows + main_rows)
    assert all(r["market_quote_timestamp_valid"] for r in flat_rows + main_rows)
    for row in flat_rows + main_rows:
        snapshot = json.loads(row["model_input_snapshot_json"])
        assert snapshot["schema"] == "feature-capture-v1"
        assert snapshot["replay_verified"] is False
        assert snapshot["features"]["history_games"] == len(games)
        assert len(snapshot["features"]["history_scores_sha256"]) == 64
        assert snapshot["features"]["research_quote_join.status"] == "matched"
        assert snapshot["features"]["research_quote_join.executable_ask"] == 0.56
        assert snapshot["features"]["research_quote_join.indicative_ask"] == 0.55
    rerun = forecast_ncaab_sport(**args)
    assert rerun["flat_logged"] == 0
    assert rerun["main_logged"] == 0
    assert len(flat_ledger.rows()) == len(main_ledger.rows()) == 1
    worse_quote = json.loads(archive.read_text(encoding="utf-8"))
    worse_quote["long"]["ask"] = 0.99
    archive.write_text(json.dumps(worse_quote) + "\n", encoding="utf-8")
    worse = forecast_ncaab_sport(
        **{**args, "flat_ledger": MultiSportPickLedger(tmp_path / "worse", flat=True), "main_ledger": None}
    )
    # Tier-1 guardrail (2026-11-15): a negative-edge moneyline is declined on every tier,
    # where it used to be written to Flat (110 of 166 settled flat v6 moneylines).
    assert worse["flat_logged"] == 0
    assert MultiSportPickLedger(tmp_path / "worse", flat=True).rows() == []
    assert any(s["reason"] == "moneyline_nonpositive_edge" for s in worse["skipped"])
    assert worse["declined_slots"] == [["NCAAB", "fixture", "moneyline"]]
    assert worse["candidate_quote_coverage"]["serving_decisions"] == 1
    archive.unlink()
    missing = forecast_ncaab_sport(
        **{
            **args,
            "flat_ledger": MultiSportPickLedger(tmp_path / "missing", flat=True),
            "main_ledger": MultiSportPickLedger(tmp_path / "missing_main"),
        }
    )
    assert missing["flat_logged"] == 1
    assert missing["main_logged"] == 0
    missing_row = MultiSportPickLedger(tmp_path / "missing", flat=True).rows()[0]
    assert missing_row["market_quote_provenance"] == "indicative_slate"
    assert missing_row["market_quote_timestamp_valid"] == "False"
    stale_quote = {**worse_quote, "observed_at_utc": (now - timedelta(hours=1)).isoformat()}
    archive.write_text(json.dumps(stale_quote) + "\n", encoding="utf-8")
    stale = forecast_ncaab_sport(
        **{**args, "flat_ledger": MultiSportPickLedger(tmp_path / "stale", flat=True), "main_ledger": None}
    )
    assert stale["flat_logged"] == 1
    assert (
        MultiSportPickLedger(tmp_path / "stale", flat=True).rows()[0]["market_quote_provenance"]
        == "indicative_slate"
    )


def test_no_games_does_not_construct_market_quotes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.ESPNClient.scoreboard", lambda *a: {"events": []}
    )
    report = forecast_ncaab_sport(data_root=tmp_path, args_date="2026-10-20", config={})
    assert report["status"] == "no_scheduled_events"
    assert report["logged"] == 0


def test_ncaab_picks_projected_winner_not_underdog_on_edge(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_MIRROR", "0")
    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_AUTHORITY", "xlsx")
    now = datetime.now(UTC)
    start = (now + timedelta(days=1)).isoformat()
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.ESPNClient.scoreboard",
        lambda *a: {"events": [{"id": "fixture"}]},
    )
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.FeatureStore.games_before",
        lambda *a: [
            GameRecord("past", (now - timedelta(days=1)).isoformat(), "NCAAB", "Away", "Home", 70, 75)
        ],
    )
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.PolymarketUSClient.slate",
        lambda *a: [
            {
                "event_id": "fixture",
                "event_start_utc": start,
                "markets": [
                    {
                        "market_type": "moneyline",
                        "sides": [
                            {
                                "selection": "away",
                                "team": "Away",
                                "description": "Away",
                                "price_probability": 0.20,
                            },
                            {
                                "selection": "home",
                                "team": "Home",
                                "description": "Home",
                                "price_probability": 0.80,
                            },
                        ],
                    }
                ],
            }
        ],
    )

    class Model:
        version = "ncaab-spread-v6"
        team_priors = _D1_TEAMS

        def predict_games(self, history, upcoming):
            return [
                SimpleNamespace(
                    market_type="moneyline",
                    probabilities={"home": 0.60, "away": 0.40},
                    uncertainty=0.1,
                    rationale="fixture",
                )
            ]

    monkeypatch.setattr("model_prediction.college_basketball_forward.ncaab_model", Model)
    flat = MultiSportPickLedger(tmp_path, flat=True)
    report = forecast_ncaab_sport(
        data_root=tmp_path,
        args_date=now.date().isoformat(),
        config={"models": {"NCAAB": {}}},
        flat_ledger=flat,
    )
    # Underdog (away, 40%) is NOT picked on moneyline even with positive edge
    # Favorite (home, 60%) has negative edge (0.60 - 0.80), so no pick is logged
    assert report["flat_logged"] == 0


def test_ncaab_gates_non_d1_and_extreme_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_MIRROR", "0")
    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_AUTHORITY", "xlsx")
    now = datetime.now(UTC)
    start = (now + timedelta(days=1)).isoformat()
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.ESPNClient.scoreboard",
        lambda *a: {"events": [{"id": "fixture_d1"}, {"id": "fixture_non_d1"}]},
    )
    # Only D1 team has 20 games; NonD1 has 2 games
    games = [
        GameRecord(
            f"d1_{i}", (now - timedelta(days=i + 1)).isoformat(), "NCAAB", "MajorAway", "MajorHome", 70, 75
        )
        for i in range(20)
    ] + [
        GameRecord(
            "n1", (now - timedelta(days=1)).isoformat(), "NCAAB", "SmallCollege", "MajorHome", 50, 100
        ),
        GameRecord("n2", (now - timedelta(days=2)).isoformat(), "NCAAB", "SmallCollege", "MajorAway", 55, 95),
    ]
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.FeatureStore.games_before",
        lambda *a: games,
    )
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.PolymarketUSClient.slate",
        lambda *a: [
            # Non-D1 game (should be skipped)
            {
                "event_id": "fixture_non_d1",
                "event_start_utc": start,
                "markets": [
                    {
                        "market_type": "moneyline",
                        "sides": [
                            {
                                "selection": "away",
                                "team": "SmallCollege",
                                "description": "SmallCollege",
                                "price_probability": 0.05,
                            },
                            {
                                "selection": "home",
                                "team": "MajorHome",
                                "description": "MajorHome",
                                "price_probability": 0.95,
                            },
                        ],
                    }
                ],
            },
            # D1 game with extreme longshot ML and blowout spread (both should be filtered)
            {
                "event_id": "fixture_d1",
                "event_start_utc": start,
                "markets": [
                    {
                        "market_type": "moneyline",
                        "sides": [
                            {
                                "selection": "away",
                                "team": "MajorAway",
                                "description": "MajorAway",
                                "price_probability": 0.05,
                            },
                            {
                                "selection": "home",
                                "team": "MajorHome",
                                "description": "MajorHome",
                                "price_probability": 0.95,
                            },
                        ],
                    },
                    {
                        "market_type": "spread",
                        "line": 28.5,
                        "sides": [
                            {
                                "selection": "away",
                                "team": "MajorAway",
                                "description": "MajorAway",
                                "price_probability": 0.50,
                                "line": 28.5,
                            },
                            {
                                "selection": "home",
                                "team": "MajorHome",
                                "description": "MajorHome",
                                "price_probability": 0.50,
                                "line": -28.5,
                            },
                        ],
                    },
                ],
            },
        ],
    )

    class Model:
        version = "ncaab-spread-v6"
        team_priors = _D1_TEAMS

        def predict_games(self, history, upcoming):
            return [
                SimpleNamespace(
                    market_type="moneyline",
                    probabilities={"home": 0.70, "away": 0.30},
                    uncertainty=0.1,
                    rationale="fixture",
                ),
                SimpleNamespace(
                    market_type="spread",
                    probabilities={"home": 0.10, "away": 0.90},
                    uncertainty=0.1,
                    rationale="fixture",
                    inference_inputs={"mean": 8.0, "sd": 10.5, "line": 28.5},
                ),
            ]

    monkeypatch.setattr("model_prediction.college_basketball_forward.ncaab_model", Model)
    flat = MultiSportPickLedger(tmp_path, flat=True)
    report = forecast_ncaab_sport(
        data_root=tmp_path,
        args_date=now.date().isoformat(),
        config={
            "models": {
                "NCAAB": {
                    "min_team_games": 15,
                    "min_moneyline_ask": 0.20,
                    "max_spread_line": 16.0,
                    "max_margin_divergence": 12.0,
                    "min_edge": 0.035,
                }
            }
        },
        flat_ledger=flat,
    )
    # Non-D1 game skipped; D1 extreme ML and blowout spread filtered out
    assert report["flat_logged"] == 0
    assert any(s["reason"] == "insufficient_history_non_d1" for s in report["skipped"])


def test_ncaab_gates_total_divergence(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_MIRROR", "0")
    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_AUTHORITY", "xlsx")
    now = datetime.now(UTC)
    start = (now + timedelta(days=1)).isoformat()
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.ESPNClient.scoreboard",
        lambda *a: {"events": [{"id": "fixture_tot"}]},
    )
    games = [
        GameRecord(f"g_{i}", (now - timedelta(days=i + 1)).isoformat(), "NCAAB", "TeamA", "TeamB", 70, 75)
        for i in range(20)
    ]
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.FeatureStore.games_before",
        lambda *a: games,
    )
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.PolymarketUSClient.slate",
        lambda *a: [
            {
                "event_id": "fixture_tot",
                "event_start_utc": start,
                "markets": [
                    {
                        "market_type": "total",
                        "line": 165.5,
                        "sides": [
                            {"selection": "under", "price_probability": 0.50, "line": 165.5},
                            {"selection": "over", "price_probability": 0.50, "line": 165.5},
                        ],
                    }
                ],
            }
        ],
    )

    class SpreadModel:
        version = "ncaab-spread-v6"
        team_priors = _D1_TEAMS

        def predict_spreads(self, history, upcoming):
            return []

    class TotalModel:
        version = "ncaab-total-v6"

        def predict_totals(self, history, upcoming):
            return [
                SimpleNamespace(
                    market_type="total",
                    probabilities={"under": 0.70, "over": 0.30},
                    uncertainty=0.1,
                    rationale="fixture",
                    line=165.5,
                    inference_inputs={"mean": 145.0, "sd": 14.5, "line": 165.5},
                )
            ]

    monkeypatch.setattr("model_prediction.college_basketball_forward.ncaab_spread_model", SpreadModel)
    monkeypatch.setattr("model_prediction.college_basketball_forward.ncaab_total_model", TotalModel)
    monkeypatch.setattr("model_prediction.college_basketball_forward.ncaab_model", SpreadModel)

    flat = MultiSportPickLedger(tmp_path, flat=True)
    report = forecast_ncaab_sport(
        data_root=tmp_path,
        args_date=now.date().isoformat(),
        config={
            "models": {
                "NCAAB": {
                    "max_total_divergence": 8.5,
                    "min_edge": 0.035,
                }
            }
        },
        flat_ledger=flat,
    )
    # Divergence gates qualified calls, but Flat retains the priced prediction.
    assert report["flat_logged"] == 0  # This fixture has no archived executable quote.


def _routing_fixture(tmp_path, monkeypatch):
    """One moneyline game with an archived executable 56c home ask (as in the real-model test above)."""
    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_MIRROR", "0")
    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_AUTHORITY", "xlsx")
    now = datetime.now(UTC)
    start = (now + timedelta(days=1)).isoformat()
    games = [
        GameRecord(
            str(i), (now - timedelta(days=i + 1)).isoformat(), "NCAAB", "Away", "Home", 65 + i % 3, 72 + i % 3
        )
        for i in range(20)
    ]
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.ESPNClient.scoreboard",
        lambda *a: {"events": [{"id": "fixture"}]},
    )
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.FeatureStore.games_before", lambda *a: games
    )
    event = {
        "event_id": "fixture",
        "event_start_utc": start,
        "title": "Away vs. Home",
        "markets": [
            {
                "market_id": "fixture-ml",
                "market_slug": "fixture-ml",
                "market_type": "moneyline",
                "sides": [
                    {"selection": "away", "description": "Away", "price_probability": 0.45, "is_long": False},
                    {"selection": "home", "description": "Home", "price_probability": 0.55, "is_long": True},
                ],
            }
        ],
    }
    archive = tmp_path / "odds" / "ncaab" / now.date().isoformat() / "polymarket_snapshots.jsonl"
    archive.parent.mkdir(parents=True)
    archive.write_text(
        json.dumps(
            {
                "provider": "polymarket_us",
                "reconstructed": False,
                "timestamp_valid": True,
                "market_state": "MARKET_STATE_OPEN",
                "event_id": "fixture",
                "event_start_utc": start,
                "market_id": "fixture-ml",
                "market_slug": "fixture-ml",
                "market_type": "moneyline",
                "line": None,
                "observed_at_utc": (now - timedelta(minutes=1)).isoformat(),
                "transact_time_utc": (now - timedelta(minutes=2)).isoformat(),
                "long": {"bid": 0.54, "ask": 0.56},
                "short": {"bid": 0.44, "ask": 0.46},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.PolymarketUSClient.slate", lambda *a: [event]
    )
    return now.date().isoformat()


def _fake_spread_model(version: str, artifact_hash: str, margin_engine: str, home: float = 0.65):
    class Model:
        def __init__(self):
            self.version = version
            self.model_artifact_hash = artifact_hash
            self.margin_engine = margin_engine
            self.team_priors = _D1_TEAMS

        def predict_games(self, history, upcoming):
            return [
                SimpleNamespace(
                    market_type="moneyline",
                    probabilities={"home": home, "away": round(1 - home, 6)},
                    uncertainty=0.1,
                    rationale="fixture",
                    feature_basis={"margin_engine": self.margin_engine},
                )
            ]

    built: list = []

    def factory():
        built.append(Model())
        return built[-1]

    return factory, built


def _unexpected_factory():
    raise AssertionError("the wrong NCAAB spread factory was dispatched")


def test_ncaab_v7_champion_serves_the_v7_factory(tmp_path, monkeypatch):
    day = _routing_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.ProductionModelRegistry.load",
        _registry("ncaab-spread-v7"),
    )
    factory, built = _fake_spread_model("ncaab-spread-v7", "h7", "v7")
    monkeypatch.setattr("model_prediction.college_basketball_forward.ncaab_spread_v7_model", factory)
    monkeypatch.setattr("model_prediction.college_basketball_forward.ncaab_spread_model", _unexpected_factory)
    flat = MultiSportPickLedger(tmp_path, flat=True)
    config = {"models": {"NCAAB": {"decline_non_d1": False}}}
    report = forecast_ncaab_sport(data_root=tmp_path, args_date=day, config=config, flat_ledger=flat)
    assert not report["errors"]
    assert report["flat_logged"] == 1
    assert len(built) == 1 and built[0].margin_engine == "v7"
    rows = flat.rows()
    assert [r["model_version"] for r in rows] == ["ncaab-spread-v7"]
    assert rows[0]["model_artifact_hash"] == "h7"
    assert json.loads(rows[0]["model_input_snapshot_json"])["artifact_hash"] == "h7"


def test_ncaab_v6_rollback_serves_the_v6_engine(tmp_path, monkeypatch):
    """Rollback to ncaab-spread-v6 serves the v6 factory as built (engine v6); nothing resets it."""
    day = _routing_fixture(tmp_path, monkeypatch)
    factory, built = _fake_spread_model("ncaab-spread-v6", "h6", "v6")
    monkeypatch.setattr("model_prediction.college_basketball_forward.ncaab_spread_model", factory)
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.ncaab_spread_v7_model", _unexpected_factory
    )
    flat = MultiSportPickLedger(tmp_path, flat=True)
    config = {"models": {"NCAAB": {"decline_non_d1": False}}}
    report = forecast_ncaab_sport(data_root=tmp_path, args_date=day, config=config, flat_ledger=flat)
    assert report["flat_logged"] == 1
    assert len(built) == 1 and built[0].margin_engine == "v6"
    rows = flat.rows()
    assert [r["model_version"] for r in rows] == ["ncaab-spread-v6"]
    assert rows[0]["model_artifact_hash"] == "h6"


def test_ncaab_retired_margin_engine_key_fails_closed(tmp_path, monkeypatch):
    day = _routing_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.ProductionModelRegistry.load",
        _registry("ncaab-spread-v7"),
    )
    factory, _ = _fake_spread_model("ncaab-spread-v7", "h7", "v7")
    monkeypatch.setattr("model_prediction.college_basketball_forward.ncaab_spread_v7_model", factory)
    config = {"models": {"NCAAB": {"decline_non_d1": False, "margin_engine": "v6"}}}
    with pytest.raises(ValueError, match="retired"):
        forecast_ncaab_sport(data_root=tmp_path, args_date=day, config=config)


def test_ncaab_split_spread_champions_fail_closed(tmp_path, monkeypatch):
    day = _routing_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.ProductionModelRegistry.load",
        _registry("ncaab-spread-v7", moneyline_id="ncaab-spread-v6"),
    )
    with pytest.raises(ValueError, match="one spread model"):
        forecast_ncaab_sport(data_root=tmp_path, args_date=day, config={"models": {"NCAAB": {}}})


def test_ncaab_v7m_moneyline_champion_labels_moneyline_rows_with_its_own_id(tmp_path, monkeypatch):
    """ncaab-moneyline-v7m over a v7 spread champion: one instance, moneyline rows carry v7m's id/hash."""
    day = _routing_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.ProductionModelRegistry.load",
        _registry("ncaab-spread-v7", moneyline_id="ncaab-moneyline-v7m"),
    )
    plain_factory, built = _fake_spread_model("ncaab-spread-v7", "h7", "v7")

    def factory():
        model = plain_factory()
        model.moneyline_version = "ncaab-moneyline-v7m"
        model.moneyline_artifact_hash = "h7m"
        return model

    monkeypatch.setattr("model_prediction.college_basketball_forward.ncaab_moneyline_v7m_model", factory)
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.ncaab_spread_v7_model", _unexpected_factory
    )
    monkeypatch.setattr("model_prediction.college_basketball_forward.ncaab_spread_model", _unexpected_factory)
    flat = MultiSportPickLedger(tmp_path, flat=True)
    config = {"models": {"NCAAB": {"decline_non_d1": False}}}
    report = forecast_ncaab_sport(data_root=tmp_path, args_date=day, config=config, flat_ledger=flat)
    assert not report["errors"]
    assert report["flat_logged"] == 1
    assert len(built) == 1
    assert report["model_version"] == "ncaab-moneyline-v7m+ncaab-spread-v7+ncaab-total-v6"
    # The moneyline calibrator is looked up under v7m's own id, so v7's blended pool never serves it.
    assert report["calibration"]["moneyline"]["model_version"] == "ncaab-moneyline-v7m"
    assert report["calibration"]["spread"]["model_version"] == "ncaab-spread-v7"
    rows = flat.rows()
    assert [r["model_version"] for r in rows] == ["ncaab-moneyline-v7m"]
    assert rows[0]["model_artifact_hash"] == "h7m"
    snapshot = json.loads(rows[0]["model_input_snapshot_json"])
    assert snapshot["artifact_hash"] == "h7m"


@pytest.mark.parametrize(
    ("moneyline_id", "spread_id"),
    [
        ("ncaab-moneyline-v7m", "ncaab-spread-v6"),  # v7m exists only over the v7 engine
        ("ncaab-moneyline-v7m", "ncaab-spread-v5"),
        ("ncaab-moneyline-v7m", "ncaab-moneyline-v7m"),  # v7m is not a spread model
        ("ncaab-spread-v7", "ncaab-moneyline-v7m"),
    ],
)
def test_ncaab_v7m_unsupported_champion_pairs_fail_closed(tmp_path, monkeypatch, moneyline_id, spread_id):
    day = _routing_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.ProductionModelRegistry.load",
        _registry(spread_id, moneyline_id=moneyline_id),
    )
    for name in ("ncaab_moneyline_v7m_model", "ncaab_spread_v7_model", "ncaab_spread_model"):
        monkeypatch.setattr(f"model_prediction.college_basketball_forward.{name}", _unexpected_factory)
    with pytest.raises(ValueError, match="one spread model"):
        forecast_ncaab_sport(data_root=tmp_path, args_date=day, config={"models": {"NCAAB": {}}})


def _real_v7_pair(margins):
    """The production v7 and v7m factories with the v7 engine replaced by fixed per-event margins."""
    from model_prediction.models.ncaab_spread_v6 import ncaab_moneyline_v7m_model, ncaab_spread_v7_model

    serving = SimpleNamespace(margins=lambda history, upcoming: dict(margins))
    v7, v7m = ncaab_spread_v7_model(), ncaab_moneyline_v7m_model()
    v7.v7_serving = serving
    v7m.v7_serving = serving
    return v7, v7m


def test_ncaab_v7m_moneyline_is_the_margin_probability_and_spread_is_v7():
    """A_ml_w0: p_home = P_t8(margin >= 1) under v7's served mean/scale, Elo weight 0; spread untouched."""
    from scipy import stats

    from model_prediction.models.basketball import UpcomingGame

    now = datetime.now(UTC)
    teams = ("Duke Blue Devils", "Kansas Jayhawks", "Gonzaga Bulldogs", "Purdue Boilermakers")
    history = [
        GameRecord(
            f"h{i}",
            (now - timedelta(days=60 - i)).isoformat(),
            "NCAAB",
            teams[i % 4],
            teams[(i + 1) % 4],
            60 + (i * 7) % 19,
            62 + (i * 5) % 23,
        )
        for i in range(40)
    ]
    start = (now + timedelta(hours=6)).isoformat()
    upcoming = [
        UpcomingGame("g1", start, teams[0], teams[1], spread_away_line=3.5, league="NCAAB"),
        UpcomingGame(
            "g2", start, teams[2], teams[3], spread_away_line=-4.0, league="NCAAB", neutral_site=True
        ),
        # no v7 margin for g3: the v6 fallback path; v7m still reads the moneyline off the margin alone
        UpcomingGame("g3", start, teams[1], teams[2], spread_away_line=1.5, league="NCAAB"),
    ]
    margins = {"g1": SimpleNamespace(mean=6.3, scale=9.1), "g2": SimpleNamespace(mean=-2.2, scale=10.4)}
    v7, v7m = _real_v7_pair(margins)
    base = {(p.event_id, p.market_type): p for p in v7.predict_games(history, upcoming)}
    new = {(p.event_id, p.market_type): p for p in v7m.predict_games(history, upcoming)}
    assert base.keys() == new.keys()
    for event_id in ("g1", "g2", "g3"):
        spread_old, spread_new = base[(event_id, "spread")], new[(event_id, "spread")]
        assert spread_new == spread_old  # every field: probabilities, mean/scale, feature basis, version
        assert spread_new.model_version == "ncaab-spread-v7"
        ml_old, ml_new = base[(event_id, "moneyline")], new[(event_id, "moneyline")]
        assert ml_new.model_version == "ncaab-moneyline-v7m"
        assert ml_new.inference_inputs == ml_old.inference_inputs
        assert ml_new.feature_basis == {**ml_old.feature_basis, "moneyline_rule": "margin_only"}
        mean = float(spread_new.inference_inputs["mean"])
        scale = float(spread_new.inference_inputs["scale"])
        expected = min(0.9999, max(0.0001, float(stats.t.sf(0.5, df=8.0, loc=mean, scale=scale))))
        assert ml_new.probabilities["home"] == pytest.approx(round(expected, 6), abs=1e-12)
        assert ml_new.probabilities["away"] == pytest.approx(round(1.0 - expected, 6), abs=1e-12)
        # the v7 blend puts half the weight on Elo, so the served v7 moneyline differs
        assert abs(ml_old.probabilities["home"] - ml_new.probabilities["home"]) > 1e-4
    assert new[("g1", "moneyline")].feature_basis["margin_engine"] == "v7"
    assert new[("g3", "moneyline")].feature_basis["margin_engine"] == "v6"


def test_ncaab_v7m_factory_identity():
    from model_prediction.models.ncaab_spread_v6 import (
        ncaab_moneyline_v7m_artifact_hash,
        ncaab_moneyline_v7m_model,
        ncaab_spread_v7_model,
    )

    v7, v7m = ncaab_spread_v7_model(), ncaab_moneyline_v7m_model()
    assert (v7m.version, v7m.model_artifact_hash) == (v7.version, v7.model_artifact_hash)
    assert (v7.moneyline_margin_only, v7.moneyline_version, v7.moneyline_artifact_hash) == (False, None, "")
    assert v7m.moneyline_margin_only and v7m.moneyline_version == "ncaab-moneyline-v7m"
    assert v7m.moneyline_artifact_hash == ncaab_moneyline_v7m_artifact_hash(v7.model_artifact_hash)
    assert v7m.moneyline_artifact_hash not in ("", v7.model_artifact_hash)


def _write_calibrator(tmp_path, *, sample_size: int, temperature: float = 2.0) -> None:
    path = tmp_path / "calibration" / "models" / "ncaab-spread-v6.moneyline_calibrator.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "sport": "ncaab",
                "market": "moneyline",
                "model_version": "ncaab-spread-v6",
                "champion_method": "temperature",
                "parameters": {"temperature": temperature},
                "sample_size": sample_size,
                "baseline_brier": 0.25,
                "calibrated_oof_brier": 0.24,
                "fit_model_versions": ["ncaab-spread-v6"],
                "fit_source": "settled_live_rows",
            }
        ),
        encoding="utf-8",
    )


def _served_moneyline(tmp_path, monkeypatch, sample_size: int):
    day = _routing_fixture(tmp_path, monkeypatch)
    _write_calibrator(tmp_path, sample_size=sample_size)
    factory, _built = _fake_spread_model("ncaab-spread-v6", "h6", "v6", home=0.80)
    monkeypatch.setattr("model_prediction.college_basketball_forward.ncaab_spread_model", factory)
    flat = MultiSportPickLedger(tmp_path, flat=True)
    config = {"models": {"NCAAB": {"decline_non_d1": False}}}
    report = forecast_ncaab_sport(data_root=tmp_path, args_date=day, config=config, flat_ledger=flat)
    assert report["flat_logged"] == 1
    return report, flat.rows()[0]


def test_ncaab_serves_a_calibrator_that_passes_the_serving_guard(tmp_path, monkeypatch):
    report, row = _served_moneyline(tmp_path, monkeypatch, sample_size=150)
    assert report["calibration"]["moneyline"]["applied"] is True
    assert float(row["model_probability_raw"]) == pytest.approx(0.80)
    assert float(row["serving_probability"]) < 0.75  # temperature 2 pulls 80% toward 50%
    assert row["calibration_method"] == "temperature"


def test_ncaab_serves_raw_when_the_calibrator_fails_the_guard(tmp_path, monkeypatch):
    report, row = _served_moneyline(tmp_path, monkeypatch, sample_size=50)
    assert report["calibration"]["moneyline"]["applied"] is False
    assert report["calibration"]["moneyline"]["reason"] == "sample_too_small"
    assert float(row["serving_probability"]) == pytest.approx(0.80)
    assert row["calibration_method"] == "identity"


# --- P1 player availability is a shadow field only (2026-11-25) ------------------------------------------


def _p1_shadow_run(root, monkeypatch, *, margin_engine="v7", shadow=None):
    root.mkdir()
    day = _routing_fixture(root, monkeypatch)
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.ProductionModelRegistry.load",
        _registry("ncaab-spread-v7"),
    )
    factory, _built = _fake_spread_model("ncaab-spread-v7", "h7", margin_engine)
    monkeypatch.setattr("model_prediction.college_basketball_forward.ncaab_spread_v7_model", factory)
    if shadow is not None:
        monkeypatch.setattr("model_prediction.college_basketball_forward.p1_margin_adjustment", shadow)
    flat = MultiSportPickLedger(root, flat=True)
    config = {"models": {"NCAAB": {"decline_non_d1": False}}}
    report = forecast_ncaab_sport(data_root=root, args_date=day, config=config, flat_ledger=flat)
    assert not report["errors"] and report["flat_logged"] == 1
    row = flat.rows()[0]
    return row, json.loads(row["model_input_snapshot_json"])["features"]


_SERVED_FIELDS = (
    "model_probability",
    "model_probability_raw",
    "market_implied_probability",
    "edge",
    "units",
    "confidence_score",
)


def test_p1_shadow_never_changes_the_served_pick(tmp_path, monkeypatch):
    from model_prediction.features import ncaab_player_availability as avail

    side = avail.TeamAvailability("Home", None, 0.2, 0.8, 5, 5, "prev")

    def large(home, away, tip, history, index):
        return avail.P1Adjustment(25.0, None, side, side)

    def broken(*_args):
        raise RuntimeError("shadow blew up")

    no_boxes, no_boxes_features = _p1_shadow_run(tmp_path / "none", monkeypatch)
    shifted, shifted_features = _p1_shadow_run(tmp_path / "large", monkeypatch, shadow=large)
    failed, failed_features = _p1_shadow_run(tmp_path / "raise", monkeypatch, shadow=broken)
    assert no_boxes["model_probability"] not in (None, "")
    for name in _SERVED_FIELDS:
        assert no_boxes[name] not in (None, ""), name
        assert no_boxes[name] == shifted[name] == failed[name], name
    # the shadow itself is recorded, and only in the snapshot
    assert no_boxes_features["shadow_p1_status"] == avail.INSUFFICIENT_HISTORY
    assert "shadow_p1_adjustment" not in no_boxes_features
    assert shifted_features["shadow_p1_status"] == "ok"
    assert shifted_features["shadow_p1_adjustment"] == 25.0
    assert shifted_features["shadow_p1_inputs.home_avail_top7_prev1"] == 0.2
    assert shifted_features["shadow_p1_inputs.coef_avail"] == avail.P1_COEF_AVAIL
    assert failed_features["shadow_p1_status"] == "error"


def test_p1_shadow_is_recorded_only_on_v7_margins(tmp_path, monkeypatch):
    _row, features = _p1_shadow_run(tmp_path / "v6", monkeypatch, margin_engine="v6")
    assert not any(key.startswith("shadow_p1") for key in features)


def test_ncaab_rows_record_the_market_and_side_the_buyer_then_uses(tmp_path, monkeypatch):
    """The forecast writes the exact Polymarket market and side it priced into the rationale (the
    slate's sides carry the exchange's own away/home ordering, no name matching), and the buyer's
    quote lookup uses them from the dashboard rows it actually reads. The archived quote here has no
    side descriptions, so matching by name finds nothing."""
    import importlib

    from model_prediction.dashboard_cache import DashboardCache

    backtests = importlib.import_module("model_prediction.dashboard.backtests")
    day = _routing_fixture(tmp_path, monkeypatch)
    factory, _built = _fake_spread_model("ncaab-spread-v6", "h6", "v6", home=0.80)
    monkeypatch.setattr("model_prediction.college_basketball_forward.ncaab_spread_model", factory)
    flat = MultiSportPickLedger(tmp_path, flat=True)
    config = {"models": {"NCAAB": {"decline_non_d1": False}}}
    report = forecast_ncaab_sport(data_root=tmp_path, args_date=day, config=config, flat_ledger=flat)
    assert report["flat_logged"] == 1
    assert flat.rows()[0]["rationale"].endswith(" market_slug=fixture-ml token_side=long")

    cache = DashboardCache(tmp_path, db_path=tmp_path / "dashboard_cache.db", authority="xlsx")
    cache.refresh(force=True)
    (row,) = cache.read_picks("flat")
    archive = tmp_path / "odds" / "ncaab" / day / "polymarket_snapshots.jsonl"
    monkeypatch.setattr(backtests, "_resolve_snapshot_path", lambda _sport, _day: archive)

    quote = backtests._pick_quote(row)

    assert quote is not None
    assert (quote["market_slug"], quote["side"], quote["ask"]) == ("fixture-ml", "long", 0.56)
    assert backtests._pick_quote({**row, "rationale": "fixture"}) is None


def _live_board(state, home, away, start="2026-11-25T23:00Z"):
    return {
        "events": [
            {
                "date": start,
                "competitions": [
                    {
                        "status": {"type": {"state": state}},
                        "competitors": [
                            {"homeAway": "home", "team": {"displayName": home}},
                            {"homeAway": "away", "team": {"displayName": away}},
                        ],
                    }
                ],
            }
        ]
    }


def test_p1_in_progress_merges_today_and_the_previous_day_and_survives_a_fetch_failure(monkeypatch):
    from model_prediction.college_basketball_forward import _p1_in_progress

    seen = []

    def previous_day(self, league, day):
        seen.append((league, day))
        return _live_board("in", "C", "D", "2026-11-25T03:00Z")

    monkeypatch.setattr("model_prediction.college_basketball_forward.ESPNClient.scoreboard", previous_day)
    live = _p1_in_progress(_live_board("in", "A", "B"), "2026-11-26")
    assert seen == [("NCAAB", "2026-11-25")] and set(live) == {"A", "B", "C", "D"}

    def broken(self, league, day):
        raise RuntimeError("espn down")

    monkeypatch.setattr("model_prediction.college_basketball_forward.ESPNClient.scoreboard", broken)
    assert set(_p1_in_progress(_live_board("in", "A", "B"), "2026-11-26")) == {"A", "B"}
    assert _p1_in_progress(None, None) == {}


def test_p1_shadow_hands_the_live_games_to_the_box_index(tmp_path, monkeypatch):
    from model_prediction.features import ncaab_player_availability as avail

    live = {"Some Team": ["a live tip"]}
    calls = []
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward._p1_in_progress",
        lambda board, day: calls.append((board, day)) or live,
    )
    side = avail.TeamAvailability("Home", None, 0.2, 0.8, 5, 5, "prev")
    seen = []

    def spy(home, away, tip, history, index):
        seen.append(index.in_progress)
        return avail.P1Adjustment(1.0, None, side, side)

    _p1_shadow_run(tmp_path / "live", monkeypatch, shadow=spy)
    assert len(calls) == 1 and calls[0][1]  # the slate's scoreboard and date reach the helper, once
    assert seen and all(index_live is live for index_live in seen)


def test_p1_shadow_never_serves_an_index_without_its_live_games_after_a_failed_lookup(tmp_path, monkeypatch):
    """A malformed scoreboard date makes the live-games lookup raise for the first game. The season's
    index used to be stored before that call, so every later game of the season was scored against an
    empty ``in_progress`` and a previous game still being played looked fine."""
    from model_prediction import college_basketball_forward as forward
    from model_prediction.features import ncaab_player_availability as avail

    live = {"Some Team": ["a live tip"]}
    lookups = []

    def flaky(board, day):
        lookups.append(day)
        if len(lookups) == 1:
            raise ValueError("bad scoreboard date")
        return live

    seen = []
    side = avail.TeamAvailability("Home", None, 0.2, 0.8, 5, 5, "prev")

    def spy(home, away, tip, history, index):
        seen.append(index.in_progress)
        return avail.P1Adjustment(1.0, None, side, side)

    monkeypatch.setattr(forward, "_p1_in_progress", flaky)
    monkeypatch.setattr(forward, "p1_margin_adjustment", spy)
    monkeypatch.setattr(
        forward.PlayerBoxIndex, "load", classmethod(lambda cls, *a, **k: cls.__new__(cls)), raising=True
    )
    monkeypatch.setattr(forward.PlayerBoxIndex, "in_progress", {}, raising=False)
    decisions = [
        {
            "kind": "spread",
            "home": home,
            "away": away,
            "start": "2026-11-25T23:00:00Z",
            "prediction": SimpleNamespace(feature_basis={"margin_engine": "v7"}),
        }
        for home, away in (("H1", "A1"), ("H2", "A2"))
    ]
    out = forward._p1_shadow_fields(
        decisions, [], tmp_path, datetime(2026, 11, 25, 12, tzinfo=UTC), board={}, args_date="2026-11-25"
    )
    assert out[id(decisions[0])]["shadow_p1_status"] == "error"
    assert seen == [live], "a game is only scored against an index that carries the live games"
