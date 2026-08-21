"""Per-league vs global-incumbent holdout evaluation (soccer split P2).

Day-by-day walk-forward over the locked holdout (last 20% of distinct
match dates) for each of the six named leagues:

  - INCUMBENT: models.soccer.SoccerModel (soccer-poisson-dc-v1), called
    exactly as production calls it -- ALL soccer history before the day
    (no league filter), baseline/home-boost measured from that pooled
    history at call time, fixed DC_RHO=-0.10.
  - CANDIDATE: model_prediction.soccer.LeagueSoccerModel with that
    league's independently fitted config (frozen per-league baseline /
    home advantage / rho) and that league's OWN history only.

Primary metrics, raw probabilities (no threshold replay -- the incumbent's
documented 66.7%-hit-rate number came from a threshold-gated totals
qualification, a different question than this raw-probability proper-score
comparison):
  - 3-way moneyline log-loss (draws included -- this is what DC rho
    affects, so this is the headline)
  - binary home-win Brier (comparable to the other sports' evaluations)
  - over-2.5 totals Brier (the market soccer actually logs)
Pooled across the six leagues + per-league breakdown + a date-cluster
bootstrap on the pooled 3-way-log-loss delta (seed 20260818).

The holdout dates here are computed per league from the local cache; the
fit script (soccer_league_split_fit.py) only ever used TRAIN+VALIDATION
for its numbers, so the comparison is clean. No production paths touched.
"""

from __future__ import annotations

import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model_prediction.config import PROJECT_ROOT
from model_prediction.models.soccer import SoccerModel, UpcomingMatch
from model_prediction.soccer.registry import model_for, named_league_codes

GAMES_PATH = Path("/Users/vincentc9002/model-prediction/data/processed/soccer/games.jsonl")
LEAGUES = named_league_codes()


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


def _three_way_log_loss(probs: list[dict], rows: list[_Record]) -> float:
    total = 0.0
    n = 0
    for prob, row in zip(probs, rows, strict=True):
        if row.home_score > row.away_score:
            p = prob["home_win"]
        elif row.home_score == row.away_score:
            p = prob["draw"]
        else:
            p = prob["away_win"]
        total += -math.log(min(1 - 1e-6, max(1e-6, p)))
        n += 1
    return total / n if n else float("nan")


def _binary_brier(probs: list[dict], rows: list[_Record]) -> float:
    return mean(
        (p["home_win"] - (1 if r.home_score > r.away_score else 0)) ** 2
        for p, r in zip(probs, rows, strict=True)
    )


def _totals_brier(probs: list[dict], rows: list[_Record]) -> float:
    return mean(
        (p["over_2_5"] - (1 if r.home_score + r.away_score > 2.5 else 0)) ** 2
        for p, r in zip(probs, rows, strict=True)
    )


def main() -> int:
    all_rows = _load_games()
    records_by_league: dict[str, list[_Record]] = defaultdict(list)
    for row in all_rows:
        records_by_league[row["league"]].append(_Record(row))

    global_model = SoccerModel()
    pooled: dict[str, list[float]] = defaultdict(list)
    per_league: dict[str, dict] = {}

    for league in LEAGUES:
        league_rows = [r for r in all_rows if r["league"] == league]
        league_rows.sort(key=lambda r: r["_date"])
        dates = sorted({r["_date"] for r in league_rows})
        train_count = max(1, int(len(dates) * 0.60))
        val_count = max(1, int(len(dates) * 0.20))
        holdout_start = dates[min(train_count + val_count, len(dates) - 1)]
        holdout_dates = [d for d in dates if d >= holdout_start]

        by_date: dict[str, list[_Record]] = defaultdict(list)
        for rec in records_by_league[league]:
            by_date[rec.event_start_utc[:10]].append(rec)
        global_by_date: dict[str, list[_Record]] = defaultdict(list)
        for recs in records_by_league.values():
            for rec in recs:
                global_by_date[rec.event_start_utc[:10]].append(rec)

        candidate_model = model_for(league)
        league_history: list[_Record] = []
        global_history: list[_Record] = []
        inc_probs: list[dict] = []
        cand_probs: list[dict] = []
        eval_rows: list[_Record] = []

        # advance the global history through every date across ALL leagues
        # (not just this league's), exactly as production's pooled history does
        all_dates = sorted(
            {d for recs in records_by_league.values() for rec in recs for d in [rec.event_start_utc[:10]]}
        )
        for day in all_dates:
            day_global = global_by_date.get(day, [])
            day_league = by_date.get(day, [])
            if day in holdout_dates and day_league:
                cand_strengths = candidate_model._strengths(league_history)
                for rec in day_league:
                    inc_upcoming = UpcomingMatch(
                        event_id="eval",
                        event_start_utc=rec.event_start_utc,
                        away_team=rec.away_team,
                        home_team=rec.home_team,
                    )
                    # incumbent emits [moneyline, total, btts] per match --
                    # predict once per game, take the first two
                    inc_predictions = global_model.predict_games(global_history, [inc_upcoming])
                    inc_probs.append(
                        {
                            "home_win": inc_predictions[0].probabilities["home"],
                            "away_win": inc_predictions[0].probabilities["away"],
                            "draw": inc_predictions[0].probabilities["draw"],
                            "over_2_5": inc_predictions[1].probabilities["over"],
                        }
                    )
                    cand = candidate_model.predict_one(cand_strengths, rec.home_team, rec.away_team)
                    cand_probs.append(
                        {
                            "home_win": cand["home_win"],
                            "away_win": cand["away_win"],
                            "draw": cand["draw"],
                            "over_2_5": cand["over_2_5"],
                        }
                    )
                    eval_rows.append(rec)
            league_history.extend(day_league)
            global_history.extend(day_global)

        inc_3way = _three_way_log_loss(inc_probs, eval_rows)
        cand_3way = _three_way_log_loss(cand_probs, eval_rows)
        inc_ml_brier = _binary_brier(inc_probs, eval_rows)
        cand_ml_brier = _binary_brier(cand_probs, eval_rows)
        inc_tot_brier = _totals_brier(inc_probs, eval_rows)
        cand_tot_brier = _totals_brier(cand_probs, eval_rows)
        per_league[league] = {
            "n": len(eval_rows),
            "incumbent_3way_log_loss": round(inc_3way, 6),
            "candidate_3way_log_loss": round(cand_3way, 6),
            "3way_log_loss_delta": round(cand_3way - inc_3way, 6),
            "incumbent_ml_brier": round(inc_ml_brier, 6),
            "candidate_ml_brier": round(cand_ml_brier, 6),
            "ml_brier_delta": round(cand_ml_brier - inc_ml_brier, 6),
            "incumbent_totals_brier": round(inc_tot_brier, 6),
            "candidate_totals_brier": round(cand_tot_brier, 6),
            "totals_brier_delta": round(cand_tot_brier - inc_tot_brier, 6),
        }
        # pooled deltas for the bootstrap: per-game 3way log-loss delta by date
        for prob_inc, prob_cand, row in zip(inc_probs, cand_probs, eval_rows, strict=True):
            if row.home_score > row.away_score:
                p_inc, p_cand = prob_inc["home_win"], prob_cand["home_win"]
            elif row.home_score == row.away_score:
                p_inc, p_cand = prob_inc["draw"], prob_cand["draw"]
            else:
                p_inc, p_cand = prob_inc["away_win"], prob_cand["away_win"]
            loss_inc = -math.log(min(1 - 1e-6, max(1e-6, p_inc)))
            loss_cand = -math.log(min(1 - 1e-6, max(1e-6, p_cand)))
            pooled["delta"].append(loss_cand - loss_inc)
            pooled["date"].append(row.event_start_utc[:10])

        print(f"{league}: {per_league[league]}")

    by_date_pooled: dict[str, list[float]] = defaultdict(list)
    for delta, day in zip(pooled["delta"], pooled["date"], strict=True):
        by_date_pooled[day].append(delta)
    dates_sorted = sorted(by_date_pooled)
    observed = mean(pooled["delta"])
    rng = random.Random(20260818)
    samples = []
    for _ in range(2000):
        sampled = [rng.choice(dates_sorted) for _ in dates_sorted]
        samples.append(mean(v for d in sampled for v in by_date_pooled[d]))
    samples.sort()
    p_better = sum(1 for s in samples if s < 0) / 2000

    pooled_inc_3way = mean(p["incumbent_3way_log_loss"] for p in per_league.values())
    pooled_cand_3way = mean(p["candidate_3way_log_loss"] for p in per_league.values())

    report = {
        "n_games_total": sum(p["n"] for p in per_league.values()),
        "per_league": per_league,
        "pooled": {
            "mean_3way_log_loss_delta": round(observed, 6),
            "candidate_mean_3way_log_loss": round(pooled_cand_3way, 6),
            "incumbent_mean_3way_log_loss": round(pooled_inc_3way, 6),
            "cluster_bootstrap": {
                "p_better": round(p_better, 4),
                "ci_2_5": round(samples[49], 6),
                "ci_97_5": round(samples[1949], 6),
                "n_dates": len(dates_sorted),
            },
        },
        "verdict": ("promote_candidate" if observed < -0.002 else "reject_or_inconclusive"),
        "note": (
            "Raw-probability proper-score comparison; promotion of any of "
            "these per-league models into soccer_forward.py/production "
            "requires the threshold-gated qualification that the incumbent "
            "totals model went through (qualify_soccer_total_model), plus "
            "the operator promotion decision -- neither happens from this "
            "script."
        ),
    }
    out_path = PROJECT_ROOT / "outputs/research/soccer_league_split/holdout_eval.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    print(f"\nwritten to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
