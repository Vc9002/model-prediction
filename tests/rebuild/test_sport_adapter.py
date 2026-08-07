"""Tests for the shared sport-adapter protocol (sport_adapter.py,
FOUNDATION_COMPLETION.md Phase 13 / item 5)."""

from __future__ import annotations

import pytest

from model_prediction.rebuild.sport_adapter import (
    STAGE_ERROR,
    STAGE_NO_DATA,
    STAGE_NOT_IMPLEMENTED,
    SUPPORTED_SPORTS,
    build_adapter,
)


class TestBuildAdapter:
    def test_every_supported_sport_returns_a_real_adapter(self, tmp_path):
        for sport in SUPPORTED_SPORTS:
            adapter = build_adapter(sport, str(tmp_path))
            assert adapter.sport == sport

    def test_unsupported_sport_fails_closed_not_a_guess(self, tmp_path):
        with pytest.raises(ValueError, match="no adapter registered"):
            build_adapter("kbo", str(tmp_path))

    def test_esports_collect_calls_the_real_stub_and_reports_it_honestly(self, tmp_path):
        # EsportsCollector.collect_date() is a real, honest stub (returns
        # status="stub", does no real network call) -- the adapter must
        # not fabricate SUCCESS for it. Proves the real stub was actually
        # called (not skipped) by checking its own real response note
        # survives into the StageResult.
        adapter = build_adapter("esports", str(tmp_path))
        result = adapter.collect("2026-08-06")
        assert result.status == STAGE_NOT_IMPLEMENTED
        assert result.detail.get("status") == "stub"


class TestHonestNotImplementedStages:
    """The real point of this module: a sport with no real trained model
    must say so honestly through the shared interface, not fabricate a
    prediction."""

    def test_nba_predict_is_honestly_not_implemented(self, tmp_path):
        adapter = build_adapter("nba", str(tmp_path))
        result = adapter.predict("2026-08-06", "late")
        assert result.status == STAGE_NOT_IMPLEMENTED

    def test_nba_match_markets_is_honestly_not_implemented(self, tmp_path):
        adapter = build_adapter("nba", str(tmp_path))
        result = adapter.match_markets("2026-08-06", "late")
        assert result.status == STAGE_NOT_IMPLEMENTED

    def test_nba_decide_is_honestly_not_implemented(self, tmp_path):
        adapter = build_adapter("nba", str(tmp_path))
        result = adapter.decide("2026-08-06", "late")
        assert result.status == STAGE_NOT_IMPLEMENTED

    def test_nba_build_features_is_honestly_not_implemented(self, tmp_path):
        # No real horizon feature builder exists for NBA yet -- must not
        # silently claim MLB's real one applies.
        adapter = build_adapter("nba", str(tmp_path))
        result = adapter.build_features("2026-08-06", "late")
        assert result.status == STAGE_NOT_IMPLEMENTED

    def test_mlb_predict_on_a_cold_empty_data_root_is_honest_no_data_not_a_crash(self, tmp_path):
        # MLB predict/match_markets/decide are now real (mlb_shadow_pipeline.py)
        # -- against a genuinely empty data_root (no scoreboard ever
        # collected), the honest real result is NO_DATA, not a crash and
        # not NOT_IMPLEMENTED (that would misrepresent real code as absent).
        adapter = build_adapter("mlb", str(tmp_path))
        result = adapter.predict("2026-08-06", "late")
        assert result.status == STAGE_NO_DATA

    def test_mlb_match_markets_without_predict_first_fails_closed(self, tmp_path):
        # match_markets() depends on real forecast state predict() builds --
        # calling it first (e.g. a bare --markets-only invocation) must fail
        # closed with a clear reason, not silently operate on nothing.
        adapter = build_adapter("mlb", str(tmp_path))
        result = adapter.match_markets("2026-08-06", "late")
        assert result.status == STAGE_ERROR

    def test_mlb_decide_without_predict_first_fails_closed(self, tmp_path):
        adapter = build_adapter("mlb", str(tmp_path))
        result = adapter.decide("2026-08-06", "late")
        assert result.status == STAGE_ERROR


class TestCollectionOnlyAdapterFailsClosedNotCrashed:
    """Real bug found live, then fixed: SoccerCollector/TennisCollector
    called the real ESPN client with league codes ("SOCCER"/"TENNIS")
    that don't exist in data_sources/espn.py's LEAGUE_PATHS -- a real
    ValueError on a real call, not a mock. Fixed by defaulting to a real
    valid league each (EPL, ATP) instead. This class now covers two
    things: the fix actually works (real network collection succeeds),
    and the adapter's try/except still reports a genuine failure as an
    honest per-stage ERROR rather than crashing, for any future collector
    bug of the same shape."""

    def test_tennis_collect_now_succeeds_with_the_real_default_league(self, tmp_path):
        adapter = build_adapter("tennis", str(tmp_path))
        result = adapter.collect("2026-08-06")
        assert result.status in ("SUCCESS", "NO_DATA")  # real network call; either is a real non-error outcome

    def test_collection_only_adapter_still_reports_a_genuine_collector_failure_as_error(self, tmp_path):
        # Proves the try/except in _CollectionOnlyAdapter.collect() still
        # does its job for a real failure, now that the specific
        # SOCCER/TENNIS bug it was written for is fixed.
        from unittest.mock import MagicMock

        from model_prediction.rebuild.sport_adapter import _CollectionOnlyAdapter

        broken_collector = MagicMock()
        broken_collector.collect_date.side_effect = ValueError("simulated real collector failure")
        adapter = _CollectionOnlyAdapter("tennis", broken_collector)

        result = adapter.collect("2026-08-06")
        assert result.status == "ERROR"
        assert "simulated real collector failure" in result.detail["error"]


class TestMLBRealCollectAndFeatures:
    def test_collect_and_build_features_are_real_not_stubs(self, tmp_path):
        # MLBAdapter.build_features is real code (horizon_builder.py) --
        # confirm it's actually being called, not silently falling back to
        # the mixin's NOT_IMPLEMENTED stub, by checking against an empty
        # data_root: real code returns NO_DATA (zero real games found),
        # the honest-stub mixin would return NOT_IMPLEMENTED instead.
        adapter = build_adapter("mlb", str(tmp_path))
        result = adapter.build_features("2026-08-06", "late")
        assert result.status != STAGE_NOT_IMPLEMENTED

    def test_build_features_with_a_run_id_records_real_ledger_lineage(self, tmp_path):
        # Real gap closed: horizon_builder.py wrote to FeatureStore but
        # that write was never captured in ShadowLedger lineage even
        # though both real methods independently existed.
        from unittest.mock import patch

        from model_prediction.rebuild.shadow_ledger import ShadowLedger

        with patch(
            "model_prediction.rebuild.mlb_features.build_live_game_feature_row",
            return_value={"event_id": "401", "home_sp_avg_velocity": 93.0},
        ), patch(
            "model_prediction.rebuild.horizon_builder.point_in_time_probable_starters",
            return_value={"401": {"home_starter": "A", "away_starter": "B"}},
        ):
            adapter = build_adapter("mlb", str(tmp_path))
            from model_prediction.rebuild.storage import NormalizedStore, provenance_row, utc_now
            norm = NormalizedStore(f"{tmp_path}/normalized")
            row = {
                **provenance_row(source="espn_public", source_record_id="401", source_version="v1",
                                  observed_at_utc=utc_now().isoformat(),
                                  effective_at_utc="2026-08-06T22:10:00+00:00",
                                  event_start_utc="2026-08-06T22:10:00+00:00"),
                "event_id": "401", "home_team": "Seattle Mariners", "away_team": "Detroit Tigers",
                "home_score": 0, "away_score": 0, "status": "STATUS_SCHEDULED", "venue": "",
            }
            norm.write("mlb", "scoreboard", __import__("polars").DataFrame([row]), primary_key=["event_id"])

            # A real run_id must reference a real runs row (foreign key) --
            # matches exactly what rebuild_shadow_cli.py's run() does
            # before calling any adapter stage.
            setup_ledger = ShadowLedger(f"{tmp_path}/shadow.db")
            real_run_id = setup_ledger.record_run("mlb", run_type="test")
            setup_ledger.close()

            result = adapter.build_features("2026-08-06", "late", run_id=real_run_id)
            assert result.status == "SUCCESS"

        ledger = ShadowLedger(f"{tmp_path}/shadow.db")
        rows = ledger.feature_snapshots_for_horizon("mlb", "late")
        ledger.close()
        assert len(rows) == 1
        assert rows[0]["run_id"] == real_run_id
        assert rows[0]["row_count"] == 1
