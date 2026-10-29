"""V3 must bind to v6 inputs and preserve a coherent pregame forecast."""

import json
from datetime import UTC, date, datetime

import pytest

from model_prediction.models.nfl_joint_market_v3 import forecast_packet
from model_prediction.models.nfl_market_anchor_v1 import SettledQuote
from model_prediction.production_registry import compute_artifact_hash
from scripts.forecast_nfl_joint_market_v3 import load_verified_packet


def _packet() -> dict:
    return {
        "schema": "nfl-joint-decision-packet-v1",
        "event_id": "next-game",
        "event_start_utc": "2026-11-01T18:00:00Z",
        "captured_at_utc": "2026-11-01T16:00:00Z",
        "replay_ready": True,
        "markets": {
            "moneyline": {
                "status": "verified", "selection": "home", "model_probability": 0.55,
                "model_snapshot": {"artifact_hash": "moneyline", "model_version": "nfl-elo-trend-lr-v4"},
                "quote": {
                    "no_vig_selected": 0.60, "selected_ask": 0.60,
                    "long_ask": 0.60, "short_ask": 0.40,
                    "lineage": {"market_snapshot_record_id": "ml-quote"},
                },
            },
            "spread": {
                "status": "verified", "selection": "home", "line": -3.5,
                "model_snapshot": {
                    "artifact_hash": "spread-v6", "model_version": "nfl-spread-structural-v6",
                    "features": {"input_qualified": True},
                    "inference_inputs": {"mean": 2.0, "sd": 14.5},
                },
                "quote": {
                    "line": -3.5, "long_ask": 0.52, "short_ask": 0.49,
                    "lineage": {"market_snapshot_record_id": "spread-quote"},
                },
            },
            "total": {
                "status": "verified", "selection": "over", "line": 44.5,
                "model_snapshot": {
                    "artifact_hash": "total-v6", "model_version": "nfl-total-structural-v6",
                    "features": {"input_qualified": True},
                    "inference_inputs": {"mean": 45.0, "sd": 14.2},
                    "artifact": {"league_avg_total": 45.5},
                },
                "quote": {
                    "line": 44.5, "long_ask": 0.51, "short_ask": 0.50,
                    "lineage": {"market_snapshot_record_id": "total-quote"},
                },
            },
        },
    }


ARTIFACT = {
    "model_version": "nfl-joint-market-v3",
    "inputs": {
        "moneyline_artifact_hash": "moneyline",
        "spread_artifact_hash": "spread-v6",
        "total_artifact_hash": "total-v6",
    },
}
ARTIFACT["artifact_hash"] = compute_artifact_hash(ARTIFACT)


def test_v3_forecast_is_joint_and_uses_prior_settled_weight() -> None:
    history = [
        SettledQuote(date(2026, 10, 25), 0.50, 0.60, 1, datetime(2026, 10, 26, tzinfo=UTC))
    ]
    result = forecast_packet(_packet(), ARTIFACT, history)
    assert result["market_weight"] == 1.0
    assert result["home_win_probability"] == pytest.approx(0.60)
    assert result["selected_probabilities"]["moneyline"] == pytest.approx(0.60)
    assert result["selected_probabilities"]["spread"] <= result["home_win_probability"]
    assert all(0 < value < 1 for value in result["selected_probabilities"].values())
    assert result["contracts"]["spread"] == {
        "selection": "home", "line": -3.5, "ask": 0.52, "quote_record_id": "spread-quote"
    }


def test_v3_rejects_wrong_parent_or_unqualified_source() -> None:
    history = [
        SettledQuote(date(2026, 10, 25), 0.50, 0.60, 1, datetime(2026, 10, 26, tzinfo=UTC))
    ]
    packet = _packet()
    packet["markets"]["spread"]["model_snapshot"]["artifact_hash"] = "wrong"
    with pytest.raises(ValueError, match="parent_artifact_hash_mismatch"):
        forecast_packet(packet, ARTIFACT, history)
    packet = _packet()
    packet["markets"]["total"]["model_snapshot"]["features"]["input_qualified"] = False
    with pytest.raises(ValueError, match="unqualified_v6_input"):
        forecast_packet(packet, ARTIFACT, history)


def test_v3_rejects_missing_history_and_future_packet() -> None:
    with pytest.raises(ValueError, match="no_prior_complete_date_evidence"):
        forecast_packet(_packet(), ARTIFACT, [])
    packet = _packet()
    packet["captured_at_utc"] = packet["event_start_utc"]
    with pytest.raises(ValueError, match="packet_not_pregame"):
        forecast_packet(packet, ARTIFACT, [])


def test_shadow_reader_rejects_changed_packet_bytes(tmp_path) -> None:
    path = tmp_path / "next-game-not-the-digest.json"
    path.write_text(json.dumps(_packet()), encoding="utf-8")
    with pytest.raises(ValueError, match="packet_filename_hash_mismatch"):
        load_verified_packet(path)
