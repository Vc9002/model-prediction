"""Fit per-league Poisson-Dixon-Coles parameters (docs/ROADMAP.md — per-league soccer directives).

For each of the six named leagues (EPL, LA_LIGA, BUNDESLIGA, SERIE_A, MLS,
UCL -- backlog's own priority order), using ONLY that league's own
historical games (data/processed/soccer/games.jsonl):

  - baseline: measured directly (mean goals/team across the league's own
    TRAIN+VALIDATION window -- never touches the locked holdout)
  - home_advantage: measured directly (home_goals_avg / away_goals_avg,
    same window)
  - dc_rho: grid-searched over a small candidate set on the VALIDATION
    split only, minimizing moneyline log-loss with baseline/home_advantage
    held fixed at their measured values -- this is the one genuinely
    "fit" (not just measured) parameter in this pass.

EWMA decay rate is intentionally NOT grid-searched here (see league_model.py's
module docstring) -- kept at the incumbent's existing defaults.

Prints the fitted config for each league so it can be hand-verified against
each league's frozen module (epl.py, la_liga.py, ...) before those modules
are treated as trustworthy -- this script is the derivation record, not
something re-run silently to auto-update the frozen numbers.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model_prediction.config import PROJECT_ROOT
from model_prediction.soccer.league_model import LeagueSoccerConfig, LeagueSoccerModel

GAMES_PATH = Path("/Users/vincentc9002/model-prediction/data/processed/soccer/games.jsonl")
LEAGUES = ("EPL", "LA_LIGA", "BUNDESLIGA", "SERIE_A", "MLS", "UCL")
RHO_GRID = (-0.20, -0.15, -0.10, -0.05, 0.0)
HOME_BOOST_DEFAULT = 1.15  # incumbent's hardcoded value, used only as a sanity comparison print


def _load_games() -> list[dict]:
    rows = []
    with GAMES_PATH.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("status") != "completed":
                continue
            row["_date"] = str(row["event_start_utc"])[:10]
            rows.append(row)
    return rows


class _Record:
    __slots__ = ("away_score", "away_team", "event_start_utc", "home_score", "home_team", "league")

    def __init__(self, row: dict) -> None:
        self.away_score = int(row["away_score"])
        self.home_score = int(row["home_score"])
        self.away_team = row["away_team"]
        self.home_team = row["home_team"]
        self.event_start_utc = row["event_start_utc"]
        self.league = row["league"]

    @property
    def start(self):
        from model_prediction.domain import parse_utc

        return parse_utc(self.event_start_utc)


def main() -> int:
    all_rows = _load_games()
    fitted = {}
    for league in LEAGUES:
        league_rows = [r for r in all_rows if r["league"] == league]
        league_rows.sort(key=lambda r: r["_date"])
        dates = sorted({r["_date"] for r in league_rows})
        if len(dates) < 30:
            print(f"{league}: too few distinct dates ({len(dates)}) -- skipped")
            continue
        train_count = max(1, int(len(dates) * 0.60))
        val_count = max(1, int(len(dates) * 0.20))
        holdout_idx = min(train_count + val_count, len(dates) - 1)
        val_start = dates[train_count]
        holdout_start = dates[holdout_idx]

        train_val_rows = [r for r in league_rows if r["_date"] < holdout_start]
        home_goals = [r["home_score"] for r in train_val_rows]
        away_goals = [r["away_score"] for r in train_val_rows]
        baseline = mean(home_goals + away_goals)
        home_advantage = mean(home_goals) / mean(away_goals) if mean(away_goals) else HOME_BOOST_DEFAULT

        records = [_Record(r) for r in league_rows]
        val_records = [
            rec
            for rec, r in zip(records, league_rows, strict=True)
            if val_start <= r["_date"] < holdout_start
        ]
        train_records_before_val = [
            rec for rec, r in zip(records, league_rows, strict=True) if r["_date"] < val_start
        ]

        def _three_way_log_loss(model: LeagueSoccerModel, strengths: dict, records: list[_Record]) -> float:
            """Proper 3-way log-loss (draws included). DC rho exists to fix
            the low-score cells -- especially draws -- so binary home-win
            log-loss would be the wrong selector for it."""
            import math

            total = 0.0
            n = 0
            for rec in records:
                result = model.predict_one(strengths, rec.home_team, rec.away_team)
                if rec.home_score > rec.away_score:
                    prob = result["home_win"]
                elif rec.home_score == rec.away_score:
                    prob = result["draw"]
                else:
                    prob = result["away_win"]
                total += -math.log(min(1 - 1e-6, max(1e-6, prob)))
                n += 1
            return total / n if n else float("inf")

        best_rho, best_loss = RHO_GRID[0], float("inf")
        for rho in RHO_GRID:
            cfg = LeagueSoccerConfig(
                league_code=league,
                model_version=f"soccer-{league.lower()}-poisson-dc-v1-candidate",
                baseline=baseline,
                home_advantage=home_advantage,
                dc_rho=rho,
            )
            model = LeagueSoccerModel(cfg)
            strengths = model._strengths(train_records_before_val)
            loss = _three_way_log_loss(model, strengths, val_records)
            if loss < best_loss:
                best_loss, best_rho = loss, rho

        fitted[league] = {
            "baseline": round(baseline, 4),
            "home_advantage": round(home_advantage, 4),
            "dc_rho": best_rho,
            "n_train_val_games": len(train_val_rows),
            "n_validation_games": len(val_records),
            "validation_log_loss": round(best_loss, 6),
        }
        print(f"{league}: {fitted[league]}")

    out_path = PROJECT_ROOT / "outputs/research/soccer_league_split/fitted_params.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(fitted, indent=2) + "\n", encoding="utf-8")
    print(f"\nwritten to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
