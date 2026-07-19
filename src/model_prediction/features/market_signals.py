"""Polymarket price-movement and liquidity signals from stored BBO snapshots.

Reads only the local snapshot store — never a live API. These signals are for
the market-residual layer and market-lag detection; they must NEVER feed the
independent game model (market isolation rule).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..domain import parse_utc


def market_movement(
    snapshot_path: str | Path,
    market_slug: str,
    side: str = "long",
) -> dict[str, Any]:
    """Movement, spread, and depth for one market side across stored snapshots."""
    path = Path(snapshot_path)
    if not path.exists():
        return {"status": "no_snapshot_store", "observations": 0}
    rows: list[tuple[Any, dict[str, Any]]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            if item.get("market_slug") != market_slug:
                continue
            observed = item.get("observed_at_utc")
            book = item.get(side)
            if observed and isinstance(book, dict):
                rows.append((parse_utc(observed), book))
    rows.sort(key=lambda pair: pair[0])
    if not rows:
        return {"status": "no_observations", "observations": 0}
    first, last = rows[0][1], rows[-1][1]
    first_ask, last_ask = first.get("ask"), last.get("ask")
    last_bid = last.get("bid")
    spread = (
        round(float(last_ask) - float(last_bid), 6)
        if last_ask is not None and last_bid is not None
        else None
    )
    return {
        "status": "ok",
        "observations": len(rows),
        "first_observed_utc": rows[0][0].isoformat(),
        "last_observed_utc": rows[-1][0].isoformat(),
        "ask_move": (
            round(float(last_ask) - float(first_ask), 6)
            if first_ask is not None and last_ask is not None
            else None
        ),
        "current_ask": last_ask,
        "current_bid": last_bid,
        "bid_ask_spread": spread,
        "ask_size": last.get("ask_size"),
        "bid_size": last.get("bid_size"),
    }
