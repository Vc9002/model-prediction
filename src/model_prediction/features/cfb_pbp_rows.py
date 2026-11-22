"""Per-team-game play-by-play rows from cached ESPN summary payloads (NCAAF v7): the single row builder.

Moved from ``scripts/cfb_pbp_features.py`` (which now re-exports it) so the research build and the live
incremental build (``data_sources/cfb_pbp_live.py``) share one implementation; train/serve parity depends on it.

Input is a cached summary wrapper (``load_raw``); output is one row per (event, team) (``game_rows``):
offense metrics for the team and the same metrics for its opponent's offense (the team's defense),
each in an all-plays version and a garbage-time-filtered (``_ng``) version.

Definitions (fixed here so later walk-forward work does not drift):

* Scrimmage play: a type in ``SCRIMMAGE_TYPES`` with a start down in 1-4 and a positive distance
  (ESPN's goal-to-go distance 0 is replaced by ``yardsToEndzone``).
  Kickoffs, punts, field goals, PATs, two-point tries, timeouts, period markers and penalty
  no-plays (type "Penalty") are excluded. Unknown types are excluded and counted.
* Offense: ``start.team.id`` of the play, falling back to the drive's team (kickoff rows carry the
  kicking team there, which is why kickoffs are excluded before this matters).
* Success: 1st down gains >= 50% of distance, 2nd >= 70%, 3rd/4th >= 100% (integer arithmetic).
  An offensive touchdown is a success; a turnover never is.
* Explosive: a non-turnover scrimmage play gaining >= 20 yards (one threshold for runs and passes).
* Yards: ``statYardage``; interception plays count 0 offensive yards (``statYardage`` there is the
  return), fumbles lost keep the gain before the fumble.
* Turnovers: scrimmage plays flagged ``isTurnover`` (interceptions, fumbles lost) charged to the offense.
  ESPN sometimes types a lost fumble as a plain Rush without the flag, so this undercounts slightly;
  special-teams fumbles (counted in the box score) are deliberately excluded.
* Garbage time: pre-play |margin| > 38 in Q2, > 28 in Q3, > 22 in Q4; never in Q1 or overtime.
  ESPN's ``homeScore``/``awayScore`` are post-play, so the pre-play margin is the previous row's score.
* Drives: drives with >= 1 scrimmage play by the drive team. Points per drive = the drive team's own
  score change from before the drive's first row to after its last row. A drive is garbage when its
  first scrimmage play is. Seconds per play = summed drive ``timeElapsed`` / scrimmage plays.

Point-in-time: every row carries ``kickoff_utc`` from the event header. A row describes a finished
game, so a decision at time T may use it only when ``kickoff_utc`` is well before T (the game must
also have ended); never use the crawl's ``fetched_at_utc`` as the availability time of the game.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EXPLOSIVE_YARDS = 20  # one threshold for runs and passes; stated in the module docstring

# Lower-cased ESPN play type texts that are offensive scrimmage snaps. Built from the play-type census
# of the coverage sample (outputs/ncaaf/cfb_espn_pbp_coverage.json); anything else is excluded and
# counted in the run summary so a new type is visible instead of silently dropped.
SCRIMMAGE_TYPES = frozenset(
    {
        "rush",
        "rushing touchdown",
        "pass",
        "pass reception",
        "pass completion",
        "pass incompletion",
        "passing touchdown",
        "sack",
        "pass interception return",
        "pass interception",
        "interception",
        "interception return touchdown",
        "fumble recovery (own)",
        "fumble recovery (opponent)",
        "fumble return touchdown",
        "safety",
    }
)
INTERCEPTION_TYPES = frozenset(
    {"pass interception return", "pass interception", "interception", "interception return touchdown"}
)
# Types that are known and intentionally not scrimmage snaps (kept out of the "unknown" counter).
NON_SCRIMMAGE_TYPES = frozenset(
    {
        "kickoff",
        "kickoff return (offense)",
        "kickoff return (defense)",
        "kickoff return touchdown",
        "punt",
        "punt return",
        "punt return touchdown",
        "blocked punt",
        "blocked punt touchdown",
        "blocked field goal",
        "blocked field goal touchdown",
        "missed field goal return",
        "missed field goal return touchdown",
        "field goal good",
        "field goal missed",
        "extra point good",
        "extra point missed",
        "blocked pat",
        "two point rush",
        "two point pass",
        "two-point conversion",
        "defensive 2pt conversion",
        "penalty",
        "timeout",
        "end period",
        "end of half",
        "end of game",
        "end of regulation",
        "coin toss",
        "official timeout",
        "uncategorized",
    }
)

GARBAGE_MARGIN_BY_PERIOD = {2: 38, 3: 28, 4: 22}


def is_garbage_time(period: int | None, pre_play_margin: int) -> bool:
    """Garbage time: |pre-play margin| strictly above 38 (Q2), 28 (Q3), 22 (Q4); never Q1 or OT."""
    if period is None:
        return False
    threshold = GARBAGE_MARGIN_BY_PERIOD.get(period)
    return threshold is not None and abs(pre_play_margin) > threshold


def is_success(
    down: int, distance: int, yards: int, *, touchdown: bool = False, turnover: bool = False
) -> bool:
    """Standard CFB success: 1st down >= 50% of distance, 2nd >= 70%, 3rd/4th >= 100%."""
    if turnover:
        return False
    if touchdown:
        return True
    if down == 1:
        return 2 * yards >= distance
    if down == 2:
        return 10 * yards >= 7 * distance
    if down in (3, 4):
        return yards >= distance
    raise ValueError(f"down must be 1-4, got {down}")


def load_raw(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (metadata, payload) from a cached wrapper; verifies the body against its sha256."""
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        wrapper = json.load(fh)
    body: str = wrapper.pop("body")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if digest != wrapper.get("sha256"):
        raise ValueError(f"sha256 mismatch for {path}: {digest} != {wrapper.get('sha256')}")
    return wrapper, json.loads(body)


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def play_distance(start: dict[str, Any]) -> int | None:
    """Yards to gain. ESPN encodes goal-to-go as distance 0 (seen in 2016 rows), so fall back to
    yardsToEndzone there; without this every 1st-and-goal snap and most short TDs drop out."""
    distance = _int(start.get("distance"))
    if distance is not None and distance > 0:
        return distance
    to_goal = _int(start.get("yardsToEndzone"))
    return to_goal if to_goal is not None and to_goal > 0 else distance


def _clock_seconds(text: str | None) -> int | None:
    if not text or ":" not in text:
        return None
    minutes, _, seconds = text.partition(":")
    m, s = _int(minutes), _int(seconds)
    return None if m is None or s is None else m * 60 + s


@dataclass
class PlayRow:
    """One ESPN play row annotated with the pre-play score."""

    type_text: str
    offense_id: str | None
    period: int | None
    down: int | None
    distance: int | None
    yards: int
    is_turnover: bool
    pre_home: int
    pre_away: int
    post_home: int
    post_away: int
    drive_index: int

    @property
    def is_scrimmage(self) -> bool:
        return (
            self.type_text in SCRIMMAGE_TYPES
            and self.down in (1, 2, 3, 4)
            and self.distance is not None
            and self.distance > 0
            and self.offense_id is not None
        )


@dataclass
class DriveRow:
    team_id: str | None
    elapsed_s: int | None
    plays: list[PlayRow] = field(default_factory=list)


def iter_game(payload: dict[str, Any]) -> tuple[list[DriveRow], Counter[str]]:
    """Walk drives/plays in ESPN order, carrying the running score to give each play its pre-play score."""
    unknown: Counter[str] = Counter()
    drives: list[DriveRow] = []
    home_score = away_score = 0
    previous_key: tuple[Any, ...] | None = None
    for d_idx, drive in enumerate((payload.get("drives") or {}).get("previous") or []):
        drive_team = str((drive.get("team") or {}).get("id") or "") or None
        elapsed = _clock_seconds((drive.get("timeElapsed") or {}).get("displayValue"))
        row = DriveRow(team_id=drive_team, elapsed_s=elapsed)
        for play in drive.get("plays") or []:
            type_text = str((play.get("type") or {}).get("text") or "").strip().lower()
            start = play.get("start") or {}
            # ESPN occasionally repeats a row under a new id (seen on 2021-22 "Interception" rows):
            # same type, text, period, clock and down/distance back to back is one play.
            key = (
                type_text,
                play.get("text"),
                (play.get("period") or {}).get("number"),
                (play.get("clock") or {}).get("displayValue"),
                start.get("down"),
                start.get("distance"),
            )
            if key == previous_key:
                continue
            previous_key = key
            if type_text not in SCRIMMAGE_TYPES and type_text not in NON_SCRIMMAGE_TYPES:
                unknown[type_text] += 1
            offense = str((start.get("team") or {}).get("id") or "") or drive_team
            # Scores never go down in a game; a lower or missing reported score is a feed glitch
            # (e.g. a trailing 0-0 "End of Game" row), so the running score carries forward.
            post_home = max(home_score, _int(play.get("homeScore")) or 0)
            post_away = max(away_score, _int(play.get("awayScore")) or 0)
            type_is_int = type_text in INTERCEPTION_TYPES
            yards = _int(play.get("statYardage")) or 0
            row.plays.append(
                PlayRow(
                    type_text=type_text,
                    offense_id=offense,
                    period=_int((play.get("period") or {}).get("number")),
                    down=_int(start.get("down")),
                    distance=play_distance(start),
                    yards=0 if type_is_int else yards,
                    is_turnover=bool(play.get("isTurnover")) or type_is_int,
                    pre_home=home_score,
                    pre_away=away_score,
                    post_home=post_home,
                    post_away=post_away,
                    drive_index=d_idx,
                )
            )
            home_score, away_score = post_home, post_away
        drives.append(row)
    return drives, unknown


def _team_points(play: PlayRow, team_is_home: bool, *, post: bool) -> int:
    if team_is_home:
        return play.post_home if post else play.pre_home
    return play.post_away if post else play.pre_away


def _empty_stats() -> dict[str, float]:
    return {
        "plays": 0,
        "successes": 0,
        "yards": 0,
        "explosives": 0,
        "turnovers": 0,
        "drives": 0,
        "points": 0,
        "drive_seconds": 0,
        "drive_scrimmage_plays": 0,
    }


def _finish(stats: dict[str, float]) -> dict[str, float | None]:
    plays, drives = stats["plays"], stats["drives"]
    out: dict[str, float | None] = dict(stats)
    out["success_rate"] = stats["successes"] / plays if plays else None
    out["yards_per_play"] = stats["yards"] / plays if plays else None
    out["explosive_rate"] = stats["explosives"] / plays if plays else None
    out["points_per_drive"] = stats["points"] / drives if drives else None
    dsp = stats["drive_scrimmage_plays"]
    out["sec_per_play"] = stats["drive_seconds"] / dsp if dsp else None
    return out


def offense_stats(
    drives: list[DriveRow], team_id: str, team_is_home: bool
) -> dict[str, dict[str, float | None]]:
    """Offensive stats for ``team_id``: ``all`` and garbage-filtered ``ng`` versions."""
    acc = {"all": _empty_stats(), "ng": _empty_stats()}
    for drive in drives:
        for play in drive.plays:
            if not play.is_scrimmage or play.offense_id != team_id:
                continue
            assert play.down is not None and play.distance is not None
            margin = play.pre_home - play.pre_away
            garbage = is_garbage_time(play.period, margin)
            own_gain = _team_points(play, team_is_home, post=True) - _team_points(
                play, team_is_home, post=False
            )
            touchdown = own_gain >= 6 and not play.is_turnover
            success = is_success(
                play.down, play.distance, play.yards, touchdown=touchdown, turnover=play.is_turnover
            )
            explosive = not play.is_turnover and play.yards >= EXPLOSIVE_YARDS
            for key in ("all", "ng") if not garbage else ("all",):
                s = acc[key]
                s["plays"] += 1
                s["successes"] += int(success)
                s["yards"] += play.yards
                s["explosives"] += int(explosive)
                s["turnovers"] += int(play.is_turnover)
        if drive.team_id != team_id:
            continue
        scrimmage = [p for p in drive.plays if p.is_scrimmage and p.offense_id == team_id]
        if not scrimmage:
            continue
        first, last = drive.plays[0], drive.plays[-1]
        points = max(
            0, _team_points(last, team_is_home, post=True) - _team_points(first, team_is_home, post=False)
        )
        lead = scrimmage[0]
        garbage = is_garbage_time(lead.period, lead.pre_home - lead.pre_away)
        for key in ("all", "ng") if not garbage else ("all",):
            s = acc[key]
            s["drives"] += 1
            s["points"] += points
            if drive.elapsed_s is not None:
                s["drive_seconds"] += drive.elapsed_s
                s["drive_scrimmage_plays"] += len(scrimmage)
    return {key: _finish(value) for key, value in acc.items()}


def _competitors(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    comps = (payload.get("header") or {}).get("competitions") or []
    if not comps:
        return None
    comp = comps[0]
    sides = {c.get("homeAway"): c for c in comp.get("competitors") or []}
    if "home" not in sides or "away" not in sides:
        return None
    return comp, sides["home"], sides["away"]


def _box_stats(payload: dict[str, Any]) -> dict[str, dict[str, int | None]]:
    """Box-score totals per team id, for cross-checking the play-derived numbers."""
    out: dict[str, dict[str, int | None]] = {}
    for team in (payload.get("boxscore") or {}).get("teams") or []:
        tid = str((team.get("team") or {}).get("id") or "")
        stats = {s.get("name"): s.get("displayValue") for s in team.get("statistics") or []}
        out[tid] = {"total_yards": _int(stats.get("totalYards")), "turnovers": _int(stats.get("turnovers"))}
    return out


def _flatten(prefix: str, stats: dict[str, dict[str, float | None]]) -> dict[str, float | None]:
    row: dict[str, float | None] = {}
    for variant, values in stats.items():
        suffix = "" if variant == "all" else "_ng"
        for key, value in values.items():
            row[f"{prefix}_{key}{suffix}"] = value
    return row


def game_rows(meta: dict[str, Any], payload: dict[str, Any]) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Two team-game rows (home, away) for one event, or none when the payload has no usable plays."""
    parts = _competitors(payload)
    if parts is None:
        return [], Counter()
    comp, home, away = parts
    home_id, away_id = str(home.get("id")), str(away.get("id"))
    drives, unknown = iter_game(payload)
    if not any(p.is_scrimmage for d in drives for p in d.plays):
        return [], unknown
    box = _box_stats(payload)
    last = next((d.plays[-1] for d in reversed(drives) if d.plays), None)
    final_match = last is not None and (last.post_home, last.post_away) == (
        _int(home.get("score")),
        _int(away.get("score")),
    )
    stats = {
        home_id: offense_stats(drives, home_id, True),
        away_id: offense_stats(drives, away_id, False),
    }
    rows = []
    for team, opp, is_home in ((home, away, True), (away, home, False)):
        tid, oid = str(team.get("id")), str(opp.get("id"))
        row: dict[str, Any] = {
            "event_id": str(meta.get("event_id")),
            "season": meta.get("season"),
            "kickoff_utc": comp.get("date"),
            "game_class": meta.get("game_class"),
            "neutral_site": bool(comp.get("neutralSite")),
            "team_id": tid,
            "opponent_id": oid,
            "is_home": is_home,
            "points_for": _int(team.get("score")),
            "points_against": _int(opp.get("score")),
            "fetched_at_utc": meta.get("fetched_at_utc"),
            "raw_sha256": meta.get("sha256"),
            "box_total_yards": (box.get(tid) or {}).get("total_yards"),
            "box_turnovers": (box.get(tid) or {}).get("turnovers"),
            # False when the play feed ends short of the final (truncated OT, missing drives, score
            # glitches): ~5% of games in the coverage sample. Filter on it for rating fits.
            "pbp_final_score_match": final_match,
        }
        row.update(_flatten("off", stats[tid]))
        row.update(_flatten("def", stats[oid]))
        rows.append(row)
    return rows, unknown
