from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v9_forecast_requires_real_decision_price_and_preserves_model_history() -> None:
    source = (ROOT / "scripts" / "forecast_mlb_v9_benchmark.py").read_text(encoding="utf-8")

    assert "decision_quote(" in source
    assert 'provider="polymarket_us"' in source
    assert "market_probability_at_decision=decision_probability" in source
    assert "pick_prob - decision_probability" in source
    assert "american_odds=-110" not in source
    assert "model_v9_path.unlink()" not in source


def test_v9_settlement_propagates_to_global_model_ledger() -> None:
    source = (ROOT / "scripts" / "forecast_mlb_v9_benchmark.py").read_text(encoding="utf-8")

    settle_block = source.split("def settle_v9_flat_ledger", 1)[1]
    assert "ModelLedger(V9_MODEL_LEDGER_PATH).settle_event(" in settle_block
