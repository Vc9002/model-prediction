from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_best_picks.py"
spec = importlib.util.spec_from_file_location("audit_best_picks", SCRIPT)
abp = importlib.util.module_from_spec(spec)
# The script defines dataclasses under `from __future__ import annotations`; the module
# must be registered in sys.modules under its own spec name before exec_module runs, or
# dataclass field resolution fails.
sys.modules["audit_best_picks"] = abp
spec.loader.exec_module(abp)


SCHEMA = """
CREATE TABLE ledger_records (
    pick_id TEXT,
    ledger_tier TEXT,
    sport TEXT,
    event_id TEXT,
    market_type TEXT,
    selection TEXT,
    line REAL,
    model_id TEXT,
    model_probability REAL,
    market_probability REAL,
    units REAL,
    status TEXT,
    result TEXT,
    pnl_units REAL,
    created_at_utc TEXT,
    event_start_utc TEXT,
    decision_payload_json TEXT
)
"""

_counter = {"n": 0}


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "ledgers.db"
    conn = sqlite3.connect(path)
    conn.execute(SCHEMA)
    conn.commit()
    conn.close()
    return path


def _add(
    db_path,
    *,
    sport="mlb",
    market_type="moneyline",
    selection="home",
    edge=0.09,
    result="win",
    model_id="m1",
    ledger_tier="main",
    event_id=None,
    pnl=None,
    units=1.0,
    status="settled",
    provenance="",
    pick_id=None,
    line=None,
    created_at="2026-01-01T00:00:00Z",
    open_pick=False,
    raw_payload=None,
    home_team="Home Team",
    away_team="Away Team",
):
    _counter["n"] += 1
    n = _counter["n"]
    pick_id = pick_id or f"pick-{n}"
    event_id = event_id or f"event-{n}"
    model_probability = 0.5 + edge
    market_probability = 0.5
    if pnl is None:
        pnl = 0.9 if result == "win" else (-1.0 if result == "loss" else 0.0)
    if raw_payload is not None:
        payload = raw_payload
    else:
        payload = json.dumps(
            {
                "market_quote_provenance": provenance,
                "away_team": away_team,
                "home_team": home_team,
            }
        )
    if open_pick:
        status = "open"
        result = None
        pnl = None
        event_start = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    else:
        event_start = created_at

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO ledger_records (
            pick_id, ledger_tier, sport, event_id, market_type, selection, line,
            model_id, model_probability, market_probability, units, status, result,
            pnl_units, created_at_utc, event_start_utc, decision_payload_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            pick_id,
            ledger_tier,
            sport,
            event_id,
            market_type,
            selection,
            line,
            model_id,
            model_probability,
            market_probability,
            units,
            status,
            result,
            pnl,
            created_at,
            event_start,
            payload,
        ),
    )
    conn.commit()
    conn.close()
    return pick_id


def _bucket_rows(db_path, n, *, result="win", provenance="", **kwargs):
    for _ in range(n):
        _add(db_path, result=result, provenance=provenance, **kwargs)


# --- Task 1: verified-quote gate -------------------------------------------------


def test_unverified_bucket_evidence_stays_qualified_moderate(db):
    # Test 1: five settled WIN rows, provenance empty string. Candidate quote is itself
    # verified, but the bucket's evidence is not, so the verdict cannot be CONFIRMED_BEST.
    _bucket_rows(db, 5, provenance="")
    engine = abp.HistoricalAuditEngine(db)
    result = engine.audit_candidate(
        sport="mlb",
        market_type="moneyline",
        selection="home",
        edge=0.09,
        model_prob=0.59,
        market_prob=0.5,
        model_id="m1",
        quote_provenance=abp.VERIFIED_QUOTE_PROVENANCE,
    )
    assert result["verdict"] == "QUALIFIED_MODERATE"
    assert result["evidence"].verified.total_graded == 0
    assert result["evidence"].all_rows.total_graded == 5
    assert any("0 verified of 5" in f for f in result["flags"])


def test_verified_bucket_and_verified_candidate_confirms_best(db):
    # Test 2: same five rows, now verified provenance, and a verified candidate.
    _bucket_rows(db, 5, provenance=abp.VERIFIED_QUOTE_PROVENANCE)
    engine = abp.HistoricalAuditEngine(db)
    result = engine.audit_candidate(
        sport="mlb",
        market_type="moneyline",
        selection="home",
        edge=0.09,
        model_prob=0.59,
        market_prob=0.5,
        model_id="m1",
        quote_provenance=abp.VERIFIED_QUOTE_PROVENANCE,
    )
    assert result["verdict"] == "CONFIRMED_BEST"


def test_verified_bucket_with_unverified_candidate_quote_is_withheld(db):
    # Test 3: verified winning bucket, candidate quote is indicative_slate (unverified).
    _bucket_rows(db, 5, provenance=abp.VERIFIED_QUOTE_PROVENANCE)
    engine = abp.HistoricalAuditEngine(db)
    result = engine.audit_candidate(
        sport="mlb",
        market_type="moneyline",
        selection="home",
        edge=0.09,
        model_prob=0.59,
        market_prob=0.5,
        model_id="m1",
        quote_provenance="indicative_slate",
    )
    assert result["verdict"] == "QUALIFIED_MODERATE"
    assert result["bucket_verdict"] == "CONFIRMED_BEST"
    assert any("indicative_slate" in f for f in result["flags"])

    missing = engine.audit_candidate(
        sport="mlb",
        market_type="moneyline",
        selection="home",
        edge=0.09,
        model_prob=0.59,
        market_prob=0.5,
        model_id="m1",
        quote_provenance=None,
    )
    assert missing["verdict"] == "QUALIFIED_MODERATE"
    assert missing["bucket_verdict"] == "CONFIRMED_BEST"
    assert any("missing" in f for f in missing["flags"])


def test_main_and_flat_tier_duplicate_decision_counts_once(db):
    # Test 4: same decision logged in both ledger tiers must not double count.
    _add(
        db,
        event_id="dup-event",
        ledger_tier="main",
        result="win",
        provenance=abp.VERIFIED_QUOTE_PROVENANCE,
    )
    _add(
        db,
        event_id="dup-event",
        ledger_tier="flat",
        result="win",
        provenance=abp.VERIFIED_QUOTE_PROVENANCE,
    )
    engine = abp.HistoricalAuditEngine(db)
    result = engine.audit_candidate(
        sport="mlb",
        market_type="moneyline",
        selection="home",
        edge=0.09,
        model_prob=0.59,
        market_prob=0.5,
        model_id="m1",
        quote_provenance=abp.VERIFIED_QUOTE_PROVENANCE,
    )
    assert result["evidence"].all_rows.total_graded == 1
    assert result["evidence"].verified.total_graded == 1


def test_malformed_payload_counts_as_unverified_without_raising(db):
    # Test 5: malformed decision_payload_json must not raise, and must not count verified.
    _add(db, result="win", raw_payload="{not valid json")
    engine = abp.HistoricalAuditEngine(db)
    result = engine.audit_candidate(
        sport="mlb",
        market_type="moneyline",
        selection="home",
        edge=0.09,
        model_prob=0.59,
        market_prob=0.5,
        model_id="m1",
        quote_provenance=abp.VERIFIED_QUOTE_PROVENANCE,
    )
    assert result["evidence"].all_rows.total_graded == 1
    assert result["evidence"].verified.total_graded == 0


def test_open_picks_end_to_end_gate(db, capsys):
    # Test 6: two open future picks against a verified winning bucket; one carries a
    # verified quote, the other indicative_slate.
    _bucket_rows(db, 5, provenance=abp.VERIFIED_QUOTE_PROVENANCE)
    _add(
        db,
        pick_id="open-verified",
        open_pick=True,
        provenance=abp.VERIFIED_QUOTE_PROVENANCE,
        model_id="m1",
    )
    _add(
        db,
        pick_id="open-indicative",
        open_pick=True,
        provenance="indicative_slate",
        model_id="m1",
    )

    audits = abp.collect_open_pick_audits(db)
    assert len(audits) == 2
    by_pick = {a["pick_id"]: a for a in audits}
    assert by_pick["open-verified"]["verdict"] == "CONFIRMED_BEST"
    assert by_pick["open-indicative"]["verdict"] == "QUALIFIED_MODERATE"

    abp.audit_open_picks(db)
    out = capsys.readouterr().out
    assert "CONFIRMED BEST PICKS (1" in out
    assert "quote: indicative_slate" in out


# --- Task 2: no hardcoded sport/market overrides; live directional evidence -----------


def test_nba_spread_bucket_was_hardcoded_confirmed_now_live_trap(db):
    # Test 7: nba spread, 8.0-11.9% bucket, four verified LOSS rows. This bucket used to
    # be a hardcoded CONFIRMED_BEST regardless of the ledger.
    _bucket_rows(
        db,
        4,
        result="loss",
        provenance=abp.VERIFIED_QUOTE_PROVENANCE,
        sport="nba",
        market_type="spread",
        selection="away",
        edge=0.09,
    )
    engine = abp.HistoricalAuditEngine(db)
    result = engine.audit_candidate(
        sport="nba",
        market_type="spread",
        selection="away",
        edge=0.09,
        model_prob=0.59,
        market_prob=0.5,
        model_id="m1",
        quote_provenance=abp.VERIFIED_QUOTE_PROVENANCE,
    )
    assert result["verdict"] == "DOWNGRADE_TRAP"
    assert any("0W-4L" in f for f in result["flags"])


def test_ncaab_total_over_unverified_bucket_not_confirmed(db):
    # Test 8: ncaab total over, 8.0-11.9% bucket, six unverified WIN rows. This bucket used
    # to be a hardcoded CONFIRMED_BEST.
    _bucket_rows(
        db,
        6,
        result="win",
        provenance="",
        sport="ncaab",
        market_type="total",
        selection="over",
        edge=0.09,
    )
    engine = abp.HistoricalAuditEngine(db)
    result = engine.audit_candidate(
        sport="ncaab",
        market_type="total",
        selection="over",
        edge=0.09,
        model_prob=0.59,
        market_prob=0.5,
        model_id="m1",
        quote_provenance=abp.VERIFIED_QUOTE_PROVENANCE,
    )
    assert result["verdict"] == "QUALIFIED_MODERATE"


def test_ncaab_moneyline_thin_unverified_bucket_not_confirmed(db):
    # Test 9: ncaab moneyline, >= 12% edge, four unverified WIN rows -- the thin-bucket
    # path that produced the bogus NCAAB moneyline label.
    _bucket_rows(
        db,
        4,
        result="win",
        provenance="",
        sport="ncaab",
        market_type="moneyline",
        selection="home",
        edge=0.15,
    )
    engine = abp.HistoricalAuditEngine(db)
    result = engine.audit_candidate(
        sport="ncaab",
        market_type="moneyline",
        selection="home",
        edge=0.15,
        model_prob=0.65,
        market_prob=0.5,
        model_id="m1",
        quote_provenance=abp.VERIFIED_QUOTE_PROVENANCE,
    )
    assert result["verdict"] == "QUALIFIED_MODERATE"
    assert any("0 verified of 4" in f for f in result["flags"])


def test_nba_total_direction_bucket_not_pooled_total(db):
    # Test 10: nba total, 5.0-7.9% bucket, four verified over WINs and four verified under
    # LOSSes. The verdict must come from the direction bucket, not the pooled total record.
    _bucket_rows(
        db,
        4,
        result="win",
        provenance=abp.VERIFIED_QUOTE_PROVENANCE,
        sport="nba",
        market_type="total",
        selection="over",
        edge=0.065,
        model_id="m1",
    )
    _bucket_rows(
        db,
        4,
        result="loss",
        provenance=abp.VERIFIED_QUOTE_PROVENANCE,
        sport="nba",
        market_type="total",
        selection="under",
        edge=0.065,
        model_id="m1",
    )
    engine = abp.HistoricalAuditEngine(db)
    over_result = engine.audit_candidate(
        sport="nba",
        market_type="total",
        selection="over",
        edge=0.065,
        model_prob=0.565,
        market_prob=0.5,
        model_id="m1",
        quote_provenance=abp.VERIFIED_QUOTE_PROVENANCE,
    )
    assert over_result["verdict"] == "CONFIRMED_BEST"

    under_result = engine.audit_candidate(
        sport="nba",
        market_type="total",
        selection="under",
        edge=0.065,
        model_prob=0.565,
        market_prob=0.5,
        model_id="m1",
        quote_provenance=abp.VERIFIED_QUOTE_PROVENANCE,
    )
    assert under_result["verdict"] == "DOWNGRADE_TRAP"


def test_nfl_total_no_history_not_confirmed(db):
    # Test 11: nfl total, 5.0-7.9% bucket, no history at all. This bucket used to be a
    # hardcoded CONFIRMED_BEST regardless of the ledger.
    engine = abp.HistoricalAuditEngine(db)
    result = engine.audit_candidate(
        sport="nfl",
        market_type="total",
        selection="under",
        edge=0.065,
        model_prob=0.565,
        market_prob=0.5,
        model_id="m1",
        quote_provenance=abp.VERIFIED_QUOTE_PROVENANCE,
    )
    assert result["verdict"] == "QUALIFIED_MODERATE"


def test_negative_edge_against_winning_bucket_is_still_a_trap(db):
    # Test 12: negative-edge candidate against a verified winning bucket is always a trap.
    _bucket_rows(db, 5, provenance=abp.VERIFIED_QUOTE_PROVENANCE)
    engine = abp.HistoricalAuditEngine(db)
    result = engine.audit_candidate(
        sport="mlb",
        market_type="moneyline",
        selection="home",
        edge=-0.02,
        model_prob=0.48,
        market_prob=0.5,
        model_id="m1",
        quote_provenance=abp.VERIFIED_QUOTE_PROVENANCE,
    )
    assert result["verdict"] == "DOWNGRADE_TRAP"
