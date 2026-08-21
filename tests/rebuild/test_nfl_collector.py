"""Regression test for a real, significant bug found via mypy while
addressing FOUNDATION_COMPLETION.md's CI quality-gate item: NFLCollector's
`_collect_espn`, `_collect_markets`, and the real `collect_date` were all
defined *nested inside* a broken outer `def collect_date(self, game_date)`
that never called or returned any of them — it just defined the nested
functions and implicitly returned None. Calling
`NFLCollector.collect_date(...)` silently did nothing and returned None,
with no exception, no error, nothing — every NFL collection attempt on
this branch was a complete no-op. mypy caught it as "Missing return
statement" on the outer (broken) function; the fix de-indents the nested
functions to be real class methods and removes the dead wrapper.
"""

from __future__ import annotations

from unittest.mock import patch

from model_prediction.rebuild import MetadataDB
from model_prediction.rebuild.collectors import NFLCollector


class FakeESPNClient:
    def scoreboard(self, league, game_date):
        return {
            "events": [
                {
                    "id": "1",
                    "date": "2026-08-06T20:00Z",
                    "competitions": [
                        {
                            "status": {"type": {"name": "STATUS_SCHEDULED"}},
                            "venue": {"fullName": "Test Stadium"},
                            "competitors": [
                                {"homeAway": "home", "team": {"displayName": "Home Team"}, "score": "0"},
                                {"homeAway": "away", "team": {"displayName": "Away Team"}, "score": "0"},
                            ],
                        }
                    ],
                }
            ]
        }


class TestNFLCollectorNotSilentlyBroken:
    def test_collect_date_returns_a_real_result_not_none(self, tmp_path):
        meta = MetadataDB(tmp_path / "metadata.db")
        collector = NFLCollector(tmp_path / "data", meta)

        with (
            patch(
                "model_prediction.data_sources.espn.ESPNClient",
                return_value=FakeESPNClient(),
            ),
            patch(
                "model_prediction.data_sources.polymarket_us.PolymarketUSClient",
                side_effect=ImportError,
            ),
        ):
            result = collector.collect_date("2026-08-06")

        assert result is not None, "collect_date silently returned None instead of a real result"
        assert result["status"] in ("ok", "partial")
        assert result["espn"]["games"] == 1

    def test_collect_date_is_a_real_bound_method(self, tmp_path):
        meta = MetadataDB(tmp_path / "metadata.db")
        collector = NFLCollector(tmp_path / "data", meta)

        assert callable(collector.collect_date)
        assert hasattr(collector, "_collect_espn")
        assert hasattr(collector, "_collect_markets")
