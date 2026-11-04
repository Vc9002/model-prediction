"""Immutable, all-listed-contract college forecast and executable-quote evidence."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .domain import parse_utc
from .runtime_paths import RuntimePaths
from .snapshot_archives import snapshot_paths

SUPPORTED = frozenset({"moneyline", "spread", "total"})


def evidence_root(data_root: str | Path) -> Path:
    """Route checkout data to its canonical runtime archive; keep test roots isolated."""
    root = Path(data_root)
    paths = RuntimePaths.resolve()
    return paths.runtime_root if root.resolve() == (paths.repo_root / "data").resolve() else root


def _digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _same_line(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    try:
        return math.isclose(float(left), float(right), abs_tol=1e-9)
    except (TypeError, ValueError):
        return False


def _valid_quote(
    row: dict[str, Any], event: dict[str, Any], market: dict[str, Any], decision_at: datetime
) -> str | None:
    for field, expected in (
        ("event_id", event.get("event_id")),
        ("market_id", market.get("market_id")),
        ("market_slug", market.get("market_slug")),
        ("market_type", market.get("market_type")),
        ("event_start_utc", event.get("event_start_utc")),
    ):
        if str(row.get(field) or "") != str(expected or ""):
            return f"{field}_mismatch"
    if not _same_line(row.get("line"), market.get("line")):
        return "line_mismatch"
    if row.get("provider") != "polymarket_us" or row.get("reconstructed") is not False:
        return "nonprospective_source"
    if row.get("timestamp_valid") is not True or row.get("market_state") != "MARKET_STATE_OPEN":
        return "invalid_timestamp_or_state"
    try:
        observed = parse_utc(str(row["observed_at_utc"]))
        transacted = parse_utc(str(row["transact_time_utc"]))
        event_start = parse_utc(str(event["event_start_utc"]))
    except (KeyError, TypeError, ValueError):
        return "invalid_time"
    if observed > decision_at or transacted > decision_at or observed >= event_start:
        return "postdecision_or_poststart_quote"
    for side in ("long", "short"):
        book = row.get(side) or {}
        try:
            ask = float(book["ask"])
            bid = float(book["bid"])
        except (KeyError, TypeError, ValueError):
            return "missing_two_sided_bbo"
        if not (math.isfinite(ask) and math.isfinite(bid) and 0 < bid <= ask < 1):
            return "invalid_two_sided_bbo"
    return None


def _quote_index(archive_root: Path, sport: str, day: str) -> dict[str, list[tuple[dict[str, Any], Path]]]:
    index: dict[str, list[tuple[dict[str, Any], Path]]] = defaultdict(list)
    for path in snapshot_paths(archive_root, sport.lower(), day):
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and row.get("market_slug"):
                    index[str(row["market_slug"])].append((row, path))
    return index


def build_candidate_evidence(
    *,
    sport: str,
    day: str,
    events: list[dict[str, Any]],
    probabilities: dict[str, dict[str, Any]],
    archive_root: Path,
    decision_at: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Record every listed market, including unsupported and quote-missing rows.

    ``probabilities`` is keyed by exact market slug and contains the model's
    probabilities at that market's exact line. It never supplies a quote.
    """
    quotes = _quote_index(archive_root, sport, day)
    rows: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    by_market: dict[str, Counter[str]] = defaultdict(Counter)
    for event in events:
        for market in event.get("markets", []):
            slug = str(market.get("market_slug") or "")
            kind = str(market.get("market_type") or "unknown")
            if not slug:
                reasons["missing_slug"] += 1
                by_market[kind]["missing_slug"] += 1
                continue
            candidates = quotes.get(slug, [])
            valid = [
                (row, path)
                for row, path in candidates
                if _valid_quote(row, event, market, decision_at) is None
            ]
            latest = max(valid, key=lambda item: parse_utc(item[0]["observed_at_utc"])) if valid else None
            quote_reason = (
                "matched"
                if latest
                else "not_archived"
                if not candidates
                else _valid_quote(candidates[-1][0], event, market, decision_at) or "quote_rejected"
            )
            model = probabilities.get(slug) or {}
            model_probs = model.get("probabilities") or {}
            model_reason = model.get("reason") or ("scored" if model_probs else "model_unavailable")
            side_rows = []
            for side in market.get("sides", []):
                selection = str(side.get("selection") or "")
                is_long = bool(side.get("is_long"))
                quote_side = (
                    (latest[0].get("long") if is_long else latest[0].get("short")) if latest else None
                )
                side_rows.append(
                    {
                        "selection": selection,
                        "is_long": is_long,
                        "line": side.get("line"),
                        "model_probability": model_probs.get(selection),
                        "executable_ask": quote_side.get("ask") if quote_side else None,
                        "executable_ask_size": quote_side.get("ask_size") if quote_side else None,
                        "executable_bid": quote_side.get("bid") if quote_side else None,
                    }
                )
            row = {
                "schema": "college_contract_candidate_v1",
                "sport": sport.lower(),
                "decision_at_utc": decision_at.isoformat(),
                "event_id": str(event.get("event_id") or ""),
                "event_start_utc": event.get("event_start_utc"),
                "event_title": event.get("title"),
                "market_id": str(market.get("market_id") or ""),
                "market_slug": slug,
                "market_type": kind,
                "line": market.get("line"),
                "model_version": model.get("model_version"),
                "model_event_id": model.get("model_event_id"),
                "feature_packet_hash": model.get("feature_packet_hash"),
                "model_reason": model_reason,
                "quote_reason": quote_reason,
                "quote_observed_at_utc": latest[0]["observed_at_utc"] if latest else None,
                "quote_transact_time_utc": latest[0]["transact_time_utc"] if latest else None,
                "quote_hash": _digest(latest[0]) if latest else None,
                "quote_archive_path": str(latest[1].resolve()) if latest else None,
                "sides": side_rows,
            }
            row["record_hash"] = _digest(row)
            rows.append(row)
            reasons[quote_reason] += 1
            by_market[kind][quote_reason] += 1
            by_market[kind][model_reason] += 1
    summary = {
        "sport": sport.lower(),
        "day": day,
        "listed_contracts": len(rows),
        "quote_matched": reasons["matched"],
        "quote_coverage": reasons["matched"] / len(rows) if rows else None,
        "model_scored": sum(bool(probabilities.get(r["market_slug"], {}).get("probabilities")) for r in rows),
        "complete": sum(r["quote_reason"] == "matched" and r["model_reason"] == "scored" for r in rows),
        "quote_reasons": dict(reasons),
        "by_market": {kind: dict(counts) for kind, counts in sorted(by_market.items())},
    }
    return rows, summary


def save_candidate_evidence(rows: list[dict[str, Any]], summary: dict[str, Any], archive_root: Path) -> Path:
    """Write one content-addressed packet; identical reruns are idempotent."""
    root = (
        evidence_root(archive_root)
        / "research"
        / "college_contract_candidates"
        / summary["sport"]
        / summary["day"]
    )
    root.mkdir(parents=True, exist_ok=True)
    digest = _digest({"summary": summary, "row_hashes": [row["record_hash"] for row in rows]})
    target = root / f"{digest}.json"
    if not target.exists():
        payload = {"summary": summary, "rows": rows, "packet_hash": digest}
        temp = root / f".{digest}.tmp"
        temp.write_text(json.dumps(payload, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
        temp.replace(target)
    return target
