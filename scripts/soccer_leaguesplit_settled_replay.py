"""Soccer per-league split: settled-picks replay (complement to the holdout eval).

The holdout eval compared per-league candidates vs the global incumbent on
ALL games. This replays the per-league candidate on the games the system
ACTUALLY decided on: settled soccer totals picks (flat + main tiers, model
soccer-poisson-dc-v1), recomputing what each per-league config would have
said for the same event at the same decision time (league-only history
strictly before the event start, same game records the serving path uses).

For each settled pick: event_id joins to data/processed/soccer/games.jsonl
for the real competition code and scores; selection (over/under) and the
recorded incumbent probability come from the ledger payload. Candidate
probability = per-league model's P(over 2.5) at that event, flipped to the
selection side. Exact-line Brier on the settled outcomes (2.5 lines --
pushes are impossible), incumbent vs candidate, per league where n>=5.

Small-sample by construction (36 settled totals picks total); this is a
decision-record check, not a promotion gate on its own.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model_prediction.config import PROJECT_ROOT
from model_prediction.soccer.league_model import LeagueSoccerModel
from model_prediction.soccer.registry import resolve

DEFAULT_LEDGER_DB = Path("/Users/vincentc9002/model-prediction-runtime/ledgers/ledgers.db")
# Both local soccer history files are checked -- the processed file feeds
# FeatureStore (what soccer_forward.py's serving path actually reads) and the
# historical file is the Odds API-sourced accumulation. Verified 2026-08-18:
# ESPN-sourced soccer rows stop ~2026-07-19 in both, so late-July/August
# settled-pick events match NEITHER (see the replay script's own finding).
GAMES_PATHS = (
    Path("/Users/vincentc9002/model-prediction/data/processed/soccer/games.jsonl"),
    Path("/Users/vincentc9002/model-prediction/data/historical/soccer_games_all.jsonl"),
)


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


def _load_games() -> dict[str, dict]:
    by_id = {}
    for path in GAMES_PATHS:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("status") == "completed":
                    by_id[str(row["event_id"])] = row
    return by_id


def _fetch_settled(db_path: Path, tier: str) -> list[dict]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT event_id, event_start_utc, selection, model_probability, result, decision_payload_json
               FROM ledger_records
               WHERE sport = 'soccer' AND market_type = 'total' AND ledger_tier = ?
                 AND status = 'settled' AND result IN ('win', 'loss')
                 AND model_id = 'soccer-poisson-dc-v1'
                 AND model_probability IS NOT NULL
               ORDER BY event_start_utc""",
            (tier,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _candidate_prob(
    model: LeagueSoccerModel, history: list[_Record], home_team: str, away_team: str, selection: str
) -> float:
    strengths = model._strengths(history)
    result = model.predict_one(strengths, home_team, away_team)
    p_over = float(result["over_2_5"])
    return p_over if str(selection).lower() == "over" else 1 - p_over


def main() -> int:
    games_by_id = _load_games()
    all_rows = _fetch_settled(DEFAULT_LEDGER_DB, "flat") + _fetch_settled(DEFAULT_LEDGER_DB, "main")

    records_by_league: dict[str, list[_Record]] = {}
    for row in games_by_id.values():
        records_by_league.setdefault(row["league"], []).append(_Record(row))
    for recs in records_by_league.values():
        recs.sort(key=lambda r: r.event_start_utc)

    compared = []
    for row in all_rows:
        event_id = str(row["event_id"])
        game = games_by_id.get(event_id)
        if game is None:
            continue
        league = game["league"]
        model = LeagueSoccerModel(resolve(league))
        history = [
            rec
            for rec in records_by_league.get(league, [])
            if rec.event_start_utc < str(row["event_start_utc"])
        ]
        candidate = _candidate_prob(model, history, game["home_team"], game["away_team"], row["selection"])
        incumbent = float(row["model_probability"])
        outcome = 1 if row["result"] == "win" else 0
        compared.append(
            {
                "date": str(row["event_start_utc"])[:10],
                "league": league,
                "selection": row["selection"],
                "incumbent_prob": incumbent,
                "candidate_prob": round(candidate, 6),
                "outcome": outcome,
                "history_games": len(history),
            }
        )

    if not compared:
        print("no settled soccer totals picks matched to games.jsonl")
        return 0

    def _brier(probs: list[float]) -> float:
        return sum((p - r["outcome"]) ** 2 for p, r in zip(probs, compared, strict=True)) / len(compared)

    by_league: dict[str, list[dict]] = {}
    for row in compared:
        by_league.setdefault(row["league"], []).append(row)

    per_league = {}
    for league, rows in sorted(by_league.items()):
        if len(rows) < 3:
            continue
        inc_brier = sum((r["incumbent_prob"] - r["outcome"]) ** 2 for r in rows) / len(rows)
        cand_brier = sum((r["candidate_prob"] - r["outcome"]) ** 2 for r in rows) / len(rows)
        per_league[league] = {
            "n": len(rows),
            "incumbent_brier": round(inc_brier, 6),
            "candidate_brier": round(cand_brier, 6),
            "delta": round(cand_brier - inc_brier, 6),
        }

    inc_probs = [r["incumbent_prob"] for r in compared]
    cand_probs = [r["candidate_prob"] for r in compared]
    report = {
        "n_settled_picks": len(compared),
        "note": (
            "Small sample (all settled soccer totals picks in the ledger). "
            "Replay is PIT-safe: per-league history strictly before each "
            "event start, same game records the serving path uses."
        ),
        "pooled": {
            "incumbent_brier": round(_brier(inc_probs), 6),
            "candidate_brier": round(_brier(cand_probs), 6),
            "delta": round(_brier(cand_probs) - _brier(inc_probs), 6),
        },
        "per_league": per_league,
        "rows": compared,
    }
    out_path = PROJECT_ROOT / "outputs/research/soccer_league_split/settled_replay.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2, default=str))
    print(f"\nwritten to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
