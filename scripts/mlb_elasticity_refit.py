#!/usr/bin/env python3
"""Refit MLB Trend Engine elasticities (offense, starter_weakness, park,
weather) via a real Poisson GLM against completed games' actual runs scored.

Saved, rerunnable research script -- unlike the 2026-07-30 one-off fit (no
surviving code), this persists its own on-disk feature cache so repeated
development runs don't re-hit ESPN for games already reconstructed.

Reuses ESPNMLBClient.reconstructed_features (the same function
forward.py::build_mlb_slate calls live) and models.mlb._offense_index /
_starter_weakness (the same functions estimate_runs() calls live) for
feature construction, so the design matrix here matches production feature
construction exactly rather than risking train/serve skew from a
reimplementation.

Usage:
    env PYTHONPATH=src:. .venv/bin/python scripts/mlb_elasticity_refit.py \
        --start 2026-06-03 --end 2026-08-02 \
        --output config/models/mlb-analyst-poisson-trend-v0.3.yaml
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from datetime import date, timedelta
from math import log
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import yaml
from sklearn.linear_model import PoissonRegressor

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

# BASE_SPEC_PATH is, by construction, whatever formula this script is
# refitting FROM -- not necessarily the live ENGINE_VERSION (that's exactly
# what a promotion moves forward). Uses the unchecked loader (same one
# mlb_measured_edge_calibrate.py relies on for the identical reason) so this
# script keeps working across a promotion instead of hard-failing the moment
# BASE_SPEC_PATH's own file stops being the live ENGINE_VERSION.
from mlb_measured_edge_calibrate import load_formula_spec_unchecked as load_formula_spec

from model_prediction.data_sources.espn import ESPNMLBClient
from model_prediction.models.mlb import (
    FormulaSpec,
    PitcherForm,
    TeamForm,
    _clip,
    _offense_index,
    _starter_weakness,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("mlb_elasticity_refit")

CACHE_PATH = PROJECT_ROOT / "data" / "research" / "mlb_elasticity_feature_cache.jsonl"
BASE_SPEC_PATH = PROJECT_ROOT / "config" / "models" / "mlb-analyst-poisson-trend-v0.2.yaml"
FACTOR_NAMES = ("offense", "starter_weakness", "park", "weather")
ALPHA = 0.05  # L2 regularization; matches v0.2's documented choice.


def _load_cache() -> dict[str, dict[str, Any]]:
    if not CACHE_PATH.exists():
        return {}
    cache: dict[str, dict[str, Any]] = {}
    with CACHE_PATH.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            cache[row["event_id"]] = row
    return cache


def _append_cache(row: dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CACHE_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def collect_rows(start: date, end: date, client: ESPNMLBClient) -> list[dict[str, Any]]:
    """One row per completed game with real raw features + real final score.

    Games where ESPN's boxscore no longer resolves a probable starter (most
    completed games well after the fact -- ESPN's `probables` field is a
    pregame artifact) are silently skipped, exactly like build_mlb_slate's
    own skip handling; only games captured close enough to game time keep
    that field populated.
    """
    cache = _load_cache()
    rows = list(cache.values())
    seen = set(cache)
    cursor = start
    fetched = skipped = 0
    while cursor <= end:
        game_date = cursor.isoformat()
        try:
            scoreboard = client.scoreboard(game_date)
        except Exception:
            logger.warning("scoreboard fetch failed for %s", game_date, exc_info=True)
            cursor += timedelta(days=1)
            continue
        for event in scoreboard.get("events", []):
            event_id = str(event.get("id", ""))
            if not event_id or event_id in seen:
                continue
            competition = (event.get("competitions") or [{}])[0]
            status = competition.get("status", {}).get("type", {})
            if not status.get("completed"):
                continue
            competitors = {c.get("homeAway"): c for c in competition.get("competitors", [])}
            away_c, home_c = competitors.get("away"), competitors.get("home")
            if away_c is None or home_c is None:
                seen.add(event_id)
                continue
            try:
                away_score = int(away_c["score"])
                home_score = int(home_c["score"])
            except (KeyError, TypeError, ValueError):
                seen.add(event_id)
                continue
            try:
                features = client.reconstructed_features(event)
            except (KeyError, TypeError, ValueError):
                # Genuinely unresolved (e.g. no probable starter on record for
                # this old game) -- permanent for this event, skip for good.
                skipped += 1
                seen.add(event_id)
                continue
            except httpx.HTTPError:
                # Transient network/server failure (e.g. a live 500) -- do
                # NOT mark seen, so a rerun retries this event instead of
                # silently and permanently losing it. Log and move on rather
                # than crashing the whole multi-hour collection run over one
                # bad request.
                logger.warning("reconstructed_features failed for event %s", event_id, exc_info=True)
                continue
            row = {
                "event_id": event_id,
                "game_date": game_date,
                "away_team": features.away_team,
                "home_team": features.home_team,
                "away_score": away_score,
                "home_score": home_score,
                "away_form": asdict(features.away_form),
                "home_form": asdict(features.home_form),
                "away_starter": asdict(features.away_starter),
                "home_starter": asdict(features.home_starter),
                "park_factor": features.park_factor,
                "weather_factor": features.weather_factor,
                "away_bullpen_weakness": features.away_bullpen_weakness,
                "home_bullpen_weakness": features.home_bullpen_weakness,
            }
            rows.append(row)
            seen.add(event_id)
            _append_cache(row)
            fetched += 1
        cursor += timedelta(days=1)
    logger.info(
        "collected %d new rows this run (%d cached from prior runs), %d skipped (unresolved probable starter)",
        fetched,
        len(cache),
        skipped,
    )
    return rows


def _team_form(payload: dict[str, Any]) -> TeamForm:
    return TeamForm(
        runs_scored=tuple(payload["runs_scored"]),
        runs_allowed=tuple(payload["runs_allowed"]),
        wins=payload["wins"],
        losses=payload["losses"],
        status=payload.get("status", "available"),
    )


def _pitcher_form(payload: dict[str, Any]) -> PitcherForm:
    fields = {k: v for k, v in payload.items()}
    return PitcherForm(**fields)


def build_design_matrix(
    rows: list[dict[str, Any]], spec: FormulaSpec, *, include_bullpen: bool
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Two rows per real game: the away team's and the home team's actual
    runs scored, each against the log of the factors that formula_version's
    estimate_runs() raises to an elasticity for that side. Fitting a Poisson
    GLM with a log link on these log-transformed covariates recovers exactly
    the elasticity exponents estimate_runs() multiplies together.
    """
    design: list[list[float]] = []
    target: list[int] = []
    game_dates: list[str] = []
    for row in rows:
        away_form = _team_form(row["away_form"])
        home_form = _team_form(row["home_form"])
        away_starter = _pitcher_form(row["away_starter"])
        home_starter = _pitcher_form(row["home_starter"])
        away_offense = _offense_index(away_form, spec)
        home_offense = _offense_index(home_form, spec)
        away_starter_weakness = _starter_weakness(away_starter, spec)
        home_starter_weakness = _starter_weakness(home_starter, spec)
        away_bullpen = _clip(row["away_bullpen_weakness"], spec.factor_bounds["bullpen_weakness"])
        home_bullpen = _clip(row["home_bullpen_weakness"], spec.factor_bounds["bullpen_weakness"])
        park = _clip(row["park_factor"], spec.factor_bounds["park"])
        weather = _clip(row["weather_factor"], spec.factor_bounds["weather"])

        away_covariates = [log(away_offense), log(home_starter_weakness), log(park), log(weather)]
        home_covariates = [log(home_offense), log(away_starter_weakness), log(park), log(weather)]
        if include_bullpen:
            away_covariates.append(log(home_bullpen))
            home_covariates.append(log(away_bullpen))
        design.append(away_covariates)
        target.append(row["away_score"])
        game_dates.append(row["game_date"])
        design.append(home_covariates)
        target.append(row["home_score"])
        game_dates.append(row["game_date"])
    return np.array(design), np.array(target), game_dates


def chronological_folds(game_dates: list[str], k: int = 4) -> list[dict[str, Any]]:
    """Expanding-window CV: fold i trains on chunks[0..i], validates on
    chunk i+1, for i in 0..k-1 -- same structure as the v0.2 fit, but this
    time the fold-level metrics are returned, not just the final numbers.
    """
    unique_dates = sorted(set(game_dates))
    chunks = np.array_split(np.array(unique_dates), k + 1)
    chunk_sets = [set(chunk.tolist()) for chunk in chunks]
    folds = []
    for i in range(k):
        train_dates = set().union(*chunk_sets[: i + 1])
        test_dates = chunk_sets[i + 1]
        folds.append({"fold": i, "train_dates": train_dates, "test_dates": test_dates})
    return folds


def run_cv(
    design: np.ndarray, target: np.ndarray, game_dates: list[str], *, alpha: float = ALPHA
) -> list[dict[str, Any]]:
    dates_array = np.array(game_dates)
    fold_results = []
    for fold in chronological_folds(game_dates):
        train_mask = np.isin(dates_array, list(fold["train_dates"]))
        test_mask = np.isin(dates_array, list(fold["test_dates"]))
        if train_mask.sum() < 10 or test_mask.sum() < 10:
            continue
        model = PoissonRegressor(alpha=alpha, max_iter=500)
        model.fit(design[train_mask], target[train_mask])
        predicted = model.predict(design[test_mask])
        actual = target[test_mask]
        correlation = float(np.corrcoef(predicted, actual)[0, 1]) if len(actual) > 1 else float("nan")
        mae = float(np.mean(np.abs(predicted - actual)))
        fold_results.append(
            {
                "fold": fold["fold"],
                "train_games": int(train_mask.sum() / 2),
                "test_games": int(test_mask.sum() / 2),
                "held_out_correlation": round(correlation, 4),
                "held_out_mae": round(mae, 4),
                "coefficients": [round(float(c), 6) for c in model.coef_],
            }
        )
    return fold_results


def fit_final(design: np.ndarray, target: np.ndarray, *, alpha: float = ALPHA) -> PoissonRegressor:
    model = PoissonRegressor(alpha=alpha, max_iter=500)
    model.fit(design, target)
    return model


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "config/models/mlb-analyst-poisson-trend-v0.3.yaml"),
    )
    args = parser.parse_args()

    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    spec = load_formula_spec(BASE_SPEC_PATH)
    client = ESPNMLBClient()

    rows = collect_rows(start, end, client)
    if len(rows) < 50:
        raise SystemExit(f"only {len(rows)} usable games collected -- too few to refit safely")

    # Primary fit: offense/starter_weakness/park/weather only, bullpen forced
    # to 0.0 -- unless the bullpen-included fit below shows a real, stable,
    # positive coefficient across every fold, overturning the v0.2 finding.
    design, target, game_dates = build_design_matrix(rows, spec, include_bullpen=False)
    fold_results = run_cv(design, target, game_dates)
    final_model = fit_final(design, target)
    elasticities = dict(zip(FACTOR_NAMES, (round(float(c), 6) for c in final_model.coef_), strict=True))

    bullpen_design, bullpen_target, bullpen_dates = build_design_matrix(rows, spec, include_bullpen=True)
    bullpen_fold_results = run_cv(bullpen_design, bullpen_target, bullpen_dates)
    bullpen_coefs = [fold["coefficients"][-1] for fold in bullpen_fold_results]
    bullpen_overturned = bool(bullpen_coefs) and all(c > 0 for c in bullpen_coefs)
    bullpen_elasticity = round(float(np.mean(bullpen_coefs)), 6) if bullpen_overturned else 0.0

    unique_games = len({row["event_id"] for row in rows})
    trained_through = max(row["game_date"] for row in rows)
    result = {
        "rows_collected": len(rows),
        "unique_games": unique_games,
        "trained_through_date": trained_through,
        "elasticities": {**elasticities, "bullpen": bullpen_elasticity},
        "bullpen_overturned_selection_bias_finding": bullpen_overturned,
        "bullpen_fold_coefficients": bullpen_coefs,
        "fold_results": fold_results,
        "cache_path": str(CACHE_PATH),
    }
    print(json.dumps(result, indent=2, default=str))

    write_yaml(
        args.output, spec, elasticities, bullpen_elasticity, unique_games, trained_through, fold_results
    )
    logger.info("wrote %s", args.output)


def write_yaml(
    output_path: str,
    spec: FormulaSpec,
    elasticities: dict[str, float],
    bullpen_elasticity: float,
    unique_games: int,
    trained_through: str,
    fold_results: list[dict[str, Any]],
) -> None:
    """Write a new versioned formula spec, carrying every non-elasticity
    field forward unchanged from v0.2 -- this script refits only the five
    run-scaling elasticities, not the shrinkage priors, uncertainty
    components, or simulation parameters.
    """
    raw = yaml.safe_load(BASE_SPEC_PATH.read_bytes())
    raw["formula_version"] = "mlb-analyst-poisson-trend-v0.3"
    raw["offense_elasticity"] = elasticities["offense"]
    raw["starter_weakness_elasticity"] = elasticities["starter_weakness"]
    raw["park_elasticity"] = elasticities["park"]
    raw["weather_elasticity"] = elasticities["weather"]
    raw["bullpen_elasticity"] = bullpen_elasticity
    correlations = ", ".join(f"fold{fold['fold']}={fold['held_out_correlation']}" for fold in fold_results)
    raw["_refit_note"] = (
        f"Refit {trained_through[:7]} by scripts/mlb_elasticity_refit.py against "
        f"{unique_games} real completed games (see rows_collected in the script's own "
        f"stdout for the two-row-per-game design matrix size). Held-out correlation by "
        f"chronological expanding-window fold: {correlations}. "
        f"bullpen_elasticity stays forced to 0.0 unless a bullpen-included fit shows a "
        f"positive coefficient in every fold (see bullpen_overturned_selection_bias_finding "
        f"in the script's stdout); this run's result is bullpen_elasticity={bullpen_elasticity}."
    )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(raw, handle, sort_keys=True, default_flow_style=False)


if __name__ == "__main__":
    main()
