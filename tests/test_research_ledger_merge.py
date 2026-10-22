"""Esports / KBO / NPB route to Flat (every candidate) and Main (gated CALLs).

The per-sport research and gated-research ledgers were merged into Flat and Main on
2026-10-22; the legacy ledgers are no longer written.
"""

from __future__ import annotations

from datetime import UTC, datetime

import model_prediction.cli.forecast as cli_forecast
from model_prediction import cli
from model_prediction.main_ledgers import MAIN_LEDGER_SPORTS, MultiSportPickLedger
from model_prediction.research_ledgers import RESEARCH_LEDGER_SPORTS, existing_research_ledgers

CONFIG = {
    "models": {"LOL": {"min_edge": 0.02, "research_confidence_gate": 0.0, "status": "shadow_qualified"}},
    "project": {"maximum_data_age_hours": 12, "maximum_unreviewed_market_disagreement": 0.10},
    "bankroll": {},
}


def _forecast() -> dict:
    base = {
        "event_start_utc": "2026-07-27T10:00:00Z",
        "teams": ["Team A", "Team B"],
        "observed_at_utc": "2026-07-26T10:00:00Z",
        "artifact_hash": "artifact-hash",
    }

    def contract(event_id: str, ask: float, valid: bool) -> dict:
        return {
            **base,
            "event_id": event_id,
            "market_slug": event_id,
            "gated_research_eligible": valid,
            "sides": [
                {"team": "Team A", "model_probability": 0.60, "executable_ask": ask},
                {"team": "Team B", "model_probability": 0.40, "executable_ask": 1 - ask},
            ],
        }

    return {
        "title": "lol",
        "model_version": "lol-tiered-elo-v4",
        "priced_contracts": [
            contract("valid", 0.55, True),
            contract("negative-edge", 0.62, True),
            contract("untrained-team", 0.55, False),
        ],
    }


def test_research_sports_are_main_and_flat_sports() -> None:
    assert set(RESEARCH_LEDGER_SPORTS) <= set(MAIN_LEDGER_SPORTS)
    assert set(RESEARCH_LEDGER_SPORTS) <= cli.DUAL_LEDGER_SPORTS


def test_esports_candidates_land_in_flat_and_main_and_not_in_legacy_research(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli_forecast, "utc_now", lambda: datetime(2026, 7, 26, 12, tzinfo=UTC))
    main = MultiSportPickLedger(tmp_path)
    flat = MultiSportPickLedger(tmp_path, flat=True)

    logged = cli._log_esports_forecast(_forecast(), CONFIG, flat, flat_mode=False, gated_ledger=main)

    assert logged == 3
    expected = {"valid", "negative-edge", "untrained-team"}
    assert {row["event_id"] for row in flat.rows()} == expected
    assert {row["event_id"] for row in main.rows()} == expected
    assert {row["league"] for row in flat.rows()} == {"LOL"}
    assert {row["league"] for row in main.rows()} == {"LOL"}
    assert existing_research_ledgers(tmp_path) == []
    assert existing_research_ledgers(tmp_path, gated=True) == []


def test_rerunning_the_same_forecast_adds_no_duplicates(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli_forecast, "utc_now", lambda: datetime(2026, 7, 26, 12, tzinfo=UTC))
    main = MultiSportPickLedger(tmp_path)
    flat = MultiSportPickLedger(tmp_path, flat=True)

    cli._log_esports_forecast(_forecast(), CONFIG, flat, flat_mode=False, gated_ledger=main)
    cli._log_esports_forecast(_forecast(), CONFIG, flat, flat_mode=False, gated_ledger=main)

    assert len(flat.rows()) == 3
    assert len(main.rows()) == 3
