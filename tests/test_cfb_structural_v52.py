"""cfb-structural-v5.2 (quick-261123-2w2): v5 with only the total probability shrunk toward 50%.

Pins: the field absent leaves v5 bit-identical to the pre-change golden outputs; v5.2's moneyline and spread
equal v5's; the shrink formula; the artifact; the serving factory; and the exact-replay contract.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from types import SimpleNamespace

import pytest

from model_prediction.cfb_replay import (
    TOTAL_SLOPE_KEY,
    VERSIONS,
    build_snapshot,
    replay_inputs,
    shrink_total_probabilities,
    validate_decision_snapshot,
)
from model_prediction.config import PROJECT_ROOT
from model_prediction.learned_replay import snapshot_hash
from model_prediction.models.cfb_distribution import CFBJointDistributionEngine
from model_prediction.models.cfb_structural_v5 import (
    CFB_V5_MODEL_VERSION,
    CFB_V52_ARTIFACT_PATH,
    CFB_V52_MODEL_VERSION,
    CFBStructuralV5Model,
    CFBStructuralV52Model,
    load_cfb_structural_v5_artifact,
    load_cfb_structural_v52_artifact,
)
from model_prediction.models.college_football import _SERVING_CLASSES, cfb_model_for_artifact
from model_prediction.production_registry import compute_artifact_hash
from tests.test_cfb_serving_parity import _contract, _event, _run_forecast, _slate

# Captured from unmodified HEAD dab60a87 (before the shrink existed): three markets per game, a half-point
# and an integer line, one model so the second game draws from the advanced RNG stream.
GOLDEN = json.loads(
    (PROJECT_ROOT / "tests" / "fixtures" / "cfb_v5_golden_predictions.json").read_text("utf-8")
)
GAMES = [SimpleNamespace(**g) for g in GOLDEN["games"]]


def _predict(model) -> list:
    return [p for game in GAMES for p in model.predict_matchup([], game)]


def _as_golden(p) -> dict:
    return json.loads(
        json.dumps(
            {
                "event_id": p.event_id,
                "market_type": p.market_type,
                "line": p.line,
                "probabilities": p.probabilities,
                "model_version": p.model_version,
                "rationale": p.rationale,
                "inference_inputs": p.inference_inputs,
            },
            sort_keys=True,
        )
    )


def _v5(**overrides) -> CFBStructuralV5Model:
    return CFBStructuralV5Model.from_artifact(load_cfb_structural_v5_artifact(), **overrides)


def _raw_total(inputs: dict) -> tuple[float, float, float]:
    """The engine's unshrunk total branch for recorded inputs (analytic; the RNG does not enter it)."""
    params = dict(inputs["parameters"])
    engine = CFBJointDistributionEngine(
        margin_sd=params["margin_sd"], total_sd=params["total_sd"], n_simulations=1
    )
    j = engine.compute_market_probabilities(
        mu_home=inputs["mu_home"], mu_away=inputs["mu_away"], total_line=inputs["total_line"]
    )
    return j.p_over, j.p_under, j.p_push_total


def test_field_absent_is_bit_identical_to_the_pre_change_v5_outputs() -> None:
    preds = _predict(_v5())
    assert [_as_golden(p) for p in preds] == GOLDEN["predictions"]
    assert all(TOTAL_SLOPE_KEY not in p.inference_inputs for p in preds)
    assert _v5().total_probability_logit_slope is None


def test_v52_moneyline_and_spread_equal_v5_and_only_the_total_moves() -> None:
    v5 = _predict(_v5())
    v52 = _predict(CFBStructuralV52Model.from_artifact(load_cfb_structural_v52_artifact()))
    assert [p.model_version for p in v52] == [CFB_V52_MODEL_VERSION] * len(v52)
    for a, b in zip(v5, v52, strict=True):
        assert a.market_type == b.market_type
        inputs = dict(b.inference_inputs)
        assert (
            inputs.pop(TOTAL_SLOPE_KEY) == load_cfb_structural_v52_artifact()["total_probability_logit_slope"]
        )
        # Same engine inputs, same RNG state: the shrink never touches the simulation.
        assert inputs == a.inference_inputs
        if a.market_type == "total":
            assert b.probabilities != a.probabilities
            assert b.probabilities["push"] == a.probabilities["push"]
            assert abs(b.probabilities["over"] - 0.5) < abs(a.probabilities["over"] - 0.5)
        else:
            assert b.probabilities == a.probabilities


def test_slope_zero_gives_exactly_half_and_keeps_the_pair_complementary() -> None:
    half_line, integer_line = [
        p for p in _predict(_v5(total_probability_logit_slope=0.0)) if p.market_type == "total"
    ]
    assert half_line.probabilities == {"over": 0.5, "under": 0.5, "push": 0.0}
    assert replay_inputs(half_line.inference_inputs, "total") == half_line.probabilities
    # Integer line: the push mass is the engine's, the decided mass splits evenly.
    over, under, push = (integer_line.probabilities[k] for k in ("over", "under", "push"))
    assert push > 0 and over == under == pytest.approx((1.0 - push) / 2.0, abs=1e-6)
    assert shrink_total_probabilities(0.9, 0.1, 0.0, 0.0) == (0.5, 0.5)
    assert shrink_total_probabilities(1.0, 0.0, 0.0, 0.0) == (0.5, 0.5)


def test_formula_at_a_quarter_slope() -> None:
    s = 0.25
    over, under = shrink_total_probabilities(0.7, 0.3, 0.0, s)
    expected = 1.0 / (1.0 + math.exp(-s * math.log(0.7 / 0.3)))
    assert over == pytest.approx(expected, abs=1e-15)
    assert over + under == 1.0
    preds = [p for p in _predict(_v5(total_probability_logit_slope=s)) if p.market_type == "total"]
    for p in preds:
        raw_over, raw_under, raw_push = _raw_total(p.inference_inputs)
        share = raw_over / (raw_over + raw_under)
        want = (1.0 - raw_push) / (1.0 + math.exp(-s * math.log(share / (1.0 - share))))
        assert p.probabilities["over"] == round(want, 6)
        assert p.probabilities["under"] == round(1.0 - raw_push - want, 6)
    # Slope 1 is the identity (up to the last float bit); s outside [0, 1] fails closed.
    assert shrink_total_probabilities(0.7, 0.3, 0.0, 1.0)[0] == pytest.approx(0.7, abs=1e-12)
    for bad in (-0.1, 1.5, float("nan")):
        with pytest.raises(ValueError, match="invalid_total_slope"):
            shrink_total_probabilities(0.7, 0.3, 0.0, bad)
        with pytest.raises(ValueError, match="invalid_total_slope"):
            _v5(total_probability_logit_slope=bad)


def test_artifact_is_v5_plus_the_slope_and_its_hash_verifies(tmp_path) -> None:
    v5 = load_cfb_structural_v5_artifact()  # the frozen v5 artifact still verifies
    v52 = load_cfb_structural_v52_artifact()
    assert v52["artifact_hash"] == compute_artifact_hash(v52)
    changed = {k for k in set(v5) | set(v52) if v5.get(k) != v52.get(k)}
    assert changed == {
        "artifact_hash",
        "model_version",
        "base_model",
        "qualification",
        "total_probability_logit_slope",
        "total_shrink_fit",
    }
    assert 0.0 <= v52["total_probability_logit_slope"] <= 1.0
    fit = v52["total_shrink_fit"]
    assert fit["base_artifact_hash"] == v5["artifact_hash"]
    fit_output = PROJECT_ROOT / fit["fit_output"]
    assert hashlib.sha256(fit_output.read_bytes()).hexdigest() == fit["fit_output_sha256"]
    report = json.loads(fit_output.read_text(encoding="utf-8"))
    assert report["fit"]["slope"] == v52["total_probability_logit_slope"]
    assert report["split"]["method"] == "complete_date_60_20_20"
    assert report["reproduction_gate"]["pass"] is True
    tampered = {**v52, "total_probability_logit_slope": 0.5}
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="artifact_hash mismatch"):
        load_cfb_structural_v52_artifact(path)
    with pytest.raises(ValueError, match="model_version mismatch"):
        load_cfb_structural_v52_artifact(PROJECT_ROOT / "config" / "models" / "cfb-structural-v5.json")
    assert CFB_V52_ARTIFACT_PATH.name == "cfb-structural-v5.2.json"


def test_v52_builds_through_cfb_model_for_artifact() -> None:
    artifact = load_cfb_structural_v52_artifact()
    model = cfb_model_for_artifact(artifact)
    assert type(model) is CFBStructuralV52Model
    assert _SERVING_CLASSES[CFB_V52_MODEL_VERSION] is CFBStructuralV52Model
    assert (model.version, model.spread_version, model.total_version) == (CFB_V52_MODEL_VERSION,) * 3
    assert model.total_probability_logit_slope == artifact["total_probability_logit_slope"]
    assert type(cfb_model_for_artifact(load_cfb_structural_v5_artifact())) is CFBStructuralV5Model
    without = {k: v for k, v in artifact.items() if k != "total_probability_logit_slope"}
    with pytest.raises(ValueError, match="must set total_probability_logit_slope"):
        CFBStructuralV52Model.from_artifact(without)
    with pytest.raises(ValueError, match="never reads"):
        CFBStructuralV52Model.from_artifact({**artifact, "surprise": 1})
    assert all(CFB_V52_MODEL_VERSION in VERSIONS[m] for m in ("moneyline", "spread", "total"))
    assert CFB_V5_MODEL_VERSION in VERSIONS["total"]


def _row(p, selection: str) -> dict:
    return {
        "event_id": p.event_id,
        "event_start_utc": p.event_start_utc,
        "model_version": p.model_version,
        "market_type": p.market_type,
        "inference_inputs": p.inference_inputs,
        "feature_basis": p.feature_basis,
        "selection": selection,
        "model_probability": p.probabilities[selection],
        "line": -p.line if p.market_type == "spread" and selection == "home" else p.line,
    }


@pytest.mark.parametrize("selection_by_market", [{"moneyline": "home", "spread": "away", "total": "over"}])
def test_v52_rows_replay_exactly_to_the_served_shrunk_probability(selection_by_market) -> None:
    artifact = load_cfb_structural_v52_artifact()
    preds = _predict(cfb_model_for_artifact(artifact))
    for p in preds:
        selection = selection_by_market[p.market_type]
        assert replay_inputs(p.inference_inputs, p.market_type) == p.probabilities
        row = _row(p, selection)
        snap = build_snapshot(
            row, model_hash=artifact["artifact_hash"], observed_at_utc="2026-11-14T12:00:00Z"
        )
        full = {**row, "league": "NCAAF", "model_artifact_hash": artifact["artifact_hash"]}
        assert validate_decision_snapshot(snap, full) == p.probabilities[selection]
        if p.market_type != "total":
            continue
        # Dropping the slope would replay the raw v5 probability: a v5.2 row can never pass as unshrunk.
        stripped = copy.deepcopy(snap)
        stripped["inference_inputs"].pop(TOTAL_SLOPE_KEY)
        stripped["snapshot_hash"] = snapshot_hash(stripped)
        with pytest.raises(ValueError, match="probability_mismatch"):
            validate_decision_snapshot(stripped, full)
        damaged = copy.deepcopy(snap)
        damaged["inference_inputs"][TOTAL_SLOPE_KEY] = 2.0
        damaged["snapshot_hash"] = snapshot_hash(damaged)
        with pytest.raises(ValueError, match="invalid_total_slope"):
            validate_decision_snapshot(damaged, full)


def test_v52_slate_contracts_and_candidate_inputs_carry_the_shrink(tmp_path) -> None:
    artifact = load_cfb_structural_v52_artifact()
    result = _slate(tmp_path, _event(), model=cfb_model_for_artifact(artifact))
    contracts = result["priced_contracts"]
    assert contracts and {c["model_version"] for c in contracts} == {CFB_V52_MODEL_VERSION}
    totals = [c for c in contracts if c["market_type"] == "total"]
    assert totals
    for c in contracts:
        assert c["inference_inputs"][TOTAL_SLOPE_KEY] == artifact["total_probability_logit_slope"]
        snap = build_snapshot(c, model_hash=artifact["artifact_hash"], observed_at_utc=c["observed_at_utc"])
        assert snap["probabilities"][c["selection"]] == pytest.approx(c["model_probability"], abs=2e-6)
    for c in totals:
        assert abs(c["model_probability"] - 0.5) < 0.01


def test_v52_is_a_main_eligible_ncaaf_version(tmp_path, monkeypatch) -> None:
    contract = _contract("total", "over", 48.5, model_version=CFB_V52_MODEL_VERSION)
    _result, _flat, main = _run_forecast(
        tmp_path, monkeypatch, [contract], model_cfg={"active_production_version": CFB_V52_MODEL_VERSION}
    )
    [row] = main.rows()
    assert row["model_version"] == CFB_V52_MODEL_VERSION
    assert row["model_artifact_hash"] == load_cfb_structural_v52_artifact()["artifact_hash"]
    outsider = _contract("total", "over", 48.5, model_version="cfb-structural-v5.1")
    _result, _flat, main = _run_forecast(tmp_path, monkeypatch, [outsider], run="outsider")
    assert main.rows() == []
