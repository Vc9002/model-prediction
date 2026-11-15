"""The NCAAF forward-capture step in the daily run is optional and fail-soft (quick 261115-w7k)."""

from model_prediction.cli import daily
from model_prediction.data_sources import espn_cfb_pregame_capture as capture


def test_capture_failure_is_returned_not_raised(monkeypatch):
    monkeypatch.setattr(daily, "is_sport_daily_enabled", lambda config, sport: True)

    def boom(*args, **kwargs):
        raise RuntimeError("espn down")

    monkeypatch.setattr(capture, "capture_cfb_pregame_snapshots", boom)
    assert daily._run_cfb_pregame_capture({}, "unused", "2026-11-15") == {"status": "error"}


def test_disabled_ncaaf_skips_without_calling_capture(monkeypatch):
    monkeypatch.setattr(daily, "is_sport_daily_enabled", lambda config, sport: False)

    def must_not_run(*args, **kwargs):
        raise AssertionError("capture ran for a disabled sport")

    monkeypatch.setattr(capture, "capture_cfb_pregame_snapshots", must_not_run)
    result = daily._run_cfb_pregame_capture({}, "unused", "2026-11-15")
    assert result["status"] == "skipped"


def test_error_status_is_not_a_material_daily_error():
    report = {"step1m_cfb_pregame_capture": {"status": "error"}}
    assert daily._material_daily_errors(report) == []
