"""Read-only chronological NCAAB margin calibration probe."""

import json
from collections import defaultdict
from pathlib import Path
from time import monotonic

from model_prediction.features.base import FeatureStore
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.basketball_leagues import ncaab_v3_model

root = Path("data")
raw = [
    json.loads(line)
    for line in (root / "processed/ncaab/games.jsonl").read_text(encoding="utf-8").splitlines()
    if line
]
by_day = defaultdict(list)
for row in raw:
    day = row["event_start_utc"][:10]
    if "2025-11-01" <= day <= "2026-04-30":
        by_day[day].append(row)
result = []
start_time = monotonic()
for index, (day, games) in enumerate(sorted(by_day.items()), 1):
    history = FeatureStore(root).games_before("ncaab", day)
    if not history:
        continue
    upcoming = [
        UpcomingGame(str(g["event_id"]), g["event_start_utc"], g["away_team"], g["home_team"], league="NCAAB")
        for g in games
    ]
    predicted = ncaab_v3_model().predict_games(history, upcoming)
    lookup = {p.event_id: p for p in predicted if p.market_type == "moneyline"}
    for game in games:
        pred = lookup.get(str(game["event_id"]))
        if pred:
            f = pred.feature_basis
            result.append(
                {
                    "event_id": game["event_id"],
                    "date": day,
                    "expected_margin": f["expected_margin"],
                    "actual_margin": game["home_score"] - game["away_score"],
                    "expected_total": f["expected_total"],
                    "actual_total": game["home_score"] + game["away_score"],
                    "elo_margin": f["elo_margin"],
                    "trend_margin": f["trend_margin"],
                }
            )
    if index % 20 == 0:
        print(index, len(by_day), len(result), round(monotonic() - start_time, 1), flush=True)
out = Path("scratch/ncaab_overhaul_training.jsonl")
out.write_text("".join(json.dumps(x) + "\n" for x in result), encoding="utf-8")
print("WROTE", out, len(result), "seconds", round(monotonic() - start_time, 1), flush=True)
