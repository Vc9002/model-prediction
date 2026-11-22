"""EXPLORATORY, READ-ONLY walk-forward replay of the served NBA v9 structural engine.

For every ET game date D: history = FeatureStore.games_before("nba", D) (the production
PIT chokepoint, ET-midnight cutoff), upcoming = D's games, priced by the exact
NBAStructuralV9Engine built from the frozen config/models/nba-spread-margin-v9.json artifact.
Closing lines (Kaggle archive, data/odds/nba/historical_closing_lines) are attached for
scoring only; they never enter the engine's mean (market_blend_weight=0.0).

Availability adjustments are NOT replayed (live-only injury snapshots), so
availability_points_gap = availability_total_loss = 0 for every row.

Also records the decomposition (elo margin, trend margin, rest, fatigue, pace) by
re-deriving it with the same objects, parity vs engine output is checked in 02_diagnostics.py section 8e.

Output: rows.jsonl in this folder. Nothing is written under the repo or runtime root.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REPO = Path("e:/MP")
sys.path.insert(0, str(REPO / "src"))

from model_prediction.domain import EASTERN, parse_utc
from model_prediction.features.base import FeatureStore
from model_prediction.features.elo_ratings import build_elo
from model_prediction.features.trends import TrendEngine
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.nba_v9 import NBAStructuralV9Engine

OUT = Path(__file__).resolve().parent / "rows.jsonl"
ODDS = REPO / "data/odds/nba/historical_closing_lines/nba_closing_lines_2024_2026.jsonl"
MIN_HISTORY = 200


def load_lines() -> dict[str, dict[str, float]]:
    out = {}
    for line in ODDS.open(encoding="utf-8"):
        r = json.loads(line)
        try:
            s = float(r["spread"])
            t = float(r["total"])
        except (TypeError, ValueError):
            continue
        # Home-margin line: +s when home favoured. spread_away_line in UpcomingGame is the
        # away handicap (+6.5 = away gets 6.5), away covers iff home_margin < line,
        # so spread_away_line == home-margin line.
        hm = s if r.get("whos_favored") == "home" else -s
        out[str(r["event_id"])] = {"home_margin_line": hm, "total_line": t}
    return out


def main() -> None:
    artifact = json.loads((REPO / "config/models/nba-spread-margin-v9.json").read_text())
    tot_art = json.loads((REPO / "config/models/nba-total-margin-v9.json").read_text())
    assert tot_art["total_sd"] == artifact["total_sd"]
    engine = NBAStructuralV9Engine.from_artifact(artifact)
    store = FeatureStore(REPO / "data")
    games = store.load_games("nba")
    lines = load_lines()
    by_date: dict[str, list] = defaultdict(list)
    for g in games:
        by_date[g.start.astimezone(EASTERN).date().isoformat()].append(g)

    n_parity_fail = 0
    with OUT.open("w", encoding="utf-8") as fh:
        for day in sorted(by_date):
            history = store.games_before("nba", day)
            if len(history) < MIN_HISTORY:
                continue
            day_games = [g for g in by_date[day] if g.season_type not in {"preseason"}]
            if not day_games:
                continue
            upcoming = []
            for g in day_games:
                ln = lines.get(g.event_id)
                upcoming.append(
                    UpcomingGame(
                        event_id=g.event_id,
                        event_start_utc=g.event_start_utc,
                        away_team=g.away_team,
                        home_team=g.home_team,
                        spread_away_line=ln["home_margin_line"] if ln else None,
                        total_line=ln["total_line"] if ln else None,
                    )
                )
            preds = engine.predict_games(history, upcoming)
            byev: dict[str, dict] = defaultdict(dict)
            for p in preds:
                byev[p.event_id][p.market_type] = p

            # Decomposition with the same objects the engine builds.
            elo = build_elo(
                history,
                "nba",
                offseason_gap_days=int(engine.offseason_gap_days),
                home_advantage=engine.elo_home_advantage,
            )
            trend = TrendEngine(
                history,
                offseason_gap_days=engine.offseason_gap_days,
                offseason_retention=engine.offseason_retention,
                isolate_postseason=engine.isolate_postseason,
            )
            baseline = trend.league_baseline or engine.baseline_points_per_team
            # Current-season mean points per team among history (season_year of day games).
            for g in day_games:
                ml = byev[g.event_id]["moneyline"]
                fb = ml.feature_basis
                ht, at = trend.team_trend(g.home_team), trend.team_trend(g.away_team)
                h_off, h_def = ht.offense["hl10"], ht.defense["hl10"]
                a_off, a_def = at.offense["hl10"], at.defense["hl10"]
                home_pts, away_pts = (h_off + a_def) / 2, (a_off + h_def) / 2
                elo_margin = (elo.rating(g.home_team) + elo.home_advantage - elo.rating(g.away_team)) / 28.0
                gs = parse_utc(g.event_start_utc)

                def last_start(team: str) -> datetime | None:
                    best = None
                    for p in history:
                        if p.start < gs and (p.home_team == team or p.away_team == team):
                            if best is None or p.start > best:
                                best = p.start
                    return best

                hl, al = last_start(g.home_team), last_start(g.away_team)
                h_rest = (gs - hl).total_seconds() / 86400 if hl else 3.0
                a_rest = (gs - al).total_seconds() / 86400 if al else 3.0
                ln = lines.get(g.event_id)
                sp = byev[g.event_id].get("spread")
                to = byev[g.event_id].get("total")
                row = {
                    "date": day,
                    "event_id": g.event_id,
                    "season_year": g.season_year,
                    "season_type": g.season_type,
                    "home": g.home_team,
                    "away": g.away_team,
                    "home_score": g.home_score,
                    "away_score": g.away_score,
                    "exp_margin": fb["expected_margin"],
                    "exp_total": fb["expected_total"],
                    "p_home_ml": ml.probabilities["home"],
                    "est_poss": fb["est_possessions"],
                    "cs_games_min": fb["current_season_games_min"],
                    "cs_games_home": ht.current_season_games_played,
                    "cs_games_away": at.current_season_games_played,
                    "elo_margin": elo_margin,
                    "trend_margin_raw": home_pts - away_pts,
                    "home_pts_trend": home_pts,
                    "away_pts_trend": away_pts,
                    "league_baseline": baseline,
                    "h_rest": h_rest,
                    "a_rest": a_rest,
                    "line_margin": ln["home_margin_line"] if ln else None,
                    "line_total": ln["total_line"] if ln else None,
                    "p_away_cover": sp.probabilities["away"] if sp else None,
                    "p_over": to.probabilities["over"] if to else None,
                    "history_n": len(history),
                }
                fh.write(json.dumps(row) + "\n")
            print(day, len(day_games), flush=True) if day.endswith("-01") else None
    print("done")


if __name__ == "__main__":
    main()
