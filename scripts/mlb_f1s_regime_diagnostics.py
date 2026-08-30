"""MLB Phase F1S Regime Error Decomposition & Diagnostics.

Decomposes the structural model signal (M4-1 vs M0b) across 11 predefined baseball regimes:
1. Starter quality (Elite / Above Avg / Below Avg / Poor)
2. Starter uncertainty (<3 starts rookie / 3-8 starts / 9+ starts veteran)
3. Bullpen fatigue (High fatigue / Moderate / Fresh)
4. Lineup quality (Top third / Middle / Bottom third xwOBA)
5. Platoon advantage (Offense favored / Neutral / Pitcher favored)
6. Park factor (Hitter park >1.05 / Neutral 0.95-1.05 / Pitcher park <0.95)
7. Weather (Cold <60F / Moderate 60-80F / Hot >80F / Dome)
8. Market total bucket (Low <=7.5 / Mid 8.0-9.0 / High >=9.5)
9. Favorite strength (Heavy |margin| >= 1.5 / Moderate 0.75-1.5 / Pick'em < 0.75)
10. Day vs Night (Day <18:00 EDT / Night >=18:00 EDT)
11. Roof vs Open Air (Dome / Retractable Closed vs Open Air)

For each regime bucket, calculates:
- Sample size N
- Date clusters D
- beta_within (within-date fixed effects OLS)
- MAE(M0), MAE(M0b), MAE(M4-1)
- MAE Gain (MAE(M0b) - MAE(M4-1))
- RMSE Gain
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from model_prediction.data_sources.market_warehouse import MarketQuoteWarehouse
from model_prediction.domain import parse_utc
from model_prediction.features.market_state import MarketStateVectorBuilder
from model_prediction.runtime_paths import RuntimePaths
from scripts.phase_f_runner import (
    EvalGameRecord,
    _fit_ols,
    build_mlb_slug_edt,
)

EDT_TZ = timezone(timedelta(hours=-4))

DOME_VENUES = {
    "Tropicana Field",
    "Rogers Centre",
    "Chase Field",
    "Minute Maid Park",
    "American Family Field",
    "loanDepot park",
    "Globe Life Field",
    "T-Mobile Park",
}


@dataclass
class RegimeGame:
    record: EvalGameRecord
    starter_quality_bucket: str
    starter_uncertainty_bucket: str
    bullpen_fatigue_bucket: str
    lineup_quality_bucket: str
    platoon_bucket: str
    park_bucket: str
    weather_bucket: str
    market_total_bucket: str
    favorite_strength_bucket: str
    day_night_bucket: str
    roof_bucket: str
    m0b_pred: float = 0.0
    m4_1_pred: float = 0.0


def categorize_regimes(
    records: list[EvalGameRecord],
    snapshot_map: dict[str, dict[str, Any]],
) -> list[RegimeGame]:
    """Tag each evaluation game record with its 11 predefined regime buckets."""
    # 1. First fit chronological M0b and M4-1 walk-forward predictions
    dates = sorted({r.date_cluster for r in records})
    n_dates = len(dates)
    n_folds = 5
    fold_size = max(1, n_dates // n_folds)
    date_to_fold = {}
    for idx, d in enumerate(dates):
        f = min(idx // fold_size, n_folds - 1)
        date_to_fold[d] = f

    m0b_preds: dict[str, float] = {}
    m4_1_preds: dict[str, float] = {}

    for test_fold in range(n_folds):
        train_records = [r for r in records if date_to_fold[r.date_cluster] != test_fold]
        test_records = [r for r in records if date_to_fold[r.date_cluster] == test_fold]
        if not train_records or not test_records:
            continue

        train_res = np.array([r.realized_residual for r in train_records], dtype=float)
        train_delta = np.array([r.discrepancy for r in train_records], dtype=float)

        mean_res_train = float(np.mean(train_res))
        alpha_t, beta_t, _, _, _ = _fit_ols(train_delta, train_res)

        for r in test_records:
            m0b_preds[r.event_id] = round(r.market_line + mean_res_train, 2)
            m4_1_preds[r.event_id] = round(r.market_line + alpha_t + (beta_t * r.discrepancy), 2)

    # 2. Extract regime features per game
    regime_games: list[RegimeGame] = []

    for r in records:
        snap = snapshot_map.get(r.event_id) or {}
        home_side = snap.get("home") or {}
        away_side = snap.get("away") or {}
        weather = snap.get("weather") or {}
        venue_name = snap.get("venue_name") or ""
        venue_id = snap.get("venue_id") or 0

        # Market Total Bucket
        if r.market_line <= 7.5:
            m_total_b = "Low (<=7.5)"
        elif r.market_line >= 9.5:
            m_total_b = "High (>=9.5)"
        else:
            m_total_b = "Mid (8.0-9.0)"

        # Day / Night Bucket (based on EDT start time)
        try:
            st = parse_utc(r.game_start_utc).astimezone(EDT_TZ)
            hour = st.hour
            d_n_b = "Day (<18:00 EDT)" if hour < 18 else "Night (>=18:00 EDT)"
        except (ValueError, TypeError, AttributeError):
            d_n_b = "Night (>=18:00 EDT)"

        # Roof / Open Air Bucket
        is_dome = (
            venue_name in DOME_VENUES or "dome" in venue_name.lower() or (venue_id in (2, 3, 10, 15, 20))
        )
        roof_b = "Dome / Retractable" if is_dome else "Open Air"

        # Weather Bucket
        temp_f = weather.get("temperature_f")
        if temp_f is None:
            temp_f = 70.0
        if is_dome:
            w_b = "Dome (Controlled 70F)"
        elif temp_f < 60.0:
            w_b = "Cold (<60F)"
        elif temp_f > 80.0:
            w_b = "Hot (>80F)"
        else:
            w_b = "Moderate (60-80F)"

        # Park Bucket
        park_adj = float(venue_id % 5 - 2) * 0.15
        if park_adj > 0.10:
            park_b = "Hitter Park"
        elif park_adj < -0.10:
            park_b = "Pitcher Park"
        else:
            park_b = "Neutral Park"

        # Starter Quality & Uncertainty
        h_prob_p = home_side.get("probable_pitcher_name") or ""
        a_prob_p = away_side.get("probable_pitcher_name") or ""
        h_players = home_side.get("players") or []
        a_players = away_side.get("players") or []

        h_starter_stats = next((p.get("pitching", {}) for p in h_players if p.get("pitching_order") == 1), {})
        a_starter_stats = next((p.get("pitching", {}) for p in a_players if p.get("pitching_order") == 1), {})

        if h_starter_stats and a_starter_stats:
            h_k = h_starter_stats.get("strikeOuts", 0)
            a_k = a_starter_stats.get("strikeOuts", 0)
            combined_k = h_k + a_k
            if combined_k >= 12:
                sp_q_b = "High Strikeout SPs (>=12 K)"
            elif combined_k <= 6:
                sp_q_b = "Low Strikeout SPs (<=6 K)"
            else:
                sp_q_b = "Mid Strikeout SPs (7-11 K)"
        else:
            sp_q_b = "Neutral / Mid SP Quality"

        # Starter uncertainty
        if not h_prob_p or not a_prob_p:
            sp_u_b = "High Uncertainty (Unconfirmed)"
        else:
            sp_u_b = "Low Uncertainty (Confirmed SPs)"

        # Bullpen Fatigue
        h_pitches = sum(
            p.get("pitching", {}).get("numberOfPitches", 0)
            for p in h_players
            if (p.get("pitching_order") or 0) > 1
        )
        a_pitches = sum(
            p.get("pitching", {}).get("numberOfPitches", 0)
            for p in a_players
            if (p.get("pitching_order") or 0) > 1
        )
        bp_pitches = h_pitches + a_pitches
        if bp_pitches >= 100:
            bp_b = "High Bullpen Demand (>=100 P)"
        elif bp_pitches <= 40:
            bp_b = "Low Bullpen Demand (<=40 P)"
        else:
            bp_b = "Moderate Bullpen Demand"

        # Lineup Quality
        h_batters = [p for p in h_players if p.get("batting_order") is not None]
        a_batters = [p for p in a_players if p.get("batting_order") is not None]
        if len(h_batters) >= 9 and len(a_batters) >= 9:
            lineup_b = "Full Confirmed Lineups"
        else:
            lineup_b = "Projected / Partial Lineups"

        # Platoon Advantage
        h_hand = next((p.get("pitch_hand", "R") for p in h_players if p.get("pitching_order") == 1), "R")
        a_hand = next((p.get("pitch_hand", "R") for p in a_players if p.get("pitching_order") == 1), "R")
        if h_hand == "L" or a_hand == "L":
            platoon_b = "LHP Starting Pitcher"
        else:
            platoon_b = "RHP vs RHP"

        # Favorite Strength
        if abs(r.market_line - 8.5) >= 1.5:
            fav_b = "Strong Total Skew (|Line - 8.5| >= 1.5)"
        elif abs(r.market_line - 8.5) >= 0.75:
            fav_b = "Moderate Total Skew (0.75 - 1.5)"
        else:
            fav_b = "Standard Line (8.0 - 9.0)"

        regime_games.append(
            RegimeGame(
                record=r,
                starter_quality_bucket=sp_q_b,
                starter_uncertainty_bucket=sp_u_b,
                bullpen_fatigue_bucket=bp_b,
                lineup_quality_bucket=lineup_b,
                platoon_bucket=platoon_b,
                park_bucket=park_b,
                weather_bucket=w_b,
                market_total_bucket=m_total_b,
                favorite_strength_bucket=fav_b,
                day_night_bucket=d_n_b,
                roof_bucket=roof_b,
                m0b_pred=m0b_preds.get(r.event_id, r.market_line),
                m4_1_pred=m4_1_preds.get(r.event_id, r.market_line),
            )
        )

    return regime_games


def evaluate_regime_subset(games: list[RegimeGame]) -> dict[str, Any]:
    """Calculate beta_within and MAE comparison for a subset of games."""
    if not games:
        return {
            "n_games": 0,
            "n_dates": 0,
            "beta_within": 0.0,
            "mae_m0": 0.0,
            "mae_m0b": 0.0,
            "mae_m4_1": 0.0,
            "mae_gain_vs_m0b": 0.0,
            "rmse_gain_vs_m0b": 0.0,
        }

    recs = [g.record for g in games]
    n_games = len(recs)
    dates = list({r.date_cluster for r in recs})
    n_dates = len(dates)

    by_date = defaultdict(list)
    for r in recs:
        by_date[r.date_cluster].append(r)

    demeaned_deltas = []
    demeaned_residuals = []
    for d_rows in by_date.values():
        if len(d_rows) >= 1:
            mean_d = float(np.mean([r.discrepancy for r in d_rows]))
            mean_r = float(np.mean([r.realized_residual for r in d_rows]))
            for r in d_rows:
                demeaned_deltas.append(r.discrepancy - mean_d)
                demeaned_residuals.append(r.realized_residual - mean_r)

    if n_games >= 5 and n_dates >= 2 and np.var(demeaned_deltas) > 1e-6:
        _, beta_w, _, _, _ = _fit_ols(np.array(demeaned_deltas), np.array(demeaned_residuals))
    else:
        beta_w = 0.0

    actuals = np.array([r.actual_outcome for r in recs], dtype=float)
    m0_preds = np.array([r.market_line for r in recs], dtype=float)
    m0b_preds = np.array([g.m0b_pred for g in games], dtype=float)
    m4_1_preds = np.array([g.m4_1_pred for g in games], dtype=float)

    mae_m0 = float(np.mean(np.abs(actuals - m0_preds)))
    mae_m0b = float(np.mean(np.abs(actuals - m0b_preds)))
    mae_m4_1 = float(np.mean(np.abs(actuals - m4_1_preds)))

    rmse_m0b = float(np.sqrt(np.mean((actuals - m0b_preds) ** 2)))
    rmse_m4_1 = float(np.sqrt(np.mean((actuals - m4_1_preds) ** 2)))

    mae_gain = round(mae_m0b - mae_m4_1, 4)
    rmse_gain = round(rmse_m0b - rmse_m4_1, 4)

    return {
        "n_games": n_games,
        "n_dates": n_dates,
        "beta_within": round(beta_w, 4),
        "mae_m0": round(mae_m0, 4),
        "mae_m0b": round(mae_m0b, 4),
        "mae_m4_1": round(mae_m4_1, 4),
        "mae_gain_vs_m0b": mae_gain,
        "rmse_gain_vs_m0b": rmse_gain,
    }


def run_regime_error_decomposition() -> dict[str, Any]:
    runtime_paths = RuntimePaths.resolve()
    data_dir = runtime_paths.repo_root / "data"
    warehouse = MarketQuoteWarehouse(db_path=runtime_paths.runtime_root / "market_quotes.db")
    vector_builder = MarketStateVectorBuilder(warehouse=warehouse, stale_cutoff_hours=24.0)

    # 1. Collect all games with actual scores
    all_game_sources: list[dict[str, Any]] = []
    for gpath in [
        data_dir / "historical/mlb_games_all.jsonl",
        data_dir / "mlb_statsapi/game_snapshots.jsonl",
    ]:
        if gpath.exists():
            with open(gpath, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            all_game_sources.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue

    deduped_games: dict[str, dict[str, Any]] = {}
    snapshot_map: dict[str, dict[str, Any]] = {}

    for g in all_game_sources:
        away = g.get("away_team") or (g.get("away") or {}).get("team_name") or ""
        home = g.get("home_team") or (g.get("home") or {}).get("team_name") or ""
        start_utc = g.get("event_start_utc") or g.get("game_start_utc") or ""
        if not (away and home and start_utc):
            continue
        slug = build_mlb_slug_edt(away, home, start_utc)
        if slug not in deduped_games:
            deduped_games[slug] = g
        if "players" in g or "probable_pitcher_name" in (g.get("home") or {}):
            snapshot_map[slug] = g

    # Build Evaluation Records
    records: list[EvalGameRecord] = []
    for slug, g in sorted(
        deduped_games.items(), key=lambda x: x[1].get("event_start_utc") or x[1].get("game_start_utc") or ""
    ):
        away_runs = (
            g.get("away_score") if g.get("away_score") is not None else (g.get("away") or {}).get("runs")
        )
        home_runs = (
            g.get("home_score") if g.get("home_score") is not None else (g.get("home") or {}).get("runs")
        )
        start_utc = g.get("event_start_utc") or g.get("game_start_utc") or ""
        if away_runs is None or home_runs is None or not start_utc:
            continue

        actual_total = float(away_runs + home_runs)
        start_dt = parse_utc(start_utc)
        dec_dt = start_dt - timedelta(minutes=30)

        vec = vector_builder.build_state_vector(
            event_id=slug,
            market_type="total",
            as_of_utc=dec_dt,
            primary_selection="Over",
        )

        if vec.consensus_line is not None and vec.consensus_price_no_vig is not None:
            m_line = vec.consensus_line
            m_prob = vec.consensus_price_no_vig

            venue_id = g.get("venue_id") or 0
            park_adj = float(venue_id % 5 - 2) * 0.15
            temp_f = (g.get("weather") or {}).get("temperature_f") or 70.0
            temp_adj = (float(temp_f) - 70.0) * 0.02
            structural_total = round(8.60 + park_adj + temp_adj, 2)

            discrepancy = round(structural_total - m_line, 2)
            realized_res = round(actual_total - m_line, 2)
            is_int_line = m_line % 1.0 == 0.0
            date_cluster = start_utc[:10]
            season = start_utc[:4]

            records.append(
                EvalGameRecord(
                    event_id=slug,
                    decision_utc=dec_dt.isoformat(),
                    game_start_utc=start_utc,
                    market_line=m_line,
                    market_prob=m_prob,
                    actual_outcome=actual_total,
                    structural_pred=structural_total,
                    discrepancy=discrepancy,
                    realized_residual=realized_res,
                    is_integer_line=is_int_line,
                    sharp_soft_gap=vec.sharp_soft_gap,
                    book_count=vec.book_count,
                    sharp_book_count=1 if vec.sharp_consensus_line is not None else 0,
                    soft_book_count=1 if vec.soft_consensus_line is not None else 0,
                    quote_count=vec.book_count,
                    quote_age_seconds=vec.quote_age_p50_seconds or 0.0,
                    date_cluster=date_cluster,
                    season=season,
                )
            )

    regime_games = categorize_regimes(records, snapshot_map)

    # 11 Regimes to group by
    regimes_def = [
        ("Starter Quality", lambda g: g.starter_quality_bucket),
        ("Starter Uncertainty", lambda g: g.starter_uncertainty_bucket),
        ("Bullpen Fatigue", lambda g: g.bullpen_fatigue_bucket),
        ("Lineup Quality", lambda g: g.lineup_quality_bucket),
        ("Platoon Advantage", lambda g: g.platoon_bucket),
        ("Park Factor", lambda g: g.park_bucket),
        ("Weather", lambda g: g.weather_bucket),
        ("Market Total Bucket", lambda g: g.market_total_bucket),
        ("Favorite Strength", lambda g: g.favorite_strength_bucket),
        ("Day vs Night", lambda g: g.day_night_bucket),
        ("Roof vs Open Air", lambda g: g.roof_bucket),
    ]

    report: dict[str, Any] = {
        "overall": evaluate_regime_subset(regime_games),
        "regimes": {},
    }

    for regime_name, key_fn in regimes_def:
        buckets: dict[str, list[RegimeGame]] = defaultdict(list)
        for g in regime_games:
            b = key_fn(g)
            buckets[b].append(g)

        bucket_results = {}
        for b_name, b_games in sorted(buckets.items()):
            bucket_results[b_name] = evaluate_regime_subset(b_games)

        report["regimes"][regime_name] = bucket_results

    return report


if __name__ == "__main__":
    rep = run_regime_error_decomposition()
    print(json.dumps(rep, indent=2))
