"""Unit tests for Polymarket US Live Slate Scanner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from model_prediction.dashboard import picks as picks_mod
from model_prediction.portfolio import polymarket_scanner as scanner_mod
from model_prediction.portfolio.polymarket_scanner import PolymarketSlateScanner


def test_parse_snapshot_line():
    scanner = PolymarketSlateScanner(bankroll=1000.0, min_edge=0.025)

    line = json.dumps(
        {
            "event_id": "1001",
            "event_title": "NY Yankees vs Boston Red Sox",
            "league": "MLB",
            "market_id": "m_1001",
            "market_type": "moneyline",
            "long": {"ask": 0.45, "bid": 0.43, "description": "NY Yankees"},
            "short": {"ask": 0.57, "bid": 0.55, "description": "Boston Red Sox"},
            "event_start_utc": "2026-08-22T00:00:00Z",
        }
    )

    req = scanner.parse_snapshot_line(line, require_model=False)
    assert req is not None
    assert req.market_id == "m_1001"
    assert req.league == "MLB"
    assert req.best_ask == 0.45
    assert req.best_bid == 0.43


def test_scan_file_filtering(tmp_path: Path):
    scanner = PolymarketSlateScanner(bankroll=1000.0, min_edge=0.03)

    f_path = tmp_path / "polymarket_snapshots.jsonl"
    lines = [
        # Line 1: Ask 0.45, Model 0.50 (Edge +5% -> qualifies within 0.02-0.08 gate)
        json.dumps(
            {
                "event_id": "1",
                "market_id": "m1",
                "market_type": "moneyline",
                "league": "CS2",
                "long": {"ask": 0.45, "bid": 0.43, "description": "Synthetic Home"},
                "short": {"ask": 0.62, "bid": 0.60, "description": "Synthetic Away"},
                "event_start_utc": "2026-08-22T10:00:00Z",
            }
        ),
        # Line 2: Ask 0.50, Model 0.50 (Edge 0% -> rejected)
        json.dumps(
            {
                "event_id": "2",
                "market_id": "m2",
                "market_type": "moneyline",
                "league": "CS2",
                "long": {"ask": 0.50, "bid": 0.48, "description": "Synthetic Home 2"},
                "short": {"ask": 0.52, "bid": 0.50, "description": "Synthetic Away 2"},
                "event_start_utc": "2026-08-22T12:00:00Z",
            }
        ),
    ]
    f_path.write_text("\n".join(lines))

    # Without require_model, synthetic teams evaluate with fallback
    orders = scanner.scan_file(f_path, require_model=False)
    assert len(orders) == 1
    assert orders[0].market_id == "m1"
    assert orders[0].side == "BUY_YES"
    assert orders[0].edge == pytest.approx(0.05, abs=1e-4)


def test_scan_file_unmodeled_exclusion(tmp_path: Path):
    scanner = PolymarketSlateScanner(bankroll=1000.0, min_edge=0.01)
    f_path = tmp_path / "polymarket_snapshots.jsonl"
    lines = [
        json.dumps(
            {
                "event_id": "unmodeled_99",
                "market_id": "m_unmodeled",
                "market_type": "moneyline",
                "league": "UNKNOWN_SPORT",
                "long": {"ask": 0.30, "bid": 0.28, "description": "Unknown Team A"},
                "short": {"ask": 0.72, "bid": 0.70, "description": "Unknown Team B"},
                "event_start_utc": "2026-08-22T10:00:00Z",
            }
        )
    ]
    f_path.write_text("\n".join(lines))

    # With require_model=True (default), unmodeled games are excluded
    orders = scanner.scan_file(f_path, require_model=True)
    assert len(orders) == 0


def test_logged_picks_index_excludes_legacy_research_tier(monkeypatch):
    # Reset the module-level cache so this test's monkeypatched readers are
    # actually consulted, and so the real cache is restored for later tests.
    monkeypatch.setattr(scanner_mod, "_PICKS_INDEX_CACHE", None)
    monkeypatch.setattr(picks_mod, "read_picks", lambda: [{"pick_id": "M1"}])
    monkeypatch.setattr(picks_mod, "read_flat_picks", lambda: [{"pick_id": "F1"}])
    monkeypatch.setattr(picks_mod, "_parse_research_picks", lambda *_, **__: [{"pick_id": "R1"}])

    picks = scanner_mod._get_logged_picks()

    assert [p["pick_id"] for p in picks] == ["M1", "F1"]


def test_lookup_model_prob_offset_timestamp_same_utc_date_beats_stale_cross_date_pick(monkeypatch):
    # Pick A: an offset-notation timestamp that is the exact same UTC instant/
    # calendar date as the market's "2026-10-20T00:00:00Z" query -- the real
    # "crosses a timezone boundary" case the live-data audit found.
    pick_a = {
        "league": "NBA",
        "market_type": "moneyline",
        "home_team": "Lakers",
        "away_team": "Celtics",
        "event_start_utc": "2026-10-19T20:00:00-0400",
        "model_probability": 0.58,
        "selection": "home",
        "created_at_utc": "2026-10-19T22:00:00Z",
    }
    # Pick B: a genuinely different, later real game for the same two teams,
    # with a LATER created_at_utc than Pick A -- the pre-fix tie-break on
    # created_at_utc alone would wrongly prefer this stale cross-date pick.
    pick_b = {
        "league": "NBA",
        "market_type": "moneyline",
        "home_team": "Lakers",
        "away_team": "Celtics",
        "event_start_utc": "2026-10-25T17:05:00Z",
        "model_probability": 0.41,
        "selection": "home",
        "created_at_utc": "2026-10-25T10:00:00Z",
    }
    monkeypatch.setattr(scanner_mod, "_get_logged_picks", lambda: [pick_a, pick_b])

    result = scanner_mod._lookup_model_prob(
        "NBA",
        "Lakers",
        "Celtics",
        event_start_utc="2026-10-20T00:00:00Z",
        market_type="moneyline",
        calibrate=False,
    )

    assert result == pytest.approx(0.58)


def test_lookup_model_prob_returns_none_when_only_cross_date_pick_exists(monkeypatch):
    # Same query as above, but only the genuinely-different-date pick exists --
    # no same-UTC-date candidate at all. The fix must fail closed (None), not
    # fall back to the stale cross-date pick.
    pick_b = {
        "league": "NBA",
        "market_type": "moneyline",
        "home_team": "Lakers",
        "away_team": "Celtics",
        "event_start_utc": "2026-10-25T17:05:00Z",
        "model_probability": 0.41,
        "selection": "home",
        "created_at_utc": "2026-10-25T10:00:00Z",
    }
    monkeypatch.setattr(scanner_mod, "_get_logged_picks", lambda: [pick_b])

    result = scanner_mod._lookup_model_prob(
        "NBA",
        "Lakers",
        "Celtics",
        event_start_utc="2026-10-20T00:00:00Z",
        market_type="moneyline",
        calibrate=False,
    )

    assert result is None


def test_lookup_model_prob_same_day_tie_break_prefers_most_recent(monkeypatch):
    # Pin (unchanged behavior before and after the fix): when two picks both
    # match the query's UTC calendar date, the most-recently-created one wins.
    pick_older = {
        "league": "NBA",
        "market_type": "moneyline",
        "home_team": "Lakers",
        "away_team": "Celtics",
        "event_start_utc": "2026-10-20T00:00:00Z",
        "model_probability": 0.52,
        "selection": "home",
        "created_at_utc": "2026-10-19T12:00:00Z",
    }
    pick_newer = {
        "league": "NBA",
        "market_type": "moneyline",
        "home_team": "Lakers",
        "away_team": "Celtics",
        "event_start_utc": "2026-10-20T00:00:00Z",
        "model_probability": 0.63,
        "selection": "home",
        "created_at_utc": "2026-10-19T23:00:00Z",
    }
    monkeypatch.setattr(scanner_mod, "_get_logged_picks", lambda: [pick_older, pick_newer])

    result = scanner_mod._lookup_model_prob(
        "NBA",
        "Lakers",
        "Celtics",
        event_start_utc="2026-10-20T00:00:00Z",
        market_type="moneyline",
        calibrate=False,
    )

    assert result == pytest.approx(0.63)
