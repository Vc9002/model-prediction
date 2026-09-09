import pytest

from model_prediction.portfolio import auto_buyer_ledger, auto_executor


@pytest.mark.parametrize("mode", ["paper", "live"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("override", [None, False, True])
def test_paper_mode_cannot_be_overridden_into_live_execution(tmp_path, monkeypatch, mode, enabled, override):
    monkeypatch.setattr(auto_executor, "DATA", tmp_path)
    monkeypatch.setattr(auto_executor, "PAPER_AUTO_BUYER_DATA_ROOT", tmp_path / "paper")
    monkeypatch.setattr(auto_executor, "load_auto_buyer_state", lambda: {"mode": mode, "enabled": enabled})
    monkeypatch.setattr(auto_executor, "save_auto_buyer_state", lambda state: None)
    monkeypatch.setattr(auto_executor, "_append_auto_buyer_cycle_history", lambda summary: None)
    reconciled = []
    settled = []
    monkeypatch.setattr(
        auto_buyer_ledger, "reconcile_pending_auto_buyer_fallbacks", lambda: reconciled.append(True) or {}
    )
    monkeypatch.setattr(
        auto_buyer_ledger,
        "settle_auto_buyer_ledger",
        lambda *args, **kwargs: settled.append(kwargs.get("data_root")) or {},
    )
    configs = []

    def evaluate(self):
        configs.append(self.config)
        return auto_executor.AutoExecutionResult()

    monkeypatch.setattr(auto_executor.AutoPolymarketBuyer, "evaluate_and_execute", evaluate)
    auto_executor.run_auto_buyer_cycle(execute_override=override)
    expected_live = mode == "live" and (enabled if override is None else bool(override))
    assert configs[0].execute_live is expected_live
    assert bool(reconciled) is expected_live
    if mode == "paper":
        assert all(path == tmp_path / "paper" for path in settled)
