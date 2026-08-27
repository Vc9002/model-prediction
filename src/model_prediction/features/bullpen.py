"""MLB bullpen strength and fatigue.

Bullpen inning-by-inning usage needs the StatsAPI game snapshots. When those
are cached this computes real numbers; when they are not, it reports
``unavailable_from_source`` — never a fabricated neutral dressed up as data.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

LEAGUE_RELIEF_ERA = 4.0532
# Same shrinkage rationale as models/mlb.py's starter ERA fix: a handful of
# relief innings is extremely noisy on its own (one blown save skews it hard)
# and must not be trusted at full confidence just because it's nonzero.
BULLPEN_PRIOR_INNINGS = 30.0
DEFAULT_SNAPSHOT_PATH = Path("data/mlb_statsapi/game_snapshots.jsonl")
DEFAULT_LOOKBACK_GAMES = 10
# Calendar-day window for bullpen_fatigue_gap, the single source of truth for
# both validation.py's training-time map and learned_forward.py's live
# serving. 2026-08-13 audit: serving used the last-N-games lookback with no
# calendar cap while training summed the trailing 3 calendar days -- a real
# train/serve definition skew.
FATIGUE_WINDOW_DAYS = 3

_RELIEF_INDEX_CACHE: dict[Path, dict[str, list[tuple[datetime, list[dict[str, float]]]]]] = {}


def _baseball_innings(value: Any) -> float:
    try:
        whole, _, outs = str(value).partition(".")
        return int(whole) + (int(outs or 0) / 3)
    except (TypeError, ValueError):
        return 0.0


def _parse_snapshot_time(value: str) -> datetime:
    from ..domain import parse_utc

    return parse_utc(value)


def load_relief_appearance_index(
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
) -> dict[str, list[tuple[datetime, list[dict[str, float]]]]]:
    """team display name -> chronological (game_start, relief_lines) history.

    Built once per process from real completed-game boxscore snapshots
    (``mlb_statsapi.py``'s ``compact_game_snapshot``), cached by path. Each
    team-game's relief_lines excludes that team's own starter (the first
    entry in the boxscore's real ``pitcher_order``), so this is bullpen
    appearances only.
    """
    path = Path(snapshot_path)
    if path in _RELIEF_INDEX_CACHE:
        return _RELIEF_INDEX_CACHE[path]
    index: dict[str, list[tuple[datetime, list[dict[str, float]]]]] = {}
    if not path.exists():
        _RELIEF_INDEX_CACHE[path] = index
        return index
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                snapshot = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                game_start = _parse_snapshot_time(str(snapshot["game_start_utc"]))
            except (KeyError, ValueError):
                continue
            for side_key in ("home", "away"):
                side = snapshot.get(side_key) or {}
                team_name = side.get("team_name")
                if not team_name:
                    continue
                pitcher_order = side.get("pitcher_order") or []
                starter_id = pitcher_order[0] if pitcher_order else None
                relief_lines = [
                    {
                        "innings": _baseball_innings(player["pitching"].get("inningsPitched")),
                        "earned_runs": float(player["pitching"].get("earnedRuns") or 0),
                    }
                    for player in side.get("players", [])
                    if player.get("pitching") and player.get("player_id") != starter_id
                ]
                index.setdefault(team_name, []).append((game_start, relief_lines))
    for games in index.values():
        games.sort(key=lambda item: item[0])
    _RELIEF_INDEX_CACHE[path] = index
    return index


def team_recent_relief_lines(
    team_name: str,
    decision: datetime,
    *,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    lookback_games: int = DEFAULT_LOOKBACK_GAMES,
    lookback_days: int | None = None,
) -> list[dict[str, float]]:
    """Real relief appearances from this team's last N completed games before
    decision -- or, when *lookback_days* is set, only the games within that
    trailing calendar-day window (``0 <= days_before_decision <= lookback_days``,
    UTC dates, matching validation.py's fatigue-map window exactly)."""
    index = load_relief_appearance_index(snapshot_path)
    games = [game for game in index.get(team_name, []) if game[0] < decision]
    if lookback_days is not None:
        games = [game for game in games if 0 <= (decision.date() - game[0].date()).days <= lookback_days]
    recent = games[-lookback_games:]
    return [line for _, lines in recent for line in lines]


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
    raw_era = 9 * earned / innings if innings > 0 else None
    # Credibility-weighted shrinkage toward the league relief ERA by sample
    # size, mirroring the starter-ERA fix in models/mlb.py's
    # _starter_weakness -- confirmed by that same investigation to matter a
    # lot for any small pitching sample, and relief innings per team-game are
    # typically smaller than a full season of starts.
    credibility = innings / (innings + BULLPEN_PRIOR_INNINGS) if innings > 0 else 0.0
    era = credibility * raw_era + (1 - credibility) * LEAGUE_RELIEF_ERA if raw_era is not None else None
    fatigue = sum(recent_relief_innings_by_day or [])
    return {
        "bullpen_era": round(era, 4) if era is not None else None,
        "bullpen_weakness_index": round(era / LEAGUE_RELIEF_ERA, 6) if era is not None else 1.0,
        "fatigue_innings_last3": round(fatigue, 2) if recent_relief_innings_by_day else None,
        "status": "available" if era is not None else "insufficient_sample",
    }
