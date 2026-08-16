"""Property-based tests for point-in-time correctness.

The single most common bug class in this codebase (see CLAUDE.md) is a
decision at time T using information from after T -- same-day vs. same-
timestamp comparisons, timestamp-capture ordering, future rows leaking into
walk-forward windows. Example-based tests pin specific cases; hypothesis
searches the space around the contract itself:

  * future starts must never change a rolling-era/gap computed at T
  * future market observations must never change a line-movement computed at T
  * name normalization must be idempotent and accent-invariant (the 2026-08-16
    José Soriano bug class: two spellings, one real person, silent miss)

These pin the invariant, not the implementation, so a refactor that keeps
the contract passes and one that breaks the contract fails loudly.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from model_prediction.cli import _identity_key
from model_prediction.features.line_movement import line_movement
from model_prediction.features.starter_history import (
    _baseball_innings,
    _normalize_name,
    starter_era_gap_live,
    starter_rolling_era,
)

BASE = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _snapshot_line(
    game_start: datetime,
    pitcher_name: str,
    innings: str,
    earned_runs: float,
) -> str:
    """One synthetic game_snapshots.jsonl record for a starter."""
    return json.dumps(
        {
            "game_start_utc": game_start.isoformat(),
            "status": "Final",
            "home": {
                "pitcher_order": [1],
                "players": [
                    {
                        "player_id": 1,
                        "name": pitcher_name,
                        "pitching": {
                            "inningsPitched": innings,
                            "earnedRuns": earned_runs,
                            "strikeOuts": 3.0,
                            "baseOnBalls": 1.0,
                            "homeRuns": 0.0,
                            "hitBatsmen": 0.0,
                        },
                    }
                ],
            },
            "away": {"pitcher_order": [], "players": []},
        }
    )


@st.composite
def prior_and_future_starts(draw):
    """A decision time, some strictly-prior starts, and some at/after it.

    Includes a salt so each example gets unique snapshot paths: the
    starter-history module caches its per-path index in a module-level
    dict, so reusing the same path across examples (or across the
    before/after reads within one example) would serve stale cached data
    and the test would pass without ever exercising a fresh read.
    """
    salt = draw(st.integers(min_value=0, max_value=10**9))
    prior = draw(
        st.lists(
            st.tuples(
                st.integers(min_value=2, max_value=400),  # days before decision
                st.integers(min_value=1, max_value=9),  # innings
                st.integers(min_value=0, max_value=6),  # earned runs
            ),
            min_size=2,
            max_size=8,
        )
    )
    future_offsets = draw(
        st.lists(
            st.integers(min_value=0, max_value=50),  # days after decision
            min_size=1,
            max_size=4,
        )
    )
    decision = BASE + timedelta(days=500)
    prior_rows = [
        _snapshot_line(decision - timedelta(days=d), "Test Pitcher", f"{ip}.0", er) for d, ip, er in prior
    ]
    future_rows = [
        _snapshot_line(decision + timedelta(days=d), "Test Pitcher", "9.0", 0.0) for d in future_offsets
    ]
    return salt, decision, prior_rows, future_rows


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(data=prior_and_future_starts())
def test_future_starts_never_change_rolling_era(tmp_path, data):
    """Adding starts at/after the decision must not move the computed ERA."""
    salt, decision, prior_rows, future_rows = data
    # distinct paths per read: distinct cache keys, so the "after" read is
    # genuinely fresh and would see leaked future rows if the filter broke
    path_before = tmp_path / f"snapshots-{salt}-before.jsonl"
    path_after = tmp_path / f"snapshots-{salt}-after.jsonl"
    path_before.write_text("\n".join(prior_rows) + "\n", encoding="utf-8")
    path_after.write_text("\n".join(prior_rows + future_rows) + "\n", encoding="utf-8")

    before = starter_rolling_era("Test Pitcher", decision, snapshot_path=path_before)
    after = starter_rolling_era("Test Pitcher", decision, snapshot_path=path_after)

    assert before["era"] == after["era"]
    assert before["starts"] == after["starts"]


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(data=prior_and_future_starts())
def test_future_starts_never_change_era_gap(tmp_path, data):
    """The gap is home minus away; future home starts must not move it."""
    salt, decision, prior_rows, future_rows = data
    # same synthetic pitcher on both sides, future rows added only to home
    away_rows = [r.replace("Test Pitcher", "Away Pitcher") for r in prior_rows]
    path_before = tmp_path / f"snapshots-{salt}-before.jsonl"
    path_after = tmp_path / f"snapshots-{salt}-after.jsonl"
    path_before.write_text("\n".join(prior_rows + away_rows) + "\n", encoding="utf-8")
    path_after.write_text("\n".join(prior_rows + future_rows + away_rows) + "\n", encoding="utf-8")

    before = starter_era_gap_live("Test Pitcher", "Away Pitcher", decision, snapshot_path=path_before)
    after = starter_era_gap_live("Test Pitcher", "Away Pitcher", decision, snapshot_path=path_after)

    assert before == after


@settings(max_examples=200)
@given(
    whole=st.integers(min_value=0, max_value=15),
    outs=st.integers(min_value=0, max_value=2),
)
def test_baseball_innings_round_trip(whole, outs):
    """ "W.O" notation must decode to W + O/3 innings, exactly."""
    assert _baseball_innings(f"{whole}.{outs}") == whole + outs / 3


@settings(max_examples=200)
@given(text=st.text(alphabet=st.characters(), max_size=40))
def test_normalize_name_idempotent(text):
    once = _normalize_name(text)
    assert _normalize_name(once) == once


@settings(max_examples=200)
@given(text=st.text(alphabet=st.characters(), max_size=40))
def test_identity_key_idempotent(text):
    once = _identity_key(text)
    assert _identity_key(once) == once


@given(
    name=st.sampled_from(
        [
            "José Soriano",
            "Jose Soriano",
            "Jesús Luzardo",
            "Jesus Luzardo",
            "Martín Pérez",
            "Martin Perez",
            "Cristóbal Sánchez",
            "Cristobal Sanchez",
        ]
    )
)
def test_normalize_name_accent_invariant(name):
    import unicodedata

    ascii_version = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))
    assume(ascii_version != name)  # only meaningful for accented inputs
    assert _normalize_name(name) == _normalize_name(ascii_version)


def _movement_snapshot(event_id: str, observed_at: datetime, prob: float) -> str:
    return json.dumps(
        {
            "event_id": event_id,
            "observed_at_utc": observed_at.isoformat(),
            "markets": {
                "moneyline": {
                    "home": {"decision_probability": prob, "market_slug": "aec-mlb-x-y-2026-01-01"},
                }
            },
        }
    )


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    prior_probs=st.lists(st.floats(min_value=0.05, max_value=0.95), min_size=2, max_size=6),
    future_probs=st.lists(st.floats(min_value=0.05, max_value=0.95), min_size=1, max_size=3),
)
def test_line_movement_ignores_future_observations(tmp_path, prior_probs, future_probs):
    """Observations at/after the decision must not move the computed movement."""
    decision = BASE + timedelta(days=10)
    prior_rows = [_movement_snapshot("evt1", BASE + timedelta(days=i), p) for i, p in enumerate(prior_probs)]
    future_rows = [
        _movement_snapshot("evt1", decision + timedelta(days=i), p) for i, p in enumerate(future_probs)
    ]
    path = tmp_path / "odds.jsonl"
    path.write_text("\n".join(prior_rows) + "\n", encoding="utf-8")

    before = line_movement("evt1", "moneyline", "home", decision, snapshot_path=path)

    path.write_text("\n".join(prior_rows + future_rows) + "\n", encoding="utf-8")
    after = line_movement("evt1", "moneyline", "home", decision, snapshot_path=path)

    assert before["status"] == "available"
    assert before["movement"] == after["movement"]
    assert before["observations_before_decision"] == after["observations_before_decision"]
    assert before["movement"] == round(prior_probs[-1] - prior_probs[0], 6)


@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(prior_probs=st.lists(st.floats(min_value=0.05, max_value=0.95), max_size=1))
def test_line_movement_fails_closed_on_thin_data(tmp_path, prior_probs):
    """Fewer than two pre-decision observations must not fabricate movement."""
    decision = BASE + timedelta(days=10)
    rows = [_movement_snapshot("evt2", BASE + timedelta(days=i), p) for i, p in enumerate(prior_probs)]
    path = tmp_path / "odds.jsonl"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    result = line_movement("evt2", "moneyline", "home", decision, snapshot_path=path)

    assert result["status"] in ("insufficient_observations", "unavailable_from_source")
    assert result["movement"] is None
