"""Per-title configuration shape for the esports split.

One TitleConfig per game title, frozen in the title's own module (cs2.py,
valorant.py, ...). The engine (engine.py) reads ONLY the config -- the
shared esports.NeutralElo gets each knob as an instance-level override, so
two titles with different numbers can never leak into each other, and a
config with None knobs reproduces the pre-split module-default behavior
bit-for-bit (the reproduction gate scripts/esports_title_split_validate.py
proves exactly that).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TitleConfig:
    title: str  # lowercase, stable: cs2 | valorant | lol | dota2 | rainbow_six
    model_id: str  # e.g. "cs2-series-v7-lr" per the backlog's naming
    k: float
    confidence_threshold: float
    platt_intercept: float | None
    platt_slope: float | None
    # Engine overrides: None = shared module default (esports.py globals).
    inactivity_half_life_days: float | None = None
    inactivity_max_pull: float | None = None
    minimum_reliable_games: int | None = None
    thin_data_max_shrink: float | None = None
    recency_half_life_days: float | None = None
    recency_max_boost: float | None = None
    tier_weights: dict[str, float] | None = None
    # Feature plan is documentation of the title-specific feature set the
    # backlog calls for -- most items are DATA-GATED (map Elo needs map
    # results per match, patch context needs patch histories, etc.) and
    # none are wired into serving until built and walk-forward validated.
    # It is listed here so the split's structure is honest about what is
    # implemented vs planned, never to imply the features exist.
    feature_plan: tuple[str, ...] = field(default_factory=tuple)
    # Identity: the provider this title's team ids come from. The hard
    # invariant is enforced structurally by engine.py's namespacing --
    # ids from different providers can never collide inside one book.
    provider: str = "bo3"


def identity_key(game_title: str, provider: str, provider_team_id: str) -> str:
    """The hard identity invariant from the backlog: (game_title, provider,
    provider_team_id) -- NOT the bare team id, and never an organization
    name (which is metadata, and can legitimately collide across titles:
    Cloud9 CS2 vs Cloud9 LoL are distinct entities)."""
    return f"{game_title.lower()}::{provider.lower()}::{provider_team_id}"
