"""Model discovery: one unified model per sport, states tracked in config.

MLB, NBA, WNBA, and NFL share the learned Elo+trend LR moneyline forecast
interface. Artifact-pinned qualification state decides whether a confidence-
gated call is qualified shadow output or a zero-unit research observation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

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
        ("EWMA attack/defense strength", "form points", "over/BTTS base rates"),
    ),
    League.TENNIS: ModelSpec(
        League.TENNIS,
        "surface-blended Elo",
        "match winner (flat call)",
        "Elo logistic",
        ("surface Elo", "overall Elo", "recent form"),
    ),
    League.WORLD_CUP: ModelSpec(
        League.WORLD_CUP,
        "Poisson goal model with Dixon-Coles low-score correction",
        "three-way result, O/U 2.5, BTTS",
        "correlated Poisson score matrix",
        ("EWMA attack/defense strength", "form points", "tournament incentives"),
    ),
    League.NFL: ModelSpec(
        League.NFL,
        "Elo + opponent-adjusted trend engine",
        "moneyline, margin, and total via normal approximation",
        "Elo win probability; normal margin/total",
        ("scoring form", "opponent adjustment", "momentum"),
        status=ModelState.SHADOW_QUALIFIED,
    ),
}


def model_spec(league: League) -> ModelSpec:
    return MODEL_SPECS[league]


def get_model(sport: str) -> Any:
    """Return the unified model object for a sport key."""
    key = sport.lower()
    factories: dict[str, Callable[[], Any]] = {}
    from .nba import nba_model
    from .nfl import nfl_model
    from .soccer import soccer_model
    from .tennis import tennis_model
    from .wnba import wnba_model

    factories = {
        "nba": nba_model,
        "nfl": nfl_model,
        "wnba": wnba_model,
        "soccer": soccer_model,
        "tennis": tennis_model,
    }
    if key == "mlb":
        raise ValueError("use model_prediction.learned_forward.build_learned_moneyline_slate")
    if key not in factories:
        raise ValueError(f"no unified model for sport: {sport}")
    return factories[key]()
