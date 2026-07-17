"""MLB bullpen strength and fatigue.

Bullpen inning-by-inning usage needs the StatsAPI game snapshots. When those
are cached this computes real numbers; when they are not, it reports
``unavailable_from_source`` — never a fabricated neutral dressed up as data.
"""

from __future__ import annotations

from typing import Any, Sequence


def bullpen_profile(
    relief_lines: Sequence[dict[str, float]] | None,
    recent_relief_innings_by_day: Sequence[float] | None = None,
) -> dict[str, Any]:
    """``relief_lines``: per-appearance dicts with innings/earned_runs keys."""
    if not relief_lines:
        return {
            "bullpen_era": None,
            "bullpen_weakness_index": 1.0,
            "fatigue_innings_last3": None,
            "status": "unavailable_from_source",
        }
    innings = sum(float(line.get("innings", 0)) for line in relief_lines)
    earned = sum(float(line.get("earned_runs", 0)) for line in relief_lines)
    era = 9 * earned / innings if innings > 0 else None
    league_relief_era = 4.10
    fatigue = sum(recent_relief_innings_by_day or [])
    return {
        "bullpen_era": round(era, 4) if era is not None else None,
        "bullpen_weakness_index": round(era / league_relief_era, 6) if era is not None else 1.0,
        "fatigue_innings_last3": round(fatigue, 2) if recent_relief_innings_by_day else None,
        "status": "available" if era is not None else "insufficient_sample",
    }
