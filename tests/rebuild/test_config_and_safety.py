from __future__ import annotations

from pathlib import Path

import pytest

from model_prediction.rebuild.config import RebuildConfigError, load_rebuild_config
from model_prediction.rebuild.safety import RebuildPathPolicy, RebuildSafetyError, reject_production_adapter

VALID_CONFIG = """
rebuild:
  enabled: true
  shadow_only: true
  execution_enabled: false
  production_promotion: false
paths:
  data_root: data/rebuild
  output_root: outputs/rebuild
  challenger_root: config/models/challengers
execution:
  allow_live_orders: false
  allow_production_ledger_write: false
sports:
  mlb: {enabled: true, status: prospective_validation}
"""


def _config(tmp_path: Path, text: str = VALID_CONFIG) -> Path:
    path = tmp_path / "config" / "rebuild.yaml"
    path.parent.mkdir()
    path.write_text(text)
    return path


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("shadow_only: true", "shadow_only: false"),
        ("execution_enabled: false", "execution_enabled: true"),
        ("production_promotion: false", "production_promotion: true"),
        ("allow_live_orders: false", "allow_live_orders: true"),
        ("allow_production_ledger_write: false", "allow_production_ledger_write: true"),
    ],
)
def test_hard_shadow_gates_cannot_be_enabled(tmp_path, old, new):
    with pytest.raises(RebuildConfigError):
        load_rebuild_config(_config(tmp_path, VALID_CONFIG.replace(old, new)))


def test_cli_paths_are_confined_to_rebuild_roots(tmp_path):
    config = load_rebuild_config(_config(tmp_path))
    policy = RebuildPathPolicy.from_config(config)
    assert policy.assert_runtime_write(tmp_path / "data" / "rebuild" / "shadow.db")
    with pytest.raises(RebuildSafetyError):
        policy.assert_runtime_write(tmp_path / "data" / "main" / "ledger.jsonl")
    with pytest.raises(RebuildSafetyError):
        policy.assert_runtime_write(tmp_path / "data" / "flat" / "ledger.jsonl")


def test_incumbent_artifact_path_is_rejected(tmp_path):
    config = load_rebuild_config(_config(tmp_path))
    policy = RebuildPathPolicy.from_config(config)
    with pytest.raises(RebuildSafetyError):
        policy.assert_challenger_artifact(tmp_path / "config" / "models" / "mlb.json")


def test_production_order_adapter_is_rejected():
    ProductionOrderAdapter = type("ProductionOrderAdapter", (), {})
    with pytest.raises(RebuildSafetyError):
        reject_production_adapter(ProductionOrderAdapter())
