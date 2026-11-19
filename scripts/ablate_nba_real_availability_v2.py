"""Research-only ablation v2: zero vs STATIC-tier vs REAL box-score NBA availability.

Three arms run on the SAME ``NBAStructuralV7Engine`` and the SAME
``split_complete_dates`` 60/20/20 walk-forward as ``ablate_nba_real_availability.py``
(which this script does not touch):

* zero   - ``availability_points_gap = 0`` (what historical games actually carried).
* static - served behaviour: each absent player is worth a name tier (4.8 / 2.5 / 1.1),
           status-weighted by ``1 - STATUS_PROBABILITIES["Out"]``, team loss capped at 12.
* real   - each absent player is worth a box-score value that is replacement-relative,
           minutes-aware, keyed by player_id and Empirical-Bayes shrunk over PLAYED games only.

LIMITATION (plain words): no historical NBA injury-report archive exists. "Out" is
INFERRED from box scores: a rotation player (mean minutes >= 8 over the team's last 15
boxscore games) with a non-COACH'S-DECISION DNP in the team's actual most recent
completed game. Both availability arms use this same inferred set and differ only in
per-player value (static name tier vs real box-score value). Served behaviour uses live
injury-report statuses; here every inferred absence is weighted as Out (1.0). The proxy
misses players ruled out for the first time tonight and keeps players who return tonight.

Real value formula, per absent player with played-game impacts ``x_1..x_n`` before the
cutoff (minutes > 0 only; DNP rows are never averaged in as zeros)::

    impact_i  = box_plus_minus_proxy(row_i) * minutes_i / 48     # per-36 rate x share of a game
    shrunk    = (PRIOR_WEIGHT * r + n * mean(last LOOKBACK impacts)) / (PRIOR_WEIGHT + n)
    value     = max(0, shrunk - r)                               # r = replacement level

Correctness fix versus v1: v1 takes each team's last 15 games that HAVE a boxscore,
however old, so a March 2026 game inherits an early-February "out" set. v2 uses the team's
actual most recent completed game from the schedule; if that game has no boxscore the side
is uncovered and contributes 0 (and is flagged), never a stale substitute.

Writes only to ``--out`` (refused when it resolves inside the repo). Never touches config,
serving code, ledgers or ``data/``; no capture or backfill is run.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from ablate_nba_v7 import load_nba_games
from nba_v5_pit_walkforward import split_complete_dates

from model_prediction.features.base import GameRecord
from model_prediction.features.nba_player_availability import (
    MAX_TEAM_LOSS_PTS,
    STATUS_PROBABILITIES,
    _estimate_player_impact,
)
from model_prediction.features.nba_player_logs import (
    box_plus_minus_proxy,
    is_confirmed_unavailable,
    parse_nba_player_boxscore,
)
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.nba_v7 import NBAStructuralV7Engine

BOX_DIR = REPO_ROOT / "data" / "availability" / "nba" / "espn_boxscores"  # the only NBA boxscore store
GAMES_PATH = REPO_ROOT / "data" / "historical" / "nba_games_all.jsonl"  # full schedule incl. uncovered games
LOOKBACK = 15  # rotation window and EB lookback, same as v1 and rolling_player_values
MEAN_MINUTES_FLOOR = 8.0  # rotation-player floor, same as v1
PRIOR_WEIGHT = 5.0  # pseudo-game count, same as rolling_player_values
K_GRID = (0.25, 0.5, 1.0)  # k = 0 IS the zero arm, never run as a duplicate
REPLACEMENT_GRID = (0.5, 1.0, 1.5, 2.0)  # candidate replacement levels in impact points per game
STATIC_BASELINE_K = 1.0  # served behaviour, fixed before any selection
TRAIN_REPLACEMENT_QUANTILE = 0.25  # train-only fit candidate, added to the grid
SMOKE_DATES = 5  # validation dates evaluated in --smoke mode
STALE_DAYS = 2  # boxscores older than this make a live use need a refresh
EXIT_OUT_IN_REPO = 3  # not 2: argparse owns 2 for usage errors

LIMITATION = (
    "No historical NBA injury-report archive exists. 'Out' is INFERRED from box scores: a rotation "
    "player (mean minutes >= 8 over the team's last 15 boxscore games) with a non-COACH'S-DECISION "
    "DNP in the team's actual most recent completed game. Both availability arms use this same "
    "inferred set and differ only in per-player value (static name tier vs real box-score value). "
    "Served behaviour uses live injury-report statuses weighted by STATUS_PROBABILITIES; here every "
    "inferred absence is weighted as Out (1.0). This proxy misses players ruled out for the first "
    "time tonight and keeps players who return tonight."
)

OUT_WEIGHT = 1.0 - STATUS_PROBABILITIES["Out"]  # inferred absences are weighted as Out
STATIC_TIERS = (4.8, 2.5, 1.1)  # the three values _estimate_player_impact can return


# ----------------------------------------------------------------------------- dataclasses


@dataclass(frozen=True)
class BoxData:
    rows_by_event: dict[str, dict[str, list[dict[str, Any]]]]
    header_date_by_event: dict[str, str]
    file_count: int
    unparsed_count: int


@dataclass(frozen=True)
class PlayerIndex:
    by_player: dict[str, list[tuple[str, float]]]
    skipped_events: int


@dataclass(frozen=True)
class AbsenceResult:
    status: str  # "ok" | "uncovered" | "no_prior_game"
    absent: tuple[tuple[str, str], ...]  # (player_id, name)


@dataclass(frozen=True)
class TeamLoss:
    static_loss: float
    static_tier_hits: dict[float, int]
    real_losses: dict[float, float]
    real_fallback: int


@dataclass
class ConfigResult:
    key: str
    n: int
    mae_margin: float
    brier: float
    accuracy: float = float("nan")
    nonzero_games: int = 0
    mean_abs_gap: float = float("nan")
    elig_n: int = 0
    elig_mae_margin: float = float("nan")
    elig_brier: float = float("nan")
    elig_accuracy: float = float("nan")


@dataclass(frozen=True)
class ConfigSpec:
    key: str
    arm: str  # "zero" | "static" | "real"
    k: float
    replacement: float | None = None


@dataclass
class EvalContext:
    all_games: list[GameRecord]
    engine: NBAStructuralV7Engine
    rows_by_event: dict[str, dict[str, list[dict[str, Any]]]]
    team_schedule: dict[str, list[tuple[str, str]]]
    team_covered: dict[str, list[tuple[str, str]]]
    player_index: PlayerIndex
    replacement_levels: tuple[float, ...]


@dataclass
class EvalOutput:
    results: dict[str, ConfigResult]
    diagnostics: dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------------- pure helpers


def minutes_weighted_impact(row: dict[str, Any]) -> float:
    """Points of team impact per game, minutes-weighted: per-36 proxy x minutes / 48.

    Algebraically 0.075 x the raw box composite, so a 15-minute bench scorer no longer
    out-ranks a 35-minute starter just because the per-36 rate inflates a small sample
    of minutes (20 pts in 15 min scores 4.9 per-36 but only 1.5 here, against 2.3 for a
    35-minute starter with the same points). Zero or negative minutes gives 0.0.
    """
    minutes = float(row.get("minutes", 0.0))
    if minutes <= 0:
        return 0.0
    return box_plus_minus_proxy(row) * minutes / 48.0


def replacement_relative_value(
    played_impacts: Sequence[float],
    replacement_level: float,
    *,
    lookback: int = LOOKBACK,
    prior_weight: float = PRIOR_WEIGHT,
) -> float | None:
    """max(0, EB-shrunk mean of the last ``lookback`` played impacts - replacement level).

    Shrinks toward the replacement level itself (same formula as ``rolling_player_values``
    with ``league_prior = r``), so a player with few games is worth about zero over a
    replacement rather than being pulled up to a 1.1 league prior. None when there is no
    played history. Played-only filtering happens in the index, never here.
    """
    window = list(played_impacts)[-lookback:]
    if not window:
        return None
    n = len(window)
    mean_val = sum(window) / n
    shrunk = (prior_weight * replacement_level + n * mean_val) / (prior_weight + n)
    return max(0.0, shrunk - replacement_level)


def _header_date(payload: dict[str, Any]) -> str | None:
    try:
        value = payload["header"]["competitions"][0]["date"]
    except (KeyError, IndexError, TypeError):
        return None
    return value if isinstance(value, str) else None


def load_boxscores_with_dates(directory: str | Path) -> BoxData:
    """One pass over the capture directory; each file parsed once, payload not retained."""
    root = Path(directory)
    rows_by_event: dict[str, dict[str, list[dict[str, Any]]]] = {}
    header_dates: dict[str, str] = {}
    file_count = 0
    unparsed = 0
    if not root.is_dir():
        return BoxData(rows_by_event, header_dates, 0, 0)
    for path in sorted(root.iterdir()):
        if not path.is_file() or path.suffix != ".json":
            continue
        file_count += 1
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            payload = data.get("payload") if isinstance(data, dict) else None
            if not isinstance(payload, dict):
                unparsed += 1
                continue
            parsed = parse_nba_player_boxscore(payload)
            header = _header_date(payload)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            unparsed += 1
            continue
        if not parsed:
            unparsed += 1
            continue
        rows_by_event[path.stem] = parsed
        if header:
            header_dates[path.stem] = header
    return BoxData(rows_by_event, header_dates, file_count, unparsed)


def build_player_index(
    rows_by_event: dict[str, dict[str, list[dict[str, Any]]]], event_start: dict[str, str]
) -> PlayerIndex:
    """``{player_id: sorted [(start_utc, impact)]}`` over PLAYED rows (minutes > 0) of all teams."""
    by_player: dict[str, list[tuple[str, float]]] = defaultdict(list)
    skipped = 0
    for event_id, teams in rows_by_event.items():
        start = event_start.get(event_id)
        if start is None:
            skipped += 1
            continue
        for rows in teams.values():
            for row in rows:
                if float(row.get("minutes", 0.0)) <= 0:
                    continue
                by_player[str(row["player_id"])].append((start, minutes_weighted_impact(row)))
    for history in by_player.values():
        history.sort(key=lambda item: item[0])
    return PlayerIndex(dict(by_player), skipped)


def played_impacts_before(index: PlayerIndex, player_id: str, cutoff_utc: str) -> list[float]:
    """Played impacts of ``player_id`` with start strictly before ``cutoff_utc``."""
    history = index.by_player.get(player_id)
    if not history:
        return []
    cut = bisect_left(history, cutoff_utc, key=lambda item: item[0])
    return [impact for _, impact in history[:cut]]


def _normalize_name(name: str) -> str:
    return " ".join(name.casefold().split())


def build_name_to_ids(name_rows: Iterable[tuple[str, str, str]], cutoff_utc: str) -> dict[str, set[str]]:
    """Normalized name -> player_ids seen strictly before the cutoff (start, player_id, name)."""
    out: dict[str, set[str]] = defaultdict(set)
    for start, player_id, name in name_rows:
        if start < cutoff_utc:
            out[_normalize_name(name)].add(str(player_id))
    return dict(out)


def resolve_player_name(name: str, name_map: dict[str, set[str]]) -> tuple[str | None, str]:
    """(player_id or None, reason) with reason in {"ok", "ambiguous", "no_match"}."""
    ids = name_map.get(_normalize_name(name), set())
    if not ids:
        return None, "no_match"
    if len(ids) > 1:
        return None, "ambiguous"
    return next(iter(ids)), "ok"


def value_for_name(
    name: str,
    name_map: dict[str, set[str]],
    values_by_id: dict[str, float],
    static_fallback: float,
) -> tuple[float, str]:
    """Real value for a name-only record, failing closed to the static tier (flagged)."""
    player_id, reason = resolve_player_name(name, name_map)
    if player_id is None:
        return static_fallback, reason
    if player_id not in values_by_id:
        return static_fallback, "no_history"
    return values_by_id[player_id], "ok"


def inferred_absent(
    team: str,
    cutoff_utc: str,
    team_schedule: Sequence[tuple[str, str]],
    team_covered: Sequence[tuple[str, str]],
    rows_by_event: dict[str, dict[str, list[dict[str, Any]]]],
) -> AbsenceResult:
    """Rotation players with a real-absence DNP in the team's TRUE last completed game.

    ``team_schedule`` holds ALL of the team's games (sorted (start, event_id)); the
    last one before the cutoff must itself have a boxscore, otherwise the side is
    "uncovered" and contributes nothing: a stale older box can never stand in for it.
    A team with fewer than two covered games has no usable rotation window and is also
    "uncovered".
    """
    cut = bisect_left(team_schedule, (cutoff_utc,))
    if cut == 0:
        return AbsenceResult("no_prior_game", ())
    last_event = team_schedule[cut - 1][1]
    last_rows = rows_by_event.get(last_event, {}).get(team)
    if not last_rows:
        return AbsenceResult("uncovered", ())
    covered_cut = bisect_left(team_covered, (cutoff_utc,))
    window = team_covered[max(0, covered_cut - LOOKBACK) : covered_cut]
    if len(window) < 2:
        return AbsenceResult("uncovered", ())
    minutes_by_player: dict[str, list[float]] = defaultdict(list)
    for _, event_id in window:
        for row in rows_by_event.get(event_id, {}).get(team, []):
            minutes_by_player[str(row["player_id"])].append(float(row["minutes"]))
    absent: list[tuple[str, str]] = []
    for row in last_rows:
        player_id = str(row["player_id"])
        minutes = minutes_by_player.get(player_id)
        if not minutes or sum(minutes) / len(minutes) < MEAN_MINUTES_FLOOR:
            continue
        if is_confirmed_unavailable(row):
            absent.append((player_id, str(row["name"])))
    return AbsenceResult("ok", tuple(absent))


def team_losses(
    absent: Sequence[tuple[str, str]],
    cutoff_utc: str,
    player_index: PlayerIndex,
    replacement_levels: Sequence[float],
) -> TeamLoss:
    """Static and real team loss for one shared absent set (both capped at the served 12)."""
    tier_hits = {tier: 0 for tier in STATIC_TIERS}
    static_total = 0.0
    real_totals = {r: 0.0 for r in replacement_levels}
    fallback = 0
    for player_id, name in absent:
        tier = _estimate_player_impact(name, "")
        static_value = OUT_WEIGHT * tier
        tier_hits[tier] = tier_hits.get(tier, 0) + 1
        static_total += static_value
        history = played_impacts_before(player_index, player_id, cutoff_utc)
        if not history:
            fallback += 1
        for r in replacement_levels:
            value = replacement_relative_value(history, r)
            real_totals[r] += static_value if value is None else value
    return TeamLoss(
        static_loss=min(MAX_TEAM_LOSS_PTS, static_total),
        static_tier_hits=tier_hits,
        real_losses={r: min(MAX_TEAM_LOSS_PTS, v) for r, v in real_totals.items()},
        real_fallback=fallback,
    )


def select_winner(results: Iterable[ConfigResult]) -> ConfigResult:
    """Lowest unrounded margin MAE; an exactly equal MAE falls to the lower Brier."""
    return min(results, key=lambda r: (r.mae_margin, r.brier))


# ----------------------------------------------------------------------------- evaluation


def build_configs(replacement_levels: Sequence[float], k_grid: Sequence[float] = K_GRID) -> list[ConfigSpec]:
    configs = [ConfigSpec("zero", "zero", 0.0)]
    configs += [ConfigSpec(f"static_k{k}", "static", k) for k in k_grid]
    for r in replacement_levels:
        configs += [ConfigSpec(f"real_r{r}_k{k}", "real", k, r) for k in k_grid]
    return configs


class _Accum:
    def __init__(self) -> None:
        self.err: list[float] = []
        self.brier: list[float] = []
        self.correct: list[float] = []
        self.abs_gap: list[float] = []
        self.eligible: list[bool] = []


def _mean(values: Sequence[float]) -> float:
    return float(np.mean(values)) if len(values) else float("nan")


def _finalize(key: str, acc: _Accum) -> ConfigResult:
    mask = np.array(acc.eligible, dtype=bool)
    err = np.array(acc.err)
    brier = np.array(acc.brier)
    correct = np.array(acc.correct)
    gaps = np.array(acc.abs_gap)
    nonzero = gaps > 0
    return ConfigResult(
        key=key,
        n=len(acc.err),
        mae_margin=_mean(err),
        brier=_mean(brier),
        accuracy=_mean(correct),
        nonzero_games=int(nonzero.sum()),
        mean_abs_gap=float(gaps[nonzero].mean()) if nonzero.any() else float("nan"),
        elig_n=int(mask.sum()),
        elig_mae_margin=_mean(err[mask]),
        elig_brier=_mean(brier[mask]),
        elig_accuracy=_mean(correct[mask]),
    )


def _upcoming(game: GameRecord, event_id: str, gap: float, total_loss: float) -> UpcomingGame:
    return UpcomingGame(
        event_id=event_id,
        event_start_utc=game.event_start_utc,
        away_team=game.away_team,
        home_team=game.home_team,
        spread_away_line=0.0,
        total_line=228.5,
        league="NBA",
        availability_points_gap=gap,
        availability_total_loss=total_loss,
    )


def _read_prediction(by_market: dict[str, Any]) -> tuple[float, float]:
    spread = by_market.get("spread")
    ml = by_market.get("moneyline")
    margin = float(spread.inference_inputs["mean"]) if spread else 0.0
    p_home = float(ml.probabilities.get("home", 0.5)) if ml else 0.5
    return margin, max(1e-4, min(1 - 1e-4, p_home))


def evaluate(
    ctx: EvalContext,
    eval_games: Sequence[GameRecord],
    configs: Sequence[ConfigSpec],
    *,
    verify_batch: bool = False,
) -> EvalOutput:
    """All configs in one batched pass: ONE ``predict_games`` call per date.

    Each game is copied once per config under ``"<event_id>|<config_key>"``; the engine
    has no cross-game state inside a call, so a copy gets exactly its solo result.
    ``verify_batch`` proves that on the first date (raises RuntimeError on mismatch).
    """
    by_date: dict[str, list[GameRecord]] = defaultdict(list)
    for g in eval_games:
        by_date[g.event_start_utc[:10]].append(g)
    levels = ctx.replacement_levels
    accs = {c.key: _Accum() for c in configs}
    status_counts: Counter[str] = Counter()
    tier_hits: Counter[float] = Counter()
    absent_player_games = 0
    real_fallback = 0
    checked = False

    for day in sorted(by_date):
        games_today = by_date[day]
        cutoff = games_today[0].event_start_utc  # v1 convention: conservative PIT for the whole day
        history = [g for g in ctx.all_games if g.event_start_utc < cutoff]
        sides: dict[str, tuple[TeamLoss, TeamLoss, bool]] = {}
        for g in games_today:
            home_abs = inferred_absent(
                g.home_team, cutoff, ctx.team_schedule.get(g.home_team, []),
                ctx.team_covered.get(g.home_team, []), ctx.rows_by_event,
            )  # fmt: skip
            away_abs = inferred_absent(
                g.away_team, cutoff, ctx.team_schedule.get(g.away_team, []),
                ctx.team_covered.get(g.away_team, []), ctx.rows_by_event,
            )  # fmt: skip
            home_loss = team_losses(home_abs.absent, cutoff, ctx.player_index, levels)
            away_loss = team_losses(away_abs.absent, cutoff, ctx.player_index, levels)
            sides[g.event_id] = (home_loss, away_loss, home_abs.status == "ok" and away_abs.status == "ok")
            for result, loss in ((home_abs, home_loss), (away_abs, away_loss)):
                status_counts[result.status] += 1
                absent_player_games += len(result.absent)
                real_fallback += loss.real_fallback
                for tier, hits in loss.static_tier_hits.items():
                    tier_hits[tier] += hits

        upcoming: list[UpcomingGame] = []
        gaps: dict[str, float] = {}
        for cfg in configs:
            for g in games_today:
                home_loss, away_loss, _ = sides[g.event_id]
                if cfg.arm == "zero":
                    hl = al = 0.0
                elif cfg.arm == "static":
                    hl, al = home_loss.static_loss, away_loss.static_loss
                else:
                    assert cfg.replacement is not None
                    hl, al = home_loss.real_losses[cfg.replacement], away_loss.real_losses[cfg.replacement]
                gap = round(cfg.k * (al - hl), 3)
                gaps[f"{g.event_id}|{cfg.key}"] = gap
                upcoming.append(_upcoming(g, f"{g.event_id}|{cfg.key}", gap, cfg.k * (hl + al)))
        preds = ctx.engine.predict_games(history, upcoming)
        by_event: dict[str, dict[str, Any]] = defaultdict(dict)
        for p in preds:
            by_event[p.event_id][p.market_type] = p

        if verify_batch and not checked:
            solo = ctx.engine.predict_games(
                history, [_upcoming(g, g.event_id, 0.0, 0.0) for g in games_today]
            )
            solo_by: dict[str, dict[str, Any]] = defaultdict(dict)
            for p in solo:
                solo_by[p.event_id][p.market_type] = p
            for g in games_today:
                batched = _read_prediction(by_event[f"{g.event_id}|zero"])
                single = _read_prediction(solo_by[g.event_id])
                if batched != single:
                    raise RuntimeError(f"batch equivalence failed for {g.event_id}: {batched} != {single}")
            checked = True
            print(f"  batch equivalence verified on {day} ({len(games_today)} games, exact match)")

        for cfg in configs:
            acc = accs[cfg.key]
            for g in games_today:
                margin, p_home = _read_prediction(by_event[f"{g.event_id}|{cfg.key}"])
                actual_margin = g.home_score - g.away_score
                home_win = 1.0 if actual_margin > 0 else 0.0
                acc.err.append(abs(margin - actual_margin))
                acc.brier.append((p_home - home_win) ** 2)
                acc.correct.append(1.0 if (p_home >= 0.5) == bool(home_win) else 0.0)
                acc.abs_gap.append(abs(gaps[f"{g.event_id}|{cfg.key}"]))
                acc.eligible.append(sides[g.event_id][2])

    diagnostics = {
        "side_status_counts": dict(status_counts),
        "absent_player_games": absent_player_games,
        "static_tier_hits": {str(t): tier_hits.get(t, 0) for t in STATIC_TIERS},
        "real_fallback": real_fallback,
    }
    return EvalOutput({key: _finalize(key, acc) for key, acc in accs.items()}, diagnostics)


# ----------------------------------------------------------------------------- reports


def _read_raw_games(path: Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def data_lag_report(box: BoxData, raw_games: Sequence[dict[str, Any]], today: date) -> dict[str, Any]:
    """Boxscore inventory: file count, header-date range, coverage overall and by month."""
    headers = sorted(box.header_date_by_event.values())
    oldest = headers[0] if headers else None
    newest = headers[-1] if headers else None
    days_since = (today - date.fromisoformat(newest[:10])).days if newest else None
    covered_ids = set(box.rows_by_event)
    months: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    covered_total = 0
    name_mismatch = 0
    for row in raw_games:
        month = str(row["event_start_utc"])[:7]
        months[month][0] += 1
        event_id = str(row["event_id"])
        if event_id in covered_ids:
            months[month][1] += 1
            covered_total += 1
            teams = box.rows_by_event[event_id]
            if row["home_team"] not in teams or row["away_team"] not in teams:
                name_mismatch += 1
    null_rows = sum(1 for r in raw_games if r.get("status") is None or r.get("season_type") is None)
    refresh = days_since is None or days_since > STALE_DAYS
    return {
        "file_count": box.file_count,
        "unparsed_count": box.unparsed_count,
        "oldest_header_date": oldest,
        "newest_header_date": newest,
        "days_since_newest": days_since,
        "games_total": len(raw_games),
        "games_covered": covered_total,
        "month_table": {m: {"games": v[0], "covered": v[1]} for m, v in sorted(months.items())},
        "null_status_or_season_type_rows": null_rows,
        "team_name_mismatch_games": name_mismatch,
        "verdict": "REFRESH NEEDED before any live use" if refresh else "boxscores current",
    }


def _print_data_lag(report: dict[str, Any]) -> None:
    print("=== A. BOXSCORE DATA LAG ===")
    print(f"files: {report['file_count']} (unparsed {report['unparsed_count']})")
    print(f"header game dates: oldest {report['oldest_header_date']}, newest {report['newest_header_date']}")
    print(f"days since newest header date (UTC today): {report['days_since_newest']}")
    pct = report["games_covered"] / max(1, report["games_total"])
    print(f"coverage of nba_games_all.jsonl: {report['games_covered']}/{report['games_total']} ({pct:.1%})")
    print(f"rows with null status or season_type (never selected by backfill_nba_boxscores.py): "
          f"{report['null_status_or_season_type_rows']}")  # fmt: skip
    print(f"covered games whose team names are missing from the boxscore keys: "
          f"{report['team_name_mismatch_games']}")  # fmt: skip
    print("month      games  covered")
    for month, v in report["month_table"].items():
        print(f"{month}  {v['games']:>5}  {v['covered']:>7}")
    print(f"VERDICT: {report['verdict']}")


def _fmt(x: float, digits: int) -> str:
    return "nan" if math.isnan(x) else f"{x:.{digits}f}"


def _print_results(title: str, results: Sequence[ConfigResult]) -> None:
    print(f"\n--- {title} ---")
    header = (
        f"{'config':<22}{'n':>5}{'MAE':>8}{'Brier':>8}{'Acc%':>7}{'nzGap':>6}{'|gap|':>7}"
        f"{'eN':>5}{'eMAE':>8}{'eBrier':>8}{'eAcc%':>7}"
    )
    print(header)
    for r in results:
        print(
            f"{r.key:<22}{r.n:>5}{_fmt(r.mae_margin, 3):>8}{_fmt(r.brier, 4):>8}"
            f"{_fmt(100 * r.accuracy, 1):>7}{r.nonzero_games:>6}{_fmt(r.mean_abs_gap, 2):>7}"
            f"{r.elig_n:>5}{_fmt(r.elig_mae_margin, 3):>8}{_fmt(r.elig_brier, 4):>8}"
            f"{_fmt(100 * r.elig_accuracy, 1):>7}"
        )


def _jsonable(value: Any) -> Any:
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        return _jsonable(asdict(value))
    return value


# ----------------------------------------------------------------------------- orchestration


def _build_context(
    all_games: list[GameRecord], box: BoxData, replacement_levels: tuple[float, ...]
) -> EvalContext:
    event_start = {g.event_id: g.event_start_utc for g in all_games}
    schedule: dict[str, list[tuple[str, str]]] = defaultdict(list)
    covered: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for g in all_games:  # all_games is sorted by start
        for team in (g.home_team, g.away_team):
            schedule[team].append((g.event_start_utc, g.event_id))
            if box.rows_by_event.get(g.event_id, {}).get(team):
                covered[team].append((g.event_start_utc, g.event_id))
    return EvalContext(
        all_games=all_games,
        engine=NBAStructuralV7Engine(),
        rows_by_event=box.rows_by_event,
        team_schedule=dict(schedule),
        team_covered=dict(covered),
        player_index=build_player_index(box.rows_by_event, event_start),
        replacement_levels=replacement_levels,
    )


def run(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--out", required=True, help="JSON result path (must be outside the repo)")
    parser.add_argument("--smoke", action="store_true", help="validation-only, first dates, no holdout")
    args = parser.parse_args(argv)

    out_path = Path(args.out).resolve()
    if out_path.is_relative_to(REPO_ROOT.resolve()):
        print(f"refusing --out inside the repo: {out_path}", file=sys.stderr)
        return EXIT_OUT_IN_REPO

    started = time.time()
    all_games = load_nba_games(GAMES_PATH)
    raw_games = _read_raw_games(GAMES_PATH)
    print("loading boxscores (1.1 GB, one pass) ...", flush=True)
    box = load_boxscores_with_dates(BOX_DIR)
    print(f"  loaded {len(box.rows_by_event)} boxscores in {time.time() - started:.0f}s", flush=True)

    lag = data_lag_report(box, raw_games, datetime.now(UTC).date())
    _print_data_lag(lag)

    _train, val_games, _holdout, meta = split_complete_dates(all_games, 0.60, 0.20)
    print("\nsplit:", meta)

    if not args.smoke:
        raise SystemExit("full run is added in Task 2")
    # Mid-validation dates, not the first ones: the first validation dates are the season opener,
    # where every team's last game is a preseason game with no boxscore, so nothing would be
    # exercised beyond the "uncovered" branch.
    all_val_dates = sorted({g.event_start_utc[:10] for g in val_games})
    start = len(all_val_dates) // 2
    val_dates = all_val_dates[start : start + SMOKE_DATES]
    smoke_games = [g for g in val_games if g.event_start_utc[:10] in set(val_dates)]
    levels = (1.0,)
    ctx = _build_context(all_games, box, levels)
    configs = [
        ConfigSpec("zero", "zero", 0.0),
        ConfigSpec(f"static_k{STATIC_BASELINE_K}", "static", STATIC_BASELINE_K),
        ConfigSpec("real_r1.0_k1.0", "real", 1.0, 1.0),
    ]
    output = evaluate(ctx, smoke_games, configs, verify_batch=True)
    _print_results(f"SMOKE ({len(val_dates)} validation dates)", list(output.results.values()))
    print("diagnostics:", output.diagnostics)

    payload = {"data_lag": lag, "limitation": LIMITATION, "split_meta": meta, "smoke": output.results,
               "diagnostics": output.diagnostics}  # fmt: skip
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(_jsonable(payload), indent=2), encoding="utf-8")
    print(f"\nwrote {out_path} in {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(run())
