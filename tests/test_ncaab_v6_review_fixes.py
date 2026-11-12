"""Regression tests for the 2026-11-12 NCAAB v6 review fixes (docs/MODEL_REVIEW_2026-11-11.md).

Each test names the finding it pins. The artifact / serving-parity tests at the bottom are the
review's cross-cutting gaps #1 and #4 applied to the NCAAB champions.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from scipy import stats

from model_prediction import college_basketball_forward
from model_prediction.features.base import GameRecord
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.ncaab_spread_v6 import ARTIFACT_PATH as SPREAD_ARTIFACT
from model_prediction.models.ncaab_spread_v6 import (
    NCAABSpreadModelV6,
    artifact_hash,
    load_verified_artifact,
    ncaab_spread_v6_model,
)
from model_prediction.models.ncaab_total_v6 import ARTIFACT_PATH as TOTAL_ARTIFACT
from model_prediction.models.ncaab_total_v6 import NCAABTotalModelV6, ncaab_total_v6_model
from model_prediction.production_registry import compute_artifact_hash

REPO = Path(__file__).resolve().parents[1]


def _history(n: int = 12, away_score: int = 70, home_score: int = 78) -> list[GameRecord]:
    now = datetime.now(UTC)
    return [
        GameRecord(
            str(i), (now - timedelta(days=i + 2)).isoformat(), "NCAAB", "Away", "Home", away_score, home_score
        )
        for i in range(n)
    ]


def _game(**kwargs) -> UpcomingGame:
    return UpcomingGame("g", datetime.now(UTC).isoformat(), "Away", "Home", **kwargs)


def _only(preds, market):
    return next(p for p in preds if p.market_type == market)


def test_coherent_moneyline_is_read_off_the_margin_distribution():
    """#10: with the fix on, P(home) is the Student-t mass above zero of the same distribution the
    spread uses; the legacy base-10 logistic gives a materially different number."""
    history = _history()
    coherent = NCAABSpreadModelV6(coherent_moneyline=True, elo_weight=0.0, trend_weight=1.0)
    legacy = NCAABSpreadModelV6(coherent_moneyline=False, elo_weight=0.0, trend_weight=1.0)
    ml = _only(coherent.predict_spreads(history, [_game()]), "moneyline")
    mean, scale = ml.inference_inputs["mean"], ml.inference_inputs["scale"]
    expected = 1.0 - float(stats.t.cdf((0.5 - mean) / scale, df=8.0))
    # inference_inputs are rounded (mean 0.01, scale 1e-4), which moves the probability by ~5e-4 at most.
    assert ml.probabilities["home"] == pytest.approx(expected, abs=1e-3)
    legacy_ml = _only(legacy.predict_spreads(history, [_game()]), "moneyline")
    assert abs(legacy_ml.probabilities["home"] - ml.probabilities["home"]) > 0.02


def test_student_t_scale_matched_distribution_has_sd_equal_to_margin_sd():
    """#10: t(df=8) with scale s has SD s*sqrt(df/(df-2)); matched mode picks s so the SD is margin_sd."""
    matched = NCAABSpreadModelV6(student_t_scale_matches_sd=True, dynamic_volatility=False)
    unmatched = NCAABSpreadModelV6(student_t_scale_matches_sd=False, dynamic_volatility=False)
    pm = _only(matched.predict_spreads(_history(), [_game(spread_away_line=4.5)]), "spread")
    pu = _only(unmatched.predict_spreads(_history(), [_game(spread_away_line=4.5)]), "spread")
    assert pm.inference_inputs["scale"] * (8.0 / 6.0) ** 0.5 == pytest.approx(10.5, abs=1e-9)
    assert pu.inference_inputs["scale"] == 10.5  # legacy: scale == sd, so the true SD was 12.1
    assert pm.inference_inputs["sd"] == pu.inference_inputs["sd"] == 10.5


def test_integer_spread_and_total_lines_exclude_push_mass():
    spread = NCAABSpreadModelV6()
    total = NCAABTotalModelV6()
    history = _history()
    whole = _only(spread.predict_spreads(history, [_game(spread_away_line=7.0)]), "spread")
    half = _only(spread.predict_spreads(history, [_game(spread_away_line=7.5)]), "spread")
    assert whole.probabilities["push"] > 0.0
    assert whole.probabilities["home"] + whole.probabilities["away"] + whole.probabilities[
        "push"
    ] == pytest.approx(1.0, abs=1e-5)
    assert half.probabilities["push"] == 0.0
    assert half.probabilities["home"] + half.probabilities["away"] == pytest.approx(1.0, abs=1e-5)
    # Away covers +7 only on home margins <= 6; the exact-7 band is the push, so it is smaller than +7.5.
    assert whole.probabilities["away"] < half.probabilities["away"]
    t_whole = total.predict_totals(history, [_game(total_line=148.0)])[0]
    t_half = total.predict_totals(history, [_game(total_line=148.5)])[0]
    assert t_whole.probabilities["push"] > 0.0
    assert t_half.probabilities["push"] == 0.0
    assert t_whole.probabilities["over"] + t_whole.probabilities["under"] + t_whole.probabilities[
        "push"
    ] == pytest.approx(1.0, abs=1e-5)


def test_elo_weight_ramp_leans_on_elo_until_the_sample_exists():
    """#11: at zero current-season games the trend half gets no weight; once the ramp length is
    played the configured elo_weight is back and the control margin is reproduced exactly."""
    ramp = NCAABSpreadModelV6(elo_weight_ramp_games=8.0, rest_weight=0.0)
    opener = _only(ramp.predict_spreads([], [_game()]), "moneyline")
    assert opener.feature_basis["elo_weight_effective"] == 1.0
    assert opener.feature_basis["expected_margin"] == pytest.approx(
        opener.feature_basis["elo_margin"], abs=0.011
    )
    mature = _only(ramp.predict_spreads(_history(12), [_game()]), "moneyline")
    assert mature.feature_basis["current_season_sample"] >= 8
    assert mature.feature_basis["elo_weight_effective"] == 0.5
    control = _only(NCAABSpreadModelV6(rest_weight=0.0).predict_spreads(_history(12), [_game()]), "moneyline")
    assert mature.feature_basis["expected_margin"] == control.feature_basis["expected_margin"]


def test_tiered_trend_priors_and_additive_matchup_resist_early_season_compression():
    """#11: a Tier-1 host against a Tier-5 visitor with no current-season games must not be
    projected as HCA-only by the trend half."""
    away, home = "Grambling Tigers", "Alabama Crimson Tide"  # SWAC 1270 vs SEC 1680 tier priors
    game = UpcomingGame("g", datetime.now(UTC).isoformat(), away, home)
    plain = _only(NCAABSpreadModelV6().predict_spreads([], [game]), "moneyline")
    priors = _only(NCAABSpreadModelV6(tiered_trend_priors=True).predict_spreads([], [game]), "moneyline")
    both = _only(
        NCAABSpreadModelV6(tiered_trend_priors=True, additive_matchup=True).predict_spreads([], [game]),
        "moneyline",
    )
    assert plain.feature_basis["trend_margin"] == pytest.approx(7.2)  # HCA only: no strength signal at all
    assert priors.feature_basis["trend_margin"] > plain.feature_basis["trend_margin"] + 5.0
    # Additive matchup recovers the full Elo-implied gap ((1680-1270)/28) instead of half of it.
    assert both.feature_basis["trend_margin"] == pytest.approx(7.2 + (1680 - 1270) / 28.0, abs=0.05)


def test_total_conference_priors_shrink_the_label_only_pace_gap():
    """#7: two pairs of teams with identical observed scoring (85-85, three games each) were projected
    far apart in pace purely because one pair is labelled SEC and the other Northeast: the level was
    shrunk toward the league mean and then divided by the conference baseline. Shrinking toward the
    conference baseline leaves only the legitimate prior difference."""
    now = datetime.now(UTC)
    filler = [
        GameRecord(
            f"f{i}",
            (now - timedelta(days=i + 2)).isoformat(),
            "NCAAB",
            f"Filler A{i}",
            f"Filler B{i}",
            76,
            76,
        )
        for i in range(60)
    ]

    def hist(a: str, h: str) -> list[GameRecord]:
        return filler + [
            GameRecord(f"{a}{i}", (now - timedelta(days=i + 2)).isoformat(), "NCAAB", a, h, 85, 85)
            for i in range(3)
        ]

    sec = ("Alabama Crimson Tide", "Arkansas Razorbacks")
    nec = ("Wagner Seahawks", "LIU Sharks")
    legacy = NCAABTotalModelV6()
    fixed = NCAABTotalModelV6(conference_trend_priors=True, pace_baseline="league")

    def pace(model, teams):
        return model.predict_totals(hist(*teams), [UpcomingGame("x", now.isoformat(), *teams)])[
            0
        ].feature_basis["expected_pace"]

    # The defect: the Northeast pair comes out *faster* than the SEC pair on identical scoring.
    assert pace(legacy, nec) > pace(legacy, sec) + 4.0
    # Fixed: the prior says the SEC pair is the faster one, and equal data cannot flip it.
    assert pace(fixed, sec) >= pace(fixed, nec)


def test_league_total_weight_is_explicit_and_pace_baseline_is_validated():
    a = (
        NCAABTotalModelV6(league_total_weight=0.15)
        .predict_totals(_history(), [_game()])[0]
        .inference_inputs["mean"]
    )
    b = NCAABTotalModelV6().predict_totals(_history(), [_game()])[0].inference_inputs["mean"]
    assert a != b
    with pytest.raises(ValueError):
        NCAABTotalModelV6(pace_baseline="nonsense")


# --------------------------------------------------------------------------- artifact contracts


class _RecordingArtifact(dict):
    def __init__(self, payload):
        super().__init__(payload)
        self.seen: set[str] = set()

    def get(self, key, default=None):  # type: ignore[override]
        self.seen.add(key)
        return super().get(key, default)


_ARTIFACT_METADATA_KEYS = {
    "artifact_hash",
    "model_id",
    "model_version",
    "implementation_type",
    "schema_version",
    "sport",
    "status",
    "target",
    "trained_through_date",
    "markets",
    "qualified_for_betting",
    "evidence",
}


@pytest.mark.parametrize(
    ("path", "loader"),
    [(SPREAD_ARTIFACT, NCAABSpreadModelV6.from_artifact), (TOTAL_ARTIFACT, NCAABTotalModelV6.from_artifact)],
)
def test_every_ncaab_v6_artifact_parameter_is_consumed_by_the_served_model(path, loader):
    """#9: a key nothing reads is a false claim to anyone auditing the artifact."""
    artifact = json.loads(path.read_text(encoding="utf-8"))
    recorder = _RecordingArtifact(artifact)
    loader(recorder)
    unread = set(artifact) - _ARTIFACT_METADATA_KEYS - recorder.seen
    assert not unread, f"{path.name} carries parameters the served model never reads: {sorted(unread)}"


@pytest.mark.parametrize("path", [SPREAD_ARTIFACT, TOTAL_ARTIFACT])
def test_ncaab_v6_artifact_hashes_verify(path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert artifact_hash(payload) == compute_artifact_hash(payload) == payload["artifact_hash"]
    load_verified_artifact(path)


def test_tampered_artifact_is_refused(tmp_path):
    payload = json.loads(SPREAD_ARTIFACT.read_text(encoding="utf-8"))
    payload["margin_sd"] = 99.0
    bad = tmp_path / "ncaab-spread-v6.json"
    bad.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="artifact_hash"):
        load_verified_artifact(bad)


def test_serving_entry_point_parameters_equal_the_artifact():
    """Cross-cutting gap #1: import what production actually calls and assert its parameters are
    the artifact's (would have caught #9 here and the NCAAF class split in #1)."""
    assert college_basketball_forward.ncaab_spread_model is ncaab_spread_v6_model
    assert college_basketball_forward.ncaab_total_model is ncaab_total_v6_model
    spread_art = json.loads(SPREAD_ARTIFACT.read_text(encoding="utf-8"))
    total_art = json.loads(TOTAL_ARTIFACT.read_text(encoding="utf-8"))
    spread = college_basketball_forward.ncaab_spread_model()
    total = college_basketball_forward.ncaab_total_model()
    for key in (
        "margin_sd",
        "elo_weight",
        "trend_weight",
        "offseason_retention",
        "logistic_scale",
        "student_t_df",
        "coherent_moneyline",
        "student_t_scale_matches_sd",
        "tiered_trend_priors",
        "additive_matchup",
        "elo_weight_ramp_games",
        "max_edge",
    ):
        assert getattr(spread, key) == spread_art[key], key
    assert spread.version == spread_art["model_version"] == "ncaab-spread-v6"
    for key in (
        "total_sd",
        "offseason_retention",
        "trend_total_weight",
        "league_total_weight",
        "pace_baseline",
        "conference_trend_priors",
        "max_edge",
    ):
        assert getattr(total, key) == total_art[key], key
    assert total.version == total_art["model_version"] == "ncaab-total-v6"
    # Sizing comes from the artifact, not a hard-coded 0.08 in the forward path.
    assert (
        spread.compute_dynamic_units(spread.max_edge, min_edge=0.035)
        == spread_art["dynamic_sizing"]["max_units"]
    )
