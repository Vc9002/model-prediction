"""Rainbow Six per-title config.

K/platt/threshold frozen from the live v6 artifact's own fitted values
(runtime-root models/rainbow_six-tiered-elo-v6.json -- k=40.0,
threshold=0.0, platt=(0.1309, 0.9288), n=3,009 matches). Engine knobs left
at None (shared defaults) until a title-specific grid says otherwise.
Smallest title sample in the system -- the widest Platt slope (0.93) of
the five, and the zero confidence threshold, both consistent with that.
"""

from __future__ import annotations

from .title_config import TitleConfig

RAINBOW_SIX_CONFIG = TitleConfig(
    title="rainbow_six",
    model_id="rainbow-six-series-v7-lr",
    k=40.0,
    confidence_threshold=0.0,
    platt_intercept=0.1309174520504469,
    platt_slope=0.9288317588937437,
    feature_plan=(
        "map_elo",  # data-gated
        "attack_defense_side",  # data-gated
    ),
)
