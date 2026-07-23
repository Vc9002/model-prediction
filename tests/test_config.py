import yaml

from model_prediction.config import PROJECT_ROOT, load_config
from model_prediction.models.learned_market import LearnedMarketArtifact


def test_model_freeze_cannot_be_enabled_by_configuration(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "model.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "model_iteration_policy": {
                    "status": "frozen",
                    "parameter_freezes_allowed": True,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MODEL_PREDICTION_CONFIG", str(config_path))

    policy = load_config()["model_iteration_policy"]

    assert policy["status"] == "continuous"
    assert policy["parameter_freezes_allowed"] is True
    assert policy["require_versioned_change"] is True
    assert policy["require_walk_forward_ablation"] is True
    assert policy["require_locked_holdout_before_promotion"] is True


def test_default_qualification_policy_is_accuracy_first() -> None:
    config = load_config()
    assert config["selection_gate"]["target_called_hit_rate"] == 0.60
    assert config["selection_gate"]["minimum_locked_holdout_calls"] == 50
    assert config["selection_gate"]["require_positive_executable_price_ev"] is False
    assert config["selection_gate"]["polymarket_costs_required_for_qualification"] is False


def test_configured_production_artifact_state_matches_locked_audit() -> None:
    config = load_config()
    # config `status` is the operator's deliberate promotion decision;
    # the artifact `qualified` boolean is the locked-holdout EVIDENCE. They
    # are allowed to differ only in the documented direction (operator
    # promotes despite a failed gate), and this test pins the current truth
    # so silent drift in either direction fails CI.
    expected = {
        "MLB": ("shadow_qualified", False),  # v5 holdout 56.3% < 60% gate; promotion is operator override
        "NBA": ("shadow_qualified", True),
        "WNBA": ("shadow_qualified", True),
        "NFL": ("shadow_qualified", True),  # v4 (ET cohorts) qualifies: 71.3% on 87 locked calls
    }
    for sport, (status, qualified) in expected.items():
        model = config["models"][sport]
        artifact = LearnedMarketArtifact.load(PROJECT_ROOT / model["production_artifact"])
        assert model["status"] == status
        assert artifact.qualified is qualified
