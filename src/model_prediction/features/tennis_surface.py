"""Surface-specific tennis features from cached Sackmann match records.

Tennis matches live in ``data/historical/tennis_matches_all.jsonl`` with a
different shape from team games (winner/loser rows, surface column). This
module reads that file directly rather than the team-game cache.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import FeatureContext, register_feature

SURFACES = ("Hard", "Clay", "Grass", "Carpet")


def load_matches(data_root: Path, as_of_date: str) -> list[dict[str, Any]]:
    path = data_root / "historical" / "tennis_matches_all.jsonl"
    if not path.exists():
        return []
    matches = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            raw = json.loads(line)
            match_date = str(raw.get("match_date", ""))[:10]
            if match_date and match_date < as_of_date:
                matches.append(raw)
    return matches


def surface_profile(matches: list[dict[str, Any]], player: str) -> dict[str, Any]:
    profile: dict[str, Any] = {}
    for surface in SURFACES:
        wins = sum(
            1 for match in matches if match.get("winner") == player and match.get("surface") == surface
        )
        losses = sum(
            1 for match in matches if match.get("loser") == player and match.get("surface") == surface
        )
        total = wins + losses
        profile[surface.lower()] = {
            "matches": total,
            "win_rate": round(wins / total, 6) if total else None,
        }
    recent = [
        1.0 if match.get("winner") == player else 0.0
        for match in matches
        if player in (match.get("winner"), match.get("loser"))
    ][-10:]
    profile["recent_win_rate"] = round(sum(recent) / len(recent), 6) if recent else None
    profile["serve_return_status"] = "unavailable_from_source"
    return profile


@register_feature("tennis_surface")
def tennis_surface_snapshot(context: FeatureContext) -> dict[str, Any]:
    matches = load_matches(context.data_root, context.as_of_date)
    player_names = {
        name for match in matches for name in (match.get("winner"), match.get("loser")) if name is not None
    }
    players = sorted(player_names)
    return {
        "matches": len(matches),
        "players": {player: surface_profile(matches, str(player)) for player in players},
    }
