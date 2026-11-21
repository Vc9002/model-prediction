"""NBA serving plumbing (NBA review 2026-11-20, section 3, B2: totals thin-sample Over floor)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from model_prediction.cli import forecast as forecast_cli

NOW = datetime(2026, 11, 19, 20, 0, tzinfo=UTC)
TIP_POLY = "2026-11-20T01:00:00Z"  # Polymarket snapshot form
OBSERVED = (NOW - timedelta(seconds=60)).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------- B2


def _total_contract(event_id: str, selection: str, edge: float, features: dict | None) -> dict:
    return {
        "event_id": event_id,
        "event_start_utc": TIP_POLY,
        "away_team": "Away",
        "home_team": "Home",
        "market_type": "total",
        "selection": selection,
        "line": 228.5,
        "model_probability": 0.60,
        "model_uncertainty": 0.03,
        "model_version": "nba-total-margin-v9",
        "model_artifact_hash": "test-hash",
        "model_qualified": True,
        "market_lineage": {},
        "market_slug": event_id,
        "rationale": "Test",
        "executable_ask": round(0.60 - edge, 6),
        "observed_at_utc": OBSERVED,
        "model_input_snapshot": None if features is None else {"features": features},
    }


def _run_total_logging(monkeypatch, tmp_path, contracts: list[dict]) -> tuple[list, object, object, dict]:
    monkeypatch.setattr(forecast_cli, "utc_now", lambda: NOW)
    monkeypatch.setattr(forecast_cli.PickRequest, "validate", lambda self, now: None)
    monkeypatch.setattr(
        forecast_cli, "_forecast_nba_total_slate", lambda *args: {"priced_contracts": contracts}
    )
    monkeypatch.setattr(forecast_cli, "unit_policy", lambda config: object())
    monkeypatch.setattr(forecast_cli, "_is_registered_serving_model", lambda *args: True)
    monkeypatch.setattr(
        forecast_cli,
        "evaluate_eligibility",
        lambda *args, **kwargs: SimpleNamespace(decision="CALL", qualified=True),
    )
    writes: list = []
    monkeypatch.setattr(
        forecast_cli,
        "_append_secondary_ledger",
        lambda ledger, request, eligibility, observed_at, source: writes.append((ledger, request.event_id)),
    )
    flat = SimpleNamespace(exposure=lambda *args, **kwargs: object())
    main = object()
    result = forecast_cli._forecast_nba_total_sport(
        data_root=tmp_path,
        args_date="2026-11-19",
        config={
            "models": {"NBA": {"min_edge": 0.05}},
            "project": {"maximum_unreviewed_market_disagreement": 0.10},
        },
        registry=SimpleNamespace(resolve=lambda *args: SimpleNamespace(canonical_team_id="t")),
        bans=None,
        flat_ledger=flat,
        main_ledger=main,
    )
    return writes, flat, main, result


def test_thin_sample_over_floor_reads_the_stored_snapshot_key(monkeypatch, tmp_path):
    contracts = [
        # True current-season count wins over the engine's lifetime count.
        _total_contract(
            "thin_true", "over", 0.07, {"current_season_games_played_min": 6, "current_season_games_min": 220}
        ),
        # Legacy snapshot without the new key: the stored engine field is read (B2 alone).
        _total_contract("thin_legacy", "over", 0.07, {"current_season_games_min": 6}),
        _total_contract("thin_big_edge", "over", 0.095, {"current_season_games_played_min": 6}),
        _total_contract(
            "mature", "over", 0.07, {"current_season_games_played_min": 20, "current_season_games_min": 220}
        ),
        _total_contract("thin_under", "under", 0.07, {"current_season_games_played_min": 6}),
        _total_contract("no_snapshot", "over", 0.07, None),
        _total_contract("garbage", "over", 0.07, {"current_season_games_played_min": "n/a"}),
        _total_contract("null_value", "over", 0.07, {"current_season_games_played_min": None}),
    ]
    writes, flat, main, result = _run_total_logging(monkeypatch, tmp_path, contracts)
    assert result["skipped"] == []
    # Flat calls everything, gated or not.
    assert [event_id for ledger, event_id in writes if ledger is flat] == [c["event_id"] for c in contracts]
    assert [event_id for ledger, event_id in writes if ledger is main] == [
        "thin_big_edge",
        "mature",
        "thin_under",
        "no_snapshot",
        "garbage",
        "null_value",
    ]
