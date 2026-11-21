"""Named NBA engines for the research replay harness (research only; nothing here serves)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .features.base import GameRecord
from .models.base import GamePrediction
from .models.basketball import UpcomingGame
from .models.nba_v9 import NBAStructuralV9Engine
from .nba_replay_harness import EngineSpec

V9_ARTIFACT = Path("config/models/nba-spread-margin-v9.json")
V91_RESEARCH_DIR = Path("config/models/research")


def _v9_spec(repo: Path) -> EngineSpec:
    artifact = json.loads((repo / V9_ARTIFACT).read_text(encoding="utf-8"))
    engine = NBAStructuralV9Engine.from_artifact(artifact)

    def predict(
        history: Sequence[GameRecord], upcoming: Sequence[UpcomingGame], _day: Sequence[GameRecord]
    ) -> list[GamePrediction]:
        return engine.predict_games(history, upcoming)

    return EngineSpec("v9", predict, market_blend_weight=engine.market_blend_weight)


def v91_spec(name: str, params: dict[str, Any]) -> EngineSpec:
    from .models.nba_v91 import NBAStructuralV91Engine

    engine = NBAStructuralV91Engine.from_artifact(params)

    def predict(
        history: Sequence[GameRecord], upcoming: Sequence[UpcomingGame], day: Sequence[GameRecord]
    ) -> list[GamePrediction]:
        return engine.predict_games(history, upcoming, season_types={g.event_id: g.season_type for g in day})

    return EngineSpec(name, predict, market_blend_weight=engine.market_blend_weight)


def engine_registry(repo: Path) -> dict[str, EngineSpec]:
    """v9 plus every v9.1 ablation config listed in outputs/nba/v10_harness/v91_configs.json."""
    registry = {"v9": _v9_spec(repo)}
    configs_path = repo / "outputs" / "nba" / "v10_harness" / "v91_configs.json"
    if configs_path.exists():
        base = json.loads((repo / V9_ARTIFACT).read_text(encoding="utf-8"))
        for name, overrides in json.loads(configs_path.read_text(encoding="utf-8")).items():
            registry[name] = v91_spec(name, {**base, **overrides})
    return registry
