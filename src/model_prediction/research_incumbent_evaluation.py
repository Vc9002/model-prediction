"""Read-only incumbent replay and explicitly hypothetical quote economics.

Never imports execution, writes ledgers, or promotes models. Legacy quote
projection hashes are distinguished from hashes of the full archive record.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

import numpy as np

from .learned_replay import replay_snapshot
from .models.learned_market import LearnedMarketArtifact
from .research_generation import cluster_interval

POLICY: dict[str, Any] = {
    "version": "incumbent_comparison_20260909_v1",
    "replay_tolerance": 0.000002,
    "minimum_brier_gain": 0.002,
    "minimum_events": 100,
    "minimum_dates": 20,
    "decision_sampling": "earliest decision per event before quote eligibility filtering",
    "budget_per_event_usd": 5,
    "quantity": "whole contracts bounded by displayed ask size and budget including fee",
    "trade_rule": "choose positive maximum expected net profit per dollar; otherwise abstain",
    "fee_rate": "0.06",
    "fee_effective_utc": "2026-07-01T04:00:00+00:00",
    "fee_rounding": "nearest cent ROUND_HALF_EVEN per hypothetical single fill",
    "fee_source": "https://docs.polymarket.us/fees",
    "maximum_quote_age_seconds": 300,
    "settlement": "binary full game, $1 win / $0 loss; no exit trade; pushes excluded",
    "rebates": "none assumed",
    "evidence": "retrospective development, hypothetical immediate taker fill; not actual fills",
    "clv": "unavailable without independently matched closing quotes",
    "promotion": False,
}


def canonical_hash(row: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def timestamp(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("timestamp_timezone_missing")
    return parsed.astimezone(UTC)


def probability(value: Any) -> float:
    if value is None or isinstance(value, bool):
        raise ValueError("invalid_probability")
    p = float(value)
    if not math.isfinite(p) or not 0 <= p <= 1:
        raise ValueError("invalid_probability")
    return p


def feature_identity(row: dict[str, Any]) -> dict[str, Any]:
    envelope = json.loads(row.get("feature_payload_json") or "{}")
    return {
        key: envelope.get(key)
        for key in (
            "features",
            "model_artifact_hash",
            "model_version",
            "feature_schema_version",
            "model_input_snapshot",
        )
    }


def read_records(database: Path, model_ids: list[str]) -> list[dict[str, Any]]:
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        placeholders = ",".join("?" for _ in model_ids)
        rows = connection.execute(
            f"SELECT * FROM ledger_records WHERE model_id IN ({placeholders}) "
            "AND status='settled' AND result IN ('win','loss','push') "
            "ORDER BY model_id, created_at_utc, pick_id, ledger_tier",
            model_ids,
        ).fetchall()
    return [dict(row) for row in rows]


def deduplicate(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Keep one context across ledger tiers; a conflict rejects the whole group."""
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    counts: Counter[str] = Counter()
    for row in records:
        try:
            key = (
                row["model_id"],
                row["event_id"],
                timestamp(row["event_start_utc"]).isoformat(),
                row["market_type"],
                row["selection"],
                row["line"],
                timestamp(row["created_at_utc"]).isoformat(),
            )
        except (KeyError, ValueError, TypeError):
            counts["invalid_context"] += 1
            continue
        groups[key].append(row)
    output = []
    for members in groups.values():
        signatures = {
            canonical_hash(
                {
                    key: row.get(key)
                    for key in (
                        "model_probability",
                        "result",
                        "model_artifact_hash",
                        "market_snapshot_hash",
                        "market_snapshot_archive_path",
                        "market_snapshot_record_id",
                    )
                }
                | {"feature_identity": feature_identity(row)}
                | {
                    key: json.loads(row["decision_payload_json"]).get(key)
                    for key in (
                        "home_team",
                        "away_team",
                        "home_score",
                        "away_score",
                    )
                }
            )
            for row in members
        }
        if len(signatures) != 1:
            counts["conflicting_context_rows"] += len(members)
            continue
        counts["mirror_duplicates"] += len(members) - 1
        if len({r.get("feature_payload_json") for r in members}) > 1:
            # Tier-specific quote-availability annotations are not different
            # model inputs. Preserve their existence without inventing a
            # feature mismatch; quote eligibility is independently checked.
            counts["feature_metadata_only_difference_contexts"] += 1
        output.append(
            members[0]
            | {
                "source_pick_ids": sorted({r["pick_id"] for r in members}),
                "source_tiers": sorted({r["ledger_tier"] for r in members}),
            }
        )
    return output, dict(counts)


def replay(row: dict[str, Any], artifact: LearnedMarketArtifact | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "pick_id": row["pick_id"],
        "event_id": row["event_id"],
        "stored": row["model_probability"],
        "reproduced": None,
        "feature_deltas": {},
        "feature_basis": "exact stored inputs; no feature regeneration claimed",
    }
    try:
        envelope = json.loads(row.get("feature_payload_json") or "{}")
        serving_snapshot = envelope.get("model_input_snapshot")
        if serving_snapshot and serving_snapshot.get("schema") in {
            "cfb-joint-replay-v1",
            "esports-elo-replay-v1",
            "scalar-artifact-replay-v1",
            "international-elo-replay-v1",
            "coded-moneyline-replay-v1",
            "mlb-margin-replay-v1",
            "mlb-v10-structural-replay-v1",
        }:
            from .learned_replay import validate_decision_snapshot as validate_serving

            if timestamp(serving_snapshot["observed_at_utc"]) > timestamp(row["created_at_utc"]):
                return result | {"status": "SERVING_SNAPSHOT_CONTEXT_MISMATCH"}
            p = validate_serving(
                serving_snapshot,
                json.loads(row["decision_payload_json"])
                | row
                | {"model_version": row["model_id"], "league": row["sport"]},
            )
            return result | {
                "status": "PASS",
                "reproduced": p,
                "absolute_error": abs(p - probability(row["model_probability"])),
                "feature_basis": serving_snapshot["replay_scope"],
                "features": serving_snapshot["features"],
                "adapter": serving_snapshot["schema"],
            }
    except (ValueError, KeyError, TypeError, AttributeError):
        return result | {"status": "MISSING_OR_INVALID_INPUT"}
    if artifact is None:
        return result | {"status": "UNSUPPORTED_REPLAY"}
    if row["model_artifact_hash"] != artifact.hash:
        return result | {"status": "DIFFERENT_OR_MISSING_ARTIFACT"}
    try:
        stored = probability(row["model_probability"])
        feature_envelope = json.loads(row["feature_payload_json"])
        if feature_envelope.get("model_artifact_hash") != artifact.hash:
            return result | {"status": "FEATURE_ARTIFACT_MISMATCH"}
        snapshot = feature_envelope.get("model_input_snapshot")
        if snapshot is not None:
            if (
                snapshot.get("artifact_hash") != artifact.hash
                or snapshot.get("event_id") != row["event_id"]
                or timestamp(snapshot["event_start_utc"]) != timestamp(row["event_start_utc"])
                or timestamp(snapshot["observed_at_utc"]) > timestamp(row["created_at_utc"])
            ):
                return result | {"status": "SERVING_SNAPSHOT_CONTEXT_MISMATCH"}
            p_home = replay_snapshot(snapshot)
            if row["selection"] not in {"home", "away"}:
                return result | {"status": "UNKNOWN_SELECTION"}
            p = p_home if row["selection"] == "home" else 1 - p_home
            delta = abs(stored - p)
            return result | {
                "status": "PASS" if delta <= POLICY["replay_tolerance"] else "PROBABILITY_MISMATCH",
                "reproduced": p,
                "absolute_error": delta,
                "feature_basis": "self-contained exact model inputs and serving transform",
                "features": snapshot["features"],
                "feature_deltas": dict.fromkeys(snapshot["features"], 0.0),
                "base_home_probability": snapshot["base_home_probability"],
                "adjustment": snapshot["adjustment"],
            }
        features = {
            name: float(feature_envelope["features"][name])
            for name in artifact.raw["market_models"]["moneyline"]["feature_names"]
        }
        if not all(math.isfinite(value) for value in features.values()):
            raise ValueError("nonfinite_feature")
        p = artifact.probability("moneyline", features)
        if row["selection"] not in {"home", "away"}:
            return result | {"status": "UNKNOWN_SELECTION"}
        p = p if row["selection"] == "home" else 1 - p
    except (KeyError, ValueError, TypeError):
        return result | {"status": "MISSING_OR_INVALID_INPUT"}
    delta = abs(stored - p)
    return result | {
        "status": "PASS" if delta <= POLICY["replay_tolerance"] else "PROBABILITY_MISMATCH",
        "reproduced": p,
        "absolute_error": delta,
        "features": features,
        "feature_deltas": dict.fromkeys(features, 0.0),
    }


def names_match(left: str, right: str) -> bool:
    def normalized(value: str) -> str:
        return " ".join(re.sub(r"[(),.\-_]", " ", value.casefold()).split())

    a, b = normalized(left), normalized(right)
    if not a or not b:
        return False
    return f" {a} " in f" {b} " or f" {b} " in f" {a} "


def home_side(snapshot: dict[str, Any], home: str, away: str) -> str:
    matrix = {
        side: [
            names_match(team, str(snapshot.get(side, {}).get("description") or "")) for team in (home, away)
        ]
        for side in ("long", "short")
    }
    if matrix == {"long": [True, False], "short": [False, True]}:
        return "long"
    if matrix == {"long": [False, True], "short": [True, False]}:
        return "short"
    raise ValueError("ambiguous_participant_mapping")


def legacy_projection(snapshot: dict[str, Any], side: str) -> dict[str, Any]:
    """Exact historical learned_forward.match_executable_quote projection."""
    selected = snapshot[side]
    other = snapshot["short" if side == "long" else "long"]
    ask = float(selected["ask"])
    other_ask = other.get("ask")
    no_vig = None
    if other_ask is not None and 0 < float(other_ask) < 1:
        no_vig = round(ask / (ask + float(other_ask)), 6)
    return {
        "market_slug": snapshot.get("market_slug"),
        "side": side,
        "executable_ask": round(ask, 6),
        "midpoint_reference": selected.get("midpoint"),
        "no_vig_probability": no_vig,
        "observed_at_utc": snapshot.get("observed_at_utc"),
        "timestamp_valid": bool(snapshot.get("timestamp_valid", False)),
        "provider": "polymarket_us",
        "reconstructed": snapshot.get("reconstructed"),
        "usage": snapshot.get("usage"),
    }


class QuoteArchive:
    def __init__(self, approved_root: Path):
        self.root = approved_root.resolve()
        self.cache: dict[Path, dict[str, list[tuple[dict[str, Any], str]]]] = {}

    def lookup(self, row: dict[str, Any]) -> tuple[dict[str, Any], str]:
        if not row.get("market_snapshot_hash") or not row.get("market_snapshot_archive_path"):
            raise ValueError("no_archived_quote_reference")
        path = Path(row["market_snapshot_archive_path"]).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise ValueError("quote_archive_unavailable_or_outside_root")
        if path not in self.cache:
            index: dict[str, list[tuple[dict[str, Any], str]]] = defaultdict(list)
            unique = {}
            with path.open() as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    raw = json.loads(line)
                    unique[canonical_hash(raw)] = raw
            for digest, raw in unique.items():
                index[digest].append((raw, "FULL_RECORD_HASH"))
                for side in ("long", "short"):
                    try:
                        projection_hash = canonical_hash(legacy_projection(raw, side))
                    except (KeyError, TypeError, ValueError):
                        continue
                    index[projection_hash].append((raw, "LEGACY_PROJECTION_HASH"))
            self.cache[path] = index
        matches = self.cache[path].get(row["market_snapshot_hash"], [])
        if len(matches) != 1:
            raise ValueError("quote_hash_missing_or_ambiguous")
        return matches[0]


def validate_quote(row: dict[str, Any], snapshot: dict[str, Any], home: str, away: str) -> str:
    if (
        snapshot.get("provider") != "polymarket_us"
        or snapshot.get("timestamp_valid") is not True
        or snapshot.get("reconstructed") is not False
        or snapshot.get("usage") != "prospective_executable_bbo"
        or snapshot.get("market_state") != "MARKET_STATE_OPEN"
    ):
        raise ValueError("quote_provenance_or_state_invalid")
    slug = str(snapshot.get("market_slug") or "")
    # Explicit allowlist for the full-event binary moneyline contract family.
    if (
        snapshot.get("market_type") != "moneyline"
        or not slug.startswith("aec-")
        or re.search(r"-(?:f\d|1st|h\d|q\d|map\d|set\d)(?:-|$)", slug)
        or snapshot.get("line") is not None
        or not snapshot.get("market_id")
        or str(snapshot.get("league", "")).casefold() != str(row["sport"]).casefold()
    ):
        raise ValueError("unsupported_contract_or_horizon")
    observed, decision, start = (
        timestamp(snapshot["observed_at_utc"]),
        timestamp(row["created_at_utc"]),
        timestamp(row["event_start_utc"]),
    )
    if timestamp(snapshot["event_start_utc"]) != start:
        raise ValueError("contract_start_mismatch")
    if not observed <= decision < start:
        raise ValueError("quote_or_decision_not_pregame")
    if (decision - observed).total_seconds() > POLICY["maximum_quote_age_seconds"]:
        raise ValueError("quote_stale")
    if observed < timestamp(POLICY["fee_effective_utc"]):
        raise ValueError("historical_fee_schedule_unverified")
    side = home_side(snapshot, home, away)
    for key in ("long", "short"):
        book = snapshot[key]
        ask, bid = probability(book.get("ask")), probability(book.get("bid"))
        size = float(book.get("ask_size"))
        if not 0 < ask < 1 or bid > ask or not math.isfinite(size) or size < 1:
            raise ValueError("invalid_price_depth_or_crossed_book")
    return side


def taker_fee(quantity: int, price: Decimal) -> Decimal:
    if quantity < 0 or not price.is_finite() or not 0 < price < 1:
        raise ValueError("invalid_fee_input")
    return (Decimal(POLICY["fee_rate"]) * quantity * price * (1 - price)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_EVEN,
    )


def simulate(p_home: float, won_home: bool, snapshot: dict[str, Any], home_key: str) -> dict[str, Any]:
    """Same fixed $5 policy for both models; no order or fill claim."""
    p_home = probability(p_home)
    choices = []
    for side in ("long", "short"):
        price = Decimal(str(snapshot[side]["ask"]))
        depth = float(snapshot[side]["ask_size"])
        if not price.is_finite() or not 0 < price < 1 or not math.isfinite(depth) or depth < 0:
            raise ValueError("invalid_price_or_depth")
        budget = Decimal(POLICY["budget_per_event_usd"])
        qty = min(int(budget / price), int(depth))
        while qty and qty * price + taker_fee(qty, price) > budget:
            qty -= 1
        if not qty:
            continue
        fee = taker_fee(qty, price)
        cost = qty * price + fee
        p = Decimal(str(p_home if side == home_key else 1 - p_home))
        ev = p * qty - cost
        if ev <= 0:
            continue
        won = won_home if side == home_key else not won_home
        choices.append(
            (
                float(ev / cost),
                side,
                {
                    "called": True,
                    "side": side,
                    "quantity": qty,
                    "ask": float(price),
                    "fee_usd": float(fee),
                    "cost_usd": float(cost),
                    "expected_profit_usd": float(ev),
                    "pnl_usd": float((Decimal(qty) if won else Decimal(0)) - cost),
                },
            )
        )
    if not choices:
        return {"called": False, "cost_usd": 0.0, "pnl_usd": 0.0, "fee_usd": 0.0}
    return max(choices, key=lambda item: (item[0], item[1]))[2]


def predictive_metrics(pairs: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not pairs:
        return None
    y = np.array([row["home_won"] for row in pairs], dtype=float)
    dates = [row["date"] for row in pairs]
    result: dict[str, Any] = {"events": len(pairs), "dates": len(set(dates))}
    losses = {}
    for label in ("incumbent", "candidate"):
        p = np.array([row[label + "_home_probability"] for row in pairs])
        losses[label] = (p - y) ** 2
        clipped = np.clip(p, 1e-12, 1 - 1e-12)
        result[label] = {
            "brier": float(losses[label].mean()),
            "accuracy": float(((p >= 0.5) == y).mean()),
            "log_loss": float(-(y * np.log(clipped) + (1 - y) * np.log(1 - clipped)).mean()),
        }
    result["brier_gain_date_bootstrap"] = cluster_interval(losses["incumbent"] - losses["candidate"], dates)
    return result


def economic_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "HYPOTHETICAL_ONLY" if rows else "NO_ELIGIBLE_QUOTES",
        "events": len(rows),
        "clv": None,
        "actual_fills": None,
        "profitability_proven": False,
    }
    for name in ("incumbent", "candidate"):
        ordered = sorted(rows, key=lambda row: (timestamp(row["settled_at_utc"]), row["event_id"]))
        trades = [row[name] for row in ordered]
        cost = sum(t["cost_usd"] for t in trades)
        pnl = sum(t["pnl_usd"] for t in trades)
        result[name] = {
            "calls": sum(t["called"] for t in trades),
            "cost_usd": cost if rows else None,
            "pnl_usd": pnl if rows else None,
            "roi": pnl / cost if cost else None,
            "fee_usd": sum(t["fee_usd"] for t in trades) if rows else None,
        }
        if rows:
            curve = np.r_[0.0, np.cumsum([t["pnl_usd"] for t in trades])]
            result[name]["max_settled_pnl_drawdown_usd"] = float((np.maximum.accumulate(curve) - curve).max())
    if rows:
        result["pnl_gain_per_event_date_bootstrap"] = cluster_interval(
            np.array([r["candidate"]["pnl_usd"] - r["incumbent"]["pnl_usd"] for r in rows]),
            [r["date"] for r in rows],
        )
    return result
