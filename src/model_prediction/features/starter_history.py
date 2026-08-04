"""Live per-starter rolling ERA, from real completed-game boxscore snapshots.

Mirrors ``features/bullpen.py``'s design exactly (same snapshot source, same
point-in-time index-then-filter shape, same real-not-fabricated philosophy)
but keyed by starting pitcher name instead of team, and restricted to each
game's starter (``pitcher_order[0]``) rather than the relievers.

Definition matches ``validation.py::_load_starter_era_map`` (the same
methodology validated via a real walk-forward backtest, 2026-08-04): rolling
ERA over a pitcher's last up to 5 real starts, requiring at least 2 prior
starts before trusting the number. Where that module reads a fixed, frozen
snapshot for backtesting, this one supports a live "as of this moment" query
against whatever the snapshot file currently contains -- which is only
correct if the file is kept current (see ``cli.py``'s daily capture step;
this module makes no network calls of its own and reports
``unavailable_from_source`` rather than guessing when a starter has no
resolvable history).

Real, if rare, risk carried over from the same tradeoff every other cross-
source name match in this codebase makes (team names via ``_team_matches``,
esports players via ``_identity_key``): two different real MLB starters
sharing an identical normalized name would silently merge histories. Not
believed to have ever occurred among real starting pitchers in this dataset,
but not structurally ruled out either -- a real ESPN athleteId -> MLB Stats
API player_id crosswalk would close this gap if it becomes a live concern.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_SNAPSHOT_PATH = Path("data/mlb_statsapi/game_snapshots.jsonl")
DEFAULT_LOOKBACK_STARTS = 5
MINIMUM_PRIOR_STARTS = 2

_STARTER_INDEX_CACHE: dict[Path, dict[str, list[tuple[datetime, float, float]]]] = {}


def _baseball_innings(value: Any) -> float:
    try:
        whole, _, outs = str(value).partition(".")
        return int(whole) + (int(outs or 0) / 3)
    except (TypeError, ValueError):
        return 0.0


def _normalize_name(name: str) -> str:
    return "".join(c.lower() for c in name if c.isalnum())


def load_starter_index(
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
) -> dict[str, list[tuple[datetime, float, float]]]:
    """Normalized starter name -> chronological (game_start, innings, earned_runs).

    Only the game's own starter (``pitcher_order[0]``), and only when that
    player actually has real pitching stats recorded (a "Scheduled" -- not
    yet played -- snapshot has an empty ``pitching`` dict and is correctly
    skipped, same guard ``validation.py``'s training-time builder uses).
    """
    path = Path(snapshot_path)
    if path in _STARTER_INDEX_CACHE:
        return _STARTER_INDEX_CACHE[path]
    index: dict[str, list[tuple[datetime, float, float]]] = {}
    if not path.exists():
        _STARTER_INDEX_CACHE[path] = index
        return index
    from ..domain import parse_utc

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
                game_start = parse_utc(str(snapshot["game_start_utc"]))
            except (KeyError, ValueError):
                continue
            for side_key in ("home", "away"):
                side = snapshot.get(side_key) or {}
                pitcher_order = side.get("pitcher_order") or []
                if not pitcher_order:
                    continue
                starter_id = pitcher_order[0]
                starter = next(
                    (p for p in side.get("players", []) if p.get("player_id") == starter_id),
                    None,
                )
                if not starter or not starter.get("pitching") or "inningsPitched" not in starter["pitching"]:
                    continue
                innings = _baseball_innings(starter["pitching"]["inningsPitched"])
                earned_runs = float(starter["pitching"].get("earnedRuns") or 0)
                name = starter.get("name")
                if not name:
                    continue
                index.setdefault(_normalize_name(name), []).append((game_start, innings, earned_runs))
    for starts in index.values():
        starts.sort(key=lambda item: item[0])
    _STARTER_INDEX_CACHE[path] = index
    return index


def starter_rolling_era(
    starter_name: str,
    decision: datetime,
    *,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    lookback_starts: int = DEFAULT_LOOKBACK_STARTS,
    minimum_prior_starts: int = MINIMUM_PRIOR_STARTS,
) -> dict[str, Any]:
    """Rolling ERA from this pitcher's last N real starts strictly before decision.

    Fails closed (``status: "insufficient_sample"``/``"unavailable_from_source"``)
    rather than guessing -- caller decides how to treat that (raise, neutral
    default, or unavailable-features note), matching every other feature
    provider in this codebase.
    """
    index = load_starter_index(snapshot_path)
    starts = [s for s in index.get(_normalize_name(starter_name), []) if s[0] < decision]
    if not starts:
        return {"era": None, "starts": 0, "status": "unavailable_from_source"}
    recent = starts[-lookback_starts:]
    if len(recent) < minimum_prior_starts:
        return {"era": None, "starts": len(recent), "status": "insufficient_sample"}
    innings = sum(item[1] for item in recent)
    earned_runs = sum(item[2] for item in recent)
    if innings <= 0:
        return {"era": None, "starts": len(recent), "status": "insufficient_sample"}
    return {
        "era": round(9 * earned_runs / innings, 4),
        "starts": len(recent),
        "innings": round(innings, 2),
        "status": "available",
    }


def starter_era_gap_live(
    home_starter_name: str,
    away_starter_name: str,
    decision: datetime,
    *,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
) -> float:
    """home_starter's rolling ERA minus away_starter's -- same sign convention
    as validation.py's ``_load_starter_era_map`` (home_era - away_era).

    Raises ValueError when either starter's history is insufficient, so the
    caller can fail this game closed rather than serve a fabricated 0.0 --
    matching the module docstring's "never guess" rule and mirroring how
    ``probable_starter_era_gap``'s existing live provider already handles
    the same failure mode.
    """
    home = starter_rolling_era(home_starter_name, decision, snapshot_path=snapshot_path)
    away = starter_rolling_era(away_starter_name, decision, snapshot_path=snapshot_path)
    if home["status"] != "available" or away["status"] != "available":
        raise ValueError(
            "NO_CALL_STARTER_ERA_GAP_INSUFFICIENT_HISTORY: "
            f"home={home_starter_name!r} status={home['status']}, "
            f"away={away_starter_name!r} status={away['status']}"
        )
    return round(home["era"] - away["era"], 6)
