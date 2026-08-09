from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from model_prediction.rebuild.providers.config import load_rebuild_sources_config


def test_repository_source_config_is_valid():
    root = Path(__file__).parents[2]
    config = load_rebuild_sources_config(root / "config/rebuild_sources.yaml")
    assert config.sportsdataverse.min_interval_seconds == 1.0
    assert config.nflverse.min_interval_seconds == 1.0
    assert config.football_data.min_interval_seconds >= 6.0


def test_unknown_provider_config_fails_closed(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text(
        "sportsdataverse: {min_interval_seconds: 1, retries: 3}\n"
        "football_data: {min_interval_seconds: 6.5, retries: 3}\n"
        "open_meteo: {min_interval_seconds: 1, retries: 3}\n"
        "polymarket: {min_interval_seconds: 0.5, retries: 3}\n"
        "invented_paid_feed: {min_interval_seconds: 0, retries: 9}\n"
    )
    with pytest.raises(ValidationError):
        load_rebuild_sources_config(path)
