"""Unit tests for Dashboard CLV and BBO Capture Health endpoints."""

from __future__ import annotations

from model_prediction.dashboard.status import _capture_health_summary, _clv_summary


def test_clv_summary_empty_defaults() -> None:
    res = _clv_summary()
    assert "count" in res
    assert "mean_clv_pct" in res
    assert "beat_close_rate" in res
    assert "series" in res


def test_capture_health_summary() -> None:
    health = _capture_health_summary()
    assert "generated_at" in health
    assert "sports" in health
