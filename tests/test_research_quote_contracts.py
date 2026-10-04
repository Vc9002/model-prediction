import copy
import json

import pytest

from model_prediction.data_sources.mlb_market_odds import canonical_mlb_market_snapshot_hash
from model_prediction.research_pairing import simulate_contracts
from model_prediction.research_quote_contracts import (
    PairedQuoteArchive,
    executable_probabilities,
    validate_mlb_halfpoint,
)


def envelope_fixture(market="spread"):
    observed, start = "2026-09-11T20:00:00Z", "2026-09-11T22:00:00Z"
    slug = "asc-mlb-col-det-2026-09-11-pos-1pt5" if market == "spread" else "tsc-mlb-col-det-2026-09-11-8pt5"
    names = ("away", "home") if market == "spread" else ("over", "under")
    lines = (1.5, -1.5) if market == "spread" else (8.5, 8.5)
    sides, quotes = [], {}
    book = {
        "provider": "polymarket_us",
        "reconstructed": False,
        "reconstruction_status_source": "live_gateway_market_and_book_response",
        "market_state": "MARKET_STATE_OPEN",
        "market_slug": slug,
        "market_id": "123",
        "line": lines[0],
        "team": "Rockies",
        "observed_at_utc": observed,
        "event_start_utc": start,
        "transact_time_utc": observed,
    }
    for i, (selection, line, direction) in enumerate(zip(names, lines, ("long", "short"), strict=True)):
        description = f"{selection} {line}"
        sides.append(
            {
                "is_long": i == 0,
                "selection": selection,
                "line": line,
                "team": "Rockies" if i == 0 else "Tigers",
                "description": description,
            }
        )
        book[direction] = {
            "ask": 0.4 if i == 0 else 0.65,
            "bid": 0.39 if i == 0 else 0.64,
            "ask_size": 50,
            "description": description,
        }
        quotes[selection] = {
            "selection": selection,
            "line": line,
            "polymarket_side": direction,
            "market_slug": slug,
            "decision_probability": book[direction]["ask"],
            "american_odds": 150 if i == 0 else -186,
        }
    envelope = {
        "provider": "polymarket_us",
        "event_id": "espn-1",
        "event_start_utc": start,
        "observed_at_utc": observed,
        "home_team": "Tigers",
        "away_team": "Rockies",
        "markets": {market: quotes},
        "snapshot_hash": "test",
        "raw_response": {
            "books": {market: book},
            "event": {
                "provider": "polymarket_us",
                "league": "MLB",
                "event_start_utc": start,
                "event_slug": "mlb-col-det-2026-09-11",
                "event_id": "different-provider-id",
                "markets": [
                    {
                        "market_id": "123",
                        "market_slug": slug,
                        "market_type": market,
                        "line": lines[0],
                        "sides": sides,
                    }
                ],
            },
        },
    }
    record = {
        "sport": "MLB",
        "market_type": market,
        "selection": names[0],
        "line": lines[0],
        "event_id": "espn-1",
        "event_start_utc": start,
        "created_at_utc": "2026-09-11T20:01:00Z",
        "decision_payload_json": json.dumps(
            {
                "home_team": "Tigers",
                "away_team": "Rockies",
                "market_quote_observed_at_utc": observed,
                "american_odds": 150,
            }
        ),
    }
    return record, envelope


@pytest.mark.parametrize("market", ["spread", "total"])
def test_halfpoint_both_sides_use_exact_bbo_and_explicit_payouts(market):
    record, envelope = envelope_fixture(market)
    evidence = validate_mlb_halfpoint(record, envelope)
    labels = ("away_cover", "home_cover") if market == "spread" else ("over", "under")
    p = {labels[0]: 0.6, labels[1]: 0.4, "push": 0.0}
    p = executable_probabilities(p, evidence)
    pre = simulate_contracts(p, None, evidence["contracts"])
    assert pre["called"] and pre["side"] == "long" and pre["pnl_usd"] is None
    assert pre["cost_usd"] <= 5
    for outcome in labels:
        settled = simulate_contracts(p, outcome, evidence["contracts"])
        assert {k: v for k, v in settled.items() if k not in {"pnl_usd", "settlement_value"}} == {
            k: v for k, v in pre.items() if k not in {"pnl_usd", "settlement_value"}
        }
        assert settled["pnl_usd"] == pytest.approx(
            (pre["quantity"] if outcome == labels[0] else 0) - pre["cost_usd"]
        )
    with pytest.raises(ValueError, match="impossible"):
        executable_probabilities(p | {"push": 0.01}, evidence)


@pytest.mark.parametrize(
    "change",
    [
        "integer",
        "f5",
        "participant",
        "selected_line",
        "raw_line",
        "raw_team",
        "raw_side",
        "description",
        "stale",
        "future_book",
        "reconstructed",
        "closed",
        "crossed",
        "no_depth",
        "nan",
        "projection",
        "duplicate_market",
    ],
)
def test_halfpoint_rejects_semantic_mismatches(change):
    record, envelope = envelope_fixture()
    book = envelope["raw_response"]["books"]["spread"]
    event = envelope["raw_response"]["event"]
    if change == "integer":
        record["line"] = 1
    elif change == "f5":
        event["event_slug"] += "-f5"
        book["market_slug"] = "asc-" + event["event_slug"] + "-pos-1pt5"
    elif change == "participant":
        envelope["home_team"] = "Other"
    elif change == "selected_line":
        record["line"] = -1.5
    elif change == "raw_line":
        event["markets"][0]["sides"][1]["line"] = 1.5
    elif change == "raw_team":
        event["markets"][0]["sides"][1]["team"] = "Rockies"
    elif change == "raw_side":
        event["markets"][0]["sides"][1]["is_long"] = True
    elif change == "description":
        book["short"]["description"] = "other"
    elif change == "stale":
        record["created_at_utc"] = "2026-09-11T20:06:00Z"
    elif change == "future_book":
        book["transact_time_utc"] = "2026-09-11T20:01:00Z"
    elif change == "reconstructed":
        book["reconstructed"] = True
    elif change == "closed":
        book["market_state"] = "CLOSED"
    elif change == "crossed":
        book["long"]["bid"] = 0.7
    elif change == "no_depth":
        book["short"]["ask_size"] = 0
    elif change == "nan":
        book["long"]["ask"] = "NaN"
    elif change == "projection":
        envelope["markets"]["spread"]["away"]["decision_probability"] = 0.42
    elif change == "duplicate_market":
        event["markets"].append(copy.deepcopy(event["markets"][0]))
    with pytest.raises((ValueError, TypeError)):
        validate_mlb_halfpoint(record, envelope)


def test_exact_envelope_hash_path_and_selected_quote_are_required(tmp_path):
    record, envelope = envelope_fixture()
    path = tmp_path / "data/market_odds_snapshots.jsonl"
    path.parent.mkdir()
    sha = canonical_mlb_market_snapshot_hash(envelope)
    envelope.update(snapshot_hash=sha, snapshot_record_id=sha, snapshot_archive_path=str(path))
    record.update(
        market_snapshot_hash=sha, market_snapshot_record_id=sha, market_snapshot_archive_path=str(path)
    )
    path.write_text(json.dumps(envelope) + "\n")
    candidate = {"sport": "MLB", "market": "spread"}
    archive = PairedQuoteArchive(tmp_path)
    assert archive.contracts(record, candidate)["source_snapshot_hash"] == sha
    envelope["raw_response"]["books"]["spread"]["long"]["ask_size"] = 400
    path.write_text(json.dumps(envelope) + "\n")
    with pytest.raises(ValueError, match="hash binding"):
        archive.contracts(record, candidate)
    with pytest.raises(ValueError, match="outside approved"):
        archive.contracts(record | {"market_snapshot_archive_path": str(tmp_path / "other")}, candidate)


@pytest.mark.parametrize("bad", ["bad", None, True, "NaN", "Infinity"])
def test_invalid_decimal_contract_inputs_are_reportable_failures(bad):
    contract = {
        "market_id": "x",
        "side": "long",
        "ask": bad,
        "ask_size": 5,
        "settlement_values": {"a": 1, "b": 0},
    }
    with pytest.raises(ValueError):
        simulate_contracts({"a": 0.8, "b": 0.2}, None, [contract])


def test_unsettled_abstention_has_no_realized_profit():
    contract = {
        "market_id": "x",
        "side": "long",
        "ask": 0.6,
        "ask_size": 5,
        "settlement_values": {"a": 1, "b": 0},
    }
    assert simulate_contracts({"a": 0.5, "b": 0.5}, None, [contract]) == {
        "called": False,
        "cost_usd": 0.0,
        "pnl_usd": None,
        "fee_usd": 0.0,
    }


def test_embedded_quote_retains_hash_binding_after_live_archive_disappears(tmp_path):
    from model_prediction.research_quote_contracts import verify_embedded_quote

    record, envelope = envelope_fixture()
    path = tmp_path / "data/market_odds_snapshots.jsonl"
    path.parent.mkdir()
    sha = canonical_mlb_market_snapshot_hash(envelope)
    envelope.update(snapshot_hash=sha, snapshot_record_id=sha, snapshot_archive_path=str(path))
    record.update(
        market_snapshot_hash=sha, market_snapshot_record_id=sha, market_snapshot_archive_path=str(path)
    )
    path.write_text(json.dumps(envelope) + "\n")
    candidate = {"sport": "MLB", "market": "spread"}
    evidence = PairedQuoteArchive(tmp_path).contracts(record, candidate)
    path.unlink()
    assert verify_embedded_quote(record, candidate, evidence) == evidence
    changed = copy.deepcopy(evidence)
    changed["contracts"][0]["settlement_values"]["away_cover"] = 0
    with pytest.raises(ValueError, match="contract_terms"):
        verify_embedded_quote(record, candidate, changed)
    changed = copy.deepcopy(evidence)
    changed["raw_record"]["raw_response"]["books"]["spread"]["long"]["ask_size"] = 1000
    with pytest.raises(ValueError, match="hash"):
        verify_embedded_quote(record, candidate, changed)
