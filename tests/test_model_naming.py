"""Tests for model naming, alias resolution, and human-readable recognition."""

from __future__ import annotations

from model_prediction.config import PROJECT_ROOT
from model_prediction.dashboard.orders import _decorate_pick
from model_prediction.production_registry import ProductionModelRegistry
from model_prediction.sports import (
    MODEL_ALIASES,
    MODEL_CATALOG,
    canonical_model_id,
    display_model_name,
    get_model_descriptor,
    list_models_by_sport,
    standard_model_name,
)


def test_catalog_covers_production_registry() -> None:
    """Every registered production model has a descriptor in MODEL_CATALOG."""
    registry = ProductionModelRegistry.load(PROJECT_ROOT)
    for model_id in registry.entries:
        assert model_id in MODEL_CATALOG, f"Missing catalog entry for production model {model_id}"
        desc = MODEL_CATALOG[model_id]
        assert desc.sport
        assert desc.market
        assert desc.standard_name
        assert desc.display_name


def test_canonical_model_id_resolves_aliases() -> None:
    """Ambiguous model IDs resolve to canonical identifiers and vice versa."""
    # MLB Spread
    assert canonical_model_id("measured-edge-margin-v3") == "measured-edge-margin-v3"
    assert canonical_model_id("mlb-spread-margin-v3") == "measured-edge-margin-v3"
    assert canonical_model_id("mlb-spread-v3") == "measured-edge-margin-v3"
    assert canonical_model_id("mlb-runline-v3") == "measured-edge-margin-v3"

    # MLB Total
    assert canonical_model_id("measured-edge-totals-v3") == "measured-edge-totals-v3"
    assert canonical_model_id("mlb-total-totals-v3") == "measured-edge-totals-v3"
    assert canonical_model_id("mlb-total-v3") == "measured-edge-totals-v3"

    # MLB Market Residual
    assert canonical_model_id("market-residual-v1") == "market-residual-v1"
    assert canonical_model_id("mlb-moneyline-market-residual-v1") == "market-residual-v1"

    # NCAAF Moneyline
    assert canonical_model_id("college-football-v1") == "college-football-v1"
    assert canonical_model_id("cfb-moneyline-v1") == "college-football-v1"
    assert canonical_model_id("cfb-v1") == "college-football-v1"

    # Case insensitive
    assert canonical_model_id("MLB-SPREAD-MARGIN-V3") == "measured-edge-margin-v3"


def test_standard_model_names_have_clear_structure() -> None:
    """Standardized names must include sport and market prefixes."""
    assert standard_model_name("measured-edge-margin-v3") == "mlb-spread-margin-v3"
    assert standard_model_name("measured-edge-totals-v3") == "mlb-total-totals-v3"
    assert standard_model_name("college-football-v1") == "cfb-moneyline-v1"
    assert standard_model_name("cfb-structural-v2") == "cfb-multi-structural-v2"
    assert standard_model_name("tennis-markov-hierarchical-v2") == "tennis-multi-markov-hierarchical-v2"
    assert standard_model_name("wnba-elo-trend-lr-v4") == "wnba-moneyline-elo-trend-lr-v4"


def test_display_model_names_are_human_readable() -> None:
    """Display names must clearly present sport, market, and architecture."""
    assert display_model_name("measured-edge-margin-v3") == "MLB Spread Runline (Margin v3)"
    assert display_model_name("measured-edge-totals-v3") == "MLB Total Over/Under (Totals v3)"
    assert display_model_name("college-football-v1") == "NCAAF Moneyline (CFB v1)"
    assert display_model_name("cfb-structural-v2") == "NCAAF Unified (CFB Structural v2)"
    assert display_model_name("tennis-markov-hierarchical-v2") == "Tennis Unified (Hierarchical Markov v2)"
    assert display_model_name("wnba-elo-trend-lr-v4") == "WNBA Moneyline (Elo Trend LR v4)"
    assert display_model_name("cs2-tiered-elo-v6") == "CS2 Moneyline (Tiered Elo v6)"


def test_production_registry_get_and_properties() -> None:
    """ProductionModelRegistry resolves entries by standard name or alias."""
    registry = ProductionModelRegistry.load(PROJECT_ROOT)

    # Lookup by standard name alias
    entry = registry.get("mlb-spread-margin-v3")
    assert entry is not None
    assert entry.model_id == "measured-edge-margin-v3"
    assert entry.standard_name == "mlb-spread-margin-v3"
    assert entry.display_name == "MLB Spread Runline (Margin v3)"

    # Lookup via subscript operator
    assert "mlb-spread-margin-v3" in registry
    assert registry["mlb-spread-margin-v3"].model_id == "measured-edge-margin-v3"

    # Lookup CFB by standard alias
    cfb_entry = registry.get("cfb-multi-structural-v2")
    assert cfb_entry is not None
    assert cfb_entry.model_id == "cfb-structural-v2"
    assert cfb_entry.display_name == "NCAAF Unified (CFB Structural v2)"


def test_list_models_by_sport() -> None:
    """Can list all models associated with a sport."""
    mlb_models = list_models_by_sport("MLB")
    assert len(mlb_models) >= 4
    model_ids = {m.model_id for m in mlb_models}
    assert "mlb-elo-trend-lr-v8" in model_ids
    assert "measured-edge-margin-v3" in model_ids
    assert "measured-edge-totals-v3" in model_ids


def test_decorate_pick_includes_display_and_standard_names() -> None:
    """_decorate_pick attaches recognizable model names to pick rows."""
    row = {
        "event_id": "test-event-1",
        "sport": "mlb",
        "model_version": "measured-edge-margin-v3",
        "market_type": "spread",
        "selection": "away",
        "status": "open",
    }
    decorated = _decorate_pick(row)
    assert decorated["model_display_name"] == "MLB Spread Runline (Margin v3)"
    assert decorated["model_standard_name"] == "mlb-spread-margin-v3"


def test_get_model_descriptor_and_aliases() -> None:
    """get_model_descriptor retrieves descriptors and MODEL_ALIASES maps aliases."""
    assert "mlb-spread-margin-v3" in MODEL_ALIASES
    desc = get_model_descriptor("mlb-spread-margin-v3")
    assert desc is not None
    assert desc.model_id == "measured-edge-margin-v3"
    assert desc.sport == "MLB"
    assert desc.market == "spread"

    # Non-existent model returns None
    assert get_model_descriptor("non-existent-model-xyz") is None
