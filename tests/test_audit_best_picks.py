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

VERIFIED = abp.VERIFIED_QUOTE_PROVENANCE


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
    payload_extra=None,
    market_prob=0.5,
    event_start=None,
):
    _counter["n"] += 1
    n = _counter["n"]
    pick_id = pick_id or f"pick-{n}"
    event_id = event_id or f"event-{n}"
    model_probability = market_prob + edge
    market_probability = market_prob
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
                **(payload_extra or {}),
            }
        )
    if open_pick:
        status = "open"
        result = None
        pnl = None
        start = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    else:
        # The audit drops rows created at/after their own event start (they are in-game
        # decisions), so the old start == created_at default would erase every fixture row.
        # This constant is later than every created_at these tests use.
        start = "2026-01-02T00:00:00Z"
    if event_start is not None:
        start = event_start

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
            start,
            payload,
        ),
    )
    conn.commit()
    conn.close()
    return pick_id


def _bucket_rows(db_path, n, *, result="win", provenance="", **kwargs):
    for _ in range(n):
        _add(db_path, result=result, provenance=provenance, **kwargs)


def _superseded_then_settled(
    db_path,
    *,
    event_id,
    model_id,
    sport,
    market_type,
    selection,
    first_edge,
    first_line,
    last_edge,
    last_line,
    last_result,
    scores=None,
    provenance=abp.VERIFIED_QUOTE_PROVENANCE,
):
    # The forecast job reruns and supersedes earlier rows: the first sighting is the
    # superseded row, the settled row is the last pre-game rerun with a moved line and edge.
    common = {
        "event_id": event_id,
        "model_id": model_id,
        "sport": sport,
        "market_type": market_type,
        "selection": selection,
        "provenance": provenance,
    }
    _add(
        db_path,
        edge=first_edge,
        line=first_line,
        result=None,
        status="superseded",
        created_at="2026-01-01T14:00:00Z",
        **common,
    )
    _add(
        db_path,
        edge=last_edge,
        line=last_line,
        result=last_result,
        status="settled",
        created_at="2026-01-01T22:00:00Z",
        payload_extra=(
            {"home_score": str(scores[0]), "away_score": str(scores[1])} if scores is not None else None
        ),
        **common,
    )


def _audit(engine, *, sport, market_type, selection, edge, model_id="m1"):
    return engine.audit_candidate(
        sport=sport,
        market_type=market_type,
        selection=selection,
        edge=edge,
        model_prob=0.5 + edge,
        market_prob=0.5,
        model_id=model_id,
        quote_provenance=abp.VERIFIED_QUOTE_PROVENANCE,
    )


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


# --- quick-261120-gaq: buckets keyed on first sighting, not the last pre-game rerun ------


def test_first_sight_bucket_not_last_rerun(db):
    # A pick offered at +6.5% whose last rerun shows +13% must stay in the 5-8% bucket and be
    # graded at its own first-sight line: under 230.5 loses on a final total of 232, even
    # though the settled row (under 233.5) won.
    _superseded_then_settled(
        db,
        event_id="evt-a",
        model_id="m1",
        sport="nba",
        market_type="total",
        selection="under",
        first_edge=0.065,
        first_line=230.5,
        last_edge=0.13,
        last_line=233.5,
        last_result="win",
        scores=(120, 112),
    )
    engine = abp.HistoricalAuditEngine(db)
    first = _audit(engine, sport="nba", market_type="total", selection="under", edge=0.065)
    assert first["pooled"].verified.wins == 0
    assert first["pooled"].verified.losses == 1
    assert first["pooled"].verified.total_graded == 1
    assert first["pooled"].verified.pnl == pytest.approx(-1.0)
    assert first["evidence"].all_rows.losses == 1

    last = _audit(engine, sport="nba", market_type="total", selection="under", edge=0.13)
    assert last["pooled"].all_rows.total_graded == 0
    assert last["pooled"].verified.total_graded == 0


def test_first_sight_graded_by_same_line_settled_result_without_scores(db):
    _superseded_then_settled(
        db,
        event_id="evt-b",
        model_id="m1",
        sport="nba",
        market_type="total",
        selection="under",
        first_edge=0.065,
        first_line=230.5,
        last_edge=0.13,
        last_line=230.5,
        last_result="win",
    )
    # Different settled line and no scores: the first-sight row cannot be graded, and the
    # later row is never substituted for it.
    _superseded_then_settled(
        db,
        event_id="evt-b2",
        model_id="m1",
        sport="nba",
        market_type="total",
        selection="under",
        first_edge=0.065,
        first_line=230.5,
        last_edge=0.13,
        last_line=233.5,
        last_result="win",
    )
    engine = abp.HistoricalAuditEngine(db)
    res = _audit(engine, sport="nba", market_type="total", selection="under", edge=0.065)
    v = res["pooled"].verified
    assert (v.wins, v.losses) == (1, 0)
    # Price-derived from the first-sight row's 50% price, not the settled row's own pnl (0.9).
    assert v.pnl == pytest.approx(1.0)
    assert res["pooled"].all_rows.total_graded == 1
    assert engine.ungraded_first_sight_keys == 1


def test_archived_and_removed_rows_never_counted(db):
    common = {"sport": "mlb", "market_type": "moneyline", "selection": "home"}
    _add(
        db,
        event_id="evt-c",
        edge=0.065,
        result="loss",
        status="archived",
        provenance=abp.VERIFIED_QUOTE_PROVENANCE,
        created_at="2026-01-01T10:00:00Z",
        **common,
    )
    _add(
        db,
        event_id="evt-c",
        edge=0.09,
        result="win",
        status="settled",
        provenance=abp.VERIFIED_QUOTE_PROVENANCE,
        created_at="2026-01-01T12:00:00Z",
        **common,
    )
    _add(
        db,
        event_id="evt-c-removed",
        edge=0.065,
        result="loss",
        status="removed",
        provenance=abp.VERIFIED_QUOTE_PROVENANCE,
        created_at="2026-01-01T10:00:00Z",
        **common,
    )
    # A live row of a removed key: the operator removed part of this decision, so the whole
    # key is excluded from first-sight evidence (quick-261120-w6i D5), not just the bad row.
    _add(
        db,
        event_id="evt-c-removed",
        edge=0.09,
        result="win",
        status="settled",
        provenance=abp.VERIFIED_QUOTE_PROVENANCE,
        created_at="2026-01-01T12:00:00Z",
        **common,
    )
    _add(
        db,
        event_id="evt-c-clean",
        edge=0.09,
        result="win",
        status="settled",
        provenance=abp.VERIFIED_QUOTE_PROVENANCE,
        created_at="2026-01-01T12:00:00Z",
        **common,
    )
    engine = abp.HistoricalAuditEngine(db)
    low = engine.stats_by_sport_mkt_bucket[("mlb", "moneyline", "5.0% - 7.9%")]
    assert low.all_rows.total_graded == 0
    assert low.verified.total_graded == 0
    mid = engine.stats_by_sport_mkt_bucket[("mlb", "moneyline", "8.0% - 11.9%")]
    assert (mid.verified.wins, mid.verified.losses) == (1, 0)
    assert mid.all_rows.total_graded == 1
    assert engine.first_sight_edge("mlb", "evt-c", "moneyline", "home", "m1") is None
    # The old-basis context map is deliberately unchanged: it still counts all three settled rows.
    lr = engine.last_run_by_sport_mkt_bucket[("mlb", "moneyline", "8.0% - 11.9%")]
    assert lr.verified.wins == 3


def test_last_run_evidence_keeps_settled_row_basis(db):
    for i in range(4):
        _superseded_then_settled(
            db,
            event_id=f"evt-d{i}",
            model_id="m1",
            sport="nba",
            market_type="total",
            selection="under",
            first_edge=0.065,
            first_line=230.5,
            last_edge=0.13,
            last_line=233.5,
            last_result="win",
            scores=(120, 112),
        )
    _superseded_then_settled(
        db,
        event_id="evt-d-m2",
        model_id="m2",
        sport="nba",
        market_type="total",
        selection="under",
        first_edge=0.065,
        first_line=230.5,
        last_edge=0.13,
        last_line=233.5,
        last_result="loss",
        scores=(120, 115),
    )
    engine = abp.HistoricalAuditEngine(db)
    res = _audit(engine, sport="nba", market_type="total", selection="under", edge=0.13)
    assert res["basis"] == "all models pooled"
    assert res["evidence"].verified.total_graded == 0
    # The context basis follows the first-sight decision (pooled); the m1-only record would
    # read 4W-0L if it chose the basis instead.
    lr = res["last_run_evidence"].verified
    assert (lr.wins, lr.losses) == (4, 1)
    assert any("Context, last pre-game run (old basis): verified 4W-1L" in f for f in res["flags"])


def test_first_sight_spread_graded_from_selected_side(db):
    # Spread line is from the selected side's perspective: away +2.5 covers a 2-point away
    # loss, the later +1.5 does not.
    _superseded_then_settled(
        db,
        event_id="evt-e",
        model_id="m1",
        sport="nba",
        market_type="spread",
        selection="away",
        first_edge=0.065,
        first_line=2.5,
        last_edge=0.13,
        last_line=1.5,
        last_result="loss",
        scores=(122, 120),
    )
    engine = abp.HistoricalAuditEngine(db)
    res = _audit(engine, sport="nba", market_type="spread", selection="away", edge=0.065)
    v = res["pooled"].verified
    assert (v.wins, v.losses) == (1, 0)
    assert v.pnl == pytest.approx(1.0)


# --- quick-261120-w6i: first-sight candidate bucketing and population fixes ---------------


def _future_start():
    return (datetime.now(UTC) + timedelta(days=1)).isoformat()


def test_open_pick_bucketed_on_first_sight_edge(db):
    # 30 verified wins at 9% edge make the 8-12% bucket confirmable.
    _bucket_rows(db, 30, provenance=abp.VERIFIED_QUOTE_PROVENANCE, edge=0.09)
    start = _future_start()
    common = {
        "event_id": "evt-open",
        "model_id": "m1",
        "provenance": abp.VERIFIED_QUOTE_PROVENANCE,
        "event_start": start,
    }
    # First offered at +9%, but the open row (the latest rerun) now shows only +3%.
    _add(db, edge=0.09, result=None, status="superseded", created_at="2026-01-01T10:00:00Z", **common)
    _add(db, edge=0.03, pick_id="open-1", open_pick=True, created_at="2026-01-01T12:00:00Z", **common)
    engine = abp.HistoricalAuditEngine(db)
    # Lookup lowercases like _decision_key, so widget-style mixed-case inputs still resolve.
    assert engine.first_sight_edge("MLB", "evt-open", "Moneyline", "HOME", "M1") == pytest.approx(0.09)
    assert engine.first_sight_edge("mlb", "never-seen", "moneyline", "home", "m1") is None

    picks = abp.collect_open_pick_audits(db)
    assert len(picks) == 1
    pick = picks[0]
    assert pick["bucket"] == "8.0% - 11.9%"
    assert pick["bucket_edge"] == pytest.approx(0.09)
    assert pick["verdict"] == "CONFIRMED_BEST"

    # The widget's 8-positional call has no bucket_edge, so it keeps bucketing on current edge.
    widget = engine.audit_candidate("mlb", "moneyline", "home", 0.03, 0.53, 0.5, "m1", VERIFIED)
    assert widget["bucket"] == "2.0% - 4.9%"
    assert widget["verdict"] == "QUALIFIED_MODERATE"
    # The negative-edge trap still reads the current edge, whatever bucket_edge says.
    neg = engine.audit_candidate(
        "mlb", "moneyline", "home", -0.02, 0.48, 0.5, "m1", VERIFIED, bucket_edge=0.09
    )
    assert neg["bucket"] == "8.0% - 11.9%"
    assert neg["verdict"] == "DOWNGRADE_TRAP"


def test_verified_record_requires_verified_first_row(db):
    common = {"sport": "mlb", "market_type": "moneyline", "selection": "home", "event_id": "evt-v"}
    _add(
        db,
        edge=0.065,
        result=None,
        status="superseded",
        provenance="",
        created_at="2026-01-01T14:00:00Z",
        **common,
    )
    _add(
        db,
        edge=0.13,
        result="win",
        provenance=abp.VERIFIED_QUOTE_PROVENANCE,
        created_at="2026-01-01T22:00:00Z",
        **common,
    )
    engine = abp.HistoricalAuditEngine(db)
    mid = engine.stats_by_sport_mkt_bucket[("mlb", "moneyline", "5.0% - 7.9%")]
    assert (mid.all_rows.wins, mid.all_rows.losses) == (1, 0)
    assert mid.verified.total_graded == 0
    # HEAD let the later verified rerun stand in for the key and counted it here instead.
    high = engine.stats_by_sport_mkt_bucket[("mlb", "moneyline", ">= 12.0%")]
    assert high.all_rows.total_graded == 0
    assert high.verified.total_graded == 0


def test_nrfi_rows_never_score_an_event(db):
    # nrfi rows store first-inning runs in home_score/away_score; reading them as a final
    # score graded this total over as a loss from a 1-0 first inning.
    _add(
        db,
        sport="mlb",
        market_type="nrfi",
        selection="nrfi",
        event_id="evt-n",
        created_at="2026-01-01T10:00:00Z",
        payload_extra={"home_score": "1", "away_score": "0"},
    )
    _superseded_then_settled(
        db,
        event_id="evt-n",
        model_id="m1",
        sport="mlb",
        market_type="total",
        selection="over",
        first_edge=0.065,
        first_line=8.5,
        last_edge=0.13,
        last_line=9.5,
        last_result="win",
        scores=(6, 4),
    )
    engine = abp.HistoricalAuditEngine(db)
    v = engine.stats_by_sport_dir_bucket[("mlb", "over", "5.0% - 7.9%")].verified
    assert (v.wins, v.losses) == (1, 0)


def test_rows_created_at_or_after_start_never_first_sight(db):
    common = {
        "sport": "mlb",
        "market_type": "moneyline",
        "selection": "home",
        "edge": 0.09,
        "provenance": abp.VERIFIED_QUOTE_PROVENANCE,
    }
    # No-seconds start form, created two hours after it.
    _add(
        db, event_id="evt-late", created_at="2026-01-01T22:00:00Z", event_start="2026-01-01T20:00Z", **common
    )
    # Created exactly at start: not strictly before, so an in-game decision.
    _add(
        db,
        event_id="evt-at",
        created_at="2026-01-01T20:00:00Z",
        event_start="2026-01-01T20:00:00Z",
        **common,
    )
    # 15:00-0500 is 20:00Z, so created 19:00Z is pre-start; a string compare would drop it.
    _add(
        db,
        event_id="evt-ok",
        created_at="2026-01-01T19:00:00Z",
        event_start="2026-01-01T15:00:00-0500",
        **common,
    )
    engine = abp.HistoricalAuditEngine(db)
    bucket = engine.stats_by_sport_mkt_bucket[("mlb", "moneyline", "8.0% - 11.9%")]
    assert bucket.all_rows.total_graded == 1
    assert bucket.verified.total_graded == 1
    assert engine.first_sight_edge("mlb", "evt-late", "moneyline", "home", "m1") is None
    assert engine.first_sight_edge("mlb", "evt-ok", "moneyline", "home", "m1") == pytest.approx(0.09)
