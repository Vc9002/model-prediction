"""Settlement fills closing_* / probability_clv for NCAAB from the runtime-root snapshot archive.

Settlement derives its data root from the ledger path (the checkout's legacy ``data/``) while
Polymarket snapshots live under the runtime root since 2026-11-04, so the closing lookup always
missed and no NCAAB row ever carried a closing quote or CLV (quick task 261115-1m3).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from model_prediction.cli import settle as settle_module
from model_prediction.config import PROJECT_ROOT
from model_prediction.domain import League, MarketType, ModelOrigin, PickRequest
from model_prediction.eligibility import EligibilityResult
from model_prediction.entities import CanonicalTeam
from model_prediction.ledger import PickLedger

SLUG = "tsc-cbb-aaaa-bbbb-2026-11-14-145pt5"
CLOSING_FIELDS = (
    "closing_line",
    "closing_american_odds",
    "closing_implied_probability",
    "closing_raw_implied_probability",
    "probability_clv",
)


def _settle_fixture(tmp_path, monkeypatch, *, archive: bool, at_decision: str | None = None):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(runtime))
    start = datetime.now(UTC) - timedelta(hours=3)
    if archive:
        folder = runtime / "odds" / "ncaab" / start.date().isoformat()
        folder.mkdir(parents=True)
        snapshots = [
            # Earlier quote, then the last pre-tip quote that must win, then a post-tip quote
            # that may never be used as the close.
            (start - timedelta(hours=2), 0.48, 0.54),
            (start - timedelta(minutes=2), 0.52, 0.50),
            (start + timedelta(minutes=30), 0.99, 0.02),
        ]
        # The row's own decision quote, observed exactly at decision time (start-5h): never a
        # close. "only" leaves it as the sole quote; "extra" adds it to the others.
        if at_decision == "only":
            snapshots = [(start - timedelta(hours=5), 0.47, 0.55)]
        elif at_decision == "extra":
            snapshots.append((start - timedelta(hours=5), 0.47, 0.55))
        folder.joinpath("polymarket_snapshots.jsonl").write_text(
            "".join(
                json.dumps(
                    {
                        "event_id": "g1",
                        "market_slug": SLUG,
                        "market_type": "total",
                        "line": 145.5,
                        "observed_at_utc": observed.isoformat(),
                        "long": {"description": "Over", "ask": over_ask},
                        "short": {"description": "Under", "ask": under_ask},
                    }
                )
                + "\n"
                for observed, over_ask, under_ask in snapshots
            ),
            encoding="utf-8",
        )
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    request = PickRequest(
        event_start_utc=start.isoformat(),
        event_id="g1",
        league=League.NCAAB,
        away_team="Aaaa",
        home_team="Bbbb",
        market_type=MarketType.TOTAL,
        selection="over",
        line=145.5,
        sportsbook="polymarket_us_indicative",
        american_odds=-110,
        model_probability=0.6,
        model_uncertainty=0.05,
        model_version="ncaab-total-v6",
        rationale=f"fixture market_slug={SLUG}",
        risks="",
        model_origin=ModelOrigin.STATISTICAL_MODEL,
        model_enabled=True,
    )
    away = CanonicalTeam("Aaaa", League.NCAAB, "Aaaa", "AAA", True, None, None, ())
    home = CanonicalTeam("Bbbb", League.NCAAB, "Bbbb", "BBB", True, None, None, ())
    row = ledger.append_evaluated(
        request,
        EligibilityResult(False, "CALL", "PAPER_CALL_MODEL_UNVALIDATED", 1.0, 60, 0.05, 0.05, away, home),
        now=start - timedelta(hours=5),
    )
    monkeypatch.setattr(
        settle_module,
        "_find_espn_result",
        lambda *a, **k: {
            "status_name": "STATUS_FINAL",
            "completed": True,
            "away_score": 70,
            "home_score": 80,
        },
    )
    config = {
        "project": {
            # The legacy repo data/ root, exactly what scheduled settlement computes.
            "ledger_path": str(PROJECT_ROOT / "data" / "picks.xlsx"),
            "market_odds_snapshot_path": str(tmp_path / "no_market_odds.jsonl"),
        }
    }
    outcome = settle_module._settle_all_unsettled(
        SimpleNamespace(), config, ledger, espn=SimpleNamespace(), scoreboard_cache={}
    )
    assert [item["pick_id"] for item in outcome["settled"]] == [row["pick_id"]], outcome
    return ledger, row["pick_id"], config


def _row(ledger, pick_id):
    return next(item for item in ledger.rows() if item["pick_id"] == pick_id)


def test_settlement_captures_the_last_pre_tip_quote_as_the_close(tmp_path, monkeypatch):
    ledger, pick_id, config = _settle_fixture(tmp_path, monkeypatch, archive=True)
    row = _row(ledger, pick_id)
    assert row["status"] == "settled"
    assert float(row["closing_implied_probability"]) == 0.52  # the over ask at the last pre-tip quote
    assert float(row["closing_raw_implied_probability"]) == 0.52
    assert row["probability_clv"] != ""

    # Idempotent: a second pass skips the settled row and leaves every closing field intact.
    before = {field: row[field] for field in CLOSING_FIELDS}
    settle_module._settle_all_unsettled(
        SimpleNamespace(), config, ledger, espn=SimpleNamespace(), scoreboard_cache={}
    )
    again = _row(ledger, pick_id)
    assert {field: again[field] for field in CLOSING_FIELDS} == before


def test_settlement_without_an_archived_quote_leaves_empty_strings_not_none(tmp_path, monkeypatch):
    ledger, pick_id, _ = _settle_fixture(tmp_path, monkeypatch, archive=False)
    row = _row(ledger, pick_id)
    assert row["status"] == "settled"
    for field in CLOSING_FIELDS:
        assert row[field] == "", field


def test_snapshot_at_decision_time_is_never_the_close(tmp_path, monkeypatch):
    ledger, pick_id, _ = _settle_fixture(tmp_path, monkeypatch, archive=True, at_decision="extra")
    row = _row(ledger, pick_id)
    assert row["status"] == "settled"
    # The start-2min quote still wins; the decision-time quote (0.47) is excluded.
    assert float(row["closing_implied_probability"]) == 0.52


def test_only_the_decision_quote_leaves_empty_strings_not_a_fake_zero_clv(tmp_path, monkeypatch):
    # Before 261120-nip the decision's own quote was the last pre-tip snapshot and became the
    # close, so every NCAAB row recorded a fake 0 CLV (339 of 339 settled rows, 2026-11-20).
    ledger, pick_id, _ = _settle_fixture(tmp_path, monkeypatch, archive=True, at_decision="only")
    row = _row(ledger, pick_id)
    assert row["status"] == "settled"
    for field in CLOSING_FIELDS:
        assert row[field] == "", field
