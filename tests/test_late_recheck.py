from datetime import UTC, datetime, timedelta

from model_prediction.late_recheck import (
    ADMIT,
    BELOW_MIN,
    NO_QUOTE,
    STALE_QUOTE,
    STARTED,
    STILL_ABOVE_MAX,
    TOO_EARLY,
    BlockedPick,
    recheck_blocked_picks,
)

NOW = datetime(2026, 10, 21, 18, 0, tzinfo=UTC)


def _pick(minutes_to_start: int, prob: float = 0.60, eid: str = "e1") -> BlockedPick:
    start = (NOW + timedelta(minutes=minutes_to_start)).isoformat()
    return BlockedPick(eid, prob, start, first_edge=0.12)


def _quote(ask: float, age_min: int = 2) -> dict:
    return {"executable_ask": ask, "observed_at_utc": (NOW - timedelta(minutes=age_min)).isoformat()}


def _one(pick, quotes):
    (decision,) = recheck_blocked_picks([pick], quotes, NOW)
    return decision


def test_admits_when_market_moved_back_inside_band():
    d = _one(_pick(30), {"e1": _quote(0.55)})
    assert d.decision == ADMIT
    assert round(d.fresh_edge, 4) == 0.05
    assert d.fresh_ask == 0.55


def test_persistent_overedge_is_not_admitted():
    assert _one(_pick(30), {"e1": _quote(0.50)}).decision == STILL_ABOVE_MAX


def test_edge_below_min_is_rejected():
    assert _one(_pick(30), {"e1": _quote(0.59)}).decision == BELOW_MIN


def test_band_boundaries_are_inclusive():
    assert _one(_pick(30, prob=0.60), {"e1": _quote(0.52)}).decision == ADMIT
    assert _one(_pick(30, prob=0.60), {"e1": _quote(0.58)}).decision == ADMIT


def test_outside_window_started_and_missing_or_stale_quote():
    assert _one(_pick(90), {"e1": _quote(0.55)}).decision == TOO_EARLY
    assert _one(_pick(-5), {"e1": _quote(0.55)}).decision == STARTED
    assert _one(_pick(30), {}).decision == NO_QUOTE
    assert _one(_pick(30), {"e1": _quote(0.55, age_min=11)}).decision == STALE_QUOTE
    assert _one(_pick(30), {"e1": {"executable_ask": 0.55}}).decision == STALE_QUOTE


def test_order_preserved_for_multiple_picks():
    picks = [_pick(30, eid="a"), _pick(30, eid="b")]
    out = recheck_blocked_picks(picks, {"a": _quote(0.55), "b": _quote(0.50)}, NOW)
    assert [(d.event_id, d.decision) for d in out] == [("a", ADMIT), ("b", STILL_ABOVE_MAX)]
