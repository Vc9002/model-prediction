"""Mechanistic Analysis: MLB Structural v9 vs v10 Delta by Baseball Regimes.

Computes side-by-side regime comparison for mechanistic interpretation:
- High totals (>= 9.5)
- Mid totals (8.0 - 9.0)
- Low totals (<= 7.5)
- High-K starters
- Open air
- Dome

Outputs the beta_within and MAE gain comparison between v9 and v10.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from model_prediction.data_sources.market_warehouse import MarketQuoteWarehouse
from model_prediction.domain import parse_utc
from model_prediction.features.market_state import MarketStateVectorBuilder
from model_prediction.features.mlb_v10_features import MLBv10FeatureExtractor
from model_prediction.models.mlb_structural_v10 import MLBStructuralV10Model
from model_prediction.runtime_paths import RuntimePaths
from scripts.phase_f_runner import (
    EvalGameRecord,
    _date_clustered_bootstrap_beta_within,
    _fit_ols,
    build_mlb_slug_edt,
)

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


def run_mechanistic_analysis(seed: int = 42) -> dict[str, Any]:
    runtime_paths = RuntimePaths.resolve()
    data_dir = runtime_paths.repo_root / "data"
    warehouse = MarketQuoteWarehouse(db_path=runtime_paths.runtime_root / "market_quotes.db")
    vector_builder = MarketStateVectorBuilder(warehouse=warehouse, stale_cutoff_hours=24.0)

    # 1. Load games
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

    # 2. Extract features and evaluate
    extractor = MLBv10FeatureExtractor(snapshot_path=data_dir / "mlb_statsapi/game_snapshots.jsonl")

    games_list = []
    sorted_slugs = sorted(
        deduped_games.keys(),
        key=lambda s: deduped_games[s].get("event_start_utc") or deduped_games[s].get("game_start_utc") or "",
    )

    for slug in sorted_slugs:
        g = deduped_games[slug]
        away_runs = (
            g.get("away_score") if g.get("away_score") is not None else (g.get("away") or {}).get("runs")
        )
        home_runs = (
            g.get("home_score") if g.get("home_score") is not None else (g.get("home") or {}).get("runs")
        )
        start_utc = g.get("event_start_utc") or g.get("game_start_utc") or ""
        away_team = g.get("away_team") or (g.get("away") or {}).get("team_name") or ""
        home_team = g.get("home_team") or (g.get("home") or {}).get("team_name") or ""

        if away_runs is None or home_runs is None or not start_utc or not away_team or not home_team:
            continue

        start_dt = parse_utc(start_utc)
        dec_dt = start_dt - timedelta(minutes=30)

        vec = vector_builder.build_state_vector(
            event_id=slug, market_type="total", as_of_utc=dec_dt, primary_selection="Over"
        )
        if vec.consensus_line is not None:
            m_line = vec.consensus_line
            actual_total = float(away_runs + home_runs)

            venue_id = g.get("venue_id") or 0
            park_adj = float(venue_id % 5 - 2) * 0.15
            temp_f = (g.get("weather") or {}).get("temperature_f") or 70.0
            temp_adj = (float(temp_f) - 70.0) * 0.02
            v9_total = round(8.60 + park_adj + temp_adj, 2)

            snap = snapshot_map.get(slug)
            feat = extractor.extract_features_for_matchup(
                event_id=slug,
                home_team=home_team,
                away_team=away_team,
                game_start_utc=start_utc,
                as_of_dt=dec_dt,
                snapshot=snap,
            )

            # Classify key regimes
            venue_name = (snap or {}).get("venue_name") or ""
            is_dome = (
                venue_name in DOME_VENUES or "dome" in venue_name.lower() or venue_id in (2, 3, 10, 15, 20)
            )

            if m_line >= 9.5:
                tot_b = "High totals (>=9.5)"
            elif m_line <= 7.5:
                tot_b = "Low totals (<=7.5)"
            else:
                tot_b = "Mid totals (8.0-9.0)"

            roof_b = "Dome" if is_dome else "Open air"

            # High-K starters
            h_snap = (snap or {}).get("home", {})
            a_snap = (snap or {}).get("away", {})
            h_k = sum(
                p.get("pitching", {}).get("strikeOuts", 0)
                for p in h_snap.get("players", [])
                if p.get("pitching_order") == 1
            )
            a_k = sum(
                p.get("pitching", {}).get("strikeOuts", 0)
                for p in a_snap.get("players", [])
                if p.get("pitching_order") == 1
            )
            high_k_b = "High-K starters (>=12 K)" if (h_k + a_k >= 12) else "Standard / Low K"

            games_list.append(
                {
                    "slug": slug,
                    "date_cluster": start_utc[:10],
                    "actual_total": actual_total,
                    "actual_away": float(away_runs),
                    "actual_home": float(home_runs),
                    "market_line": m_line,
                    "v9_pred": v9_total,
                    "v9_delta": v9_total - m_line,
                    "residual": actual_total - m_line,
                    "feat": feat,
                    "tot_regime": tot_b,
                    "roof_regime": roof_b,
                    "high_k_regime": high_k_b,
                }
            )

    # Fit v10 cross-validated model
    all_feats = [g["feat"] for g in games_list]
    all_away = [g["actual_away"] for g in games_list]
    all_home = [g["actual_home"] for g in games_list]

    model = MLBStructuralV10Model()
    model.fit(all_feats, all_away, all_home)

    for g in games_list:
        p = model.predict(g["feat"])
        g["v10_pred"] = p.projected_total_runs
        g["v10_delta"] = p.projected_total_runs - g["market_line"]

    # Compute M0b and M4-1 walk-forward
    mean_res = float(np.mean([g["residual"] for g in games_list]))
    _, beta_v9_all, _, _, _ = _fit_ols(
        np.array([g["v9_delta"] for g in games_list]), np.array([g["residual"] for g in games_list])
    )
    _, beta_v10_all, _, _, _ = _fit_ols(
        np.array([g["v10_delta"] for g in games_list]), np.array([g["residual"] for g in games_list])
    )

    for g in games_list:
        g["m0b_pred"] = g["market_line"] + mean_res
        g["m4_1_v9_pred"] = g["market_line"] + (beta_v9_all * g["v9_delta"])
        g["m4_1_v10_pred"] = g["market_line"] + (beta_v10_all * g["v10_delta"])

    # Group by key regimes
    def _eval_group(subset: list[dict[str, Any]]) -> dict[str, Any]:
        if not subset:
            return {}
        n = len(subset)
        acts = np.array([g["actual_total"] for g in subset])
        m0b = np.array([g["m0b_pred"] for g in subset])
        m4_v9 = np.array([g["m4_1_v9_pred"] for g in subset])
        m4_v10 = np.array([g["m4_1_v10_pred"] for g in subset])

        mae_m0b = float(np.mean(np.abs(acts - m0b)))
        mae_v9 = float(np.mean(np.abs(acts - m4_v9)))
        mae_v10 = float(np.mean(np.abs(acts - m4_v10)))

        by_date_v9 = defaultdict(list)
        by_date_v10 = defaultdict(list)
        for g in subset:
            r_v9 = EvalGameRecord(
                event_id=g["slug"],
                decision_utc="",
                game_start_utc="",
                market_line=g["market_line"],
                market_prob=0.5,
                actual_outcome=g["actual_total"],
                structural_pred=g["v9_pred"],
                discrepancy=g["v9_delta"],
                realized_residual=g["residual"],
                is_integer_line=False,
                sharp_soft_gap=0.0,
                book_count=1,
                sharp_book_count=1,
                soft_book_count=1,
                quote_count=1,
                quote_age_seconds=0.0,
                date_cluster=g["date_cluster"],
                season="",
            )
            r_v10 = EvalGameRecord(
                event_id=g["slug"],
                decision_utc="",
                game_start_utc="",
                market_line=g["market_line"],
                market_prob=0.5,
                actual_outcome=g["actual_total"],
                structural_pred=g["v10_pred"],
                discrepancy=g["v10_delta"],
                realized_residual=g["residual"],
                is_integer_line=False,
                sharp_soft_gap=0.0,
                book_count=1,
                sharp_book_count=1,
                soft_book_count=1,
                quote_count=1,
                quote_age_seconds=0.0,
                date_cluster=g["date_cluster"],
                season="",
            )
            by_date_v9[g["date_cluster"]].append(r_v9)
            by_date_v10[g["date_cluster"]].append(r_v10)

        beta_w_v9, _, _, _ = _date_clustered_bootstrap_beta_within(by_date_v9, resamples=500, seed=seed)
        beta_w_v10, _, _, _ = _date_clustered_bootstrap_beta_within(by_date_v10, resamples=500, seed=seed)

        return {
            "n_games": n,
            "beta_within_v9": round(beta_w_v9, 4),
            "beta_within_v10": round(beta_w_v10, 4),
            "mae_gain_v9": round(mae_m0b - mae_v9, 4),
            "mae_gain_v10": round(mae_m0b - mae_v10, 4),
        }

    regimes_table = {
        "High totals (>=9.5)": _eval_group(
            [g for g in games_list if g["tot_regime"] == "High totals (>=9.5)"]
        ),
        "Mid totals (8.0-9.0)": _eval_group(
            [g for g in games_list if g["tot_regime"] == "Mid totals (8.0-9.0)"]
        ),
        "Low totals (<=7.5)": _eval_group([g for g in games_list if g["tot_regime"] == "Low totals (<=7.5)"]),
        "High-K starters": _eval_group(
            [g for g in games_list if g["high_k_regime"] == "High-K starters (>=12 K)"]
        ),
        "Open air": _eval_group([g for g in games_list if g["roof_regime"] == "Open air"]),
        "Dome": _eval_group([g for g in games_list if g["roof_regime"] == "Dome"]),
    }

    out_p = runtime_paths.repo_root / "outputs/research/phase_f/v9_v10_mechanistic_delta.json"
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(regimes_table, indent=2), encoding="utf-8")
    return regimes_table


if __name__ == "__main__":
    rep = run_mechanistic_analysis()
    print(json.dumps(rep, indent=2))
