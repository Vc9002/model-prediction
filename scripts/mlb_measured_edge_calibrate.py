#!/usr/bin/env python3
"""Refit Measured Edge margin/totals scale+offset against a rebuilt Trend
Engine formula (see scripts/mlb_elasticity_refit.py) and fresh game data.

Reports two calibration windows separately rather than blending them:

- ``diagnostic``: data/historical/mlb_market_lines_reconstructed.jsonl,
  ESPN's postgame pickcenter open/close lines. Larger sample, but
  ingest.py::reconstruct_mlb_markets hard-labels these rows
  ``timestamp_valid: False`` -- ESPN gives no guarantee that a
  postgame-retrieved summary is an immutable point-in-time archive.
  Diagnostic only, matching v1's own precedent for the primary fit.
- ``real_market``: data/odds/mlb/<date>/polymarket_snapshots.jsonl, genuine
  prospectively captured executable Polymarket BBO. Small (grows only as
  fast as this project has been capturing snapshots -- currently ~2.5
  weeks), but real.

Only the diagnostic window's fit is written as the artifact's top-level
scale/offset (matching v1's precedent and its larger sample); the
real-market window's fit is stored alongside as a corroborating check, not
blended into it.

Usage:
    env PYTHONPATH=src:. .venv/bin/python scripts/mlb_measured_edge_calibrate.py \
        --formula config/models/mlb-analyst-poisson-trend-v0.2.yaml \
        --feature-cache data/research/mlb_elasticity_feature_cache.jsonl \
        --odds-start 2026-07-17 --odds-end 2026-08-02 \
        --output-margin config/models/measured-edge-margin-v2.json \
        --output-totals config/models/measured-edge-totals-v2.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from model_prediction.domain import MarketType, parse_utc
from model_prediction.models.mlb import (
    FormulaSpec,
    MLBGameFeatures,
    PitcherForm,
    TeamForm,
    derive_market_distribution,
    estimate_runs,
    simulate_game,
)

GAMES_ALL_PATH = PROJECT_ROOT / "data" / "historical" / "mlb_games_all.jsonl"
MARKET_LINES_PATH = PROJECT_ROOT / "data" / "historical" / "mlb_market_lines_reconstructed.jsonl"
ODDS_ROOT = PROJECT_ROOT / "data" / "odds" / "mlb"
_PARTIAL_GAME_MARKERS = ("-f5-", "-f3-", "-f7-", "-1st-", "-h1-", "-h2-")


def load_formula_spec_unchecked(path: str | Path) -> FormulaSpec:
    """Same field construction as models.mlb.load_formula_spec, minus the
    ENGINE_VERSION equality gate -- appropriate here because this script
    computes estimate_runs()/simulate_game() directly against a candidate
    formula version that is, by definition, not yet the production
    ENGINE_VERSION. The production MeasuredEdgeMarginModel/TotalsModel
    classes keep their own strict gate; this script never touches them.
    """
    path = Path(path)
    raw_bytes = path.read_bytes()
    raw = yaml.safe_load(raw_bytes)
    bounds = {name: tuple(values) for name, values in raw["factor_bounds"].items()}
    return FormulaSpec(
        formula_version=raw["formula_version"],
        feature_schema_version=raw["feature_schema_version"],
        league_runs_per_team_game=float(raw["league_runs_per_team_game"]),
        league_starter_era=float(raw["league_starter_era"]),
        league_strikeout_rate=float(raw["league_strikeout_rate"]),
        league_walk_rate=float(raw["league_walk_rate"]),
        recent_half_life_games=float(raw["recent_half_life_games"]),
        recent_prior_strength_games=float(raw["recent_prior_strength_games"]),
        starter_season_prior_innings=float(raw["starter_season_prior_innings"]),
        starter_recent_prior_innings=float(raw["starter_recent_prior_innings"]),
        starter_rate_prior_batters_faced=float(raw["starter_rate_prior_batters_faced"]),
        starter_recent_weight=float(raw["starter_recent_weight"]),
        starter_season_weight=float(raw["starter_season_weight"]),
        strikeout_weight=float(raw["strikeout_weight"]),
        walk_weight=float(raw["walk_weight"]),
        home_field_run_factor=float(raw["home_field_run_factor"]),
        away_field_run_factor=float(raw["away_field_run_factor"]),
        offense_elasticity=float(raw["offense_elasticity"]),
        starter_weakness_elasticity=float(raw["starter_weakness_elasticity"]),
        bullpen_elasticity=float(raw["bullpen_elasticity"]),
        park_elasticity=float(raw["park_elasticity"]),
        weather_elasticity=float(raw["weather_elasticity"]),
        factor_bounds=bounds,
        uncertainty={key: float(value) for key, value in raw["uncertainty"].items()},
        simulation=raw["simulation"],
        seed_method=raw["seed_method"],
        configuration_hash=hashlib.sha256(raw_bytes).hexdigest(),
    )


def _load_feature_cache(path: Path) -> dict[str, dict[str, Any]]:
    cache: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return cache
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            cache[row["event_id"]] = row
    return cache


def _load_games_all(path: Path) -> dict[str, dict[str, Any]]:
    games: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("status") == "completed":
                games[str(row["event_id"])] = row
    return games


def _team_form_from_cache(payload: dict[str, Any]) -> TeamForm:
    return TeamForm(
        runs_scored=tuple(payload["runs_scored"]),
        runs_allowed=tuple(payload["runs_allowed"]),
        wins=payload["wins"],
        losses=payload["losses"],
        status=payload.get("status", "available"),
    )


def _features_from_cache_row(row: dict[str, Any]) -> MLBGameFeatures:
    return MLBGameFeatures(
        event_id=row["event_id"],
        event_start_utc="",
        decision_timestamp_utc=row["game_date"] + "T00:00:00Z",
        away_team=row["away_team"],
        home_team=row["home_team"],
        away_form=_team_form_from_cache(row["away_form"]),
        home_form=_team_form_from_cache(row["home_form"]),
        away_starter=PitcherForm(**row["away_starter"]),
        home_starter=PitcherForm(**row["home_starter"]),
        away_bullpen_weakness=row["away_bullpen_weakness"],
        home_bullpen_weakness=row["home_bullpen_weakness"],
        park_factor=row["park_factor"],
        weather_factor=row["weather_factor"],
    )


def raw_probabilities(
    features: MLBGameFeatures, spec: FormulaSpec, spread_line: float | None, total_line: float | None
) -> dict[str, float]:
    """Raw (pre-calibration) simulated cover probabilities for the selected
    side of each market -- the exact quantity MeasuredEdgeMarginModel/
    TotalsModel.calibrate_selected_side() maps to a calibrated probability.
    """
    estimate = estimate_runs(features, spec)
    simulation = simulate_game(features, estimate, spec)
    out: dict[str, float] = {}
    if spread_line is not None:
        spread = derive_market_distribution(simulation, MarketType.SPREAD, spread_line)
        out["spread_away_probability"] = spread.first_win_probability
        out["spread_home_probability"] = spread.second_win_probability
    if total_line is not None:
        total = derive_market_distribution(simulation, MarketType.TOTAL, total_line)
        out["total_over_probability"] = total.first_win_probability
        out["total_under_probability"] = total.second_win_probability
    return out


def diagnostic_window_rows(
    spec: FormulaSpec, feature_cache: dict[str, dict[str, Any]], games: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Join reconstructed market lines + cached features + real final scores."""
    rows: list[dict[str, Any]] = []
    if not MARKET_LINES_PATH.exists():
        return rows
    with MARKET_LINES_PATH.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            market_row = json.loads(line)
            event_id = str(market_row["event_id"])
            feature_row = feature_cache.get(event_id)
            game = games.get(event_id)
            if feature_row is None or game is None:
                continue
            markets = market_row.get("markets", {})
            spread = markets.get("spread", {})
            total = markets.get("total", {})
            spread_line = (spread.get("away") or {}).get("closing_line") or (spread.get("away") or {}).get("line")
            total_line = (total.get("over") or {}).get("closing_line") or (total.get("over") or {}).get("line")
            if spread_line is None and total_line is None:
                continue
            features = _features_from_cache_row(feature_row)
            probs = raw_probabilities(features, spec, spread_line, total_line)
            rows.append(
                {
                    "event_id": event_id,
                    "away_score": int(game["away_score"]),
                    "home_score": int(game["home_score"]),
                    "spread_line": spread_line,
                    "total_line": total_line,
                    **probs,
                }
            )
    return rows


def _closing_full_game_lines(day_path: Path, event_start_utc: str) -> dict[str, dict[str, Any]]:
    """Per event_id: the closing snapshot of the most-balanced full-game
    spread and total market, mirroring mlb_market_odds.py's
    _select_full_game_market (partial-game slugs excluded, most-balanced
    line wins among full-game alternates) applied to the CLOSING snapshot
    of each candidate line rather than its initial listing.
    """
    if not day_path.exists():
        return {}
    event_start = parse_utc(event_start_utc)
    by_slug: dict[tuple[str, str], tuple[Any, dict[str, Any]]] = {}
    with day_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            slug = str(row.get("market_slug") or "")
            if not slug or any(marker in slug.casefold() for marker in _PARTIAL_GAME_MARKERS):
                continue
            market_type = row.get("market_type")
            if market_type not in ("spread", "total"):
                continue
            try:
                observed = parse_utc(row["observed_at_utc"])
            except (KeyError, ValueError):
                continue
            if observed > event_start:
                continue
            key = (row["event_id"], slug)
            existing = by_slug.get(key)
            if existing is None or observed > existing[0]:
                by_slug[key] = (observed, row)
    by_event_market: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for (event_id, _slug), (_observed, row) in by_slug.items():
        by_event_market.setdefault((event_id, row["market_type"]), []).append(row)

    def _balance(row: dict[str, Any]) -> float:
        midpoint = (row.get("long") or {}).get("midpoint")
        return abs(float(midpoint) - 0.5) if midpoint is not None else 0.5

    result: dict[str, dict[str, Any]] = {}
    for (event_id, market_type), candidates in by_event_market.items():
        best = min(candidates, key=_balance)
        result.setdefault(event_id, {})[market_type] = best
    return result


def _team_identity(value: str) -> str:
    return " ".join(str(value).casefold().split())


def _feature_cache_by_team_pair(
    feature_cache: dict[str, dict[str, Any]],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Polymarket's event_id is its own internal id, unrelated to ESPN's --
    the only reliable join key across the two sources is (game_date, away
    team, home team), both already "City Name" strings in both sources."""
    index: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in feature_cache.values():
        key = (row["game_date"], _team_identity(row["away_team"]), _team_identity(row["home_team"]))
        index[key] = row
    return index


def real_market_window_rows(
    spec: FormulaSpec,
    feature_cache: dict[str, dict[str, Any]],
    games: dict[str, dict[str, Any]],
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    feature_by_team_pair = _feature_cache_by_team_pair(feature_cache)
    cursor = start
    while cursor <= end:
        day_path = ODDS_ROOT / cursor.isoformat() / "polymarket_snapshots.jsonl"
        game_date = cursor.isoformat()
        if day_path.exists():
            # event_start_utc/title aren't known ahead of time per-file, so
            # pull them from the first row seen for each Polymarket event_id.
            event_starts: dict[str, str] = {}
            event_titles: dict[str, str] = {}
            with day_path.open(encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    event_starts.setdefault(row["event_id"], row["event_start_utc"])
                    event_titles.setdefault(row["event_id"], row.get("event_title", ""))
            for event_id, event_start_utc in event_starts.items():
                title = event_titles.get(event_id, "")
                if " vs. " not in title:
                    continue
                away_name, home_name = (part.strip() for part in title.split(" vs. ", 1))
                feature_row = feature_by_team_pair.get(
                    (game_date, _team_identity(away_name), _team_identity(home_name))
                )
                if feature_row is None:
                    continue
                game = games.get(feature_row["event_id"])
                if game is None:
                    continue
                closing = _closing_full_game_lines(day_path, event_start_utc)
                per_event = closing.get(event_id, {})
                spread_row = per_event.get("spread")
                total_row = per_event.get("total")
                spread_line = None
                if spread_row is not None:
                    is_away = spread_row.get("team") == feature_row.get("away_team")
                    spread_line = float(spread_row["line"]) if is_away else -float(spread_row["line"])
                total_line = float(total_row["line"]) if total_row is not None else None
                if spread_line is None and total_line is None:
                    continue
                features = _features_from_cache_row(feature_row)
                probs = raw_probabilities(features, spec, spread_line, total_line)
                rows.append(
                    {
                        "event_id": feature_row["event_id"],
                        "away_score": int(game["away_score"]),
                        "home_score": int(game["home_score"]),
                        "spread_line": spread_line,
                        "total_line": total_line,
                        **probs,
                    }
                )
        cursor += timedelta(days=1)
    return rows


def _ols(raw: np.ndarray, outcome: np.ndarray) -> tuple[float, float]:
    """scale, offset for calibrated = scale * raw + offset, least squares."""
    design = np.column_stack([raw, np.ones_like(raw)])
    (scale, offset), *_ = np.linalg.lstsq(design, outcome, rcond=None)
    return float(scale), float(offset)


def _flat_diagnostic(raw: np.ndarray, outcome: np.ndarray, scale: float, offset: float) -> dict[str, Any]:
    """Flat -110 diagnostic on the calibrated probability's own selected
    side -- same format as v1's calibration_note (hit rate, units on picks
    that clear a 52.4% no-vig breakeven threshold at -110).
    """
    calibrated = np.clip(scale * raw + offset, 1e-6, 1 - 1e-6)
    picks = calibrated > 0.524
    n = int(picks.sum())
    if n == 0:
        return {"picks": 0, "hit_rate": None, "units": None}
    hits = int(outcome[picks].sum())
    units = hits * (100 / 110) - (n - hits)
    return {"picks": n, "hit_rate": round(hits / n, 4), "units": round(float(units), 2)}


def calibrate_market(rows: list[dict[str, Any]], prob_key: str, line_key: str) -> dict[str, Any]:
    """Calibrate one market (spread or total) using whichever side of each
    game's pair was actually eligible (both sides included -- each game
    contributes two rows, one per side, matching how the live model
    evaluates both sides of every market)."""
    raw_values: list[float] = []
    outcomes: list[float] = []
    for row in rows:
        if row.get(line_key) is None:
            continue
        away_score, home_score = row["away_score"], row["home_score"]
        if prob_key == "spread":
            margin = away_score - home_score + row["spread_line"]
            if margin == 0:
                continue  # push
            raw_values.append(row["spread_away_probability"])
            outcomes.append(1.0 if margin > 0 else 0.0)
            raw_values.append(row["spread_home_probability"])
            outcomes.append(1.0 if margin < 0 else 0.0)
        else:
            total_margin = away_score + home_score - row["total_line"]
            if total_margin == 0:
                continue
            raw_values.append(row["total_over_probability"])
            outcomes.append(1.0 if total_margin > 0 else 0.0)
            raw_values.append(row["total_under_probability"])
            outcomes.append(1.0 if total_margin < 0 else 0.0)
    if len(raw_values) < 20:
        return {"games": 0, "scale": None, "offset": None}
    raw = np.array(raw_values)
    outcome = np.array(outcomes)
    scale, offset = _ols(raw, outcome)
    correlation = float(np.corrcoef(raw, outcome)[0, 1]) if len(raw) > 1 else float("nan")
    diagnostic = _flat_diagnostic(raw, outcome, scale, offset)
    return {
        "games": len(raw) // 2,
        "scale": round(scale, 4),
        "offset": round(offset, 4),
        "correlation": round(correlation, 4),
        "flat_110_diagnostic": diagnostic,
    }


def write_artifact(
    output_path: str,
    model_name: str,
    model_version: str,
    base_score_model_version: str,
    diagnostic: dict[str, Any],
    real_market: dict[str, Any],
) -> None:
    if diagnostic.get("scale") is None:
        raise SystemExit(f"{model_name}: diagnostic window fit failed (too few games) -- not writing artifact")
    scale, offset = diagnostic["scale"], diagnostic["offset"]
    if not 0 < scale <= 1.5:
        raise SystemExit(f"{model_name}: fitted scale {scale} outside governance bounds (0, 1.5]")
    calibrated_at_half = scale * 0.5 + offset
    if not 0.35 <= calibrated_at_half <= 0.65:
        raise SystemExit(
            f"{model_name}: fitted offset implies too much bias at raw=0.5 (calibrated={calibrated_at_half:.3f})"
        )
    real_market_note = (
        f"Real-market (genuine captured Polymarket BBO) corroboration: {real_market['games']} games, "
        f"scale={real_market['scale']}, offset={real_market['offset']}, correlation={real_market['correlation']}, "
        f"flat -110: {real_market['flat_110_diagnostic']}."
        if real_market.get("scale") is not None
        else f"Real-market window had too few games ({real_market.get('games', 0)}) to fit independently this run."
    )
    artifact = {
        "model_name": model_name,
        "model_version": model_version,
        "calibration_version": model_version,
        "base_score_model_version": base_score_model_version,
        "calibration_method": "flat_probability_shrinkage_toward_half",
        "scale": scale,
        "offset": offset,
        "trained_on_games": diagnostic["games"],
        "calibration_windows": {"diagnostic": diagnostic, "real_market": real_market},
        "calibration_note": (
            f"Rebuilt against {base_score_model_version}'s refit elasticities "
            f"(scripts/mlb_elasticity_refit.py) and fresh game data "
            f"(scripts/mlb_measured_edge_calibrate.py). Primary fit (this artifact's top-level "
            f"scale/offset) is OLS of the rebuilt formula's raw simulated cover probability "
            f"against real game outcomes over {diagnostic['games']} games from "
            f"data/historical/mlb_market_lines_reconstructed.jsonl -- ESPN's own postgame "
            f"pickcenter reconstruction, hard-labeled timestamp_valid=False by "
            f"ingest.py::reconstruct_mlb_markets; diagnostic only, NOT real historical "
            f"Polymarket lines (correcting v1's calibration_note, which mischaracterized this "
            f"same data source). Correlation with real outcomes: {diagnostic['correlation']}. "
            f"Flat -110 diagnostic: {diagnostic['flat_110_diagnostic']}. " + real_market_note
        ),
    }
    artifact["artifact_hash"] = hashlib.sha256(
        json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    Path(output_path).write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {output_path}: scale={scale} offset={offset} games={diagnostic['games']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formula", required=True)
    parser.add_argument(
        "--feature-cache",
        default=str(PROJECT_ROOT / "data/research/mlb_elasticity_feature_cache.jsonl"),
    )
    parser.add_argument("--odds-start", default="2026-07-17")
    parser.add_argument("--odds-end", required=True)
    parser.add_argument(
        "--output-margin", default=str(PROJECT_ROOT / "config/models/measured-edge-margin-v2.json")
    )
    parser.add_argument(
        "--output-totals", default=str(PROJECT_ROOT / "config/models/measured-edge-totals-v2.json")
    )
    args = parser.parse_args()

    spec = load_formula_spec_unchecked(args.formula)
    feature_cache = _load_feature_cache(Path(args.feature_cache))
    games = _load_games_all(GAMES_ALL_PATH)

    diagnostic_rows = diagnostic_window_rows(spec, feature_cache, games)
    real_market_rows = real_market_window_rows(
        spec, feature_cache, games, date.fromisoformat(args.odds_start), date.fromisoformat(args.odds_end)
    )
    print(f"diagnostic window rows: {len(diagnostic_rows)}, real-market window rows: {len(real_market_rows)}")

    margin_diagnostic = calibrate_market(diagnostic_rows, "spread", "spread_line")
    margin_real_market = calibrate_market(real_market_rows, "spread", "spread_line")
    totals_diagnostic = calibrate_market(diagnostic_rows, "total", "total_line")
    totals_real_market = calibrate_market(real_market_rows, "total", "total_line")

    write_artifact(
        args.output_margin, "Measured Edge Margin", "measured-edge-margin-v2",
        spec.formula_version, margin_diagnostic, margin_real_market,
    )
    write_artifact(
        args.output_totals, "Measured Edge Totals", "measured-edge-totals-v2",
        spec.formula_version, totals_diagnostic, totals_real_market,
    )


if __name__ == "__main__":
    main()
