"""The NFL margin moneyline challenger logs its own Flat row beside the champion.

It must never write to Main, never gate another pick through the cross-market
check, never price off a quote captured after the slate, and never run against
a parent spread artifact other than the one its evidence was replayed on.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from model_prediction.cli import forecast as cli_forecast
from model_prediction.cli import forecast_nfl_moneyline_shadow as shadow
from model_prediction.eligibility import Exposure
from model_prediction.production_registry import compute_artifact_hash

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 13, 14, tzinfo=UTC)


class _CaptureLedger:
    def __init__(self) -> None:
        self.appended: list = []

    def exposure(self, request, now=None, **kwargs):
        return Exposure()

    def rows(self, filters=None):
        return []

    def append_evaluated(self, request, eligibility, now=None):
        self.appended.append((request, eligibility))
        return {"pick_id": f"pick-{len(self.appended)}"}


def _config() -> dict:
    return {
        "project": {"maximum_data_age_hours": 12, "maximum_unreviewed_market_disagreement": 0.10},
        "bankroll": {},
    }


def _slate(prob_home_win: float) -> dict:
    return {
        "observed_at_utc": NOW.isoformat(),
        "priced_contracts": [],
        "moneyline_shadow_inputs": [
            {
                "event_id": "nfl-ml-1",
                "event_start_utc": "2026-09-13T17:00:00Z",
                "home_team": "Buffalo Bills",
                "away_team": "Kansas City Chiefs",
                "prob_home_win": prob_home_win,
                "projected_margin_home": 4.8,
                "spread_home_line": -3.5,
                "input_qualified": True,
            }
        ],
    }


def _quote(tmp_path: Path, ask: float) -> dict:
    record = {
        "market_slug": "nfl-kc-buf-2026-09-13",
        "market_type": "moneyline",
        "provider": "polymarket_us",
        "observed_at_utc": "2026-09-13T12:00:00Z",
        "timestamp_valid": True,
        "reconstructed": False,
        "usage": "prospective_executable_bbo",
    }
    archive = tmp_path / "polymarket_snapshots.jsonl"
    archive.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return {
        "market_slug": record["market_slug"],
        "_archive_record": record,
        "_archive_path": str(archive),
        "executable_ask": ask,
        "no_vig_probability": 0.63,
        "observed_at_utc": record["observed_at_utc"],
        "timestamp_valid": True,
    }


def _log(monkeypatch, tmp_path, registry, ban_list, prob_home_win: float, quote):
    seen: list = []

    def _match(data_root, sport, game_date, candidate):
        seen.append(candidate)
        return quote(candidate) if callable(quote) else quote

    monkeypatch.setattr(shadow, "match_executable_quote", _match)
    flat = _CaptureLedger()
    result = shadow.log_nfl_margin_moneyline_shadow(
        forecast=_slate(prob_home_win),
        data_root=tmp_path,
        args_date="2026-09-13",
        config=_config(),
        registry=registry,
        bans=ban_list,
        flat_ledger=flat,
        observed_now=NOW,
    )
    return result, flat, seen


def test_challenger_logs_a_flat_paper_row_for_the_margin_favourite(monkeypatch, tmp_path, registry, ban_list):
    result, flat, seen = _log(monkeypatch, tmp_path, registry, ban_list, 0.69396, _quote(tmp_path, 0.64))
    assert result["status"] == "ok" and result["logged"] == 1
    request, eligibility = flat.appended[0]
    assert request.model_version == "nfl-moneyline-margin-v1"
    assert request.market_type.value == "moneyline" and request.selection == "home"
    assert request.model_probability == pytest.approx(0.69396)
    assert request.sportsbook == "polymarket_us"
    assert request.market_quote_provenance == "decision_time_executable_quote"
    # Never a served call: the registry champion is a different model.
    assert eligibility.qualified is False and eligibility.reason_code.startswith("PAPER_CALL_")
    # The quote is bounded to the slate's decision time.
    assert seen[0].model_input_snapshot == {"observed_at_utc": NOW.isoformat()}


def test_challenger_takes_the_away_side_when_the_margin_favours_it(monkeypatch, tmp_path, registry, ban_list):
    _, flat, seen = _log(monkeypatch, tmp_path, registry, ban_list, 0.41, _quote(tmp_path, 0.55))
    request, _ = flat.appended[0]
    assert seen[0].selection == "away" and request.selection == "away"
    assert request.model_probability == pytest.approx(0.59)


def test_missing_quote_is_an_explicit_no_call_and_writes_nothing(monkeypatch, tmp_path, registry, ban_list):
    result, flat, _ = _log(monkeypatch, tmp_path, registry, ban_list, 0.69, None)
    assert flat.appended == []
    assert result["skipped"] == [{"event_id": "nfl-ml-1", "reason": "NO_CALL_MONEYLINE_QUOTE_UNAVAILABLE"}]


def test_changed_parent_artifact_blocks_the_challenger(monkeypatch, tmp_path, registry, ban_list):
    real = cli_forecast._load_exact_artifact_contract

    def _load(model_version: str):
        payload, error = real(model_version)
        if model_version == shadow.NFL_MARGIN_MONEYLINE_PARENT and payload is not None:
            payload = {**payload, "artifact_hash": "0" * 64}
        return payload, error

    monkeypatch.setattr(cli_forecast, "_load_exact_artifact_contract", _load)
    result, flat, _ = _log(monkeypatch, tmp_path, registry, ban_list, 0.69, _quote(tmp_path, 0.64))
    assert flat.appended == []
    assert result["status"] == "blocked" and result["reason"] == "NO_CALL_PARENT_ARTIFACT_HASH_MISMATCH"


def test_shadow_rows_are_invisible_to_the_cross_market_check():
    champion = {"model_version": "nfl-elo-trend-lr-v4", "decision": "CALL"}
    challenger = {"model_version": "nfl-moneyline-margin-v1", "decision": "CALL"}
    assert shadow.without_moneyline_shadow_rows([champion, challenger]) == [champion]


def test_only_the_replayed_parent_engine_feeds_the_challenger():
    forecast = SimpleNamespace(prob_home_win=0.6, projected_margin_home=2.0)
    kwargs = {
        "event_id": "e",
        "event_start_utc": "2026-09-13T17:00:00Z",
        "home_team": "Buffalo Bills",
        "away_team": "Kansas City Chiefs",
        "forecast": forecast,
        "spread_home_line": -1.5,
        "input_qualified": True,
    }
    assert shadow.moneyline_shadow_input(parent_model_version="nfl-spread-structural-v6", **kwargs) is None
    entry = shadow.moneyline_shadow_input(parent_model_version="nfl-spread-structural-v7", **kwargs)
    assert entry is not None and entry["prob_home_win"] == 0.6


def test_artifact_self_verifies_and_pins_the_current_parent():
    artifact = json.loads((PROJECT_ROOT / "config/models/nfl-moneyline-margin-v1.json").read_text("utf-8"))
    parent = json.loads((PROJECT_ROOT / "config/models/nfl-spread-structural-v7.json").read_text("utf-8"))
    assert artifact["artifact_hash"] == compute_artifact_hash(artifact)
    assert artifact["parent_artifact_hash"] == parent["artifact_hash"]
    assert artifact["qualification"]["qualified"] is False
