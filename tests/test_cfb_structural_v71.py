"""cfb-structural-v7.1 (quick-261122-v71): the v7 refit with the 2026-11-22 bug-hunt fixes.

Each fix has a test that fails when the fix is reverted:

1. neutral-site intercept (``CFBStructuralV7Model.means``);
2. CFBD covariates z-scored over, and applied to, the season's FBS teams only (``standardise_covariates``);
3. serving fits each game at min(run clock, its research decision time) and adds points-only rows only for
   games ESPN will never give play-by-play for (``price_v7_slate`` / ``load_v7_serving_data``);
4. (the 404 retry is in tests/test_cfb_pbp_live.py);
5. v7.1 ledger rows carry a feature-capture snapshot of the model inputs (``_forecast_cfb_sport``).

The parity fixture (outputs/ncaaf/cfb_v71_parity_fixture.json) is priced by scripts/cfb_v71_fit.py; it carries the
raw CFBD rows (FCS teams with talent included) and the season FBS list, so serving runs the standardisation itself.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from model_prediction.config import PROJECT_ROOT
from model_prediction.data_sources import cfb_pbp_live as live
from model_prediction.features import cfb_features_v7 as core
from model_prediction.ledger import PickLedger
from model_prediction.models.cfb_structural_v7 import (
    CFB_V7_MODEL_VERSION,
    CFB_V71_MODEL_VERSION,
    NO_CALL_NO_KICKOFF,
    CFBStructuralV7Model,
    fit_decision,
    load_cfb_structural_v7_artifact,
    load_v7_serving_data,
    price_game,
    price_v7_slate,
)
from model_prediction.production_registry import ProductionModelRegistry, compute_artifact_hash
from tests.test_cfb_pbp_live import PAD_FBS, _build_store, _cfbd_rows

FIXTURE = PROJECT_ROOT / "outputs" / "ncaaf" / "cfb_v71_parity_fixture.json"
SPEC = PROJECT_ROOT / "outputs" / "ncaaf" / "cfb_v71_spec.json"
V71 = CFB_V71_MODEL_VERSION


@pytest.fixture(scope="module")
def model() -> CFBStructuralV7Model:
    return CFBStructuralV7Model(load_cfb_structural_v7_artifact(model_version=V71))


@pytest.fixture(scope="module")
def v7_model() -> CFBStructuralV7Model:
    return CFBStructuralV7Model(load_cfb_structural_v7_artifact())


@pytest.fixture(scope="module")
def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rows(fixture: dict) -> list[core.TeamGame]:
    out = [core.team_game_from_row(r) for r in fixture["rows"]]
    return sorted(out, key=lambda r: (r.kickoff, r.event_id, -r.home_sign))


@pytest.fixture(scope="module")
def classes(fixture: dict) -> dict[tuple[str, int], str]:
    return {(t, int(s)): c for t, s, c in fixture["classes"]}


# ----------------------------------------------------------------------------------------------- artifact
def test_artifact_is_hash_verified_registered_and_carries_the_v71_switches(
    model: CFBStructuralV7Model,
) -> None:
    art = load_cfb_structural_v7_artifact(model_version=V71)
    assert art["model_version"] == V71 and art["artifact_hash"] == compute_artifact_hash(art)
    assert art["training"]["spec_sha256"] == hashlib.sha256(SPEC.read_bytes()).hexdigest()
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    assert art["stage2"] == spec["stage2"]
    assert "neutral_intercept" in art["stage2"]["margin_calibration"]
    assert art["qualification"] == {**art["qualification"], "status": "challenger", "qualified": False}
    assert model.version == V71 and model.per_game_fit and model.score_only_terminal and model.feature_capture
    assert model.covariate_population == ("fbs" if model.uses_cfbd else None)
    entry = ProductionModelRegistry.load(PROJECT_ROOT).entries[V71]
    assert entry.artifact_hash == art["artifact_hash"] and entry.serving_status == "shadow"
    # v7's artifact keeps v7's behaviour: every switch is off.
    v7 = CFBStructuralV7Model(load_cfb_structural_v7_artifact())
    assert v7.version == CFB_V7_MODEL_VERSION
    assert not (v7.per_game_fit or v7.score_only_terminal or v7.feature_capture or v7.covariate_population)
    with pytest.raises(ValueError, match="model_version mismatch"):
        load_cfb_structural_v7_artifact(PROJECT_ROOT / "config" / "models" / f"{V71}.json")


def test_unknown_switch_values_fail_closed() -> None:
    art = load_cfb_structural_v7_artifact(model_version=V71)
    for path, value in (
        (("point_in_time", "serving_fit_rule"), "run_clock_plus"),
        (("serving", "score_only_rows"), "everything"),
        (("prior", "covariate_population"), "all_teams"),
    ):
        bad = json.loads(json.dumps(art))
        bad[path[0]][path[1]] = value
        bad["artifact_hash"] = compute_artifact_hash(bad)
        with pytest.raises(ValueError, match="not one this module implements"):
            CFBStructuralV7Model(bad)


# ----------------------------------------------------------------------------------------------- parity (fixes 1, 2)
def test_serving_reproduces_the_v71_fit_script_from_raw_cfbd_rows(
    model: CFBStructuralV7Model, fixture: dict, rows: list[core.TeamGame], classes: dict
) -> None:
    """Train/serve parity for the changed features: serving standardises the RAW CFBD rows (FCS teams with
    talent included) over the FBS list and prices every case, neutral-site ones included, as the fit script did."""
    assert (
        fixture["spec_sha256"]
        == load_cfb_structural_v7_artifact(model_version=V71)["training"]["spec_sha256"]
    )
    assert len(fixture["cases"]) >= 10 and any(c["neutral"] for c in fixture["cases"])
    fcs_talent = [
        r
        for r in fixture["cfbd_rows"]
        if r["espn_team_id"] not in set(fixture["fbs_team_ids"]) and r.get("talent_prev_season") is not None
    ]
    assert fcs_talent, "the fixture must carry FCS rows with talent, or the FBS-only rule is not exercised"
    covariates = model.covariates(fixture["cfbd_rows"], fixture["fbs_team_ids"])
    assert covariates is not None
    for t, z in fixture["covariates"].items():
        assert covariates[t] == pytest.approx(z, abs=1e-12)
    for case in fixture["cases"]:
        got = price_game(
            model,
            rows,
            season=fixture["season"],
            home_id=case["home_id"],
            away_id=case["away_id"],
            neutral=case["neutral"],
            decision=datetime.fromisoformat(case["decision_utc"]),
            covariates=covariates,
            classes=classes,
            spread_home_line=case["spread_home_line"],
            total_line=case["total_line"],
        )
        exp = case["expected"]
        assert got.mu_margin == pytest.approx(exp["mu_margin"], abs=1e-9)
        assert got.mu_total == pytest.approx(exp["mu_total"], abs=1e-9)
        assert got.margin_sd == pytest.approx(exp["margin_sd"], abs=1e-9)
        assert got.probabilities["home_win"] == pytest.approx(exp["home_win"], abs=1e-9)
        assert got.probabilities["home_cover"] == pytest.approx(exp["home_cover"], abs=1e-9)
        assert got.probabilities["over"] == pytest.approx(exp["over"], abs=1e-9)


def test_identical_teams_at_a_neutral_site_price_at_the_neutral_intercept(
    model: CFBStructuralV7Model, v7_model: CFBStructuralV7Model
) -> None:
    """Fix 1: v7 priced two identical teams at a neutral site at margin -1.62 (home win 47.6%)."""
    side = core.SideFeatures("1", {k: 1.0 for k in core.METRICS})
    feat = core.MatchupFeatures(side, replace(side, team_id="2"), 6, 6, False, neutral=True)
    mu, _ = model.means(feat)
    assert mu == pytest.approx(model.margin_cal["neutral_intercept"], abs=1e-12)
    assert mu != pytest.approx(model.margin_cal["intercept"], abs=1e-3)
    home_mu, _ = model.means(replace(feat, neutral=False))
    assert home_mu == pytest.approx(model.margin_cal["intercept"], abs=1e-12)
    # v7 (no neutral term) keeps its pooled intercept at a neutral site.
    assert v7_model.means(feat)[0] == pytest.approx(v7_model.margin_cal["intercept"], abs=1e-12)


def test_fbs_only_standardisation_ignores_fcs_rows(fixture: dict) -> None:
    """Fix 2: CFBD dropped FCS talent from 2025. Whatever FCS rows exist, FBS z-scores do not move and FCS teams
    carry no covariate, in every season (training and serving alike)."""
    fbs = set(fixture["fbs_team_ids"])
    full = core.standardise_covariates(fixture["cfbd_rows"], population=fbs)
    fbs_only_rows = [r for r in fixture["cfbd_rows"] if r["espn_team_id"] in fbs]
    no_fcs_talent = [
        r if r["espn_team_id"] in fbs else {**r, "talent_prev_season": None} for r in fixture["cfbd_rows"]
    ]
    assert core.standardise_covariates(fbs_only_rows, population=fbs) == full
    assert core.standardise_covariates(no_fcs_talent, population=fbs) == full
    assert set(full) <= fbs
    # v7's rule (no population) is what drifted: dropping FCS talent moves FBS teams' talent z.
    legacy, legacy_no_fcs = (
        core.standardise_covariates(fixture["cfbd_rows"]),
        core.standardise_covariates(no_fcs_talent),
    )
    some_fbs = next(t for t in sorted(fbs) if "tal" in legacy.get(t, {}))
    assert legacy[some_fbs]["tal"] != pytest.approx(legacy_no_fcs[some_fbs]["tal"], abs=1e-3)


# ----------------------------------------------------------------------------------------------- serving (fix 3)
def _store(tmp_path: Path, fixture: dict) -> dict:
    built = _build_store(tmp_path, fixture)
    built["fbs"] = sorted(
        {t for (t, s), c in built["classes"].items() if s == fixture["season"] and c == core.FBS}
        | set(PAD_FBS)
    )
    built["cfbd"] = _cfbd_rows(built["teams"], fixture["season"])
    return built


def _game(case: dict, kickoff: datetime | None) -> dict:
    return {
        "event_id": f"{case['home_id']}-{case['away_id']}",
        "home_team_id": case["home_id"],
        "away_team_id": case["away_id"],
        "is_neutral_site": case["neutral"],
        "spread_home_line": case["spread_home_line"],
        "total_line": case["total_line"],
        "event_start_utc": None if kickoff is None else kickoff.isoformat(),
    }


def test_a_late_run_fits_at_the_research_decision_time(tmp_path, model, fixture, rows) -> None:
    """Fix 3: a 23:50 ET run for a 19:30 ET kickoff fits at 08:00 ET of the game date, as the walk did; v7 fit at
    the run clock and admitted same-day results the validated rule never saw."""
    built = _store(tmp_path, fixture)
    covariates = model.covariates(built["cfbd"], built["fbs"])
    case = fixture["cases"][-1]
    # The fixture holds 2023-11-04 games at 15:00 and 17:00 ET that are final (kickoff + 5 h) before 23:50 ET.
    run = datetime(2023, 11, 5, 3, 50, tzinfo=UTC)  # 23:50 EDT on 11-04
    kickoff = datetime(2023, 11, 4, 23, 30, tzinfo=UTC)  # 19:30 EDT on 11-04
    fit_at = fit_decision(model, run, kickoff)
    assert fit_at == datetime(2023, 11, 4, 12, 0, tzinfo=UTC)  # 08:00 EDT
    same_day = [
        r for r in rows if r.season == fixture["season"] and fit_at < r.kickoff + timedelta(hours=5) <= run
    ]
    assert same_day, "the fixture must hold games admitted by the run clock but not by the research rule"
    results, summary = price_v7_slate(tmp_path, run, [_game(case, kickoff)], model=model)
    assert summary["priced"] == 1, results
    expected = price_game(
        model,
        rows,
        season=fixture["season"],
        home_id=case["home_id"],
        away_id=case["away_id"],
        neutral=case["neutral"],
        decision=fit_at,
        classes=built["classes"],
        covariates=covariates,
        spread_home_line=case["spread_home_line"],
        total_line=case["total_line"],
    )
    got = results[0]
    assert got["fit_decision_utc"] == fit_at.isoformat()
    assert got["mu_margin"] == pytest.approx(expected.mu_margin, abs=1e-9)
    for key, value in expected.probabilities.items():
        assert got["probabilities"][key] == pytest.approx(value, abs=1e-9)
    # An earlier run on the same ET date still fits at the run clock (min).
    early_run = datetime(2023, 11, 4, 11, 0, tzinfo=UTC)
    assert fit_decision(model, early_run, kickoff) == early_run


def test_v71_without_a_kickoff_no_calls(tmp_path, model, fixture) -> None:
    _store(tmp_path, fixture)
    case = fixture["cases"][-1]
    results, summary = price_v7_slate(
        tmp_path, datetime.fromisoformat(case["decision_utc"]), [_game(case, None)], model=model
    )
    assert results[0]["status"] == "no_call" and results[0]["reason"] == NO_CALL_NO_KICKOFF
    assert summary["no_call"] == {NO_CALL_NO_KICKOFF: 1}


def test_points_only_rows_only_for_games_espn_will_never_cover(tmp_path, model, v7_model, fixture) -> None:
    """Fix 3b: a final game whose pbp is merely not fetched yet is not fed in as points only (v7 did that); a game
    ESPN confirmed has no pbp (a 404 a day after kickoff) is."""
    built = _store(tmp_path, fixture)
    store = built["store"]
    decision = datetime.fromisoformat(fixture["cases"][-1]["decision_utc"])
    eid, ev = max(
        ((e, v) for e, v in built["events"].items() if v["kickoff"] + timedelta(hours=30) <= decision),
        key=lambda x: x[1]["kickoff"],
    )
    path = store.pbp_path(fixture["season"])
    kept = [
        r
        for r in (json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x)
        if r["event_id"] != eid
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in kept), encoding="utf-8")

    def points_only(m: CFBStructuralV7Model) -> set[str]:
        data = load_v7_serving_data(tmp_path, decision, model=m)
        return {r.event_id for r in data.rows if r.values.get("sr") is None}

    assert eid in points_only(v7_model)  # v7: pending pbp fed in as points only
    assert eid not in points_only(model)
    live._append_jsonl(
        store.manifest,
        [
            {
                "event_id": eid,
                "season": fixture["season"],
                "status": "http_404",
                "start_utc": live._iso(ev["kickoff"]),
                "fetched_at_utc": live._iso(ev["kickoff"] + timedelta(hours=25)),
            }
        ],
    )
    assert eid in points_only(model)


# ----------------------------------------------------------------------------------------------- ledger (fix 5)
def test_v71_rows_store_their_model_inputs_and_v5_v62_rows_do_not_change(
    tmp_path, monkeypatch, model
) -> None:
    from tests.test_cfb_multi_challenger import (
        DECISION,
        SEASON,
        V5,
        _price,
        _quotes,
        _run_forecast,
        _v5_row,
    )

    inputs = {
        "fit_decision_utc": DECISION.isoformat(),
        "neutral_site": False,
        "home_games": 9,
        "away_games": 9,
        "home_expected": {"pts": 38.1, "sr": 0.47},
        "away_expected": {"pts": 17.2, "sr": 0.39},
        "home_cfbd_z": {"tal": 1.2},
        "away_cfbd_z": {},
    }

    def seam(_root, decision, games, *, model):
        assert all(g["event_start_utc"] for g in games)  # v7.1 needs the kickoff
        return [
            {
                "event_id": g["event_id"],
                "status": "ok",
                "mu_margin": 21.0,
                "mu_total": 55.0,
                "margin_sd": 15.0,
                "total_sd": 14.0,
                "early": False,
                "is_fbs_vs_fcs": False,
                "probabilities": {
                    "home_win": 0.9,
                    "away_win": 0.1,
                    "home_cover": 0.4,
                    "push_spread": 0.0,
                    "away_cover": 0.6,
                    "over": 0.6,
                    "push_total": 0.0,
                    "under": 0.4,
                },
                "inputs": inputs,
            }
            for g in games
        ], {"season": SEASON, "data": {"source": "test"}}

    v71_rows, summary = _price(model, [_quotes()], seam, model_id=V71)
    assert summary["priced"] == 1 and v71_rows
    assert all(c["model_version"] == V71 and c["feature_capture"] for c in v71_rows)
    assert all(c["feature_basis"]["model_inputs"] == inputs for c in v71_rows)
    result, ledgers = _run_forecast(tmp_path, monkeypatch, [_v5_row(), *v71_rows])
    assert result["errors"] == []
    # Paper challenger: never a Main or gated row.
    assert {r["model_version"] for t in ("main", "gated") for r in ledgers[t].rows()} <= {V5}
    for tier in ("flat", "research"):
        rows = ledgers[tier].rows()
        written = [r for r in rows if r["model_version"] == V71]
        assert len(written) == len(v71_rows), tier
        for row in written:
            snap = json.loads(row["model_input_snapshot_json"])
            assert snap["schema"] == "feature-capture-v1" and snap["replay_verified"] is False
            # flatten_features keeps one level: feature_basis.model_inputs -> "model_inputs.<field>".
            assert snap["features"]["model_inputs.home_expected"] == {"pts": 38.1, "sr": 0.47}
            assert snap["features"]["model_inputs.fit_decision_utc"] == DECISION.isoformat()
            # The ledger's feature payload (feature_payload_json) is built from this snapshot: v7 rows had 0.
            payload = PickLedger._feature_payload(row, event_type="update")
            assert payload["observed_feature_count"] > 0 and payload["availability_status"] == "available"
            assert snap["probability"] == pytest.approx(float(row["model_probability"]), abs=1e-6)
        v5 = [r for r in rows if r["model_version"] == V5]
        assert len(v5) == 1 and not v5[0].get("model_input_snapshot_json")


def test_the_slate_hook_dispatches_the_v71_route_to_the_v7_family_pricer(
    tmp_path, monkeypatch, model
) -> None:
    """A registered challenger without a branch in build_cfb_slate prices nothing ("no_pricer"): v7.1 must reach
    the v7-family pricer with its own id, and v7 (unrouted) must not run."""
    from tests.test_cfb_multi_challenger import V62, _slate

    v71_hash = ProductionModelRegistry.load(PROJECT_ROOT).entries[V71].artifact_hash
    routes = {
        V62: (["moneyline", "spread", "total"], None),
        V71: (["moneyline", "spread", "total"], v71_hash),
    }
    calls: list[str] = []

    def pricer(_root, decision, games, *, model):
        calls.append(model.version)
        return [
            {"event_id": g["event_id"], "status": "no_call", "reason": "NO_CALL_V7_TEST"} for g in games
        ], {}

    slate = _slate(tmp_path, monkeypatch, routes, lambda *_a: ([], {"status": "ok"}), pricer)
    assert calls == [V71]
    summary = slate["challenger_pricing_by_model"][V71]
    assert summary["model_version"] == V71 and summary["no_calls"] == {"NO_CALL_V7_TEST": 1}
