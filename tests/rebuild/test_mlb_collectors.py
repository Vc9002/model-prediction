"""Regression tests for real bugs found while running MLBCollector against
live data during the Checkpoint 4 takeover (see outputs/rebuild/takeover_status.md).

Both bugs were silent: no exception surfaced, callers just got empty results.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import polars as pl
import pytest

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
