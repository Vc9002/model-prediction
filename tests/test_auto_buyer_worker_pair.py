import json
from pathlib import Path

from model_prediction import auto_buyer_worker as worker


def test_worker_runs_paper_when_live_fails(monkeypatch, capsys):
    monkeypatch.setattr(worker, "enable_background_priority", lambda: None)
    monkeypatch.setattr(
        worker.RuntimePaths, "resolve", lambda **_: type("Paths", (), {"lock_root": Path(".")})()
    )
    monkeypatch.setattr(worker, "acquire_lock", lambda _: type("Lock", (), {"close": lambda self: None})())
    monkeypatch.setattr(
        worker, "run_auto_buyer_cycle", lambda **_: (_ for _ in ()).throw(RuntimeError("live failed"))
    )
    called = []
    monkeypatch.setattr(
        worker,
        "run_ncaab_spread_paper_trial",
        lambda: called.append(True) or {"status": "paper", "paper_orders": 0},
    )

    assert worker.main() == 1
    assert called == [True]
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "error"
    assert output["ncaab_spread_paper_trial"]["status"] == "paper"
