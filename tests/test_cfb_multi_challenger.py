"""Several challengers per sport/market (quick-261121-v7r): registry routes, v7 routing, slug hygiene, isolation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from model_prediction.config import PROJECT_ROOT
from model_prediction.features import cfb_features_v7 as core
from model_prediction.models.cfb_structural_v6 import load_cfb_structural_v6_artifact
from model_prediction.models.cfb_structural_v7 import (
    CFB_V7_MODEL_VERSION,
    NO_CALL_NO_CFBD,
    NO_CALL_PBP_MISSING,
    CFBStructuralV7Model,
    check_pbp_complete,
    load_cfb_structural_v7_artifact,
    price_game,
)
from model_prediction.models.cfb_structural_v7_challenger import (
    NO_CALL_LIVE_DATA_UNWIRED,
    price_challenger_slate,
    v7_serving_code_hash,
)
from model_prediction.models.cfb_structural_v62 import (
    CFB_V62_MODEL_VERSION,
    Anchor,
    CFBStructuralV62Model,
    EventQuotes,
    load_cfb_structural_v62_artifact,
    v62_serving_code_hash,
)
from model_prediction.models.cfb_structural_v62 import build_contracts as build_v62_contracts
from model_prediction.production_registry import (
    ProductionModelRegistry,
    remove_challenger_route,
)

V5, V62, V7 = "cfb-structural-v5", CFB_V62_MODEL_VERSION, CFB_V7_MODEL_VERSION
PARITY = PROJECT_ROOT / "outputs" / "ncaaf" / "cfb_v7_parity_fixture.json"
# Parity fixture case: home 130 vs away 259, 2023 season, the 2023-11-11 morning decision (fair margin +26.46).
HOME_ID, AWAY_ID, SEASON = "130", "259", 2023
DECISION = datetime(2023, 11, 11, 12, 0, tzinfo=UTC)
QUOTE_AT = "2023-11-11T11:00:00+00:00"
KICKOFF = "2023-11-11T20:00:00Z"
ML_SLUG = "aec-cfb-awayu-homeu-2023-11-11"
SP_SLUG = "asc-cfb-awayu-homeu-2023-11-11-24pt5"
TOT_SLUG = "tsc-cfb-awayu-homeu-2023-11-11-50pt5"


# ----------------------------------------------------------------------------------------------- registry
def _config() -> dict:
    return yaml.safe_load((PROJECT_ROOT / "config" / "production.yaml").read_text(encoding="utf-8"))


def _registry(config: dict) -> ProductionModelRegistry:
    return ProductionModelRegistry.from_config(config, repo_root=PROJECT_ROOT)


def _model(config: dict, model_id: str) -> dict:
    return next(m for m in config["prediction_service"]["models"] if m["model_id"] == model_id)


def test_ncaaf_routes_v62_then_v7_and_v62_stays_the_primary() -> None:
    registry = ProductionModelRegistry.load(PROJECT_ROOT)
    for market in ("moneyline", "spread", "total"):
        assert [e.model_id for e in registry.challengers_for("NCAAF", market)] == [V62, V7]
        assert registry.challenger("NCAAF", market).model_id == V62
        contract = registry.lifecycle_contract("NCAAF", market)
        assert contract.champion_model_id == V5 and contract.challenger_model_id == V62
        assert registry.champion("NCAAF", market).model_id == V5
    assert not [p for p in registry.challenger_problems if "NCAAF" in p]


def test_a_scalar_route_still_works() -> None:
    config = _config()
    config["prediction_service"]["challengers"]["NCAAF"] = {"moneyline": V62, "spread": V7, "total": None}
    registry = _registry(config)
    assert [e.model_id for e in registry.challengers_for("NCAAF", "moneyline")] == [V62]
    assert registry.challenger("NCAAF", "spread").model_id == V7
    assert registry.challenger("NCAAF", "total") is None and registry.challengers_for("NCAAF", "total") == []
    assert registry.lifecycle_contract("NCAAF", "spread").challenger_model_id == V7


def test_each_broken_challenger_fails_closed_on_its_own() -> None:
    config = _config()
    _model(config, V62)["enabled"] = False
    config["prediction_service"]["challengers"]["NCAAF"]["moneyline"] = [
        "no-such-model",
        V62,  # disabled
        V7,
        7,  # not an id
        V7,  # listed twice
        V5,  # the champion
        "nba-elo-trend-lr-v5",  # another sport's contract
    ]
    registry = _registry(config)
    assert [e.model_id for e in registry.challengers_for("NCAAF", "moneyline")] == [V7]
    # No fall-through: the primary is broken, so single-challenger consumers see no challenger at all.
    assert registry.challenger("NCAAF", "moneyline") is None
    assert [e.model_id for e in registry.challengers_for("NCAAF", "spread")] == [V7]
    problems = "\n".join(registry.challenger_problems)
    for fragment in (
        "'no-such-model': not a registered",
        f"'{V62}': unavailable",
        "got 7",
        f"'{V7}': listed twice",
        f"'{V5}': is the champion",
        "'nba-elo-trend-lr-v5': contract is NBA",
    ):
        assert fragment in problems, fragment
    for market in ("moneyline", "spread", "total"):
        assert registry.champion("NCAAF", market).model_id == V5


def test_a_broken_v7_leaves_v62_routed_and_primary() -> None:
    config = _config()
    bad = "config/models/does-not-exist-v7.json"
    _model(config, V7)["artifact"] = bad
    config["prediction_service"]["artifact_map"][V7] = bad
    registry = _registry(config)
    assert registry.entries[V7].load_error
    for market in ("moneyline", "spread", "total"):
        assert [e.model_id for e in registry.challengers_for("NCAAF", market)] == [V62]
        assert registry.challenger("NCAAF", market).model_id == V62


def test_metadata_keys_are_not_routes() -> None:
    registry = ProductionModelRegistry.load(PROJECT_ROOT)
    assert registry.challengers_for("MLB", "research_candidates") == []
    assert not [p for p in registry.challenger_problems if "research_candidates" in p]
    config = _config()
    config["prediction_service"]["challengers"]["NFL"] = None  # not a mapping: that sport only
    registry = _registry(config)
    assert any("challengers[NFL] must be a mapping" in p for p in registry.challenger_problems)
    assert [e.model_id for e in registry.challengers_for("NCAAF", "total")] == [V62, V7]


def test_remove_challenger_route_handles_lists_and_scalars() -> None:
    routes = {"moneyline": [V62, V7], "spread": [V62, V7, "x"], "total": V7, "nrfi": [V7]}
    assert remove_challenger_route(routes, "moneyline", V7) and routes["moneyline"] == V62
    assert remove_challenger_route(routes, "spread", V62) and routes["spread"] == [V7, "x"]
    assert remove_challenger_route(routes, "total", V7) and "total" not in routes
    assert remove_challenger_route(routes, "nrfi", V7) and "nrfi" not in routes
    assert not remove_challenger_route(routes, "moneyline", "other")
    assert not remove_challenger_route(routes, "missing", V7)
    assert not remove_challenger_route(None, "moneyline", V7)


# ----------------------------------------------------------------------------------------------- v7 pricing
@pytest.fixture(scope="module")
def v7_model() -> CFBStructuralV7Model:
    return CFBStructuralV7Model(load_cfb_structural_v7_artifact())


@pytest.fixture(scope="module")
def v62_model() -> CFBStructuralV62Model:
    return CFBStructuralV62Model(load_cfb_structural_v62_artifact(), load_cfb_structural_v6_artifact())


@pytest.fixture(scope="module")
def data() -> SimpleNamespace:
    fixture = json.loads(PARITY.read_text(encoding="utf-8"))
    rows = sorted(
        (core.team_game_from_row(r) for r in fixture["rows"]),
        key=lambda r: (r.kickoff, r.event_id, -r.home_sign),
    )
    return SimpleNamespace(
        rows=rows, classes=core.season_classes(rows), covariates=fixture["covariates"] or None
    )


def _seam(data: SimpleNamespace, *, completed_games: dict | None = None, slate_no_call: str | None = None):
    """A stand-in for ``cfb_structural_v7.price_v7_slate`` (quick-261121-v7l) honouring its result contract,
    priced through the real ``check_pbp_complete`` + ``price_game`` on the v7 parity fixture."""

    def pricer(_root, decision, games, *, model):
        results = []
        for g in games:
            out = {
                "event_id": g["event_id"],
                "home_team_id": g["home_team_id"],
                "away_team_id": g["away_team_id"],
            }
            try:
                if slate_no_call is not None:
                    raise core.V7NoCall(slate_no_call, "slate")
                check_pbp_complete(
                    data.rows, completed_games or {}, (g["home_team_id"], g["away_team_id"]), SEASON, decision
                )
                p = price_game(
                    model,
                    data.rows,
                    season=SEASON,
                    home_id=g["home_team_id"],
                    away_id=g["away_team_id"],
                    neutral=g["is_neutral_site"],
                    decision=decision,
                    classes=data.classes,
                    covariates=data.covariates,
                    spread_home_line=g["spread_home_line"],
                    total_line=g["total_line"],
                )
            except core.V7NoCall as exc:
                out.update(status="no_call", reason=exc.reason)
            else:
                out.update(
                    status="ok",
                    mu_margin=p.mu_margin,
                    mu_total=p.mu_total,
                    margin_sd=p.margin_sd,
                    total_sd=p.total_sd,
                    early=p.early,
                    is_fbs_vs_fcs=p.is_fbs_vs_fcs,
                    probabilities=p.probabilities,
                )
            results.append(out)
        return results, {"season": SEASON, "data": {"source": "parity_fixture"}}

    return pricer


def _quotes(
    event_id: str = "v7e1", *, sportsbook: str = "polymarket_us", observed: str = QUOTE_AT
) -> EventQuotes:
    lineage = {"market_quote_observed_at_utc": observed}
    return EventQuotes(
        event_id=event_id,
        event_start_utc=KICKOFF,
        away_team="Away U",
        home_team="Home U",
        is_neutral_site=False,
        moneyline={
            "home_ask": 0.80,
            "away_ask": 0.22,
            "slug_base": ML_SLUG,
            "sportsbook": sportsbook,
            "lineage": lineage,
        },
        spread={
            "home_line": -24.5,
            "home_ask": 0.50,
            "away_ask": 0.52,
            "slug": SP_SLUG,
            "sportsbook": sportsbook,
            "lineage": lineage,
        },
        total={
            "line": 50.5,
            "over_ask": 0.50,
            "under_ask": 0.52,
            "slug": TOT_SLUG,
            "sportsbook": sportsbook,
            "lineage": lineage,
        },
        home_team_id=HOME_ID,
        away_team_id=AWAY_ID,
    )


def _price(v7_model, events, pricer, **kw):
    return price_challenger_slate(
        PROJECT_ROOT,
        DECISION,
        events,
        markets=("moneyline", "spread", "total"),
        model=v7_model,
        slate_pricer=pricer,
        **kw,
    )


def test_v7_prices_through_price_game_with_its_own_identity(v7_model, data) -> None:
    contracts, summary = _price(v7_model, [_quotes()], _seam(data))
    assert summary["status"] == "ok" and summary["priced"] == 1
    assert {c["market_type"] for c in contracts} == {"moneyline", "spread", "total"}
    ml = next(c for c in contracts if c["market_type"] == "moneyline")
    # Parity fixture case: home win 0.938 against a home ask of 0.80.
    assert ml["selection"] == "home" and ml["model_probability"] == pytest.approx(0.938, abs=0.001)
    assert ml["edge_vs_executable_ask"] == pytest.approx(ml["model_probability"] - 0.80, abs=1e-6)
    for c in contracts:
        assert c["model_version"] == V7 and c["model_artifact_hash"] == v7_model.artifact_hash
        assert c["code_revision"] == v7_serving_code_hash() != v62_serving_code_hash()
        assert c["sportsbook"] == "polymarket_us" and c["observed_at_utc"] == QUOTE_AT
        assert c["edge_vs_executable_ask"] > 0
        assert c["feature_basis"]["season"] == SEASON
        assert c["feature_basis"]["live_data"] == {"source": "parity_fixture"}
    assert ml["market_slug"] == ML_SLUG  # the contract, never "<contract>-home"


def test_v7_prices_only_a_captured_book_seen_before_the_decision(v7_model, data) -> None:
    loader = _seam(data)
    for quotes in (
        _quotes(sportsbook="polymarket_us_indicative"),
        _quotes(sportsbook="espn_consensus"),
        _quotes(observed="2023-11-11T12:00:01+00:00"),
    ):
        contracts, summary = _price(v7_model, [quotes], loader)
        assert contracts == [] and summary["no_calls"] == {"no_captured_quote": 1}


def test_v7_no_calls_fail_closed_and_write_nothing(v7_model, data) -> None:

    from model_prediction.models import cfb_structural_v7_challenger as routing

    # The live-data seam is not wired: the slate no-calls as a whole (forced, whatever the v7l branch supplies).
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(routing, "default_slate_pricer", lambda: None)
        contracts, summary = _price(v7_model, [_quotes()], None)
    assert (
        contracts == [] and summary["status"] == "no_call" and summary["reason"] == NO_CALL_LIVE_DATA_UNWIRED
    )

    contracts, summary = _price(
        v7_model, [_quotes("a"), _quotes("b")], _seam(data, slate_no_call=NO_CALL_NO_CFBD)
    )
    assert contracts == [] and summary["no_calls"] == {NO_CALL_NO_CFBD: 2}

    # Per game: the crawl is behind for one team (a completed game with no pbp row); the other game prices.
    lagging = EventQuotes(**{**_quotes("lag").__dict__, "home_team_id": "999"})
    behind = _seam(data, completed_games={"999": ["uncrawled-event"]})
    contracts, summary = _price(v7_model, [lagging, _quotes("ok")], behind)
    assert summary["no_calls"] == {NO_CALL_PBP_MISSING: 1} and summary["priced"] == 1
    assert {c["event_id"] for c in contracts} == {"ok"}

    # A seam answer missing a game is a decline, not a guess.
    contracts, summary = _price(v7_model, [_quotes()], lambda *_a, **_k: ([], {}))
    assert contracts == [] and summary["no_calls"] == {routing.NO_CALL_NO_RESULT: 1}

    no_id = EventQuotes(**{**_quotes("noid").__dict__, "away_team_id": None})
    contracts, summary = _price(v7_model, [no_id], _seam(data))
    assert contracts == [] and summary["no_calls"] == {core.NO_CALL_UNKNOWN_TEAM: 1}


def test_v7_refuses_a_model_that_is_not_the_registered_artifact(v7_model, data) -> None:
    with pytest.raises(ValueError, match="not the registered"):
        _price(v7_model, [_quotes()], _seam(data), expected_artifact_hash="0" * 64)
    registered = ProductionModelRegistry.load(PROJECT_ROOT).entries[V7].artifact_hash
    assert _price(v7_model, [_quotes()], _seam(data), expected_artifact_hash=registered)[0]


# ----------------------------------------------------------------------------------------------- end to end
def _v5_row() -> dict:
    return {
        "event_id": "v7e1",
        "event_start_utc": KICKOFF,
        "away_team": "Away U",
        "home_team": "Home U",
        "market_type": "total",
        "selection": "under",
        "line": 51.5,
        "executable_ask": 0.50,
        "model_probability": 0.56,
        "model_uncertainty": 0.05,
        "edge_vs_executable_ask": 0.06,
        "model_version": V5,
        "rationale": "fixture",
        "weather_inputs_verified": True,
        "sportsbook": "polymarket_us",
        "observed_at_utc": QUOTE_AT,
        "market_slug": "tsc-cfb-awayu-homeu-2023-11-11-51pt5",
        "feature_basis": {"is_fbs_vs_fcs": False, "projected_total": 46.0},
    }


def _run_forecast(tmp_path: Path, monkeypatch, contracts: list[dict]) -> tuple[dict, dict]:
    from model_prediction.cli import forecast
    from model_prediction.ledger import PickLedger
    from model_prediction.models import college_football

    fixture = {"status": "research", "model_code_hash": "fixture", "priced_contracts": contracts}
    monkeypatch.setattr(college_football, "build_cfb_slate", lambda **_kwargs: fixture)
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
            "models": {"NCAAF": {"min_edge": 0.005, "status": "shadow_qualified"}},
            "project": {"maximum_data_age_hours": 24},
            "bankroll": {},
        },
        research_ledger=ledgers["research"],
        gated_ledger=ledgers["gated"],
        flat_ledger=ledgers["flat"],
        main_ledger=ledgers["main"],
        force=True,
    )
    return result, ledgers


def test_v5_main_v62_flat_and_v7_flat_rows_land_with_distinct_identities(
    tmp_path: Path, monkeypatch, v7_model, v62_model, data
) -> None:
    from model_prediction.cli.settle import _extract_market_slug
    from model_prediction.data_sources.polymarket_execute import _extract_market_slug as ticket_slug
    from model_prediction.ncaaf_prekickoff_capture import ncaaf_settlement_slug

    v62_rows = build_v62_contracts(
        v62_model,
        _quotes(),
        v6_projected_margin=10.0,
        v6_projected_total=60.0,
        is_fbs_vs_fcs=False,
        sample_games=5,
        anchor=Anchor("v7e1", 21.0, 51.0, -21.0, "2023-11-11T10:00:00+00:00", "DraftKings", "core_odds", "h"),
        decision_time=DECISION,
        markets=("moneyline", "spread", "total"),
    )
    v7_rows, _ = _price(v7_model, [_quotes()], _seam(data))
    assert v62_rows and v7_rows
    result, ledgers = _run_forecast(tmp_path, monkeypatch, [_v5_row(), *v62_rows, *v7_rows])
    assert result["errors"] == []

    main = ledgers["main"].rows()
    assert [r["model_version"] for r in main] == [V5]
    assert all(r["model_version"] == V5 for r in ledgers["gated"].rows())
    for tier in ("flat", "research"):
        rows = ledgers[tier].rows()
        by_model = {m: [r for r in rows if r["model_version"] == m] for m in (V5, V62, V7)}
        assert len(by_model[V5]) == 1, tier
        assert len(by_model[V62]) == len(v62_rows), tier  # v7 never displaces a v6.2 row
        assert len(by_model[V7]) == len(v7_rows), tier
        assert {r["model_artifact_hash"] for r in by_model[V62]} == {v62_model.artifact_hash}
        assert {r["model_artifact_hash"] for r in by_model[V7]} == {v7_model.artifact_hash}
        assert {r["code_revision"] for r in by_model[V62]} == {v62_serving_code_hash()}
        assert {r["code_revision"] for r in by_model[V7]} == {v7_serving_code_hash()}
        assert by_model[V5][0]["code_revision"] == "fixture"
        # Slug hygiene on the rows actually written: every reader gets the real contract slug.
        expected = {"moneyline": ML_SLUG, "spread": SP_SLUG, "total": TOT_SLUG}
        for row in by_model[V7]:
            slug = expected[row["market_type"]]
            assert _extract_market_slug(row["rationale"], row) == slug
            assert (
                ncaaf_settlement_slug(_extract_market_slug(row["rationale"], row), row["rationale"]) == slug
            )
            assert ticket_slug(row["rationale"]) == slug


def test_v7_no_call_leaves_v5_and_v62_ledger_rows_identical(
    tmp_path: Path, monkeypatch, v7_model, v62_model, data
) -> None:
    """A no-calling v7 contributes no contract, so the forecast writes exactly what it wrote without v7."""
    v7_rows, summary = _price(v7_model, [_quotes()], _seam(data, slate_no_call=NO_CALL_PBP_MISSING))
    assert v7_rows == [] and summary["no_calls"] == {NO_CALL_PBP_MISSING: 1}
    v62_rows = build_v62_contracts(
        v62_model,
        _quotes(),
        v6_projected_margin=10.0,
        v6_projected_total=60.0,
        is_fbs_vs_fcs=False,
        sample_games=5,
        anchor=None,
        decision_time=DECISION,
        markets=("moneyline", "spread", "total"),
    )
    assert v62_rows

    def strip(rows: list[dict]) -> list[dict]:
        drop = {"pick_id", "created_at_utc", "operation_id", "updated_at_utc", "audit_event_id"}
        return [{k: v for k, v in r.items() if k not in drop} for r in rows]

    base = [_v5_row(), *v62_rows]
    _, without = _run_forecast(tmp_path / "a", monkeypatch, base)
    _, with_v7 = _run_forecast(tmp_path / "b", monkeypatch, [*base, *v7_rows])
    for tier in ("research", "gated", "flat", "main"):
        assert json.dumps(strip(with_v7[tier].rows()), sort_keys=True) == json.dumps(
            strip(without[tier].rows()), sort_keys=True
        ), tier


# ----------------------------------------------------------------------------------------------- slate hook
def _snapshot_row(slug: str, market_type: str, long_ask: float, short_ask: float, **extra) -> dict:
    return {
        "provider": "polymarket_us",
        "reconstructed": False,
        "usage": "prospective_executable_bbo",
        "timestamp_valid": True,
        "market_state": "MARKET_STATE_OPEN",
        "market_slug": slug,
        "market_type": market_type,
        "event_title": "Alabama Crimson Tide vs. Georgia Bulldogs",
        "team": "Alabama Crimson Tide",
        "event_start_utc": KICKOFF,
        "observed_at_utc": "2023-11-11T11:00:00Z",
        "long": {"ask": long_ask, "bid": long_ask - 0.02},
        "short": {"ask": short_ask, "bid": short_ask - 0.02},
        **extra,
    }


def _slate(tmp_path: Path, monkeypatch, routes: dict, v62_fake, v7_pricer):
    """build_cfb_slate over one ESPN event with captured Polymarket books, champion faked, routes pinned."""
    from model_prediction.models import cfb_structural_v7_challenger, cfb_structural_v62, college_football

    path = tmp_path / "odds" / "ncaaf" / "2023-11-11" / "polymarket_snapshots.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        _snapshot_row(ML_SLUG, "moneyline", 0.22, 0.80),
        _snapshot_row(SP_SLUG, "spread", 0.52, 0.50, line=24.5),
        _snapshot_row(TOT_SLUG, "total", 0.50, 0.52, line=50.5),
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    event = {
        "id": "v7e1",
        "date": KICKOFF,
        "season": {"year": SEASON, "type": 2},
        "week": {"number": 11},
        "competitions": [
            {
                "neutralSite": False,
                "competitors": [
                    {"homeAway": "home", "team": {"id": HOME_ID, "displayName": "Georgia Bulldogs"}},
                    {"homeAway": "away", "team": {"id": AWAY_ID, "displayName": "Alabama Crimson Tide"}},
                ],
                "odds": [],
            }
        ],
    }
    client = SimpleNamespace(scoreboard=lambda *_a, **_k: {"events": [event]})
    monkeypatch.setattr(college_football, "_load_cfb_history", lambda *_a, **_k: [])
    monkeypatch.setattr(
        "model_prediction.features.multi_sport_weather.resolve_cfb_game_weather",
        lambda *_a, **_k: (70.0, 0.0, 0.0, True),
    )

    def predict(_self, _history, _game, **_kwargs):
        return [
            SimpleNamespace(
                market_type="moneyline",
                probabilities={"home": 0.6, "away": 0.4},
                uncertainty=0.1,
                model_version="cfb-structural-v4",
                feature_basis={},
                inference_inputs={},
                rationale="fixture",
            )
        ]

    monkeypatch.setattr(college_football.CFBStructuralV4Model, "predict_matchup", predict)
    monkeypatch.setattr(college_football, "_ncaaf_challenger_routes", lambda: routes)
    monkeypatch.setattr(cfb_structural_v62, "price_challenger_slate", v62_fake)
    monkeypatch.setattr(cfb_structural_v7_challenger, "default_slate_pricer", lambda: v7_pricer)
    return college_football.build_cfb_slate(
        tmp_path, "2023-11-11", client=client, observed_at=DECISION, price_challenger=True
    )


def test_slate_hook_runs_each_challenger_in_route_order_and_isolates_v7(tmp_path, monkeypatch, data) -> None:

    v7_hash = ProductionModelRegistry.load(PROJECT_ROOT).entries[V7].artifact_hash
    both = {V62: (["moneyline", "spread", "total"], None), V7: (["moneyline", "spread", "total"], v7_hash)}
    only_v62 = {V62: both[V62]}
    seen: list = []
    sentinel = {"model_version": V62, "event_id": "v7e1", "market_type": "total"}

    def v62_fake(root, obs, events):
        seen.append(events)
        return [sentinel], {"status": "ok", "model_version": V62}

    no_call = _seam(data, slate_no_call=NO_CALL_PBP_MISSING)

    def dumps(slate: dict) -> str:
        return json.dumps(slate["priced_contracts"], sort_keys=True, default=str)

    baseline = _slate(tmp_path, monkeypatch, only_v62, v62_fake, None)
    assert baseline["priced_contracts"][-1] == sentinel
    assert baseline["challenger_pricing"] == {"status": "ok", "model_version": V62}
    champion = [c for c in baseline["priced_contracts"] if c.get("model_version") != V62]
    assert champion, "the faked champion must price the captured moneyline"

    declined = _slate(tmp_path, monkeypatch, both, v62_fake, no_call)
    assert dumps(declined) == dumps(baseline)
    assert declined["challenger_pricing_by_model"][V7]["no_calls"] == {NO_CALL_PBP_MISSING: 1}

    unwired = _slate(tmp_path, monkeypatch, both, v62_fake, None)
    assert dumps(unwired) == dumps(baseline)
    assert unwired["challenger_pricing_by_model"][V7]["reason"] == NO_CALL_LIVE_DATA_UNWIRED

    def boom(*_a, **_k):
        raise RuntimeError("v7 live data down")

    failed = _slate(tmp_path, monkeypatch, both, v62_fake, boom)
    assert dumps(failed) == dumps(baseline)
    assert failed["challenger_pricing_by_model"][V7]["status"] == "error"
    assert failed["challenger_pricing"]["status"] == "ok"  # v6.2's summary, untouched

    priced = _slate(tmp_path, monkeypatch, both, v62_fake, _seam(data))
    contracts = priced["priced_contracts"]
    n = len(baseline["priced_contracts"])
    assert dumps({"priced_contracts": contracts[:n]}) == dumps(baseline)  # v5 then v6.2, unchanged
    v7_rows = contracts[n:]
    assert v7_rows and {c["model_version"] for c in v7_rows} == {V7}
    assert {c["market_slug"] for c in v7_rows} <= {ML_SLUG, SP_SLUG, TOT_SLUG}
    assert all(c["feature_basis"]["season"] == SEASON for c in v7_rows)
    # v6.2 received exactly the event dicts it always did (no v7-only keys: EventQuotes(**event) must not raise).
    for events in seen:
        assert set(events[0]) == set(EventQuotes.__dataclass_fields__)
        EventQuotes(**events[0])

    def v62_boom(*_a):
        raise RuntimeError("v6.2 down")

    v62_failed = _slate(tmp_path, monkeypatch, both, v62_boom, _seam(data))
    assert v62_failed["challenger_pricing"]["status"] == "error"
    assert [c for c in v62_failed["priced_contracts"] if c.get("model_version") == V7]
    assert dumps({"priced_contracts": v62_failed["priced_contracts"][: len(champion)]}) == dumps(
        {"priced_contracts": champion}
    )
