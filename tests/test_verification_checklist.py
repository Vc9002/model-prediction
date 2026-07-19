from model_prediction.experiment_design import ExperimentLog
from model_prediction.feature_contract import FeatureObservation
from model_prediction.verification_checklist import CHECKLIST, format_checklist, run_checklist


def test_checklist_has_thirteen_items_with_valid_kinds() -> None:
    assert len(CHECKLIST) == 13
    assert all(item.kind in {"auto", "manual"} for item in CHECKLIST)


def test_run_checklist_with_no_evidence_leaves_everything_not_checked() -> None:
    results = {r.item_id: r.status for r in run_checklist()}
    assert all(status == "not_checked" for status in results.values())


def test_run_checklist_source_keys_pass_for_free_sources() -> None:
    results = {r.item_id: r for r in run_checklist(source_keys=["espn", "polymarket_us"])}
    assert results["no_signup_default"].status == "pass"


def test_run_checklist_source_keys_fail_for_excluded_source() -> None:
    results = {r.item_id: r for r in run_checklist(source_keys=["liquipedia"])}
    assert results["no_signup_default"].status == "fail"


def test_run_checklist_observation_metadata_pass() -> None:
    obs = FeatureObservation(
        event_id="1", entity_id="e", feature_name="f", value=1.0,
        effective_at_utc="2026-07-18T19:00:00Z", observed_at_utc="2026-07-18T18:00:00Z",
        source="espn", source_version="v1",
    )
    results = {r.item_id: r for r in run_checklist(observations=[obs])}
    assert results["observation_metadata"].status == "pass"
    assert results["no_future_leakage"].status == "pass"


def test_run_checklist_experiment_log_flags_repeated_trials(tmp_path) -> None:
    log = ExperimentLog(tmp_path / "mlb.jsonl")
    log.record("v1", "mlb", {})
    results = {r.item_id: r for r in run_checklist(experiment_log=log, sport="mlb")}
    assert results["final_test_unused_for_selection"].status == "pass"

    log.record("v2", "mlb", {})
    results2 = {r.item_id: r for r in run_checklist(experiment_log=log, sport="mlb")}
    assert results2["final_test_unused_for_selection"].status == "fail"


def test_run_checklist_ablation_baseline_presence() -> None:
    with_baseline = {r.item_id: r for r in run_checklist(ablation_variant_names=["incumbent", "+ feature A"])}
    assert with_baseline["ablations_include_baseline"].status == "pass"

    without_baseline = {r.item_id: r for r in run_checklist(ablation_variant_names=["+ feature A", "+ feature B"])}
    assert without_baseline["ablations_include_baseline"].status == "fail"


def test_run_checklist_predictive_metrics() -> None:
    ok = {r.item_id: r for r in run_checklist(predictive_metrics={"status": "ok", "brier_score": 0.2})}
    assert ok["proper_scores_reported"].status == "pass"

    insufficient = {r.item_id: r for r in run_checklist(predictive_metrics={"status": "insufficient_sample"})}
    assert insufficient["proper_scores_reported"].status == "fail"


def test_run_checklist_artifact_hashes() -> None:
    good_hash = "a" * 64
    ok = {r.item_id: r for r in run_checklist(artifact_hashes=[good_hash])}
    assert ok["artifacts_reproducible"].status == "pass"

    bad = {r.item_id: r for r in run_checklist(artifact_hashes=["not-a-hash"])}
    assert bad["artifacts_reproducible"].status == "fail"


def test_run_checklist_zero_units() -> None:
    ok = {r.item_id: r for r in run_checklist(research_units=[0.0, 0.0])}
    assert ok["zero_unit_until_promotion"].status == "pass"

    bad = {r.item_id: r for r in run_checklist(research_units=[0.0, 1.5])}
    assert bad["zero_unit_until_promotion"].status == "fail"


def test_format_checklist_renders_markers_and_details() -> None:
    results = run_checklist(source_keys=["espn"])
    text = format_checklist(results)
    assert "[x]" in text
    assert "The default path requires no new account" in text
