"""Read-only NFL v3 shadow forecast from an immutable pregame packet."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

from model_prediction.domain import parse_utc
from model_prediction.learned_replay import replay_snapshot as replay_learned
from model_prediction.models.nfl_joint_market_v3 import forecast_packet
from model_prediction.models.nfl_market_anchor_v1 import SettledQuote
from model_prediction.nfl_joint_packet_capture import _verified_quote
from model_prediction.production_registry import compute_artifact_hash
from model_prediction.scalar_artifact_replay import replay_snapshot as replay_scalar
from scripts.evaluate_nfl_market_anchor_v1 import _valid_row

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "config/models/challengers/nfl-joint-market-v3.json"


def load_verified_packet(path: Path) -> tuple[dict, str]:
    packet = json.loads(path.read_text(encoding="utf-8"))
    encoded = json.dumps(packet, sort_keys=True, separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    if path.stem != f"{packet['event_id']}-{digest}":
        raise ValueError("packet_filename_hash_mismatch")
    captured = parse_utc(packet["captured_at_utc"])
    start = parse_utc(packet["event_start_utc"])
    if not captured < start:
        raise ValueError("packet_not_pregame")
    archives = {}
    for market, row in packet["markets"].items():
        if market not in ("moneyline", "spread", "total"):
            continue
        snapshot = row["model_snapshot"]
        if snapshot["event_id"] != packet["event_id"] or parse_utc(snapshot["event_start_utc"]) != start:
            raise ValueError("packet_model_event_mismatch")
        if not parse_utc(snapshot["observed_at_utc"]) <= captured:
            raise ValueError("future_model_snapshot")
        if snapshot["artifact_hash"] != compute_artifact_hash(snapshot["artifact"]):
            raise ValueError("packet_model_artifact_hash_mismatch")
        if market == "moneyline":
            probability = replay_learned(snapshot)
            if row["selection"] == "away":
                probability = 1 - probability
        else:
            probability = replay_scalar(snapshot)[row["selection"]]
        if abs(probability - float(row["model_probability"])) > 1e-5:
            raise ValueError("packet_model_probability_mismatch")
        if _verified_quote(row["quote"], packet["captured_at_utc"], packet["event_start_utc"], archives) is None:
            raise ValueError("packet_quote_archive_mismatch")
    return packet, digest


def load_prior_moneyline_history(database: Path, expected_artifact_hash: str) -> list[SettledQuote]:
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT event_id, market_type, selection, model_artifact_hash, model_probability, result, status, created_at_utc, "
        "decision_payload_json FROM ledger_records WHERE sport='nfl' AND ledger_tier='flat' "
        "AND market_type='moneyline' ORDER BY created_at_utc"
    ).fetchall()
    connection.close()
    latest = {row["event_id"]: row for row in rows}
    history = []
    for row in latest.values():
        try:
            if row["model_artifact_hash"] != expected_artifact_hash:
                continue
            valid = _valid_row(row)
            if valid is None:
                continue
            snapshot = json.loads(json.loads(row["decision_payload_json"])["model_input_snapshot_json"])
            if snapshot["artifact_hash"] != expected_artifact_hash:
                continue
            if not parse_utc(snapshot["observed_at_utc"]) <= parse_utc(row["created_at_utc"]):
                continue
            home_probability = replay_learned(snapshot)
            selected_probability = home_probability if row["selection"] == "home" else 1 - home_probability
            if abs(selected_probability - float(row["model_probability"])) > 1e-5:
                continue
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            valid = None
        if valid is not None:
            history.append(valid[1])
    return history


def evaluate(packet_path: Path, database: Path) -> dict:
    packet, digest = load_verified_packet(packet_path)
    artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    if artifact["artifact_hash"] != compute_artifact_hash(artifact):
        raise ValueError("v3_artifact_hash_mismatch")
    for relative_path, expected_hash in artifact["source_sha256"].items():
        if hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest() != expected_hash:
            raise ValueError(f"v3_source_hash_mismatch:{relative_path}")
    captured = parse_utc(packet["captured_at_utc"])
    game_date = parse_utc(packet["event_start_utc"]).astimezone(ZoneInfo("America/New_York")).date()
    history = [
        row for row in load_prior_moneyline_history(database, artifact["inputs"]["moneyline_artifact_hash"])
        if row.event_date < game_date and row.settled_at_utc <= captured
    ]
    result = forecast_packet(packet, artifact, history)
    result["training_evidence"] = sorted(
        (
            {
                "event_date": row.event_date.isoformat(),
                "model_probability": row.model_probability,
                "market_probability": row.market_probability,
                "outcome": row.outcome,
                "settled_at_utc": row.settled_at_utc.isoformat(),
            }
            for row in history
        ),
        key=lambda item: (item["event_date"], item["settled_at_utc"], item["model_probability"]),
    )
    result["packet_sha256"] = digest
    result["source_packet_path"] = str(packet_path.resolve())
    result["scope"] = "prospective research forecast; no call, stake, or order"
    result["shadow_sha256"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("packet", type=Path)
    parser.add_argument("database", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate(args.packet, args.database)
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        try:
            with args.output.open("x", encoding="utf-8") as handle:
                handle.write(encoded)
        except FileExistsError:
            if args.output.read_text(encoding="utf-8") != encoded:
                raise ValueError("existing_shadow_forecast_differs") from None
    print(encoded)
