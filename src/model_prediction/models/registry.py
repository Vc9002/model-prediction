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

from ..domain import League, ModelOrigin, ModelState


@dataclass(frozen=True)
class ModelSpec:
    league: League
    family: str
    target: str
    distribution: str
    trend_features: tuple[str, ...]
    status: ModelState = ModelState.RESEARCH
    origin: ModelOrigin = ModelOrigin.STATISTICAL_MODEL


MODEL_SPECS = {
    League.MLB: ModelSpec(
        League.MLB,
        "Elo + opponent-adjusted trend logistic regression",
        "moneyline home-win probability",
        "binary logistic regression",
        ("Elo home-win probability", "offensive momentum gap"),
        status=ModelState.SHADOW_QUALIFIED,
    ),
    League.NBA: ModelSpec(
        League.NBA,
        "Elo + opponent-adjusted trend engine",
        "moneyline, margin, and total via normal approximation",
        "Elo win probability; normal margin/total",
        ("adjusted efficiency", "pace", "lineup strength", "momentum"),
        status=ModelState.SHADOW_QUALIFIED,
    ),
    League.WNBA: ModelSpec(
        League.WNBA,
        "Elo + opponent-adjusted trend engine (WNBA constants)",
        "moneyline, margin, and total via normal approximation",
        "Elo win probability; normal margin/total",
        ("adjusted efficiency", "pace", "lineup strength", "momentum"),
        status=ModelState.SHADOW_QUALIFIED,
    ),
    League.SOCCER: ModelSpec(
        League.SOCCER,
        "Poisson goal model with Dixon-Coles low-score correction",
        "three-way result, O/U 2.5, BTTS",
        "correlated Poisson score matrix",
        ("EWMA attack/defense strength", "home-goal boost", "low-score dependence"),
        status=ModelState.RESEARCH,
    ),
    League.TENNIS: ModelSpec(
        League.TENNIS,
        "surface-blended Elo (60% surface-specific, 40% overall)",
        "moneyline win probability, WTA only",
        "chronological Elo updated match-by-match, blended per surface",
        ("overall Elo", "per-surface Elo", "singles-only ESPN match history"),
        status=ModelState.RESEARCH,
    ),
    League.NFL: ModelSpec(
        League.NFL,
        "Elo + opponent-adjusted trend engine",
        "moneyline, margin, and total via normal approximation",
        "Elo win probability; normal margin/total",
        ("scoring form", "opponent adjustment", "momentum"),
        status=ModelState.SHADOW_QUALIFIED,
    ),
    League.LOL: ModelSpec(
        League.LOL,
        "venue-neutral series Elo baseline",
        "best-of match/series winner probability",
        "binary Elo expectation",
        ("point-in-time team Elo",),
        status=ModelState.RESEARCH,
    ),
    League.CS2: ModelSpec(
        League.CS2,
        "venue-neutral series Elo baseline",
        "best-of match/series winner probability",
        "binary Elo expectation",
        ("point-in-time team Elo",),
        status=ModelState.RESEARCH,
    ),
    League.KBO: ModelSpec(
        League.KBO,
        "tie-aware home-field Elo baseline",
        "expected moneyline settlement (tie pays 0.50)",
        "decisive-result Elo plus empirical tie probability",
        ("point-in-time team Elo", "home field", "league tie rate"),
        status=ModelState.RESEARCH,
    ),
    League.NPB: ModelSpec(
        League.NPB,
        "tie-aware home-field Elo baseline",
        "expected moneyline settlement (tie pays 0.50)",
        "decisive-result Elo plus empirical tie probability",
        ("point-in-time team Elo", "home field", "league tie rate"),
        status=ModelState.RESEARCH,
    ),
    League.DOTA2: ModelSpec(
        League.DOTA2,
        "venue-neutral series Elo baseline",
        "best-of match/series winner probability",
        "binary Elo expectation",
        ("point-in-time team Elo",),
        status=ModelState.RESEARCH,
    ),
    League.VALORANT: ModelSpec(
        League.VALORANT,
        "venue-neutral series Elo baseline",
        "best-of match/series winner probability",
        "binary Elo expectation",
        ("point-in-time team Elo",),
        status=ModelState.RESEARCH,
    ),
    League.RAINBOW_SIX: ModelSpec(
        League.RAINBOW_SIX,
        "venue-neutral series Elo baseline",
        "best-of match/series winner probability",
        "binary Elo expectation",
        ("point-in-time team Elo",),
        status=ModelState.RESEARCH,
    ),
}

_CONFIG_STATUS_CACHE: dict[str, dict[str, ModelState]] = {}


def _status_from_config(league: League) -> ModelState | None:
    """Read a league's model state from config/model.yaml, if present."""
    cache_key = "main"
    if cache_key not in _CONFIG_STATUS_CACHE:
        from ..config import PROJECT_ROOT

        config_path = PROJECT_ROOT / "config/model.yaml"
        statuses: dict[str, ModelState] = {}
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
                # One league using a non-ModelState status string in the YAML
                # (a stale placeholder, a typo, whatever) must not silently
                # blank out every OTHER league's status -- a single bad value
                # here previously poisoned the whole cache via one blanket
                # try/except around the entire dict comprehension.
                with suppress(ValueError, AttributeError):
                    statuses[lk] = ModelState(v.get("status", "research"))
        _CONFIG_STATUS_CACHE[cache_key] = statuses
    return _CONFIG_STATUS_CACHE[cache_key].get(league.value)


def model_spec(league: League) -> ModelSpec:
    spec = MODEL_SPECS[league]
    config_status = _status_from_config(league)
    if config_status is not None and config_status != spec.status:
        return ModelSpec(
            league=spec.league,
            family=spec.family,
            target=spec.target,
            distribution=spec.distribution,
            trend_features=spec.trend_features,
            status=config_status,
            origin=spec.origin,
        )
    return spec


def get_model(sport: str) -> Any:
    """Return the unified model object for a sport key."""
    key = sport.lower()
    factories: dict[str, Callable[[], Any]] = {}
    from .soccer import soccer_model
    from .tennis import tennis_model

    factories = {
        "soccer": soccer_model,
        "tennis": tennis_model,
    }
    if key in ("mlb", "nba", "wnba", "nfl"):
        raise ValueError(
            f"{sport} production uses learned_forward.build_learned_moneyline_slate; "
            "this registry is for research/backtest models only"
        )
    if key not in factories:
        raise ValueError(f"no unified model for sport: {sport}")
    return factories[key]()
