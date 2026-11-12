import json
from pathlib import Path

from model_prediction import auto_buyer_worker as worker


def test_worker_runs_paper_when_live_fails(monkeypatch, capsys):
    monkeypatch.setattr(worker, "enable_background_priority", lambda: None)
    monkeypatch.setattr(
        worker.RuntimePaths, "resolve", lambda **_: type("Paths", (), {"lock_root": Path(".")})()
    )
    monkeypatch.setattr(
        worker, "acquire_lock", lambda _, **__: type("Lock", (), {"close": lambda self: None})()
    )
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


def test_worker_waits_for_a_running_forecast_instead_of_skipping(monkeypatch, capsys):
    """A non-blocking lock attempt skipped every cycle that started while the
    forecast was still writing, so freshly refreshed picks waited a slot or more."""
    monkeypatch.setattr(worker, "enable_background_priority", lambda: None)
    monkeypatch.setattr(
        worker.RuntimePaths, "resolve", lambda **_: type("Paths", (), {"lock_root": Path(".")})()
    )
    waits = []

    def fake_lock(_path, *, wait_seconds=0.0):
        waits.append(wait_seconds)
        return type("Lock", (), {"close": lambda self: None})()

    monkeypatch.setattr(worker, "acquire_lock", fake_lock)
    monkeypatch.setattr(worker, "run_auto_buyer_cycle", lambda **_: {})
    monkeypatch.setattr(worker, "run_ncaab_spread_paper_trial", lambda: {"status": "disabled"})

    assert worker.main() == 0
    capsys.readouterr()
    # Long enough to outlast a forecast already ten minutes in, short enough to
    # finish inside the supervisor's kill timeout and before the next trigger.
    assert 600 <= waits[0] < 1500
