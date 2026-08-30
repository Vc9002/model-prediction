from model_prediction.model_ledger import ModelLedger, _event_settlement_key
from scripts import audit_ledger_pnl


def test_void_event_is_valid_evidence_only_for_zero_pnl_push() -> None:
    event = {"event_type": "pick_voided", "event_id": "void-1", "payload": {"reason": "void"}}

    assert audit_ledger_pnl._matching_audit_event({"result": "push", "pnl_units": "0.0000"}, [event]) == event
    assert audit_ledger_pnl._matching_audit_event({"result": "push", "pnl_units": "0.5000"}, [event]) is None


def test_model_repair_planner_never_treats_backups_as_active_ledgers(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(audit_ledger_pnl, "DATA", tmp_path)
    model_root = tmp_path / "model_ledgers"
    row = {
        "model_id": "example-model",
        "model_version": "example-v1",
        "event_id": "event-1",
        "market_type": "moneyline",
        "selection": "home",
        "line": "",
        "status": "open",
    }
    active = model_root / "example-model.xlsx"
    backup = model_root / "example-model.bak-pnl-sync-20260823T000000Z.xlsx"
    ModelLedger(active).append_prediction(row)
    ModelLedger(backup).append_prediction(row)
    source = {**row, "status": "settled", "result": "win", "pnl_units": "1.0000"}

    planned = audit_ledger_pnl._plan_model_repairs({_event_settlement_key(source): source})

    assert list(planned) == [active]
    assert len(planned[active]) == 1


def _row(pnl: str, units: str, result: str = "win", close: str = "0.61", clv: str = "0.0001") -> dict:
    return {
        "result": result,
        "pnl_units": pnl,
        "units": units,
        "closing_implied_probability": close,
        "probability_clv": clv,
    }


def test_economic_signature_folds_stake_sizing_out_of_losses() -> None:
    # Same loss, different tier sizing (-1.25U vs -1.5U) — the economic
    # content is identical (loss = full stake), so signatures must match.
    assert audit_ledger_pnl._economic_signature(
        _row("-1.25", "1.25", result="loss")
    ) == audit_ledger_pnl._economic_signature(_row("-1.5", "1.5", result="loss"))


def test_economic_signature_still_detects_real_quote_conflicts() -> None:
    # Same win, same 1.0U stake, different quotes (-156 vs -122) — the
    # stake-normalized pnl differs, so this MUST remain a conflict.
    assert audit_ledger_pnl._economic_signature(
        _row("0.6410", "1.00")
    ) != audit_ledger_pnl._economic_signature(_row("0.8197", "1.00"))


def test_economic_signature_agrees_when_quote_matches_across_sizing() -> None:
    # Same quote, different sizing (1.0U vs 1.25U) — identical
    # stake-normalized economics, so signatures must match.
    assert audit_ledger_pnl._economic_signature(_row("0.80", "1.00")) == audit_ledger_pnl._economic_signature(
        _row("1.00", "1.25")
    )


def test_economic_signature_handles_missing_units_safely() -> None:
    assert audit_ledger_pnl._economic_signature(_row("1.0000", "")) == (
        "win",
        None,
        0.61,
        0.0001,
    )
