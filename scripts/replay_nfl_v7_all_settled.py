"""Replay the fixed NFL v7 spread/total engines point-in-time and calibrate them.

Two cohorts, nothing skipped:

1. Every completed NFL game with a closing spread AND total line in
   data/processed/nfl/games.jsonl from the 2021 season to today (efficiency inputs
   exist from 2021). Lines are the nflverse closing lines; outcomes are cover /
   over against them. This is the calibration cohort: walk-forward 60/20/20 by
   kickoff time, calibrator selected on the first 80% (expanding-window OOF
   tournament), scored once on the locked last 20%, then the champion method is
   refit on all games for the serving artifact (the pipeline's convention).
2. Every settled Flat-ledger NFL spread/total pick, re-priced by the fixed engine
   at the pick's own line and executable ask, with inputs as of the pick's
   decision time -- "as if we had run all the Flat picks with this model".
   Moneyline rows are summarised from the ledger (the moneyline champion is the
   nfl-elo-trend-lr-v4 logistic model, not these engines).

Point-in-time inputs per game:
- team/QB efficiency: nflverse weekly stats with gameday strictly before the game
  (legacy frame 2021-2025 + the newest snapshot's full 2026 player file). Stats
  can receive small retroactive corrections; the frames are not vintaged, which is
  the same caveat the production snapshots carry (historical_source_observation_
  times_verified = False).
- projected QB1: nflverse depth chart rows published <= decision time (2026 only;
  earlier seasons fall back to the engine's attempts-based resolver).
- QB injury penalty: ESPN injury snapshots captured <= decision time (available
  from 2026-10-16; zero before).
- rest days, neutral site, roof: from the game record (schedule facts).
- weather: variant A = unavailable (no weather term); variant B = the game's
  recorded temp/wind (nflverse). B approximates what a pregame forecast would have
  said and is what the total calibrator is fit on; A is reported alongside.

Writes (unless --no-write):
  data/calibration/models/nfl-spread-structural-v7.spread_calibrator.json
  data/calibration/models/nfl-total-structural-v7.total_calibrator.json
  config/models/nfl-*-structural-v7.json  (qualification.replay block + new hash)
  outputs/nfl/nfl_v7_replay.json, outputs/nfl/nfl_v7_replay.md
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sqlite3
import sys
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, "src")

import polars as pl

from model_prediction.calibration import (
    BetaCalibrator,
    IsotonicCalibrator,
    TemperatureCalibrator,
    TrainablePlattCalibrator,
    calibration_metrics,
    run_calibration_tournament,
)
from model_prediction.cli.forecast_nfl_support import _load_nfl_injuries_known_at
from model_prediction.data_sources.nfl_efficiency_snapshot import (
    QB_COLUMNS,
    TEAM_COLUMNS,
    build_efficiency_frames,
)
from model_prediction.domain import EASTERN, parse_utc
from model_prediction.features.multi_sport_weather import resolve_nfl_game_weather
from model_prediction.features.nfl_efficiency import NFLEfficiencyStore, normalize_nfl_team_code
from model_prediction.features.nfl_qb_oline import check_nfl_qb_injury_status
from model_prediction.models.nfl_spread_v7 import NFLSpreadModelV7
from model_prediction.models.nfl_total_v7 import NFLTotalModelV7
from model_prediction.production_registry import compute_artifact_hash

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
LEDGER_DB = (
    Path(os.environ.get("MODEL_PREDICTION_RUNTIME_ROOT", r"E:\model-prediction-runtime"))
    / "ledgers"
    / "ledgers.db"
)
STANDARD_ASK = round(110 / 210, 6)  # 52.38%: a two-sided closing line priced at the standard lay
MIN_EDGE = 0.05  # config/model.yaml NFL.min_edge -- the live Main hurdle
DECISION_LEAD = timedelta(hours=6)  # approximates the last daily cycle before kickoff
TEAM_FRAME_COLS = ["game_id", "gameday", "team", "opponent_team", *TEAM_COLUMNS]
QB_FRAME_COLS = ["game_id", "gameday", "team", "player_display_name", *QB_COLUMNS]


# ----------------------------------------------------------------------------- inputs
def latest_snapshot_dir() -> Path:
    paths = sorted(
        glob.glob(str(DATA / "processed" / "nfl" / "efficiency_snapshots" / "*" / "manifest.json"))
    )
    if not paths:
        raise SystemExit("no NFL efficiency snapshot on disk")
    return Path(paths[-1]).parent


def build_store(snapshot_dir: Path) -> tuple[NFLEfficiencyStore, dict[str, Any]]:
    legacy_team = pl.read_parquet(DATA / "processed" / "nfl" / "team_weekly_efficiency.parquet")
    legacy_qb = pl.read_parquet(DATA / "processed" / "nfl" / "qb_weekly_efficiency.parquet")
    players = pl.read_parquet(snapshot_dir / "players.parquet")
    schedule = pl.read_parquet(snapshot_dir / "schedule.parquet")
    team26, qb26 = build_efficiency_frames(players, schedule, datetime.now(UTC))

    def _norm(frame: pl.DataFrame, cols: list[str]) -> pl.DataFrame:
        out = frame.select(cols).with_columns(pl.col("gameday").cast(pl.Utf8))
        numeric = [
            c for c in cols if c not in ("game_id", "gameday", "team", "opponent_team", "player_display_name")
        ]
        return out.with_columns([pl.col(c).cast(pl.Float64) for c in numeric])

    team = pl.concat(
        [_norm(legacy_team.filter(pl.col("season") < 2026), TEAM_FRAME_COLS), _norm(team26, TEAM_FRAME_COLS)],
        how="vertical",
    )
    qb = pl.concat(
        [_norm(legacy_qb.filter(pl.col("season") < 2026), QB_FRAME_COLS), _norm(qb26, QB_FRAME_COLS)],
        how="vertical",
    )
    manifest = {
        "legacy_rows": legacy_team.filter(pl.col("season") < 2026).height,
        "snapshot": snapshot_dir.name,
        "snapshot_team_rows": team26.height,
        "team_rows": team.height,
        "qb_rows": qb.height,
        "gameday_range": [team["gameday"].min(), team["gameday"].max()],
    }
    return NFLEfficiencyStore.from_frames(team, qb, source_manifest=manifest), manifest


def load_games() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with (DATA / "processed" / "nfl" / "games.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def spread_sign_convention(games: list[dict[str, Any]], schedule: pl.DataFrame) -> int:
    """+1 if games.jsonl spread_line already is the HOME line (negative = home favoured),
    -1 if it is nflverse's home-margin convention (positive = home favoured)."""
    sched = {
        (str(r["gameday"]), str(r["home_team"]), str(r["away_team"])): float(r["spread_line"])
        for r in schedule.filter(pl.col("spread_line").is_not_null()).iter_rows(named=True)
    }
    same = flipped = 0
    for g in games:
        if g.get("spread_line") is None:
            continue
        day = parse_utc(g["event_start_utc"]).astimezone(EASTERN).date().isoformat()
        key = (day, normalize_nfl_team_code(g["home_team"]), normalize_nfl_team_code(g["away_team"]))
        nfl = sched.get(key)
        if nfl is None or nfl == 0:
            continue
        if abs(float(g["spread_line"]) - nfl) < 1e-9:
            same += 1
        elif abs(float(g["spread_line"]) + nfl) < 1e-9:
            flipped += 1
    print(f"spread_line convention check vs nflverse: same-sign {same}, flipped {flipped}")
    if same == flipped == 0:
        raise SystemExit("could not establish games.jsonl spread_line convention")
    # nflverse: positive = home favoured. games.jsonl matching it means home-margin convention.
    return -1 if same >= flipped else 1


# ----------------------------------------------------------------------------- engines
def load_models() -> tuple[dict, dict, NFLSpreadModelV7, NFLTotalModelV7]:
    sp = json.loads(
        (REPO / "config" / "models" / "nfl-spread-structural-v7.json").read_text(encoding="utf-8")
    )
    tot = json.loads(
        (REPO / "config" / "models" / "nfl-total-structural-v7.json").read_text(encoding="utf-8")
    )
    return sp, tot, NFLSpreadModelV7.from_artifact(sp), NFLTotalModelV7.from_artifact(tot)


class QBResolver:
    """Depth-chart QB1 per team as of a decision time (2026 only; None elsewhere)."""

    def __init__(self, snapshot_dir: Path) -> None:
        depth = pl.read_parquet(snapshot_dir / "depth.parquet")
        self.qb1 = depth.filter((pl.col("pos_abb") == "QB") & (pl.col("pos_rank") == 1)).select(
            "dt", "team", "player_name"
        )
        self.min_dt = str(self.qb1["dt"].min())

    def projected(self, team_code: str, decision_time: datetime) -> str | None:
        cutoff = decision_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        if cutoff < self.min_dt:
            return None
        rows = self.qb1.filter((pl.col("team") == team_code) & (pl.col("dt") <= cutoff)).sort(
            "dt", descending=True
        )
        return str(rows["player_name"][0]) if rows.height else None


class InjuryResolver:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[list[dict[str, Any]], Any]] = {}

    def entries(self, decision_time: datetime) -> list[dict[str, Any]]:
        key = decision_time.strftime("%Y-%m-%dT%H")
        if key not in self._cache:
            self._cache[key] = _load_nfl_injuries_known_at(DATA, decision_time)
        return self._cache[key][0]


def price_game(
    *,
    store: NFLEfficiencyStore,
    spread_model: NFLSpreadModelV7,
    total_model: NFLTotalModelV7,
    qbs: QBResolver,
    injuries: InjuryResolver,
    home: str,
    away: str,
    start: datetime,
    decision_time: datetime,
    spread_home_line: float,
    total_line: float,
    is_neutral: bool,
    home_rest: int,
    away_rest: int,
    is_dome: bool,
    wind: float | None,
    temp: float | None,
) -> dict[str, Any]:
    h_code, a_code = normalize_nfl_team_code(home), normalize_nfl_team_code(away)
    h_qb, a_qb = qbs.projected(h_code, decision_time), qbs.projected(a_code, decision_time)
    eff = store.get_matchup_efficiency(home, away, as_of_date=start, home_qb=h_qb, away_qb=a_qb)
    inj = injuries.entries(decision_time)
    _, h_stat, h_pen = check_nfl_qb_injury_status(home, eff.home_qb or "", inj)
    _, a_stat, a_pen = check_nfl_qb_injury_status(away, eff.away_qb or "", inj)
    if h_pen or a_pen:
        eff = replace(
            eff, home_qb_tier_adj=eff.home_qb_tier_adj - h_pen, away_qb_tier_adj=eff.away_qb_tier_adj - a_pen
        )
    fc_sp = spread_model.forecast_from_efficiency(
        eff,
        spread_home_line=spread_home_line,
        is_neutral_site=is_neutral,
        home_rest_days=home_rest,
        away_rest_days=away_rest,
    )
    fc_tot_a = total_model.forecast_from_efficiency(
        eff, total_line=total_line, is_dome=is_dome, wind_mph=None, temperature_f=None
    )
    fc_tot_b = total_model.forecast_from_efficiency(
        eff, total_line=total_line, is_dome=is_dome, wind_mph=wind, temperature_f=temp
    )
    return {
        "p_home_cover": fc_sp.prob_home_cover,
        "p_home_win": fc_sp.prob_home_win,
        "margin": fc_sp.projected_margin_home,
        "effective_sd": fc_sp.effective_sd,
        "p_over_a": fc_tot_a.prob_over,
        "p_over_b": fc_tot_b.prob_over,
        "total_a": fc_tot_a.projected_total,
        "total_b": fc_tot_b.projected_total,
        "weather_penalty_b": fc_tot_b.weather_total_penalty,
        "home_prior_games": eff.home_prior_games,
        "away_prior_games": eff.away_prior_games,
        "home_qb": eff.home_qb,
        "away_qb": eff.away_qb,
        "qb_source": "depth_chart" if (h_qb and a_qb) else "attempts_fallback",
        "qb_penalty": h_pen + a_pen,
        "qb_status": [h_stat, a_stat],
    }


# ----------------------------------------------------------------------------- metrics
def _pnl(p: float, ask: float, won: bool) -> float:
    return (1.0 / ask - 1.0) if won else -1.0


def market_block(
    rows: list[dict[str, Any]], prob_key: str, outcome_key: str, *, ask_key: str | None = None
) -> dict[str, Any]:
    probs = [r[prob_key] for r in rows]
    outs = [r[outcome_key] for r in rows]
    metrics = (
        calibration_metrics(probs, outs)
        if len(probs) >= 30
        else {"status": "insufficient_sample", "sample_size": len(probs)}
    )
    favored_hits = sum(1 for p, y in zip(probs, outs, strict=True) if (p >= 0.5) == bool(y))
    out: dict[str, Any] = {
        "n": len(rows),
        "brier": metrics.get("brier_score"),
        "log_loss": metrics.get("log_loss"),
        "ece": metrics.get("expected_calibration_error"),
        "calibration_slope": metrics.get("calibration_slope"),
        "calibration_intercept": metrics.get("calibration_intercept"),
        "hit_rate_favoured_side": round(favored_hits / len(rows), 4) if rows else None,
        "reliability_buckets": metrics.get("reliability_buckets"),
        "market_brier_at_closing_line": 0.25,
    }
    # Betting the side with p >= threshold at the (two-sided) ask
    for label, hurdle in (("edge_ge_0.03", 0.03), ("edge_ge_0.05", 0.05), ("edge_ge_0.08", 0.08)):
        bets = wins = 0
        units = 0.0
        for r in rows:
            p = r[prob_key]
            ask = r[ask_key] if ask_key else STANDARD_ASK
            side_p, side_ask, side_won = (
                (p, ask, bool(r[outcome_key]))
                if p >= 0.5
                else (1 - p, (1 - ask) if ask_key else STANDARD_ASK, not bool(r[outcome_key]))
            )
            if side_p - side_ask < hurdle:
                continue
            bets += 1
            wins += int(side_won)
            units += _pnl(side_p, side_ask, side_won)
        out[label] = {
            "bets": bets,
            "wins": wins,
            "hit_rate": round(wins / bets, 4) if bets else None,
            "units": round(units, 2),
            "roi": round(units / bets, 4) if bets else None,
        }
    return out


def by_season(rows: list[dict[str, Any]], prob_key: str, outcome_key: str) -> dict[str, Any]:
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[int(r["season"])].append(r)
    return {
        str(s): {
            "n": len(g),
            **{
                k: v
                for k, v in market_block(g, prob_key, outcome_key).items()
                if k in ("brier", "log_loss", "hit_rate_favoured_side", "edge_ge_0.05")
            },
        }
        for s, g in sorted(groups.items())
    }


def calibrator_params(cal: Any) -> dict[str, Any]:
    if isinstance(cal, TemperatureCalibrator):
        return {"temperature": float(cal.temperature)}
    if isinstance(cal, TrainablePlattCalibrator):
        return {"intercept": float(cal.intercept), "slope": float(cal.slope)}
    if isinstance(cal, BetaCalibrator):
        return {"a": float(cal.a), "b": float(cal.b), "c": float(cal.c)}
    if isinstance(cal, IsotonicCalibrator):
        return {"thresholds": [float(t) for t in cal.thresholds], "values": [float(v) for v in cal.values]}
    return {}


def fit_method(method: str, probs: list[float], outs: list[int], model_version: str) -> Any:
    fitters = {
        "temperature": TemperatureCalibrator.fit,
        "platt": TrainablePlattCalibrator.fit,
        "beta": BetaCalibrator.fit,
        "isotonic": IsotonicCalibrator.fit,
    }
    return fitters[method](probs, outs, model_version)


def walk_forward_calibration(
    rows: list[dict[str, Any]], prob_key: str, outcome_key: str, model_version: str, market: str
) -> dict[str, Any]:
    rows = sorted(rows, key=lambda r: r["start"])
    probs = [r[prob_key] for r in rows]
    outs = [r[outcome_key] for r in rows]
    n = len(rows)
    n_sel = int(n * 0.8)
    sel_p, sel_y = probs[:n_sel], outs[:n_sel]
    hold_p, hold_y = probs[n_sel:], outs[n_sel:]
    tourney = run_calibration_tournament(sel_p, sel_y, model_version, temporal=True)
    method = tourney["champion_method"]
    raw_hold = calibration_metrics(hold_p, hold_y)
    if method == "identity":
        cal_hold = raw_hold
        hold_cal_p = hold_p
    else:
        cal_sel = fit_method(method, sel_p, sel_y, model_version)
        hold_cal_p = [cal_sel.transform(p) for p in hold_p]
        cal_hold = calibration_metrics(hold_cal_p, hold_y)
    final = None if method == "identity" else fit_method(method, probs, outs, model_version)
    oof_brier = tourney.get("scorecard", {}).get(method, {}).get("oof_brier_score")
    raw_all = calibration_metrics(probs, outs)
    return {
        "market": market,
        "model_version": model_version,
        "n": n,
        "selection_n": n_sel,
        "holdout_n": n - n_sel,
        "holdout_start": rows[n_sel]["start"] if n_sel < n else None,
        "champion_method": method,
        "scorecard": tourney.get("scorecard"),
        "raw_brier_all": raw_all.get("brier_score"),
        "oof_brier_selection": oof_brier,
        "holdout_raw": {
            k: raw_hold.get(k)
            for k in (
                "brier_score",
                "log_loss",
                "expected_calibration_error",
                "calibration_slope",
                "calibration_intercept",
            )
        },
        "holdout_calibrated": {
            k: cal_hold.get(k)
            for k in (
                "brier_score",
                "log_loss",
                "expected_calibration_error",
                "calibration_slope",
                "calibration_intercept",
            )
        },
        "final_parameters": calibrator_params(final) if final is not None else {},
        "_final": final,
        "_holdout_calibrated_probs": hold_cal_p,
    }


def calibrator_artifact(wf: dict[str, Any], sport: str = "nfl") -> dict[str, Any]:
    return {
        "sport": sport,
        "market": wf["market"],
        "model_version": wf["model_version"],
        "champion_method": wf["champion_method"],
        "parameters": wf["final_parameters"],
        "sample_size": wf["n"],
        "baseline_brier": wf["raw_brier_all"],
        "calibrated_oof_brier": wf["oof_brier_selection"]
        if wf["champion_method"] != "identity"
        else wf["raw_brier_all"],
        "calibrated_at_utc": datetime.now(UTC).isoformat(),
        "cohort": "games.jsonl completed games with nflverse closing lines, 2021 -> today, point-in-time replay (scripts/replay_nfl_v7_all_settled.py)",
        "selection": "expanding-window OOF tournament on the first 80% by kickoff; champion refit on all games",
        "locked_holdout": {
            "n": wf["holdout_n"],
            "start": wf["holdout_start"],
            "raw": wf["holdout_raw"],
            "calibrated": wf["holdout_calibrated"],
        },
    }


# ----------------------------------------------------------------------------- flat ledger
def load_flat_rows() -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{LEDGER_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        select pick_id, event_id, event_start_utc, market_type, selection, line, model_id, model_probability,
               market_probability, units, result, pnl_units, created_at_utc, decision_payload_json, feature_payload_json
        from ledger_records
        where ledger_tier='flat' and lower(sport)='nfl' and status='settled'
        order by event_start_utc, created_at_utc
        """
    ).fetchall()
    return [dict(r) for r in rows]


def flat_replay(
    rows: list[dict[str, Any]],
    games_by_event: dict[str, dict[str, Any]],
    *,
    store: NFLEfficiencyStore,
    spread_model: NFLSpreadModelV7,
    total_model: NFLTotalModelV7,
    qbs: QBResolver,
    injuries: InjuryResolver,
    sign: int,
    calibrators: dict[str, Any],
) -> dict[str, Any]:
    scored: list[dict[str, Any]] = []
    ml_rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for r in rows:
        market = str(r["market_type"]).lower()
        if market == "moneyline":
            ml_rows.append(r)
            continue
        if r["result"] not in ("win", "loss"):
            skipped.append({"pick_id": r["pick_id"], "reason": f"result={r['result']}"})
            continue
        decision = json.loads(r["decision_payload_json"] or "{}")
        features = json.loads(r["feature_payload_json"] or "{}")
        snap_feat = ((features.get("model_input_snapshot") or {}).get("features")) or {}
        game = games_by_event.get(str(r["event_id"]))
        home = str(
            decision.get("home_team") or snap_feat.get("home_team") or (game or {}).get("home_team") or ""
        )
        away = str(
            decision.get("away_team") or snap_feat.get("away_team") or (game or {}).get("away_team") or ""
        )
        if not home or not away:
            skipped.append({"pick_id": r["pick_id"], "reason": "teams unresolved"})
            continue
        start = parse_utc(str(r["event_start_utc"]))
        observed = parse_utc(
            str(
                decision.get("market_quote_observed_at_utc")
                or decision.get("observed_at_utc")
                or r["created_at_utc"]
            )
        )
        selection = str(r["selection"]).lower()
        line = float(r["line"])
        ask = float(r["market_probability"])
        is_neutral = bool((game or {}).get("is_neutral_site", False))
        home_rest = int(snap_feat.get("home_rest_days") or (game or {}).get("home_rest_days") or 7)
        away_rest = int(snap_feat.get("away_rest_days") or (game or {}).get("away_rest_days") or 7)
        roof = str((game or {}).get("roof") or "")
        is_dome = bool(snap_feat.get("is_dome")) or roof in ("dome", "closed")
        event_day = start.astimezone(EASTERN).date().isoformat()
        w_temp, w_wind, _w_precip, w_dome = resolve_nfl_game_weather(
            DATA,
            home,
            event_day,
            event_start_utc=start.isoformat(),
            observed_at_utc=observed.isoformat(),
            is_neutral_site=is_neutral,
        )
        is_dome = is_dome or w_dome
        if market == "spread":
            spread_home_line = line if selection == "home" else -line
            total_line = float((game or {}).get("total_line") or 44.5)
        else:
            total_line = line
            spread_home_line = sign * float((game or {}).get("spread_line") or 0.0)
        priced = price_game(
            store=store,
            spread_model=spread_model,
            total_model=total_model,
            qbs=qbs,
            injuries=injuries,
            home=home,
            away=away,
            start=start,
            decision_time=observed,
            spread_home_line=spread_home_line,
            total_line=total_line,
            is_neutral=is_neutral,
            home_rest=home_rest,
            away_rest=away_rest,
            is_dome=is_dome,
            wind=w_wind if not is_dome else 0.0,
            temp=w_temp if not is_dome else 70.0,
        )
        if market == "spread":
            p_sel = priced["p_home_cover"] if selection == "home" else 1.0 - priced["p_home_cover"]
            weather_used = None
        else:
            # Live weather only where a verified pregame forecast existed for that decision time.
            p_over = (
                priced["p_over_b"]
                if (is_dome or (w_wind is not None and w_temp is not None))
                else priced["p_over_a"]
            )
            p_sel = p_over if selection == "over" else 1.0 - p_over
            weather_used = (
                "verified_forecast"
                if (w_wind is not None and w_temp is not None)
                else ("dome" if is_dome else "unavailable")
            )
        cal = calibrators.get(market)
        p_cal = float(cal.transform(p_sel)) if cal is not None else p_sel
        won = r["result"] == "win"
        scored.append(
            {
                "pick_id": r["pick_id"],
                "event_id": str(r["event_id"]),
                "start": start.isoformat(),
                "season": start.year if start.month > 2 else start.year - 1,
                "market": market,
                "selection": selection,
                "line": line,
                "ask": ask,
                "home": home,
                "away": away,
                "orig_model": r["model_id"],
                "orig_p": float(r["model_probability"]),
                "orig_pnl": float(r["pnl_units"] or 0.0),
                "orig_units": float(r["units"] or 1.0),
                "outcome": int(won),
                "new_p": p_sel,
                "new_p_calibrated": p_cal,
                "weather_used": weather_used,
                "prior_games": [priced["home_prior_games"], priced["away_prior_games"]],
                "qb_source": priced["qb_source"],
                "qb_penalty": priced["qb_penalty"],
            }
        )

    def _summary(sub: list[dict[str, Any]], p_key: str) -> dict[str, Any]:
        if not sub:
            return {"n": 0}
        probs = [s[p_key] for s in sub]
        outs = [s["outcome"] for s in sub]
        m = calibration_metrics(probs, outs, minimum_sample=10)
        bets = wins = 0
        units = 0.0
        for s in sub:
            if s[p_key] - s["ask"] >= MIN_EDGE:
                bets += 1
                wins += s["outcome"]
                units += _pnl(s[p_key], s["ask"], bool(s["outcome"]))
        all_units = sum(_pnl(s[p_key], s["ask"], bool(s["outcome"])) for s in sub)
        return {
            "n": len(sub),
            "brier": m.get("brier_score"),
            "log_loss": m.get("log_loss"),
            "ece": m.get("expected_calibration_error"),
            "hit_rate_of_recorded_selection": round(sum(outs) / len(outs), 4),
            "units_1u_every_pick_at_recorded_ask": round(all_units, 2),
            "fires_at_min_edge_0.05": {
                "bets": bets,
                "wins": wins,
                "units": round(units, 2),
                "roi": round(units / bets, 4) if bets else None,
            },
            "mean_p": round(sum(probs) / len(probs), 4),
            "mean_ask": round(sum(s["ask"] for s in sub) / len(sub), 4),
        }

    report: dict[str, Any] = {"skipped": skipped, "n_scored": len(scored), "markets": {}}
    for market in ("spread", "total"):
        sub = [s for s in scored if s["market"] == market]
        orig = {
            "n": len(sub),
            "brier": calibration_metrics(
                [s["orig_p"] for s in sub], [s["outcome"] for s in sub], minimum_sample=10
            ).get("brier_score"),
            "units_recorded": round(sum(s["orig_pnl"] for s in sub), 2),
            "models": sorted({s["orig_model"] for s in sub}),
        }
        report["markets"][market] = {
            "as_logged": orig,
            "fixed_v7_raw": _summary(sub, "new_p"),
            "fixed_v7_calibrated": _summary(sub, "new_p_calibrated"),
            "by_original_model": {
                mv: _summary([s for s in sub if s["orig_model"] == mv], "new_p")
                for mv in sorted({s["orig_model"] for s in sub})
            },
            "weather_used": dict(
                sorted(
                    defaultdict(
                        int,
                        {
                            k: sum(1 for s in sub if s["weather_used"] == k)
                            for k in {s["weather_used"] for s in sub}
                        },
                    ).items(),
                    key=lambda kv: str(kv[0]),
                )
            ),
        }
    ml_w = sum(1 for r in ml_rows if r["result"] == "win")
    ml_l = sum(1 for r in ml_rows if r["result"] == "loss")
    report["moneyline_as_logged"] = {
        "n": len(ml_rows),
        "wins": ml_w,
        "losses": ml_l,
        "pushes": len(ml_rows) - ml_w - ml_l,
        "units": round(sum(float(r["pnl_units"] or 0.0) for r in ml_rows), 2),
        "models": sorted({r["model_id"] for r in ml_rows}),
        "note": "moneyline champion is nfl-elo-trend-lr-v4 (logistic regression), not re-priced by the v7 engines",
    }
    report["rows"] = scored
    return report


# ----------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-write", action="store_true", help="compute and report only; write no artifacts")
    args = ap.parse_args()

    snapshot_dir = latest_snapshot_dir()
    store, store_manifest = build_store(snapshot_dir)
    schedule = pl.read_parquet(snapshot_dir / "schedule.parquet")
    sp_art, tot_art, spread_model, total_model = load_models()
    qbs = QBResolver(snapshot_dir)
    injuries = InjuryResolver()
    games = load_games()
    sign = spread_sign_convention(games, schedule)
    print(f"games.jsonl spread_line -> spread_home_line multiplier: {sign}")

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
    print(f"cohort: {len(cohort)} completed games with closing lines (2021 ->)")

    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for g in cohort:
        start = parse_utc(g["event_start_utc"])
        decision_time = start - DECISION_LEAD
        spread_home_line = sign * float(g["spread_line"])
        total_line = float(g["total_line"])
        roof = str(g.get("roof") or "")
        is_dome = roof in ("dome", "closed")
        wind = None if is_dome or g.get("wind") is None else float(g["wind"])
        temp = None if is_dome or g.get("temp") is None else float(g["temp"])
        try:
            priced = price_game(
                store=store,
                spread_model=spread_model,
                total_model=total_model,
                qbs=qbs,
                injuries=injuries,
                home=g["home_team"],
                away=g["away_team"],
                start=start,
                decision_time=decision_time,
                spread_home_line=spread_home_line,
                total_line=total_line,
                is_neutral=bool(g.get("is_neutral_site", False)),
                home_rest=int(g.get("home_rest_days") or 7),
                away_rest=int(g.get("away_rest_days") or 7),
                is_dome=is_dome,
                wind=wind,
                temp=temp,
            )
        except Exception as exc:  # noqa: BLE001 -- report every failure, skip nothing silently
            failures.append({"event_id": g["event_id"], "error": f"{type(exc).__name__}: {exc}"})
            continue
        margin = int(g["home_score"]) - int(g["away_score"])
        total = int(g["home_score"]) + int(g["away_score"])
        cover_val = margin + spread_home_line
        rows.append(
            {
                "event_id": g["event_id"],
                "start": start.isoformat(),
                "season": int(g["season_year"]),
                "home": g["home_team"],
                "away": g["away_team"],
                "spread_home_line": spread_home_line,
                "total_line": total_line,
                "is_dome": is_dome,
                "wind": wind,
                "temp": temp,
                "margin": margin,
                "total": total,
                "home_cover": None if cover_val == 0 else int(cover_val > 0),
                "over": None if total == total_line else int(total > total_line),
                **priced,
            }
        )
    print(f"priced {len(rows)} games, {len(failures)} failures")

    spread_rows = [r for r in rows if r["home_cover"] is not None]
    total_rows = [r for r in rows if r["over"] is not None]
    report: dict[str, Any] = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "artifacts": {"spread": sp_art["artifact_hash"], "total": tot_art["artifact_hash"]},
        "store": store_manifest,
        "cohort": {
            "games_priced": len(rows),
            "failures": failures,
            "seasons": sorted({r["season"] for r in rows}),
            "spread_decided": len(spread_rows),
            "spread_pushes": len(rows) - len(spread_rows),
            "total_decided": len(total_rows),
            "total_pushes": len(rows) - len(total_rows),
            "both_teams_have_prior_games": sum(
                1 for r in rows if r["home_prior_games"] > 0 and r["away_prior_games"] > 0
            ),
            "qb_source": dict(
                defaultdict(
                    int,
                    {k: sum(1 for r in rows if r["qb_source"] == k) for k in {r["qb_source"] for r in rows}},
                )
            ),
            "games_with_qb_injury_penalty": sum(1 for r in rows if r["qb_penalty"] > 0),
            "games_with_recorded_weather": sum(1 for r in rows if r["wind"] is not None),
        },
        "spread": {
            "all": market_block(spread_rows, "p_home_cover", "home_cover"),
            "by_season": by_season(spread_rows, "p_home_cover", "home_cover"),
            "heavy_lines_abs_ge_8": market_block(
                [r for r in spread_rows if abs(r["spread_home_line"]) >= 8.0], "p_home_cover", "home_cover"
            ),
        },
        "total": {
            "variant_a_no_weather": market_block(total_rows, "p_over_a", "over"),
            "variant_b_recorded_weather": market_block(total_rows, "p_over_b", "over"),
            "by_season_variant_b": by_season(total_rows, "p_over_b", "over"),
            "games_where_weather_changed_total": sum(
                1 for r in total_rows if abs(r["total_b"] - r["total_a"]) > 1e-9
            ),
        },
    }

    wf_spread = walk_forward_calibration(
        spread_rows, "p_home_cover", "home_cover", sp_art["model_version"], "spread"
    )
    wf_total = walk_forward_calibration(total_rows, "p_over_b", "over", tot_art["model_version"], "total")
    report["calibration"] = {
        "spread": {k: v for k, v in wf_spread.items() if not k.startswith("_")},
        "total": {k: v for k, v in wf_total.items() if not k.startswith("_")},
    }

    games_by_event = {str(g["event_id"]): g for g in games}
    flat = flat_replay(
        load_flat_rows(),
        games_by_event,
        store=store,
        spread_model=spread_model,
        total_model=total_model,
        qbs=qbs,
        injuries=injuries,
        sign=sign,
        calibrators={"spread": wf_spread["_final"], "total": wf_total["_final"]},
    )
    report["flat_ledger_replay"] = {k: v for k, v in flat.items() if k != "rows"}
    report["flat_ledger_rows"] = flat["rows"]

    out_dir = REPO / "outputs" / "nfl"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "nfl_v7_replay.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    (out_dir / "nfl_v7_replay.md").write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))

    if args.no_write:
        print("--no-write: calibrator and model artifacts untouched")
        return
    cal_dir = DATA / "calibration" / "models"
    cal_dir.mkdir(parents=True, exist_ok=True)
    for wf in (wf_spread, wf_total):
        path = cal_dir / f"{wf['model_version']}.{wf['market']}_calibrator.json"
        path.write_text(
            json.dumps(calibrator_artifact(wf), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print("wrote", path, "sha256", hashlib.sha256(path.read_bytes()).hexdigest()[:12])
    for art, name, wf, block in (
        (sp_art, "nfl-spread-structural-v7", wf_spread, report["spread"]["all"]),
        (tot_art, "nfl-total-structural-v7", wf_total, report["total"]["variant_b_recorded_weather"]),
    ):
        art.setdefault("qualification", {})
        art["qualification"]["replay"] = {
            "generated_at_utc": report["generated_at_utc"],
            "cohort": "games.jsonl completed games with nflverse closing lines, 2021 -> today; point-in-time inputs; scripts/replay_nfl_v7_all_settled.py",
            "n": block["n"],
            "brier": block["brier"],
            "log_loss": block["log_loss"],
            "calibration_slope": block["calibration_slope"],
            "calibration_intercept": block["calibration_intercept"],
            "market_brier_at_closing_line": 0.25,
            "locked_holdout": wf["holdout_raw"] | {"n": wf["holdout_n"], "start": wf["holdout_start"]},
            "serving_calibrator": wf["champion_method"],
        }
        art["artifact_hash"] = compute_artifact_hash(art)
        (REPO / "config" / "models" / f"{name}.json").write_text(
            json.dumps(art, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print("updated", name, "hash", art["artifact_hash"][:12])


def _fmt(x: Any, nd: int = 4) -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def render_markdown(report: dict[str, Any]) -> str:
    c = report["cohort"]
    lines = [
        "# NFL v7 point-in-time replay and calibration",
        "",
        f"Generated {report['generated_at_utc']} -- spread artifact `{report['artifacts']['spread'][:12]}`, total artifact `{report['artifacts']['total'][:12]}`.",
        "",
        "## Cohort",
        "",
        f"- {c['games_priced']} completed games with closing lines, seasons {c['seasons'][0]}-{c['seasons'][-1]}; {len(c['failures'])} failures.",
        f"- spread decided {c['spread_decided']} (pushes {c['spread_pushes']}); total decided {c['total_decided']} (pushes {c['total_pushes']}).",
        f"- both teams had prior-season-games efficiency data in {c['both_teams_have_prior_games']} games; QB source {c['qb_source']}; QB injury penalty applied in {c['games_with_qb_injury_penalty']} games; recorded weather in {c['games_with_recorded_weather']} games.",
        "",
        "## Spread (P(home cover) vs closing line; market Brier at the closing line = 0.2500)",
        "",
    ]
    s = report["spread"]["all"]
    lines += [
        f"- n {s['n']}: Brier {_fmt(s['brier'])}, log loss {_fmt(s['log_loss'])}, ECE {_fmt(s['ece'])}, slope {_fmt(s['calibration_slope'], 3)}, intercept {_fmt(s['calibration_intercept'], 3)}, favoured-side hit {_fmt(s['hit_rate_favoured_side'])}.",
    ]
    for k in ("edge_ge_0.03", "edge_ge_0.05", "edge_ge_0.08"):
        e = s[k]
        lines.append(
            f"- bets at 52.38% ask with {k.replace('edge_ge_', 'edge >= ')}: {e['bets']} bets, hit {_fmt(e['hit_rate'])}, {e['units']:+.2f}u, ROI {_fmt(e['roi'])}."
        )
    h = report["spread"]["heavy_lines_abs_ge_8"]
    lines.append(
        f"- |line| >= 8 subset: n {h['n']}, Brier {_fmt(h['brier'])}, favoured-side hit {_fmt(h['hit_rate_favoured_side'])}."
    )
    lines += [
        "",
        "| season | n | Brier | log loss | favoured hit | edge>=5% bets | units |",
        "|---|---|---|---|---|---|---|",
    ]
    for season, b in report["spread"]["by_season"].items():
        e = b["edge_ge_0.05"]
        lines.append(
            f"| {season} | {b['n']} | {_fmt(b['brier'])} | {_fmt(b['log_loss'])} | {_fmt(b['hit_rate_favoured_side'])} | {e['bets']} | {e['units']:+.2f} |"
        )
    lines += ["", "## Total (P(over) vs closing line)", ""]
    for label, key in (
        ("A: weather unavailable", "variant_a_no_weather"),
        ("B: recorded weather", "variant_b_recorded_weather"),
    ):
        t = report["total"][key]
        e = t["edge_ge_0.05"]
        lines.append(
            f"- {label}: n {t['n']}, Brier {_fmt(t['brier'])}, log loss {_fmt(t['log_loss'])}, ECE {_fmt(t['ece'])}, slope {_fmt(t['calibration_slope'], 3)}, favoured-side hit {_fmt(t['hit_rate_favoured_side'])}; edge>=5%: {e['bets']} bets, {e['units']:+.2f}u."
        )
    lines.append(
        f"- weather changed the projected total in {report['total']['games_where_weather_changed_total']} games."
    )
    lines += [
        "",
        "| season | n | Brier (B) | log loss | favoured hit | edge>=5% bets | units |",
        "|---|---|---|---|---|---|---|",
    ]
    for season, b in report["total"]["by_season_variant_b"].items():
        e = b["edge_ge_0.05"]
        lines.append(
            f"| {season} | {b['n']} | {_fmt(b['brier'])} | {_fmt(b['log_loss'])} | {_fmt(b['hit_rate_favoured_side'])} | {e['bets']} | {e['units']:+.2f} |"
        )
    lines += ["", "## Walk-forward calibration (select on first 80%, score once on locked last 20%)", ""]
    for m in ("spread", "total"):
        w = report["calibration"][m]
        lines.append(
            f"- {m}: champion `{w['champion_method']}` (selection n {w['selection_n']}, OOF Brier {_fmt(w['oof_brier_selection'])} vs raw {_fmt(w['raw_brier_all'])}); "
            f"holdout n {w['holdout_n']} from {str(w['holdout_start'])[:10]}: raw Brier {_fmt(w['holdout_raw']['brier_score'])} / log loss {_fmt(w['holdout_raw']['log_loss'])} -> "
            f"calibrated {_fmt(w['holdout_calibrated']['brier_score'])} / {_fmt(w['holdout_calibrated']['log_loss'])}; final parameters {w['final_parameters'] if w['champion_method'] != 'isotonic' else '(isotonic table)'}."
        )
    f = report["flat_ledger_replay"]
    lines += [
        "",
        f"## Flat-ledger replay ({f['n_scored']} settled spread/total picks re-priced at their own line and ask; {len(f['skipped'])} skipped)",
        "",
    ]
    for m in ("spread", "total"):
        b = f["markets"][m]
        al, raw, cal = b["as_logged"], b["fixed_v7_raw"], b["fixed_v7_calibrated"]
        lines.append(
            f"- {m}: {al['n']} picks as logged ({', '.join(al['models'])}) Brier {_fmt(al['brier'])}, recorded {al['units_recorded']:+.2f}u."
        )
        if raw.get("n"):
            fr, fc = raw["fires_at_min_edge_0.05"], cal["fires_at_min_edge_0.05"]
            lines.append(
                f"  - fixed v7 raw: Brier {_fmt(raw['brier'])}, 1u on every recorded selection {raw['units_1u_every_pick_at_recorded_ask']:+.2f}u; fires at edge>=5%: {fr['bets']} bets, {fr['wins']} wins, {fr['units']:+.2f}u."
            )
            lines.append(
                f"  - fixed v7 calibrated: Brier {_fmt(cal['brier'])}, 1u on every recorded selection {cal['units_1u_every_pick_at_recorded_ask']:+.2f}u; fires at edge>=5%: {fc['bets']} bets, {fc['wins']} wins, {fc['units']:+.2f}u."
            )
            lines.append(f"  - weather used: {b['weather_used']}.")
    ml = f["moneyline_as_logged"]
    lines.append(
        f"- moneyline as logged ({', '.join(ml['models'])}): {ml['n']} picks, {ml['wins']}W/{ml['losses']}L/{ml['pushes']}P, {ml['units']:+.2f}u. {ml['note']}."
    )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
