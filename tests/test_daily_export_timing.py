from contextlib import contextmanager

import pytest

from model_prediction.cli import daily


def test_daily_timing_includes_deferred_export(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(daily.time, "monotonic", lambda: clock[0])

    @contextmanager
    def exports():
        try:
            yield
        finally:
            clock[0] += 7.0

    monkeypatch.setattr(daily, "defer_sqlite_xlsx_exports", exports)

    @daily._with_timed_exports
    def pipeline():
        clock[0] += 3.0
        return {"status": "ok", "timing": {"total_seconds": 3.0, "forecast_seconds": 3.0}}

    report = pipeline()
    assert report["timing"] == {"total_seconds": 10.0, "forecast_seconds": 3.0, "xlsx_export_seconds": 7.0}


def test_daily_timing_preserves_failure_and_flushes(monkeypatch):
    flushed = []

    @contextmanager
    def exports():
        try:
            yield
        finally:
            flushed.append(True)

    monkeypatch.setattr(daily, "defer_sqlite_xlsx_exports", exports)

    @daily._with_timed_exports
    def pipeline():
        raise ValueError("pipeline failed")

    with pytest.raises(ValueError, match="pipeline failed"):
        pipeline()
    assert flushed == [True]
