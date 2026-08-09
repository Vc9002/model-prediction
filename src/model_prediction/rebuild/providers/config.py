"""Validated rate-limit/retry configuration for rebuild data sources."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from model_prediction.rebuild.config import default_repo_root


class ProviderPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    min_interval_seconds: float = Field(ge=0)
    retries: int = Field(ge=1, le=10)


class RebuildSourcesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sportsdataverse: ProviderPolicy
    nflverse: ProviderPolicy
    football_data: ProviderPolicy
    open_meteo: ProviderPolicy
    polymarket: ProviderPolicy


def load_rebuild_sources_config(path: str | Path | None = None) -> RebuildSourcesConfig:
    source_path = Path(path) if path is not None else default_repo_root() / "config" / "rebuild_sources.yaml"
    payload = yaml.safe_load(source_path.read_text())
    return RebuildSourcesConfig.model_validate(payload)
