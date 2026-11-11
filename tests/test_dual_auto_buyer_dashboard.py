import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from model_prediction.portfolio import ncaab_paper_trial as trial


def test_paper_trial_toggle_is_independent_and_preserves_bucket(tmp_path, monkeypatch):
    path = tmp_path / "auto_buyer_ncaab_spread_paper_trial.json"
    settings = {
        "enabled": True,
        "model_id": "ncaab-spread-v6",
        "market_type": "spread",
        "min_saved_edge": 0.05,
        "max_saved_edge_exclusive": 0.08,
    }
    path.write_text(json.dumps(settings), encoding="utf-8")
    monkeypatch.setattr(trial.RuntimePaths, "resolve", lambda: SimpleNamespace(runtime_root=tmp_path))

    assert trial.set_paper_trial_enabled(False) == {**settings, "enabled": False}
    assert trial.paper_trial_status() == {**settings, "enabled": False}
    with pytest.raises(TypeError, match="boolean"):
        trial.set_paper_trial_enabled("true")
    assert json.loads(path.read_text(encoding="utf-8")) == {**settings, "enabled": False}


def test_dashboard_uses_separate_paper_route_without_live_override():
    html = Path("dashboard.html").read_text(encoding="utf-8")
    assert 'id="ncaabPaperToggleBtn"' in html
    assert 'id="ncaabPaperStatus"' in html
    assert '"/api/auto-buyer/ncaab-paper/toggle"' in html
    assert '"/api/auto-buyer/ncaab-paper/status"' in html
    assert '"/api/auto-buyer/ncaab-paper/run"' in html
    assert "body:JSON.stringify({confirm:true,enabled})" in html
    assert "body: JSON.stringify({ confirm: true, execute: isLive })" not in html
