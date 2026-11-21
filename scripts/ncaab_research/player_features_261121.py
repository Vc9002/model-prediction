"""Point-in-time NCAAB player features (quick 261121-hmc, lane P). No lines, no outcomes.

Reads the player box scores written by ``scripts/backfill_ncaab_player_boxes.py``
(``data/research/ncaab_external/espn_player_boxes/<date>/<event_id>.json``) and the archived scoreboard
(team schedules), and writes one row per (event_id, team) to
``data/research/ncaab_external/player_features/pit_player_features.jsonl``.

Point in time: a game's features use only that team's games on an EARLIER Eastern calendar date (the
scoreboard ``date``), plus the complete previous season. The game's own box is an outcome and never
enters its own row. Definitions: ``player_features_261121.md``.

Usage:
    .venv/Scripts/python scripts/ncaab_research/player_features_261121.py audit
    .venv/Scripts/python scripts/ncaab_research/player_features_261121.py build
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import cached_property
from itertools import pairwise
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BOX_ROOT = ROOT / "data" / "research" / "ncaab_external" / "espn_player_boxes"
SCOREBOARD = ROOT / "data" / "research" / "ncaab_market" / "scoreboard.jsonl"
OUT_ROOT = ROOT / "data" / "research" / "ncaab_external" / "player_features"
FEATURES_PATH = OUT_ROOT / "pit_player_features.jsonl"
SCHEMA = "ncaab-pit-player-features-v1"
SEASONS = (2022, 2023, 2024, 2025, 2026)

TOP_ROTATION = 7  # top-N athletes by season-to-date minutes form the "rotation"
MIN_ROTATION_GAMES = 3  # fewer prior same-season games: rotation too noisy, availability is null
MIN_PRIOR_TEAM_GAMES = 10  # a team with fewer prior-season boxes has no usable prior-season roster
MIN_PRIOR_MINUTES_RATE = 100.0  # prior-season minutes needed before a per-40 rate is trusted
EARLY_SEASON_GAMES = 5  # informational flag only
LONG_ABSENCE_GAMES = 3


@dataclass(frozen=True)
class PlayerLine:
    athlete_id: str
    minutes: float
    gmsc: float  # Hollinger game score for the game (0 when the athlete did not play)


@dataclass(frozen=True)
class TeamGame:
    event_id: str
    team_id: str
    opponent_id: str
    season: int
    game_date: str  # Eastern calendar day, the PIT cutoff key
    event_start_utc: str
    side: str
    team_conference_id: Any
    box_available: bool
    players: tuple[PlayerLine, ...] = ()
    box_quality_reason: str | None = None

    @cached_property
    def played(self) -> dict[str, float]:
        return {p.athlete_id: p.minutes for p in self.players if p.minutes > 0}


def game_score(p: dict[str, Any]) -> float:
    """Hollinger game score from one box line; absent stats count as zero."""

    def v(name: str) -> float:
        value = p.get(name)
        return float(value) if value is not None else 0.0

    return (
        v("points")
        + 0.4 * v("fgm")
        - 0.7 * v("fga")
        - 0.4 * (v("fta") - v("ftm"))
        + 0.7 * v("oreb")
        + 0.3 * v("dreb")
        + v("stl")
        + 0.7 * v("ast")
        + 0.7 * v("blk")
        - 0.4 * v("pf")
        - v("tov")
    )


MINUTES_TOLERANCE = 5  # team minutes may differ from 200 (+25 per OT) by rounding; more means a broken box


def box_quality(team: dict[str, Any], opponent: dict[str, Any] | None = None) -> str | None:
    """None when the team's player box is complete; else why it cannot be trusted.

    ESPN serves some boxes with real players listed as did-not-play, without stats and often without
    an athlete id (seen: four starters of one team, 57 team minutes). Using such a box would mark
    those players absent, so the whole team-game is treated as having no box (fail closed).
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
        # ESPN sometimes copies one team's player list onto the other side of the same game; which
        # side is real cannot be told from the box alone, so both sides fail.
        own = {p.get("athlete_id") for p in players if p.get("athlete_id") is not None}
        other = {
            p.get("athlete_id") for p in opponent.get("players") or [] if p.get("athlete_id") is not None
        }
        if own & other:
            return "players_shared_with_opponent"
    return None


def team_games_from_record(record: dict[str, Any]) -> list[TeamGame]:
    out = []
    for side, other in (("home", "away"), ("away", "home")):
        team = record[side]
        quality = box_quality(team, record[other])
        players = []
        for p in team.get("players") or []:
            if p.get("athlete_id") is None:
                continue  # unidentifiable row (seen only on DNP lines); cannot be joined across games
            minutes = float(p.get("minutes") or 0)
            players.append(PlayerLine(str(p["athlete_id"]), minutes, game_score(p) if minutes > 0 else 0.0))
        out.append(
            TeamGame(
                event_id=str(record["event_id"]),
                team_id=str(team["team_id"]),
                opponent_id=str(record[other]["team_id"]),
                season=int(record["season_year"]),
                game_date=str(record["game_date"]),
                event_start_utc=str(record["event_start_utc"]),
                side=side,
                team_conference_id=team.get("conference_id"),
                box_available=quality is None,
                players=tuple(players),
                box_quality_reason=quality,
            )
        )
    return out


def iter_box_records(root: Path = BOX_ROOT) -> Iterable[dict[str, Any]]:
    for path in sorted(root.glob("*/*.json")):
        with path.open(encoding="utf-8") as handle:
            yield json.load(handle)


def load_schedule(seasons: Iterable[int] = SEASONS, path: Path = SCOREBOARD) -> dict[tuple[str, int], list]:
    """(team_id, season) -> sorted [(game_date, event_id)] of completed games on the scoreboard."""
    wanted = set(seasons)
    sched: dict[tuple[str, int], set] = defaultdict(set)
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            for event in row.get("events") or []:
                season = event.get("season_year")
                if season not in wanted or not event.get("completed"):
                    continue
                for side in ("home", "away"):
                    sched[(str(event[f"{side}_team_id"]), int(season))].add(
                        (str(row["date"]), str(event["event_id"]))
                    )
    return {key: sorted(value) for key, value in sched.items()}


# ---------------------------------------------------------------------------------------------------
# Prior season (complete before the current season starts, so every row of it is PIT-valid)


@dataclass
class PriorSeason:
    season: int
    max_date: str
    athlete_team_minutes: dict[str, dict[str, float]] = field(default_factory=dict)
    athlete_team_gmsc: dict[str, dict[str, float]] = field(default_factory=dict)
    team_minutes: dict[str, float] = field(default_factory=dict)
    team_gmsc: dict[str, float] = field(default_factory=dict)
    team_games: dict[str, int] = field(default_factory=dict)  # usable boxes only
    team_games_any: dict[str, int] = field(default_factory=dict)  # every box file, usable or not


def build_prior_season(games: Iterable[TeamGame], season: int) -> PriorSeason:
    all_games = [g for g in games if g.season == season]
    prior = PriorSeason(season=season, max_date=max((g.game_date for g in all_games), default=""))
    for g in all_games:
        prior.team_games_any[g.team_id] = prior.team_games_any.get(g.team_id, 0) + 1
    for g in (g for g in all_games if g.box_available):
        prior.team_games[g.team_id] = prior.team_games.get(g.team_id, 0) + 1
        for p in g.players:
            if p.minutes <= 0:
                continue
            prior.athlete_team_minutes.setdefault(p.athlete_id, {})
            prior.athlete_team_gmsc.setdefault(p.athlete_id, {})
            mins = prior.athlete_team_minutes[p.athlete_id]
            mins[g.team_id] = mins.get(g.team_id, 0.0) + p.minutes
            gm = prior.athlete_team_gmsc[p.athlete_id]
            gm[g.team_id] = gm.get(g.team_id, 0.0) + p.gmsc
            prior.team_minutes[g.team_id] = prior.team_minutes.get(g.team_id, 0.0) + p.minutes
            prior.team_gmsc[g.team_id] = prior.team_gmsc.get(g.team_id, 0.0) + p.gmsc
    return prior


# ---------------------------------------------------------------------------------------------------
# Feature computation for one team-game


AVAILABILITY_FIELDS = (
    "avail_top7_prev1",
    "avail_top7_prev2_any",
    "top7_n_out_prev1",
    "top7_max_absence_streak",
    "top7_minutes_share_out_3plus",
    "top1_out_prev1",
    "roster_minutes_share_out_prev1",
)
PRIOR_FIELDS = (
    "continuity_same_team",
    "continuity_any_team",
    "ret_min_share_same",
    "ret_gmsc_share_same",
    "ret_min_share_transfer_in",
    "ret_gmsc_share_transfer_in",
    "prior_gmsc40_cur_weighted",
    "prior_gmsc40_coverage",
)


def _ratio(num: float, den: float) -> float | None:
    return num / den if den > 0 else None


def availability_features(
    history: list[TeamGame], scheduled_before: list[tuple[str, str]] | None
) -> dict[str, Any]:
    """Who of the season-to-date rotation played in the team's previous game(s).

    ``history`` must already be restricted to this team's same-season games on earlier Eastern dates
    with a box. ``scheduled_before`` is the scoreboard schedule before the game day; when given, a
    previous game with no box makes the features null instead of silently skipping a game.
    """
    out: dict[str, Any] = {name: None for name in AVAILABILITY_FIELDS}
    out["availability_null_reason"] = None
    out["avail_prev2_null_reason"] = None
    if len(history) < MIN_ROTATION_GAMES:
        # Separate "too early in the season" from "games were played but their boxes failed the gate".
        early = scheduled_before is None or len(scheduled_before) < MIN_ROTATION_GAMES
        reason = "insufficient_history" if early else "insufficient_box_history"
        out["availability_null_reason"] = reason
        out["avail_prev2_null_reason"] = reason
        return out
    by_event = {g.event_id: g for g in history}
    if scheduled_before:
        prev_ids = [eid for _, eid in scheduled_before[-2:]]
        if prev_ids[-1] not in by_event:
            out["availability_null_reason"] = "prev_game_box_missing"
            out["avail_prev2_null_reason"] = "prev_game_box_missing"
            return out
        prev1 = by_event[prev_ids[-1]]
        prev2 = by_event.get(prev_ids[0]) if len(prev_ids) == 2 else None
    else:
        prev1, prev2 = history[-1], history[-2]

    season_minutes: dict[str, float] = defaultdict(float)
    for g in history:
        for athlete, minutes in g.played.items():
            season_minutes[athlete] += minutes
    ranked = sorted(season_minutes.items(), key=lambda kv: (-kv[1], kv[0]))
    top = ranked[:TOP_ROTATION]
    top_total = sum(m for _, m in top)
    played1 = prev1.played

    streaks = {}
    for athlete, _ in top:
        streak = 0
        for g in reversed(history):
            if athlete in g.played:
                break
            streak += 1
        streaks[athlete] = streak

    out["avail_top7_prev1"] = _ratio(sum(m for a, m in top if a in played1), top_total)
    out["top7_n_out_prev1"] = sum(1 for a, _ in top if a not in played1)
    out["top7_max_absence_streak"] = max(streaks.values())
    out["top7_minutes_share_out_3plus"] = _ratio(
        sum(m for a, m in top if streaks[a] >= LONG_ABSENCE_GAMES), top_total
    )
    out["top1_out_prev1"] = top[0][0] not in played1
    out["roster_minutes_share_out_prev1"] = _ratio(
        sum(m for a, m in season_minutes.items() if a not in played1), sum(season_minutes.values())
    )
    if prev2 is None:
        out["avail_prev2_null_reason"] = "prev2_game_box_missing"
    else:
        played2 = prev2.played
        out["avail_top7_prev2_any"] = _ratio(
            sum(m for a, m in top if a in played1 or a in played2), top_total
        )
    return out


def prior_season_features(history: list[TeamGame], team_id: str, prior: PriorSeason | None) -> dict[str, Any]:
    """Roster continuity and returning production, counting only athletes who already appeared."""
    out: dict[str, Any] = {name: None for name in PRIOR_FIELDS}
    out["prior_null_reason"] = None
    if prior is None:
        out["prior_null_reason"] = "no_prior_season_data"
        return out
    if (
        prior.team_games.get(team_id, 0) < MIN_PRIOR_TEAM_GAMES
        or prior.team_minutes.get(team_id, 0) <= 0
        or prior.team_gmsc.get(team_id, 0) <= 0
    ):
        out["prior_null_reason"] = (
            "prior_season_boxes_unusable"
            if prior.team_games_any.get(team_id, 0) >= MIN_PRIOR_TEAM_GAMES
            else "team_absent_prior_season"
        )
        return out
    if not history:
        out["prior_null_reason"] = "no_current_season_history"
        return out
    current: dict[str, float] = defaultdict(float)
    for g in history:
        for athlete, minutes in g.played.items():
            current[athlete] += minutes
    total_current = sum(current.values())

    # Only stints on teams with a full usable prior season count. Non-D1 schools appear in a few
    # boxes per season and failed team-seasons have few usable ones; a share of a 2-game total is not
    # on the scale of a 30-game share (one such ratio reached 6x a team's whole game-score total).
    def eligible(team: str) -> bool:
        return (
            prior.team_games.get(team, 0) >= MIN_PRIOR_TEAM_GAMES
            and prior.team_minutes.get(team, 0) > 0
            and prior.team_gmsc.get(team, 0) > 0
        )

    stints = {
        a: {t: m for t, m in prior.athlete_team_minutes.get(a, {}).items() if eligible(t)} for a in current
    }
    same_team = {a for a in current if stints[a].get(team_id, 0) > 0}
    any_team = {a for a in current if stints[a]}
    out["continuity_same_team"] = _ratio(sum(current[a] for a in same_team), total_current)
    out["continuity_any_team"] = _ratio(sum(current[a] for a in any_team), total_current)

    out["ret_min_share_same"] = (
        sum(prior.athlete_team_minutes[a][team_id] for a in same_team) / (prior.team_minutes[team_id])
    )
    team_gmsc = prior.team_gmsc.get(team_id, 0.0)
    out["ret_gmsc_share_same"] = (
        sum(prior.athlete_team_gmsc[a][team_id] for a in same_team) / team_gmsc if team_gmsc > 0 else None
    )
    transfer_min = 0.0
    transfer_gmsc = 0.0
    for athlete in any_team:
        for old_team, minutes in stints[athlete].items():
            if old_team == team_id:
                continue
            transfer_min += minutes / prior.team_minutes[old_team]
            transfer_gmsc += prior.athlete_team_gmsc[athlete][old_team] / prior.team_gmsc[old_team]
    out["ret_min_share_transfer_in"] = transfer_min
    out["ret_gmsc_share_transfer_in"] = transfer_gmsc

    rated = {
        a: 40.0 * sum(prior.athlete_team_gmsc[a][t] for t in stints[a]) / sum(stints[a].values())
        for a in any_team
        if sum(stints[a].values()) >= MIN_PRIOR_MINUTES_RATE
    }
    rated_minutes = sum(current[a] for a in rated)
    out["prior_gmsc40_coverage"] = _ratio(rated_minutes, total_current)
    out["prior_gmsc40_cur_weighted"] = (
        sum(current[a] * rated[a] for a in rated) / rated_minutes if rated_minutes > 0 else None
    )
    return out


def features_for_game(
    target: TeamGame,
    season_games: list[TeamGame],
    prior: PriorSeason | None,
    scheduled: list[tuple[str, str]] | None,
) -> dict[str, Any]:
    """One feature row. The strict ``<`` on the Eastern date is the PIT cut: the game itself and any
    other game that day are excluded, and so is everything later."""
    history = sorted(
        (
            g
            for g in season_games
            if g.team_id == target.team_id
            and g.season == target.season
            and g.box_available
            and g.game_date < target.game_date
        ),
        key=lambda g: (g.game_date, g.event_start_utc, g.event_id),
    )
    if prior is not None and not prior.max_date < target.game_date:
        raise ValueError("prior_season_overlaps_current_game")
    scheduled_before = (
        [(d, e) for d, e in scheduled if d < target.game_date] if scheduled is not None else None
    )
    row: dict[str, Any] = {
        "schema": SCHEMA,
        "event_id": target.event_id,
        "team_id": target.team_id,
        "opponent_id": target.opponent_id,
        "side": target.side,
        "season": target.season,
        "game_date": target.game_date,
        "team_conference_id": target.team_conference_id,
        "n_prior_games_box": len(history),
        "n_prior_games_scheduled": len(scheduled_before) if scheduled_before is not None else None,
        "early_season": len(history) < EARLY_SEASON_GAMES,
    }
    row.update(availability_features(history, scheduled_before))
    row.update(prior_season_features(history, target.team_id, prior))
    return row


def build_feature_rows(
    team_games: list[TeamGame], schedule: dict[tuple[str, int], list] | None = None
) -> list[dict[str, Any]]:
    by_team_season: dict[tuple[str, int], list[TeamGame]] = defaultdict(list)
    by_season: dict[int, list[TeamGame]] = defaultdict(list)
    for g in team_games:
        by_team_season[(g.team_id, g.season)].append(g)
        by_season[g.season].append(g)
    priors = {s: build_prior_season(by_season[s - 1], s - 1) for s in by_season if by_season.get(s - 1)}
    rows = []
    for (team_id, season), games in by_team_season.items():
        scheduled = schedule.get((team_id, season), []) if schedule is not None else None
        for target in games:
            rows.append(features_for_game(target, games, priors.get(season), scheduled))
    rows.sort(key=lambda r: (r["game_date"], r["event_id"], r["side"]))
    return rows


# ---------------------------------------------------------------------------------------------------
# Data-quality summaries (no outcomes, no lines)


def _quantiles(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    values = sorted(values)

    def q(p: float) -> float:
        return round(values[min(len(values) - 1, int(p * (len(values) - 1) + 0.5))], 4)

    return {"n": len(values), "p05": q(0.05), "p25": q(0.25), "p50": q(0.5), "p75": q(0.75), "p95": q(0.95)}


def feature_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {"rows": len(rows), "by_season": {}}
    for season in sorted({r["season"] for r in rows}):
        sub = [r for r in rows if r["season"] == season]
        d1 = [r for r in sub if r["team_conference_id"] is not None]
        block: dict[str, Any] = {"rows": len(sub), "rows_d1_team": len(d1), "null_rate_d1": {}, "dist_d1": {}}
        for name in AVAILABILITY_FIELDS + PRIOR_FIELDS:
            vals = [r[name] for r in d1]
            block["null_rate_d1"][name] = round(sum(v is None for v in vals) / max(len(vals), 1), 4)
            nums = [float(v) for v in vals if v is not None]
            block["dist_d1"][name] = _quantiles(nums)
        for reason in ("availability_null_reason", "avail_prev2_null_reason", "prior_null_reason"):
            block[reason] = dict(Counter(r[reason] for r in d1 if r[reason] is not None))
        summary["by_season"][str(season)] = block
    return summary


def audit(root: Path = BOX_ROOT) -> dict[str, Any]:
    in_scope: dict[str, tuple[int, bool]] = {}
    with SCOREBOARD.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            for event in row.get("events") or []:
                if event.get("season_year") in SEASONS and event.get("completed"):
                    d1 = (
                        event.get("home_conference_id") is not None
                        and event.get("away_conference_id") is not None
                    )
                    in_scope[str(event["event_id"])] = (int(event["season_year"]), d1)

    files_per_event: Counter[str] = Counter()
    coverage: dict[str, Counter] = defaultdict(Counter)
    empty_team = Counter()
    quality_by_season: dict[str, Counter] = defaultdict(Counter)
    team_season_quality: dict[tuple, Counter] = defaultdict(Counter)
    minutes_dev: Counter[str] = Counter()
    minutes_bad_examples: list[dict[str, Any]] = []
    points_mismatch = 0
    no_id_rows = 0
    dup_in_game = 0
    both_teams = 0
    team_games_n = 0
    athlete_names: dict[str, set] = defaultdict(set)
    athlete_season_teams: dict[str, dict[int, set]] = defaultdict(lambda: defaultdict(set))
    name_team_season_ids: dict[tuple, set] = defaultdict(set)
    for record in iter_box_records(root):
        eid = str(record["event_id"])
        files_per_event[eid] += 1
        season = int(record["season_year"])
        coverage[str(season)]["files_d1" if record.get("d1_vs_d1") else "files_non_d1"] += 1
        ids_by_side = {}
        for side in ("home", "away"):
            team = record[side]
            team_games_n += 1
            quality = box_quality(team, record["away" if side == "home" else "home"])
            quality_by_season[str(season)][quality or "ok"] += 1
            if team.get("conference_id") is not None:
                team_season_quality[(team["team_id"], season)][quality is None] += 1
            players = team.get("players") or []
            if not team.get("player_box_available"):
                empty_team[str(season)] += 1
                continue
            played = [p for p in players if (p.get("minutes") or 0) > 0]
            mins = sum(p.get("minutes") or 0 for p in players)
            periods = team.get("periods") or 2
            expected = 200 + 25 * max(periods - 2, 0)
            dev = mins - expected
            bucket = "exact" if dev == 0 else ("within_5" if abs(dev) <= 5 else "off_gt_5")
            minutes_dev[bucket] += 1
            if bucket == "off_gt_5" and len(minutes_bad_examples) < 15:
                minutes_bad_examples.append(
                    {"event_id": eid, "team_id": team["team_id"], "minutes": mins, "expected": expected}
                )
            pts = sum(p.get("points") or 0 for p in players)
            if team.get("score_header") is not None and pts != team["score_header"]:
                points_mismatch += 1
            ids = [p["athlete_id"] for p in players if p.get("athlete_id") is not None]
            no_id_rows += sum(1 for p in players if p.get("athlete_id") is None)
            dup_in_game += len(ids) - len(set(ids))
            ids_by_side[side] = set(ids)
            for p in played:
                if p.get("athlete_id") is None:
                    continue
                aid = p["athlete_id"]
                athlete_names[aid].add(p.get("name"))
                athlete_season_teams[aid][season].add(team["team_id"])
                name_team_season_ids[(p.get("name"), team["team_id"], season)].add(aid)
        if len(ids_by_side) == 2:
            both_teams += len(ids_by_side["home"] & ids_by_side["away"])

    for eid, (season, d1) in in_scope.items():
        coverage[str(season)]["scoreboard_d1" if d1 else "scoreboard_non_d1"] += 1
        if files_per_event.get(eid):
            coverage[str(season)]["present_d1" if d1 else "present_non_d1"] += 1

    multi_season = [a for a, s in athlete_season_teams.items() if len(s) >= 2]
    moved = 0
    consecutive_pairs = 0
    for aid in multi_season:
        seasons = sorted(athlete_season_teams[aid])
        for s0, s1 in pairwise(seasons):
            if s1 == s0 + 1:
                consecutive_pairs += 1
                if not (athlete_season_teams[aid][s0] & athlete_season_teams[aid][s1]):
                    moved += 1
    # Id instability check: the same name on the same team in consecutive seasons under different ids.
    name_team_ids: dict[tuple, dict[int, set]] = defaultdict(dict)
    for (name, team, season), ids in name_team_season_ids.items():
        name_team_ids[(name, team)][season] = ids
    id_changes = []
    for (name, team), per_season in name_team_ids.items():
        for season, ids in per_season.items():
            nxt = per_season.get(season + 1)
            if nxt and not (ids & nxt):
                id_changes.append({"name": name, "team_id": team, "season": season + 1})
    multi_team_in_season = sum(
        1 for s in athlete_season_teams.values() for teams in s.values() if len(teams) > 1
    )
    return {
        "coverage_by_season": {k: dict(v) for k, v in sorted(coverage.items())},
        "events_with_multiple_files": sum(1 for n in files_per_event.values() if n > 1),
        "team_games": team_games_n,
        "box_quality_by_season": {k: dict(v) for k, v in sorted(quality_by_season.items())},
        "d1_team_seasons_box_fail_rate_over_half_by_season": dict(
            Counter(str(season) for (_, season), c in team_season_quality.items() if c[False] > c[True])
        ),
        "d1_team_seasons_by_season": dict(Counter(str(season) for _, season in team_season_quality)),
        "team_games_without_player_box_by_season": dict(empty_team),
        "minutes_vs_expected": dict(minutes_dev),
        "minutes_off_examples": minutes_bad_examples,
        "points_sum_mismatch_team_games": points_mismatch,
        "rows_without_athlete_id": no_id_rows,
        "duplicate_athlete_rows_within_team_game": dup_in_game,
        "athletes_on_both_teams_same_game": both_teams,
        "athletes_with_minutes": len(athlete_season_teams),
        "athletes_multiple_names": sum(1 for n in athlete_names.values() if len(n) > 1),
        "athletes_in_2plus_seasons": len(multi_season),
        "consecutive_season_pairs": consecutive_pairs,
        "consecutive_pairs_team_changed": moved,
        "athlete_team_seasons_multiple_teams": multi_team_in_season,
        "same_name_team_new_id_next_season": len(id_changes),
        "same_name_team_new_id_examples": id_changes[:15],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=("audit", "build"))
    args = ap.parse_args()
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    if args.command == "audit":
        result = audit()
        (OUT_ROOT / "audit_261121.json").write_text(
            json.dumps(result, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    team_games = [tg for record in iter_box_records() for tg in team_games_from_record(record)]
    rows = build_feature_rows(team_games, load_schedule())
    with FEATURES_PATH.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
    summary = feature_summary(rows)
    summary["minutes_sum_median_team_game"] = statistics.median(
        sum(p.minutes for p in tg.players) for tg in team_games if tg.box_available
    )
    (OUT_ROOT / "feature_summary_261121.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(f"wrote {len(rows)} rows to {FEATURES_PATH}")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
