import json
from pathlib import Path

from model_prediction.models.mlb_first_inning import FirstInningGameRow, MLBHalfInningModel
from model_prediction.production_registry import ProductionModelRegistry, compute_artifact_hash


def test_mlb_nrfi_v3_artifact_hash_and_registry():
    # 1. Artifact Hash Verification
    repo_root = Path(__file__).resolve().parents[1]
    artifact_path = repo_root / "config/models/mlb-nrfi-v3.json"
    assert artifact_path.exists()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    embedded_hash = payload["artifact_hash"]
    computed_hash = compute_artifact_hash(payload)
    assert embedded_hash == computed_hash
    assert payload["model_version"] == "mlb-nrfi-v3"
    assert payload["method"] == "half_inning_logistic"
    assert payload["qualification"]["qualified"] is True
    assert payload["qualification"]["holdout_log_loss"] < 0.691

    # 2. Production Registry Champion Resolution
    registry = ProductionModelRegistry.load(repo_root)
    champ = registry.champion("MLB", "nrfi")
    assert champ.model_id in ("mlb-nrfi-v3", "mlb-nrfi-v4")
    assert "mlb-nrfi-v3" in registry.entries
    assert registry.entries["mlb-nrfi-v3"].available is True
    assert "mlb-nrfi-v3" not in registry.blocked_workflows

    # 3. Model from_dict instantiation and prediction
    model = MLBHalfInningModel.from_dict(payload)
    assert len(model.coef_away) == len(model.feature_names)
    assert len(model.coef_home) == len(model.feature_names)
    assert len(model.scaler_mean) == len(model.feature_names)
    assert model.fit_n_games >= 4000

    features = dict(zip(payload["feature_names"], payload["scaler_mean"], strict=True))
    row = FirstInningGameRow(None, "2026-10-07T20:00:00Z", "Home", "Away", "Venue", features, 0, 0.0)
    p_nrfi = model.predict_p_nrfi(row)
    assert 0.40 < p_nrfi < 0.70
    p_yrfi = model.predict_p_yrfi(row)
    assert abs(p_nrfi + p_yrfi - 1.0) < 1e-9
