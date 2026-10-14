import hashlib
import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from model_prediction.data_sources.polymarket_us import (
    CAPTURED_UNPRICED_ESPORTS_LEAGUES,
    POLYMARKET_SPORT_LEAGUES,
)
from model_prediction.domain import eastern_today
from model_prediction.esports import (
    TITLE_SPECS,
    Bo3EsportsClient,
    NeutralElo,
    _metrics,
    forecast_esports_slate,
    refresh_recent_matches,
    validate_esports_baseline,
)
from model_prediction.production_feature_ablation import ESPORTS_TITLES


def test_polymarket_us_esports_taxonomy_is_explicit_and_complete() -> None:
    assert POLYMARKET_SPORT_LEAGUES["esports"] == (
        "LOL",
        "CS2",
        "COD",
        "VALORANT",
        "DOTA2",
        "ROCKET_LEAGUE",
        "OVERWATCH",
        "RAINBOW_SIX",
    )


def test_esports_capture_and_pricing_sets_cannot_drift_silently() -> None:
    """2026-07-27 audit gap: BBO capture covered all 8 esports leagues while
    models priced only 4, with no test catching the drift. Capture of the
    extra leagues is deliberate (market evidence for future models), but
    every captured league must land in exactly one named tier: priced
    (ESPORTS_TITLES), spec'd-but-unpriced (TITLE_SPECS), or the documented
    deferral set -- so a future capture/pricing change fails here instead of
    drifting silently."""
    captured = set(POLYMARKET_SPORT_LEAGUES["esports"])
    priced = {TITLE_SPECS[title]["polymarket_league"] for title in ESPORTS_TITLES}
    speced = {spec["polymarket_league"] for spec in TITLE_SPECS.values()}
    assert priced <= captured, "a priced league has no BBO capture"
    assert captured - speced == CAPTURED_UNPRICED_ESPORTS_LEAGUES, (
        "capture set drifted: every captured league must be priced, spec'd, or named deferred"
    )


def test_neutral_elo_has_no_team_order_advantage() -> None:
    book = NeutralElo(k=20, ratings={"a": 1600, "b": 1400})
    assert round(book.probability("a", "b") + book.probability("b", "a"), 12) == 1.0


def test_raw_probability_never_applies_decay_or_shrink() -> None:
    """raw_probability() is the basis for rating updates and must stay pure
    Elo dynamics regardless of last_match_utc/games_played -- decay/shrink
    are prediction-time-only adjustments applied by probability(), never
    fed back into how ratings actually move."""
    book = NeutralElo(
        k=20,
        ratings={"a": 1600, "b": 1400},
        last_match_utc={"a": "2020-01-01T00:00:00Z", "b": "2026-08-04T00:00:00Z"},
        games_played={"a": 1, "b": 500},
    )
    from model_prediction.features.elo_ratings import expected_win_probability

    assert book.raw_probability("a", "b") == expected_win_probability(1600, 1400)


def test_thin_data_matchup_is_shrunk_toward_half() -> None:
    """A brand-new team (0 recorded games) makes the whole matchup's
    prediction unreliable regardless of the rating gap -- real bug this
    guards against not existing: pre-v6, a fresh 1500-default team facing an
    established 1900-rated team produced a confident, unshrunk edge."""
    from model_prediction.features.elo_ratings import expected_win_probability

    book = NeutralElo(k=20, ratings={"veteran": 1900, "rookie": 1500})
    book.games_played = {"veteran": 200}  # rookie has no entry -> 0 games
    shrunk = book.probability("veteran", "rookie")
    unshrunk = expected_win_probability(1900, 1500)
    assert abs(shrunk - 0.5) < abs(unshrunk - 0.5) * 0.6  # meaningfully closer to a coin flip


def test_established_matchup_is_not_shrunk() -> None:
    book = NeutralElo(k=20, ratings={"a": 1900, "b": 1500})
    book.games_played = {"a": 200, "b": 50}
    from model_prediction.features.elo_ratings import expected_win_probability

    assert book.probability("a", "b") == pytest.approx(expected_win_probability(1900, 1500))


def test_inactive_team_rating_decays_toward_neutral_at_prediction_time() -> None:
    """A team that hasn't played in a long time relative to the prediction's
    reference_date should be treated as less certain (rating pulled toward
    1500), even though their point-estimate rating and games_played stay
    exactly as last observed."""
    book = NeutralElo(k=20, ratings={"stale": 1900, "active": 1500})
    book.games_played = {"stale": 200, "active": 200}
    book.last_match_utc = {"stale": "2020-01-01T00:00:00Z", "active": "2026-08-04T00:00:00Z"}
    reference_date = datetime(2026, 8, 4, tzinfo=UTC)
    decayed = book.probability("stale", "active", reference_date)
    fresh = book.probability("stale", "active", datetime(2020, 1, 1, tzinfo=UTC))
    assert decayed < fresh  # long-inactive favorite's edge shrinks, doesn't grow
    assert 0.5 < decayed < fresh


def test_no_reference_date_disables_decay_matching_pre_v6_behavior() -> None:
    """Omitting reference_date (the pre-v6 call signature) must still skip
    inactivity decay entirely -- existing callers that haven't been updated
    to pass one keep their old behavior, not a silent new one."""
    book = NeutralElo(k=20, ratings={"stale": 1900, "active": 1500})
    book.games_played = {"stale": 200, "active": 200}
    book.last_match_utc = {"stale": "2020-01-01T00:00:00Z", "active": "2026-08-04T00:00:00Z"}
    from model_prediction.features.elo_ratings import expected_win_probability

    assert book.probability("stale", "active") == pytest.approx(expected_win_probability(1900, 1500))


def test_metrics_units_at_minus_110_matches_flat_stake_diagnostic() -> None:
    # 3 correct, 1 wrong at threshold 0 (all rows selected) -> 3*(10/11) - 1
    rows = [
        {"probability": 0.7, "outcome": 1},
        {"probability": 0.7, "outcome": 1},
        {"probability": 0.3, "outcome": 0},
        {"probability": 0.7, "outcome": 0},
    ]
    metrics = _metrics(rows)
    assert metrics["calls"] == 4
    assert metrics["hits"] == 3
    assert metrics["units_at_minus_110"] == pytest.approx(3 * (10 / 11) - 1)


def test_metrics_empty_selection_reports_zero_units_not_none() -> None:
    metrics = _metrics([], threshold=0.9)
    assert metrics["calls"] == 0
    assert metrics["hits"] == 0
    assert metrics["units_at_minus_110"] == 0.0


def test_cs2_backfill_excludes_legacy_csgo_game_version() -> None:
    rows = [
        {
            "id": 1,
            "slug": "old-csgo",
            "team1_id": 10,
            "team2_id": 20,
            "winner_team_id": 10,
            "team1_score": 2,
            "team2_score": 0,
            "status": "finished",
            "start_date": "2024-01-01T12:00:00.000+00:00",
            "end_date": "2024-01-01T14:00:00.000+00:00",
            "bo_type": 3,
            "discipline_id": 1,
            "game_version": 1,
        },
        {
            "id": 2,
            "slug": "current-cs2",
            "team1_id": 10,
            "team2_id": 20,
            "winner_team_id": 20,
            "team1_score": 1,
            "team2_score": 2,
            "status": "finished",
            "start_date": "2024-01-02T12:00:00.000+00:00",
            "end_date": "2024-01-02T15:00:00.000+00:00",
            "bo_type": 3,
            "discipline_id": 1,
            "game_version": 2,
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/matches")
        return httpx.Response(
            200,
            json={"total": {"count": 2, "limit": 100, "offset": 0}, "results": rows},
        )

    source = Bo3EsportsClient(
        "https://example.test/api/v1",
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    matches, _ = source.finished_matches("cs2", date(2024, 1, 1), date(2024, 1, 3))
    assert [row["match_id"] for row in matches] == ["bo3:2"]


def test_refresh_recent_matches_merges_instead_of_overwriting(tmp_path) -> None:
    """refresh_recent_matches must merge a short recent fetch into existing
    history, not replace it (unlike backfill_esports, which does a full-file
    overwrite and would silently delete older history if called with a
    short window on a schedule)."""
    directory = tmp_path / "esports" / "lol"
    directory.mkdir(parents=True)
    old_match = {
        "match_id": "bo3:1",
        "source_match_id": 1,
        "start_utc": "2024-01-01T00:00:00Z",
        "end_utc": "2024-01-01T01:00:00Z",
        "team1_id": "bo3:3:10",
        "team1_name": "Old Team A",
        "team1_score": 2,
        "team2_id": "bo3:3:20",
        "team2_name": "Old Team B",
        "team2_score": 0,
        "winner_id": "bo3:3:10",
        "best_of": 3,
        "tier": "a",
        "title": "lol",
        "tournament_id": 1,
        "game_version": None,
        "source_url": "https://bo3.gg/matches/old",
    }
    (directory / "matches.jsonl").write_text(json.dumps(old_match) + "\n", encoding="utf-8")
    old_teams = {
        "bo3:3:10": {
            "team_id": "bo3:3:10",
            "source_team_id": 10,
            "name": "Old Team A",
            "slug": "a",
            "acronym": "A",
        },
        "bo3:3:20": {
            "team_id": "bo3:3:20",
            "source_team_id": 20,
            "name": "Old Team B",
            "slug": "b",
            "acronym": "B",
        },
    }
    (directory / "teams.json").write_text(json.dumps(old_teams), encoding="utf-8")

    # Relative to "today" (matches refresh_recent_matches's own eastern_today()
    # lookback window) rather than a hardcoded date -- a fixed date drifts out
    # of the 14-day window as real time passes and starts failing for reasons
    # unrelated to the code under test. Found 2026-08-04 when a long session
    # crossed exactly this boundary.
    recent_match_date = eastern_today() - timedelta(days=3)
    new_match_row = {
        "id": 2,
        "team1_id": 10,
        "team2_id": 30,
        "winner_team_id": 30,
        "team1_score": 0,
        "team2_score": 2,
        "status": "finished",
        "start_date": f"{recent_match_date.isoformat()}T12:00:00.000+00:00",
        "end_date": f"{recent_match_date.isoformat()}T14:00:00.000+00:00",
        "bo_type": 3,
        "discipline_id": 3,
        "game_version": None,
    }
    new_team_row = {"id": 30, "discipline_id": 3, "name": "New Team C", "slug": "c", "acronym": "C"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/matches"):
            return httpx.Response(
                200,
                json={"total": {"count": 1, "limit": 100, "offset": 0}, "results": [new_match_row]},
            )
        if request.url.path.endswith("/teams"):
            return httpx.Response(
                200,
                json={"total": {"count": 1, "limit": 100, "offset": 0}, "results": [new_team_row]},
            )
        raise AssertionError(f"unexpected request: {request.url}")

    client = Bo3EsportsClient(
        "https://example.test/api/v1",
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = refresh_recent_matches(tmp_path, "lol", lookback_days=14, client=client)

    assert result["new_or_updated_matches"] == 1
    assert result["total_matches"] == 2

    merged = [
        json.loads(line)
        for line in (directory / "matches.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    match_ids = {row["match_id"] for row in merged}
    assert match_ids == {"bo3:1", "bo3:2"}
    # The old match's team names must survive untouched (team 20 isn't in
    # this fetch's team catalog at all).
    old_row = next(row for row in merged if row["match_id"] == "bo3:1")
    assert old_row["team1_name"] == "Old Team A"
    assert old_row["team2_name"] == "Old Team B"

    teams = json.loads((directory / "teams.json").read_text(encoding="utf-8"))
    assert "bo3:3:20" in teams, "team not present in this fetch's page range must be preserved, not dropped"
    assert teams["bo3:3:20"]["name"] == "Old Team B"
    assert teams["bo3:3:30"]["name"] == "New Team C"

    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["match_count"] == 2
    import hashlib

    assert (
        manifest["matches_sha256"] == hashlib.sha256((directory / "matches.jsonl").read_bytes()).hexdigest()
    )


def test_validation_is_chronological_versioned_and_never_promotes_baseline(tmp_path) -> None:
    directory = tmp_path / "esports/lol"
    directory.mkdir(parents=True)
    start = datetime(2023, 1, 1, tzinfo=UTC)
    rows = []
    for index in range(600):
        winner = "bo3:3:a" if index % 4 else "bo3:3:b"
        rows.append(
            {
                "match_id": f"bo3:{index}",
                "title": "lol",
                "start_utc": (start + timedelta(hours=index)).isoformat().replace("+00:00", "Z"),
                "team1_id": "bo3:3:a",
                "team2_id": "bo3:3:b",
                "winner_id": winner,
            }
        )
    matches_path = directory / "matches.jsonl"
    matches_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    digest = hashlib.sha256(matches_path.read_bytes()).hexdigest()
    (directory / "manifest.json").write_text(json.dumps({"matches_sha256": digest}), encoding="utf-8")

    result = validate_esports_baseline(tmp_path, "lol", tmp_path / "artifacts")

    assert result["status"] == "ok"
    assert result["chronological_split"]["train"]["n"] == 360
    assert result["chronological_split"]["locked_test"]["n"] == 120
    assert result["promotion_eligible"] is False
    assert result["units"] == 0
    artifact = json.loads((tmp_path / "artifacts/lol-tiered-elo-v6.json").read_text())
    assert artifact["qualified_for_betting"] is False
    assert artifact["model_state"] == "research"
    assert artifact["artifact_hash"] == result["artifact_hash"]
    # Production-model audit gap #2 (2026-08-15): champion evidence must
    # live INSIDE the artifact — the v8-style structured blocks.
    assert set(artifact["training"]) == {"coefficient_fit", "threshold_selection", "locked_holdout"}
    assert artifact["training"]["coefficient_fit"]["observations"] == 360
    assert artifact["training"]["locked_holdout"]["observations"] == 120
    assert "locked_test_all_matches" in artifact["qualification"]
    assert artifact["qualification"]["qualified"] is False


def test_forecast_requires_exact_identity_and_remains_zero_unit(tmp_path) -> None:
    data = tmp_path / "data/esports/lol"
    artifacts = tmp_path / "artifacts"
    data.mkdir(parents=True)
    artifacts.mkdir()
    (data / "teams.json").write_text(
        json.dumps(
            {
                "a": {"name": "Alpha Esports", "slug": "alpha-esports", "acronym": "ALP"},
                "b": {"name": "Beta Gaming", "slug": "beta-gaming", "acronym": "BET"},
            }
        )
    )
    (artifacts / "lol-tiered-elo-v6.json").write_text(
        json.dumps(
            {
                "model_version": "lol-tiered-elo-v6",
                "trained_through_utc": "2026-07-18T00:00:00Z",
                "artifact_hash": "hash",
                "k": 20,
                "ratings": {"a": 1600, "b": 1400},
                "platt_intercept": None,
                "platt_slope": None,
                "confidence_threshold": 0.10,
            }
        )
    )

    class FakeClient:
        def slate(self, league, game_date, timezone_name):
            assert league == "LOL"
            return [
                {
                    "event_id": "event",
                    "event_start_utc": "2099-01-01T12:00:00Z",
                    "markets": [
                        {
                            "market_type": "moneyline",
                            "market_slug": "market",
                            "sides": [
                                {"description": "Alpha Esports"},
                                {"description": "Beta Gaming"},
                            ],
                        }
                    ],
                }
            ]

        def snapshot(self, slug):
            return {
                "observed_at_utc": "2026-07-18T01:00:00Z",
                "long": {"description": "Alpha Esports", "ask": 0.7},
                "short": {"description": "Beta Gaming", "ask": 0.31},
            }

    result = forecast_esports_slate(tmp_path / "data", artifacts, "lol", "2099-01-01", client=FakeClient())

    assert result["priced_count"] == 1
    assert result["priced_contracts"][0]["qualification"] == "NO_CALL_MODEL_UNVALIDATED"
    assert result["priced_contracts"][0]["units"] == 0
    assert result["priced_contracts"][0]["source_teams_resolved"] is True
    assert result["priced_contracts"][0]["source_teams_trained"] is True
    assert result["priced_contracts"][0]["gated_research_eligible"] is True
    from model_prediction.esports_replay import build_snapshot, replay_inputs

    evidence = build_snapshot(result["priced_contracts"][0])
    assert replay_inputs(evidence["inference_inputs"]) == evidence["probabilities"]


def test_forecast_esports_prices_map_spread_and_totals_derivatives(tmp_path) -> None:
    data = tmp_path / "data/esports/lol"
    artifacts = tmp_path / "artifacts"
    data.mkdir(parents=True)
    artifacts.mkdir()
    (data / "teams.json").write_text(
        json.dumps(
            {
                "a": {"name": "Alpha Esports", "slug": "alpha-esports", "acronym": "ALP"},
                "b": {"name": "Beta Gaming", "slug": "beta-gaming", "acronym": "BET"},
            }
        )
    )
    (artifacts / "lol-tiered-elo-v6.json").write_text(
        json.dumps(
            {
                "model_version": "lol-tiered-elo-v6",
                "trained_through_utc": "2026-07-18T00:00:00Z",
                "artifact_hash": "hash",
                "k": 20,
                "ratings": {"a": 1600, "b": 1400},
                "platt_intercept": None,
                "platt_slope": None,
                "confidence_threshold": 0.10,
            }
        )
    )

    class FakeClient:
        def slate(self, league, game_date, timezone_name):
            return [
                {
                    "event_id": "event-1",
                    "event_start_utc": "2099-01-01T12:00:00Z",
                    "markets": [
                        {
                            "market_type": "moneyline",
                            "market_slug": "market-ml",
                            "sides": [
                                {"description": "Alpha Esports"},
                                {"description": "Beta Gaming"},
                            ],
                        },
                        {
                            "market_type": "spread",
                            "market_slug": "market-spread",
                            "line": -1.5,
                            "sides": [
                                {"description": "-1.50"},
                                {"description": "+1.50"},
                            ],
                        },
                        {
                            "market_type": "total",
                            "market_slug": "market-total",
                            "line": 2.5,
                            "sides": [
                                {"description": "Over"},
                                {"description": "Under"},
                            ],
                        },
                    ],
                }
            ]

        def snapshot(self, slug):
            if slug == "market-ml":
                return {
                    "observed_at_utc": "2026-07-18T01:00:00Z",
                    "long": {"description": "Alpha Esports", "ask": 0.70},
                    "short": {"description": "Beta Gaming", "ask": 0.31},
                }
            elif slug == "market-spread":
                return {
                    "observed_at_utc": "2026-07-18T01:00:00Z",
                    "long": {"description": "-1.50", "ask": 0.45},
                    "short": {"description": "+1.50", "ask": 0.58},
                }
            elif slug == "market-total":
                return {
                    "observed_at_utc": "2026-07-18T01:00:00Z",
                    "long": {"description": "Over", "ask": 0.52},
                    "short": {"description": "Under", "ask": 0.50},
                }
            raise KeyError(slug)

    result = forecast_esports_slate(tmp_path / "data", artifacts, "lol", "2099-01-01", client=FakeClient())
    assert result["priced_count"] == 3
    market_types = {c["market_type"] for c in result["priced_contracts"]}
    assert market_types == {"moneyline", "spread", "total"}

    # Check spread contract
    spread_contract = next(c for c in result["priced_contracts"] if c["market_type"] == "spread")
    assert spread_contract["model_version"] == "lol-spread-tiered-elo-v6"
    assert spread_contract["line"] == -1.5
    assert len(spread_contract["sides"]) == 2
    long_side = next(s for s in spread_contract["sides"] if s["side"] == "long")
    assert long_side["model_probability"] > 0

    # Check total contract
    total_contract = next(c for c in result["priced_contracts"] if c["market_type"] == "total")
    assert total_contract["model_version"] == "lol-total-tiered-elo-v6"
    assert total_contract["line"] == 2.5
    over_side = next(s for s in total_contract["sides"] if s["side"] == "long")
    under_side = next(s for s in total_contract["sides"] if s["side"] == "short")
    assert round(over_side["model_probability"] + under_side["model_probability"], 4) == 1.0


def test_forecast_esports_prices_map_spread_anchored_to_team2(tmp_path) -> None:
    data = tmp_path / "data/esports/lol"
    artifacts = tmp_path / "artifacts"
    data.mkdir(parents=True)
    artifacts.mkdir()
    (data / "teams.json").write_text(
        json.dumps(
            {
                "a": {"name": "Alpha Esports", "slug": "alpha-esports", "acronym": "ALP"},
                "b": {"name": "Beta Gaming", "slug": "beta-gaming", "acronym": "BET"},
            }
        )
    )
    (artifacts / "lol-tiered-elo-v6.json").write_text(
        json.dumps(
            {
                "model_version": "lol-tiered-elo-v6",
                "trained_through_utc": "2026-07-18T00:00:00Z",
                "artifact_hash": "hash",
                "k": 20,
                "ratings": {"a": 1600, "b": 1400},
                "platt_intercept": None,
                "platt_slope": None,
                "confidence_threshold": 0.10,
            }
        )
    )

    class FakeClient:
        def slate(self, league, game_date, timezone_name):
            return [
                {
                    "event_id": "event-1",
                    "event_start_utc": "2099-01-01T12:00:00Z",
                    "markets": [
                        {
                            "market_type": "moneyline",
                            "market_slug": "market-ml",
                            "sides": [
                                {"description": "Alpha Esports"},
                                {"description": "Beta Gaming"},
                            ],
                        },
                        {
                            "market_type": "spread",
                            "market_slug": "market-spread-t2-pos",
                            "line": 1.5,
                            "sides": [
                                {"description": "+1.50"},
                                {"description": "-1.50"},
                            ],
                        },
                        {
                            "market_type": "spread",
                            "market_slug": "market-spread-t2-neg",
                            "line": -1.5,
                            "sides": [
                                {"description": "-1.50"},
                                {"description": "+1.50"},
                            ],
                        },
                    ],
                }
            ]

        def snapshot(self, slug):
            if slug == "market-ml":
                return {
                    "observed_at_utc": "2026-07-18T01:00:00Z",
                    "long": {"description": "Alpha Esports", "ask": 0.70},
                    "short": {"description": "Beta Gaming", "ask": 0.31},
                }
            elif slug == "market-spread-t2-pos":
                return {
                    "observed_at_utc": "2026-07-18T01:00:00Z",
                    "team": "Beta Gaming",
                    "line": 1.5,
                    "long": {"description": "+1.50", "ask": 0.55},
                    "short": {"description": "-1.50", "ask": 0.48},
                }
            elif slug == "market-spread-t2-neg":
                return {
                    "observed_at_utc": "2026-07-18T01:00:00Z",
                    "team": "Beta Gaming",
                    "line": -1.5,
                    "long": {"description": "-1.50", "ask": 0.35},
                    "short": {"description": "+1.50", "ask": 0.68},
                }
            raise KeyError(slug)

    result = forecast_esports_slate(tmp_path / "data", artifacts, "lol", "2099-01-01", client=FakeClient())
    assert result["priced_count"] == 3

    # Beta +1.5 market
    c_pos = next(c for c in result["priced_contracts"] if c["market_slug"] == "market-spread-t2-pos")
    long_pos = next(s for s in c_pos["sides"] if s["side"] == "long")
    short_pos = next(s for s in c_pos["sides"] if s["side"] == "short")
    assert "Beta Gaming +1.5" in long_pos["team"]
    assert "Alpha Esports -1.5" in short_pos["team"]
    assert round(long_pos["model_probability"] + short_pos["model_probability"], 4) == 1.0

    # Beta -1.5 market
    c_neg = next(c for c in result["priced_contracts"] if c["market_slug"] == "market-spread-t2-neg")
    long_neg = next(s for s in c_neg["sides"] if s["side"] == "long")
    short_neg = next(s for s in c_neg["sides"] if s["side"] == "short")
    assert "Beta Gaming -1.5" in long_neg["team"]
    assert "Alpha Esports +1.5" in short_neg["team"]
    assert round(long_neg["model_probability"] + short_neg["model_probability"], 4) == 1.0


def test_log_and_settle_esports_derivatives(tmp_path, monkeypatch) -> None:
    from model_prediction.cli import _log_esports_forecast, _settle_esports_pick
    from model_prediction.data_sources import polymarket_us
    from model_prediction.ledger import PickLedger

    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    config = {
        "models": {
            "LOL": {
                "status": "research",
                "min_edge": 0.02,
                "research_confidence_gate": 0.0,
            }
        },
        "project": {"maximum_data_age_hours": 24, "maximum_unreviewed_market_disagreement": 0.50},
        "bankroll": {"base_unit_dollars": 10.0},
    }

    forecast = {
        "title": "lol",
        "model_version": "lol-tiered-elo-v6",
        "priced_contracts": [
            {
                "event_id": "event-1",
                "event_start_utc": "2099-01-01T12:00:00Z",
                "market_slug": "lol-spread-1",
                "market_type": "spread",
                "line": -1.5,
                "teams": ["Alpha Esports", "Beta Gaming"],
                "model_version": "lol-spread-tiered-elo-v6",
                "artifact_hash": "hash1",
                "gated_research_eligible": True,
                "observed_at_utc": "2026-07-18T01:00:00Z",
                "sides": [
                    {
                        "side": "long",
                        "team": "Alpha Esports -1.5",
                        "model_probability": 0.65,
                        "executable_ask": 0.50,
                    },
                    {
                        "side": "short",
                        "team": "Beta Gaming +1.5",
                        "model_probability": 0.35,
                        "executable_ask": 0.55,
                    },
                ],
            },
            {
                "event_id": "event-1",
                "event_start_utc": "2099-01-01T12:00:00Z",
                "market_slug": "lol-total-1",
                "market_type": "total",
                "line": 2.5,
                "teams": ["Alpha Esports", "Beta Gaming"],
                "model_version": "lol-total-tiered-elo-v6",
                "artifact_hash": "hash1",
                "gated_research_eligible": True,
                "observed_at_utc": "2026-07-18T01:00:00Z",
                "sides": [
                    {"side": "long", "team": "Over", "model_probability": 0.60, "executable_ask": 0.45},
                    {"side": "short", "team": "Under", "model_probability": 0.40, "executable_ask": 0.60},
                ],
            },
        ],
    }

    logged = _log_esports_forecast(forecast, config, ledger)
    assert logged == 2

    rows = ledger.rows()
    spread_row = next(r for r in rows if r["market_type"] == "spread")
    assert spread_row["selection"] == "home"
    assert float(spread_row["line"]) == -1.5
    assert spread_row["home_team"] == "Alpha Esports"
    assert spread_row["away_team"] == "Beta Gaming"

    total_row = next(r for r in rows if r["market_type"] == "total")
    assert total_row["selection"] == "over"
    assert float(total_row["line"]) == 2.5

    # Test settlement with binary prices [0.0, 1.0]
    class MockResolvedClient:
        def __init__(self, winner_desc, loser_desc):
            self._sides = [
                {"description": winner_desc, "price": "1.0"},
                {"description": loser_desc, "price": "0.0"},
            ]

        def market(self, slug):
            return {"marketSides": self._sides}

        def book(self, slug):
            return {"state": "MARKET_STATE_EXPIRED"}

    # Settle spread: Alpha Esports -1.5 wins (home sweeps 2-0)
    monkeypatch.setattr(
        polymarket_us,
        "PolymarketUSClient",
        lambda: MockResolvedClient("Alpha Esports -1.5", "Beta Gaming +1.5"),
    )
    res_spread = _settle_esports_pick(spread_row, ledger)
    assert res_spread is not None and res_spread.get("settled") is True
    assert res_spread["result"] == "win"

    # Settle total: Over wins (series went 2-1)
    monkeypatch.setattr(
        polymarket_us,
        "PolymarketUSClient",
        lambda: MockResolvedClient("Over", "Under"),
    )
    res_total = _settle_esports_pick(total_row, ledger)
    assert res_total is not None and res_total.get("settled") is True
    assert res_total["result"] == "win"
