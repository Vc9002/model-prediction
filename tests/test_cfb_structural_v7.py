"""cfb-structural-v7 research challenger: artifact integrity, train/serve parity, point in time, fail-closed."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from model_prediction.config import PROJECT_ROOT
from model_prediction.features import cfb_features_v7 as core
from model_prediction.models.cfb_structural_v7 import (
    CFB_V7_MODEL_VERSION,
    NO_CALL_NO_CLASS,
    NO_CALL_PBP_MISSING,
    CFBStructuralV7Model,
    check_pbp_complete,
    load_cfb_structural_v7_artifact,
    price_game,
)
from model_prediction.production_registry import compute_artifact_hash

FIXTURE = PROJECT_ROOT / "outputs" / "ncaaf" / "cfb_v7_parity_fixture.json"
SPEC = PROJECT_ROOT / "outputs" / "ncaaf" / "cfb_v7_spec.json"


@pytest.fixture(scope="module")
def model() -> CFBStructuralV7Model:
    return CFBStructuralV7Model(load_cfb_structural_v7_artifact())


@pytest.fixture(scope="module")
def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rows(fixture: dict) -> list[core.TeamGame]:
    out = [core.team_game_from_row(r) for r in fixture["rows"]]
    return sorted(out, key=lambda r: (r.kickoff, r.event_id, -r.home_sign))


def test_artifact_is_hash_verified_and_matches_the_spec() -> None:
    art = load_cfb_structural_v7_artifact()
    assert art["model_version"] == CFB_V7_MODEL_VERSION
    assert art["artifact_hash"] == compute_artifact_hash(art)
    assert art["training"]["spec_sha256"] == hashlib.sha256(SPEC.read_bytes()).hexdigest()
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    assert art["stage2"] == spec["stage2"]
    assert art["margin_distribution"]["key_log_multipliers"] == spec["margin_pmf"]["theta"]
    assert art["qualification"]["status"] == "challenger"
    assert art["qualification"]["qualified"] is False


def test_tampered_artifact_fails_closed(tmp_path: Path) -> None:
    art = load_cfb_structural_v7_artifact()
    art["stage2"]["intercept"] += 1.0
    bad = tmp_path / "a.json"
    bad.write_text(json.dumps(art), encoding="utf-8")
    with pytest.raises(ValueError, match="artifact_hash mismatch"):
        load_cfb_structural_v7_artifact(bad)


def test_serving_reproduces_the_fit_script_parity_fixture(
    model: CFBStructuralV7Model, fixture: dict, rows: list[core.TeamGame]
) -> None:
    assert fixture["spec_sha256"] == load_cfb_structural_v7_artifact()["training"]["spec_sha256"]
    assert len(fixture["cases"]) >= 10
    for case in fixture["cases"]:
        got = price_game(
            model,
            rows,
            season=fixture["season"],
            home_id=case["home_id"],
            away_id=case["away_id"],
            neutral=case["neutral"],
            decision=datetime.fromisoformat(case["decision_utc"]),
            covariates=fixture["covariates"] or None,
            classes=core.season_classes(rows),
            spread_home_line=case["spread_home_line"],
            total_line=case["total_line"],
        )
        exp = case["expected"]
        assert got.mu_margin == pytest.approx(exp["mu_margin"], abs=1e-9)
        assert got.mu_total == pytest.approx(exp["mu_total"], abs=1e-9)
        assert got.margin_sd == pytest.approx(exp["margin_sd"], abs=1e-9)
        assert got.total_sd == pytest.approx(exp["total_sd"], abs=1e-9)
        assert got.probabilities["home_win"] == pytest.approx(exp["home_win"], abs=1e-9)
        assert got.probabilities["home_cover"] == pytest.approx(exp["home_cover"], abs=1e-9)
        assert got.probabilities["over"] == pytest.approx(exp["over"], abs=1e-9)


def test_probabilities_are_coherent(
    model: CFBStructuralV7Model, fixture: dict, rows: list[core.TeamGame]
) -> None:
    case = fixture["cases"][-1]
    got = price_game(
        model,
        rows,
        season=fixture["season"],
        home_id=case["home_id"],
        away_id=case["away_id"],
        neutral=False,
        decision=datetime.fromisoformat(case["decision_utc"]),
        covariates=fixture["covariates"] or None,
        classes=core.season_classes(rows),
        spread_home_line=-3.0,
        total_line=51.0,
    )
    p = got.probabilities
    assert p["home_win"] + p["away_win"] == pytest.approx(1.0, abs=1e-12)  # no tie mass
    assert p["home_cover"] + p["push_spread"] + p["away_cover"] == pytest.approx(1.0, abs=1e-12)
    assert p["over"] + p["push_total"] + p["under"] == pytest.approx(1.0, abs=1e-12)


def test_unfinished_and_future_games_never_feed_ratings(
    model: CFBStructuralV7Model, fixture: dict, rows: list[core.TeamGame]
) -> None:
    case = fixture["cases"][-1]
    decision = datetime.fromisoformat(case["decision_utc"])
    kw = {
        "season": fixture["season"],
        "home_id": case["home_id"],
        "away_id": case["away_id"],
        "neutral": case["neutral"],
        "decision": decision,
        "covariates": fixture["covariates"] or None,
        "classes": core.season_classes(rows),
    }
    base = price_game(model, rows, **kw)
    # A game that kicked off 4 h before the decision (inside the 5 h result buffer) and one in the future: both
    # carry absurd values, and neither may move the price.
    template = next(r for r in rows if r.season == fixture["season"])
    absurd = {k: 999.0 for k in core.METRICS}
    leaks = [
        replace(template, event_id="leak-live", kickoff=decision - timedelta(hours=4), values=absurd),
        replace(template, event_id="leak-future", kickoff=decision + timedelta(days=2), values=absurd),
    ]
    assert price_game(model, rows + leaks, **kw) == base
    assert core.admitted(decision - timedelta(hours=5), decision)
    assert not core.admitted(decision - timedelta(hours=4, minutes=59), decision)


def test_decision_time_is_the_et_morning_or_the_kickoff() -> None:
    night = datetime(2023, 9, 17, 2, 30, tzinfo=UTC)  # 22:30 ET on 09-16
    assert core.decision_time_for(night) == datetime(2023, 9, 16, 12, 0, tzinfo=UTC)
    dawn = datetime(2023, 8, 26, 11, 0, tzinfo=UTC)  # 07:00 ET, before the 08:00 run
    assert core.decision_time_for(dawn) == dawn


def test_fail_closed_no_rating_and_missing_pbp(
    model: CFBStructuralV7Model, fixture: dict, rows: list[core.TeamGame]
) -> None:
    case = fixture["cases"][0]
    decision = datetime.fromisoformat(case["decision_utc"])
    with pytest.raises(core.V7NoCall) as exc:
        price_game(
            model,
            rows,
            season=fixture["season"],
            home_id="999999",
            away_id=case["away_id"],
            neutral=False,
            decision=decision,
            covariates=fixture["covariates"] or None,
            classes={**core.season_classes(rows), ("999999", fixture["season"]): core.FBS},
        )
    assert exc.value.reason == core.NO_CALL_NO_RATING
    late = datetime.fromisoformat(fixture["cases"][-1]["decision_utc"])
    team = fixture["cases"][-1]["home_id"]
    done = [
        r.event_id
        for r in rows
        if r.team_id == team and r.season == fixture["season"] and core.admitted(r.kickoff, late)
    ]
    check_pbp_complete(rows, {team: done}, [team], fixture["season"], late)
    with pytest.raises(core.V7NoCall) as exc2:
        check_pbp_complete(rows, {team: [*done, "not-crawled"]}, [team], fixture["season"], late)
    assert exc2.value.reason == NO_CALL_PBP_MISSING


def test_truncated_history_prices_like_full_history(
    model: CFBStructuralV7Model, fixture: dict, rows: list[core.TeamGame]
) -> None:
    """Serving sees only games before the decision; with the season-level class map supplied, the price is the one
    the full-history research path produced (and a missing class label fails closed instead of defaulting)."""
    classes = core.season_classes(rows)
    for case in fixture["cases"]:
        decision = datetime.fromisoformat(case["decision_utc"])
        kw = {
            "season": fixture["season"],
            "home_id": case["home_id"],
            "away_id": case["away_id"],
            "neutral": case["neutral"],
            "decision": decision,
            "covariates": fixture["covariates"] or None,
            "classes": classes,
        }
        past = [r for r in rows if r.kickoff < decision]
        assert price_game(model, past, **kw) == price_game(model, rows, **kw)
    case = fixture["cases"][0]
    with pytest.raises(core.V7NoCall) as exc:
        price_game(
            model,
            rows,
            season=fixture["season"],
            home_id=case["home_id"],
            away_id=case["away_id"],
            neutral=False,
            decision=datetime.fromisoformat(case["decision_utc"]),
            covariates=fixture["covariates"] or None,
            classes={k: v for k, v in classes.items() if k != (case["home_id"], fixture["season"])},
        )
    assert exc.value.reason == NO_CALL_NO_CLASS
