"""Exact archived quote adapters for shared research policy evaluation.

Supports existing full-game binary moneyline and MLB half-point spread/total
envelopes. Integer push rules, first-inning contracts, draws and void handling
are not inferred from these sources. No live quote fetch or order submission.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from .data_sources.mlb_market_odds import (
    canonical_mlb_market_snapshot_hash,
    load_verified_mlb_market_snapshot,
)
from .research_incumbent_evaluation import POLICY, QuoteArchive, canonical_hash, timestamp, validate_quote
from .research_pairing import _a_is_home


def number(value: Any) -> float:
    if isinstance(value, bool):
        raise TypeError("invalid_quote_numeric_value")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("invalid_quote_numeric_value")
    return result


def validate_mlb_halfpoint(record: dict[str, Any], envelope: dict[str, Any]) -> dict[str, Any]:
    """Validate the semantics of an envelope whose hash was already verified."""
    market, side = record["market_type"], record["selection"]
    decision = json.loads(record["decision_payload_json"])
    if record["sport"].upper() != "MLB" or market not in {"spread", "total"}:
        raise ValueError("unsupported_mlb_halfpoint_market")
    line = number(record["line"])
    if line.is_integer() or not (line * 2).is_integer():
        raise ValueError("unverified_integer_or_nonstandard_settlement_rules")
    expected_labels = {"home", "away"} if market == "spread" else {"over", "under"}
    if side not in expected_labels or set(envelope["markets"][market]) != expected_labels:
        raise ValueError("invalid_mlb_market_sides")
    observed, created, start = (
        timestamp(envelope["observed_at_utc"]),
        timestamp(record["created_at_utc"]),
        timestamp(record["event_start_utc"]),
    )
    if (
        envelope["provider"] != "polymarket_us"
        or envelope["event_id"] != record["event_id"]
        or timestamp(envelope["event_start_utc"]) != start
    ):
        raise ValueError("mlb_envelope_event_or_provider_mismatch")
    if (
        not timestamp(POLICY["fee_effective_utc"]) <= observed <= created < start
        or (created - observed).total_seconds() > POLICY["maximum_quote_age_seconds"]
    ):
        raise ValueError("mlb_envelope_quote_time_invalid")
    if any(envelope[f"{s}_team"] != decision[f"{s}_team"] for s in ("home", "away")):
        raise ValueError("mlb_envelope_participant_mismatch")
    book = envelope["raw_response"]["books"][market]
    event = envelope["raw_response"]["event"]
    if (
        book.get("provider") != "polymarket_us"
        or book.get("reconstructed") is not False
        or book.get("reconstruction_status_source") != "live_gateway_market_and_book_response"
        or book.get("market_state") != "MARKET_STATE_OPEN"
    ):
        raise ValueError("mlb_book_provenance_invalid")
    if (
        timestamp(book["observed_at_utc"]) != observed
        or timestamp(book["event_start_utc"]) != start
        or timestamp(book["transact_time_utc"]) > observed
    ):
        raise ValueError("mlb_book_time_mismatch")
    if (
        event.get("provider") != "polymarket_us"
        or event.get("league") != "MLB"
        or timestamp(event["event_start_utc"]) != start
    ):
        raise ValueError("mlb_provider_event_mismatch")
    selections = envelope["markets"][market]
    long_entries = [
        (selection, q) for selection, q in selections.items() if q.get("polymarket_side") == "long"
    ]
    short_entries = [
        (selection, q) for selection, q in selections.items() if q.get("polymarket_side") == "short"
    ]
    if len(long_entries) != 1 or len(short_entries) != 1:
        raise ValueError("mlb_ambiguous_long_short_mapping")
    long_selection, long_quote = long_entries[0]
    long_line = number(long_quote["line"])
    slug_line = str(abs(long_line) if market == "spread" else long_line).replace(".", "pt")
    suffix = ("pos-" if long_line > 0 else "neg-") + slug_line if market == "spread" else slug_line
    expected_slug = f"{'asc' if market == 'spread' else 'tsc'}-{event['event_slug']}-{suffix}"
    if (
        not re.fullmatch(r"mlb-[a-z0-9]+-[a-z0-9]+-\d{4}-\d{2}-\d{2}", str(event["event_slug"]))
        or book["market_slug"] != expected_slug
    ):
        raise ValueError("mlb_contract_horizon_or_slug_mismatch")
    if number(book["line"]) != long_line or (market == "total" and (long_selection != "over" or line <= 0)):
        raise ValueError("mlb_book_line_or_total_side_mismatch")
    matching = [
        m
        for m in event["markets"]
        if m.get("market_id") == book.get("market_id") and m.get("market_slug") == book["market_slug"]
    ]
    if len(matching) != 1 or matching[0].get("market_type") != market or not book.get("market_id"):
        raise ValueError("mlb_provider_contract_identity_mismatch")
    raw_market = matching[0]
    if number(raw_market["line"]) != long_line or len(raw_market["sides"]) != 2:
        raise ValueError("mlb_provider_contract_line_mismatch")
    home_line = number(selections["home"]["line"]) if market == "spread" else None
    if number(selections[side]["line"]) != line:
        raise ValueError("mlb_exact_selected_line_mismatch")
    contracts = []
    for direction, (selection, quote) in (("long", long_entries[0]), ("short", short_entries[0])):
        raw_sides = [s for s in raw_market["sides"] if s.get("is_long") is (direction == "long")]
        if len(raw_sides) != 1:
            raise ValueError("mlb_provider_side_mapping_mismatch")
        raw_side, bbo = raw_sides[0], book[direction]
        expected_line = (
            home_line
            if selection == "home"
            else -home_line
            if selection == "away" and home_line is not None
            else line
        )
        if (
            quote.get("selection") != selection
            or quote.get("market_slug") != book["market_slug"]
            or number(quote["line"]) != expected_line
            or raw_side.get("selection") != selection
            or number(raw_side["line"]) != expected_line
        ):
            raise ValueError("mlb_side_line_or_selection_mismatch")
        if market == "spread" and (
            raw_side.get("team") != decision[f"{selection}_team"]
            or (direction == "long" and book.get("team") != raw_side["team"])
        ):
            raise ValueError("mlb_spread_team_mismatch")
        if str(bbo.get("description")) != str(raw_side.get("description")):
            raise ValueError("mlb_book_side_description_mismatch")
        ask, bid, size = number(bbo["ask"]), number(bbo["bid"]), number(bbo["ask_size"])
        if not 0 < ask < 1 or not 0 <= bid <= ask or size < 1:
            raise ValueError("invalid_mlb_price_depth_or_crossed_book")
        if abs(number(quote["decision_probability"]) - ask) > 1e-12:
            raise ValueError("mlb_projected_price_not_raw_ask")
        win = {"home": "home_cover", "away": "away_cover"}.get(selection, selection)
        labels = {"home_cover", "away_cover"} if market == "spread" else {"over", "under"}
        contracts.append(
            {
                "market_id": book["market_id"],
                "side": direction,
                "ask": ask,
                "ask_size": size,
                "settlement_values": {label: int(label == win) for label in sorted(labels)},
            }
        )
    return {
        "contracts": contracts,
        "impossible_outcomes": ["push"],
        "raw_record": envelope,
        "raw_record_sha256": canonical_hash(envelope),
        "linkage": "MLB_ENVELOPE_CANONICAL_HASH_V1",
        "source_snapshot_hash": envelope["snapshot_hash"],
        "settlement_scope": "completed full-game integer scores and half-point lines; voids excluded",
    }


def binary_evidence(
    record: dict[str, Any], candidate: dict[str, Any], quote: dict[str, Any], linkage: str
) -> dict[str, Any]:
    decision = json.loads(record["decision_payload_json"])
    home = validate_quote(record, quote, decision["home_team"], decision["away_team"])
    a = home if _a_is_home(candidate, decision) else "short" if home == "long" else "long"
    return {
        "contracts": [
            {
                "market_id": quote["market_id"],
                "side": side,
                "ask": quote[side]["ask"],
                "ask_size": quote[side]["ask_size"],
                "settlement_values": {"a_win": int(side == a), "b_win": int(side != a)},
            }
            for side in ("long", "short")
        ],
        "impossible_outcomes": [],
        "raw_record": quote,
        "raw_record_sha256": canonical_hash(quote),
        "source_snapshot_hash": record["market_snapshot_hash"],
        "linkage": linkage,
        "settlement_scope": "completed full-game binary moneyline",
    }


def verify_embedded_quote(
    record: dict[str, Any], candidate: dict[str, Any], evidence: dict[str, Any]
) -> dict[str, Any]:
    """Revalidate a hash-bound pregame archive without requiring its live file.

    Legacy projections do not independently bind depth; their original archive
    is still required. Only full-record or canonical MLB-envelope hashes qualify.
    """
    quote = evidence["raw_record"]
    source_hash = record["market_snapshot_hash"]
    if (
        evidence["raw_record_sha256"] != canonical_hash(quote)
        or evidence["source_snapshot_hash"] != source_hash
    ):
        raise ValueError("embedded_quote_hash_mismatch")
    if evidence["linkage"] == "FULL_RECORD_HASH" and candidate["market"] == "moneyline":
        if source_hash != canonical_hash(quote) or set(candidate["probabilities"]) != {"a_win", "b_win"}:
            raise ValueError("embedded_moneyline_source_hash_mismatch")
        rebuilt = binary_evidence(record, candidate, quote, evidence["linkage"])
    elif evidence["linkage"] == "MLB_ENVELOPE_CANONICAL_HASH_V1":
        decision = json.loads(record["decision_payload_json"])
        if not (
            canonical_mlb_market_snapshot_hash(quote)
            == source_hash
            == quote["snapshot_hash"]
            == quote["snapshot_record_id"]
            == record["market_snapshot_record_id"]
            and quote["snapshot_archive_path"] == record["market_snapshot_archive_path"]
            and quote["observed_at_utc"] == decision["market_quote_observed_at_utc"]
            and quote["markets"][candidate["market"]][record["selection"]]["american_odds"]
            == int(decision["american_odds"])
        ):
            raise ValueError("embedded_mlb_source_binding_mismatch")
        rebuilt = validate_mlb_halfpoint(record, quote)
    else:
        raise ValueError("embedded_quote_requires_complete_source_hash")
    if rebuilt != evidence:
        raise ValueError("embedded_quote_contract_terms_mismatch")
    return rebuilt


class PairedQuoteArchive:
    def __init__(self, root: Path):
        self.root = root
        self.moneyline = QuoteArchive(root / "data/odds")

    def contracts(self, record: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        market = candidate["market"]
        decision = json.loads(record["decision_payload_json"])
        if market == "moneyline" and set(candidate["probabilities"]) == {"a_win", "b_win"}:
            quote, linkage = self.moneyline.lookup(record)
            return binary_evidence(record, candidate, quote, linkage)
        if candidate["sport"] != "MLB" or market not in {"spread", "total"}:
            raise ValueError("no_verified_archive_adapter_for_settlement_terms")
        envelope = load_verified_mlb_market_snapshot(
            archive_path=record.get("market_snapshot_archive_path"),
            record_id=record.get("market_snapshot_record_id"),
            approved_roots=(self.root / "data/market_odds_snapshots.jsonl",),
            expected_snapshot_hash=record.get("market_snapshot_hash"),
            event_id=record["event_id"],
            observed_at_utc=decision["market_quote_observed_at_utc"],
            provider="polymarket_us",
            market_type=market,
            selection=record["selection"],
            line=float(record["line"]),
            american_odds=int(decision["american_odds"]),
        )
        return validate_mlb_halfpoint(record, envelope)


def executable_probabilities(probabilities: dict[str, float], evidence: dict[str, Any]) -> dict[str, float]:
    excluded = set(evidence["impossible_outcomes"])
    if any(probabilities.get(label, 0) != 0 for label in excluded):
        raise ValueError("nonzero_probability_for_impossible_contract_outcome")
    return {k: v for k, v in probabilities.items() if k not in excluded}
