import json
from types import SimpleNamespace

from model_prediction.portfolio import ncaab_paper_trial as trial
from model_prediction.portfolio.auto_executor import (
    AutoExecutionConfig,
    _is_disabled_auto_buyer_sport_market,
    _is_paper_ncaab_spread_v6_trial,
)
from model_prediction.portfolio.ncaab_paper_trial import eligible_trial_pick


def test_trial_cohort_is_exact_model_market_and_saved_edge():
    base = {"model_version": "ncaab-spread-v6", "sport": "ncaab", "market_type": "spread", "status": "open"}
    assert eligible_trial_pick({**base, "edge": "0.050000"}, min_edge=0.05, max_edge=0.08)
    assert eligible_trial_pick({**base, "edge": "0.079999"}, min_edge=0.05, max_edge=0.08)
    for row in (
        {**base, "edge": "0.049999"},
        {**base, "edge": "0.080000"},
        {**base, "edge": "nan"},
        {**base, "market_type": "moneyline", "edge": "0.06"},
        {**base, "market_type": "total", "edge": "0.06"},
        {**base, "model_version": "ncaab-spread-v5", "edge": "0.06"},
    ):
        assert not eligible_trial_pick(row, min_edge=0.05, max_edge=0.08)


def test_live_ncaab_spread_remains_blocked():
    row = {"model_version": "ncaab-spread-v6", "sport": "ncaab", "market_type": "spread"}
    assert _is_disabled_auto_buyer_sport_market(row)
    config = AutoExecutionConfig(mode="paper", paper_ncaab_spread_v6_trial=True)
    assert config.execute_live is False
    assert _is_paper_ncaab_spread_v6_trial(row, config)
    assert not _is_paper_ncaab_spread_v6_trial(
        row, AutoExecutionConfig(mode="live", paper_ncaab_spread_v6_trial=True)
    )
    assert not _is_paper_ncaab_spread_v6_trial({**row, "market_type": "moneyline"}, config)
    assert not _is_paper_ncaab_spread_v6_trial({**row, "market_type": "total"}, config)
    assert not _is_paper_ncaab_spread_v6_trial({**row, "model_version": "ncaab-spread-v5"}, config)


def test_trial_runner_passes_only_exact_cohort_to_paper_buyer(tmp_path, monkeypatch):
    (tmp_path / "auto_buyer_ncaab_spread_paper_trial.json").write_text(
        json.dumps(
            {
                "enabled": True,
                "model_id": "ncaab-spread-v6",
                "market_type": "spread",
                "min_saved_edge": 0.05,
                "max_saved_edge_exclusive": 0.08,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(trial.RuntimePaths, "resolve", lambda: SimpleNamespace(runtime_root=tmp_path))
    base = {"model_version": "ncaab-spread-v6", "sport": "ncaab", "market_type": "spread", "status": "open"}
    monkeypatch.setattr(
        trial,
        "read_picks",
        lambda: [
            {**base, "pick_id": "good", "edge": "0.06"},
            {**base, "pick_id": "too_high", "edge": "0.08"},
            {**base, "pick_id": "total", "market_type": "total", "edge": "0.06"},
            {**base, "pick_id": "settled", "status": "settled", "edge": "0.06"},
        ],
    )
    monkeypatch.setattr(trial, "read_flat_picks", list)
    monkeypatch.setattr(trial, "settle_auto_buyer_ledger", lambda **_: None)
    captured = {}

    class PaperBuyer:
        def __init__(self, *, config, data_root):
            captured["config"] = config
            captured["data_root"] = data_root

        def evaluate_and_execute(self, picks):
            captured["picks"] = picks
            return SimpleNamespace(dry_run_orders=[])

    monkeypatch.setattr(trial, "AutoPolymarketBuyer", PaperBuyer)
    assert trial.run_ncaab_spread_paper_trial() == {
        "status": "paper",
        "candidates": 1,
        "paper_orders": 0,
    }
    assert [p["pick_id"] for p in captured["picks"]] == ["good"]
    assert captured["config"].mode == "paper"
    assert captured["config"].execute_live is False
    assert captured["config"].whitelisted_models == ("ncaab-spread-v6",)
