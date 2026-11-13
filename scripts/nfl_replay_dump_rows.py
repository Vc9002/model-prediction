"""Per-game dump of the NFL v7 replay with margin/total components, for residual analysis.

Usage: python scripts/nfl_replay_dump_rows.py <out.json>  (run from the repo root)

Reuses the replay script's loaders so the cohort and PIT inputs are identical to
outputs/nfl/nfl_v7_replay.json; adds the structural components the engines sum so
each can be regressed against the market residual.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, "scripts")
sys.path.insert(0, "src")

import polars as pl
import replay_nfl_v7_all_settled as R

from model_prediction.features.nfl_efficiency import normalize_nfl_team_code
from model_prediction.features.nfl_qb_oline import check_nfl_qb_injury_status

OUT = Path(sys.argv[1])


def main() -> None:
    snapshot_dir = R.latest_snapshot_dir()
    store, _ = R.build_store(snapshot_dir)
    schedule = pl.read_parquet(snapshot_dir / "schedule.parquet")
    _sp_art, _tot_art, spread_model, total_model = R.load_models()
    qbs = R.QBResolver(snapshot_dir)
    injuries = R.InjuryResolver()
    games = R.load_games()
    sign = R.spread_sign_convention(games, schedule)
    cohort = [
        g
        for g in games
        if g.get("status") == "completed"
        and int(g.get("season_year") or 0) >= 2021
        and g.get("home_score") is not None
        and g.get("away_score") is not None
        and g.get("spread_line") is not None
        and g.get("total_line") is not None
    ]
    print("cohort", len(cohort), "game keys:", sorted(cohort[0].keys()))
    rows = []
    fails = 0
    for g in cohort:
        start = R.parse_utc(g["event_start_utc"])
        decision_time = start - R.DECISION_LEAD
        line = sign * float(g["spread_line"])
        total_line = float(g["total_line"])
        roof = str(g.get("roof") or "")
        is_dome = roof in ("dome", "closed")
        wind = None if is_dome or g.get("wind") is None else float(g["wind"])
        temp = None if is_dome or g.get("temp") is None else float(g["temp"])
        home, away = g["home_team"], g["away_team"]
        try:
            h_code, a_code = normalize_nfl_team_code(home), normalize_nfl_team_code(away)
            h_qb, a_qb = qbs.projected(h_code, decision_time), qbs.projected(a_code, decision_time)
            eff = store.get_matchup_efficiency(home, away, as_of_date=start, home_qb=h_qb, away_qb=a_qb)
            inj = injuries.entries(decision_time)
            _, _, h_pen = check_nfl_qb_injury_status(home, eff.home_qb or "", inj)
            _, _, a_pen = check_nfl_qb_injury_status(away, eff.away_qb or "", inj)
            if h_pen or a_pen:
                eff = replace(
                    eff,
                    home_qb_tier_adj=eff.home_qb_tier_adj - h_pen,
                    away_qb_tier_adj=eff.away_qb_tier_adj - a_pen,
                )
            is_neutral = bool(g.get("is_neutral_site", False))
            home_rest = int(g.get("home_rest_days") or 7)
            away_rest = int(g.get("away_rest_days") or 7)
            fc_sp = spread_model.forecast_from_efficiency(
                eff,
                spread_home_line=line,
                is_neutral_site=is_neutral,
                home_rest_days=home_rest,
                away_rest_days=away_rest,
            )
            fc_sp_struct = spread_model.forecast_from_efficiency(
                eff,
                spread_home_line=line,
                is_neutral_site=is_neutral,
                home_rest_days=home_rest,
                away_rest_days=away_rest,
                market_weight=0.0,
            )
            fc_tot_a = total_model.forecast_from_efficiency(
                eff, total_line=total_line, is_dome=is_dome, wind_mph=None, temperature_f=None
            )
            fc_tot_b = total_model.forecast_from_efficiency(
                eff, total_line=total_line, is_dome=is_dome, wind_mph=wind, temperature_f=temp
            )
        except Exception as exc:  # noqa: BLE001
            fails += 1
            print("FAIL", g["event_id"], exc)
            continue
        # --- spread components (mirror of forecast_spread) ---
        h_eff_pts_noqb = (
            (0.65 * eff.home_pass_epa + 0.35 * eff.away_def_pass_epa) * 23.0
            + (0.65 * eff.home_rush_epa + 0.35 * eff.away_def_rush_epa) * 9.0
            + eff.home_pressure_disparity * 3.5
        )
        a_eff_pts_noqb = (
            (0.65 * eff.away_pass_epa + 0.35 * eff.home_def_pass_epa) * 23.0
            + (0.65 * eff.away_rush_epa + 0.35 * eff.home_def_rush_epa) * 9.0
            + eff.away_pressure_disparity * 3.5
        )
        pass_pts = (0.65 * eff.home_pass_epa + 0.35 * eff.away_def_pass_epa) * 23.0 - (
            0.65 * eff.away_pass_epa + 0.35 * eff.home_def_pass_epa
        ) * 23.0
        rush_pts = (0.65 * eff.home_rush_epa + 0.35 * eff.away_def_rush_epa) * 9.0 - (
            0.65 * eff.away_rush_epa + 0.35 * eff.home_def_rush_epa
        ) * 9.0
        pressure_pts = (eff.home_pressure_disparity - eff.away_pressure_disparity) * 3.5
        qb_pts = eff.home_qb_tier_adj - eff.away_qb_tier_adj
        rest_diff = min(7, max(-7, home_rest - away_rest))
        rest_pts = rest_diff * 0.40
        hfa = 0.0 if is_neutral else spread_model.home_field_advantage
        edsr_diff = (eff.home_first_down_rate - eff.away_def_first_down_rate) - (
            eff.away_first_down_rate - eff.home_def_first_down_rate
        )
        tol_diff = eff.away_turnover_luck - eff.home_turnover_luck
        # --- total components (mirror of forecast_total) ---
        pass_edge = eff.home_pass_epa + eff.away_pass_epa
        rush_edge = eff.home_rush_epa + eff.away_rush_epa
        rz_edge = ((eff.home_rz_td_rate + eff.away_def_rz_td_rate) / 2.0 - 0.60) + (
            (eff.away_rz_td_rate + eff.home_def_rz_td_rate) / 2.0 - 0.60
        )
        exp_edge = ((eff.home_explosive_rate + eff.away_def_explosive_rate) / 2.0 - 0.08) + (
            (eff.away_explosive_rate + eff.home_def_explosive_rate) / 2.0 - 0.08
        )
        adj_home_pace = eff.home_pace * (1.0 + total_model.ngsp_weight * (eff.home_pass_ratio - 0.58))
        adj_away_pace = eff.away_pace * (1.0 + total_model.ngsp_weight * (eff.away_pass_ratio - 0.58))
        pace_edge = ((adj_home_pace + adj_away_pace) / 2.0 - 62.5) * total_model.pace_weight
        qb_tot = (eff.home_qb_tier_adj + eff.away_qb_tier_adj) * 0.4
        high_drag = max(0.0, total_line - 46.5) * 0.40 if total_line >= 47.0 else 0.0
        margin = int(g["home_score"]) - int(g["away_score"])
        total = int(g["home_score"]) + int(g["away_score"])
        rows.append(
            {
                "event_id": g["event_id"],
                "season": int(g["season_year"]),
                "week": g.get("week"),
                "start": start.isoformat(),
                "home": home,
                "away": away,
                "line": line,
                "total_line": total_line,
                "is_dome": is_dome,
                "is_neutral": is_neutral,
                "wind": wind,
                "temp": temp,
                "home_rest": home_rest,
                "away_rest": away_rest,
                "margin": margin,
                "total": total,
                "resid_margin": margin + line,  # actual minus market expectation
                "resid_total": total - total_line,
                "p_home_cover": fc_sp.prob_home_cover,
                "p_home_win": fc_sp.prob_home_win,
                "exp_margin": fc_sp.projected_margin_home,
                "struct_exp_margin": fc_sp_struct.projected_margin_home,
                "effective_sd": fc_sp.effective_sd,
                "p_over_a": fc_tot_a.prob_over,
                "p_over_b": fc_tot_b.prob_over,
                "exp_total_a": fc_tot_a.projected_total,
                "exp_total_b": fc_tot_b.projected_total,
                "weather_penalty_b": fc_tot_b.weather_total_penalty,
                "home_prior_games": eff.home_prior_games,
                "away_prior_games": eff.away_prior_games,
                "home_qb": eff.home_qb,
                "away_qb": eff.away_qb,
                "qb_source": "depth_chart" if (h_qb and a_qb) else "attempts_fallback",
                "qb_penalty": h_pen + a_pen,
                # spread components (points, home-positive)
                "c_pass": pass_pts,
                "c_rush": rush_pts,
                "c_pressure": pressure_pts,
                "c_qb": qb_pts,
                "c_rest": rest_pts,
                "c_hfa": hfa,
                "c_edsr": spread_model.edsr_weight * edsr_diff,
                "c_tol": spread_model.turnover_luck_weight * tol_diff,
                "c_eff_total_noqb": h_eff_pts_noqb - a_eff_pts_noqb,
                # total components (points)
                "t_pass": total_model.pass_weight * pass_edge,
                "t_rush": total_model.rush_weight * rush_edge,
                "t_rz": total_model.red_zone_weight * rz_edge,
                "t_explosive": total_model.explosive_weight * exp_edge,
                "t_pace": pace_edge,
                "t_qb": qb_tot,
                "t_dome": total_model.dome_boost if is_dome else 0.0,
                "t_weather_b": -fc_tot_b.weather_total_penalty,
                "t_bias": total_model.bias,
                "t_high_drag": -high_drag,
                # raw eff fields for further slicing
                "home_off_epa": eff.home_off_epa,
                "away_off_epa": eff.away_off_epa,
                "home_def_epa": eff.home_def_epa,
                "away_def_epa": eff.away_def_epa,
                "home_pace": eff.home_pace,
                "away_pace": eff.away_pace,
            }
        )
    print("rows", len(rows), "fails", fails)
    OUT.write_text(json.dumps(rows, default=str))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
