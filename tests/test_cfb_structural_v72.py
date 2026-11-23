"""cfb-structural-v7.2 (quick-261123-6q5): v7.1 served as the NCAAF champion, totals shrunk toward 50%.

v7.2 = v7.1's artifact + ``total_probability_logit_slope`` + v5.2's ``gates`` block. These tests build a SYNTHETIC
v7.2 artifact in tmp_path (v7.1 + slope 0.25 + v5.2's gates, hash recomputed) so they hold whether or not the real
``config/models/cfb-structural-v7.2.json`` exists and whatever slope it carries.

Pinned here:
- parity: moneyline and spread equal v7.1 bit for bit; totals equal ``shrink_total_probabilities`` of v7.1's;
- refusals: a missing/None/invalid slope, v7 and v7.1 as an NCAAF serving class, a role that does not match the
  version, and an absent artifact (production loads it by id and fails closed);
- the slate: champion-labelled contracts before the challengers', no NB contract, a no-call game gets nothing;
- the writer: v7.2 reaches Main through v5.2's gates; totals stay off Main (weather gate, OD-5); settlement slugs.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from model_prediction.cfb_replay import TOTAL_SLOPE_KEY, shrink_total_probabilities
from model_prediction.config import PROJECT_ROOT
from model_prediction.features import cfb_features_v7 as core
from model_prediction.models import cfb_structural_v7
from model_prediction.models.cfb_structural_v7 import (
    CFB_V7_MODEL_VERSION,
    CFB_V71_MODEL_VERSION,
    CFB_V72_MODEL_VERSION,
    CHAMPION_VERSIONS,
    NO_CALL_PBP_MISSING,
    CFBStructuralV7Model,
    cfb_structural_v72_model,
    load_cfb_structural_v7_artifact,
    price_game,
    price_v7_slate,
)
from model_prediction.models.cfb_structural_v7_challenger import (
    CHAMPION_CALIBRATION_METHOD,
    CHAMPION_FEATURE_SCHEMA,
    build_contracts,
    price_challenger_slate,
    v7_serving_code_hash,
)
from model_prediction.models.cfb_structural_v62 import EventQuotes
from model_prediction.production_registry import compute_artifact_hash
from tests.test_cfb_multi_challenger import (
    AWAY_ID,
    DECISION,
    HOME_ID,
    KICKOFF,
    ML_SLUG,
    QUOTE_AT,
    SEASON,
    SP_SLUG,
    TOT_SLUG,
    _quotes,
    _snapshot_row,
)

V7, V71, V72 = CFB_V7_MODEL_VERSION, CFB_V71_MODEL_VERSION, CFB_V72_MODEL_VERSION
V62 = "cfb-structural-v6.2"
SLOPE = 0.25
FIXTURE = PROJECT_ROOT / "outputs" / "ncaaf" / "cfb_v71_parity_fixture.json"
ALL_MARKETS = ("moneyline", "spread", "total")


def _v72_artifact(**changes) -> dict:
    """v7.1's artifact + slope + v5.2's gates under the v7.2 id, re-hashed. ``changes`` override top-level keys
    (a value of ``...`` deletes the key) before hashing."""
    art = json.loads(json.dumps(load_cfb_structural_v7_artifact(model_version=V71)))
    v52 = json.loads(
        (PROJECT_ROOT / "config" / "models" / "cfb-structural-v5.2.json").read_text(encoding="utf-8")
    )
    art.update(model_version=V72, gates=v52["gates"])
    art[TOTAL_SLOPE_KEY] = SLOPE
    for key, value in changes.items():
        if value is ...:
            art.pop(key, None)
        else:
            art[key] = value
    art.pop("artifact_hash", None)
    art["artifact_hash"] = compute_artifact_hash(art)
    return art


@pytest.fixture(scope="module")
def v72_artifact(tmp_path_factory) -> dict:
    """Written to disk and read back through the hash-verifying loader, as production does by id."""
    path = tmp_path_factory.mktemp("v72") / f"{V72}.json"
    path.write_text(json.dumps(_v72_artifact(), indent=2), encoding="utf-8")
    return load_cfb_structural_v7_artifact(path, model_version=V72)


@pytest.fixture(scope="module")
def v72(v72_artifact: dict) -> CFBStructuralV7Model:
    return CFBStructuralV7Model(v72_artifact)


@pytest.fixture(scope="module")
def v71() -> CFBStructuralV7Model:
    return CFBStructuralV7Model(load_cfb_structural_v7_artifact(model_version=V71))


@pytest.fixture(scope="module")
def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------------------------- parity
def test_v72_moneyline_and_spread_equal_v71_bit_for_bit_and_totals_are_the_shrunk_v71(
    v71, v72, fixture
) -> None:
    rows = sorted(
        (core.team_game_from_row(r) for r in fixture["rows"]),
        key=lambda r: (r.kickoff, r.event_id, -r.home_sign),
    )
    classes = {(t, int(s)): c for t, s, c in fixture["classes"]}
    assert len(fixture["cases"]) == 12
    moved = 0
    for case in fixture["cases"]:
        priced = {}
        for model in (v71, v72):
            priced[model.version] = price_game(
                model,
                rows,
                season=fixture["season"],
                home_id=case["home_id"],
                away_id=case["away_id"],
                neutral=case["neutral"],
                decision=datetime.fromisoformat(case["decision_utc"]),
                covariates=model.covariates(fixture["cfbd_rows"], fixture["fbs_team_ids"]),
                classes=classes,
                spread_home_line=case["spread_home_line"],
                total_line=case["total_line"],
            )
        a, b = priced[V71], priced[V72]
        assert (a.mu_margin, a.mu_total, a.margin_sd, a.total_sd, a.early, a.is_fbs_vs_fcs) == (
            b.mu_margin,
            b.mu_total,
            b.margin_sd,
            b.total_sd,
            b.early,
            b.is_fbs_vs_fcs,
        )
        pa, pb = a.probabilities, b.probabilities
        for key in ("home_win", "away_win", "home_cover", "push_spread", "away_cover", "push_total"):
            assert pb[key] == pa[key], key
        assert (pb["over"], pb["under"]) == shrink_total_probabilities(
            pa["over"], pa["under"], pa["push_total"], SLOPE
        )
        assert a.raw_total is None and b.raw_total == {"over": pa["over"], "under": pa["under"]}
        assert abs(pb["over"] - 0.5) <= abs(pa["over"] - 0.5) + 1e-15
        moved += pb["over"] != pa["over"]
    assert moved >= 10, "the shrink must actually move the fixture's totals"


def test_the_live_seam_records_the_slope_and_raw_totals_for_v72_only(tmp_path, v71, v72, fixture) -> None:
    from tests.test_cfb_structural_v71 import _game, _store

    _store(tmp_path, fixture)
    case = fixture["cases"][-1]
    run = datetime(2023, 11, 5, 3, 50, tzinfo=UTC)
    kickoff = datetime(2023, 11, 4, 23, 30, tzinfo=UTC)
    (r71,), _ = price_v7_slate(tmp_path, run, [_game(case, kickoff)], model=v71)
    (r72,), s72 = price_v7_slate(tmp_path, run, [_game(case, kickoff)], model=v72)
    assert r71["status"] == r72["status"] == "ok" and s72["model_version"] == V72
    p71, p72 = r71["probabilities"], r72["probabilities"]
    assert {k: v for k, v in p72.items() if k not in ("over", "under")} == {
        k: v for k, v in p71.items() if k not in ("over", "under")
    }
    assert (p72["over"], p72["under"]) == shrink_total_probabilities(
        p71["over"], p71["under"], p71["push_total"], SLOPE
    )
    extra = {TOTAL_SLOPE_KEY: SLOPE, "raw_over": p71["over"], "raw_under": p71["under"]}
    assert r72["inputs"] == {**r71["inputs"], **extra}
    assert not set(extra) & set(r71["inputs"])  # v7.1's snapshot keeps its exact key set


# ----------------------------------------------------------------------------------------------- refusals
@pytest.mark.parametrize("slope", [..., None, 1.5, -0.1, "steep"])
def test_a_v72_artifact_without_a_valid_slope_is_refused(slope) -> None:
    with pytest.raises(ValueError):
        CFBStructuralV7Model(_v72_artifact(**{TOTAL_SLOPE_KEY: slope}))


def test_a_tampered_v72_artifact_is_refused() -> None:
    bad = _v72_artifact()
    bad[TOTAL_SLOPE_KEY] = 0.9
    with pytest.raises(ValueError, match="artifact_hash mismatch"):
        CFBStructuralV7Model(bad)


def test_cfb_model_for_artifact_serves_v72_and_refuses_v7_and_v71(v72_artifact) -> None:
    from model_prediction.models.college_football import _SERVING_CLASSES, cfb_model_for_artifact

    model = cfb_model_for_artifact(v72_artifact)
    assert isinstance(model, CFBStructuralV7Model) and model.version == V72
    assert model.total_probability_logit_slope == SLOPE and model.feature_capture
    assert CHAMPION_VERSIONS == (V72,)
    for version in (V7, V71):
        with pytest.raises(ValueError, match="no NCAAF serving class"):
            cfb_model_for_artifact(load_cfb_structural_v7_artifact(model_version=version))
    assert not set(_SERVING_CLASSES) & {V7, V71, V72}  # the NB registry stays NB-only


def test_production_loads_v72_by_id_and_fails_closed_when_it_is_absent(tmp_path, monkeypatch) -> None:
    from model_prediction.cli import forecast

    monkeypatch.setattr(cfb_structural_v7, "PROJECT_ROOT", tmp_path)
    with pytest.raises(FileNotFoundError):
        cfb_structural_v72_model()
    monkeypatch.setattr(forecast, "PROJECT_ROOT", tmp_path)
    artifact, error = forecast._load_exact_artifact_contract(V72)
    assert artifact is None and error == f"exact serving artifact missing for {V72}"
    with pytest.raises(ValueError, match="exact serving artifact missing"):
        forecast._forecast_cfb_sport(
            data_root=tmp_path,
            args_date="2023-11-11",
            config={"models": {"NCAAF": {"active_production_version": V72}}},
        )


def test_the_role_must_match_the_version(v71, v72) -> None:
    pricing = cfb_structural_v7.V7Pricing(
        20.0, 55.0, 15.0, 14.0, False, False, {"home_win": 0.9, "away_win": 0.1}
    )
    kw = {"season": SEASON, "decision_time": DECISION, "markets": ALL_MARKETS}
    with pytest.raises(ValueError, match="cannot price as champion"):
        build_contracts(v71, _quotes(), pricing, role="champion", **kw)
    with pytest.raises(ValueError, match="cannot price as challenger"):
        build_contracts(v72, _quotes(), pricing, **kw)
    with pytest.raises(ValueError, match="unknown cfb v7 pricing role"):
        build_contracts(v72, _quotes(), pricing, role="main", **kw)
    with pytest.raises(ValueError, match="cannot price as champion"):
        price_challenger_slate(
            PROJECT_ROOT, DECISION, [_quotes()], markets=ALL_MARKETS, model=v71, model_id=V71, role="champion"
        )
    no_capture = _v72_artifact(serving={"score_only_rows": "terminal_without_pbp"})
    with pytest.raises(ValueError, match="must set serving.feature_capture"):
        build_contracts(CFBStructuralV7Model(no_capture), _quotes(), pricing, role="champion", **kw)


# ----------------------------------------------------------------------------------------------- champion pricing
_OK = {
    "status": "ok",
    "mu_margin": 6.5,
    "mu_total": 54.0,
    "margin_sd": 15.0,
    "total_sd": 14.0,
    "early": False,
    "is_fbs_vs_fcs": False,
}


def _seam(by_event: dict, calls: list | None = None, fail: str | None = None):
    """A fake ``price_v7_slate``: ``by_event`` maps event id -> probabilities (ok) or a NO_CALL reason."""

    def pricer(_root, decision, games, *, model):
        if calls is not None:
            calls.append((model.version, sorted(g["event_id"] for g in games)))
        if model.version == fail:
            raise RuntimeError("v7 store unreadable")
        out = []
        for g in games:
            spec = by_event[g["event_id"]]
            if isinstance(spec, str):
                out.append({"event_id": g["event_id"], "status": "no_call", "reason": spec})
            else:
                out.append(
                    {
                        **_OK,
                        "event_id": g["event_id"],
                        "is_fbs_vs_fcs": bool(spec.get("_fcs")),
                        "probabilities": {k: v for k, v in spec.items() if k != "_fcs"},
                        "inputs": {
                            "fit_decision_utc": decision.isoformat(),
                            "home_games": 9,
                            "away_games": 9,
                        },
                    }
                )
        return out, {"season": SEASON, "data": {"source": "test"}}

    return pricer


def test_champion_prices_only_captured_books_seen_before_the_decision(v72) -> None:
    """OD-2: an indicative-only event and a book observed after the decision give no champion contract."""
    probs = {
        "home_win": 0.9,
        "away_win": 0.1,
        "home_cover": 0.6,
        "away_cover": 0.4,
        "over": 0.6,
        "under": 0.4,
    }
    events = [
        _quotes("indicative", sportsbook="polymarket_us_indicative"),
        _quotes("late", observed="2023-11-11T12:00:01+00:00"),
    ]
    contracts, summary = price_challenger_slate(
        PROJECT_ROOT,
        DECISION,
        events,
        markets=ALL_MARKETS,
        model=v72,
        model_id=V72,
        role="champion",
        slate_pricer=_seam({"indicative": probs, "late": probs}),
    )
    assert (
        contracts == [] and summary["no_calls"] == {"no_captured_quote": 2} and summary["role"] == "champion"
    )


def _event(eid: str, home: tuple[str, str], away: tuple[str, str]) -> dict:
    return {
        "id": eid,
        "date": KICKOFF,
        "season": {"year": SEASON, "type": 2},
        "week": {"number": 11},
        "competitions": [
            {
                "neutralSite": False,
                "competitors": [
                    {"homeAway": "home", "team": {"id": home[0], "displayName": home[1]}},
                    {"homeAway": "away", "team": {"id": away[0], "displayName": away[1]}},
                ],
                "odds": [],
            }
        ],
    }


def _champion_slate(tmp_path: Path, monkeypatch, model, pricer, routes: dict) -> dict:
    """build_cfb_slate with the v7.2 champion over two ESPN events with captured books: v72e1 (Alabama @ Georgia,
    all three markets) and v72e2 (Oklahoma @ Texas, moneyline only)."""
    from model_prediction.models import cfb_structural_v7_challenger, cfb_structural_v62, college_football

    path = tmp_path / "odds" / "ncaaf" / "2023-11-11" / "polymarket_snapshots.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    texas = {"event_title": "Oklahoma Sooners vs. Texas Longhorns", "team": "Oklahoma Sooners"}
    rows = [
        _snapshot_row(ML_SLUG, "moneyline", 0.22, 0.80),
        _snapshot_row(SP_SLUG, "spread", 0.52, 0.50, line=24.5),
        _snapshot_row(TOT_SLUG, "total", 0.50, 0.52, line=50.5),
        _snapshot_row("aec-cfb-okla-tex-2023-11-11", "moneyline", 0.40, 0.62, **texas),
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    events = [
        _event("v72e1", (HOME_ID, "Georgia Bulldogs"), (AWAY_ID, "Alabama Crimson Tide")),
        _event("v72e2", ("251", "Texas Longhorns"), ("201", "Oklahoma Sooners")),
    ]
    client = SimpleNamespace(scoreboard=lambda *_a, **_k: {"events": events})
    monkeypatch.setattr(college_football, "_load_cfb_history", lambda *_a, **_k: [])
    monkeypatch.setattr(
        "model_prediction.features.multi_sport_weather.resolve_cfb_game_weather",
        lambda *_a, **_k: (70.0, 0.0, 0.0, True),
    )

    def nb_engine(*_a, **_k):
        raise AssertionError("the NB engine must not price a v7 champion slate")

    monkeypatch.setattr(college_football.CFBStructuralV4Model, "predict_matchup", nb_engine)
    monkeypatch.setattr(college_football, "_ncaaf_challenger_routes", lambda: routes)
    monkeypatch.setattr(
        cfb_structural_v62,
        "price_challenger_slate",
        lambda *_a: ([{"model_version": V62, "event_id": "v72e1", "market_type": "total"}], {"status": "ok"}),
    )
    monkeypatch.setattr(cfb_structural_v7_challenger, "default_slate_pricer", lambda: pricer)
    return college_football.build_cfb_slate(
        tmp_path, "2023-11-11", client=client, observed_at=DECISION, model=model, price_challenger=True
    )


_E1 = {"home_win": 0.9, "away_win": 0.1, "home_cover": 0.56, "push_spread": 0.0, "away_cover": 0.44}
_E1 |= {"over": 0.56, "push_total": 0.0, "under": 0.44}


def test_the_champion_slate_prices_through_the_v7_seam_before_the_challengers(
    tmp_path, monkeypatch, v72
) -> None:
    calls: list = []
    routes = {V62: (list(ALL_MARKETS), None), V71: (list(ALL_MARKETS), None)}
    pricer = _seam({"v72e1": dict(_E1), "v72e2": NO_CALL_PBP_MISSING}, calls)
    slate = _champion_slate(tmp_path, monkeypatch, v72, pricer, routes)

    # The champion is priced first, over both events; v7.1 (Flat) after it.
    assert [c[0] for c in calls] == [V72, V71]
    assert calls[0][1] == ["v72e1", "v72e2"]
    contracts = slate["priced_contracts"]
    champion = [c for c in contracts if c["model_version"] == V72]
    assert contracts[: len(champion)] == champion
    assert {c["model_version"] for c in contracts[len(champion) :]} == {V62, V71}
    # No NB contract, no v5.2 fallback for the no-call game.
    assert {c["model_version"] for c in contracts} == {V72, V62, V71}
    assert {c["event_id"] for c in contracts if c["model_version"] != V62} == {"v72e1"}
    assert {c["market_type"] for c in champion} == set(ALL_MARKETS)
    for c in champion:
        assert c["rationale"].startswith("CFB v7.2 champion: ")
        assert f"market_slug={c['market_slug']}." in c["rationale"]
        assert "(" not in c["rationale"] and ")" not in c["rationale"]
        assert "challenger_of" not in c["feature_basis"]
        assert c["model_artifact_hash"] == v72.artifact_hash
        assert c["code_revision"] == v7_serving_code_hash() == slate["model_code_hash"]
        assert c["inference_inputs"] is None and c["feature_capture"] is True
        assert c["feature_schema_version"] == CHAMPION_FEATURE_SCHEMA
        assert c["calibration_method"] == CHAMPION_CALIBRATION_METHOD
        assert c["sportsbook"] == "polymarket_us" and c["observed_at_utc"] == "2023-11-11T11:00:00Z"
        assert c["weather_inputs_verified"] is None  # OD-5: v7.2 totals never clear the weather gate
        assert c["model_uncertainty"] == 0.0  # OD-8: the 5% policy default sizes them
    assert {c["market_slug"] for c in champion} == {ML_SLUG, SP_SLUG, TOT_SLUG}
    for c in contracts[len(champion) :]:
        if c["model_version"] == V71:
            assert (
                "challenger" in c["rationale"] and c["feature_basis"]["challenger_of"] == "cfb-structural-v5"
            )
    assert slate["model_version"] == V72
    summary = slate["champion_pricing"]
    assert summary["role"] == "champion" and summary["priced"] == 1
    assert summary["no_calls"] == {NO_CALL_PBP_MISSING: 1}
    assert slate["candidate_quote_coverage"] == {"status": "not_requested"}


def test_a_champion_seam_failure_prices_nothing_and_never_falls_back(tmp_path, monkeypatch, v72) -> None:
    routes = {V62: (list(ALL_MARKETS), None), V71: (list(ALL_MARKETS), None)}
    pricer = _seam({"v72e1": dict(_E1), "v72e2": dict(_E1)}, fail=V72)
    slate = _champion_slate(tmp_path, monkeypatch, v72, pricer, routes)
    assert slate["champion_pricing"]["status"] == "error"
    assert "v7 store unreadable" in slate["champion_pricing"]["reason"]
    versions = [c["model_version"] for c in slate["priced_contracts"]]
    assert V72 not in versions and V62 in versions and V71 in versions  # the Flat challengers still run


# ----------------------------------------------------------------------------------------------- writer
def _eq(eid: str, *, ml=None, spread=None, total=None) -> EventQuotes:
    lineage = {"market_quote_observed_at_utc": QUOTE_AT}
    return EventQuotes(
        event_id=eid,
        event_start_utc=KICKOFF,
        away_team=f"Away {eid}",
        home_team=f"Home {eid}",
        is_neutral_site=False,
        moneyline=None
        if ml is None
        else {
            "home_ask": ml[0],
            "away_ask": ml[1],
            "slug_base": f"aec-cfb-{eid}",
            "sportsbook": "polymarket_us",
        }
        | {"lineage": lineage},
        spread=None
        if spread is None
        else {"home_line": spread[0], "home_ask": spread[1], "away_ask": spread[2], "slug": f"asc-cfb-{eid}"}
        | {"sportsbook": "polymarket_us", "lineage": lineage},
        total=None
        if total is None
        else {"line": total[0], "over_ask": total[1], "under_ask": total[2], "slug": f"tsc-cfb-{eid}"}
        | {"sportsbook": "polymarket_us", "lineage": lineage},
        home_team_id=f"h{eid}",
        away_team_id=f"a{eid}",
    )


def test_v72_reaches_main_through_v52_gates_and_totals_stay_off_main(
    tmp_path, monkeypatch, v72, v72_artifact
) -> None:
    from model_prediction.cli import forecast
    from model_prediction.cli.settle import _extract_market_slug
    from model_prediction.learned_replay import validate_decision_snapshot
    from model_prediction.ledger import PickLedger
    from model_prediction.models import college_football
    from model_prediction.ncaaf_prekickoff_capture import ncaaf_settlement_slug

    events = [
        # e1: moneyline 65% vs 60% and spread -7.5 at 56% vs 51% clear Main; the total is weather-gated (OD-5).
        _eq("e1", ml=(0.60, 0.42), spread=(-7.5, 0.51, 0.51), total=(50.5, 0.50, 0.52)),
        # e2: FBS vs FCS, blocked by v5.2's block_fcs gate.
        _eq("e2", ml=(0.60, 0.42)),
        # e3: a 36% dog at a 30% ask (below the 33.3% floor) and a 24.5-point spread (above 21.5).
        _eq("e3", ml=(0.66, 0.30), spread=(-24.5, 0.51, 0.51)),
    ]
    probs = {
        "e1": {**_E1, "home_win": 0.65, "away_win": 0.35},
        "e2": {"home_win": 0.65, "away_win": 0.35, "_fcs": True},
        "e3": {
            "home_win": 0.64,
            "away_win": 0.36,
            "home_cover": 0.57,
            "push_spread": 0.0,
            "away_cover": 0.43,
        },
    }
    contracts, summary = price_challenger_slate(
        PROJECT_ROOT,
        DECISION,
        events,
        markets=ALL_MARKETS,
        model=v72,
        model_id=V72,
        role="champion",
        slate_pricer=_seam(probs),
    )
    assert summary["priced"] == 3 and len(contracts) == 6

    original = forecast._load_exact_artifact_contract
    monkeypatch.setattr(
        forecast,
        "_load_exact_artifact_contract",
        lambda version: (v72_artifact, None) if version == V72 else original(version),
    )
    slate = {"status": "research", "model_code_hash": "unused", "priced_contracts": contracts}
    monkeypatch.setattr(college_football, "build_cfb_slate", lambda **_kwargs: slate)
    monkeypatch.setattr(forecast, "utc_now", lambda: DECISION)
    ledgers = {
        tier: PickLedger(
            tmp_path / f"{tier}.xlsx", audit_path=tmp_path / f"{tier}.jsonl", tier=tier, sport="ncaaf"
        )
        for tier in ("research", "gated", "flat", "main")
    }
    result = forecast._forecast_cfb_sport(
        data_root=tmp_path,
        args_date="2023-11-11",
        config={
            "models": {
                "NCAAF": {"min_edge": 0.005, "status": "shadow_qualified", "active_production_version": V72}
            },
            "project": {"maximum_data_age_hours": 24},
            "bankroll": {},
        },
        research_ledger=ledgers["research"],
        gated_ledger=ledgers["gated"],
        flat_ledger=ledgers["flat"],
        main_ledger=ledgers["main"],
    )
    assert result["errors"] == []
    main = ledgers["main"].rows()
    assert sorted((r["event_id"], r["market_type"]) for r in main) == [("e1", "moneyline"), ("e1", "spread")]
    assert {(r["event_id"], r["market_type"]) for r in ledgers["gated"].rows()} == {
        ("e1", "moneyline"),
        ("e1", "spread"),
    }
    rejections = {(r["event_id"], r["market"], r["reason"]) for r in result["main_rejections"]}
    assert {
        ("e1", "total", "pregame_weather_unverified"),
        ("e2", "moneyline", "fcs_blocked"),
        ("e3", "moneyline", "moneyline_ask_below_floor"),
        ("e3", "spread", "spread_line_above_max"),
    } <= rejections
    for row in main:
        assert row["model_version"] == V72 and row["model_artifact_hash"] == v72.artifact_hash
        assert row["code_revision"] == v7_serving_code_hash()
        assert row["decision"] == "CALL" and float(row["units"]) > 0
        snap = json.loads(row["model_input_snapshot_json"])
        assert snap["schema"] == "feature-capture-v1"
        assert validate_decision_snapshot(snap, row) == pytest.approx(
            float(row["model_probability"]), abs=1e-6
        )
    flat = ledgers["flat"].rows()
    assert len(flat) == 6 and {r["model_version"] for r in flat} == {V72}
    for row in flat:
        slug = next(
            c["market_slug"]
            for c in contracts
            if c["event_id"] == row["event_id"] and c["market_type"] == row["market_type"]
        )
        assert _extract_market_slug(row["rationale"], row) == slug
        assert ncaaf_settlement_slug(_extract_market_slug(row["rationale"], row), row["rationale"]) == slug
