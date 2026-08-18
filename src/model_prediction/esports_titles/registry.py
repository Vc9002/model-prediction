"""Per-title config resolution + namespaced engine (the "split" engine).

resolve(title) maps a game title to its frozen TitleConfig. TitleEloEngine
wraps the SHARED esports.NeutralElo with two structural guarantees the
pre-split code only had by convention:

  1. Team ids are namespaced to (game_title, provider) before touching
     the book -- identity_key() from title_config.py -- so a team id can
     never collide across titles inside one engine instance. The shared
     engine never sees a bare id from this wrapper.
  2. Every hyperparameter knob the shared engine reads from module
     globals is passed as an instance-level override from the config;
     None knobs fall through to the shared defaults (bit-for-bit parity
     with the pre-split engine, proven by scripts/
     esports_title_split_validate.py's reproduction gate).

Not wired into cli.py's live esports forecast path -- candidate split only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .. import esports as shared_esports
from .cs2 import CS2_CONFIG
from .dota2 import DOTA2_CONFIG
from .lol import LOL_CONFIG
from .rainbow_six import RAINBOW_SIX_CONFIG
from .title_config import TitleConfig, identity_key
from .valorant import VALORANT_CONFIG

_TITLES: dict[str, TitleConfig] = {
    config.title: config
    for config in (CS2_CONFIG, VALORANT_CONFIG, LOL_CONFIG, DOTA2_CONFIG, RAINBOW_SIX_CONFIG)
}


def resolve(title: str) -> TitleConfig:
    code = str(title).lower()
    try:
        return _TITLES[code]
    except KeyError as error:
        raise ValueError(f"unsupported esports title: {code}") from error


@dataclass
class TitleEloEngine:
    config: TitleConfig
    _book: shared_esports.NeutralElo = field(init=False)

    def __post_init__(self) -> None:
        self._book = shared_esports.NeutralElo(
            k=self.config.k,
            ratings={},
            platt_intercept=self.config.platt_intercept,
            platt_slope=self.config.platt_slope,
            inactivity_half_life_days=self.config.inactivity_half_life_days,
            inactivity_max_pull=self.config.inactivity_max_pull,
            minimum_reliable_games=self.config.minimum_reliable_games,
            thin_data_max_shrink=self.config.thin_data_max_shrink,
            recency_half_life_days=self.config.recency_half_life_days,
            recency_max_boost=self.config.recency_max_boost,
            tier_weights=self.config.tier_weights,
        )

    def _namespaced(self, team_id: str) -> str:
        return identity_key(self.config.title, self.config.provider, str(team_id))

    def probability(self, team1_id: str, team2_id: str, reference_date: datetime | None = None) -> float:
        return self._book.probability(self._namespaced(team1_id), self._namespaced(team2_id), reference_date)

    def update(self, row: dict[str, Any]) -> None:
        namespaced = dict(row)
        namespaced["team1_id"] = self._namespaced(str(row["team1_id"]))
        namespaced["team2_id"] = self._namespaced(str(row["team2_id"]))
        # winner_id must be namespaced through the SAME key builder or the
        # engine's own winner comparison (winner_id == team1_id) silently
        # breaks -- a bare winner id never equals a namespaced team id, so
        # every update would be scored as a team2 win. The reproduction
        # gate in scripts/esports_title_split_validate.py caught exactly
        # this bug on first run (max probability diff 0.49 vs shared engine).
        if row.get("winner_id") is not None:
            namespaced["winner_id"] = self._namespaced(str(row["winner_id"]))
        self._book.update(namespaced)

    @property
    def ratings(self) -> dict[str, float]:
        """Bare-team-id view for reporting: strips the namespace prefix."""
        prefix = identity_key(self.config.title, self.config.provider, "")
        return {
            key[len(prefix) :]: value for key, value in self._book.ratings.items() if key.startswith(prefix)
        }
