"""Live "as of decision time" feature computation for ``MLBFirstInningModel``.

``mlb_first_inning.py``'s ``build_first_inning_ledger`` only exists as an
offline batch walk-forward builder over *completed* snapshots (every row's
features come from games strictly before that row, but the row itself must
already have a final box score to know ``home_starter``/``away_starter`` by
Stats API player_id). Live serving only has team names, a venue name, and
ESPN's probable-starter *names* for a game that hasn't been played yet --
there is no ESPN athleteId -> Stats API player_id crosswalk in this codebase.

This module follows the same accepted-risk pattern ``features/starter_history.py``
already uses for exactly this problem: key entities by normalized name
instead of player_id, matched against the same snapshot file, and query "as
of" a given decision time directly (no offline ledger, no caching of
accumulator state across calls). The per-feature formulas here are
byte-for-byte the same as ``build_first_inning_ledger``'s -- see
``tests/test_mlb_first_inning_live.py``'s parity test, which recomputes a
real historical game's features through this module and asserts they match
the batch ledger's row for that same game exactly.

Real, if rare, risk carried over from ``starter_history.py``: two different
real starters sharing an identical normalized name would silently merge
histories. Not believed to have ever occurred in this dataset among starting
pitchers.
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Any

from .mlb_first_inning import (
    DEFAULT_SNAPSHOT_PATH,
    FEATURE_NAMES,
    MIN_STARTER_IP_FOR_FIP,
    PARK_PRIOR_GAMES,
    STARTER_PRIOR_STARTS,
    TEAM_PRIOR_GAMES,
    _load_raw_snapshots,
    _parse_ip,
    _shrink,
    _top3_composite,
    compute_first_inning_priors,
)


def _normalize_starter_name(name: str) -> str:
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(c.lower() for c in decomposed if c.isalnum() and not unicodedata.combining(c))


def _starter_name_for(players: list[dict[str, Any]], sid: int | None) -> str | None:
    if sid is None:
        return None
    player = next((p for p in players if p.get("player_id") == sid), None)
    name = player.get("name") if player else None
    return _normalize_starter_name(name) if name else None


def live_first_inning_features(
    home_team: str,
    away_team: str,
    venue_name: str,
    home_starter_name: str,
    away_starter_name: str,
    decision: datetime,
    *,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    priors: dict[str, float] | None = None,
) -> dict[str, float]:
    """The same 19 ``FEATURE_NAMES`` values ``build_first_inning_ledger`` would
    emit for a game at ``home_team`` vs ``away_team`` in ``venue_name``,
    starting at ``decision``, computed from every real (non-scheduled) prior
    snapshot strictly before ``decision``.

    Never fails on cold-start entities (unknown team/starter/venue) -- falls
    back to the league prior exactly like the batch builder does; this is
    what the frozen model was trained to expect.
    """
    if priors is None:
        priors = compute_first_inning_priors(snapshot_path, end_utc=decision)

    snaps = [s for s in _load_raw_snapshots(snapshot_path) if s["start"] < decision]

    starters: dict[
        str, list[float]
    ] = {}  # norm starter name -> [n, sum_opp_1st, sum_ip, sum_so, sum_bb, sum_bf, sum_hr]
    team_home: dict[str, list[float]] = {}
    team_away: dict[str, list[float]] = {}
    venues: dict[str, list[float]] = {}
    batters: dict[int, list[float]] = {}
    team_pool: dict[str, list[tuple[datetime, dict[int, float]]]] = {}
    starter_last_date: dict[str, datetime] = {}

    for snap in snaps:
        h_team = snap["home_team"]
        a_team = snap["away_team"]
        ven = snap["venue"]
        away_sid = snap["away_starter"]
        home_sid = snap["home_starter"]

        away_name = _starter_name_for(snap["players"], away_sid)
        home_name = _starter_name_for(snap["players"], home_sid)

        for sid_name, opp_runs_key in (
            (away_name, "runs_1st_home"),
            (home_name, "runs_1st_away"),
        ):
            if sid_name is None:
                continue
            st = starters.setdefault(sid_name, [0.0] * 7)
            player = next(
                (
                    p
                    for p in snap["players"]
                    if p.get("name") and _normalize_starter_name(p["name"]) == sid_name
                ),
                None,
            )
            pitching = (player or {}).get("pitching") or {}
            ip = _parse_ip(pitching.get("inningsPitched"))
            # Only accumulate real box-score data (skip cold/empty entries).
            if not pitching:
                continue
            st[0] += 1.0
            st[1] += snap[opp_runs_key]
            st[2] += ip
            st[3] += float(pitching.get("strikeOuts") or 0.0)
            st[4] += float(pitching.get("baseOnBalls") or 0.0)
            st[5] += float(pitching.get("battersFaced") or 0.0)
            st[6] += float(pitching.get("homeRuns") or 0.0)
            starter_last_date[sid_name] = snap["start"]

        th = team_home.setdefault(h_team, [0.0] * 3)
        th[0] += 1.0
        th[1] += snap["runs_1st_home"]
        th[2] += snap["runs_1st_away"]
        ta = team_away.setdefault(a_team, [0.0] * 3)
        ta[0] += 1.0
        ta[1] += snap["runs_1st_away"]
        ta[2] += snap["runs_1st_home"]

        v = venues.setdefault(ven, [0.0] * 2)
        v[0] += 1.0
        v[1] += snap["runs_1st_away"] + snap["runs_1st_home"]

        participants: dict[int, float] = {}
        for player in snap["players"]:
            batting = player.get("batting") or {}
            pa = float(batting.get("plateAppearances") or 0.0)
            pid = player.get("player_id")
            if pid is None or pa <= 0:
                continue
            pid_i = int(pid)
            hits = float(batting.get("hits") or 0.0)
            walks = float(batting.get("baseOnBalls") or 0.0)
            hbp = float(batting.get("hitByPitch") or 0.0)
            so = float(batting.get("strikeOuts") or 0.0)
            tb = float(batting.get("totalBases") or hits)
            b = batters.setdefault(pid_i, [0.0] * 4)
            b[0] += pa
            b[1] += hits + walks + hbp
            b[2] += walks - so
            b[3] += tb - hits
            participants[pid_i] = pa
        for team in (h_team, a_team):
            pool = team_pool.setdefault(team, [])
            pool.append((snap["start"], participants))

    away_name_n = _normalize_starter_name(away_starter_name) if away_starter_name else None
    home_name_n = _normalize_starter_name(home_starter_name) if home_starter_name else None
    away_st = starters.get(away_name_n) if away_name_n else None
    home_st = starters.get(home_name_n) if home_name_n else None
    away_team_away = team_away.get(away_team, [0.0] * 3)
    home_team_home = team_home.get(home_team, [0.0] * 3)
    away_team_home_d = team_home.get(away_team, [0.0] * 3)
    home_team_away_d = team_away.get(home_team, [0.0] * 3)
    ven = venues.get(venue_name, [0.0] * 2)

    away_scored_away = (
        _shrink(
            away_team_away[1] / away_team_away[0],
            int(away_team_away[0]),
            TEAM_PRIOR_GAMES,
            priors["half_away"],
        )
        if away_team_away[0] > 0
        else priors["half_away"]
    )
    home_scored_home = (
        _shrink(
            home_team_home[1] / home_team_home[0],
            int(home_team_home[0]),
            TEAM_PRIOR_GAMES,
            priors["half_home"],
        )
        if home_team_home[0] > 0
        else priors["half_home"]
    )
    away_allowed_away = (
        _shrink(
            away_team_home_d[2] / away_team_home_d[0],
            int(away_team_home_d[0]),
            TEAM_PRIOR_GAMES,
            priors["half_home"],
        )
        if away_team_home_d[0] > 0
        else priors["half_home"]
    )
    home_allowed_home = (
        _shrink(
            home_team_away_d[2] / home_team_away_d[0],
            int(home_team_away_d[0]),
            TEAM_PRIOR_GAMES,
            priors["half_away"],
        )
        if home_team_away_d[0] > 0
        else priors["half_away"]
    )
    park_1st = (
        _shrink(ven[1] / ven[0], int(ven[0]), PARK_PRIOR_GAMES, priors["total"])
        if ven[0] > 0
        else priors["total"]
    )

    away_st_opp_1st = (
        _shrink(away_st[1] / away_st[0], int(away_st[0]), STARTER_PRIOR_STARTS, priors["half_away"])
        if away_st and away_st[0] > 0
        else priors["half_away"]
    )
    home_st_opp_1st = (
        _shrink(home_st[1] / home_st[0], int(home_st[0]), STARTER_PRIOR_STARTS, priors["half_home"])
        if home_st and home_st[0] > 0
        else priors["half_home"]
    )

    def _starter_rates(st: list[float] | None) -> tuple[float, float, float]:
        if st is None or st[0] <= 0 or st[2] <= MIN_STARTER_IP_FOR_FIP:
            return priors["fip"], priors["k_pct"], priors["bb_pct"]
        ip, so, bb, bf, hr = st[2], st[3], st[4], st[5], st[6]
        fip = (13.0 * hr + 3.0 * bb - 2.0 * so) / max(1.0, ip) + 3.10
        k_pct = so / bf if bf > 0 else so / max(1.0, 3.0 * ip)
        bb_pct = bb / bf if bf > 0 else bb / max(1.0, 3.0 * ip)
        return fip, k_pct, bb_pct

    away_fip, away_k, away_bb = _starter_rates(away_st)
    home_fip, home_k, home_bb = _starter_rates(home_st)

    away_top3 = _top3_composite(away_team, batters, team_pool.get(away_team, []), priors)
    home_top3 = _top3_composite(home_team, batters, team_pool.get(home_team, []), priors)

    def _days_rest(last_date: datetime | None, default: float) -> float:
        if last_date is None:
            return default
        days = round((decision - last_date).total_seconds() / 86400.0)
        return min(6.0, max(0.0, float(days)))

    away_st_rest = _days_rest(starter_last_date.get(away_name_n) if away_name_n else None, 4.0)
    home_st_rest = _days_rest(starter_last_date.get(home_name_n) if home_name_n else None, 4.0)

    return {
        "away_starter_opp_1st_runs": round(away_st_opp_1st, 4),
        "home_starter_opp_1st_runs": round(home_st_opp_1st, 4),
        "away_team_1st_scored_away": round(away_scored_away, 4),
        "home_team_1st_scored_home": round(home_scored_home, 4),
        "away_team_1st_allowed_away": round(away_allowed_away, 4),
        "home_team_1st_allowed_home": round(home_allowed_home, 4),
        "park_1st_runs": round(park_1st, 4),
        "away_starter_fip": round(away_fip, 3),
        "home_starter_fip": round(home_fip, 3),
        "away_starter_k_pct": round(away_k, 4),
        "home_starter_k_pct": round(home_k, 4),
        "away_starter_bb_pct": round(away_bb, 4),
        "home_starter_bb_pct": round(home_bb, 4),
        "away_top3_composite": round(away_top3, 5),
        "home_top3_composite": round(home_top3, 5),
        "away_starter_starts": round(math.log1p(away_st[0] if away_st else 0.0), 4),
        "home_starter_starts": round(math.log1p(home_st[0] if home_st else 0.0), 4),
        "away_starter_days_rest": round(away_st_rest, 2),
        "home_starter_days_rest": round(home_st_rest, 2),
    }


assert set(FEATURE_NAMES) == {
    "away_starter_opp_1st_runs",
    "home_starter_opp_1st_runs",
    "away_team_1st_scored_away",
    "home_team_1st_scored_home",
    "away_team_1st_allowed_away",
    "home_team_1st_allowed_home",
    "park_1st_runs",
    "away_starter_fip",
    "home_starter_fip",
    "away_starter_k_pct",
    "home_starter_k_pct",
    "away_starter_bb_pct",
    "home_starter_bb_pct",
    "away_top3_composite",
    "home_top3_composite",
    "away_starter_starts",
    "home_starter_starts",
    "away_starter_days_rest",
    "home_starter_days_rest",
}, "live_first_inning_features must stay in exact sync with FEATURE_NAMES"
