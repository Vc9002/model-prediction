"""MLB point-in-time batter offense priors (v9 Phase 3 -- ``projected_offense_pit``).

Section 8 of ``docs/MODEL_IMPROVEMENTS.md`` (corrected v9 plan, 2026-08-19):
decompose team offense into PIT player talent priors instead of a team-level
proxy. This is deliberately narrow -- three components (production,
discipline, power), each a per-PA rate, credibility-shrunk toward an
empirical league rate by sample size (same rationale as ``bullpen.py``'s
``BULLPEN_PRIOR_INNINGS`` shrinkage and ``park_factors_pit.py``'s
``prior_strength`` shrinkage). Not 15 hitter stats searched individually.

``projected_offense_pit`` is NOT a confirmed lineup -- it derives player
participation weights entirely from games PRECEDING the decision time
(expected PA share, summed over the team's recent player pool). Using a
final boxscore's actual batting order in history would be retrospective
lineup leakage; that is `confirmed_lineup_offense_pit`'s job once the
prospective ``data/point_in_time/mlb_lineups.jsonl`` archive (capturing
live since 2026-08-18) is deep enough -- a separate, later feature.

Uses the ``mlb_statsapi.py`` game snapshots (real per-batter boxscore
lines). When those are missing this reports ``unavailable_from_source``,
never a fabricated neutral dressed up as data.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_SNAPSHOT_PATH = Path("data/mlb_statsapi/game_snapshots.jsonl")
DEFAULT_TEAM_LOOKBACK_GAMES = 10

# Sabermetric stabilization points (Carleton 2016-style rules of thumb) used
# as credibility-shrinkage prior masses -- fixed constants, not tuned
# against any holdout, same rationale as bullpen.py's BULLPEN_PRIOR_INNINGS.
PRODUCTION_PRIOR_PA = 200.0  # on-base rate stabilizes slower than discipline
DISCIPLINE_PRIOR_PA = 120.0  # BB-rate minus K-rate stabilizes fastest
POWER_PRIOR_PA = 160.0  # isolated-power-style rate

# League empirical rates are computed once from the full snapshot dataset
# (module-level cache) rather than expanding point-in-time per game -- the
# same simplification bullpen.py makes with its hardcoded LEAGUE_RELIEF_ERA
# constant. A handful of early-2024 games see a very slightly forward-
# looking league average; this is a fixed baseline, not a per-game leak into
# any single player's own shrinkage target.
_LEAGUE_RATES_CACHE: dict[Path, dict[str, float]] = {}
_PLAYER_INDEX_CACHE: dict[Path, dict[int, list[tuple[datetime, str, dict[str, float]]]]] = {}
_TEAM_GAME_INDEX_CACHE: dict[Path, dict[str, list[tuple[datetime, dict[int, float]]]]] = {}


def _parse_snapshot_time(value: str) -> datetime:
    from ..domain import parse_utc

    return parse_utc(value)


def _component_stats(batting: dict[str, float]) -> dict[str, float] | None:
    pa = float(batting.get("plateAppearances") or 0.0)
    if pa <= 0:
        return None
    hits = float(batting.get("hits") or 0.0)
    walks = float(batting.get("baseOnBalls") or 0.0)
    hbp = float(batting.get("hitByPitch") or 0.0)
    strikeouts = float(batting.get("strikeOuts") or 0.0)
    total_bases = float(batting.get("totalBases") or hits)
    return {
        "pa": pa,
        "production": hits + walks + hbp,
        "discipline": walks - strikeouts,
        "power": total_bases - hits,
    }


def _load_indexes(
    snapshot_path: str | Path,
) -> tuple[
    dict[int, list[tuple[datetime, str, dict[str, float]]]],
    dict[str, list[tuple[datetime, dict[int, float]]]],
]:
    """Build (per-player chronological batting lines, per-team chronological
    game participant PA) indexes from real completed-game boxscore snapshots.
    Cached by path -- mirrors ``bullpen.py``'s ``load_relief_appearance_index``.
    """
    path = Path(snapshot_path)
    if path in _PLAYER_INDEX_CACHE:
        return _PLAYER_INDEX_CACHE[path], _TEAM_GAME_INDEX_CACHE[path]

    by_player: dict[int, list[tuple[datetime, str, dict[str, float]]]] = {}
    by_team_game: dict[str, list[tuple[datetime, dict[int, float]]]] = {}

    if not path.exists():
        _PLAYER_INDEX_CACHE[path] = by_player
        _TEAM_GAME_INDEX_CACHE[path] = by_team_game
        return by_player, by_team_game

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
                participants: dict[int, float] = {}
                for player in side.get("players", []):
                    stats = _component_stats(player.get("batting") or {})
                    if stats is None:
                        continue
                    player_id = player.get("player_id")
                    if player_id is None:
                        continue
                    by_player.setdefault(int(player_id), []).append((game_start, team_name, stats))
                    participants[int(player_id)] = stats["pa"]
                if participants:
                    by_team_game.setdefault(team_name, []).append((game_start, participants))

    for player_games in by_player.values():
        player_games.sort(key=lambda item: item[0])
    for team_games in by_team_game.values():
        team_games.sort(key=lambda item: item[0])

    _PLAYER_INDEX_CACHE[path] = by_player
    _TEAM_GAME_INDEX_CACHE[path] = by_team_game
    return by_player, by_team_game


def _league_rates(snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH) -> dict[str, float]:
    path = Path(snapshot_path)
    if path in _LEAGUE_RATES_CACHE:
        return _LEAGUE_RATES_CACHE[path]
    by_player, _ = _load_indexes(path)
    total_pa = total_production = total_discipline = total_power = 0.0
    for lines in by_player.values():
        for _, _, stats in lines:
            total_pa += stats["pa"]
            total_production += stats["production"]
            total_discipline += stats["discipline"]
            total_power += stats["power"]
    rates = (
        {
            "production": total_production / total_pa,
            "discipline": total_discipline / total_pa,
            "power": total_power / total_pa,
        }
        if total_pa > 0
        else {"production": 0.32, "discipline": -0.10, "power": 0.16}
    )
    _LEAGUE_RATES_CACHE[path] = rates
    return rates


def player_shrunk_rates(
    player_id: int,
    decision: datetime,
    *,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
) -> dict[str, float]:
    """Credibility-shrunk per-PA rates for one player using only games
    strictly before *decision* -- the target game's own line never updates
    its own prior. Players with no prior PAs get the pure league prior
    (credibility 0)."""
    by_player, _ = _load_indexes(snapshot_path)
    league = _league_rates(snapshot_path)
    lines = by_player.get(player_id, [])
    pa = production = discipline = power = 0.0
    for game_start, _, stats in lines:
        if game_start >= decision:
            break
        pa += stats["pa"]
        production += stats["production"]
        discipline += stats["discipline"]
        power += stats["power"]

    def _shrink(raw_sum: float, prior_pa: float, league_rate: float) -> float:
        if pa <= 0:
            return league_rate
        credibility = pa / (pa + prior_pa)
        return credibility * (raw_sum / pa) + (1.0 - credibility) * league_rate

    return {
        "pa": pa,
        "production": _shrink(production, PRODUCTION_PRIOR_PA, league["production"]),
        "discipline": _shrink(discipline, DISCIPLINE_PRIOR_PA, league["discipline"]),
        "power": _shrink(power, POWER_PRIOR_PA, league["power"]),
    }


def team_offense_pit_profile(
    team_name: str,
    decision: datetime,
    *,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    lookback_games: int = DEFAULT_TEAM_LOOKBACK_GAMES,
) -> dict[str, Any]:
    """PA-share-weighted, credibility-shrunk offense composite for one team.

    Participation weights come from the team's last *lookback_games*
    completed games before *decision* (its "recent player pool"); each
    pool member's own component rates are their career-to-date shrunk
    priors as of *decision* -- both windows strictly precede the decision
    time, so this is safe to compute historically without lineup leakage.
    """
    _, by_team_game = _load_indexes(snapshot_path)
    games = [game for game in by_team_game.get(team_name, []) if game[0] < decision]
    recent = games[-lookback_games:]
    if not recent:
        return {
            "production": None,
            "discipline": None,
            "power": None,
            "composite": None,
            "status": "unavailable_from_source",
        }

    recent_pa: dict[int, float] = {}
    for _, participants in recent:
        for player_id, pa in participants.items():
            recent_pa[player_id] = recent_pa.get(player_id, 0.0) + pa
    total_pa = sum(recent_pa.values())
    if total_pa <= 0:
        return {
            "production": None,
            "discipline": None,
            "power": None,
            "composite": None,
            "status": "insufficient_sample",
        }

    production = discipline = power = 0.0
    for player_id, pa in recent_pa.items():
        weight = pa / total_pa
        rates = player_shrunk_rates(player_id, decision, snapshot_path=snapshot_path)
        production += weight * rates["production"]
        discipline += weight * rates["discipline"]
        power += weight * rates["power"]

    composite = (production + discipline + power) / 3.0
    return {
        "production": round(production, 6),
        "discipline": round(discipline, 6),
        "power": round(power, 6),
        "composite": round(composite, 6),
        "status": "available",
    }


def matchup_offense_pit_gap(
    home_team: str,
    away_team: str,
    decision: datetime,
    *,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    lookback_games: int = DEFAULT_TEAM_LOOKBACK_GAMES,
) -> tuple[float, bool]:
    """(home composite - away composite, available) for ``offense_pit_gap``.

    Returns ``(0.0, False)`` when either side lacks enough recent history --
    mirrors ``_bullpen_weakness_gap``'s ``(value, available)`` shape."""
    home = team_offense_pit_profile(
        home_team, decision, snapshot_path=snapshot_path, lookback_games=lookback_games
    )
    away = team_offense_pit_profile(
        away_team, decision, snapshot_path=snapshot_path, lookback_games=lookback_games
    )
    if home["status"] != "available" or away["status"] != "available":
        return 0.0, False
    return round(home["composite"] - away["composite"], 6), True
