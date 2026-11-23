"""cfb-structural-v5.1 paper challenger: conference fix, in-path scoring proxy, train/serve parity, margin pmf,
max-edge sides, fail-closed serving, Flat-only routing and auditable rows (quick-261122-v51)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from model_prediction.config import PROJECT_ROOT
from model_prediction.features.cfb_conference import (
    ConferenceMembership,
    lagged_scoring_proxies,
    proxy_value,
)
from model_prediction.features.cfb_features import CFBFeatureExtractor, CFBGameRecord
from model_prediction.features.cfb_features_v51 import CFBFeatureExtractorV51
from model_prediction.models import cfb_structural_v51 as v51
from model_prediction.models.cfb_structural_v51 import (
    CFB_V51_MODEL_VERSION,
    NO_CALL_CONFERENCE_SEASON_MISSING,
    NO_CALL_HISTORY_STALE,
    NO_CALL_NO_SCHEDULE,
    NO_CALL_UNKNOWN_CLASS,
    CFBStructuralV51Model,
    V51Params,
    V51Pricing,
    build_contracts,
    margin_pmf,
    market_probabilities,
    price_challenger_slate,
    price_features,
    training_features,
)
from model_prediction.models.cfb_structural_v62 import EventQuotes
from model_prediction.models.college_football import UpcomingCFBGame
from tests.test_cfb_serving_parity import nb_rollback_model

V5 = "cfb-structural-v5"
BOISE, WSU, BAMA, UGA, AUB, LSU = "68", "265", "333", "61", "2", "99"
NAMES = {
    BOISE: "Boise State Broncos",
    WSU: "Washington State Cougars",
    BAMA: "Alabama Crimson Tide",
    UGA: "Georgia Bulldogs",
    AUB: "Auburn Tigers",
    LSU: "LSU Tigers",
}
KICKOFF = "2026-11-07T20:00:00Z"
DECISION = datetime(2026, 11, 7, 15, 0, tzinfo=UTC)
QUOTE_AT = "2026-11-07T14:30:00+00:00"
ML_SLUG = "aec-cfb-boise-wsu-2026-11-07"
SP_SLUG = "asc-cfb-boise-wsu-2026-11-07-6pt5"
TOT_SLUG = "tsc-cfb-boise-wsu-2026-11-07-52pt5"


def _table() -> dict:
    def entry(conf: str, cls: str = "fbs") -> dict:
        return {"conference": conf, "classification": cls}

    sec = {t: entry("SEC") for t in (BAMA, UGA, AUB, LSU)}
    return {
        "schema": "cfb-conference-membership-v1",
        "independent_conferences": ["FBS Independents"],
        "seasons": {
            "2025": {
                BOISE: entry("Mountain West"),
                WSU: entry("Pac-12"),
                **sec,
                "9": entry("Big Sky", "fcs"),
            },
            "2026": {
                BOISE: entry("Pac-12"),
                WSU: entry("Pac-12"),
                **sec,
                "9": entry("Big Sky", "fcs"),
                "10": entry("Big Sky", "fcs"),
                "87": entry("FBS Independents"),
                "41": entry("FBS Independents"),
                "777": entry("Pac-12"),
            },
        },
        "aliases": {name: tid for tid, name in NAMES.items()},
    }


@pytest.fixture
def membership() -> ConferenceMembership:
    return ConferenceMembership(_table())


def _params(**over) -> V51Params:
    p = {
        "margin_calibration": {"intercept": -0.5, "slope": 1.35, "neutral_intercept": 0.0},
        "total_calibration": {"intercept": -5.0, "slope": 1.1},
        "margin_sd_table": {"established": 17.0, "thin_sample": 17.1, "fbs_vs_fcs": 18.2},
        "established_min_sample": 6,
        "conference_margin_scale": 0.97,
        "total_sd": 17.2,
        "sd_widening": {
            "blowout_spread_threshold": 21.5,
            "blowout_margin_scale": 0.025,
            "blowout_cap_points": 15.0,
            "total_deviation_threshold": 8.0,
            "total_deviation_scale": 0.025,
            "total_deviation_cap_points": 20.0,
        },
        "floors": {"total_points": 24.0, "team_points": 3.0},
        "margin_pmf": {"max_margin": 120, "key_multipliers": {"0": 0.0}},
    }
    p.update(over)
    return V51Params.from_mapping(p)


def _model(membership: ConferenceMembership, *, epa_mode: str = "live_proxy", staleness: float = 14.0):
    return CFBStructuralV51Model(
        _params(),
        membership,
        epa_mode=epa_mode,
        home_advantage_points=2.5,
        max_history_staleness_days=staleness,
        artifact_hash="f" * 64,
    )


def _history(*, stored_epa: float | None = 5.0, end: str = "2026-11-01") -> list[CFBGameRecord]:
    """Two seasons of FBS games among six teams, with nonsense stored "EPA" fields the v5.1 path must not read."""
    teams = list(NAMES)
    rows: list[CFBGameRecord] = []
    n = 0
    for season, first in (
        (2025, datetime(2025, 9, 6, 19, tzinfo=UTC)),
        (2026, datetime(2026, 9, 5, 19, tzinfo=UTC)),
    ):
        for week in range(10):
            start = first + timedelta(days=7 * week)
            if start.strftime("%Y-%m-%d") > end:
                break
            order = teams[week % 6 :] + teams[: week % 6]
            for i in range(3):
                home, away = order[i], order[5 - i]
                n += 1
                rows.append(
                    CFBGameRecord(
                        event_id=f"e{n}",
                        event_start_utc=(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%MZ"),
                        league="NCAAF",
                        away_team=NAMES[away],
                        home_team=NAMES[home],
                        away_score=10 + (n * 7) % 31,
                        home_score=14 + (n * 11) % 29,
                        season_type="regular-season",
                        season_year=season,
                        home_team_id=home,
                        away_team_id=away,
                        feed_groups=(80,),
                        home_epa_per_play=stored_epa,
                        away_epa_per_play=None if stored_epa is None else -stored_epa,
                    )
                )
    return sorted(rows, key=lambda r: r.start)


def _game(**over) -> UpcomingCFBGame:
    base = {
        "event_id": "g1",
        "event_start_utc": KICKOFF,
        "away_team": NAMES[BOISE],
        "home_team": NAMES[WSU],
        "is_neutral_site": False,
        "season_year": 2026,
        "week": 10,
        "away_team_id": BOISE,
        "home_team_id": WSU,
        "spread_home_line": -3.0,  # v5's effective line: v5.1 must ignore it and price the captured book's
        "total_line": 48.0,
    }
    base.update(over)
    return UpcomingCFBGame(**base)


def _lineage(at: str = QUOTE_AT) -> dict:
    return {"market_quote_observed_at_utc": at, "market_snapshot_hash": "h" * 64}


def _quotes(*, sportsbook: str = "polymarket_us", at: str = QUOTE_AT, home_line: float = -6.5) -> EventQuotes:
    return EventQuotes(
        event_id="g1",
        event_start_utc=KICKOFF,
        away_team=NAMES[BOISE],
        home_team=NAMES[WSU],
        is_neutral_site=False,
        moneyline={"home_ask": 0.62, "away_ask": 0.40, "slug_base": ML_SLUG, "sportsbook": sportsbook,
                   "lineage": _lineage(at)},
        spread={"home_line": home_line, "home_ask": 0.52, "away_ask": 0.50, "slug": SP_SLUG, "sportsbook": sportsbook,
                "lineage": _lineage(at)},
        total={"line": 52.5, "over_ask": 0.51, "under_ask": 0.51, "slug": TOT_SLUG, "sportsbook": sportsbook,
               "lineage": _lineage(at)},
        home_team_id=WSU,
        away_team_id=BOISE,
    )  # fmt: skip


# ----------------------------------------------------------------------------------------------- conference
def test_conference_flag_is_season_keyed_and_fails_closed(membership) -> None:
    kw = {"home_name": NAMES[WSU], "away_name": NAMES[BOISE]}
    assert membership.is_conference_matchup(2026, **kw).flag is True  # rebuilt Pac-12
    assert membership.is_conference_matchup(2025, **kw).flag is False  # Boise still in the MWC
    missing = membership.is_conference_matchup(2027, **kw)
    assert (missing.flag, missing.resolved, missing.reason) == (False, False, "season_missing")
    unknown = membership.is_conference_matchup(2026, home_name="Nowhere Tech", away_name=NAMES[BOISE])
    assert (unknown.flag, unknown.resolved, unknown.reason) == (False, False, "home_id_unresolved")
    # an ESPN id the event gives wins over the alias; FCS and independents are never a conference game
    assert membership.is_conference_matchup(2026, home_name="x", away_name="y", home_team_id="777",
                                            away_team_id=BOISE).flag is True  # fmt: skip
    assert membership.is_conference_matchup(2026, home_name="x", away_name="y", home_team_id="9",
                                            away_team_id="10").flag is False  # fmt: skip
    assert membership.is_conference_matchup(2026, home_name="x", away_name="y", home_team_id="87",
                                            away_team_id="41").flag is False  # fmt: skip


def test_real_membership_table_covers_the_2026_realignment() -> None:
    real = ConferenceMembership.load()
    cases = [
        (2026, "68", "265", True),  # Boise State @ Washington State, rebuilt Pac-12
        (2025, "68", "265", False),
        (2026, "2459", "2439", True),  # Northern Illinois @ UNLV, MWC
        (2026, "2348", "6", True),  # Louisiana Tech @ South Alabama, Sun Belt
        (2026, "2229", "2623", True),  # FIU @ Missouri State, both missing from the static table
        (2023, "251", "333", False),  # Texas @ Alabama before the move
        (2024, "251", "333", True),
    ]
    for season, away, home, expected in cases:
        res = real.is_conference_matchup(
            season, home_name="", away_name="", home_team_id=home, away_team_id=away
        )
        assert res.resolved and res.flag is expected, (season, away, home)
    assert real.seasons[0] <= 2016 and real.seasons[-1] >= 2026
    assert real.team_id("Hawaii Rainbow Warriors") == "62"


def test_v51_extractor_reads_the_season_table_not_the_static_one(membership) -> None:
    """Regression for models.md finding 3: the static table calls Boise State @ Washington State non-conference."""
    history = _history()
    game = _game()
    v5 = training_features(CFBFeatureExtractor(), history, game, season=2026, week=10, neutral=False)
    fixed = training_features(
        CFBFeatureExtractorV51(membership), history, game, season=2026, week=10, neutral=False
    )
    assert v5.is_conference_matchup is False
    assert fixed.is_conference_matchup is True


# ----------------------------------------------------------------------------------------------- scoring proxy
def test_lagged_scoring_proxy_is_a_pure_function_of_earlier_scores() -> None:
    rows = [
        ({"event_id": "a", "home_score": 30, "away_score": 20}, "H", "A"),
        ({"event_id": "b", "home_score": 42, "away_score": 7}, "A", "C"),
        ({"event_id": "c", "home_score": 14, "away_score": 21}, "H", "A"),
    ]
    out = lagged_scoring_proxies(rows)
    assert out["a"] == (None, None)
    assert out["b"] == (pytest.approx(proxy_value([20])), None)
    assert out["c"] == (pytest.approx(proxy_value([30])), pytest.approx(proxy_value([20, 42])))
    assert proxy_value([28.0]) == 0.0


@pytest.mark.parametrize("epa_mode", ["live_proxy", "dropped"])
def test_v51_features_ignore_the_stored_epa_fields(membership, epa_mode) -> None:
    """Regression for models.md finding 4: stripping or corrupting the stored fields changes nothing in v5.1."""
    game = _game()
    ex = CFBFeatureExtractorV51(membership, epa_mode=epa_mode)
    with_junk = training_features(ex, _history(stored_epa=5.0), game, season=2026, week=10, neutral=False)
    stripped = training_features(ex, _history(stored_epa=None), game, season=2026, week=10, neutral=False)
    assert with_junk == stripped
    # The same check on v5's extractor proves the fixture can see a stored-field reader.
    v5_junk = training_features(CFBFeatureExtractor(), _history(stored_epa=5.0), game, season=2026, week=10,
                                neutral=False)  # fmt: skip
    v5_none = training_features(CFBFeatureExtractor(), _history(stored_epa=None), game, season=2026, week=10,
                                neutral=False)  # fmt: skip
    assert v5_junk.home_epa_net != v5_none.home_epa_net


def test_live_proxy_and_dropped_channel_differ(membership) -> None:
    game = _game()
    live = training_features(CFBFeatureExtractorV51(membership, epa_mode="live_proxy"), _history(), game,
                             season=2026, week=10, neutral=False)  # fmt: skip
    drop = training_features(CFBFeatureExtractorV51(membership, epa_mode="dropped"), _history(), game,
                             season=2026, week=10, neutral=False)  # fmt: skip
    assert live.home_epa_net != drop.home_epa_net


# ----------------------------------------------------------------------------------------------- parity
@pytest.mark.parametrize("epa_mode", ["live_proxy", "dropped"])
def test_train_serve_parity_for_v51_features(membership, epa_mode) -> None:
    """The fit harness's feature call and the served contract agree exactly, for every new or changed input."""
    model = _model(membership, epa_mode=epa_mode)
    history = _history()
    game = _game()
    past = [g for g in history if g.start < datetime(2026, 11, 7, 20, tzinfo=UTC)]
    trained = model.feature_dict(
        training_features(model.extractor, past, game, season=2026, week=10, neutral=False)
    )
    contracts, summary = price_challenger_slate(
        PROJECT_ROOT, DECISION, [_quotes()], games={"g1": game}, markets=["moneyline", "spread", "total"],
        model=model, history=history,
    )  # fmt: skip
    assert summary["status"] == "ok" and summary["priced"] == 1 and contracts
    for c in contracts:
        basis = c["feature_basis"]
        for key, value in trained.items():
            assert basis[key] == value, key
        assert basis["conference"]["is_conference_matchup"] is True
        assert basis["is_conference_matchup"] is True
        assert basis["epa_mode"] == epa_mode
    pricing = price_features(model.params, trained, spread_home_line=-6.5, total_line=52.5)
    probs = {
        "moneyline": pricing.probabilities,
        "spread": pricing.probabilities,
        "total": pricing.probabilities,
    }
    key = {("moneyline", "home"): "home_win", ("moneyline", "away"): "away_win", ("spread", "home"): "home_cover",
           ("spread", "away"): "away_cover", ("total", "over"): "over", ("total", "under"): "under"}  # fmt: skip
    for c in contracts:
        assert c["model_probability"] == round(
            probs[c["market_type"]][key[(c["market_type"], c["selection"])]], 6
        )


def test_history_is_cut_at_the_decision_not_the_game_day(membership) -> None:
    """models.md finding 9: a row that started after the decision cannot shape a decision made before it."""
    model = _model(membership)
    late = replace(_history()[-1], event_id="late", event_start_utc="2026-11-07T16:00Z")
    base_history = _history()
    _, summary = price_challenger_slate(
        PROJECT_ROOT, DECISION, [_quotes()], games={"g1": _game()}, markets=["moneyline"], model=model,
        history=[*base_history, late],
    )  # fmt: skip
    assert summary["history_games"] == len(base_history)


# ----------------------------------------------------------------------------------------------- margin pmf
def test_margin_pmf_has_no_tie_and_prices_whole_line_pushes() -> None:
    params = _params()
    pmf = margin_pmf(params, 2.0, 16.0)
    k = np.arange(-120, 121)
    assert pmf.sum() == pytest.approx(1.0) and pmf[k == 0][0] == 0.0
    pr = market_probabilities(params, mu_margin=2.0, sd_margin=16.0, mu_total=50.0, sd_total=17.0,
                              spread_home_line=-3.0, total_line=50.5)  # fmt: skip
    assert pr["spread_push"] == pytest.approx(float(pmf[k == 3][0]))
    assert pr["home_cover"] + pr["away_cover"] + pr["spread_push"] == pytest.approx(1.0)
    assert pr["home_win"] == pytest.approx(float(pmf[k > 0].sum()))
    keyed = _params(margin_pmf={"max_margin": 120, "key_multipliers": {"0": 0.0, "3": 2.0, "7": 1.8}})
    pr_keyed = market_probabilities(keyed, mu_margin=2.0, sd_margin=16.0, mu_total=50.0, sd_total=17.0,
                                    spread_home_line=-3.0, total_line=50.5)  # fmt: skip
    assert pr_keyed["spread_push"] > 1.8 * pr["spread_push"]


def test_params_refuse_a_tie_mass_and_bad_values() -> None:
    with pytest.raises(ValueError, match="tie"):
        _params(margin_pmf={"max_margin": 120, "key_multipliers": {"0": 1.0}})
    with pytest.raises(ValueError, match="slopes"):
        _params(margin_calibration={"intercept": 0.0, "slope": -1.0})


# ----------------------------------------------------------------------------------------------- side selection
def _fixed_pricing(monkeypatch, **probs) -> None:
    full = {"home_win": 0.5, "away_win": 0.5, "home_cover": 0.5, "away_cover": 0.5, "spread_push": 0.0,
            "over": 0.5, "under": 0.5, "total_push": 0.0, **probs}  # fmt: skip
    monkeypatch.setattr(
        v51, "price_features",
        lambda *_a, **_k: V51Pricing(1.0, 50.0, 17.0, 17.0, "established", -6.5, 52.5, full),
    )  # fmt: skip


def _build(model, quotes=None, markets=("moneyline", "spread", "total")):
    return build_contracts(model, quotes or _quotes(), {"x": 1}, {"is_conference_matchup": True},
                           decision_time=DECISION, markets=markets, uncertainty=0.07)  # fmt: skip


def test_sides_are_chosen_by_edge(monkeypatch, membership) -> None:
    """models.md findings 1-2: v5 logs the key-window underdog and the higher-probability moneyline side even at
    negative edge. v5.1 takes the larger edge: here the -6.5 favourite (+3.0%) and the moneyline dog (+2.0%)."""
    _fixed_pricing(monkeypatch, home_win=0.58, away_win=0.42, home_cover=0.55, away_cover=0.45, over=0.53,
                   under=0.47)  # fmt: skip
    rows = {c["market_type"]: c for c in _build(_model(membership))}
    assert (rows["spread"]["selection"], rows["spread"]["line"]) == ("home", -6.5)
    assert rows["spread"]["edge_vs_executable_ask"] == pytest.approx(0.03)
    assert rows["moneyline"]["selection"] == "away" and rows["moneyline"]["edge_vs_executable_ask"] > 0
    assert rows["total"]["selection"] == "over"
    assert (
        rows["moneyline"]["market_slug"] == ML_SLUG
        and f"market_slug={ML_SLUG}." in rows["moneyline"]["rationale"]
    )
    for c in rows.values():
        assert c["model_version"] == CFB_V51_MODEL_VERSION and c["rationale"].startswith(
            "CFB Structural v5.1"
        )
        assert c["model_uncertainty"] == 0.07 and c["snapshot_mode"] == "feature_capture"
        assert c["inference_inputs"] is None and c["sportsbook"] == "polymarket_us"


def test_zero_and_negative_edges_emit_nothing(monkeypatch, membership) -> None:
    _fixed_pricing(monkeypatch, home_win=0.62 + 1e-12, away_win=0.38, home_cover=0.50, away_cover=0.48,
                   over=0.49, under=0.49)  # fmt: skip
    assert _build(_model(membership)) == []


def test_only_a_captured_book_seen_before_the_decision_is_priced(monkeypatch, membership) -> None:
    """pricing.md finding 3: never ESPN consensus or the indicative event list; never a quote seen later."""
    _fixed_pricing(monkeypatch, home_win=0.70, away_win=0.30)
    model = _model(membership)
    assert _build(model, _quotes(sportsbook="polymarket_us_indicative")) == []
    assert _build(model, _quotes(sportsbook="espn_consensus")) == []
    assert _build(model, _quotes(at="2026-11-07T15:30:00+00:00")) == []
    assert len(_build(model, markets=("moneyline",))) == 1


# ----------------------------------------------------------------------------------------------- fail closed
def _slate(model, *, games=None, history=None, quotes=None):
    return price_challenger_slate(
        PROJECT_ROOT, DECISION, [quotes or _quotes()], games={"g1": _game()} if games is None else games,
        markets=["moneyline", "spread", "total"], model=model, history=_history() if history is None else history,
    )  # fmt: skip


def test_no_calls_fail_closed(membership) -> None:
    model = _model(membership)
    contracts, s = _slate(model, games={"g1": _game(season_year=2027)})
    assert contracts == [] and s["no_calls"] == {NO_CALL_CONFERENCE_SEASON_MISSING: 1}
    contracts, s = _slate(model, games={})
    assert contracts == [] and s["no_calls"] == {NO_CALL_NO_SCHEDULE: 1}
    newcomer = _game(home_team="Nowhere Tech Owls", home_team_id="777")
    contracts, s = _slate(model, games={"g1": newcomer})
    assert contracts == [] and s["no_calls"] == {NO_CALL_UNKNOWN_CLASS: 1}
    contracts, s = _slate(model, history=_history(end="2026-10-01"))  # in season, ingest frozen 5 weeks
    assert contracts == [] and s["no_calls"] == {NO_CALL_HISTORY_STALE: 1}
    contracts, s = _slate(model, games={"g1": _game(event_start_utc="2026-11-07T14:00:00Z")})
    assert contracts == [] and s["no_calls"] == {"NO_CALL_V51_EVENT_STARTED": 1}
    contracts, s = _slate(model, quotes=_quotes(sportsbook="polymarket_us_indicative"))
    assert contracts == [] and s["no_calls"] == {"no_captured_quote": 1}
    assert _slate(model)[0]


def test_a_model_that_is_not_the_registered_artifact_is_refused(membership) -> None:
    with pytest.raises(ValueError, match="not the registered"):
        price_challenger_slate(PROJECT_ROOT, DECISION, [_quotes()], games={"g1": _game()}, markets=["moneyline"],
                               model=_model(membership), history=_history(), expected_artifact_hash="0" * 64)  # fmt: skip


# ----------------------------------------------------------------------------------------------- artifact
def test_real_artifact_loads_hash_verified_and_matches_the_registry() -> None:
    from model_prediction.production_registry import ProductionModelRegistry

    art = v51.load_cfb_structural_v51_artifact()
    model = CFBStructuralV51Model.from_artifact(art)
    entry = ProductionModelRegistry.load(PROJECT_ROOT).entries[CFB_V51_MODEL_VERSION]
    assert entry.load_error is None and entry.artifact_hash == model.artifact_hash
    assert entry.serving_status == "shadow"
    assert art["qualification"]["promotion_approved"] is False and art["serving"]["main"] is False
    assert model.epa_mode in ("live_proxy", "dropped")
    tampered = dict(art, conference_source={**art["conference_source"], "sha256": "0" * 64})
    with pytest.raises(ValueError, match="sha256"):
        CFBStructuralV51Model.from_artifact(tampered)


def test_v51_is_never_main_qualified_nor_bought() -> None:
    from model_prediction.portfolio.auto_executor import DEFAULT_WHITELIST_MODELS

    assert CFB_V51_MODEL_VERSION not in DEFAULT_WHITELIST_MODELS
    assert V5 in DEFAULT_WHITELIST_MODELS
    state = PROJECT_ROOT / "data" / "auto_buyer_state.json"
    if state.exists():
        assert CFB_V51_MODEL_VERSION not in json.loads(state.read_text(encoding="utf-8")).get(
            "whitelist_models", []
        )


# ----------------------------------------------------------------------------------------------- end to end
def test_v51_rows_land_in_flat_and_research_only_with_a_snapshot(
    tmp_path: Path, monkeypatch, membership
) -> None:
    from model_prediction.cli import forecast
    from model_prediction.cli.settle import _extract_market_slug
    from model_prediction.feature_capture import validate_decision_snapshot
    from model_prediction.ledger import PickLedger
    from model_prediction.models import college_football

    _fixed_pricing(monkeypatch, home_win=0.58, away_win=0.42, home_cover=0.55, away_cover=0.45, over=0.53,
                   under=0.47)  # fmt: skip
    contracts = _build(_model(membership))
    assert len(contracts) == 3
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
        args_date="2026-11-07",
        config={"models": {"NCAAF": {"min_edge": 0.005, "status": "shadow_qualified"}},
                "project": {"maximum_data_age_hours": 24}, "bankroll": {}},
        research_ledger=ledgers["research"], gated_ledger=ledgers["gated"], flat_ledger=ledgers["flat"],
        main_ledger=ledgers["main"],
    )  # fmt: skip
    assert result["errors"] == []
    assert ledgers["main"].rows() == [] and ledgers["gated"].rows() == []
    for tier in ("flat", "research"):
        rows = ledgers[tier].rows()
        assert len(rows) == 3 and {r["model_version"] for r in rows} == {CFB_V51_MODEL_VERSION}
        for row in rows:
            assert row["model_artifact_hash"] == "f" * 64
            assert row["code_revision"] == v51.v51_serving_code_hash()
            snap = json.loads(row["model_input_snapshot_json"])
            assert snap["schema"] == "feature-capture-v1" and snap["artifact_hash"] == "f" * 64
            assert snap["features"]["conference.is_conference_matchup"] is True
            validate_decision_snapshot(snap, row)
            # Flat treatment as v6.2/v7: a CALL with ledger-sized units, here with a real model uncertainty.
            assert row["decision"] == "CALL" and float(row["units"]) > 0, row["market_type"]
            expected = {"moneyline": ML_SLUG, "spread": SP_SLUG, "total": TOT_SLUG}[row["market_type"]]
            assert _extract_market_slug(row["rationale"], row) == expected


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
        "event_title": "Boise State Broncos vs. Washington State Cougars",
        "team": "Boise State Broncos",
        "event_start_utc": KICKOFF,
        "observed_at_utc": "2026-11-07T14:30:00Z",
        "long": {"ask": long_ask, "bid": long_ask - 0.01},
        "short": {"ask": short_ask, "bid": short_ask - 0.01},
        **extra,
    }


def _hook_slate(tmp_path: Path, monkeypatch, membership, routes: dict, v51_model=None):
    """build_cfb_slate over one ESPN event with captured books, the champion faked, routes pinned."""
    from model_prediction.models import cfb_structural_v62, college_football

    path = tmp_path / "odds" / "ncaaf" / "2026-11-07" / "polymarket_snapshots.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        _snapshot_row(ML_SLUG, "moneyline", 0.40, 0.62),
        _snapshot_row(SP_SLUG, "spread", 0.50, 0.52, line=6.5),
        _snapshot_row(TOT_SLUG, "total", 0.51, 0.51, line=52.5),
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    event = {
        "id": "g1",
        "date": KICKOFF,
        "season": {"year": 2026, "type": 2},
        "week": {"number": 10},
        "competitions": [
            {
                "neutralSite": False,
                "competitors": [
                    {"homeAway": "home", "team": {"id": WSU, "displayName": NAMES[WSU]}},
                    {"homeAway": "away", "team": {"id": BOISE, "displayName": NAMES[BOISE]}},
                ],
                "odds": [],
            }
        ],
    }
    client = SimpleNamespace(scoreboard=lambda *_a, **_k: {"events": [event]})
    history = _history()
    monkeypatch.setattr(college_football, "_load_cfb_history", lambda *_a, **_k: history)
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
                model_version=V5,
                feature_basis={},
                inference_inputs={},
                rationale="fixture",
            )
        ]

    seen: list = []
    sentinel = {"model_version": "cfb-structural-v6.2", "event_id": "g1", "market_type": "total"}

    def v62_fake(root, obs, events):
        seen.append(events)
        return [sentinel], {"status": "ok"}

    monkeypatch.setattr(college_football.CFBStructuralV4Model, "predict_matchup", predict)
    monkeypatch.setattr(college_football, "_ncaaf_challenger_routes", lambda: routes)
    monkeypatch.setattr(cfb_structural_v62, "price_challenger_slate", v62_fake)
    monkeypatch.setattr(v51, "cfb_structural_v51_model", lambda: v51_model or _model(membership))
    slate = college_football.build_cfb_slate(
        tmp_path,
        "2026-11-07",
        client=client,
        observed_at=DECISION,
        price_challenger=True,
        model=nb_rollback_model(),
    )
    return slate, seen, sentinel


def test_slate_hook_prices_v51_after_the_other_challengers_and_isolates_it(
    tmp_path, monkeypatch, membership
) -> None:
    from model_prediction.models.cfb_structural_v62 import EventQuotes as Quotes

    markets = ["moneyline", "spread", "total"]
    v62_only = {"cfb-structural-v6.2": (markets, None)}
    both = {**v62_only, CFB_V51_MODEL_VERSION: (markets, "f" * 64)}
    baseline, seen, sentinel = _hook_slate(tmp_path, monkeypatch, membership, v62_only)
    routed, seen2, _ = _hook_slate(tmp_path, monkeypatch, membership, both)
    n = len(baseline["priced_contracts"])
    assert baseline["priced_contracts"][-1] == sentinel
    assert json.dumps(routed["priced_contracts"][:n], sort_keys=True, default=str) == json.dumps(
        baseline["priced_contracts"], sort_keys=True, default=str
    )
    v51_rows = routed["priced_contracts"][n:]
    assert v51_rows and {c["model_version"] for c in v51_rows} == {CFB_V51_MODEL_VERSION}
    assert routed["challenger_pricing_by_model"][CFB_V51_MODEL_VERSION]["status"] == "ok"
    for c in v51_rows:
        assert c["feature_basis"]["season"] == 2026 and c["feature_basis"]["model_week"] == 10
        assert c["feature_basis"]["conference"]["is_conference_matchup"] is True
        assert c["market_slug"] in (ML_SLUG, SP_SLUG, TOT_SLUG)
        if c["market_type"] == "spread":  # the captured book's line, not the champion game's
            assert abs(c["line"]) == 6.5 and c["feature_basis"]["pricing_spread_home_line"] == -6.5
    # v6.2 still receives exactly its EventQuotes keys
    for events in seen + seen2:
        assert set(events[0]) == set(Quotes.__dataclass_fields__)
    # a model that is not the registered artifact prices nothing and leaves the rest untouched
    wrong = _model(membership)
    wrong.artifact_hash = "0" * 64
    failed, _, _ = _hook_slate(tmp_path, monkeypatch, membership, both, v51_model=wrong)
    assert failed["challenger_pricing_by_model"][CFB_V51_MODEL_VERSION]["status"] == "error"
    assert json.dumps(failed["priced_contracts"], sort_keys=True, default=str) == json.dumps(
        baseline["priced_contracts"], sort_keys=True, default=str
    )


def test_conference_table_pin_survives_line_endings(tmp_path: Path) -> None:
    """The artifact pins the table's content hash: an LF checkout (CI, macOS) and a CRLF one (Windows) both load."""
    art = v51.load_cfb_structural_v51_artifact()
    text = (PROJECT_ROOT / art["conference_source"]["path"]).read_text(encoding="utf-8")
    for name, body in (
        ("lf.json", text.replace("\r\n", "\n")),
        ("crlf.json", text.replace("\r\n", "\n").replace("\n", "\r\n")),
    ):
        path = tmp_path / name
        path.write_bytes(body.encode("utf-8"))
        assert ConferenceMembership.load(path, expected_sha256=art["conference_source"]["sha256"]).has_season(
            2026
        )
