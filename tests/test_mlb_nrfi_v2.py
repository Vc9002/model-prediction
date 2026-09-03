import json
from pathlib import Path

from model_prediction.models.mlb_first_inning import MLBFirstInningModel
from model_prediction.portfolio.auto_executor import DEFAULT_WHITELIST_MODELS, EXPLICIT_BLACKLIST_MODELS
from model_prediction.production_registry import ProductionModelRegistry, compute_artifact_hash


def test_mlb_nrfi_v2_artifact_hash_and_registry():
    # 1. Artifact Hash Verification
    artifact_path = Path("config/models/mlb-nrfi-v2.json")
    assert artifact_path.exists()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    embedded_hash = payload["artifact_hash"]
    computed_hash = compute_artifact_hash(payload)
    assert embedded_hash == computed_hash
    assert payload["model_version"] == "mlb-nrfi-v2"
    assert payload["qualification"]["qualified"] is True
    assert payload["qualification"]["holdout_log_loss"] < 0.693

    # 2. Production Registry Champion Resolution
    registry = ProductionModelRegistry.load(Path("."))
    champ = registry.champion("MLB", "nrfi")
    assert champ.model_id == "mlb-nrfi-v2"
    assert champ.available is True
    assert "mlb-nrfi-v2" not in registry.blocked_workflows

    # 3. Model from_dict instantiation and prediction
    model = MLBFirstInningModel.from_dict(payload)
    assert len(model.coef) > 0
    assert len(model.scaler_mean) == len(model.feature_names)
    assert model.train_nrfi_rate > 0.40

    # 4. Whitelist / Blacklist -- all MLB pulled from Auto-Buyer 2026-09-02
    # pending qualification review (see auto_executor.py's blacklist comment
    # and docs/DEBUG.md); mlb-nrfi-v2 stays a valid production champion for
    # MLB.nrfi (asserted above), just not live-staked by Auto-Buyer for now.
    assert "mlb-nrfi-v2" not in DEFAULT_WHITELIST_MODELS
    assert "mlb-nrfi-v2" in EXPLICIT_BLACKLIST_MODELS
    assert "mlb-nrfi-v1" in EXPLICIT_BLACKLIST_MODELS
