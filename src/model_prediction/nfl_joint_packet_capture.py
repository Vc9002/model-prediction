"""Append-only decision-time NFL research packets, including no-calls."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .domain import parse_utc
from .learned_replay import replay_snapshot as replay_learned
from .learned_replay import snapshot_hash
from .production_registry import compute_artifact_hash
from .scalar_artifact_replay import replay_snapshot as replay_scalar


def _verified_model(market: str, row: dict[str, Any], captured_at: str) -> dict[str, Any]:
    snapshot = row.get("model_input_snapshot")
    if not isinstance(snapshot, dict):
        return {"status": "missing_model_snapshot"}
    try:
        if snapshot.get("snapshot_hash") != snapshot_hash(snapshot):
            raise ValueError("snapshot_hash_mismatch")
        artifact = snapshot["artifact"]
        if snapshot["artifact_hash"] != compute_artifact_hash(artifact):
            raise ValueError("artifact_hash_mismatch")
        if not parse_utc(snapshot["observed_at_utc"]) <= parse_utc(captured_at) < parse_utc(
            snapshot["event_start_utc"]
        ):
            raise ValueError("snapshot_timing_invalid")
        if market == "moneyline":
            probability = replay_learned(snapshot)
            if row["selection"] == "away":
                probability = 1 - probability
        else:
            probability = replay_scalar(snapshot)[row["selection"]]
        if abs(probability - float(row["model_probability"])) > 1e-5:
            raise ValueError("probability_replay_mismatch")
    except (KeyError, TypeError, ValueError) as exc:
        return {"status": "invalid_model_snapshot", "reason": str(exc)}
    return {
        "status": "verified",
        "selection": row["selection"],
        "line": row.get("line"),
        "model_probability": float(row["model_probability"]),
        "model_version": snapshot["model_version"],
        "model_snapshot": snapshot,
        "call": row.get("call"),
    }


def _verified_quote(
    quote: Any, captured_at: str, event_start: str, archives: dict[Path, dict[str, dict[str, Any]]]
) -> dict[str, Any] | None:
    if not isinstance(quote, dict):
        return None
    try:
        lineage = quote["lineage"]
        observed = parse_utc(lineage["market_quote_observed_at_utc"])
        if not observed <= parse_utc(captured_at) < parse_utc(event_start):
            return None
        if lineage["market_quote_timestamp_valid"] is not True or lineage["market_quote_reconstructed"] is not False:
            return None
        if lineage["market_quote_source"] != "polymarket_us":
            return None
        path = Path(lineage["market_snapshot_archive_path"])
        if not path.is_file():
            return None
        if any(not 0 < float(quote[key]) < 1 for key in ("long_ask", "short_ask")):
            return None
        if path not in archives:
            records = {}
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    body = {key: value for key, value in record.items() if not str(key).startswith("_")}
                    digest = hashlib.sha256(
                        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest()
                    records[digest] = body
            archives[path] = records
        original = archives[path].get(lineage["market_snapshot_record_id"])
        if original is None or lineage["market_snapshot_hash"] != lineage["market_snapshot_record_id"]:
            return None
        if original.get("observed_at_utc") != lineage["market_quote_observed_at_utc"]:
            return None
        if any(float((original.get(side) or {}).get("ask")) != float(quote[f"{side}_ask"]) for side in ("long", "short")):
            return None
        if "line" in quote and float(original.get("line")) != float(quote["line"]):
            return None
    except (KeyError, TypeError, ValueError, OSError):
        return None
    return quote


def capture_packets(
    data_root: Path,
    game_date: str,
    captured_at: str,
    candidates: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    """Write immutable event packets. Missing markets remain explicit gaps."""
    root = data_root / "research" / "nfl_joint_packets" / game_date
    written: list[str] = []
    gaps: dict[str, int] = {}
    archives: dict[Path, dict[str, dict[str, Any]]] = {}
    for event_id, markets in sorted(candidates.items()):
        if not markets:
            continue
        first = next(iter(markets.values()))
        event_start = str(first["event_start_utc"])
        if parse_utc(captured_at) >= parse_utc(event_start):
            continue
        entries = {}
        for market in ("moneyline", "spread", "total"):
            row = markets.get(market)
            if row is None:
                entries[market] = {"status": "missing_model_or_contract"}
            else:
                entries[market] = _verified_model(market, row, captured_at)
                entries[market]["quote"] = _verified_quote(
                    row.get("quote"), captured_at, event_start, archives
                )
                if entries[market]["quote"] is None:
                    entries[market]["quote_status"] = "missing_exact_two_sided_quote"
            if entries[market]["status"] != "verified" or entries[market].get("quote") is None:
                key = f"{market}:{entries[market].get('quote_status', entries[market]['status'])}"
                gaps[key] = gaps.get(key, 0) + 1
        packet = {
            "schema": "nfl-joint-decision-packet-v1",
            "event_id": event_id,
            "event_start_utc": event_start,
            "captured_at_utc": captured_at,
            "markets": entries,
            "replay_ready": all(
                entries[market]["status"] == "verified" and entries[market].get("quote") is not None
                for market in ("moneyline", "spread", "total")
            ),
            "research_only": True,
        }
        encoded = json.dumps(packet, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{event_id}-{digest}.json"
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(encoded + "\n")
        except FileExistsError:
            if path.read_text(encoding="utf-8") != encoded + "\n":
                raise ValueError("existing_packet_hash_collision") from None
        written.append(str(path))
    return {"packets": len(written), "paths": written, "gaps": gaps}
