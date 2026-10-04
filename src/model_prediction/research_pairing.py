"""Exact candidate/incumbent pairing with explicit outcome and payout spaces.

No historical state recovery, implicit complementary team contracts, inferred
push rules, order submission, or promotion is performed here.
"""

from __future__ import annotations

import json
import math
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .learned_replay import validate_decision_snapshot
from .research_forward import aware_time, rebuild_forecast
from .research_generation import ESPORTS
from .research_incumbent_evaluation import POLICY, probability, taker_fee
from .research_scoring import outcome_label, proper_score


def coherent_distribution(values: dict[str, float]) -> tuple[dict[str, float], float]:
    p = {name: probability(value) for name, value in values.items()}
    error = sum(p.values()) - 1.0
    if len(p) < 2 or abs(error) > 2e-6:
        raise ValueError("incoherent_incumbent_probability_mass")
    # Permit only the known six-decimal serving-rounding error. Record the
    # adjustment; never normalize a materially incoherent calibrated head.
    return {name: value / (1 + error) for name, value in p.items()}, error


def incumbent_distribution(snapshot: dict[str, Any]) -> tuple[dict[str, float], float]:
    """Caller must first pass exact selected-decision replay."""
    schema = snapshot["schema"]
    if schema == "learned-moneyline-replay-v1":
        from .learned_replay import replay_snapshot

        home = replay_snapshot(snapshot)
        values = {"home": home, "away": 1 - home}
    elif schema == "scalar-artifact-replay-v1":
        from .scalar_artifact_replay import replay_snapshot as replay_scalar

        values = replay_scalar(snapshot)
    elif schema == "international-elo-replay-v1":
        values = snapshot["outcome_probabilities"]
    elif schema == "mlb-margin-replay-v1":
        values = snapshot["served_probabilities"] | {"push": snapshot["raw_probabilities"]["push"]}
    elif schema in {
        "cfb-joint-replay-v1",
        "esports-elo-replay-v1",
        "coded-moneyline-replay-v1",
        "mlb-v10-structural-replay-v1",
    }:
        values = snapshot["probabilities"]
    else:
        raise ValueError("unsupported_incumbent_snapshot_schema")
    return coherent_distribution(values)


def _a_is_home(candidate: dict[str, Any], row: dict[str, Any]) -> bool:
    raw, sport = candidate["raw_fixture"], candidate["sport"]
    if sport == "TENNIS":
        names = {str(raw["player1_id"]): raw["player1_name"], str(raw["player2_id"]): raw["player2_name"]}
        a_id, b_id = sorted(names)
        a_name, b_name = names[a_id], names[b_id]
    elif sport in ESPORTS:
        a_name, b_name = raw["team1_name"], raw["team2_name"]
    else:
        a_name, b_name = raw["home_team"], raw["away_team"]
    if a_name == b_name or row["home_team"] == row["away_team"]:
        raise ValueError("ambiguous_pair_participants")
    if (a_name, b_name) == (row["home_team"], row["away_team"]):
        return True
    if (sport == "TENNIS" or sport in ESPORTS) and (a_name, b_name) == (row["away_team"], row["home_team"]):
        return False
    raise ValueError("pair_participant_orientation_mismatch")


def first_bound_record(
    deduplicated: list[dict[str, Any]],
    originals: list[dict[str, Any]],
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    """Retain the bound copy when a verified mirror arrives after capture.

    The first decision context is still mandatory. A bound ID from a later
    decision cannot be substituted, even if it concerns the same event.
    """
    if not deduplicated:
        raise ValueError("no_incumbent_decision")
    first = min(deduplicated, key=lambda r: (aware_time(r["created_at_utc"]), r["pick_id"]))
    if context is None:
        return first
    bound_id = context["pick_id"]
    if bound_id not in first.get("source_pick_ids", [first["pick_id"]]):
        raise ValueError("pair_bound_incumbent_mismatch")
    matches = [r for r in originals if r["pick_id"] == bound_id]
    if not matches:
        raise ValueError("pair_bound_incumbent_missing")
    return matches[0]


def reproduce_pair(
    record: dict[str, Any],
    candidate: dict[str, Any],
    model_path: Path,
    archived_features: Path,
) -> dict[str, Any]:
    """Rebuild the exact shared prediction context without requiring an outcome."""
    row = (
        json.loads(record["decision_payload_json"])
        | record
        | {"league": record["sport"], "model_version": record["model_id"]}
    )
    snapshot = json.loads(record["feature_payload_json"])["model_input_snapshot"]
    validate_decision_snapshot(snapshot, row)
    context = candidate.get("capture_context")
    if context is not None and (
        context["pick_id"] != record["pick_id"]
        or context["incumbent_snapshot_hash"] != snapshot["snapshot_hash"]
        or aware_time(context["incumbent_created_at_utc"]) != aware_time(record["created_at_utc"])
    ):
        raise ValueError("pair_bound_incumbent_mismatch")
    observed, start = (
        aware_time(candidate["observed_at_utc"]),
        aware_time(candidate["raw_fixture"]["event_start_utc"]),
    )
    if (
        candidate["sport"] != str(record["sport"]).upper()
        or candidate["market"] != record["market_type"]
        or candidate["fixture"]["event_id"] != str(record["event_id"])
        or aware_time(record["event_start_utc"]) != start
        or aware_time(snapshot["observed_at_utc"]) != observed
        or not observed <= aware_time(record["created_at_utc"]) < start
    ):
        raise ValueError("pair_decision_context_mismatch")
    market = candidate["market"]
    if market in {"spread", "total"}:
        expected_line = float(record["line"]) * (
            -1 if market == "spread" and record["selection"] == "away" else 1
        )
        if candidate["line"] != expected_line:
            raise ValueError("pair_exact_line_mismatch")
    a_home = _a_is_home(candidate, row)
    candidate_probabilities = rebuild_forecast(candidate, model_path, archived_features)
    probabilities, rounding_error = incumbent_distribution(snapshot)
    mapping = (
        {"home": "a_win" if a_home else "b_win", "away": "b_win" if a_home else "a_win"}
        if market == "moneyline"
        else {"home": "home_cover", "away": "away_cover"}
        if market == "spread"
        else {}
    )
    incumbent_probabilities = {mapping.get(key, key): value for key, value in probabilities.items()}
    if market in {"spread", "total"}:
        incumbent_probabilities.setdefault("push", 0.0)
    if set(incumbent_probabilities) != set(candidate_probabilities):
        raise ValueError("pair_outcome_space_mismatch")
    return {
        "event_id": record["event_id"],
        "pick_id": record["pick_id"],
        "sport": candidate["sport"],
        "market": market,
        "date": start.date().isoformat(),
        "observed_at_utc": observed.isoformat(),
        "incumbent_reproduction": "PASS",
        "candidate_source_rebuild": "PASS",
        "incumbent_snapshot_hash": snapshot["snapshot_hash"],
        "candidate_snapshot_hash": candidate["snapshot_hash"],
        "incumbent_model": record["model_id"],
        "candidate_model": candidate["model_id"],
        "incumbent_rounding_mass_error": rounding_error,
        "incumbent_probabilities": incumbent_probabilities,
        "candidate_probabilities": candidate_probabilities,
        "selected_outcome": mapping.get(record["selection"], record["selection"]),
        "incumbent_score": None,
        "candidate_score": None,
        "outcome": None,
        "economics": None,
        "promote": False,
        "evidence_origin": "captured_state_pair_not_historical_PIT_certification",
    }


def compare_pair(
    record: dict[str, Any],
    candidate: dict[str, Any],
    model_path: Path,
    archived_features: Path,
    outcome: dict[str, Any],
    outcome_captured_at_utc: str,
) -> dict[str, Any]:
    """Reproduce both forecasts, then independently verify and score settlement."""
    paired = reproduce_pair(record, candidate, model_path, archived_features)
    if record.get("status") != "settled" or not (
        aware_time(record["event_start_utc"])
        < aware_time(record["settled_at_utc"])
        <= aware_time(outcome_captured_at_utc)
    ):
        raise ValueError("pair_decision_context_mismatch")
    label = outcome_label(candidate, outcome, outcome_captured_at_utc)
    expected_result = (
        "push"
        if label == "push" or (label == "draw" and candidate["sport"] in {"KBO", "NPB"})
        else "win"
        if paired["selected_outcome"] == label
        else "loss"
    )
    if record["result"] != expected_result:
        raise ValueError("pair_settlement_outcome_mismatch")
    return paired | {
        "settled_at_utc": record["settled_at_utc"],
        "outcome": label,
        "incumbent_score": proper_score(paired["incumbent_probabilities"], label),
        "candidate_score": proper_score(paired["candidate_probabilities"], label),
    }


def _decimal(value: Any) -> Decimal:
    try:
        if isinstance(value, bool):
            raise TypeError("invalid_numeric_contract_value")
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("invalid_numeric_contract_value") from exc


def simulate_contracts(
    probabilities: dict[str, float],
    outcome: str | None,
    contracts: list[dict[str, Any]],
    *,
    budget_usd: str = "5",
    fee_rate: str = "0.06",
) -> dict[str, Any]:
    """Explicit payout-table policy math; callers must separately verify quotes.

    A payout is dollars per contract, or 'entry_price' for principal returned.
    Entry fees are retained. No fee refund, closing execution or rebate is
    assumed. Numeric half-value draws and principal refunds stay distinct.
    """
    if outcome is not None:
        proper_score(probabilities, outcome)
    elif (
        len(probabilities) < 2
        or any(not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities.values())
        or abs(sum(probabilities.values()) - 1) > 1e-8
    ):
        raise ValueError("invalid_probability_distribution")
    if fee_rate != POLICY["fee_rate"]:
        raise ValueError("unsupported_fee_schedule")
    budget = _decimal(budget_usd)
    if not budget.is_finite() or budget <= 0:
        raise ValueError("invalid_budget")
    choices = []
    seen = set()
    for contract in contracts:
        identity = (contract["market_id"], contract["side"])
        if identity in seen:
            raise ValueError("duplicate_executable_contract")
        seen.add(identity)
        ask = _decimal(contract["ask"])
        depth = _decimal(contract["ask_size"])
        if not ask.is_finite() or not 0 < ask < 1 or not depth.is_finite() or depth < 0:
            raise ValueError("invalid_executable_price_or_depth")
        payouts = contract["settlement_values"]
        if set(payouts) != set(probabilities):
            raise ValueError("incomplete_contract_settlement_table")
        values = {key: ask if value == "entry_price" else _decimal(value) for key, value in payouts.items()}
        if any(not value.is_finite() or not 0 <= value <= 1 for value in values.values()):
            raise ValueError("invalid_contract_settlement_value")
        quantity = min(int(depth), int(budget / ask))
        while quantity and quantity * ask + taker_fee(quantity, ask) > budget:
            quantity -= 1
        if not quantity:
            continue
        fee = taker_fee(quantity, ask)
        cost = quantity * ask + fee
        ev = quantity * sum(Decimal(str(probabilities[key])) * value for key, value in values.items()) - cost
        if ev <= 0:
            continue
        trade = {
            "called": True,
            "market_id": identity[0],
            "side": identity[1],
            "quantity": quantity,
            "ask": float(ask),
            "fee_usd": float(fee),
            "cost_usd": float(cost),
            "expected_profit_usd": float(ev),
            "pnl_usd": float(quantity * values[outcome] - cost) if outcome is not None else None,
            "settlement_value": float(values[outcome]) if outcome is not None else None,
        }
        choices.append((float(ev / cost), str(identity), trade))
    if not choices:
        return {
            "called": False,
            "cost_usd": 0.0,
            "pnl_usd": 0.0 if outcome is not None else None,
            "fee_usd": 0.0,
        }
    return max(choices, key=lambda item: (item[0], item[1]))[2]
