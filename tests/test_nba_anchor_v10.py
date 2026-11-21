"""nba-anchor-v10 scaffold: always a side, anchored price, PIT-safe pluggable anchors."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path

import pytest

from model_prediction.models.nba_anchor_v10 import (
    REASON_COIN_FLIP,
    REASON_LAMBDA_ZERO,
    REASON_NO_ANCHOR,
    AnchorMarketParams,
    AnchorParams,
    AnchorQuote,
    CapturedAnchorSource,
    ClosingLineAnchorSource,
    ContractQuote,
    price_game,
    price_market,
    taker_fee_per_contract,
)

ROOT = Path(__file__).resolve().parent.parent
DECISION = datetime(2026, 11, 20, 23, 0, tzinfo=UTC)


def _params(lam: float = 0.0, min_edge: float | None = None) -> AnchorParams:
    return AnchorParams(
        markets={
            "spread": AnchorMarketParams(lam, 13.8, min_edge),
            "total": AnchorMarketParams(lam, 17.6, min_edge),
        },
        fee_coefficient=0.06,
    )


def _quote(market: str, line: float, observed: str | None = "2026-11-20T22:00:00Z") -> AnchorQuote:
    return AnchorQuote("e1", market, line, "espn_consensus", observed, benchmark_only=False)


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def test_lambda_zero_at_anchor_line_is_coin_flip_with_model_side() -> None:
    contract = ContractQuote(line=4.5, asks={"home": 0.52, "away": 0.51})
    call = price_market(
        "e1", "spread", 1.0, _quote("spread", 4.5), contract, _params(), decision_time=DECISION, serving=True
    )
    # Model says home wins by 1, under the 4.5 line: side is away, probability exactly 50%.
    assert call.side == "away" and call.probability == pytest.approx(0.5)
    assert call.edge == pytest.approx(0.5 - 0.51 - taker_fee_per_contract(0.51, 0.06))
    assert not call.main_eligible
    assert REASON_COIN_FLIP in call.reason_codes and REASON_LAMBDA_ZERO in call.reason_codes


def test_positive_lambda_blends_toward_model_and_nets_fees() -> None:
    contract = ContractQuote(line=220.5, asks={"over": 0.50, "under": 0.52})
    call = price_market(
        "e1",
        "total",
        230.5,
        _quote("total", 220.5),
        contract,
        _params(lam=0.5, min_edge=0.02),
        decision_time=DECISION,
        serving=True,
    )
    assert call.blended_mean == pytest.approx(225.5)
    expected = 1.0 - _phi((220.5 - 225.5) / 17.6)
    assert call.side == "over" and call.probability == pytest.approx(expected)
    assert call.edge == pytest.approx(expected - 0.50 - 0.06 * 0.5 * 0.5)
    assert call.main_eligible


def test_benchmark_only_anchor_refused_when_serving() -> None:
    source = ClosingLineAnchorSource({"e1": (3.5, 221.0)})
    contracts = {"spread": ContractQuote(3.5, {"home": 0.5, "away": 0.5})}
    with pytest.raises(ValueError, match="benchmark-only"):
        price_game("e1", {"spread": 2.0}, contracts, source, _params(), decision_time=DECISION)
    calls = price_game(
        "e1", {"spread": 2.0}, contracts, source, _params(), decision_time=DECISION, serving=False
    )
    assert calls[0].anchor_source == "closing_line_archive" and not calls[0].main_eligible


def test_anchor_observed_after_decision_is_refused() -> None:
    late = _quote("spread", 3.5, observed="2026-11-20T23:30:00Z")
    with pytest.raises(ValueError, match="after decision time"):
        price_market(
            "e1", "spread", 2.0, late, ContractQuote(3.5, {}), _params(), decision_time=DECISION, serving=True
        )


def test_captured_source_uses_latest_capture_at_or_before_decision() -> None:
    quotes = {
        ("e1", "spread"): [
            _quote("spread", 3.0, "2026-11-20T18:00:00Z"),
            _quote("spread", 3.5, "2026-11-20T22:59:00Z"),
            _quote("spread", 5.0, "2026-11-20T23:10:00Z"),
        ]
    }
    anchor = CapturedAnchorSource(quotes).anchor("e1", "spread", DECISION)
    assert anchor is not None and anchor.line == 3.5


def test_missing_anchor_still_calls_every_market_model_only() -> None:
    contracts = {
        "spread": ContractQuote(3.5, {"home": 0.5, "away": 0.5}),
        "total": ContractQuote(220.5, {"over": 0.5, "under": 0.5}),
    }
    calls = price_game(
        "e1",
        {"spread": 8.0, "total": 210.0},
        contracts,
        CapturedAnchorSource({}),
        _params(0.0, 0.0),
        decision_time=DECISION,
    )
    assert [(c.market, c.side) for c in calls] == [("spread", "home"), ("total", "under")]
    assert all(REASON_NO_ANCHOR in c.reason_codes and not c.main_eligible for c in calls)


def test_research_artifact_ships_lambda_zero_after_g1_fail() -> None:
    artifact = json.loads((ROOT / "config/models/research/nba-anchor-v10.json").read_text(encoding="utf-8"))
    params = AnchorParams.from_artifact(artifact)
    assert {m: artifact["markets"][m]["g1"] for m in ("spread", "total")} == {
        "spread": "FAIL",
        "total": "FAIL",
    }
    assert params.markets["spread"].lambda_ == 0.0 and params.markets["total"].lambda_ == 0.0
    assert params.markets["spread"].main_min_edge is None
    assert "nba-anchor-v10" not in (ROOT / "config/production.yaml").read_text(encoding="utf-8")


def test_fallback_source_prefers_consensus_then_polymarket_mid() -> None:
    from model_prediction.models.nba_anchor_v10 import FallbackAnchorSource

    consensus = CapturedAnchorSource({("e1", "total"): [_quote("total", 221.0)]})
    mid = CapturedAnchorSource(
        {("e1", "total"): [_quote("total", 222.5)], ("e1", "spread"): [_quote("spread", 4.0)]}
    )
    source = FallbackAnchorSource([consensus, mid])
    total = source.anchor("e1", "total", DECISION)
    spread = source.anchor("e1", "spread", DECISION)
    assert total is not None and total.line == 221.0
    assert spread is not None and spread.line == 4.0
