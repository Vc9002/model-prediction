"""Decision packet capture must retain missingness and reject false replay claims."""

import hashlib
import json
from pathlib import Path

from model_prediction.learned_replay import build_snapshot as build_learned_snapshot
from model_prediction.models.learned_market import LearnedMarketArtifact
from model_prediction.nfl_joint_packet_capture import capture_packets
from model_prediction.scalar_artifact_replay import build_snapshot as build_scalar_snapshot

ROOT = Path(__file__).resolve().parents[1]


def test_packet_capture_records_no_calls_and_missing_contracts(tmp_path) -> None:
    event = {
        "event_id": "game-1",
        "event_start_utc": "2026-11-01T18:00:00Z",
        "selection": "home",
        "model_probability": 0.6,
        "call": False,
        "model_input_snapshot": {"snapshot_hash": "tampered"},
    }
    args = (tmp_path, "2026-11-01", "2026-11-01T17:00:00Z", {"game-1": {"moneyline": event}})
    first = capture_packets(*args)
    second = capture_packets(*args)
    assert first == second
    assert first["packets"] == 1
    packet = json.loads(next((tmp_path / "research/nfl_joint_packets/2026-11-01").glob("*.json")).read_text())
    assert packet["replay_ready"] is False
    assert packet["markets"]["moneyline"]["status"] == "invalid_model_snapshot"
    assert packet["markets"]["spread"]["status"] == "missing_model_or_contract"
    assert packet["markets"]["total"]["status"] == "missing_model_or_contract"


def test_packet_capture_never_backfills_after_start(tmp_path) -> None:
    event = {"event_id": "game-1", "event_start_utc": "2026-11-01T18:00:00Z"}
    result = capture_packets(
        tmp_path, "2026-11-01", "2026-11-01T18:00:00Z", {"game-1": {"moneyline": event}}
    )
    assert result["packets"] == 0
    assert not (tmp_path / "research").exists()


def test_complete_verified_packet_is_replay_ready(tmp_path) -> None:
    start = "2026-11-01T18:00:00Z"
    observed = "2026-11-01T16:00:00Z"
    captured = "2026-11-01T17:00:00Z"
    artifact = LearnedMarketArtifact.load(ROOT / "config/models/nfl-elo-trend-lr-v4.json")
    features = {"elo_probability": 0.6, "trend_gap": 1.0}
    home = artifact.probability("moneyline", features)
    moneyline = build_learned_snapshot(
        artifact, features, event_id="game-1", observed_at_utc=observed,
        event_start_utc=start, base_home_probability=home, served_home_probability=home,
        adjustment={"method": "identity", "applied": False, "reason": "not_applicable"},
    )
    record = {"observed_at_utc": observed, "long": {"ask": 0.52}, "short": {"ask": 0.49}, "line": 44.5}
    digest = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    archive = tmp_path / "quote.jsonl"
    archive.write_text(json.dumps(record) + "\n")
    lineage = {
        "market_quote_observed_at_utc": observed,
        "market_quote_timestamp_valid": True,
        "market_quote_reconstructed": False,
        "market_quote_source": "polymarket_us",
        "market_snapshot_archive_path": str(archive),
        "market_snapshot_hash": digest,
        "market_snapshot_record_id": digest,
    }
    quote = {"long_ask": 0.52, "short_ask": 0.49, "lineage": lineage}
    markets = {
        "moneyline": {
            "event_start_utc": start, "selection": "home", "model_probability": home,
            "model_input_snapshot": moneyline, "quote": quote, "call": False,
        }
    }
    for market, model, inputs, selection in (
        ("spread", "nfl-spread-structural-v6", {"mean": 2.0, "sd": 14.5, "line": 3.5}, "home"),
        ("total", "nfl-total-structural-v6", {"mean": 45.0, "sd": 14.2, "line": 44.5}, "over"),
    ):
        spec = json.loads((ROOT / "config/models" / f"{model}.json").read_text())
        snapshot = build_scalar_snapshot(
            spec, {}, inputs, event_id="game-1", event_start_utc=start, observed_at_utc=observed,
        )
        from model_prediction.scalar_artifact_replay import replay_snapshot

        markets[market] = {
            "event_start_utc": start, "selection": selection,
            "model_probability": replay_snapshot(snapshot)[selection],
            "model_input_snapshot": snapshot, "quote": quote, "call": False,
        }
    result = capture_packets(tmp_path, "2026-11-01", captured, {"game-1": markets})
    packet = json.loads(Path(result["paths"][0]).read_text())
    assert result["gaps"] == {}
    assert packet["replay_ready"] is True
    assert packet["markets"]["moneyline"]["call"] is False
    archive.write_text('{"tampered":true}\n')
    tampered = capture_packets(tmp_path, "2026-11-01", "2026-11-01T17:05:00Z", {"game-1": markets})
    tampered_packet = json.loads(Path(tampered["paths"][0]).read_text())
    assert tampered_packet["replay_ready"] is False
    assert tampered_packet["markets"]["spread"]["quote_status"] == "missing_exact_two_sided_quote"
