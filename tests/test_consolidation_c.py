"""Tests for consolidation C: data service, event identity, execution boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from model_prediction.event_identity import (
    canonicalize,
    map_same_event,
    mappings_for,
)
from model_prediction.production_store import ProductionPredictionStore
from model_prediction.runtime_paths import RuntimePaths

# ── event identity (item 14) ────────────────────────────────────────────────


def test_espn_ids_are_canonical_by_definition(tmp_path: Path) -> None:
    assert canonicalize("espn", "401690001", "WNBA", repo_root=tmp_path) == "401690001"


def test_provider_ids_register_and_map_to_a_canonical_event(tmp_path: Path) -> None:
    first = canonicalize("polymarket", "token-123", "WNBA", repo_root=tmp_path)
    assert first == "polymarket:token-123"
    # Idempotent on re-sight.
    assert canonicalize("polymarket", "token-123", "WNBA", repo_root=tmp_path) == first

    # A real same-event mapping replaces the provisional prefixed id.
    map_same_event("polymarket", "token-123", "401690001", "WNBA", repo_root=tmp_path)
    assert canonicalize("polymarket", "token-123", "WNBA", repo_root=tmp_path) == "401690001"
    assert mappings_for("401690001", repo_root=tmp_path) == [
        {"provider": "polymarket", "provider_event_id": "token-123", "sport": "WNBA"}
    ]


# ── execution ticket boundary (item 13) ─────────────────────────────────────


def test_ticket_round_trips_and_rejects_tampering(tmp_path: Path, monkeypatch) -> None:
    from model_prediction import execution_ticket

    monkeypatch.setattr(
        execution_ticket,
        "_secret_path",
        lambda: RuntimePaths.for_test(tmp_path).runtime_root / "execution_secret.key",
    )
    ticket = execution_ticket.create_ticket(
        {"side": "buy", "event_id": "e1", "contract": "ct1", "amount_usd": 5.0}
    )
    payload = execution_ticket.verify_ticket(ticket)
    assert payload["order"]["side"] == "buy"
    assert payload["order"]["amount_usd"] == 5.0

    # Tampering with the body breaks the signature.
    body, _, signature = ticket.rpartition(".")
    tampered = f'{body[:-4]}"x":0{"}".join(["}"] if body.endswith("}") else [])}.{signature}'
    with pytest.raises(ValueError, match="signature"):
        execution_ticket.verify_ticket(tampered)


def test_expired_ticket_rejected(tmp_path: Path, monkeypatch) -> None:
    from model_prediction import execution_ticket

    monkeypatch.setattr(
        execution_ticket,
        "_secret_path",
        lambda: RuntimePaths.for_test(tmp_path).runtime_root / "execution_secret.key",
    )
    ticket = execution_ticket.create_ticket({"side": "sell"}, ttl_seconds=-1)
    with pytest.raises(ValueError, match="expired"):
        execution_ticket.verify_ticket(ticket)


def test_research_and_shadow_have_no_import_path_to_execution(tmp_path: Path) -> None:
    """Item 13: research and shadow processes must have no import path or
    credential access to order execution. Scan the rebuild package (and
    the research-ledger modules) for references to execution machinery."""
    import subprocess
    import sys

    out = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import pathlib, re\n"
                "text = ''.join(p.read_text(errors='ignore') for p in "
                "pathlib.Path('src/model_prediction/rebuild').rglob('*.py'))\n"
                "bad = [line for line in text.splitlines() if re.search("
                "r'execution_ticket|dashboard_server|cli\\.execute|place_order|"
                "sell_position', line)]\n"
                "print('\\n'.join(bad[:10]))\n"
                "raise SystemExit(1 if bad else 0)\n"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, f"rebuild references execution machinery:\n{out.stdout}"


# ── read-only data service (items 10-12) ────────────────────────────────────


def test_data_service_predictions_are_paginated_and_readonly(tmp_path: Path, monkeypatch) -> None:
    from model_prediction.dashboard import data_service

    paths = RuntimePaths.for_test(tmp_path)
    monkeypatch.setattr(data_service, "_paths", lambda: paths)
    with ProductionPredictionStore(paths) as store:
        run_id = store.start_run()
        for i in range(5):
            store.append_prediction(
                run_id=run_id,
                prediction_id=f"p{i}",
                event_id=f"e{i}",
                sport="WNBA",
                market="moneyline",
                market_type="moneyline",
                model_id="wnba-elo-trend-lr-v4",
                probabilities={"home": 0.6, "away": 0.4},
                decision_time_utc=f"2026-08-14T12:00:0{i}+00:00",
            )

        page1 = data_service.handle("predictions", {"limit": ["2"]})
        assert len(page1["predictions"]) == 2
        assert page1["next_cursor"] is not None
        page2 = data_service.handle(
            "predictions", {"limit": ["2"], "cursor": [str(page1["next_cursor"])]}
        )
        page3 = data_service.handle(
            "predictions", {"limit": ["2"], "cursor": [str(page2["next_cursor"])]}
        )
        assert len(page2["predictions"]) == 2 and len(page3["predictions"]) == 1
        assert page3["next_cursor"] is None

        counts = data_service.handle("predictions/counts", {})
        assert counts["counts"] == {"predicted": 5}

        versions = data_service.handle("versions", {})
        assert versions["parts"]["predictions"]["n"] == 5

    # Serving reads must not mutate the database: the file mtime is stable.
    import os

    before = os.stat(paths.production_db).st_mtime
    data_service.handle("predictions", {"limit": ["1"]})
    data_service.handle("versions", {})
    assert os.stat(paths.production_db).st_mtime == before


def test_data_service_health_and_missing_dbs(tmp_path: Path, monkeypatch) -> None:
    from model_prediction.dashboard import data_service

    monkeypatch.setattr(data_service, "_paths", lambda: RuntimePaths.for_test(tmp_path))
    result = data_service.handle("predictions", {})
    assert result["predictions"] == [] and "no production database" in result["note"]
    result = data_service.handle("runs", {})
    assert result["runs"] == []
    health = data_service.handle("health", {})
    assert health["status"] in ("HEALTHY", "DEGRADED", "DOWN")
    with pytest.raises(KeyError):
        data_service.handle("does-not-exist", {})
