"""Model discovery: one unified model per sport, states tracked in config.

MLB, NBA, WNBA, and NFL share the learned Elo+trend LR moneyline forecast
interface. Artifact-pinned qualification state decides whether a confidence-
gated call is qualified shadow output or a zero-unit research observation.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from ..domain import League, ModelOrigin
from ..lifecycle import model_enabled


@dataclass(frozen=True)
class ModelSpec:
    league: League
    family: str
    target: str
    distribution: str
    trend_features: tuple[str, ...]
    enabled: bool = True
    origin: ModelOrigin = ModelOrigin.STATISTICAL_MODEL


MODEL_SPECS = {
    League.MLB: ModelSpec(
        League.MLB,
        "Elo + opponent-adjusted trend logistic regression",
        "moneyline home-win probability",
        "binary logistic regression",
        ("Elo home-win probability", "offensive momentum gap"),
    ),
    League.NBA: ModelSpec(
        League.NBA,
        "Elo + opponent-adjusted trend engine",
        "moneyline, margin, and total via normal approximation",
        "Elo win probability; normal margin/total",
        ("adjusted efficiency", "pace", "lineup strength", "momentum"),
    ),
    League.WNBA: ModelSpec(
        League.WNBA,
        "Elo + opponent-adjusted trend engine (WNBA constants)",
        "moneyline, margin, and total via normal approximation",
        "Elo win probability; normal margin/total",
        ("adjusted efficiency", "pace", "lineup strength", "momentum"),
    ),
    League.SOCCER: ModelSpec(
        League.SOCCER,
        "Poisson goal model with Dixon-Coles low-score correction",
        "three-way result, O/U 2.5, BTTS",
        "correlated Poisson score matrix",
        ("EWMA attack/defense strength", "home-goal boost", "low-score dependence"),
    ),
    League.TENNIS: ModelSpec(
        League.TENNIS,
        "surface-blended Elo (60% surface-specific, 40% overall)",
        "moneyline win probability, WTA only",
        "chronological Elo updated match-by-match, blended per surface",
        ("overall Elo", "per-surface Elo", "singles-only ESPN match history"),
    ),
    League.NFL: ModelSpec(
        League.NFL,
        "Elo + opponent-adjusted trend engine",
        "moneyline, margin, and total via normal approximation",
        "Elo win probability; normal margin/total",
        ("scoring form", "opponent adjustment", "momentum"),
    ),
    League.NCAAF: ModelSpec(
        League.NCAAF,
        "Elo + opponent-adjusted trend & key-number engine",
        "moneyline, spread margin, and total via calibrated continuous-discrete distribution",
        "Elo win probability; key-number adjusted margin/total",
        ("adjusted efficiency", "tempo", "conference strength", "rest disparity", "momentum"),
    ),
    League.LOL: ModelSpec(
        League.LOL,
        "venue-neutral series Elo baseline",
        "best-of match/series winner probability",
        "binary Elo expectation",
        ("point-in-time team Elo",),
    ),
    League.CS2: ModelSpec(
        League.CS2,
        "venue-neutral series Elo baseline",
        "best-of match/series winner probability",
        "binary Elo expectation",
        ("point-in-time team Elo",),
    ),
    League.KBO: ModelSpec(
        League.KBO,
        "tie-aware home-field Elo baseline",
        "expected moneyline settlement (tie pays 0.50)",
        "decisive-result Elo plus empirical tie probability",
        ("point-in-time team Elo", "home field", "league tie rate"),
    ),
    League.NPB: ModelSpec(
        League.NPB,
        "tie-aware home-field Elo baseline",
        "expected moneyline settlement (tie pays 0.50)",
        "decisive-result Elo plus empirical tie probability",
        ("point-in-time team Elo", "home field", "league tie rate"),
    ),
    League.DOTA2: ModelSpec(
        League.DOTA2,
        "venue-neutral series Elo baseline",
        "best-of match/series winner probability",
        "binary Elo expectation",
        ("point-in-time team Elo",),
    ),
    League.VALORANT: ModelSpec(
        League.VALORANT,
        "venue-neutral series Elo baseline",
        "best-of match/series winner probability",
        "binary Elo expectation",
        ("point-in-time team Elo",),
    ),
    League.RAINBOW_SIX: ModelSpec(
        League.RAINBOW_SIX,
        "venue-neutral series Elo baseline",
        "best-of match/series winner probability",
        "binary Elo expectation",
        ("point-in-time team Elo",),
    ),
    League.HOCKEY: ModelSpec(
        League.HOCKEY,
        "Poisson goal model with Dixon-Coles low-score correction and OT adjustment",
        "two-way moneyline, puck line spread, and total goals",
        "correlated Poisson score matrix with sudden-death OT resolution",
        ("EWMA attack/defense strength", "home-ice boost", "low-score dependence"),
    ),
    League.NHL: ModelSpec(
        League.NHL,
        "Poisson goal model with Dixon-Coles low-score correction and OT adjustment",
        "two-way moneyline, puck line spread, and total goals",
        "correlated Poisson score matrix with sudden-death OT resolution",
        ("EWMA attack/defense strength", "home-ice boost", "low-score dependence"),
    ),
    League.DARTS: ModelSpec(
        League.DARTS,
        "exact Markov leg-progression distribution",
        "match winner, leg spread, and total legs",
        "discrete Markov leg-state probability distribution",
        ("point-in-time player Elo rating", "leg hold expectancy", "break probability"),
    ),
    League.DARTS_PDC: ModelSpec(
        League.DARTS_PDC,
        "exact Markov leg-progression distribution",
        "match winner, leg spread, and total legs",
        "discrete Markov leg-state probability distribution",
        ("point-in-time player Elo rating", "leg hold expectancy", "break probability"),
    ),
    League.DARTS_MODUS: ModelSpec(
        League.DARTS_MODUS,
        "exact Markov leg-progression distribution",
        "match winner, leg spread, and total legs",
        "discrete Markov leg-state probability distribution",
        ("point-in-time player Elo rating", "leg hold expectancy", "break probability"),
    ),
    League.NCAAB: ModelSpec(
        League.NCAAB,
        "independent tempo and possession scoring engine",
        "moneyline, margin, and total via normal approximation (NCAAB constants)",
        "Elo win probability; 40-minute college possession margin/total",
        ("adjusted efficiency", "tempo", "conference strength", "home court advantage"),
    ),
    League.WNCAAB: ModelSpec(
        League.WNCAAB,
        "independent tempo and possession scoring engine (WNCAAB constants)",
        "moneyline, margin, and total via normal approximation",
        "Elo win probability; 40-minute women's college margin/total",
        ("adjusted efficiency", "tempo", "momentum"),
    ),
    League.EUROLEAGUE: ModelSpec(
        League.EUROLEAGUE,
        "independent FIBA tempo and possession scoring engine",
        "moneyline, margin, and total via normal approximation (EuroLeague constants)",
        "Elo win probability; 40-minute FIBA margin/total",
        ("adjusted efficiency", "FIBA pace", "European home advantage"),
    ),
}

_CONFIG_STATUS_CACHE: dict[str, dict[str, bool]] = {}


def _enabled_from_config(league: League) -> bool | None:
    """Read a league's enabled flag from config/model.yaml, if present."""
    cache_key = "main"
    if cache_key not in _CONFIG_STATUS_CACHE:
        from ..config import PROJECT_ROOT

        config_path = PROJECT_ROOT / "config/model.yaml"
        statuses: dict[str, bool] = {}
        if config_path.exists():
            try:
                import yaml

                raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
                models = raw.get("models") or {}
            except Exception:  # noqa: BLE001 — config fallback must never crash status derivation
                models = {}
            for lk, v in models.items():
                if lk not in League.__members__:
                    continue
                # One malformed league entry must not blank out every OTHER
                # league's flag -- a single bad value here previously poisoned
                # the whole cache via one blanket try/except.
                with suppress(ValueError, AttributeError):
                    statuses[lk] = model_enabled(v)
        _CONFIG_STATUS_CACHE[cache_key] = statuses
    return _CONFIG_STATUS_CACHE[cache_key].get(league.value)


def model_spec(league: League) -> ModelSpec:
    spec = MODEL_SPECS[league]
    config_enabled = _enabled_from_config(league)
    if config_enabled is not None and config_enabled != spec.enabled:
        return ModelSpec(
            league=spec.league,
            family=spec.family,
            target=spec.target,
            distribution=spec.distribution,
            trend_features=spec.trend_features,
            enabled=config_enabled,
            origin=spec.origin,
        )
    return spec


def get_model(sport: str) -> Any:
    """Return the unified model object for a sport key."""
    key = sport.lower()
    from .basketball_leagues import euroleague_model, ncaab_model, wncaab_model
    from .college_football import cfb_model
    from .darts import darts_model
    from .hockey import hockey_model
    from .soccer import soccer_model
    from .tennis import tennis_model

    factories: dict[str, Callable[[], Any]] = {
        "soccer": soccer_model,
        "tennis": tennis_model,
        "ncaaf": cfb_model,
        "cfb": cfb_model,
        "hockey": hockey_model,
        "nhl": hockey_model,
        "darts": darts_model,
        "ncaab": ncaab_model,
        "wncaab": wncaab_model,
        "eurolg": euroleague_model,
        "euroleague": euroleague_model,
    }
    if key in ("mlb", "nba", "wnba", "nfl"):
        raise ValueError(
            f"{sport} production uses learned_forward.build_learned_moneyline_slate; "
            "this registry is for research/backtest models only"
        )
    if key not in factories:
        raise ValueError(f"no unified model for sport: {sport}")
    return factories[key]()


def get_market_model(league_or_sport: str, market: Any) -> Any:
    """Return a dedicated per-league, per-market model object."""
    from .market_models import get_market_model as _get_market_model

    return _get_market_model(league_or_sport, market)
