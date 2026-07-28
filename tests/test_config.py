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
        # v6's probable_starter_era_gap coefficient was fit on non-PIT ESPN
        # data (current ERA applied retroactively to historical dates) and
        # is force-set unqualified regardless of raw holdout numbers -- see
        # test_mlb_v6_stays_unqualified_despite_raw_holdout_numbers below.
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


def test_mlb_v6_stays_unqualified_despite_raw_holdout_numbers() -> None:
    """MLB v6's active feature is probable_starter_era_gap, whose coefficient
    was fit on ESPN's probables endpoint -- confirmed (by this artifact's own
    experimental_note) to return the pitcher's CURRENT/live ERA regardless of
    query date, so applying it to historical training dates leaks future
    information. The coefficient sign is backwards (worse home-starter ERA
    *raising* home win probability) and the feature alone predicts at 25%
    accuracy, worse than chance -- the 71.1% raw holdout hit rate it rode to
    is 9-3 during a short span picking mostly home/market-favorite teams, not
    independent model skill.

    A later automated re-evaluation of this same frozen model against fresh
    games could technically clear the 50-call/60%-hit-rate bar on raw
    numbers again (as it did once, requiring this fix) without the
    underlying methodology becoming any more trustworthy. This test pins
    `qualified`/`meets_primary_holdout_metrics` to False and requires the
    contamination to stay documented in `failures`, so a bare holdout
    re-evaluation silently flipping this back to True fails CI instead of
    quietly re-arming a spurious "qualified" label for real-money execution
    gates (cli.py::_row_artifact_qualified) to trust.
    """
    config = load_config()
    artifact_path = PROJECT_ROOT / config["models"]["MLB"]["production_artifact"]
    raw = yaml.safe_load(artifact_path.read_text(encoding="utf-8")) or {}
    # yaml.safe_load also parses JSON (a strict subset); avoids a second import.
    qualification = raw["qualification"]
    assert qualification["qualified"] is False
    assert qualification["meets_primary_holdout_metrics"] is False
    assert any("CONTAMINATED_FEATURE" in failure for failure in qualification["failures"])
    assert "probable_starter_era_gap" in raw["market_models"]["moneyline"]["feature_names"]
