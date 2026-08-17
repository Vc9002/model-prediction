"""MLB v9 Step 7 (Phase 6) -- run-distribution method backtest.

Compares the incumbent joint score distribution (gamma_poisson) against the
negative_binomial and independent_poisson challengers on REAL historical
games, using ONLY local cached data (no live API calls):

  - team form (last-10-game trailing runs_scored/runs_allowed): reconstructed
    from data/historical/mlb_games_all.jsonl, mirroring
    data_sources/espn.py::parse_team_form's exact window/logic.
  - starter form (season + last-5-start innings/ER/K/BB/BF): reconstructed
    from data/mlb_statsapi/game_snapshots.jsonl, mirroring
    data_sources/espn.py::parse_pitcher_form's exact aggregation. "Season" is
    approximated as the decision date's calendar year (ESPN's gamelog is
    season-scoped; the local snapshot file is not season-partitioned).
  - bullpen: reuses features/bullpen.py::bullpen_profile +
    team_recent_relief_lines directly (already local-only, PIT-correct).
  - park: reuses features/park_factors.py::park_factor directly (the static
    table v8/production currently use -- this backtest isolates the
    DISTRIBUTION METHOD, not the park-factor PIT question already tested as
    Step 3 variant K).
  - weather: fixed neutral (1.0, status=unavailable_from_source). Disclosed
    simplification -- avoids any live Open-Meteo calls. Applied identically
    to every game and every method, so it cannot bias the METHOD comparison
    (it shifts the mean run estimate equally under gamma_poisson, NB, and
    independent_poisson for a given game).

Unlike the Step 3 ablation matrix, this is NOT a fit/predict comparison --
simulate_game() doesn't learn from data, it draws from a fixed distributional
family around the SAME RunEstimate for every method. There is therefore no
train/test leakage risk and no walk-forward CV requirement here: this is a
single pooled evaluation of "which distributional assumption is best
calibrated against real outcomes," not a model-fitting ablation. A
date-cluster bootstrap (reused from roadmap_challenger.py) is still used to
judge whether an observed Brier delta is real or noise.

Usage:
    python scripts/mlb_v9_distribution_backtest.py --min-date 2025-04-01
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from dataclasses import replace as _dc_replace
from datetime import datetime
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.calibration import calibration_metrics
from model_prediction.config import PROJECT_ROOT
from model_prediction.domain import parse_utc
from model_prediction.features.bullpen import (
    bullpen_profile,
    team_recent_relief_lines,
)
from model_prediction.features.park_factors import park_factor
from model_prediction.models.mlb import (
    DISTRIBUTION_METHODS,
    MLBGameFeatures,
    PitcherForm,
    TeamForm,
    compare_distribution_methods,
    estimate_runs,
    feature_hash,
    load_formula_spec,
)
from model_prediction.roadmap_challenger import _cluster_bootstrap_brier_delta

GAMES_PATH = PROJECT_ROOT / "data/historical/mlb_games_all.jsonl"
SNAPSHOTS_PATH = PROJECT_ROOT / "data/mlb_statsapi/game_snapshots.jsonl"
FORMULA_SPEC_PATH = PROJECT_ROOT / "config/models/mlb-analyst-poisson-trend-v0.3.yaml"


def _baseball_innings(value: object) -> float:
    try:
        whole, _, outs = str(value).partition(".")
        return int(whole) + (int(outs or 0) / 3)
    except (TypeError, ValueError):
        return 0.0


def _normalize_name(name: str) -> str:
    return "".join(c.lower() for c in name if c.isalnum())


def load_games(path: Path) -> list[dict]:
    games = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("status") == "completed":
                games.append(row)
    games.sort(key=lambda r: r["event_start_utc"])
    return games


def build_team_history(games: list[dict]) -> dict[str, list[tuple[datetime, int, int]]]:
    """team -> chronological (date, team_score, opp_score)."""
    history: dict[str, list[tuple[datetime, int, int]]] = defaultdict(list)
    for game in games:
        start = parse_utc(game["event_start_utc"])
        history[game["home_team"]].append((start, game["home_score"], game["away_score"]))
        history[game["away_team"]].append((start, game["away_score"], game["home_score"]))
    for entries in history.values():
        entries.sort(key=lambda item: item[0])
    return history


def team_form_at(
    history: dict[str, list[tuple[datetime, int, int]]], team: str, decision: datetime
) -> TeamForm:
    entries = [e for e in history.get(team, []) if e[0] < decision]
    recent = entries[-10:]
    return TeamForm(
        runs_scored=tuple(e[1] for e in recent),
        runs_allowed=tuple(e[2] for e in recent),
        wins=sum(1 for e in recent if e[1] > e[2]),
        losses=sum(1 for e in recent if e[1] < e[2]),
        status="available" if recent else "unavailable_from_source",
    )


# (date, innings, earned_runs, strikeouts, walks, batters_faced, player_id, name, pitch_hand)
_StarterRow = tuple[datetime, float, int, int, int, int, str, str, str | None]


def build_starter_history(path: Path) -> dict[str, list[_StarterRow]]:
    index: dict[str, list[_StarterRow]] = defaultdict(list)
    if not path.exists():
        return index
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                snap = json.loads(line)
                game_start = parse_utc(str(snap["game_start_utc"]))
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
            for side_key in ("home", "away"):
                side = snap.get(side_key) or {}
                order = side.get("pitcher_order") or []
                if not order:
                    continue
                starter_id = order[0]
                starter = next((p for p in side.get("players", []) if p.get("player_id") == starter_id), None)
                if not starter or not starter.get("pitching") or "inningsPitched" not in starter["pitching"]:
                    continue
                pitching = starter["pitching"]
                name = starter.get("name")
                if not name:
                    continue
                row: _StarterRow = (
                    game_start,
                    _baseball_innings(pitching.get("inningsPitched")),
                    int(pitching.get("earnedRuns") or 0),
                    int(pitching.get("strikeOuts") or 0),
                    int(pitching.get("baseOnBalls") or 0),
                    int(pitching.get("battersFaced") or 0),
                    str(starter.get("player_id") or ""),
                    name,
                    starter.get("pitch_hand"),
                )
                index[_normalize_name(name)].append(row)
    for rows in index.values():
        rows.sort(key=lambda item: item[0])
    return index


def pitcher_form_at(
    index: dict[str, list[_StarterRow]], starter_name: str, decision: datetime
) -> PitcherForm | None:
    """None means this starter can't be resolved from local history -- caller skips the game."""
    rows = index.get(_normalize_name(starter_name), [])
    season_rows = [r for r in rows if r[0] < decision and r[0].year == decision.year]
    if not season_rows:
        return None
    last_five = season_rows[-5:]
    latest = season_rows[-1]
    return PitcherForm(
        player_id=latest[6],
        name=latest[7],
        throwing_hand=latest[8],
        starts_before_game=len(season_rows),
        season_innings=sum(r[1] for r in season_rows),
        season_earned_runs=sum(r[2] for r in season_rows),
        season_strikeouts=sum(r[3] for r in season_rows),
        season_walks=sum(r[4] for r in season_rows),
        season_batters_faced=sum(r[5] for r in season_rows),
        last_five_innings=sum(r[1] for r in last_five),
        last_five_earned_runs=sum(r[2] for r in last_five),
        last_five_strikeouts=sum(r[3] for r in last_five),
        last_five_walks=sum(r[4] for r in last_five),
        last_five_batters_faced=sum(r[5] for r in last_five),
    )


def game_starters(snapshot: dict, side_key: str) -> str | None:
    side = snapshot.get(side_key) or {}
    order = side.get("pitcher_order") or []
    if not order:
        return None
    starter = next((p for p in side.get("players", []) if p.get("player_id") == order[0]), None)
    return starter.get("name") if starter else None


def load_snapshot_starters(path: Path) -> dict[str, tuple[str | None, str | None]]:
    """Key by (game_start_utc[:16], home_team, away_team) -- team names live
    nested at snapshot["home"]["team_name"] / ["away"]["team_name"], not at
    the top level -- to (home_starter, away_starter)."""
    out: dict[str, tuple[str | None, str | None]] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                snap = json.loads(line)
            except json.JSONDecodeError:
                continue
            home_name = (snap.get("home") or {}).get("team_name", "")
            away_name = (snap.get("away") or {}).get("team_name", "")
            key = str(snap.get("game_start_utc", ""))[:16] + "|" + home_name + "|" + away_name
            out[key] = (game_starters(snap, "home"), game_starters(snap, "away"))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-date", default="2025-04-01")
    parser.add_argument("--max-games", type=int, default=None)
    args = parser.parse_args()

    games = load_games(GAMES_PATH)
    team_history = build_team_history(games)
    starter_history = build_starter_history(SNAPSHOTS_PATH)
    snapshot_starters = load_snapshot_starters(SNAPSHOTS_PATH)
    spec = load_formula_spec(FORMULA_SPEC_PATH)

    pooled: dict[str, list[float]] = {method: [] for method in DISTRIBUTION_METHODS}
    outcomes: list[int] = []
    dates: list[str] = []
    skipped = {"no_starter_snapshot": 0, "starter_unresolved": 0, "team_form_unavailable": 0}

    for game in games:
        date_str = game["event_start_utc"][:10]
        if date_str < args.min_date:
            continue
        start = parse_utc(game["event_start_utc"])
        home_name, away_name = game["home_team"], game["away_team"]
        snap_key = date_str + "T" + game["event_start_utc"][11:16] + "|" + home_name + "|" + away_name
        # game_start_utc in snapshots may not share exact minute; fall back to
        # a scan-free best-effort match on the same key format used above.
        starters = snapshot_starters.get(snap_key)
        if starters is None:
            skipped["no_starter_snapshot"] += 1
            continue
        home_starter_name, away_starter_name = starters
        if not home_starter_name or not away_starter_name:
            skipped["no_starter_snapshot"] += 1
            continue

        home_form = team_form_at(team_history, home_name, start)
        away_form = team_form_at(team_history, away_name, start)
        if home_form.status != "available" or away_form.status != "available":
            skipped["team_form_unavailable"] += 1
            continue

        home_pitcher = pitcher_form_at(starter_history, home_starter_name, start)
        away_pitcher = pitcher_form_at(starter_history, away_starter_name, start)
        if home_pitcher is None or away_pitcher is None:
            skipped["starter_unresolved"] += 1
            continue

        park = park_factor(home_name)
        away_bullpen = bullpen_profile(team_recent_relief_lines(away_name, start))
        home_bullpen = bullpen_profile(team_recent_relief_lines(home_name, start))

        features = MLBGameFeatures(
            event_id=str(game["event_id"]),
            event_start_utc=game["event_start_utc"],
            decision_timestamp_utc=game["event_start_utc"],
            away_team=away_name,
            home_team=home_name,
            away_form=away_form,
            home_form=home_form,
            away_starter=away_pitcher,
            home_starter=home_pitcher,
            away_bullpen_weakness=away_bullpen["bullpen_weakness_index"],
            home_bullpen_weakness=home_bullpen["bullpen_weakness_index"],
            away_bullpen_status=away_bullpen["status"],
            home_bullpen_status=home_bullpen["status"],
            park_factor=park["park_factor"],
            park_factor_status=park["status"],
            weather_factor=1.0,
            weather_status="unavailable_from_source",
            starter_confirmed=True,
            starter_status="actual",
        )
        features = _dc_replace(features, feature_snapshot_hash=feature_hash(features))
        estimate = estimate_runs(features, spec)
        comparison = compare_distribution_methods(features, estimate, spec, methods=DISTRIBUTION_METHODS)

        home_won = 1 if game["home_score"] > game["away_score"] else 0
        outcomes.append(home_won)
        dates.append(date_str)
        for method in DISTRIBUTION_METHODS:
            pooled[method].append(comparison[method]["moneyline"].second_win_probability)

        if args.max_games and len(outcomes) >= args.max_games:
            break

    n = len(outcomes)
    report: dict = {"n_games": n, "skipped": skipped, "min_date": args.min_date, "methods": {}}
    for method in DISTRIBUTION_METHODS:
        report["methods"][method] = calibration_metrics(pooled[method], outcomes, minimum_sample=1)

    control = "gamma_poisson"

    class _Row:
        __slots__ = ("date", "outcome")

        def __init__(self, date: str, outcome: int) -> None:
            self.date = date
            self.outcome = outcome

    rows = [_Row(d, o) for d, o in zip(dates, outcomes, strict=True)]
    for method in DISTRIBUTION_METHODS:
        if method == control:
            continue
        bootstrap = _cluster_bootstrap_brier_delta(pooled[control], pooled[method], rows, seed=20260817)
        by_date: dict[str, list[float]] = defaultdict(list)
        for c, m, row in zip(pooled[control], pooled[method], rows, strict=True):
            by_date[row.date].append((m - row.outcome) ** 2 - (c - row.outcome) ** 2)
        rng = random.Random(20260817)
        ds = sorted(by_date)
        better = 0
        resamples = 2000
        for _ in range(resamples):
            sampled = [rng.choice(ds) for _ in ds]
            vals = [v for day in sampled for v in by_date[day]]
            if mean(vals) < 0:
                better += 1
        report["methods"][method]["vs_control_bootstrap"] = bootstrap
        report["methods"][method]["vs_control_p_better"] = round(better / resamples, 4)

    out_dir = PROJECT_ROOT / "outputs/research/mlb_v9_distribution"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"backtest_from{args.min_date}.json"
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report["_file"] = str(out_path)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
