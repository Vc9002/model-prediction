from pathlib import Path

import pytest
import yaml

from model_prediction.audit import AuditLog
from model_prediction.bans import TeamBanList
from model_prediction.entities import EntityRegistry


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def registry() -> EntityRegistry:
    return EntityRegistry.from_json(PROJECT_ROOT / "data/entities/teams.json")


@pytest.fixture
def ban_list(tmp_path, registry) -> TeamBanList:
    config = {
        "schema_version": "2",
        "unrelated": {"preserve": True},
        "team_ban_list": {
            "enabled": True,
            "teams": {"MLB": [], "NBA": [], "WNBA": [], "NFL": [], "WORLD_CUP": []},
            "allowed_reasons": ["manual_governance", "data_quality"],
        },
    }
    path = tmp_path / "model.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return TeamBanList(path, registry, AuditLog(tmp_path / "events.jsonl"))
