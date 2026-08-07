"""Regression tests for real bugs found while running MLBCollector against
live data during the Checkpoint 4 takeover (see outputs/rebuild/takeover_status.md).

Both bugs were silent: no exception surfaced, callers just got empty results.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import polars as pl

from model_prediction.rebuild import MetadataDB, NormalizedStore
from model_prediction.rebuild.collectors import MLBCollector


class FakePolymarketClient:
    """Records the type of the game_date argument .slate() was called with."""

    def __init__(self) -> None:
        self.received_game_date: object = None

    def slate(self, league: str, game_date: object) -> list[dict]:
        self.received_game_date = game_date
        return []


class TestPolymarketDateType:
    """PolymarketUSClient.slate() compares a real `date` object against its
    game_date argument. Passing a raw str means `date_obj != "2026-08-06"` is
    unconditionally True for every event, so the slate is always empty —
    Polymarket collection silently returned zero markets for every sport,
    forever, with no error. Regression: every .slate() call site in
    collectors.py must pass a `date` instance, not a str.
    """

    def test_mlb_collect_polymarket_books_passes_a_date_object(self, tmp_path):
        meta = MetadataDB(tmp_path / "metadata.db")
        collector = MLBCollector(tmp_path / "data", meta)
        fake_client = FakePolymarketClient()

        with patch(
            "model_prediction.data_sources.polymarket_us.PolymarketUSClient",
            return_value=fake_client,
        ):
            collector.collect_polymarket_books("2026-08-06")

        assert isinstance(fake_client.received_game_date, date), (
            "collect_polymarket_books must convert the string game_date to a "
            "date object before calling .slate() — passing a str makes the "
            "internal `.date() != game_date` comparison always False"
        )
        assert fake_client.received_game_date == date(2026, 8, 6)


class TestVenuesForDate:
    """_venues_for_date() selected a `venue_id` column that doesn't exist in
    the normalized MLB scoreboard schema (only `venue` does). The resulting
    ColumnNotFoundError was swallowed by a bare `except Exception: return []`,
    so weather collection received zero venues for every date, always —
    data/rebuild/raw/open_meteo never existed. Regression: a normalized
    scoreboard table with only real columns (no venue_id) must still yield
    venues.
    """

    def test_venues_for_date_works_without_a_venue_id_column(self, tmp_path):
        meta = MetadataDB(tmp_path / "metadata.db")
        norm = NormalizedStore(tmp_path / "data" / "normalized")
        # Real schema has no venue_id column — only venue. See collectors.py's
        # collect_espn_scoreboard for the actual row shape.
        df = pl.DataFrame({
            "event_start_utc": ["2026-08-06T01:40Z"],
            "venue": ["T-Mobile Park"],
        })
        norm.write("mlb", "scoreboard", df)

        collector = MLBCollector(tmp_path / "data", meta)
        venues = collector._venues_for_date("2026-08-06")

        assert venues, (
            "_venues_for_date must not silently return [] when the "
            "normalized table has the real (venue-only) schema"
        )
        assert venues[0]["id"] == "T-Mobile Park"


class FakeWeatherResponse:
    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {"hourly": {}}


class TestWeatherForecastEndpoint:
    """collect_weather_forecast() called Open-Meteo's Archive API
    (archive-api.open-meteo.com) — ERA5 *reanalysis* of realized weather, not
    a forecast, and it 400s for any date without several days of processing
    lag (verified live: both real venue requests for today's date returned
    400). Beyond the 400s, using realized weather as a "forecast" feature
    would be a genuine train-serving mismatch against a live pipeline that
    only ever has a forecast at inference time. Regression: today-or-future
    dates must hit the live Forecast API; past dates fall back to the
    Historical Forecast API (a disclosed approximation, not the broken
    Archive/reanalysis endpoint).
    """

    def test_future_date_uses_live_forecast_api(self, tmp_path):
        meta = MetadataDB(tmp_path / "metadata.db")
        collector = MLBCollector(tmp_path / "data", meta)
        captured = {}

        def fake_get(url, params=None, timeout=None):
            captured["url"] = url
            return FakeWeatherResponse()

        with patch("httpx.get", side_effect=fake_get):
            result = collector.collect_weather_forecast("2099-01-01", 40.0, -75.0, "test_venue")

        assert captured["url"] == "https://api.open-meteo.com/v1/forecast"
        assert result["endpoint"] == "live_forecast"

    def test_past_date_uses_historical_forecast_api_not_archive(self, tmp_path):
        meta = MetadataDB(tmp_path / "metadata.db")
        collector = MLBCollector(tmp_path / "data", meta)
        captured = {}

        def fake_get(url, params=None, timeout=None):
            captured["url"] = url
            return FakeWeatherResponse()

        with patch("httpx.get", side_effect=fake_get):
            result = collector.collect_weather_forecast("2000-01-01", 40.0, -75.0, "test_venue")

        assert captured["url"] == "https://historical-forecast-api.open-meteo.com/v1/forecast"
        assert "archive-api" not in captured["url"]
        assert result["endpoint"] == "historical_forecast_stitched"


class FakePolymarketClientWithRealShape:
    """Returns data shaped like PolymarketUSClient._normalize_event's real
    output (event_id/market_id/market_type keys, per-side prices in a
    "sides" list) — not the event["id"]/market["bestBid"] shape the
    collector used to assume, which doesn't exist on the real object at all
    and silently produced 132 "successful" books that were empty shells
    (null price, empty event_id/market_id) every field except line."""

    def slate(self, league, game_date):
        return [{
            "event_id": "70535",
            "event_start_utc": "2026-08-06T23:00:00+00:00",
            "markets": [{
                "market_id": "350520",
                "market_type": "moneyline",
                "line": None,
                "sides": [
                    {"side_id": "1", "selection": "away", "team": "Los Angeles Angels",
                     "line": None, "price_probability": 0.395, "decimal_odds": 2.53, "american_odds": 153},
                    {"side_id": "2", "selection": "home", "team": "Baltimore Orioles",
                     "line": None, "price_probability": 0.61, "decimal_odds": 1.64, "american_odds": -156},
                ],
            }],
        }]


class TestPolymarketRealDataShape:
    """collect_polymarket_books() read event.get("id")/market.get("id")/
    market.get("type")/market.get("bestBid")/market.get("bestAsk") — none of
    which exist on PolymarketUSClient._normalize_event's real return shape
    (event_id/market_id/market_type, with per-side prices under "sides").
    Every field except "line" silently resolved to "" or None for every
    real book collected, with no exception and status: "ok". Regression:
    real per-side prices and identifiers must actually reach the stored row.
    """

    def test_real_shaped_market_produces_real_prices_not_nulls(self, tmp_path):
        meta = MetadataDB(tmp_path / "metadata.db")
        collector = MLBCollector(tmp_path / "data", meta)

        with patch(
            "model_prediction.data_sources.polymarket_us.PolymarketUSClient",
            return_value=FakePolymarketClientWithRealShape(),
        ):
            result = collector.collect_polymarket_books("2026-08-06")

        assert result["status"] == "ok"
        assert result["books"] == 2
        df = pl.read_parquet(collector.markets.path("mlb", "2026-08-06"))
        assert df["executable_price"].null_count() == 0, (
            "executable_price must be populated from side['price_probability'], "
            "not silently null from a nonexistent market['bestAsk'] key"
        )
        assert set(df["event_id"].to_list()) == {"70535"}
        assert set(df["team_or_side"].to_list()) == {"home", "away"}
        home_row = df.filter(pl.col("team_or_side") == "home")
        assert home_row["team"][0] == "Baltimore Orioles"
        assert home_row["executable_price"][0] == 0.61


class FakeESPNClient:
    def scoreboard(self, game_date):
        return {"events": [{
            "id": "401816384", "date": "2026-07-20T22:35Z",
            "competitions": [{
                "status": {"type": {"name": "STATUS_FINAL"}},
                "venue": {"fullName": "Oriole Park at Camden Yards"},
                "competitors": [
                    {"homeAway": "home", "team": {"displayName": "Baltimore Orioles"}, "score": "3"},
                    {"homeAway": "away", "team": {"displayName": "Los Angeles Angels"}, "score": "1"},
                ],
            }],
        }]}


class TestESPNScoreboardSnapshotHash:
    """Real bug found live while backfilling more history for the
    Foundation Completion pass: every collect_espn_scoreboard (MLB, NBA,
    NFL, Soccer, Tennis) did
    `self.raw.write(...).snapshot_hash.snapshot_hash` — RawStore.write()
    returns a RawSnapshotRef whose `.snapshot_hash` field is already the
    hash string, so the second `.snapshot_hash` access raised
    AttributeError on `str`. This crashed every real call — confirmed live
    calling collect_espn_scoreboard('2026-07-15') before the fix. Nothing
    upstream of the direct call site caught it.
    """

    def test_collect_espn_scoreboard_does_not_raise(self, tmp_path):
        meta = MetadataDB(tmp_path / "metadata.db")
        collector = MLBCollector(tmp_path / "data", meta)

        with patch(
            "model_prediction.data_sources.espn.ESPNMLBClient",
            return_value=FakeESPNClient(),
        ):
            result = collector.collect_espn_scoreboard("2026-07-20")

        assert result["status"] == "ok"
        assert result["games"] == 1


class FakeESPNClientWithTeamIds:
    """Real ESPN payloads always carry a stable numeric team.id alongside
    displayName (verified against real collected data under
    data/rebuild/raw/espn_public/) — this fixture matches that real shape,
    unlike FakeESPNClient above which predates identity wiring."""

    def scoreboard(self, game_date):
        return {"events": [{
            "id": "401816384", "date": "2026-07-20T22:35Z",
            "competitions": [{
                "status": {"type": {"name": "STATUS_FINAL"}},
                "venue": {"fullName": "Oriole Park at Camden Yards"},
                "competitors": [
                    {"homeAway": "home", "team": {"id": "1", "displayName": "Baltimore Orioles"}, "score": "3"},
                    {"homeAway": "away", "team": {"id": "3", "displayName": "Los Angeles Angels"}, "score": "1"},
                ],
            }],
        }]}


class TestScoreboardCanonicalIdentity:
    """FOUNDATION_COMPLETION.md Phase 4: collect_espn_scoreboard now
    resolves each team through IdentityRegistry using ESPN's real stable
    team.id, not just the display-name columns it already wrote."""

    def test_scoreboard_rows_carry_real_canonical_team_ids(self, tmp_path):
        meta = MetadataDB(tmp_path / "metadata.db")
        collector = MLBCollector(tmp_path / "data", meta)

        with patch(
            "model_prediction.data_sources.espn.ESPNMLBClient",
            return_value=FakeESPNClientWithTeamIds(),
        ):
            collector.collect_espn_scoreboard("2026-07-20")

        df = collector.norm.read("mlb", "scoreboard")
        row = df.row(0, named=True)
        assert row["home_team_canonical_id"] is not None
        assert row["away_team_canonical_id"] is not None
        assert row["home_team_canonical_id"] != row["away_team_canonical_id"]

        # And the identity is real and independently resolvable, not just
        # a value that happens to be non-null.
        # source_id is sport-namespaced ("espn_public:mlb") to prevent real
        # cross-sport ESPN team-id collisions (e.g. WNBA team id "20" and
        # MLB team id "20" are unrelated real teams) -- see
        # resolve_espn_scoreboard_team_ids()'s docstring for the live bug this fixed.
        resolved = collector.identity.resolve("espn_public:mlb", "1")
        assert resolved is not None
        assert resolved.canonical_name == "Baltimore Orioles"

    def test_rerunning_collection_reuses_the_same_canonical_id(self, tmp_path):
        meta = MetadataDB(tmp_path / "metadata.db")
        collector = MLBCollector(tmp_path / "data", meta)

        with patch(
            "model_prediction.data_sources.espn.ESPNMLBClient",
            return_value=FakeESPNClientWithTeamIds(),
        ):
            collector.collect_espn_scoreboard("2026-07-20")
            first_id = collector.identity.resolve("espn_public:mlb", "1").entity_id

            collector.collect_espn_scoreboard("2026-07-20")
            second_id = collector.identity.resolve("espn_public:mlb", "1").entity_id

        assert first_id == second_id, "rerunning collection must not mint a duplicate canonical identity"


class FakeESPNClientDoubleheader:
    """Two real games between the same two teams on the same calendar
    date, each with its own distinct real ESPN event id -- the real shape
    of a doubleheader."""

    def scoreboard(self, game_date):
        common = {
            "competitions": [{
                "status": {"type": {"name": "STATUS_FINAL"}},
                "venue": {"fullName": "Oriole Park at Camden Yards"},
                "competitors": [
                    {"homeAway": "home", "team": {"id": "1", "displayName": "Baltimore Orioles"}, "score": "3"},
                    {"homeAway": "away", "team": {"id": "3", "displayName": "Los Angeles Angels"}, "score": "1"},
                ],
            }],
        }
        return {"events": [
            {"id": "401816384", "date": "2026-07-20T18:05Z", **common},
            {"id": "401816385", "date": "2026-07-20T22:35Z", **common},
        ]}


class TestScoreboardEventIdentity:
    """Task 1 (event identity): collect_espn_scoreboard now resolves each
    game through IdentityRegistry using ESPN's own real event id, writing
    a real event_canonical_id column additively alongside the existing
    team-identity columns."""

    def test_scoreboard_rows_carry_a_real_canonical_event_id(self, tmp_path):
        meta = MetadataDB(tmp_path / "metadata.db")
        collector = MLBCollector(tmp_path / "data", meta)

        with patch(
            "model_prediction.data_sources.espn.ESPNMLBClient",
            return_value=FakeESPNClientWithTeamIds(),
        ):
            collector.collect_espn_scoreboard("2026-07-20")

        df = collector.norm.read("mlb", "scoreboard")
        row = df.row(0, named=True)
        assert row["event_canonical_id"] is not None
        resolved = collector.identity.resolve("espn_public:mlb", "401816384")
        assert resolved is not None
        assert resolved.entity_id == row["event_canonical_id"]
        assert resolved.entity_type == "event"

    def test_doubleheader_games_get_two_distinct_canonical_event_ids(self, tmp_path):
        meta = MetadataDB(tmp_path / "metadata.db")
        collector = MLBCollector(tmp_path / "data", meta)

        with patch(
            "model_prediction.data_sources.espn.ESPNMLBClient",
            return_value=FakeESPNClientDoubleheader(),
        ):
            result = collector.collect_espn_scoreboard("2026-07-20")

        assert result["games"] == 2
        df = collector.norm.read("mlb", "scoreboard")
        event_canonical_ids = df["event_canonical_id"].to_list()
        assert len(event_canonical_ids) == 2
        assert len(set(event_canonical_ids)) == 2, (
            "a real doubleheader (same two teams, same day, two real ESPN "
            "event ids) must not collapse into one canonical event"
        )

    def test_rerunning_collection_reuses_the_same_canonical_event(self, tmp_path):
        meta = MetadataDB(tmp_path / "metadata.db")
        collector = MLBCollector(tmp_path / "data", meta)

        with patch(
            "model_prediction.data_sources.espn.ESPNMLBClient",
            return_value=FakeESPNClientWithTeamIds(),
        ):
            collector.collect_espn_scoreboard("2026-07-20")
            first_id = collector.identity.resolve("espn_public:mlb", "401816384").entity_id

            collector.collect_espn_scoreboard("2026-07-20")
            second_id = collector.identity.resolve("espn_public:mlb", "401816384").entity_id

        assert first_id == second_id, "rerunning collection must not mint a duplicate canonical event"
