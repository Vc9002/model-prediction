"""Market-relative production diagnostics (Phase-23 evidence, wired 2026-08-27).

Reads SETTLED rows from the canonical runtime ledger and reports the
market_eval battery per (sport, market_type): delta logloss/Brier of the
model against the market probability recorded at decision time, edge
distribution, and ROI at the entry market price. This is the production
surface of the market-edge program — read-only evidence, fail-soft, and
deliberately informational: it never flips health status by itself, and
it never blocks a ledger write.

Limitation (documented, not accidental): the sqlite ledger stores the
decision-time market probability only — no closing-price column — so the
battery here is model-vs-market-at-decision-time; the CLV-vs-closing
battery lives in the research harnesses (`market_eval.market_relative_report`
with real closing snapshots).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from .market_eval import MarketEvalRow, market_relative_report
from .runtime_ledger_store import RuntimeLedgerStore
from .runtime_paths import RuntimePaths


def market_relative_from_rows(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Battery per (sport, market_type) from settled ledger row dicts.

    Rows are the ``RuntimeLedgerStore.records`` shape: settled rows carry
    ``model_probability``, ``market_probability`` (decision-time),
    ``result`` ("win"/"loss"), and timestamps. Push/void rows and rows
    missing either probability are skipped — the report is evidence, not
    a gate, so it prefers honest small samples over fabricated ones.
    """
    groups: dict[tuple[str, str], list[MarketEvalRow]] = defaultdict(list)
    settled_n: dict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        if row.get("status") != "settled" or row.get("result") not in ("win", "loss"):
            continue
        sport = str(row.get("sport") or "unknown")
        market_type = str(row.get("market_type") or "unknown")
        # settled_n counts every settled win/loss row for the market,
        # independent of whether it carries market-evidence probabilities —
        # this is the denominator gap the Phase-23 review flagged: ledger
        # row count is not the same as trustworthy market-evidence count.
        settled_n[(sport, market_type)] += 1
        model_prob = row.get("model_probability")
        market_prob = row.get("market_probability")
        if model_prob is None or market_prob is None:
            continue
        if not (0.0 < model_prob < 1.0 and 0.0 < market_prob < 1.0):
            continue
        decision_utc = str(row.get("event_start_utc") or row.get("created_at_utc") or "")
        groups[(sport, market_type)].append(
            MarketEvalRow(
                event_id=str(row.get("event_id") or row.get("pick_id") or ""),
                decision_utc=decision_utc,
                market_type=market_type,
                line=row.get("line"),
                model_prob=float(model_prob),
                market_prob=float(market_prob),
                # Entry-time executable price proxy: the market probability
                # recorded with the decision (see module limitation note).
                bet_price=float(market_prob),
                outcome=1 if row.get("result") == "win" else 0,
            )
        )
    by_market: dict[str, Any] = {}
    keys = sorted(set(groups) | set(settled_n))
    for key in keys:
        sport, market_type = key
        report = market_relative_report(groups.get(key, []))
        report["settled_n"] = settled_n.get(key, 0)
        report["market_evidence_n"] = len(groups.get(key, []))
        by_market[f"{sport}:{market_type}"] = report
    return {"status": "ok", "n_markets": len(by_market), "by_market": by_market}


def market_relative_health(paths: RuntimePaths) -> dict[str, Any]:
    """Read the canonical runtime ledger and report the battery.

    Fail-soft evidence: any read failure becomes ``{"status":
    "unavailable"}`` with the reason — callers must never degrade on this
    section's absence.
    """
    try:
        with RuntimeLedgerStore(paths) as store:
            rows = store.records()
    except Exception as exc:  # noqa: BLE001 — evidence must never crash health
        return {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}
    return market_relative_from_rows(rows)
