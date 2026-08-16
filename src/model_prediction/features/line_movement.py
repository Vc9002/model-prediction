"""Line-movement shadow feature from repeated point-in-time market snapshots.

Reads the daily-captured Polymarket odds snapshot file (one JSON record per
observation; the same event appears many times across a day as the daily
cycle re-captures the book) and measures how the market's implied
probability for one side moved between the first and last observation
strictly before a decision time.

Why it might matter: a pick whose side the market has been *moving toward*
("with the steam") has historically been a healthier signal than one the
market is moving away from (reverse line movement) -- the market's own
re-rating carries information independent of the model. That hypothesis is
NOT established in this codebase yet; this module is a shadow feature:
inert, wired into no model, provided so the hypothesis can be backtested
against settled picks before any promotion decision.

Point-in-time contract matches every other feature provider here: only
observations with ``observed_at_utc <= decision`` are considered, and the
module never guesses -- fewer than two pre-decision observations for a side
returns ``status: "insufficient_observations"`` rather than a fabricated 0.0.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_SNAPSHOT_PATH = Path("data/market_odds_snapshots.jsonl")


def _observations_for_side(
    snapshot_path: Path,
    event_id: str,
    market_type: str,
    selection: str,
) -> list[tuple[datetime, float]]:
    """Chronological (observed_at_utc, side probability) pairs for one side."""
    rows: list[tuple[datetime, float]] = []
    if not snapshot_path.exists():
        return rows
    from ..domain import parse_utc

    with snapshot_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(record.get("event_id") or "") != event_id:
                continue
            raw_ts = record.get("observed_at_utc")
            side = ((record.get("markets") or {}).get(market_type) or {}).get(selection) or {}
            raw_prob = side.get("decision_probability")
            if raw_ts is None or raw_prob is None:
                continue
            try:
                observed_at = parse_utc(str(raw_ts))
                probability = float(raw_prob)
            except (ValueError, TypeError):
                continue
            rows.append((observed_at, probability))
    rows.sort(key=lambda item: item[0])
    return rows


def line_movement(
    event_id: str,
    market_type: str,
    selection: str,
    decision: datetime,
    *,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
) -> dict[str, Any]:
    """Signed market movement for ``selection`` before ``decision``.

    Returns a dict with ``status`` -- "available" when at least two distinct
    pre-decision observations exist for the side, "insufficient_observations"
    when fewer, "unavailable_from_source" when the event is absent from the
    snapshot file. ``movement`` is (last pre-decision probability - first
    observed probability), positive when the market moved *toward* the
    selected side. Point-in-time safe by construction: observations at or
    after ``decision`` are excluded.
    """
    path = Path(snapshot_path)
    observations = _observations_for_side(path, event_id, market_type, selection)
    if not observations:
        return {"status": "unavailable_from_source", "movement": None}
    prior = [(ts, prob) for ts, prob in observations if ts < decision]
    if len(prior) < 2:
        return {
            "status": "insufficient_observations",
            "movement": None,
            "observations_before_decision": len(prior),
        }
    first_ts, first_prob = prior[0]
    last_ts, last_prob = prior[-1]
    return {
        "status": "available",
        "movement": round(last_prob - first_prob, 6),
        "first_probability": first_prob,
        "decision_probability": last_prob,
        "observations_before_decision": len(prior),
        "span_seconds": round((last_ts - first_ts).total_seconds(), 1),
    }
