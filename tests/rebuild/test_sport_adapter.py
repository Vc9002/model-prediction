"""Tests for the shared sport-adapter protocol (sport_adapter.py,
FOUNDATION_COMPLETION.md Phase 13 / item 5)."""

from __future__ import annotations

import pytest

from model_prediction.rebuild.sport_adapter import (
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
            build_adapter("esports", str(tmp_path))


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

    def test_mlb_predict_market_decide_are_honestly_not_implemented_through_the_shared_adapter(self, tmp_path):
        # scripts/mlb_shadow_run.py is the one proven real path for these
        # stages -- the shared adapter must not claim to have them until
        # that extraction genuinely happens.
        adapter = build_adapter("mlb", str(tmp_path))
        assert adapter.predict("2026-08-06", "late").status == STAGE_NOT_IMPLEMENTED
        assert adapter.match_markets("2026-08-06", "late").status == STAGE_NOT_IMPLEMENTED
        assert adapter.decide("2026-08-06", "late").status == STAGE_NOT_IMPLEMENTED


class TestCollectionOnlyAdapterFailsClosedNotCrashed:
    """Real bug found live: SoccerCollector/TennisCollector call the real
    ESPN client with league codes ("SOCCER"/"TENNIS") that don't exist in
    data_sources/espn.py's LEAGUE_PATHS -- a real ValueError on a real
    call, not a mock. The adapter must report this as an honest per-stage
    ERROR, not crash the whole CLI process."""

    def test_tennis_collect_reports_error_not_crash(self, tmp_path):
        adapter = build_adapter("tennis", str(tmp_path))
        result = adapter.collect("2026-08-06")
        assert result.status == "ERROR"
        assert "error" in result.detail


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
