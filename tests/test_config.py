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
                },
                # Minimum sections validate_config requires -- this test's
                # only concern is model_iteration_policy, but load_config now
                # validates the whole file's structure at load time.
                "project": {"ledger_path": "data/picks.xlsx", "audit_path": "data/events.jsonl"},
                "bankroll": {"unit_value_usd": 5.0},
                "execution": {"status": "paper"},
                "models": {},
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


def _minimal_valid_config(**overrides) -> dict:
    config = {
        "project": {"ledger_path": "data/picks.xlsx", "audit_path": "data/events.jsonl"},
        "bankroll": {"unit_value_usd": 5.0},
        "execution": {"status": "paper"},
        "models": {"MLB": {"status": "shadow_qualified", "origin": "statistical_model", "min_edge": 0.02}},
    }
    config.update(overrides)
    return config


def test_validate_config_accepts_the_real_project_config() -> None:
    """The live config/model.yaml itself must always pass -- this is the
    file every CLI invocation actually loads."""
    from model_prediction.config import validate_config

    validate_config(load_config())  # raises on failure; no assertion needed


def test_validate_config_rejects_a_typo_in_model_status() -> None:
    """Real bug class this guards against: a typo like status: reserach
    used to surface as a cryptic ValueError('reserach' is not a valid
    ModelState) deep inside a forecast call, or get silently swallowed by a
    broad except (ValueError, KeyError): continue -- see the four cli.py
    forecast loops fixed 2026-08-02. Now caught at config-load time with a
    clear message naming the exact bad field."""
    from model_prediction.config import validate_config

    config = _minimal_valid_config()
    config["models"]["MLB"]["status"] = "reserach"
    try:
        validate_config(config)
    except ValueError as error:
        assert "models.MLB.status" in str(error)
    else:
        raise AssertionError("a typo'd model status was not rejected")


def test_validate_config_rejects_missing_required_sections() -> None:
    from model_prediction.config import validate_config

    try:
        validate_config({})
    except ValueError as error:
        message = str(error)
        assert "'project' section" in message
        assert "'bankroll' section" in message
        assert "'execution' section" in message
        assert "'models' section" in message
    else:
        raise AssertionError("an empty config was not rejected")


def test_validate_config_rejects_non_positive_unit_value() -> None:
    from model_prediction.config import validate_config

    config = _minimal_valid_config()
    config["bankroll"]["unit_value_usd"] = -5.0
    try:
        validate_config(config)
    except ValueError as error:
        assert "bankroll.unit_value_usd" in str(error)
    else:
        raise AssertionError("a negative unit_value_usd was not rejected")


def test_validate_config_ignores_non_sport_model_entries() -> None:
    """shared_features/market_residual/promotion have no status field -- they
    must not be misread as a per-sport model entry with a missing status."""
    from model_prediction.config import validate_config

    config = _minimal_valid_config()
    config["models"]["shared_features"] = {"trend_analysis": {"enabled": True}}
    validate_config(config)  # must not raise


def test_load_config_rejects_a_broken_config_file_on_disk(tmp_path, monkeypatch) -> None:
    """Proves validate_config is actually wired into load_config, not just
    independently correct -- a config file typo must be caught the moment
    any CLI command starts, not deep inside whatever forecast call happens
    to read the bad field first."""
    config_path = tmp_path / "model.yaml"
    broken = _minimal_valid_config()
    broken["models"]["MLB"]["status"] = "reserach"
    config_path.write_text(yaml.safe_dump(broken), encoding="utf-8")
    monkeypatch.setenv("MODEL_PREDICTION_CONFIG", str(config_path))

    try:
        load_config()
    except ValueError as error:
        assert "models.MLB.status" in str(error)
    else:
        raise AssertionError("load_config() accepted a config with an invalid model status")


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


def test_mlb_v8_is_honestly_unqualified_at_its_lowered_threshold() -> None:
    """MLB v8 (2026-08-04) replaced v7's pitcher_era_gap (team-level rolling
    runs-allowed) with starter_era_gap (real per-starter rolling ERA from
    mlb_statsapi boxscore history, features/starter_history.py), via a real
    walk-forward test matching v7's own methodology.

    Threshold history within the same day: first built at
    target_hit_rate=0.65 (0.619665), which DID clear the 60%/50-call bar on
    its own (60.8% holdout hit rate) -- but produced zero real Main-ledger
    calls on its first live day (0/13 games cleared it), since Main's own
    separate confidence gate (cli.py) requires model_probability to clear
    this threshold. Operator directive: lower it to target_hit_rate=0.60
    (validation.py's own existing DIAGNOSTIC_THRESHOLD_TARGET_HIT_RATE, not
    an arbitrary pick) for real coverage -- roughly doubles selectivity and
    holdout volume (148 -> 352 calls, +23.8u -> +41.3u) but the holdout hit
    rate at this looser bar (58.5%) no longer clears 60% on its own either,
    on top of the pre-existing validation Brier regression. This test pins
    that real, honest double-shortfall state so a future re-evaluation
    can't silently read this artifact as qualified for either reason.
    """
    config = load_config()
    artifact_path = PROJECT_ROOT / config["models"]["MLB"]["production_artifact"]
    raw = yaml.safe_load(artifact_path.read_text(encoding="utf-8")) or {}
    # yaml.safe_load also parses JSON (a strict subset); avoids a second import.
    qualification = raw["qualification"]
    assert qualification["qualified"] is False
    assert qualification["meets_primary_holdout_metrics"] is False
    assert qualification["hit_rate"] < 0.60
    assert qualification["hit_rate"] > 0.50  # real, positive signal -- not a coin flip
    assert qualification["validation_brier_score"] > qualification["brier_score"]
    assert len(qualification["failures"]) == 2
    assert any(
        "validation Brier regressed" in failure for failure in qualification["failures"]
    )
    assert any(
        "no longer clears the 60% bar" in failure for failure in qualification["failures"]
    )
    assert "starter_era_gap" in raw["market_models"]["moneyline"]["feature_names"]
    assert "pitcher_era_gap" not in raw["market_models"]["moneyline"]["feature_names"]
