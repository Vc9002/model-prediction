"""cfb-structural-v5 challenger: fitted layers, fail-closed artifact loading, replay round-trip."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from model_prediction.cfb_replay import replay_inputs
from model_prediction.config import PROJECT_ROOT
from model_prediction.models.cfb_structural_v4 import CFBStructuralV4Model
from model_prediction.models.cfb_structural_v5 import (
    CFB_V5_ARTIFACT_PATH,
    CFB_V5_MODEL_VERSION,
    CFBStructuralV5Model,
    load_cfb_structural_v5_artifact,
)
from model_prediction.production_registry import ProductionModelRegistry

PARAMS = {
    "margin_calibration": {"intercept": 0.8, "slope": 1.1},
    "total_calibration": {"intercept": -1.0, "slope": 1.02},
    "margin_sd_table": {"established": 16.8, "thin_sample": 17.5, "fbs_vs_fcs": 19.0},
    "established_min_sample": 4,
    "conference_margin_scale": 0.98,
    "total_sd": 16.9,
}
GAME = SimpleNamespace(
    event_id="v5-test",
    event_start_utc="2026-11-14T20:00:00Z",
    away_team="Duke Blue Devils",
    home_team="Miami Hurricanes",
    spread_home_line=-3.5,
    total_line=50.5,
)


def _model(**overrides) -> CFBStructuralV5Model:
    return CFBStructuralV5Model(**{**PARAMS, **overrides})


def test_mean_calibration_and_sd_table_are_applied_not_the_v4_constants() -> None:
    model = _model()
    margin, total = model.calibrate_means(10.0, 50.0)
    assert margin == pytest.approx(0.8 + 1.1 * 10.0)
    assert total == pytest.approx(-1.0 + 1.02 * 50.0)
    assert (
        model.compute_margin_sd(
            spread_home_line=-3.5, is_fbs_vs_fcs=False, sample_games=8, is_conference_matchup=False
        )
        == 16.8
    )
    assert model.compute_margin_sd(
        spread_home_line=-3.5, is_fbs_vs_fcs=False, sample_games=8, is_conference_matchup=True
    ) == pytest.approx(16.8 * 0.98, abs=1e-3)
    assert (
        model.compute_margin_sd(
            spread_home_line=-3.5, is_fbs_vs_fcs=False, sample_games=2, is_conference_matchup=True
        )
        == 17.5
    )
    assert (
        model.compute_margin_sd(
            spread_home_line=-3.5, is_fbs_vs_fcs=True, sample_games=9, is_conference_matchup=True
        )
        == 19.0
    )
    # Blowout-line widening is carried over from v4 unchanged.
    assert model.compute_margin_sd(
        spread_home_line=-31.5, is_fbs_vs_fcs=False, sample_games=8, is_conference_matchup=False
    ) == pytest.approx(16.8 * 1.25, abs=1e-3)
    # Totals: no uncertainty widening, deviation widening kept.
    assert model.compute_total_sd(projected_total=50.0, total_line=52.0) == 16.9
    assert model.compute_total_sd(projected_total=70.0, total_line=50.0) > 16.9
    # The v4-named helpers route to the fitted versions (no path prices with v4 SDs).
    assert model.compute_effective_margin_sd(-3.5, 0.30, False) == 16.8
    assert model.compute_effective_total_sd(50.0, 52.0, 0.30) == 16.9


def test_forecast_uses_calibrated_means_and_replays_exactly() -> None:
    model = _model()
    v4 = CFBStructuralV4Model()
    feat = model.extractor.extract_features(
        history=[],
        away_team=GAME.away_team,
        home_team=GAME.home_team,
        event_id="x",
        game_start_utc=GAME.event_start_utc,
        season_year=2026,
        week=12,
    )
    fc = model.forecast_game(feat, spread_home_line=-3.5, total_line=50.5)
    margin, total = model.calibrate_means(feat.projected_margin_home, feat.projected_total)
    assert fc.inference_inputs["mu_home"] - fc.inference_inputs["mu_away"] == pytest.approx(margin, abs=1e-6)
    assert fc.inference_inputs["mu_home"] + fc.inference_inputs["mu_away"] == pytest.approx(total, abs=1e-6)
    assert fc.effective_margin_sd != v4.compute_effective_margin_sd(
        -3.5, feat.uncertainty, feat.is_conference_matchup
    )
    preds = model.predict_matchup([], GAME)
    assert [p.model_version for p in preds] == [CFB_V5_MODEL_VERSION] * 3
    for p in preds:
        assert replay_inputs(p.inference_inputs, p.market_type) == p.probabilities
    second = model.predict_matchup([], GAME)
    assert second[0].inference_inputs["rng_state_before"] != preds[0].inference_inputs["rng_state_before"]


def test_constructor_fails_closed_on_bad_parameters() -> None:
    with pytest.raises(ValueError, match="missing slices"):
        _model(margin_sd_table={"established": 16.8})
    with pytest.raises(ValueError, match="positive slope"):
        _model(margin_calibration={"intercept": 0.0, "slope": 0.0})
    with pytest.raises(ValueError, match="conference_margin_scale"):
        _model(conference_margin_scale=0.2)


class _Recording(dict):
    def __init__(self, payload):
        super().__init__(payload)
        self.seen: set[str] = set()

    def __getitem__(self, key):
        self.seen.add(key)
        return super().__getitem__(key)

    def get(self, key, default=None):  # type: ignore[override]
        self.seen.add(key)
        return super().get(key, default)

    def __contains__(self, key) -> bool:
        self.seen.add(key)
        return super().__contains__(key)


@pytest.mark.skipif(
    not CFB_V5_ARTIFACT_PATH.exists(), reason="v5 artifact not yet produced by scripts/cfb_v5_research.py"
)
def test_artifact_loads_fail_closed_consumes_every_parameter_and_is_not_a_champion() -> None:
    artifact = load_cfb_structural_v5_artifact()
    recorder = _Recording(artifact)
    model = CFBStructuralV5Model.from_artifact(recorder)
    unread = (
        set(artifact)
        - recorder.seen
        - {
            "artifact_hash",
            "model_version",
            "method",
            "note",
            "league",
            "sport",
            "schema_version",
            "positive_class",
            "qualification",
            "calibration",
            "training",
            "base_model",
        }
    )
    assert not unread, sorted(unread)
    assert model.margin_sd_table["established"] == artifact["margin_sd_table"]["established"]
    assert (
        artifact["qualification"]["status"] == "research" and artifact["qualification"]["qualified"] is False
    )
    assert artifact["training"]["fitted_on"].startswith("train only")
    with pytest.raises(ValueError, match="never reads"):
        CFBStructuralV5Model.from_artifact({**artifact, "surprise": 1})
    reg = ProductionModelRegistry.load(PROJECT_ROOT)
    for market in ("moneyline", "spread", "total"):
        assert reg.champion("NCAAF", market).model_id == "cfb-structural-v4"
    tampered = dict(artifact)
    tampered["total_sd"] = 99.0
    tmp = PROJECT_ROOT / "outputs" / "_tampered_cfb_v5.json"
    tmp.write_text(json.dumps(tampered), encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="artifact_hash mismatch"):
            load_cfb_structural_v5_artifact(tmp)
    finally:
        tmp.unlink(missing_ok=True)
