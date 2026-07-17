"""MLB starting-pitcher rate stats: ERA, FIP, K%, BB%, WHIP.

Computed from pitcher game logs already cached by the ESPN/StatsAPI feeds. The
current Measured Edge path keeps its own ``PitcherForm``; this module is the
richer pipeline for subsequent versioned MLB experiments.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


# League-average FIP constant; updated annually, stored here as a named default.
FIP_CONSTANT = 3.15


@dataclass(frozen=True)
class PitcherGameLine:
    innings: float
    earned_runs: int
    strikeouts: int
    walks: int
    hits: int
    home_runs: int
    hit_by_pitch: int
    batters_faced: int


def pitcher_rates(lines: Sequence[PitcherGameLine]) -> dict[str, Any]:
    innings = sum(line.innings for line in lines)
    batters = sum(line.batters_faced for line in lines)
    if innings <= 0:
        return {
            "starts": len(lines),
            "innings": 0.0,
            "era": None,
            "fip": None,
            "k_rate": None,
            "bb_rate": None,
            "whip": None,
            "status": "insufficient_sample",
        }
    strikeouts = sum(line.strikeouts for line in lines)
    walks = sum(line.walks for line in lines)
    hits = sum(line.hits for line in lines)
    home_runs = sum(line.home_runs for line in lines)
    hbp = sum(line.hit_by_pitch for line in lines)
    return {
        "starts": len(lines),
        "innings": round(innings, 2),
        "era": round(9 * sum(line.earned_runs for line in lines) / innings, 4),
        "fip": round((13 * home_runs + 3 * (walks + hbp) - 2 * strikeouts) / innings + FIP_CONSTANT, 4),
        "k_rate": round(strikeouts / batters, 4) if batters else None,
        "bb_rate": round(walks / batters, 4) if batters else None,
        "whip": round((walks + hits) / innings, 4),
        "status": "available",
    }
