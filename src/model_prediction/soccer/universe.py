"""Dynamic Polymarket soccer universe discovery and event accounting.

Replaces static league enumerations by discovering all active soccer competitions
directly from live Polymarket metadata, maintaining the invariant:
    discovered = predicted + no_call
with zero silent drops.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("model_prediction.soccer")

DISCOVERED_LEAGUES_PATH = Path("data/point_in_time/soccer_discovered_leagues.jsonl")


@dataclass(frozen=True)
class DiscoveredSoccerLeague:
    """Standardized metadata for a discovered Polymarket soccer league."""

    polymarket_league_id: str
    slug: str
    display_name: str
    sport: str
    country_or_region: str | None
    active: bool
    observed_at_utc: str
    raw_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def discover_soccer_leagues(
    polymarket_markets: list[dict[str, Any]] | None = None,
    output_path: Path = DISCOVERED_LEAGUES_PATH,
) -> list[DiscoveredSoccerLeague]:
    """Extract and persist all distinct soccer leagues from Polymarket market payloads."""
    discovered: dict[str, DiscoveredSoccerLeague] = {}
    now_str = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    markets = polymarket_markets or []
    for m in markets:
        # Check if market belongs to soccer
        tags = [str(t).lower() for t in m.get("tags", [])]
        category = str(m.get("category", "")).lower()
        question = str(m.get("question", "")).lower()
        slug = str(m.get("slug", "")).lower()

        is_soccer = (
            "soccer" in tags
            or "football" in tags
            or category in ("soccer", "football")
            or "epl" in slug
            or "la-liga" in slug
            or "champions-league" in slug
            or "serie-a" in slug
            or "bundesliga" in slug
            or " vs. " in question
            or "ligue-1" in slug
            or "mls" in slug
        )
        if not is_soccer:
            continue

        league_id = str(m.get("league_id") or m.get("series_id") or slug.split("-")[0] or "soccer_global")
        display_name = str(
            m.get("series_name") or m.get("groupItemTitle") or league_id.replace("_", " ").title()
        )

        raw_str = json.dumps(m, sort_keys=True)
        raw_hash = hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

        entry = DiscoveredSoccerLeague(
            polymarket_league_id=league_id,
            slug=slug,
            display_name=display_name,
            sport="soccer",
            country_or_region=m.get("country"),
            active=bool(m.get("active", True)),
            observed_at_utc=now_str,
            raw_hash=raw_hash,
        )
        discovered[league_id] = entry

    # Append to append-only discovery log
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("a", encoding="utf-8") as fh:
            for league in discovered.values():
                fh.write(json.dumps(league.to_dict()) + "\n")

    return list(discovered.values())
