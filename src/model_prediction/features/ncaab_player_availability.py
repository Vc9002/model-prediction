"""NCAAB player availability from prior-game box scores (candidate P1, SHADOW ONLY).

ESPN publishes no NCAAB injury data, so the only live signal is who played. For each team, as of a
game's tip, this computes the two inputs of research candidate P1 (quick 261121-hmc, protocol 261120
addendum v4/v5):

- ``avail_top7_prev1``: the minute-weighted share of the team's season-to-date top-7 rotation that played
  in its previous game;
- ``top7_minutes_share_out_3plus``: the minute-weighted share of that rotation that has missed 3+ straight
  games;

and P1's margin adjustment ``b1 * d(avail) + b2 * d(out3)`` (home minus away, points of home margin)
with the coefficients pinned before any confirmation data was read (commit 411f2a19,
``outputs/ncaab/research_261120/P1_player_availability/confirmation_coefficients.json``). The definitions
are copies of ``scripts/ncaab_research/player_features_261121.py`` (``src`` must not import ``scripts``);
``tests/test_ncaab_player_availability.py`` pins equality on real 2026-27 boxes.

Point in time: a prior game counts only if it started strictly before the tip (full timestamps) and its
box was observed (``observed_at_utc``) no later than the decision time. Only the tip's season counts:
last season's minutes would put departed players in the rotation.

Fail closed, per team (the matchup adjustment is None when either side fails):

- ``NO_CALL_AVAILABILITY_INSUFFICIENT_HISTORY``: fewer than 3 usable prior boxes this season;
- ``NO_CALL_AVAILABILITY_BOX_QUALITY``: the previous game's box was captured but fails the research gate
  (no player list, points or minutes that do not add up, a player with minutes but no id, the opponent's
  list copied onto this side, or a team name that matches neither side);
- ``NO_CALL_AVAILABILITY_STALE``: the previous game is final but its box is not captured yet, and it is
  still inside the capture window (the daily job captures yesterday's games, so a fix is expected); or a
  game that tipped after the last final is still in progress (``PlayerBoxIndex.in_progress``, fed from the
  scoreboard), so the last final is not really the previous game;
- ``NO_CALL_AVAILABILITY_PREV_GAME_BOX_MISSING``: the previous game is past the capture window with no box;
- ``NO_CALL_AVAILABILITY_BOX_COVERAGE``: the usable boxes cover clearly fewer than the team's prior
  same-season games: more than ``MAX_MISSING_BOXES`` (1) prior games without a usable box, or fewer than
  ``MIN_BOX_COVERAGE`` (80%) of them with one. A prior game with no usable box is silently absent from the
  rotation and from the absence streaks, so a tree that starts empty at deploy would report "ok" from
  three post-deploy games and never show a long-term absentee. One miss is tolerated (a single ESPN
  failure or a box that fails the quality gate is routine and moves a seven-man rotation little); two
  are not, because they are what a missing early season looks like. The tree must be backfilled over
  the season (``cli ncaab-player-box-backfill``) before this stops firing.

Inert in serving: ``learned_forward._compute_features`` computes it only for an artifact whose
``feature_names`` lists a name in ``FEATURE_NAMES`` (none does), and ``college_basketball_forward`` records
the adjustment as shadow fields on v7 rows without touching any served number. Promotion is a separate
decision with its own confirmation.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..domain import EASTERN, parse_utc

FEATURE_NAMES = frozenset(
    {
        "ncaab_p1_avail_top7_prev1_diff",
        "ncaab_p1_top7_out3plus_share_diff",
        "ncaab_p1_margin_adjustment",
    }
)

# Pinned (commit 411f2a19): one OLS fit, no intercept, on all development rows 2023-2026.
P1_COEF_AVAIL = 5.804739185553095
P1_COEF_OUT3 = -2.683617378023771
P1_COEF_SOURCE = (
    "outputs/ncaab/research_261120/P1_player_availability/confirmation_coefficients.json@411f2a19"
)

TOP_ROTATION = 7
MIN_BOX_GAMES = 3
LONG_ABSENCE_GAMES = 3
MINUTES_TOLERANCE = 5  # team minutes may differ from 200 (+25 per OT) by rounding; more means a broken box
# Days back (Eastern) the daily job still captures: it fetches yesterday's finals on every run.
CAPTURE_WINDOW_DAYS = 1
# Coverage gate: a team fails closed when more than this many prior games lack a usable box, or when under
# this share of them have one (the ratio only bites on a 3-4 game history; the count bites everywhere).
MAX_MISSING_BOXES = 1
MIN_BOX_COVERAGE = 0.8

INSUFFICIENT_HISTORY = "NO_CALL_AVAILABILITY_INSUFFICIENT_HISTORY"
PREV_GAME_BOX_MISSING = "NO_CALL_AVAILABILITY_PREV_GAME_BOX_MISSING"
BOX_QUALITY = "NO_CALL_AVAILABILITY_BOX_QUALITY"
STALE = "NO_CALL_AVAILABILITY_STALE"
BOX_COVERAGE = "NO_CALL_AVAILABILITY_BOX_COVERAGE"


def ncaab_season_year(start: datetime) -> int:
    """ESPN's NCAAB season year: a November 2026 tip belongs to 2027 (the 2026-27 season)."""
    day = start.astimezone(EASTERN).date()
    return day.year + 1 if day.month >= 7 else day.year


def box_quality(team: dict[str, Any], opponent: dict[str, Any] | None = None) -> str | None:
    """None when the team's player box is complete; else why it cannot be trusted.

    Copy of the research gate: ESPN serves some boxes with real players listed as did-not-play, without
    stats and often without an athlete id. Using such a box would mark those players absent.
    """
    players = team.get("players") or []
    if not team.get("player_box_available") or not players:
        return "no_player_box"
    score = team.get("score_header")
    if score is None:
        score = team.get("score_scoreboard")
    if score is None or sum(p.get("points") or 0 for p in players) != int(score):
        return "points_sum_mismatch"
    expected = 200 + 25 * max((team.get("periods") or 2) - 2, 0)
    if abs(sum(p.get("minutes") or 0 for p in players) - expected) > MINUTES_TOLERANCE:
        return "minutes_sum_mismatch"
    if any(p.get("athlete_id") is None and (p.get("minutes") or 0) > 0 for p in players):
        return "played_without_athlete_id"
    if opponent is not None:
        # ESPN sometimes copies one team's player list onto the other side; which side is real cannot be
        # told from the box alone, so both sides fail.
        own = {p.get("athlete_id") for p in players if p.get("athlete_id") is not None}
        other = {
            p.get("athlete_id") for p in opponent.get("players") or [] if p.get("athlete_id") is not None
        }
        if own & other:
            return "players_shared_with_opponent"
    return None


@dataclass(frozen=True)
class TeamBox:
    """One team's side of one captured game."""

    event_id: str
    start: datetime
    quality: str | None
    played: dict[str, float]  # athlete id -> minutes, athletes with minutes > 0 only


class PlayerBoxIndex:
    """Captured player boxes of one season, as known at ``as_of`` (observed_at_utc <= as_of)."""

    def __init__(self, records: Iterable[dict[str, Any]], as_of: datetime, season_year: int) -> None:
        self.as_of = as_of
        self.season_year = season_year
        self.by_event: dict[str, dict[str, TeamBox]] = {}
        self.by_team: dict[str, list[TeamBox]] = defaultdict(list)
        # team name -> tip times of its games in progress right now (``in_progress_starts``); empty means
        # unknown, which can only make the "previous game" look older, so the caller sets it when it can.
        self.in_progress: dict[str, list[datetime]] = {}
        for record in records:
            self.add(record)

    def add(self, record: dict[str, Any]) -> None:
        """Reduce one record to its two team sides; a record the decision could not have seen is ignored."""
        if parse_utc(str(record["observed_at_utc"])) > self.as_of:
            return
        start = parse_utc(str(record["event_start_utc"]))
        if ncaab_season_year(start) != self.season_year:
            return
        sides: dict[str, TeamBox] = {}
        for side, other in (("home", "away"), ("away", "home")):
            team = record[side]
            played = {
                str(p["athlete_id"]): float(p.get("minutes") or 0)
                for p in team.get("players") or []
                if p.get("athlete_id") is not None and float(p.get("minutes") or 0) > 0
            }
            sides[str(team["team_name"])] = TeamBox(
                str(record["event_id"]), start, box_quality(team, record[other]), played
            )
        self.by_event[str(record["event_id"])] = sides
        for name, box in sides.items():
            self.by_team[name].append(box)

    @classmethod
    def load(cls, root: str | Path, as_of: datetime, season_year: int) -> PlayerBoxIndex:
        """Read the season's ``<root>/<scoreboard date>/<event_id>.json`` files, one at a time.

        Each file is reduced as it is read (a season of raw records is hundreds of MB by March), and an
        unreadable or malformed file is skipped: it counts as not captured, so its teams fail closed.
        """
        index = cls((), as_of, season_year)
        first_day = f"{season_year - 1}-07-01"
        last_day = (as_of.astimezone(EASTERN).date() + timedelta(days=1)).isoformat()
        base = Path(root)
        if base.exists():
            for folder in sorted(base.iterdir()):
                if not folder.is_dir() or not first_day <= folder.name <= last_day:
                    continue
                for path in sorted(folder.glob("*.json")):
                    try:
                        index.add(json.loads(path.read_text(encoding="utf-8")))
                    except (OSError, ValueError, KeyError, TypeError, AttributeError):
                        continue
        return index


def in_progress_starts(board: dict[str, Any]) -> dict[str, list[datetime]]:
    """Team display name -> tip times of its in-progress games on an ESPN scoreboard.

    Only state "in": a postponed or canceled game is not final either, but it is not a game that just
    finished and will produce a box, so it must not hold the previous-game check open.
    """
    out: dict[str, list[datetime]] = defaultdict(list)
    for event in (board or {}).get("events") or []:
        competition = (event.get("competitions") or [{}])[0]
        if (competition.get("status") or {}).get("type", {}).get("state") != "in" or not event.get("date"):
            continue
        start = parse_utc(str(event["date"]))
        for side in competition.get("competitors") or []:
            name = (side.get("team") or {}).get("displayName")
            if name:
                out[str(name)].append(start)
    return dict(out)


@dataclass(frozen=True)
class TeamAvailability:
    team: str
    reason: str | None  # one of the NO_CALL_AVAILABILITY_* codes, None when the inputs are usable
    avail_top7_prev1: float | None
    top7_minutes_share_out_3plus: float | None
    usable_boxes: int
    prior_games: int
    prev_event_id: str | None
    detail: str | None = None  # box-quality reason behind BOX_QUALITY


def team_availability(
    team: str, tip: datetime, history: list[Any], index: PlayerBoxIndex
) -> TeamAvailability:
    """P1 inputs for ``team`` as of ``tip``; ``history`` is the forecast's completed-game list.

    The previous game is the latest final before the tip in either the score history or the box tree,
    so a box captured before score ingestion caught up still counts as the previous game.
    """
    season = index.season_year
    starts: dict[str, datetime] = {
        g.event_id: g.start
        for g in history
        if team in (g.home_team, g.away_team) and g.start < tip and ncaab_season_year(g.start) == season
    }
    for box in index.by_team.get(team, []):
        if box.start < tip:
            starts.setdefault(box.event_id, box.start)
    scheduled = sorted(starts, key=lambda event_id: (starts[event_id], event_id))
    boxes = {event_id: index.by_event.get(event_id) for event_id in scheduled}
    usable = [
        sides[team]
        for event_id, sides in boxes.items()
        if sides is not None and team in sides and sides[team].quality is None
    ]

    def fail(reason: str, detail: str | None = None) -> TeamAvailability:
        return TeamAvailability(
            team,
            reason,
            None,
            None,
            len(usable),
            len(scheduled),
            scheduled[-1] if scheduled else None,
            detail,
        )

    if len(usable) < MIN_BOX_GAMES:
        return fail(INSUFFICIENT_HISTORY)
    last_final = starts[scheduled[-1]]
    if any(last_final < live < tip for live in index.in_progress.get(team, ())):
        # The latest final is not the previous game: a later one is still being played, and its box (and
        # the players who sat in it) is not knowable yet.
        return fail(STALE, "previous_game_in_progress")
    prev_sides = boxes[scheduled[-1]]
    if prev_sides is None:
        prev_day = starts[scheduled[-1]].astimezone(EASTERN).date()
        window_start: date = index.as_of.astimezone(EASTERN).date() - timedelta(days=CAPTURE_WINDOW_DAYS)
        return fail(STALE if prev_day >= window_start else PREV_GAME_BOX_MISSING)
    if team not in prev_sides:
        return fail(BOX_QUALITY, "team_name_matches_neither_side")
    prev = prev_sides[team]
    if prev.quality is not None:
        return fail(BOX_QUALITY, prev.quality)

    season_minutes: dict[str, float] = defaultdict(float)
    for box in usable:
        for athlete, minutes in box.played.items():
            season_minutes[athlete] += minutes
    top = sorted(season_minutes.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_ROTATION]
    top_total = sum(m for _, m in top)
    if top_total <= 0:
        return fail(INSUFFICIENT_HISTORY)
    missing = len(scheduled) - len(usable)
    if missing > MAX_MISSING_BOXES or len(usable) / len(scheduled) < MIN_BOX_COVERAGE:
        return fail(BOX_COVERAGE, f"usable_{len(usable)}_of_{len(scheduled)}_prior_games")
    streaks = {}
    for athlete, _ in top:
        streak = 0
        for box in reversed(usable):
            if athlete in box.played:
                break
            streak += 1
        streaks[athlete] = streak
    return TeamAvailability(
        team,
        None,
        sum(m for a, m in top if a in prev.played) / top_total,
        sum(m for a, m in top if streaks[a] >= LONG_ABSENCE_GAMES) / top_total,
        len(usable),
        len(scheduled),
        prev.event_id,
    )


@dataclass(frozen=True)
class P1Adjustment:
    """P1 shift of the home margin, or None (reason set) when either side fails closed."""

    adjustment: float | None
    reason: str | None
    home: TeamAvailability
    away: TeamAvailability

    def shadow_inputs(self) -> dict[str, Any]:
        """Flat scalars for a pick payload (the snapshot flattens one level and drops None)."""
        out: dict[str, Any] = {"coef_avail": P1_COEF_AVAIL, "coef_out3": P1_COEF_OUT3}
        for side, team in (("home", self.home), ("away", self.away)):
            out[f"{side}_status"] = team.reason or "ok"
            out[f"{side}_avail_top7_prev1"] = team.avail_top7_prev1
            out[f"{side}_top7_out3plus_share"] = team.top7_minutes_share_out_3plus
            out[f"{side}_usable_boxes"] = team.usable_boxes
            out[f"{side}_prior_games"] = team.prior_games
            out[f"{side}_missing_boxes"] = team.prior_games - team.usable_boxes
            out[f"{side}_box_coverage"] = (
                round(team.usable_boxes / team.prior_games, 4) if team.prior_games else None
            )
            out[f"{side}_prev_event_id"] = team.prev_event_id
            out[f"{side}_detail"] = team.detail
        return out


def p1_margin_adjustment(
    home_team: str, away_team: str, tip: datetime, history: list[Any], index: PlayerBoxIndex
) -> P1Adjustment:
    if ncaab_season_year(tip) != index.season_year:
        raise ValueError("NO_CALL_AVAILABILITY_SEASON_MISMATCH: box index is for another season")
    home = team_availability(home_team, tip, history, index)
    away = team_availability(away_team, tip, history, index)
    reason = home.reason or away.reason
    if reason is not None:
        return P1Adjustment(None, reason, home, away)
    adjustment = P1_COEF_AVAIL * (
        float(home.avail_top7_prev1 or 0.0) - float(away.avail_top7_prev1 or 0.0)
    ) + (
        P1_COEF_OUT3
        * (float(home.top7_minutes_share_out_3plus or 0.0) - float(away.top7_minutes_share_out_3plus or 0.0))
    )
    return P1Adjustment(adjustment, None, home, away)
