"""Point-in-time total-score model with park/weather/pitcher features.

Predicts combined final score from local game history without using a market line.
V2 adds park factors, weather, starting pitcher, rest/travel, exponential weighting,
and calibration to transform score predictions into over/under probabilities.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import mean as _mean
from statistics import pstdev
from typing import Any

import numpy as np
from sklearn.linear_model import Ridge

from .features.base import FeatureStore, GameRecord
from .features.schedule_load import team_schedule_load, travel_timezone_displacement
from .features.wnba_boxscores import build_wnba_four_factors_logs, load_wnba_boxscore_files
from .features.wnba_pace_four_factors import compute_team_four_factors, project_wnba_game_total
from .features.wnba_player_impact import compute_lineup_impact
from .features.wnba_player_logs import build_wnba_player_logs, team_player_profiles

# ── Feature definitions ──────────────────────────────────────────────────────
FEATURE_NAMES = (
    "league_total_mean",  # League average run environment
    "away_run_rate_ewma",  # Away scoring rate (10-game half-life)
    "away_run_rate_allowed_ewma",  # Away runs allowed rate
    "home_run_rate_ewma",  # Home scoring rate
    "home_run_rate_allowed_ewma",  # Home runs allowed rate
    "park_factor",  # Park run multiplier (Coors=1.12, etc.)
    "weather_factor",  # Temp/wind bonus (neutral=1.0)
    "bullpen_rest_days",  # Avg bullpen rest days (both teams)
    "travel_distance",  # Away team travel distance (miles / 1000)
    "last_10_total_avg",  # Both teams' last 10 game total averages
    "season_total_std",  # Season run total standard deviation
)

# WNBA replaces the MLB-centric dead slots of FEATURE_NAMES with real
# point-in-time signals (2026-08-26). park_factor/weather_factor stay at
# their WNBA-correct neutrals (no WNBA park or weather feed exists);
# bullpen_rest_days/travel_distance are baseball constructs -- WNBA gets the
# real rest-day average and the in-repo timezone-displacement travel proxy;
# wnba_pace_40m is the credibility-shrunk four-factors pace of both teams.
# MLB/NBA/NFL keep FEATURE_NAMES unchanged (see build_total_score_rows).
WNBA_FEATURE_NAMES = (
    "league_total_mean",
    "away_run_rate_ewma",
    "away_run_rate_allowed_ewma",
    "home_run_rate_ewma",
    "home_run_rate_allowed_ewma",
    "park_factor",
    "weather_factor",
    "wnba_rest_days_avg",
    "wnba_travel_tz_hours",
    "last_10_total_avg",
    "season_total_std",
    "wnba_pace_40m",
)
# Structural-challenger extras (plan P0 possessions×PPP + player impact),
# appended only under ``include_player_impact=True`` so the incumbent
# vector stays byte-identical for before/after reproduction.
WNBA_CHALLENGER_FEATURE_NAMES = WNBA_FEATURE_NAMES + (
    "lineup_net_advantage",
    "injury_impact_gap",
    "structural_total",
)
WNBA_FF_LOOKBACK = 15  # matches wnba_pace_four_factors.compute_team_four_factors
WNBA_SCHEDULE_LOOKBACK = 10  # rest needs only the last game + 7-day window

MINIMUM_TEAM_GAMES = 8
MINIMUM_LEAGUE_GAMES = 40
EWMA_HALF_LIFE = 10.0
EWMA_ALPHA = 1 - math.exp(math.log(0.5) / EWMA_HALF_LIFE)


# ── Park factors ─────────────────────────────────────────────────────────────
PARK_FACTORS: dict[str, float] = {
    "Colorado Rockies": 1.12,
    "Arizona Diamondbacks": 1.05,
    "Boston Red Sox": 1.04,
    "Texas Rangers": 1.03,
    "Cincinnati Reds": 1.03,
    "Baltimore Orioles": 1.02,
    "Kansas City Royals": 1.02,
    "Chicago Cubs": 1.01,
    "Atlanta Braves": 1.01,
    "Milwaukee Brewers": 1.01,
    "Philadelphia Phillies": 1.00,
    "Toronto Blue Jays": 1.00,
    "Washington Nationals": 1.00,
    "Chicago White Sox": 0.99,
    "Detroit Tigers": 0.99,
    "Los Angeles Angels": 0.99,
    "Minnesota Twins": 0.99,
    "New York Yankees": 0.99,
    "Pittsburgh Pirates": 0.98,
    "Cleveland Guardians": 0.97,
    "Houston Astros": 0.97,
    "Tampa Bay Rays": 0.97,
    "Los Angeles Dodgers": 0.96,
    "Miami Marlins": 0.96,
    "San Diego Padres": 0.96,
    "San Francisco Giants": 0.95,
    "New York Mets": 0.94,
    "St. Louis Cardinals": 0.94,
    "Seattle Mariners": 0.92,
    "Oakland Athletics": 0.92,
    "Athletics": 0.92,
}


@dataclass(frozen=True)
class TotalScoreRow:
    date: str
    event_id: str
    features: tuple[float, ...]
    actual_total: float
    baseline_total: float


def _artifact_hash(payload: dict[str, Any]) -> str:
    canonical = {k: v for k, v in payload.items() if k != "artifact_hash"}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _ewma_update(current: float | None, new: float, alpha: float = EWMA_ALPHA) -> float:
    if current is None:
        return new
    return alpha * new + (1 - alpha) * current


def build_total_score_rows(
    games: Sequence[GameRecord],
    *,
    minimum_team_games: int = MINIMUM_TEAM_GAMES,
    minimum_league_games: int = MINIMUM_LEAGUE_GAMES,
    wnba_boxscores: dict[str, dict[str, dict[str, float]]] | None = None,
    wnba_legacy_signals: bool = False,
    wnba_player_boxscores: dict[str, dict[str, Any]] | None = None,
    include_player_impact: bool = False,
) -> list[TotalScoreRow]:
    """Build PIT totals rows.

    ``wnba_boxscores`` (from ``features.wnba_boxscores.load_wnba_boxscore_files``)
    enables the WNBA pace signal: per-team four-factors logs are appended only
    after the row for their game is built, so every row sees strictly-prior
    games (the 2026-08-18 last-10 pattern). ``wnba_legacy_signals`` reproduces
    the pre-2026-08-26 WNBA vector (hardcoded constants) for before/after
    walk-forward comparisons; MLB/NBA/NFL are never affected by either flag.
    ``include_player_impact`` (with ``wnba_player_boxscores`` from
    ``features.wnba_player_logs.load_wnba_player_boxscores``) appends the
    three structural-challenger features; without it the incumbent vector is
    byte-identical.
    """
    scored_ewma: dict[str, float | None] = {}
    allowed_ewma: dict[str, float | None] = {}
    league_totals: deque[float] = deque(maxlen=200)
    recent_totals: dict[str, deque[float]] = {}
    recent_games: dict[str, deque[GameRecord]] = {}
    wnba_ff_logs: dict[str, deque[dict[str, float]]] = {}
    wnba_pl_logs: dict[str, deque[dict[str, Any]]] = {}
    rows: list[TotalScoreRow] = []

    for game in sorted(games, key=lambda g: g.start):
        home = game.home_team
        away = game.away_team
        is_wnba = game.league.upper() == "WNBA"
        use_wnba_signals = is_wnba and not wnba_legacy_signals

        if (
            len(league_totals) >= minimum_league_games
            and scored_ewma.get(home) is not None
            and scored_ewma.get(away) is not None
        ):
            baseline = _mean(league_totals)
            away_scored = scored_ewma.get(away)
            away_run_rate = away_scored if away_scored is not None else baseline / 2
            away_allowed = allowed_ewma.get(away)
            away_allow_rate = away_allowed if away_allowed is not None else baseline / 2
            home_scored = scored_ewma.get(home)
            home_run_rate = home_scored if home_scored is not None else baseline / 2
            home_allowed = allowed_ewma.get(home)
            home_allow_rate = home_allowed if home_allowed is not None else baseline / 2
            pf = PARK_FACTORS.get(home, 1.0)
            weather = 1.0  # Neutral — no live weather feed yet
            bullpen_rest = 3.0  # Default 3 days rest
            travel = 0.0  # Default no travel
            # Was `last_10_avg = baseline` -- a placeholder that made this an
            # exact duplicate of league_total_mean. Ridge then split one
            # weight across two identical columns (both landed on the same
            # -1.825776 in the 2026-08-18 WNBA refit), so the level term
            # pulled twice as hard in the wrong direction. This is the real
            # last-10 signal the feature name has always claimed: the mean
            # combined score of each side's recent games, strictly from
            # games already played.
            recent = [t for team in (away, home) for t in recent_totals.get(team, ())]
            last_10_avg = _mean(recent) if recent else baseline
            season_std = max(pstdev(league_totals) if len(league_totals) > 3 else 2.0, 1.0)

            common: tuple[float, ...] = (
                round(baseline, 4),
                round(away_run_rate, 4),
                round(away_allow_rate, 4),
                round(home_run_rate, 4),
                round(home_allow_rate, 4),
                round(pf, 4),
                round(weather, 4),
            )
            if use_wnba_signals:
                # Real WNBA signals, all strictly-prior:
                # rest days from the schedule of games already played
                # (schedule_load caps at 7, matching the validation harness),
                # travel as the in-repo timezone-displacement proxy (no
                # venue-coordinate history exists -- rest_travel.json records
                # travel_status "unavailable_from_source"), pace from the
                # four-factors module over strictly-prior boxscore logs.
                home_rest = team_schedule_load(recent_games.get(home, ()), home, game.start)
                away_rest = team_schedule_load(recent_games.get(away, ()), away, game.start)
                rest_avg = (home_rest.rest_days_capped + away_rest.rest_days_capped) / 2.0
                tz_hours = float(travel_timezone_displacement(away, home))
                home_ff = compute_team_four_factors(home, list(wnba_ff_logs.get(home, ())))
                away_ff = compute_team_four_factors(away, list(wnba_ff_logs.get(away, ())))
                pace_avg = (home_ff.pace_40m + away_ff.pace_40m) / 2.0
                features = common + (
                    round(rest_avg, 4),
                    round(tz_hours, 4),
                    round(last_10_avg, 4),
                    round(season_std, 4),
                    round(pace_avg, 4),
                )
                if include_player_impact:
                    # Structural-challenger block: lineup strength +
                    # absence proxy from strictly-prior player logs, and
                    # the pure possessions×PPP projection as a feature.
                    home_profiles, home_missing = team_player_profiles(list(wnba_pl_logs.get(home, ())))
                    away_profiles, away_missing = team_player_profiles(list(wnba_pl_logs.get(away, ())))
                    impact = compute_lineup_impact(home_profiles, away_profiles, home_missing, away_missing)
                    structural_total = project_wnba_game_total(home_ff, away_ff)["projected_total"]
                    features = features + (
                        round(impact.lineup_net_advantage, 4),
                        round(impact.injury_impact_gap, 4),
                        round(structural_total, 4),
                    )
            else:
                features = common + (
                    round(bullpen_rest, 4),
                    round(travel, 4),
                    round(last_10_avg, 4),
                    round(season_std, 4),
                )
            rows.append(
                TotalScoreRow(
                    date=game.start.date().isoformat(),
                    event_id=game.event_id,
                    features=features,
                    actual_total=float(game.total),
                    baseline_total=baseline,
                )
            )

        total = float(game.total)
        scored_ewma[away] = _ewma_update(scored_ewma.get(away), float(game.away_score))
        allowed_ewma[away] = _ewma_update(allowed_ewma.get(away), float(game.home_score))
        scored_ewma[home] = _ewma_update(scored_ewma.get(home), float(game.home_score))
        allowed_ewma[home] = _ewma_update(allowed_ewma.get(home), float(game.away_score))
        league_totals.append(total)
        # After the row is built, never before: this game is not information
        # available to a decision made before it started.
        for team in (away, home):
            recent_totals.setdefault(team, deque(maxlen=10)).append(total)
        if is_wnba:
            # Same strict-prior discipline for the WNBA schedule and
            # four-factors pace state: a game joins these deques only after
            # every decision row that precedes it has been built.
            for team in (away, home):
                recent_games.setdefault(team, deque(maxlen=WNBA_SCHEDULE_LOOKBACK)).append(game)
            if wnba_boxscores is not None:
                stats = wnba_boxscores.get(game.event_id)
                if stats is not None:
                    logs = build_wnba_four_factors_logs(home, away, game.home_score, game.away_score, stats)
                    if logs is not None:
                        for team in (away, home):
                            wnba_ff_logs.setdefault(team, deque(maxlen=WNBA_FF_LOOKBACK)).append(logs[team])
                    if include_player_impact and wnba_player_boxscores is not None:
                        player_box = wnba_player_boxscores.get(game.event_id)
                        if player_box is not None:
                            plogs = build_wnba_player_logs(home, away, player_box, stats)
                            if plogs is not None:
                                for team in (home, away):
                                    wnba_pl_logs.setdefault(team, deque(maxlen=WNBA_FF_LOOKBACK)).append(
                                        plogs[team]
                                    )

    return rows


def _metrics(predictions: Sequence[float], rows: Sequence[TotalScoreRow]) -> dict[str, float]:
    errors = [p - r.actual_total for p, r in zip(predictions, rows, strict=True)]
    abs_err = [abs(e) for e in errors]
    return {
        "mae": round(_mean(abs_err), 6),
        "rmse": round(math.sqrt(_mean([e * e for e in errors])), 6),
        "mean_error": round(_mean(errors), 6),
    }


def _paired_mae_gain_interval(
    predictions: Sequence[float],
    rows: Sequence[TotalScoreRow],
    samples: int = 2_000,
) -> tuple[float, float]:
    gains = [
        abs(r.baseline_total - r.actual_total) - abs(p - r.actual_total)
        for p, r in zip(predictions, rows, strict=True)
    ]
    gen = random.Random(20260717)
    boot = sorted(_mean([gains[gen.randrange(len(gains))] for _ in gains]) for _ in range(samples))
    return round(boot[int(samples * 0.025)], 6), round(boot[int(samples * 0.975)], 6)


def validate_total_score_model(store: FeatureStore, sport: str) -> dict[str, Any]:
    is_wnba = sport.lower() == "wnba"
    feature_names = list(WNBA_FEATURE_NAMES if is_wnba else FEATURE_NAMES)
    wnba_boxscores = (
        load_wnba_boxscore_files(store.data_root / "availability" / "wnba" / "espn_boxscores")
        if is_wnba
        else None
    )
    games = store.load_games(sport)
    if len(games) < 200:
        return {"status": "insufficient_data", "games": len(games)}
    rows = build_total_score_rows(
        games,
        minimum_team_games=8,
        minimum_league_games=40,
        wnba_boxscores=wnba_boxscores,
    )
    if len(rows) < 50:
        return {"status": "insufficient_rows", "rows": len(rows)}

    split = int(len(rows) * 0.7)
    train_rows = rows[:split]
    test_rows = rows[split:]

    X_train = [list(r.features) for r in train_rows]
    y_train = [r.actual_total for r in train_rows]
    X_test = [list(r.features) for r in test_rows]

    model = Ridge(alpha=1.0, random_state=42)
    model.fit(np.asarray(X_train), y_train)
    predictions = model.predict(np.asarray(X_test)).tolist()

    baseline_preds = [r.baseline_total for r in test_rows]
    model_metrics = _metrics(predictions, test_rows)
    baseline_metrics = _metrics(baseline_preds, test_rows)
    mae_gain = _paired_mae_gain_interval(predictions, test_rows)

    improved = model_metrics["mae"] < baseline_metrics["mae"]
    artifact = {
        "model_version": f"{sport.lower()}-total-score-ridge-v2",
        "method": "ridge_regression",
        "feature_names": feature_names,
        "coefficients": [round(float(c), 6) for c in model.coef_],
        "intercept": round(float(model.intercept_), 6),
        "alpha": 1.0,
        "metrics": model_metrics,
        "baseline_metrics": baseline_metrics,
        "mae_gain_95ci": list(mae_gain),
        "improved_vs_baseline": improved,
        "train_rows": len(train_rows),
        "test_rows": len(test_rows),
        "artifact_hash": "",
    }
    artifact["artifact_hash"] = _artifact_hash(artifact)

    return {
        "status": "validated" if improved else "no_improvement",
        "model": artifact,
        "sport": sport,
    }


def predict_total(game: GameRecord, model_data: dict[str, Any]) -> float | None:
    """Predict combined total for a single game using a trained model artifact."""
    features = list(model_data["feature_names"])
    coeffs = list(model_data["coefficients"])
    intercept = float(model_data["intercept"])
    if len(features) != len(coeffs):
        return None
    # Simple prediction: baseline * park factor, adjust for scoring rates
    league_avg = 9.0  # default MLB
    pf = PARK_FACTORS.get(game.home_team, 1.0)
    return round(
        intercept
        + sum(
            c * (league_avg if f == "league_total_mean" else pf if f == "park_factor" else 1.0)
            for f, c in zip(features, coeffs)
        ),
        4,
    )


class TotalScoreArtifact:
    """Hash-verified total-score artifact, compatible with v1 and v2 payloads."""

    def __init__(self, payload: dict[str, Any]) -> None:
        if payload.get("artifact_hash") != _artifact_hash(payload):
            raise ValueError("total-score artifact hash mismatch")
        self.payload = payload.get("model", payload)
        self.fields = self._resolve_fields()

    def _resolve_fields(self) -> dict[str, Any]:
        # v2: keys at top level
        if "feature_names" in self.payload:
            return self.payload
        # v1 compatibility: keys shared from validate output
        result = {}
        for key in ("feature_names", "coefficients", "intercept"):
            result[key] = self.payload.get(key)
        return result

    @classmethod
    def load(cls, path: str | Path) -> TotalScoreArtifact:
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def predict(self, features: dict[str, float]) -> float:
        names = self.fields.get("feature_names") or self.payload.get("feature_names", [])
        missing = [name for name in names if name not in features]
        if missing:
            raise ValueError(f"missing total-score features: {missing}")
        coeffs = self.fields.get("coefficients") or self.payload.get("coefficients", [])
        value = float(self.fields.get("intercept") or self.payload.get("intercept", 0)) + sum(
            float(c) * float(features[n]) for n, c in zip(names, coeffs, strict=True)
        )
        return max(0.0, value)


def validate_all_total_score_models(
    store: FeatureStore,
    sports: Sequence[str],
    artifact_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Validate totals model for multiple sports. Legacy wrapper for CLI compatibility."""
    results: dict[str, Any] = {}
    for sport in sports:
        results[sport] = validate_total_score_model(store, sport)
    return {
        "schema_version": "2",
        "status": "research_only",
        "sports": results,
    }


def mlb_pitching_runs_allowed(
    starter_era: float,
    starter_expected_ip: float = 5.5,
    bullpen_era: float = 4.10,
    rest_days: float = 5.0,
    short_rest_threshold_days: float = 4.0,
    short_rest_era_penalty: float = 0.50,
) -> dict[str, Any]:
    """Calculate innings-weighted pitching expected runs allowed.

    Parameters
    ----------
    starter_era : float
        Starting pitcher rolling ERA or FIP (e.g. 3.65).
    starter_expected_ip : float, default 5.5
        Expected innings pitched for starter in [1.0, 8.5].
    bullpen_era : float, default 4.10
        Bullpen rolling ERA/runs allowed per 9 innings.
    rest_days : float, default 5.0
        Days of rest since starting pitcher's last appearance.
    short_rest_threshold_days : float, default 4.0
        Threshold below which starter is penalized for short rest fatigue.
    short_rest_era_penalty : float, default 0.50
        ERA penalty added if starter has short rest (< 4 days).

    Returns
    -------
    dict with expected_runs_allowed, starter_runs, bullpen_runs, starter_ip, bullpen_ip, rest_penalty_applied.
    """
    ip_s = max(1.0, min(8.5, float(starter_expected_ip)))
    ip_bp = max(0.5, 9.0 - ip_s)

    # Apply short-rest fatigue penalty
    rest = float(rest_days)
    rest_penalty = rest < short_rest_threshold_days
    adj_starter_era = float(starter_era) + (short_rest_era_penalty if rest_penalty else 0.0)
    adj_starter_era = max(1.0, adj_starter_era)
    bp_era = max(1.0, float(bullpen_era))

    starter_runs = (adj_starter_era / 9.0) * ip_s
    bullpen_runs = (bp_era / 9.0) * ip_bp
    total_runs_allowed = starter_runs + bullpen_runs

    return {
        "expected_runs_allowed": round(total_runs_allowed, 4),
        "starter_runs": round(starter_runs, 4),
        "bullpen_runs": round(bullpen_runs, 4),
        "starter_ip": round(ip_s, 2),
        "bullpen_ip": round(ip_bp, 2),
        "rest_penalty_applied": rest_penalty,
        "effective_starter_era": round(adj_starter_era, 3),
    }


def stadium_wind_orientation_multiplier(
    wind_speed_mph: float,
    wind_direction_deg: float,
    park_orientation_deg: float = 0.0,
    temp_f: float = 72.0,
    is_dome: bool = False,
) -> float:
    """Calculate park-orientation and wind/temperature run scoring multiplier.

    Vector math: theta_rel = wind_direction - park_orientation (from home to center).
    Wind blowing directly out to center (+cos(theta) > 0) boosts run expectancy.
    Wind blowing directly in from center (+cos(theta) < 0) depresses run expectancy.

    Parameters
    ----------
    wind_speed_mph : float
        Wind speed in miles per hour.
    wind_direction_deg : float
        Compass direction wind is coming from or blowing toward (0-360 deg).
    park_orientation_deg : float, default 0.0
        Compass heading from home plate to centerfield (e.g. 45 deg for NE).
    temp_f : float, default 72.0
        Ambient air temperature in Fahrenheit.
    is_dome : bool, default False
        True if venue is closed roof/dome where weather is nullified.

    Returns
    -------
    float : run scoring multiplier in [0.70, 1.30] (1.0 = neutral).
    """
    if is_dome:
        return 1.0

    speed = max(0.0, float(wind_speed_mph))
    wind_rad = math.radians(float(wind_direction_deg) - float(park_orientation_deg))
    cos_component = math.cos(wind_rad)

    # 10mph wind blowing straight out gives ~ +8% run scoring boost
    wind_delta = 0.08 * cos_component * min(speed / 10.0, 2.5)

    # Temperature adjustment: +0.5% per degree above 72F, -0.5% below 72F
    temp_delta = 0.005 * (max(30.0, min(105.0, float(temp_f))) - 72.0)

    multiplier = 1.0 + wind_delta + temp_delta
    return round(max(0.70, min(1.30, multiplier)), 4)


def mlb_totals_v2_projected_runs(
    home_pitching: dict[str, Any],
    away_pitching: dict[str, Any],
    home_lineup_ops_ratio: float = 1.0,
    away_lineup_ops_ratio: float = 1.0,
    park_factor: float = 1.0,
    wind_weather_multiplier: float = 1.0,
) -> dict[str, float]:
    """Calculate combined MLB game projected total from innings-weighted components.

    Returns
    -------
    dict with home_projected_runs, away_projected_runs, total_projected_runs.
    """
    pf = max(0.75, min(1.35, float(park_factor)))
    wm = max(0.70, min(1.30, float(wind_weather_multiplier)))

    # Home team bats against away pitching
    home_expected = (
        float(away_pitching["expected_runs_allowed"]) * max(0.6, float(home_lineup_ops_ratio)) * pf * wm
    )
    # Away team bats against home pitching
    away_expected = (
        float(home_pitching["expected_runs_allowed"]) * max(0.6, float(away_lineup_ops_ratio)) * pf * wm
    )

    total = home_expected + away_expected
    return {
        "home_projected_runs": round(home_expected, 3),
        "away_projected_runs": round(away_expected, 3),
        "total_projected_runs": round(total, 3),
    }


def analytical_totals_probabilities(
    home_projected_runs: float,
    away_projected_runs: float,
    total_line: float,
    max_runs: int = 30,
) -> dict[str, float]:
    """Calculate exact analytical over, under, and push probabilities under independent Poisson processes.

    Parameters
    ----------
    home_projected_runs : float
        Expected runs for home team.
    away_projected_runs : float
        Expected runs for away team.
    total_line : float
        Betting total line (e.g. 7.5, 8.0, 8.5).
    max_runs : int, default 30
        Upper bound truncation for joint distribution matrix.

    Returns
    -------
    dict with 'prob_over', 'prob_under', 'prob_push'.
    """
    from scipy import stats

    lh = max(0.2, float(home_projected_runs))
    la = max(0.2, float(away_projected_runs))

    k = np.arange(0, max_runs + 1)
    pmf_h = stats.poisson.pmf(k, lh)
    pmf_a = stats.poisson.pmf(k, la)

    # 2D joint score probability matrix
    joint = np.outer(pmf_h, pmf_a)

    # Sum coordinates
    total_grid = k[:, None] + k[None, :]

    prob_over = float(np.sum(joint[total_grid > total_line]))
    prob_under = float(np.sum(joint[total_grid < total_line]))
    prob_push = float(np.sum(joint[total_grid == total_line]))

    # Normalize across truncated support
    support_sum = prob_over + prob_under + prob_push
    if support_sum > 0:
        prob_over /= support_sum
        prob_under /= support_sum
        prob_push /= support_sum

    return {
        "prob_over": round(prob_over, 4),
        "prob_under": round(prob_under, 4),
        "prob_push": round(prob_push, 4),
    }


def analytical_spread_probabilities(
    home_projected_runs: float,
    away_projected_runs: float,
    spread_line: float = -1.5,
    max_runs: int = 30,
) -> dict[str, float]:
    """Calculate exact analytical spread cover probability for home team.

    For example, with spread_line = -1.5, computes P(Home Score - Away Score > 1.5).
    """
    from scipy import stats

    lh = max(0.2, float(home_projected_runs))
    la = max(0.2, float(away_projected_runs))

    k = np.arange(0, max_runs + 1)
    pmf_h = stats.poisson.pmf(k, lh)
    pmf_a = stats.poisson.pmf(k, la)

    joint = np.outer(pmf_h, pmf_a)
    margin_grid = k[:, None] - k[None, :]

    prob_cover_home = float(np.sum(joint[margin_grid > -spread_line]))
    prob_cover_away = float(np.sum(joint[margin_grid < -spread_line]))
    prob_push = float(np.sum(joint[margin_grid == -spread_line]))

    support_sum = prob_cover_home + prob_cover_away + prob_push
    if support_sum > 0:
        prob_cover_home /= support_sum
        prob_cover_away /= support_sum
        prob_push /= support_sum

    return {
        "prob_cover_home": round(prob_cover_home, 4),
        "prob_cover_away": round(prob_cover_away, 4),
        "prob_push": round(prob_push, 4),
    }
