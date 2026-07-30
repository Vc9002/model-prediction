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
        # v7 (2026-07-30) is a real walk-forward fit on point-in-time-safe
        # features only (no probable_starter_era_gap) -- honestly unqualified
        # on real numbers (58.0% holdout hit rate, below the 60% bar), not
        # force-unqualified due to contamination like v6 before it -- see
        # test_mlb_v7_reports_its_real_holdout_shortfall below.
        "MLB": ("shadow_qualified", False),
        "NBA": ("shadow_qualified", True),
        "WNBA": ("shadow_qualified", True),
        "NFL": ("shadow_qualified", True),  # v4 (ET cohorts) qualifies: 71.3% on 87 locked calls
    }
    for sport, (status, qualified) in expected.items():
        model = config["models"][sport]
        artifact = LearnedMarketArtifact.load(PROJECT_ROOT / model["production_artifact"])
        assert model["status"] == status
        assert artifact.qualified is qualified


def test_mlb_v7_reports_its_real_holdout_shortfall() -> None:
    """MLB v7 (2026-07-30) replaced v6's contaminated probable_starter_era_gap
    experiment with a real walk-forward fit on the full historical dataset
    (3784 train / 1076 validation / 1370 locked-holdout games), using only
    point-in-time-safe features (elo_probability, trend_gap, park_factor,
    weather_factor, pitcher_era_gap, bullpen_fatigue_gap -- no ESPN live
    probables). It is honestly unqualified on real numbers, not contaminated:
    58.0% locked-holdout hit rate is genuinely below this project's 60% bar,
    and April 2026 was a real losing month. This test pins that real shortfall
    so a future re-evaluation can't silently mark it qualified without the
    underlying numbers actually clearing the bar.
    """
    config = load_config()
    artifact_path = PROJECT_ROOT / config["models"]["MLB"]["production_artifact"]
    raw = yaml.safe_load(artifact_path.read_text(encoding="utf-8")) or {}
    # yaml.safe_load also parses JSON (a strict subset); avoids a second import.
    qualification = raw["qualification"]
    assert qualification["qualified"] is False
    assert qualification["meets_primary_holdout_metrics"] is False
    assert qualification["hit_rate"] < 0.60
    assert not any("CONTAMINATED_FEATURE" in failure for failure in qualification["failures"])
    assert "probable_starter_era_gap" not in raw["market_models"]["moneyline"]["feature_names"]
