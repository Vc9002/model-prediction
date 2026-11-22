"""Pin which keys the shipped v9 artifacts leave unread or default (261121-xbn, containment only).

``NBAStructuralV9Engine.__init__`` swallows unknown keywords, so a retuned or misspelled artifact
key is silently ignored and an omitted one silently takes the code default. Several shipped
artifacts rely on both. These tests do not judge that; they make any drift (a new key, a renamed
key, a changed default) fail loudly. Honouring an unread key, editing an artifact, or changing a
default alters served numbers and the artifact hash, so each needs a new version via
``model_promotion``.
"""

from __future__ import annotations

import inspect
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from model_prediction.models.nba_v9 import (
    NBAMoneylineModelV9,
    NBASpreadModelV9,
    NBAStructuralV9Engine,
    NBATotalModelV9,
    _warn_on_artifact_key_drift,
    artifact_key_drift,
)

ROOT = Path(__file__).resolve().parent.parent
ACCEPTED = [
    name
    for name, param in inspect.signature(NBAStructuralV9Engine.__init__).parameters.items()
    if name != "self" and param.kind is not inspect.Parameter.VAR_KEYWORD
]

_VERSIONS = {
    "moneyline_version": "nba-moneyline-v9",
    "spread_version": "nba-spread-margin-v9",
    "total_version": "nba-total-margin-v9",
}

# model name -> (wrapper class, unread keys, defaulted key -> resolved engine value)
SHIPPED: dict[str, tuple[type, list[str], dict[str, Any]]] = {
    "nba-spread-margin-v9": (
        NBASpreadModelV9,
        [
            "artifact_hash",
            "cdf_family",
            "coherent_moneyline",
            "dynamic_matchup_pace",
            "expected_margin_detail",
            "expected_margin_formula",
            "expected_margin_source",
            "league",
            "method",
            "model_version",
            "note",
            "positive_class",
            "possession_model",
            "prediction_formula",
            "qualification",
            "schema_version",
            "sport",
            "use_logistic_moneyline",
        ],
        {"league_pace": 99.2, **_VERSIONS},
    ),
    "nba-total-margin-v9": (
        NBATotalModelV9,
        [
            "artifact_hash",
            "cdf_family",
            "dynamic_matchup_pace",
            "expected_total_detail",
            "expected_total_formula",
            "expected_total_source",
            "league",
            "method",
            "model_version",
            "note",
            "positive_class",
            "possession_model",
            "prediction_formula",
            "qualification",
            "schema_version",
            "sport",
            "team_total_weight",
            "trend_total_weight",
        ],
        {
            "b2b_penalty_away": 1.5,
            "b2b_penalty_home": 2.0,
            "elo_home_advantage": 38.0,
            "elo_weight": 0.7,
            "home_court_points": 1.75,
            "league_pace": 99.2,
            "logistic_scale": 11.8,
            "margin_sd": 14.0,
            "market_blend_weight": 0.0,
            "moneyline_margin_weight": 0.3,
            "rest_weight": 0.15,
            "trend_weight": 0.3,
            **_VERSIONS,
        },
    ),
    "nba-moneyline-v9": (
        NBAMoneylineModelV9,
        [
            "artifact_hash",
            "feature_schema_version",
            "market_models",
            "method",
            "model_version",
            "qualification",
            "sport",
        ],
        {
            "b2b_pace_drag": 0.8,
            "b2b_penalty_away": 1.5,
            "b2b_penalty_home": 2.0,
            "baseline_points_per_team": 113.8,
            "dynamic_volatility": False,
            "elo_home_advantage": 38.0,
            "elo_weight": 0.7,
            "half_pace_additive": True,
            "home_court_points": 1.75,
            "isolate_postseason": True,
            "league_pace": 99.2,
            "logistic_scale": 11.8,
            "margin_sd": 14.0,
            "market_blend_weight": 0.0,
            "moneyline_margin_weight": 0.3,
            "offseason_gap_days": 60.0,
            "offseason_retention": 0.4,
            "overtime_risk_adjustment": True,
            "pace_sd_scaling": False,
            "rest_weight": 0.15,
            "sample_scaled_overtime": True,
            "schedule_density_drag": True,
            "total_sd": 18.0,
            "trend_weight": 0.3,
            **_VERSIONS,
        },
    ),
}


def _load(name: str) -> dict[str, Any]:
    return json.loads((ROOT / "config/models" / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(SHIPPED))
def test_shipped_v9_artifact_unread_and_defaulted_keys_are_pinned(name: str) -> None:
    _wrapper, expected_unread, expected_defaults = SHIPPED[name]
    unread, defaulted = artifact_key_drift(_load(name), ACCEPTED)
    assert unread == sorted(expected_unread)
    assert defaulted == sorted(expected_defaults)


@pytest.mark.parametrize("name", sorted(SHIPPED))
def test_shipped_v9_artifact_defaulted_values_are_pinned(name: str) -> None:
    wrapper, _unread, expected_defaults = SHIPPED[name]
    engine = wrapper.from_artifact(_load(name)).engine
    for key, expected in expected_defaults.items():
        assert getattr(engine, key) == pytest.approx(expected), key
        assert type(getattr(engine, key)) is type(expected), key


@pytest.mark.parametrize("name", sorted(SHIPPED))
def test_wrapper_and_engine_from_artifact_agree_on_every_shipped_artifact(name: str) -> None:
    wrapper, _unread, _defaults = SHIPPED[name]
    artifact = _load(name)
    via_wrapper = vars(wrapper.from_artifact(artifact).engine)
    via_engine = vars(NBAStructuralV9Engine.from_artifact(artifact))
    assert via_wrapper == via_engine


def test_helper_flags_a_misspelled_key_and_leaves_the_constructor_input_alone() -> None:
    artifact = {"margn_sd": 15.0, "total_sd": 19.0}
    unread, defaulted = artifact_key_drift(artifact, ACCEPTED)
    assert unread == ["margn_sd"]
    assert "margin_sd" in defaulted
    assert "total_sd" not in defaulted
    engine = NBASpreadModelV9.from_artifact(artifact).engine
    assert engine.margin_sd == 14.0  # the misspelt override really is ignored: that is the hazard
    assert engine.total_sd == 19.0
    assert artifact == {"margn_sd": 15.0, "total_sd": 19.0}


def test_helper_is_silent_on_a_complete_artifact() -> None:
    full = {name: param.default for name, param in _signature_params().items() if name in ACCEPTED}
    full["moneyline_margin_weight"] = 0.3
    assert artifact_key_drift(full, ACCEPTED) == ([], [])


def _signature_params() -> dict[str, inspect.Parameter]:
    return dict(inspect.signature(NBAStructuralV9Engine.__init__).parameters)


def test_from_artifact_logs_one_info_line_naming_both_sets(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="model_prediction.models.nba_v9"):
        NBATotalModelV9.from_artifact(_load("nba-total-margin-v9"))
    records = [r for r in caplog.records if r.name == "model_prediction.models.nba_v9"]
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
    message = records[0].getMessage()
    assert "nba-total-margin-v9" in message
    assert "trend_total_weight" in message
    assert "margin_sd" in message


def test_from_artifact_does_not_warn_on_a_complete_artifact(caplog: pytest.LogCaptureFixture) -> None:
    full = {name: param.default for name, param in _signature_params().items() if name in ACCEPTED}
    full["moneyline_margin_weight"] = 0.3
    with caplog.at_level(logging.INFO, logger="model_prediction.models.nba_v9"):
        NBASpreadModelV9.from_artifact(full)
    assert not [r for r in caplog.records if r.name == "model_prediction.models.nba_v9"]


def test_warning_helper_never_raises() -> None:
    _warn_on_artifact_key_drift(None, "nba-spread-margin-v9")  # type: ignore[arg-type]
