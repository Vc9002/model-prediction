"""Unit tests for TimeContext PIT guardrail."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from model_prediction.core.time_context import PointInTimeLeakageError, TimeContext


@pytest.mark.unit
def test_time_context_creation_and_immutability():
    now_utc = datetime(2026, 11, 7, 18, 0, 0, tzinfo=UTC)
    event_start = datetime(2026, 11, 7, 19, 0, 0, tzinfo=UTC)
    ctx = TimeContext.of(decision_time=now_utc, event_start=event_start)

    assert ctx.decision_time_utc == now_utc
    assert ctx.event_start_utc == event_start

    # Check immutability (frozen dataclass)
    with pytest.raises(AttributeError):
        ctx.decision_time_utc = datetime.now(UTC)  # type: ignore[misc]


@pytest.mark.unit
def test_time_context_rejects_naive_datetime():
    naive = datetime(2026, 11, 7, 18, 0, 0, tzinfo=None)  # noqa: DTZ001
    with pytest.raises(ValueError, match="timezone-aware"):
        TimeContext(decision_time_utc=naive)


@pytest.mark.unit
def test_time_context_observability_checks():
    dec_time = datetime(2026, 11, 7, 18, 0, 0, tzinfo=UTC)
    ctx = TimeContext.of(decision_time=dec_time)

    # Observable cases (at or before decision boundary)
    past = dec_time - timedelta(minutes=5)
    exact = dec_time
    assert ctx.is_observable(past) is True
    assert ctx.is_observable(exact) is True
    ctx.assert_observable(past, label="prior_snapshot")
    ctx.assert_observable(exact, label="exact_snapshot")

    # Unobservable cases (future leakage)
    future = dec_time + timedelta(seconds=1)
    assert ctx.is_observable(future) is False
    with pytest.raises(PointInTimeLeakageError, match="PIT Leakage Violation"):
        ctx.assert_observable(future, label="future_lineup")

    # None handling
    assert ctx.is_observable(None) is False
    with pytest.raises(PointInTimeLeakageError, match="Missing observation timestamp"):
        ctx.assert_observable(None, label="missing_timestamp")


@pytest.mark.unit
def test_time_context_serialization():
    dec_str = "2026-11-07T18:00:00+00:00"
    start_str = "2026-11-07T19:00:00+00:00"
    ctx = TimeContext.of(decision_time=dec_str, event_start=start_str)

    data = ctx.to_dict()
    assert data["decision_time_utc"] == dec_str
    assert data["event_start_utc"] == start_str

    restored = TimeContext.from_dict(data)
    assert restored.decision_time_utc == ctx.decision_time_utc
    assert restored.event_start_utc == ctx.event_start_utc
