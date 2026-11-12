"""NFL forecast-path admission and freshness guards (2026-11-11 fix batch)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from model_prediction.cli.forecast import _downgrade_unserved
from model_prediction.cli.forecast_nfl_support import (
    _main_call_eligible,
    _nfl_inputs_fresh,
    _select_nfl_total_market,
)
from model_prediction.domain import RecordType


@dataclass(frozen=True)
class _Eligibility:
    decision: str
    record_type: RecordType
    reason_code: str


def test_downgraded_research_row_is_never_main_eligible() -> None:
    qualified = _Eligibility("CALL", RecordType.QUALIFIED_SHADOW_CALL, "QUALIFIED")
    assert _main_call_eligible(qualified, edge=0.06, min_edge=0.05, max_edge=0.10)
    assert not _main_call_eligible(qualified, edge=0.04, min_edge=0.05, max_edge=0.10)
    assert not _main_call_eligible(qualified, edge=0.11, min_edge=0.05, max_edge=0.10)

    downgraded = _downgrade_unserved(qualified)
    # Still a CALL so Flat keeps the diagnostic row with units ...
    assert downgraded.decision == "CALL"
    assert downgraded.record_type is RecordType.RESEARCH_OBSERVATION
    # ... but Main must refuse it even inside the edge band.
    assert not _main_call_eligible(downgraded, edge=0.06, min_edge=0.05, max_edge=0.10)


def _total_row(slug: str, long_ask: float, observed: str, *, short_ask: float | None = None) -> dict:
    row = {"market_slug": slug, "line": 44.5, "long": {"ask": long_ask}, "observed_at_utc": observed}
    if short_ask is not None:
        row["short"] = {"ask": short_ask}
    return row


def test_total_selector_uses_latest_quote_per_contract_not_the_most_balanced_stale_one() -> None:
    # Real shape from odds/nfl/2026-11-09: the 05:52Z capture read 49/51, the 18:10Z one 53/47.
    rows = [
        _total_row("tsc-nfl-buf-min-total-44pt5", 0.49, "2026-11-09T05:52:00Z", short_ask=0.51),
        _total_row("tsc-nfl-buf-min-total-44pt5", 0.53, "2026-11-09T18:10:00Z", short_ask=0.47),
    ]
    picked = _select_nfl_total_market(rows)
    assert picked is not None
    assert picked["observed_at_utc"] == "2026-11-09T18:10:00Z"
    assert picked["long"]["ask"] == 0.53


def test_total_selector_requires_a_two_sided_quote() -> None:
    # The under branch reads market["short"]["ask"]; a one-sided row used to be selected
    # and then dropped silently when the model leaned under.
    assert (
        _select_nfl_total_market([_total_row("tsc-nfl-x-total-44pt5", 0.50, "2026-11-09T18:10:00Z")]) is None
    )


def test_inputs_fresh_is_an_ordering_and_age_test_not_a_calendar_date_match() -> None:
    # nflverse published the depth chart Tue 15:32Z; the 00:30 ET (05:30Z Wed) run must still qualify.
    run = datetime(2026, 11, 11, 5, 30, tzinfo=UTC)
    common = {
        "qb_update_utc": "2026-11-10T15:32:14Z",
        "last_game_date": date(2026, 11, 8),
        "observed_at": run,
    }
    assert _nfl_inputs_fresh(**common, injury_observed_utc="2026-11-11T05:20:00Z", max_age_hours=12)
    # Injury capture 13h old -> stale.
    assert not _nfl_inputs_fresh(**common, injury_observed_utc="2026-11-10T16:00:00Z", max_age_hours=12)
    # Depth chart published BEFORE the team's last game cannot reflect post-game changes.
    assert not _nfl_inputs_fresh(
        qb_update_utc="2026-11-07T15:00:00Z",
        last_game_date=date(2026, 11, 8),
        injury_observed_utc="2026-11-11T05:20:00Z",
        observed_at=run,
        max_age_hours=12,
    )
    # Anything dated after the decision time is a PIT violation, not freshness.
    assert not _nfl_inputs_fresh(**common, injury_observed_utc="2026-11-11T06:00:00Z", max_age_hours=12)
    assert not _nfl_inputs_fresh(
        qb_update_utc=None,
        last_game_date=None,
        injury_observed_utc="2026-11-11T05:20:00Z",
        observed_at=run,
        max_age_hours=12,
    )
