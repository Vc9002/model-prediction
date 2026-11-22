"""One place for the human-facing model name and edge bucket of a pick.

Display only: ledger model ids, promotion records and buyer whitelists keep their ids. The ids are
confusing to read because they do not say what actually ran: ``ncaab-spread-v6`` rows from
2026-11-16 17:15Z on were priced by the v7 margin engine (``features.margin_engine``), the
``ncaab-spread-v7`` id also writes moneyline picks, and ``cfb-structural-v5`` covers all three NCAAF
markets. ``model_display_name`` names the sport, the market the pick is actually in, and the version
that priced it ("NCAAB Spread v7").

Standard library only: the Rainmeter best-pick widget runs a separate Python with ``-I`` and loads
this file by path.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

# Operator choice 2026-11-22: the bands the best-pick audit and widget already used, so no logged
# pick changes bucket. Lower bound inclusive, upper exclusive; edges are fractions (0.05 = 5%).
EDGE_BUCKETS: tuple[tuple[float | None, float | None, str], ...] = (
    (None, 0.0, "<0%"),
    (0.0, 0.02, "0-2%"),
    (0.02, 0.05, "2-5%"),
    (0.05, 0.08, "5-8%"),
    (0.08, 0.12, "8-12%"),
    (0.12, None, "12%+"),
)
EDGE_BUCKET_LABELS: tuple[str, ...] = tuple(label for _, _, label in EDGE_BUCKETS)

_SPORT_BY_PREFIX = {
    "nba": "NBA",
    "nfl": "NFL",
    "ncaab": "NCAAB",
    "ncaam": "NCAAB",
    "cfb": "NCAAF",
    "ncaaf": "NCAAF",
    "college": "NCAAF",
    "mlb": "MLB",
    "measured": "MLB",
    "wnba": "WNBA",
}
_MARKET_NAMES = {
    "moneyline": "Moneyline",
    "ml": "Moneyline",
    "spread": "Spread",
    "total": "Total",
    "over_under": "Total",
    "nrfi": "NRFI",
}
# Last version token in an id: v7, v7m, v6.2, v10.
_VERSION = re.compile(r"(?<![a-z0-9])v\d+(?:\.\d+)?[a-z]?(?![a-z0-9])", re.IGNORECASE)


def edge_bucket(edge: float) -> str:
    """Bucket label for an edge given as a fraction (0.034 -> "2-5%")."""
    for lo, hi, label in EDGE_BUCKETS:
        if (lo is None or edge >= lo) and (hi is None or edge < hi):
            return label
    return EDGE_BUCKET_LABELS[-1]


def edge_bucket_pct(edge_pct: float) -> str:
    """Same bucket for an edge given in percent (3.4 -> "2-5%")."""
    return edge_bucket(edge_pct / 100.0)


def _number(version: str) -> str:
    match = re.match(r"v(\d+(?:\.\d+)?)", version or "")
    return match.group(1) if match else ""


def _engine_version(payload: Mapping[str, Any] | None) -> str | None:
    """The margin engine a row's payload says actually priced it (NCAAB), if any."""
    if not payload:
        return None
    snapshot = payload.get("model_input_snapshot_json")
    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot or "{}")
        except ValueError:
            return None
    if not isinstance(snapshot, Mapping):
        return None
    features = snapshot.get("features")
    engine = features.get("margin_engine") if isinstance(features, Mapping) else None
    return str(engine).lower() if engine else None


def model_display_name(
    model_id: str,
    market_type: str | None = None,
    payload: Mapping[str, Any] | None = None,
) -> str:
    """ "NCAAB Spread v7" style name: sport, the pick's own market, and the version that priced it.

    ``market_type`` is the market the pick is in (the id's own market word is ignored, since one id
    can serve several markets). ``payload`` is the ledger row's decision payload; when it records the
    NCAAB margin engine, that engine's version wins over the id's.
    """
    mid = str(model_id or "").strip()
    parts = re.split(r"[-_]", mid.lower())
    sport = _SPORT_BY_PREFIX.get(parts[0], parts[0].upper() if parts and parts[0] else "")
    market = _MARKET_NAMES.get(str(market_type or "").strip().lower(), "")
    found = _VERSION.findall(mid)
    version = found[-1].lower() if found else ""
    engine = _engine_version(payload)
    # Only a different engine NUMBER renames: v7m (moneyline variant on the v7 engine) stays v7m.
    if engine and sport == "NCAAB" and _VERSION.fullmatch(engine) and _number(engine) != _number(version):
        version = engine
    return " ".join(x for x in (sport, market, version) if x)
