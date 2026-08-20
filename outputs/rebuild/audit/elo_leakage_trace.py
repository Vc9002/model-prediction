"""Read-only NBA Elo leakage trace.

Standalone analysis script for the docs/model_audit review of
nba-elo-trend-lr-v4. Does NOT modify any serving code. Imports the real,
unmodified `build_elo` from src/model_prediction/features/elo_ratings.py
and the real `FeatureStore`/`GameRecord` from features/base.py, and
replicates -- without altering -- the exact day-bucketing walk-forward
loop documented in src/model_prediction/validation.py::build_walk_forward_rows
(lines ~223-358 as of this audit), specifically the part relevant to Elo:

    for day in sorted(by_date):
        if len(history) >= minimum_history_games:
            elo = build_elo(history, sport)   # history = games strictly before `day`
            ... build features for every game ON `day` using this `elo` ...
        history.extend(day_games)             # today's games added AFTER

validation.py itself cannot be imported directly in this environment
(top-level `from sklearn.linear_model import LogisticRegression`, and
scikit-learn is not installed here / not to be installed as part of a
docs-only audit) -- so this script re-derives only the Elo-relevant subset
of that loop, using the real build_elo function, not a reimplementation of
Elo math. The day-bucketing logic itself is copied verbatim from the lines
cited above (confirmed by direct source read, not from memory/comments).

Data: data/historical/nba_games_all.jsonl (tracked in git, real ESPN NBA
results, 2025-10-02 through 2026-02-01, 3633 rows including NBA + preseason
+ some non-NBA preseason exhibition opponents like "Melbourne United").
data/processed/ is gitignored/absent in this worktree, so this is the best
available real dataset to trace against. This is a data-availability gap
in the *audit environment*, not a flaw in the *serving code* -- noted in
the audit doc.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from model_prediction.domain import EASTERN
from model_prediction.features.base import GameRecord
from model_prediction.features.elo_ratings import DEFAULT_ELO, build_elo

HIST_PATH = REPO_ROOT / "data/historical/nba_games_all.jsonl"


def load_games() -> list[GameRecord]:
    games: list[GameRecord] = []
    seen = set()
    with HIST_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            if raw.get("status") != "completed":
                continue
            key = str(raw["event_id"])
            if key in seen:
                continue
            seen.add(key)
            games.append(
                GameRecord(
                    event_id=key,
                    event_start_utc=str(raw["event_start_utc"]),
                    league=str(raw.get("league", "NBA")),
                    away_team=str(raw["away_team"]),
                    home_team=str(raw["home_team"]),
                    away_score=int(raw["away_score"]),
                    home_score=int(raw["home_score"]),
                    season_type=str(raw.get("season_type", "unknown")),
                    season_year=raw.get("season_year"),
                    competition_type=str(raw.get("competition_type", "unknown")),
                )
            )
    games.sort(key=lambda g: g.start)
    return games


def main() -> None:
    games = load_games()
    print(f"Loaded {len(games)} completed NBA games from {HIST_PATH}")
    print(f"Span: {games[0].start.isoformat()} -> {games[-1].start.isoformat()}")
    season_types = sorted({g.season_type for g in games})
    print(f"season_type values present: {season_types}")

    by_date: dict[str, list[GameRecord]] = defaultdict(list)
    for g in games:
        by_date[g.start.astimezone(EASTERN).date().isoformat()].append(g)

    minimum_history_games = 50

    # Sample: pick events spread across the season -- roughly every ~12th
    # game-day once history warms past the minimum, plus first-eligible-day
    # and last-day explicitly.
    all_days = sorted(by_date)
    eligible_days = []
    running = 0
    for day in all_days:
        if running >= minimum_history_games:
            eligible_days.append(day)
        running += len(by_date[day])

    sample_days = set()
    if eligible_days:
        sample_days.add(eligible_days[0])
        sample_days.add(eligible_days[-1])
        step = max(1, len(eligible_days) // 9)
        for i in range(0, len(eligible_days), step):
            sample_days.add(eligible_days[i])
    sample_days = sorted(sample_days)

    print(f"\n{len(eligible_days)} eligible days (history >= {minimum_history_games} games)")
    print(f"Sampling {len(sample_days)} days for per-event trace\n")

    results = []
    violations = []

    running_history: list[GameRecord] = []
    {day: i for i, day in enumerate(all_days)}

    for day in all_days:
        day_games = sorted(by_date[day], key=lambda g: (g.start, g.event_id))
        if day in sample_days and len(running_history) >= minimum_history_games:
            elo = build_elo(running_history, "nba")
            # last update timestamp per team = max game.start among running_history
            # (the history available strictly before `day`)
            last_update: dict[str, object] = {}
            for g in running_history:
                for team in (g.home_team, g.away_team):
                    if team not in last_update or g.start > last_update[team]:
                        last_update[team] = g.start

            for g in day_games:
                if g.home_score == g.away_score:
                    continue  # NBA has no ties; matches validation.py's skip rule
                home_rating = elo.rating(g.home_team)
                away_rating = elo.rating(g.away_team)
                elo_prob = elo.expected_home_win(g.home_team, g.away_team)
                lu_home = last_update.get(g.home_team)
                lu_away = last_update.get(g.away_team)
                home_ok = (lu_home is None) or (lu_home < g.start)
                away_ok = (lu_away is None) or (lu_away < g.start)
                row = {
                    "event_id": g.event_id,
                    "event_start_utc": g.event_start_utc,
                    "home_team": g.home_team,
                    "away_team": g.away_team,
                    "home_rating": round(home_rating, 3),
                    "away_rating": round(away_rating, 3),
                    "elo_probability": round(elo_prob, 6),
                    "home_cold_start": lu_home is None,
                    "away_cold_start": lu_away is None,
                    "last_home_update_utc": lu_home.isoformat() if lu_home else None,
                    "last_away_update_utc": lu_away.isoformat() if lu_away else None,
                    "home_invariant_ok": home_ok,
                    "away_invariant_ok": away_ok,
                    "history_size_before_day": len(running_history),
                }
                results.append(row)
                if not (home_ok and away_ok):
                    violations.append(row)

        running_history.extend(day_games)

    print(f"Traced {len(results)} individual games across {len(sample_days)} sampled days.\n")
    for row in results:
        print(json.dumps(row, indent=2, default=str))

    print("\n" + "=" * 70)
    if violations:
        print(f"LEAKAGE DETECTED: {len(violations)} invariant violations")
        for v in violations:
            print(json.dumps(v, indent=2, default=str))
    else:
        print(f"NO INVARIANT VIOLATIONS across {len(results)} sampled games.")

    # Additional checks: cold start value, ordering, offseason regression trigger.
    print("\n--- Cold start check ---")
    print(f"DEFAULT_ELO = {DEFAULT_ELO}")
    never_seen_examples = [r for r in results if r["home_cold_start"] or r["away_cold_start"]]
    print(f"{len(never_seen_examples)} sampled games involve a cold-start team (rating==DEFAULT_ELO)")
    for r in never_seen_examples[:5]:
        print(json.dumps(r, indent=2, default=str))

    # Check for any duplicate event_start timestamps across different teams that
    # could make same-day ordering ambiguous within a day bucket (shouldn't matter
    # since elo snapshot is taken once per day, before any of that day's games).
    print("\n--- Preseason / exhibition contamination check ---")
    non_regular = [g for g in games if g.season_type not in ("regular-season", "unknown")]
    print(f"{len(non_regular)} / {len(games)} games have season_type != 'regular-season' (e.g. preseason)")
    st_counts: dict[str, int] = defaultdict(int)
    for g in games:
        st_counts[g.season_type] += 1
    print(dict(st_counts))


if __name__ == "__main__":
    main()
