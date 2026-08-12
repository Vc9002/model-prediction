"""Unified MLB model: the Trend Engine score simulation plus the two
Measured Edge heads (margin and totals).

This file absorbs the former ``mlb_v02.py`` score engine and the Measured Edge
heads. Model research is continuous: parameters are never frozen, but every
change must be versioned and survive walk-forward ablation plus a locked
holdout before promotion. Artifact hashes preserve exact reproducibility.

Versioning happens in config and git tags, never in filenames.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from math import exp, log
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from ..domain import MarketType
from .base import ScoreSimulation

ENGINE_VERSION = "mlb-analyst-poisson-trend-v0.3"
MARGIN_MODEL_VERSION = "measured-edge-margin-v3"
TOTALS_MODEL_VERSION = "measured-edge-totals-v3"

# Backwards-compatible alias kept for artifact validation strings.
MODEL_VERSION = ENGINE_VERSION


# --------------------------------------------------------------------------
# Trend Engine score model (formerly mlb_v02.py)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FormulaSpec:
    formula_version: str
    feature_schema_version: str
    league_runs_per_team_game: float
    league_starter_era: float
    league_strikeout_rate: float
    league_walk_rate: float
    recent_half_life_games: float
    recent_prior_strength_games: float
    starter_season_prior_innings: float
    starter_recent_prior_innings: float
    starter_rate_prior_batters_faced: float
    starter_recent_weight: float
    starter_season_weight: float
    strikeout_weight: float
    walk_weight: float
    home_field_run_factor: float
    away_field_run_factor: float
    offense_elasticity: float
    starter_weakness_elasticity: float
    bullpen_elasticity: float
    park_elasticity: float
    weather_elasticity: float
    factor_bounds: dict[str, tuple[float, float]]
    uncertainty: dict[str, float]
    simulation: dict[str, Any]
    seed_method: str
    configuration_hash: str


@dataclass(frozen=True)
class TeamForm:
    runs_scored: tuple[int, ...]
    runs_allowed: tuple[int, ...]
    wins: int
    losses: int
    status: str = "available"


@dataclass(frozen=True)
class PitcherForm:
    player_id: str
    name: str
    throwing_hand: str | None
    starts_before_game: int
    season_innings: float
    season_earned_runs: int
    season_strikeouts: int
    season_walks: int
    season_batters_faced: int
    last_five_innings: float
    last_five_earned_runs: int
    last_five_strikeouts: int
    last_five_walks: int
    last_five_batters_faced: int
    xfip: float | None = None
    xfip_status: str = "unavailable_from_source"
    status: str = "available"

    @property
    def rookie_or_limited(self) -> bool:
        return self.starts_before_game < 10


@dataclass(frozen=True)
class MLBGameFeatures:
    event_id: str
    event_start_utc: str
    decision_timestamp_utc: str
    away_team: str
    home_team: str
    away_form: TeamForm
    home_form: TeamForm
    away_starter: PitcherForm
    home_starter: PitcherForm
    away_bullpen_weakness: float = 1.0
    home_bullpen_weakness: float = 1.0
    away_bullpen_status: str = "unavailable_from_source"
    home_bullpen_status: str = "unavailable_from_source"
    park_factor: float = 1.0
    park_factor_status: str = "unavailable_from_source"
    weather_factor: float = 1.0
    weather_status: str = "unavailable_from_source"
    lineup_status: str = "unavailable_from_source"
    wrc_plus_status: str = "unavailable_from_source"
    source_snapshot_ids: tuple[str, ...] = ()
    feature_snapshot_hash: str = ""
    market_snapshot_hash: str = ""
    starter_confirmed: bool = False
    starter_status: str = "probable"


@dataclass(frozen=True)
class RunEstimate:
    away_expected_runs: float
    home_expected_runs: float
    factors: dict[str, float]
    uncertainty: float
    uncertainty_components: dict[str, float]
    formula_version: str
    feature_schema_version: str
    configuration_hash: str


@dataclass(frozen=True)
class MarketDistribution:
    first_selection: str
    first_win_probability: float
    second_selection: str
    second_win_probability: float
    push_probability: float


def load_formula_spec(path: str | Path) -> FormulaSpec:
    path = Path(path)
    raw_bytes = path.read_bytes()
    raw = yaml.safe_load(raw_bytes)
    if raw["formula_version"] != ENGINE_VERSION:
        raise ValueError("formula file version does not match the Trend Engine code")
    required = {
        "seed_method", "factor_bounds", "uncertainty", "simulation",
        "league_runs_per_team_game", "league_starter_era",
        "offense_elasticity", "starter_weakness_elasticity", "bullpen_elasticity",
        "park_elasticity", "weather_elasticity",
    }
    missing = sorted(required - set(raw))
    engine_sim_fields = {"simulations", "shared_environment_variance", "team_specific_variance"}
    if missing or not engine_sim_fields.issubset(set(raw.get("simulation") or {})):
        raise ValueError(
            "legacy measured-edge rollback is unavailable: the frozen v0.2 formula "
            f"spec at {path.name} was rewritten (2026-07-17) and no longer carries the "
            f"original engine fields (missing: {missing or sorted(engine_sim_fields)}). "
            "The learned LR path (--model learned, the default) is the production forecast."
        )
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


def estimate_runs(features: MLBGameFeatures, spec: FormulaSpec) -> RunEstimate:
    away_offense = _offense_index(features.away_form, spec)
    home_offense = _offense_index(features.home_form, spec)
    away_starter_weakness = _starter_weakness(features.away_starter, spec)
    home_starter_weakness = _starter_weakness(features.home_starter, spec)
    away_bullpen = _clip(features.away_bullpen_weakness, spec.factor_bounds["bullpen_weakness"])
    home_bullpen = _clip(features.home_bullpen_weakness, spec.factor_bounds["bullpen_weakness"])
    park = _clip(features.park_factor, spec.factor_bounds["park"])
    weather = _clip(features.weather_factor, spec.factor_bounds["weather"])
    # Elasticities (added 2026-07-30) replace the previous implicit
    # assumption that every factor moves the run estimate exactly
    # proportionally (exponent 1.0). Fit via a real Poisson regression
    # against 629 real games spanning 2024-02..2026-07 (the 2026-07-29
    # backtest's 162-game/12-day window was too narrow to fit this safely).
    # See mlb-analyst-poisson-trend-v0.2.yaml for the fitted values and the
    # full rebuild rationale, including why bullpen_elasticity is 0.0.
    away_expected = (
        spec.league_runs_per_team_game
        * away_offense ** spec.offense_elasticity
        * home_starter_weakness ** spec.starter_weakness_elasticity
        * home_bullpen ** spec.bullpen_elasticity
        * park ** spec.park_elasticity
        * weather ** spec.weather_elasticity
        * spec.away_field_run_factor
    )
    home_expected = (
        spec.league_runs_per_team_game
        * home_offense ** spec.offense_elasticity
        * away_starter_weakness ** spec.starter_weakness_elasticity
        * away_bullpen ** spec.bullpen_elasticity
        * park ** spec.park_elasticity
        * weather ** spec.weather_elasticity
        * spec.home_field_run_factor
    )
    components = _uncertainty_components(features, spec)
    total_uncertainty = min(
        spec.uncertainty["maximum"],
        max(
            spec.uncertainty["minimum"],
            sum(value * value for value in components.values()) ** 0.5,
        ),
    )
    return RunEstimate(
        round(away_expected, 6),
        round(home_expected, 6),
        {
            "away_offense_index": away_offense,
            "home_offense_index": home_offense,
            "away_starter_weakness_index": away_starter_weakness,
            "home_starter_weakness_index": home_starter_weakness,
            "away_bullpen_weakness_index": away_bullpen,
            "home_bullpen_weakness_index": home_bullpen,
            "park_factor": park,
            "weather_factor": weather,
            "away_field_run_factor": spec.away_field_run_factor,
            "home_field_run_factor": spec.home_field_run_factor,
        },
        round(total_uncertainty, 6),
        components,
        spec.formula_version,
        spec.feature_schema_version,
        spec.configuration_hash,
    )


# Valid joint score-distribution methods for simulate_game. Each draws away/home
# runs from ONE coherent joint distribution; the market heads (moneyline/spread/
# total) are then derived from that single draw, never from disconnected
# classifiers. gamma_poisson (default) is the incumbent correlated-overdispersion
# draw (shared + team-specific gamma multipliers around a Poisson mean);
# negative_binomial is the first serious challenger per the model roadmap
# (independent NB, overdispersion tuned by spec.simulation["negative_binomial_phi"]);
# independent_poisson is the no-overdispersion null.
DISTRIBUTION_METHODS = ("gamma_poisson", "negative_binomial", "independent_poisson")


def simulate_game(
    features: MLBGameFeatures,
    estimate: RunEstimate,
    spec: FormulaSpec,
    simulations: int | None = None,
    seed_namespace: str = "",
    method: str = "gamma_poisson",
) -> ScoreSimulation:
    count = simulations or int(spec.simulation["simulations"])
    if count <= 0:
        raise ValueError("simulation count must be positive")
    if method not in DISTRIBUTION_METHODS:
        raise ValueError(f"unknown distribution method {method!r}; expected one of {DISTRIBUTION_METHODS}")
    # The `method` part of the seed is deliberately EXCLUDED for the default
    # gamma_poisson path: the method refactor appended it to every seed and
    # silently changed every incumbent simulated price bit-for-bit versus the
    # pre-refactor formula (found 2026-08-13). Non-default methods have no
    # pre-existing stream to preserve, so they keep method in the seed.
    seed_parts = [
        features.event_id,
        spec.formula_version,
        features.decision_timestamp_utc,
        features.market_snapshot_hash,
        features.feature_snapshot_hash,
        seed_namespace,
    ]
    if method != "gamma_poisson":
        seed_parts.append(method)
    seed = stable_seed(*seed_parts)
    rng = np.random.default_rng(seed)
    away = home = None
    if method == "independent_poisson":
        away = rng.poisson(estimate.away_expected_runs, count)
        home = rng.poisson(estimate.home_expected_runs, count)
    elif method == "negative_binomial":
        # Independent NB per team. Parameterized by mean + overdispersion phi
        # (variance = mean * phi), so phi=1.0 collapses to Poisson. Fitted MLB
        # run scoring is overdispersed (~1.2x variance); the challenger defaults
        # to that, configurable via spec.simulation["negative_binomial_phi"].
        phi = float(spec.simulation.get("negative_binomial_phi", 1.2))
        phi = max(1.0 + 1e-9, phi)
        away_n, home_n = _nb_n(estimate.away_expected_runs, phi), _nb_n(estimate.home_expected_runs, phi)
        away_p = away_n / (away_n + estimate.away_expected_runs)
        home_p = home_n / (home_n + estimate.home_expected_runs)
        away = rng.negative_binomial(away_n, away_p, count)
        home = rng.negative_binomial(home_n, home_p, count)
    else:  # gamma_poisson (incumbent)
        shared_variance = float(spec.simulation["shared_environment_variance"])
        team_variance = float(spec.simulation["team_specific_variance"])
        shared = rng.gamma(1 / shared_variance, shared_variance, count)
        away_specific = rng.gamma(1 / team_variance, team_variance, count)
        home_specific = rng.gamma(1 / team_variance, team_variance, count)
        away = rng.poisson(estimate.away_expected_runs * shared * away_specific)
        home = rng.poisson(estimate.home_expected_runs * shared * home_specific)
    ties = away == home
    if ties.any():
        lower, upper = spec.simulation["extra_inning_home_probability_bounds"]
        home_probability = _clip(
            estimate.home_expected_runs / (estimate.home_expected_runs + estimate.away_expected_runs),
            (float(lower), float(upper)),
        )
        home_wins = rng.random(int(ties.sum())) < home_probability
        tie_indices = np.flatnonzero(ties)
        home[tie_indices[home_wins]] += 1
        away[tie_indices[~home_wins]] += 1
    return ScoreSimulation(away.tolist(), home.tolist(), estimate.uncertainty, spec.formula_version)


def _nb_n(mean: float, phi: float) -> float:
    """NB size parameter n for mean + overdispersion phi (variance = mean*phi).

    Standard NB mean/variance identities: mean = n*(1-p)/p, var = n*(1-p)/p^2.
    With phi = var/mean = 1/p, we get p = 1/phi and n = mean/(phi - 1).
    Clamped so n stays positive and finite for phi > 1.
    """
    if mean <= 0:
        return 1.0
    n = mean / (phi - 1.0)
    return max(n, 0.05)


def derive_market_distribution(
    simulation: ScoreSimulation, market_type: MarketType, line: float | None = None
) -> MarketDistribution:
    away = np.asarray(simulation.away_scores)
    home = np.asarray(simulation.home_scores)
    if market_type is MarketType.MONEYLINE:
        first_margin = away - home
        first, second = "away", "home"
    elif market_type is MarketType.SPREAD:
        if line is None:
            raise ValueError("spread requires the away selection-relative line")
        first_margin = away - home + line
        first, second = "away", "home"
    else:
        if line is None:
            raise ValueError("total requires a line")
        first_margin = away + home - line
        first, second = "over", "under"
    first_wins = float(np.mean(first_margin > 0))
    second_wins = float(np.mean(first_margin < 0))
    pushes = float(np.mean(first_margin == 0))
    return MarketDistribution(first, first_wins, second, second_wins, pushes)


def compare_distribution_methods(
    features: MLBGameFeatures,
    estimate: RunEstimate,
    spec: FormulaSpec,
    *,
    methods: Sequence[str] = DISTRIBUTION_METHODS,
    simulations: int | None = None,
    spread_line: float | None = None,
    total_line: float | None = None,
) -> dict[str, dict[str, MarketDistribution]]:
    """Price moneyline/spread/total under each joint score distribution.

    For every method this draws ONE coherent joint score distribution (via
    simulate_game with that method) and derives all three markets from that
    single draw. The three market heads can never silently contradict each
    other because they share one score simulation per method — the exact
    invariant the model roadmap's "one MLB score distribution for ML + spread
    + total" architecture requires. Returns {method: {market: MarketDistribution}}.
    """
    result: dict[str, dict[str, MarketDistribution]] = {}
    for method in methods:
        simulation = simulate_game(
            features, estimate, spec, simulations=simulations, seed_namespace="distribution_compare", method=method
        )
        markets = {
            "moneyline": derive_market_distribution(simulation, MarketType.MONEYLINE),
        }
        if spread_line is not None:
            markets["spread"] = derive_market_distribution(simulation, MarketType.SPREAD, spread_line)
        if total_line is not None:
            markets["total"] = derive_market_distribution(simulation, MarketType.TOTAL, total_line)
        result[method] = markets
    return result


def stable_seed(*parts: str) -> int:
    encoded = json.dumps(parts, separators=(",", ":"), ensure_ascii=True).encode()
    return int.from_bytes(hashlib.sha256(encoded).digest()[:8], "big", signed=False)


def feature_hash(features: MLBGameFeatures) -> str:
    return hashlib.sha256(
        json.dumps(asdict(features), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _offense_index(form: TeamForm, spec: FormulaSpec) -> float:
    if not form.runs_scored:
        return 1.0
    decay = exp(-log(2) / spec.recent_half_life_games)
    weights = [decay ** (len(form.runs_scored) - 1 - index) for index in range(len(form.runs_scored))]
    ewm = sum(weight * value for weight, value in zip(weights, form.runs_scored, strict=True)) / sum(weights)
    n = len(form.runs_scored)
    shrinkage = n / (n + spec.recent_prior_strength_games)
    shrunk_runs = shrinkage * ewm + (1 - shrinkage) * spec.league_runs_per_team_game
    return _clip(shrunk_runs / spec.league_runs_per_team_game, spec.factor_bounds["offense"])


def _starter_weakness(pitcher: PitcherForm, spec: FormulaSpec) -> float:
    # Both season and recent ERA get credibility-weighted shrinkage toward a
    # stable baseline by innings pitched -- _offense_index already does this
    # for team offense; ERA needs it even more, since a starter who's only
    # thrown a handful of innings this season (early callup, return from
    # injury) can otherwise post something like a 21.60 ERA off one bad start
    # and have it trusted at full weight. Confirmed necessary by a real
    # 162-game backtest: without shrinking season_era specifically (shrinking
    # only the recent-vs-season blend does nothing when recent IS the entire
    # season sample), starter_weakness pinned against its clip bounds on a
    # meaningful share of real games.
    raw_season_era = _era(pitcher.season_earned_runs, pitcher.season_innings, spec.league_starter_era)
    season_credibility = pitcher.season_innings / (pitcher.season_innings + spec.starter_season_prior_innings)
    season_era = season_credibility * raw_season_era + (1 - season_credibility) * spec.league_starter_era
    raw_recent_era = _era(pitcher.last_five_earned_runs, pitcher.last_five_innings, season_era)
    recent_credibility = pitcher.last_five_innings / (pitcher.last_five_innings + spec.starter_recent_prior_innings)
    recent_era = recent_credibility * raw_recent_era + (1 - recent_credibility) * season_era
    blended_era = spec.starter_season_weight * season_era + spec.starter_recent_weight * recent_era
    # Same small-sample problem as ERA above: last_five_batters_faced is
    # typically only ~100-130 batters, so a raw K%/BB% over that window
    # deserves the same credibility-weighted shrinkage toward the league
    # rate rather than being trusted at face value.
    rate_credibility = pitcher.last_five_batters_faced / (
        pitcher.last_five_batters_faced + spec.starter_rate_prior_batters_faced
    )
    raw_k_rate = _rate(
        pitcher.last_five_strikeouts,
        pitcher.last_five_batters_faced,
        spec.league_strikeout_rate,
    )
    raw_bb_rate = _rate(pitcher.last_five_walks, pitcher.last_five_batters_faced, spec.league_walk_rate)
    k_rate = rate_credibility * raw_k_rate + (1 - rate_credibility) * spec.league_strikeout_rate
    bb_rate = rate_credibility * raw_bb_rate + (1 - rate_credibility) * spec.league_walk_rate
    discipline = (
        1
        - spec.strikeout_weight * (k_rate - spec.league_strikeout_rate)
        + spec.walk_weight * (bb_rate - spec.league_walk_rate)
    )
    raw = blended_era / spec.league_starter_era * discipline
    return _clip(raw, spec.factor_bounds["starter_weakness"])


def _uncertainty_components(features: MLBGameFeatures, spec: FormulaSpec) -> dict[str, float]:
    u = spec.uncertainty
    components = {"base": u["base"], "model_form": u["model_form"]}
    components["pitcher"] = u["pitcher"]
    if features.away_starter.rookie_or_limited or features.home_starter.rookie_or_limited:
        components["rookie_starter"] = u["rookie_starter"]
    if "unavailable" in features.away_bullpen_status or "unavailable" in features.home_bullpen_status:
        components["bullpen"] = u["bullpen_unavailable"]
    if "unavailable" in features.weather_status:
        components["weather"] = u["weather_unavailable"]
    if features.lineup_status == "projected":
        components["lineup"] = u["lineup_projected"]
    elif "unavailable" in features.lineup_status:
        components["lineup"] = u["lineup_unavailable"]
    if not features.starter_confirmed or features.starter_status != "confirmed":
        components["starter_confirmation"] = u["lineup_projected"]
    if features.away_starter.xfip_status != "available" or features.home_starter.xfip_status != "available":
        components["missing_xfip"] = u["missing_xfip"]
    if features.wrc_plus_status != "available":
        components["missing_wrc_plus"] = u["missing_wrc_plus"]
    return components


def _era(earned_runs: int, innings: float, fallback: float) -> float:
    return fallback if innings <= 0 else 9 * earned_runs / innings


def _rate(numerator: int, denominator: int, fallback: float) -> float:
    return fallback if denominator <= 0 else numerator / denominator


def _clip(value: float, bounds: Sequence[float]) -> float:
    return max(float(bounds[0]), min(float(bounds[1]), float(value)))


# --------------------------------------------------------------------------
# Measured Edge heads (formerly measured_edge_margin.py / measured_edge_totals.py)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MarginModelOutput:
    run_estimate: RunEstimate
    simulation: ScoreSimulation
    moneyline: MarketDistribution
    spread: MarketDistribution | None
    away_spread_line: float | None
    model_version: str
    model_artifact_hash: str
    calibration_version: str


class MeasuredEdgeMarginModel:
    """Trend Engine margin simulation plus versioned Measured Edge calibration."""

    def __init__(self, model_path: str | Path, formula_spec: FormulaSpec) -> None:
        self.raw = _load_artifact(model_path, MARGIN_MODEL_VERSION)
        if formula_spec.formula_version != ENGINE_VERSION:
            raise ValueError("margin model must use the configured Trend Engine score model")
        scale, offset = _artifact_float(self.raw, "scale"), _artifact_float(self.raw, "offset")
        # The real invariant this guards is "the calibration doesn't inject a
        # large systematic bias", checked directly at a 50/50 raw input,
        # rather than bounding offset in isolation -- a fixed |offset|<=0.25
        # bound implicitly assumed scale stays close to 1 (mild shrinkage).
        # A real backtest (2026-07-29) showed the honest, correctly-centered
        # fit needs much heavier shrinkage (scale ~0.1), which mathematically
        # requires a larger offset (~0.45) to keep calibrated(0.5) near 0.5 --
        # rejecting that as "outside bounds" would have forced a dishonestly
        # off-center fit just to satisfy an arbitrary number.
        if not 0 < scale <= 1.5:
            raise ValueError("margin calibration scale outside governance bounds")
        calibrated_at_half = scale * 0.5 + offset
        if not 0.35 <= calibrated_at_half <= 0.65:
            raise ValueError(
                "margin calibration offset implies too much bias at raw probability 0.5"
            )
        self.formula_spec = formula_spec

    def predict(
        self,
        features: MLBGameFeatures,
        away_spread_line: float | None = None,
        method: str = "gamma_poisson",
    ) -> MarginModelOutput:
        estimate = estimate_runs(features, self.formula_spec)
        simulation = simulate_game(features, estimate, self.formula_spec, method=method)
        return MarginModelOutput(
            run_estimate=estimate,
            simulation=simulation,
            moneyline=derive_market_distribution(simulation, MarketType.MONEYLINE),
            spread=(
                None
                if away_spread_line is None
                else derive_market_distribution(
                    simulation,
                    MarketType.SPREAD,
                    away_spread_line,
                )
            ),
            away_spread_line=away_spread_line,
            model_version=MARGIN_MODEL_VERSION,
            model_artifact_hash=_artifact_str(self.raw, "artifact_hash"),
            calibration_version=_artifact_str(self.raw, "calibration_version"),
        )

    def calibrate_selected_side(self, raw_probability: float) -> float:
        if not 0 < raw_probability < 1:
            raise ValueError("raw probability must be between 0 and 1")
        calibrated = _artifact_float(self.raw, "scale") * raw_probability + _artifact_float(self.raw, "offset")
        if not 0 < calibrated < 1:
            # __init__'s governance check only validates scale/offset at
            # raw_probability=0.5 (the real invariant it guards: "no large
            # systematic bias at a coinflip"); it says nothing about the
            # extremes. A confident selected-side raw_probability near 1
            # could still push a scale/offset pair that passed that check
            # into an invalid "probability" here -- fail closed rather than
            # silently return a value outside [0,1] into the ledger/edge math.
            raise ValueError(
                f"margin calibration produced an out-of-range probability ({calibrated}) "
                f"for raw_probability={raw_probability}"
            )
        return calibrated


_TOTALS_OVERRIDE_FIELDS = {
    "away_bullpen_weakness",
    "home_bullpen_weakness",
    "park_factor",
    "weather_factor",
}


@dataclass(frozen=True)
class TotalsModelOutput:
    run_estimate: RunEstimate
    simulation: ScoreSimulation
    total: MarketDistribution
    total_line: float
    model_version: str
    model_artifact_hash: str
    calibration_version: str


class MeasuredEdgeTotalsModel:
    """Separate totals simulation with independent, versioned calibration."""

    def __init__(self, model_path: str | Path, formula_spec: FormulaSpec) -> None:
        self.raw = _load_artifact(model_path, TOTALS_MODEL_VERSION)
        if formula_spec.formula_version != ENGINE_VERSION:
            raise ValueError("totals model must use the configured Trend Engine score model")
        # Same governance gate as MeasuredEdgeMarginModel -- previously
        # missing entirely here, so a future totals recalibration could ship
        # with zero bounds on scale/offset. Real invariant: calibration
        # doesn't inject a large systematic bias, checked at a 50/50 raw
        # input (see the matching comment on the margin model for why this
        # is checked at 0.5 specifically, not by bounding offset alone).
        scale, offset = _artifact_float(self.raw, "scale"), _artifact_float(self.raw, "offset")
        if not 0 < scale <= 1.5:
            raise ValueError("totals calibration scale outside governance bounds")
        calibrated_at_half = scale * 0.5 + offset
        if not 0.35 <= calibrated_at_half <= 0.65:
            raise ValueError(
                "totals calibration offset implies too much bias at raw probability 0.5"
            )
        self.formula_spec = formula_spec

    def predict(
        self,
        features: MLBGameFeatures,
        total_line: float,
        feature_overrides: dict[str, float] | None = None,
        method: str = "gamma_poisson",
    ) -> TotalsModelOutput:
        totals_features = self._apply_feature_overrides(features, feature_overrides)
        estimate = estimate_runs(totals_features, self.formula_spec)
        simulation = simulate_game(
            totals_features,
            estimate,
            self.formula_spec,
            seed_namespace="totals",
            method=method,
        )
        return TotalsModelOutput(
            run_estimate=estimate,
            simulation=simulation,
            total=derive_market_distribution(simulation, MarketType.TOTAL, total_line),
            total_line=total_line,
            model_version=TOTALS_MODEL_VERSION,
            model_artifact_hash=_artifact_str(self.raw, "artifact_hash"),
            calibration_version=_artifact_str(self.raw, "calibration_version"),
        )

    def calibrate_selected_side(self, raw_probability: float) -> float:
        if not 0 < raw_probability < 1:
            raise ValueError("raw probability must be between 0 and 1")
        calibrated = _artifact_float(self.raw, "scale") * raw_probability + _artifact_float(self.raw, "offset")
        if not 0 < calibrated < 1:
            raise ValueError(
                f"totals calibration produced an out-of-range probability ({calibrated}) "
                f"for raw_probability={raw_probability}"
            )
        return calibrated

    @staticmethod
    def _apply_feature_overrides(
        features: MLBGameFeatures,
        feature_overrides: dict[str, float] | None,
    ) -> MLBGameFeatures:
        """Pass future bullpen, park, and weather inputs into Trend Engine fields."""
        if not feature_overrides:
            return features
        unsupported = set(feature_overrides) - _TOTALS_OVERRIDE_FIELDS
        if unsupported:
            raise ValueError(f"unsupported totals feature overrides: {sorted(unsupported)}")
        # dataclasses.replace can't type-check partial **kwargs against a
        # heterogeneous dataclass; _TOTALS_OVERRIDE_FIELDS restricts the keys
        # above to MLBGameFeatures' float-typed fields only.
        return replace(features, **feature_overrides)  # type: ignore[arg-type]


def _artifact_str(raw: dict[str, object], key: str) -> str:
    value = raw[key]
    assert isinstance(value, str), f"artifact field {key!r} must be a str, got {type(value).__name__}"
    return value


def _artifact_float(raw: dict[str, object], key: str) -> float:
    value = raw[key]
    assert isinstance(value, (int, float)), f"artifact field {key!r} must be numeric, got {type(value).__name__}"
    return float(value)


def _load_artifact(path: str | Path, expected_version: str) -> dict[str, object]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    canonical = {key: value for key, value in raw.items() if key != "artifact_hash"}
    actual_hash = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if actual_hash != raw.get("artifact_hash"):
        raise ValueError("Measured Edge artifact hash mismatch")
    if raw.get("model_version") != expected_version:
        raise ValueError("Measured Edge model version mismatch")
    if raw.get("base_score_model_version") != ENGINE_VERSION:
        raise ValueError("Measured Edge must use the configured Trend Engine score model")
    if raw.get("calibration_method") != "flat_probability_shrinkage_toward_half":
        raise ValueError("Measured Edge calibration method mismatch")
    if "scale" not in raw or "offset" not in raw:
        raise ValueError("Measured Edge config is missing required scale or offset field")
    return raw
