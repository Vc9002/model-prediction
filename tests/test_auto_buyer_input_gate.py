"""The paper buyer must not buy a pick whose own forecast flagged its inputs as unqualified.

Regression for 2026-11-15: the buyer reads Flat as well as Main and only blocked
``PAPER_CALL_*`` rows, so NFL rows Main had declined (unqualified efficiency
inputs, a total priced with no pregame weather) were still buy candidates.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta

from model_prediction.domain import iso_utc, utc_now
from model_prediction.portfolio import auto_executor
from model_prediction.portfolio.auto_executor import (
    DISABLED_AUTO_BUYER_SPORT_MARKETS,
    AutoExecutionConfig,
    AutoPolymarketBuyer,
    _has_unqualified_inputs,
    _load_pick_input_flags,
)


def _ledger(tmp_path, rows: dict[str, dict]):
    db = tmp_path / "ledgers.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE ledger_records (pick_id TEXT, ledger_tier TEXT, feature_payload_json TEXT)")
    for pick_id, features in rows.items():
        conn.execute(
            "INSERT INTO ledger_records VALUES (?, 'flat', ?)", (pick_id, json.dumps({"features": features}))
        )
    conn.commit()
    conn.close()
    return db


def _pick(pick_id: str, market: str = "total", **extra) -> dict:
    return {
        "pick_id": pick_id,
        "model_id": "nfl-total-structural-v7",
        "status": "open",
        "decision": "CALL",
        "reason_code": "FLAT_CALL",
        "league": "NFL",
        "market_type": market,
        "selection": "under",
        "line": 40.5,
        "event_start_utc": iso_utc(utc_now() + timedelta(minutes=30)),
        "model_probability": 0.56,
        **extra,
    }


def test_ledger_flags_are_parsed_from_string_booleans(tmp_path):
    db = _ledger(
        tmp_path,
        {
            "bad-inputs": {"input_qualified": "False", "weather_inputs_verified": "True"},
            "no-weather": {"input_qualified": "True", "weather_inputs_verified": "False"},
            "clean": {"input_qualified": "True", "weather_inputs_verified": "True"},
            "no-flags": {"elo_probability": "0.57"},
        },
    )
    flags = _load_pick_input_flags(["bad-inputs", "no-weather", "clean", "no-flags", "absent"], db)
    assert flags == {
        "bad-inputs": {"input_qualified": False, "weather_inputs_verified": True},
        "no-weather": {"input_qualified": True, "weather_inputs_verified": False},
        "clean": {"input_qualified": True, "weather_inputs_verified": True},
        "no-flags": {},
    }
    assert _has_unqualified_inputs(_pick("bad-inputs"), flags) is True
    assert _has_unqualified_inputs(_pick("no-weather"), flags) is True
    assert _has_unqualified_inputs(_pick("clean"), flags) is False
    # A model that publishes no flag is not blocked by this gate.
    assert _has_unqualified_inputs(_pick("no-flags"), flags) is False
    assert _has_unqualified_inputs(_pick("absent"), flags) is False


def test_unverified_weather_only_blocks_totals(tmp_path):
    flags = {"p": {"input_qualified": True, "weather_inputs_verified": False}}
    assert _has_unqualified_inputs(_pick("p", market="total"), flags) is True
    assert _has_unqualified_inputs(_pick("p", market="moneyline"), flags) is False


def test_unreadable_ledger_fails_closed_for_nfl_structural_rows():
    assert _has_unqualified_inputs(_pick("x"), None) is True
    nba = _pick("y", league="NBA", model_id="nba-spread-margin-v9", market="spread")
    assert _has_unqualified_inputs(nba, None) is False


def test_buyer_rejects_flat_row_with_unqualified_inputs(tmp_path, monkeypatch):
    db = _ledger(
        tmp_path,
        {
            "lv-under": {"input_qualified": "False", "weather_inputs_verified": "True"},
            "det-over": {"input_qualified": "True", "weather_inputs_verified": "False"},
            "ind-under": {"input_qualified": "True", "weather_inputs_verified": "True"},
        },
    )
    real_loader = auto_executor._load_pick_input_flags
    monkeypatch.setattr(auto_executor, "DISABLED_AUTO_BUYER_SPORT_MARKETS", frozenset())
    monkeypatch.setattr(auto_executor, "_load_pick_input_flags", lambda ids: real_loader(ids, db))
    buyer = AutoPolymarketBuyer(
        config=AutoExecutionConfig(
            whitelisted_models=("nfl-total-structural-v7",),
            blacklisted_models=(),
            mode="paper",
            execute_live=False,
            # The clean pick is stopped at the win-probability gate, which sits
            # after quote mapping, so the test needs no exchange.
            min_model_probability=0.99,
            capture_missing_slates=False,
        ),
        data_root=tmp_path,
        live_quote_fn=lambda _slug: {
            "observed_at_utc": iso_utc(utc_now()),
            "market_state": "MARKET_STATE_OPEN",
            "long": {"ask": 0.5},
            "short": {"ask": 0.5},
        },
    )
    result = buyer.evaluate_and_execute([_pick("lv-under"), _pick("det-over"), _pick("ind-under")])
    assert result.rejected_input_unqualified == 2
    assert result.whitelisted_count == 1


def test_nfl_spread_is_disabled_and_total_is_live_in_the_buyer():
    assert ("nfl", "spread") in DISABLED_AUTO_BUYER_SPORT_MARKETS
    assert ("nfl", "total") not in DISABLED_AUTO_BUYER_SPORT_MARKETS
