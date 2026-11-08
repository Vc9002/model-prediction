"""Round 8 of the NBA market-blend investigation.

Task 1: extend the blend-weight sweep from scripts/market_blend_kaggle_revalidation.py
(which tested 0.0-0.8) up to 1.0, to find whether Brier keeps improving to a pure-market
price (weight=1.0, meaning v7 adds nothing) or peaks below 1.0 (v7 adds real information
on top of the market). Selected on VALIDATION only; ONE holdout pass on the selected weight.

Task 2: real historical opening-line data exists for the 2025-26 season only
(Kaggle minmaxer/nba-closing-lines-ats-2025-26: open_spread_home, open_total,
open_home_win_prob -- distinct from the closing lines used in the prior round). Match
those to data/historical/nba_games_all.jsonl by (date, home_team, away_team) and re-run
the SAME weight (selected by Task 1's validation step -- no second holdout search) against
opening lines, to get a realistic estimate of what's actually available pregame in live
production, versus the closing-line backtest which is optimistically biased.

Reuses evaluate_engine/load_nba_games from test_combo_features_v7.py and the same
60/20/20 chronological split as every prior round. Shadow/research only; does not
touch config/production.yaml.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from nba_v5_pit_walkforward import split_complete_dates
from test_combo_features_v7 import evaluate_engine, load_nba_games

from model_prediction.models.nba_v7 import NBAStructuralV7Engine

CLOSING_ODDS_PATH = Path("data/odds/nba/historical_closing_lines/nba_closing_lines_2024_2026.jsonl")
OPENING_CSV_PATH = Path(
    "C:/Users/Vince/AppData/Local/Temp/claude/e--MP/55e4a91b-7011-45bc-b2e9-c87fbc97cb1f/"
    "scratchpad/kaggle_nba_odds/current_season/nba_2025_26_closing_lines_ats.csv"
)


def load_closing_odds() -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    with open(CLOSING_ODDS_PATH, encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            try:
                spread = float(row["spread"])
                total = float(row["total"])
            except (TypeError, ValueError):
                continue
            home_margin_line = spread if row.get("whos_favored") == "home" else -spread
            out[str(row["event_id"])] = {
                "spread_away_line": -home_margin_line,
                "total_line": total,
            }
    return out


def load_opening_odds(all_games: list) -> tuple[dict[str, dict[str, float]], int, int]:
    """Match the 2025-26 opening-line CSV to nba_games_all.jsonl event_ids by (date, teams)."""
    by_key = {(g.event_start_utc[:10], g.home_team, g.away_team): g.event_id for g in all_games}
    out: dict[str, dict[str, float]] = {}
    total_rows = 0
    matched = 0
    with open(OPENING_CSV_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            total_rows += 1
            try:
                open_spread_home = float(row["open_spread_home"])
                open_total = float(row["open_total"])
            except (TypeError, ValueError):
                continue
            key = (row["date_et"], row["home_team"], row["away_team"])
            event_id = by_key.get(key)
            if event_id is None:
                continue
            matched += 1
            # open_spread_home uses standard spread notation: negative = home favored
            # (verified against favorite/home_score/away_score columns, e.g. OKC vs HOU
            # open_spread_home=-7.5 with OKC (home) favored and winning). The closing-odds
            # loader's spread_away_line = -home_margin_line, where home_margin_line is
            # positive when home is favored -- i.e. spread_away_line IS already in the
            # same negative-favors-home convention as open_spread_home. No extra negation.
            out[event_id] = {
                "spread_away_line": open_spread_home,
                "total_line": open_total,
            }
    return out, total_rows, matched


def run() -> None:
    games_path = Path("data/historical/nba_games_all.jsonl")
    all_games = load_nba_games(games_path)
    _train_games, val_games, holdout_games, meta = split_complete_dates(all_games, 0.60, 0.20)
    print("Data split:", meta)

    # ---------- TASK 1: full weight sweep 0.0-1.0, validation-only selection ----------
    closing_odds = load_closing_odds()
    val_odds_n = sum(1 for g in val_games if g.event_id in closing_odds)
    hold_odds_n = sum(1 for g in holdout_games if g.event_id in closing_odds)
    print(
        f"\nClosing-odds coverage: validation {val_odds_n}/{len(val_games)}, holdout {hold_odds_n}/{len(holdout_games)}"
    )

    print("\n=== TASK 1: FULL WEIGHT SWEEP 0.0-1.0 (closing lines), VALIDATION ===")
    weights = [0.0, 0.2, 0.35, 0.5, 0.65, 0.8, 0.85, 0.9, 0.95, 1.0]
    val_results = {}
    for bw in weights:
        eng = NBAStructuralV7Engine(market_blend_weight=bw)
        m = evaluate_engine(eng, all_games, val_games, odds=closing_odds, use_real_odds=True)
        val_results[bw] = m
        print(f"blend_weight={bw}: {m}")

    best_bw = min(val_results, key=lambda k: val_results[k].moneyline_brier)
    peaked_before_1 = best_bw < 1.0
    print(f"\nSelected on validation: blend_weight={best_bw}  (peaked before 1.0: {peaked_before_1})")
    print(f"blend=1.0 (pure market) validation result: {val_results[1.0]}")
    print(f"blend=0.0 (pure v7) validation result:      {val_results[0.0]}")

    baseline = NBAStructuralV7Engine(market_blend_weight=0.0)
    pure_market = NBAStructuralV7Engine(market_blend_weight=1.0)
    best_eng = NBAStructuralV7Engine(market_blend_weight=best_bw)

    print("\n--- HOLDOUT (touched once per engine): baseline(0.0) vs selected vs pure-market(1.0) ---")
    baseline_hold = evaluate_engine(baseline, all_games, holdout_games, odds=closing_odds, use_real_odds=True)
    selected_hold = evaluate_engine(best_eng, all_games, holdout_games, odds=closing_odds, use_real_odds=True)
    pure_market_hold = evaluate_engine(
        pure_market, all_games, holdout_games, odds=closing_odds, use_real_odds=True
    )
    print(f"blend=0.0 (v7 only):        {baseline_hold}")
    print(f"blend={best_bw} (selected): {selected_hold}")
    print(f"blend=1.0 (pure market):    {pure_market_hold}")

    # ---------- TASK 2: opening-line reality check (2025-26 season only) ----------
    print("\n=== TASK 2: OPENING-LINE REALITY CHECK (2025-26 season only) ===")
    opening_odds, csv_rows, matched = load_opening_odds(all_games)
    print(f"Opening-line CSV rows: {csv_rows}, matched to nba_games_all.jsonl by (date,home,away): {matched}")

    val_open_n = sum(1 for g in val_games if g.event_id in opening_odds)
    hold_open_n = sum(1 for g in holdout_games if g.event_id in opening_odds)
    print(
        f"Opening-odds coverage: validation {val_open_n}/{len(val_games)}, holdout {hold_open_n}/{len(holdout_games)}"
    )

    if hold_open_n < 20:
        print(
            f"\nINSUFFICIENT HOLDOUT COVERAGE for opening-line test (n={hold_open_n}). "
            "Not reporting a holdout number on a sample this small -- would violate the "
            "same statistical-power discipline flagged for the original n=92 closing-line result."
        )
        opening_baseline_hold = None
        opening_selected_hold = None
    else:
        opening_baseline_hold = evaluate_engine(
            baseline, all_games, holdout_games, odds=opening_odds, use_real_odds=True
        )
        opening_selected_hold = evaluate_engine(
            best_eng, all_games, holdout_games, odds=opening_odds, use_real_odds=True
        )
        print(f"\nOpening-line holdout (n={hold_open_n}), blend=0.0 (v7 only): {opening_baseline_hold}")
        print(
            f"Opening-line holdout (n={hold_open_n}), blend={best_bw} (same weight as Task 1, no re-search): {opening_selected_hold}"
        )

    out = {
        "split_meta": meta,
        "task1_validation_sweep": {str(k): vars(v) for k, v in val_results.items()},
        "task1_selected_blend_weight": best_bw,
        "task1_peaked_before_1": peaked_before_1,
        "task1_holdout_baseline": vars(baseline_hold),
        "task1_holdout_selected": vars(selected_hold),
        "task1_holdout_pure_market": vars(pure_market_hold),
        "task2_opening_csv_rows": csv_rows,
        "task2_opening_matched": matched,
        "task2_opening_coverage_validation": val_open_n,
        "task2_opening_coverage_holdout": hold_open_n,
        "task2_opening_holdout_baseline": vars(opening_baseline_hold) if opening_baseline_hold else None,
        "task2_opening_holdout_selected": vars(opening_selected_hold) if opening_selected_hold else None,
    }
    Path("scratch").mkdir(exist_ok=True)
    Path("scratch/nba_v7_round8_result.json").write_text(json.dumps(out, indent=2))
    print("\nWrote scratch/nba_v7_round8_result.json")


if __name__ == "__main__":
    run()
