"""The shared display name and edge bucket (display only; ledger ids never change)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from model_prediction.display_names import (
    EDGE_BUCKET_LABELS,
    edge_bucket,
    edge_bucket_pct,
    model_display_name,
)

_V7_ENGINE = {"model_input_snapshot_json": '{"features": {"margin_engine": "v7"}}'}


@pytest.mark.parametrize(
    ("edge", "label"),
    [
        (-0.0001, "<0%"),
        (0.0, "0-2%"),
        (0.0199, "0-2%"),
        (0.02, "2-5%"),
        (0.05, "5-8%"),
        (0.08, "8-12%"),
        (0.1199, "8-12%"),
        (0.12, "12%+"),
        (0.45, "12%+"),
    ],
)
def test_edge_bucket_bands(edge: float, label: str) -> None:
    assert edge_bucket(edge) == label
    assert edge_bucket_pct(edge * 100) == label


def test_one_bucket_set_everywhere() -> None:
    """The best-pick audit (also loaded by the widget) and market_eval use the shared bands."""
    from model_prediction import market_eval

    spec = importlib.util.spec_from_file_location(
        "_audit_best_picks", Path(__file__).resolve().parents[1] / "scripts" / "audit_best_picks.py"
    )
    assert spec is not None and spec.loader is not None
    audit = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = audit  # its dataclasses look their module up in sys.modules
    try:
        spec.loader.exec_module(audit)
    finally:
        sys.modules.pop(spec.name, None)
    for edge in (-0.03, 0.0, 0.019, 0.02, 0.049, 0.05, 0.079, 0.08, 0.119, 0.12, 0.3):
        assert audit.get_edge_bucket(edge) == edge_bucket(edge)
        assert market_eval._edge_bucket(edge) == edge_bucket(edge)
    assert EDGE_BUCKET_LABELS == ("<0%", "0-2%", "2-5%", "5-8%", "8-12%", "12%+")


@pytest.mark.parametrize(
    ("model_id", "market", "payload", "name"),
    [
        # v7 engine served under the v6 id 2026-11-16..11-21: named by the engine that priced it.
        ("ncaab-spread-v6", "spread", _V7_ENGINE, "NCAAB Spread v7"),
        ("ncaab-spread-v6", "spread", None, "NCAAB Spread v6"),
        # The id's market word is not the pick's market.
        ("ncaab-spread-v7", "moneyline", _V7_ENGINE, "NCAAB Moneyline v7"),
        # Same engine number keeps the variant suffix.
        ("ncaab-moneyline-v7m", "moneyline", _V7_ENGINE, "NCAAB Moneyline v7m"),
        ("cfb-structural-v5", "total", None, "NCAAF Total v5"),
        ("cfb-structural-v6.2", "spread", None, "NCAAF Spread v6.2"),
        ("cfb-structural-v7.1", "moneyline", None, "NCAAF Moneyline v7.1"),
        ("college-football-v1", "moneyline", None, "NCAAF Moneyline v1"),
        ("nba-elo-trend-lr-v5", "moneyline", None, "NBA Moneyline v5"),
        ("mlb-structural-v10-frozen", "total", None, "MLB Total v10"),
        ("nba-total-margin-v9", None, None, "NBA v9"),
    ],
)
def test_model_display_name(model_id: str, market: str | None, payload: dict | None, name: str) -> None:
    assert model_display_name(model_id, market, payload) == name


def test_engine_override_is_ncaab_only_and_ignores_bad_payloads() -> None:
    assert model_display_name("nba-spread-margin-v9", "spread", _V7_ENGINE) == "NBA Spread v9"
    assert model_display_name("ncaab-spread-v6", "spread", {"model_input_snapshot_json": "not json"}) == (
        "NCAAB Spread v6"
    )
