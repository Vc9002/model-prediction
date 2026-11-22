"""NCAAF settlement never records a quote observed before the decision as the close.

The day-ahead NCAAF capture (quick 261121-dac) files D+1/D+2 books. A v5 row priced off an
indicative event-list price still carries the contract slug, so without a lower bound the
day-ahead quote, observed before the decision, became that row's "close".
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

SLUG = "aec-cfb-aaaa-bbbb-2026-11-21"


def _settle(tmp_path, monkeypatch, snapshots_offsets):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(runtime))
    start = datetime.now(UTC) - timedelta(hours=3)
    folder = runtime / "odds" / "ncaaf" / start.date().isoformat()
    folder.mkdir(parents=True)
    folder.joinpath("polymarket_snapshots.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "event_id": "g1",
                    "market_slug": SLUG,
                    "market_type": "moneyline",
                    "observed_at_utc": (start + offset).isoformat(),
                    "market_state": "MARKET_STATE_OPEN",
                    "event_start_utc": start.isoformat(),
                    "long": {"description": "Bbbb", "ask": ask},
                    "short": {"description": "Aaaa", "ask": round(1.02 - ask, 2)},
                }
            )
            + "\n"
            for offset, ask in snapshots_offsets
        ),
        encoding="utf-8",
    )
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    request = PickRequest(
        event_start_utc=start.isoformat(),
        event_id="g1",
        league=League.NCAAF,
        away_team="Aaaa",
        home_team="Bbbb",
        market_type=MarketType.MONEYLINE,
        selection="home",
        line=None,
        sportsbook="polymarket_us_indicative",
        american_odds=-110,
        model_probability=0.6,
        model_uncertainty=0.05,
        model_version="cfb-structural-v5",
        rationale=f"fixture market_slug={SLUG}",
        risks="",
        model_origin=ModelOrigin.STATISTICAL_MODEL,
        model_enabled=True,
    )
    away = CanonicalTeam("Aaaa", League.NCAAF, "Aaaa", "AAA", True, None, None, ())
    home = CanonicalTeam("Bbbb", League.NCAAF, "Bbbb", "BBB", True, None, None, ())
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
            "away_score": 10,
            "home_score": 20,
        },
    )
    config = {
        "project": {
            "ledger_path": str(PROJECT_ROOT / "data" / "picks.xlsx"),
            "market_odds_snapshot_path": str(tmp_path / "no_market_odds.jsonl"),
        }
    }
    settle_module._settle_all_unsettled(
        SimpleNamespace(), config, ledger, espn=SimpleNamespace(), scoreboard_cache={}
    )
    return next(item for item in ledger.rows() if item["pick_id"] == row["pick_id"])


def test_day_ahead_quote_before_the_decision_is_never_the_close(tmp_path, monkeypatch):
    # Only a quote 30 h before kickoff exists; the decision was at kickoff - 5 h.
    row = _settle(tmp_path, monkeypatch, [(timedelta(hours=-30), 0.55)])
    assert row["status"] == "settled"
    assert row["closing_implied_probability"] == ""
    assert row["probability_clv"] == ""


def test_pre_kickoff_quote_after_the_decision_is_the_close(tmp_path, monkeypatch):
    row = _settle(tmp_path, monkeypatch, [(timedelta(hours=-30), 0.55), (timedelta(minutes=-10), 0.62)])
    assert float(row["closing_implied_probability"]) == 0.62
