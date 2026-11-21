"""NBA serving plumbing (NBA review 2026-11-20, section 3: B2, B3, B10, start-time compare).

Team strings and quote shapes are copied from the live 2026-11-19 Polymarket capture
(LAC @ SA, `asc-nba-lac-sa-2026-11-19-*`) and ESPN's NBA displayNames.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from model_prediction import calibration_pipeline
from model_prediction.cli import forecast as forecast_cli
from model_prediction.features import nba_player_availability
from model_prediction.features.base import GameRecord
from model_prediction.learned_forward import _team_matches
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.nba_v9 import NBASpreadModelV9, NBATotalModelV9

NOW = datetime(2026, 11, 19, 20, 0, tzinfo=UTC)
TIP_ESPN = "2026-11-20T01:00Z"  # ESPN scoreboard form
TIP_POLY = "2026-11-20T01:00:00Z"  # Polymarket snapshot form
OBSERVED = (NOW - timedelta(seconds=60)).isoformat().replace("+00:00", "Z")


def _event(event_id: str, away: str, home: str, date: str = TIP_ESPN) -> dict:
    return {
        "id": event_id,
        "date": date,
        "competitions": [
            {
                "competitors": [
                    {"homeAway": "away", "team": {"displayName": away}},
                    {"homeAway": "home", "team": {"displayName": home}},
                ]
            }
        ],
    }


def _spread_row(team: str, line: float = 2.5, start: str = TIP_POLY, slug: str | None = None) -> dict:
    return {
        "event_id": "236700",
        "event_slug": "nba-lac-sa-2026-11-19",
        "event_start_utc": start,
        "event_title": "LA Clippers vs. SA Spurs",
        "league": "NBA",
        "line": line,
        "long": {"ask": 0.50, "bid": 0.48, "midpoint": 0.49, "description": f"+{line:.2f}"},
        "short": {"ask": 0.52, "bid": 0.50, "midpoint": 0.51, "description": f"-{line:.2f}"},
        "market_slug": slug or f"asc-nba-lac-sa-2026-11-19-pos-{line:g}".replace(".", "pt"),
        "market_type": "spread",
        "observed_at_utc": OBSERVED,
        "provider": "polymarket_us",
        "team": team,
        "timestamp_valid": True,
        "usage": "prospective_executable_bbo",
    }


def _total_row(line: float = 197.5) -> dict:
    return {
        "event_id": "236700",
        "event_slug": "nba-lac-sa-2026-11-19",
        "event_start_utc": TIP_POLY,
        "event_title": "LA Clippers vs. SA Spurs",
        "league": "NBA",
        "line": line,
        "long": {"ask": 0.50, "bid": 0.47, "description": "Over"},
        "short": {"ask": 0.53, "bid": 0.50, "description": "Under"},
        "market_slug": f"tsc-nba-lac-sa-2026-11-19-{line:g}".replace(".", "pt"),
        "market_type": "total",
        "observed_at_utc": OBSERVED,
        "provider": "polymarket_us",
        "team": None,
        "timestamp_valid": True,
        "usage": "prospective_executable_bbo",
    }


def _game(event_id: str, start: str, away: str, home: str, away_score: int, home_score: int) -> GameRecord:
    return GameRecord(
        event_id=event_id,
        event_start_utc=start,
        league="NBA",
        away_team=away,
        home_team=home,
        away_score=away_score,
        home_score=home_score,
        season_type="regular-season",
    )


CLIPPERS, SPURS, NUGGETS = "LA Clippers", "San Antonio Spurs", "Denver Nuggets"
HISTORY = [
    # 2025-26 season: carried-over rows that must not count as current-season games.
    _game("p1", "2026-03-01T00:00:00Z", NUGGETS, CLIPPERS, 110, 112),
    _game("p2", "2026-03-05T02:00:00Z", CLIPPERS, SPURS, 105, 101),
    _game("p3", "2026-04-10T00:00:00Z", SPURS, NUGGETS, 99, 108),
    _game("p4", "2026-04-12T02:30:00Z", NUGGETS, CLIPPERS, 100, 104),
    # 2026-27 season.
    _game("c1", "2026-10-25T02:30:00Z", CLIPPERS, NUGGETS, 101, 99),
    _game("s1", "2026-10-26T00:00:00Z", NUGGETS, SPURS, 100, 105),
    _game("c2", "2026-11-15T03:00:00Z", NUGGETS, CLIPPERS, 115, 110),
    _game("s2", "2026-11-17T01:00:00Z", SPURS, NUGGETS, 112, 109),
    # Clippers played last night: 21.5 h before tip, a back-to-back.
    _game("c3", "2026-11-19T03:30:00Z", NUGGETS, CLIPPERS, 108, 111),
]
AVAILABILITY = {"availability_points_gap": 2.0, "availability_total_loss": 6.0}


def _patch_slate(monkeypatch, *, market: str, rows: list[dict], events: list[dict]) -> None:
    champion = SimpleNamespace(
        model_id=f"nba-{market}-margin-v9", available=True, evidence_status="predictively_qualified"
    )
    monkeypatch.setattr(forecast_cli, "utc_now", lambda: NOW)
    monkeypatch.setattr(
        forecast_cli.ProductionModelRegistry,
        "load",
        lambda root: SimpleNamespace(champion=lambda *args: champion),
    )
    monkeypatch.setattr(forecast_cli.FeatureStore, "games_before", lambda *args: list(HISTORY))
    monkeypatch.setattr(forecast_cli, "_read_polymarket_snapshot_rows", lambda path: [dict(r) for r in rows])
    monkeypatch.setattr(
        nba_player_availability,
        "compute_nba_matchup_availability",
        lambda *args, **kwargs: dict(AVAILABILITY),
    )
    monkeypatch.setattr(
        calibration_pipeline, "load_serving_calibrator", lambda *args, **kwargs: (None, {"status": "test"})
    )
    monkeypatch.setattr(
        forecast_cli, "ESPNClient", lambda: SimpleNamespace(scoreboard=lambda *args: {"events": events})
    )


def _client(events: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(scoreboard=lambda *args: {"events": events})


# --------------------------------------------------------------------------- B3


def test_shared_team_matcher_is_not_loosened():
    """The fix is NBA-scoped; the shared matcher keeps refusing the abbreviation."""
    assert _team_matches("LA Clippers", "Los Angeles Clippers") is False


@pytest.mark.parametrize(
    ("team", "side", "expected"),
    [
        ("LA Clippers", "Los Angeles Clippers", True),
        ("Los Angeles Clippers", "LA Clippers", True),
        ("LA Clippers", "Clippers", True),
        ("Los Angeles Lakers", "Los Angeles Lakers", True),
        ("LA Lakers", "Los Angeles Lakers", True),
        ("LA Clippers", "Los Angeles Lakers", False),
        ("Los Angeles Lakers", "Los Angeles Clippers", False),
        ("Los Angeles Lakers", "LA Clippers", False),
        ("San Antonio Spurs", "Los Angeles Clippers", False),
    ],
)
def test_nba_team_alias_matches_only_the_same_franchise(team, side, expected):
    assert forecast_cli._nba_team_matches(team, side) is expected


def test_clippers_road_spread_is_priced(monkeypatch, tmp_path):
    """ESPN 'LA Clippers' @ SA against Polymarket team 'Los Angeles Clippers' (11-19 live rows)."""
    events = [_event("401", CLIPPERS, SPURS)]
    _patch_slate(monkeypatch, market="spread", rows=[_spread_row("Los Angeles Clippers")], events=events)
    result = forecast_cli._forecast_nba_spread_slate(tmp_path, "2026-11-19", _client(events))
    assert result["unmatched"] == []
    assert [c["event_id"] for c in result["priced_contracts"]] == ["401"]
    contract = result["priced_contracts"][0]
    # The model keeps ESPN's name: history (Elo/trends) is keyed on it.
    assert contract["away_team"] == CLIPPERS
    assert contract["market_slug"] == "asc-nba-lac-sa-2026-11-19-pos-2pt5"


def test_lakers_anchored_row_never_prices_a_clippers_game(monkeypatch, tmp_path):
    events = [_event("402", CLIPPERS, SPURS)]
    _patch_slate(monkeypatch, market="spread", rows=[_spread_row("Los Angeles Lakers")], events=events)
    result = forecast_cli._forecast_nba_spread_slate(tmp_path, "2026-11-19", _client(events))
    assert result["priced_contracts"] == []
    assert result["unmatched"] == [{"event_id": "402", "reason": "no matched spread market"}]


def test_clippers_total_is_priced_from_the_event_title(monkeypatch, tmp_path):
    events = [_event("403", CLIPPERS, SPURS)]
    _patch_slate(monkeypatch, market="total", rows=[_total_row()], events=events)
    result = forecast_cli._forecast_nba_total_slate(tmp_path, "2026-11-19", _client(events))
    assert result["unmatched"] == []
    assert [c["event_id"] for c in result["priced_contracts"]] == ["403"]


# --------------------------------------------------------------------- start time


def test_offset_form_quote_start_matches_the_same_instant(monkeypatch, tmp_path):
    """A '-0500' start string is the same instant as ESPN's Z form; [:16] alone misses it."""
    events = [_event("404", CLIPPERS, SPURS)]
    row = _spread_row("Los Angeles Clippers", start="2026-11-19T20:00:00-0500")
    _patch_slate(monkeypatch, market="spread", rows=[row], events=events)
    result = forecast_cli._forecast_nba_spread_slate(tmp_path, "2026-11-19", _client(events))
    assert [c["event_id"] for c in result["priced_contracts"]] == ["404"]


def test_quote_for_another_tip_time_does_not_match(monkeypatch, tmp_path):
    events = [_event("405", CLIPPERS, SPURS)]
    row = _spread_row("Los Angeles Clippers", start="2026-11-20T03:30:00Z")
    _patch_slate(monkeypatch, market="spread", rows=[row], events=events)
    result = forecast_cli._forecast_nba_spread_slate(tmp_path, "2026-11-19", _client(events))
    assert result["priced_contracts"] == []


@pytest.mark.parametrize(
    ("quote_start", "event_date", "expected"),
    [
        ("2026-11-20T01:00:00Z", "2026-11-20T01:00Z", True),
        ("2026-11-19T20:00:00-0500", "2026-11-20T01:00Z", True),
        ("2026-11-20T01:00:00Z", "2026-11-20T01:30Z", False),
        ("not-a-time", "not-a-time", True),  # unparseable: old [:16] string compare is kept
        ("", "2026-11-20T01:00Z", False),
    ],
)
def test_nba_same_start_minute(quote_start, event_date, expected):
    assert forecast_cli._nba_same_start_minute(quote_start, event_date) is expected


# --------------------------------------------------------------------------- B10


def _direct(model_cls, method: str, artifact: dict, **line) -> tuple[object, object]:
    """The engine's own output with and without the availability inputs."""
    model = model_cls.from_artifact(artifact)
    served = UpcomingGame(
        event_id="401",
        event_start_utc=TIP_ESPN,
        away_team=CLIPPERS,
        home_team=SPURS,
        availability_points_gap=AVAILABILITY["availability_points_gap"],
        availability_total_loss=AVAILABILITY["availability_total_loss"],
        league="NBA",
        **line,
    )
    zeroed = UpcomingGame(
        event_id="401", event_start_utc=TIP_ESPN, away_team=CLIPPERS, home_team=SPURS, league="NBA", **line
    )
    return getattr(model, method)(list(HISTORY), [served])[0], getattr(model, method)(
        list(HISTORY), [zeroed]
    )[0]


def test_v9_spread_snapshot_stores_decision_components_without_changing_the_call(monkeypatch, tmp_path):
    events = [_event("401", CLIPPERS, SPURS)]
    _patch_slate(monkeypatch, market="spread", rows=[_spread_row("Los Angeles Clippers")], events=events)
    result = forecast_cli._forecast_nba_spread_slate(tmp_path, "2026-11-19", _client(events))
    contract = result["priced_contracts"][0]
    artifact, _ = forecast_cli._load_exact_artifact_contract("nba-spread-margin-v9")
    served, zeroed = _direct(NBASpreadModelV9, "predict_spreads", artifact, spread_away_line=2.5)

    # No change to probability, side, line or ask.
    away_prob = served.probability("away")
    selection = "away" if away_prob >= 0.5 else "home"
    assert contract["selection"] == selection
    assert contract["model_probability"] == round(away_prob if selection == "away" else 1 - away_prob, 6)
    assert contract["line"] == (2.5 if selection == "away" else -2.5)
    assert contract["executable_ask"] == (0.50 if selection == "away" else 0.52)

    features = contract["model_input_snapshot"]["features"]
    # Existing keys are untouched.
    for key, value in served.feature_basis.items():
        assert features[key] == value
    assert features["expected_margin_pre_availability"] == round(zeroed.inference_inputs["mean"], 4)
    assert features["availability_margin_component"] == pytest.approx(
        served.inference_inputs["mean"] - zeroed.inference_inputs["mean"], abs=1e-4
    )
    assert features["availability_points_gap_input"] == 2.0
    assert features["availability_total_loss_input"] == 6.0
    assert features["rest_days_away"] == pytest.approx(21.5 / 24, abs=1e-4)
    assert features["rest_days_home"] == pytest.approx(3.0, abs=1e-4)
    assert features["b2b_away"] is True
    assert features["b2b_home"] is False
    assert features["current_season_games_away"] == 3
    assert features["current_season_games_home"] == 2
    assert features["current_season_games_played_min"] == 2
    # The engine's own field is lifetime rows (B1, fixed in a new engine, not here).
    assert features["current_season_games_min"] == 4
    assert features["decision_market_mid_long"] == 0.49
    assert features["decision_market_mid_short"] == 0.51
    assert features["decision_market_mid_selection"] == (0.49 if selection == "away" else 0.51)
    assert features["decision_market_quote_observed_at_utc"] == OBSERVED


def test_v9_total_snapshot_stores_decision_components_without_changing_the_call(monkeypatch, tmp_path):
    events = [_event("401", CLIPPERS, SPURS)]
    _patch_slate(monkeypatch, market="total", rows=[_total_row()], events=events)
    result = forecast_cli._forecast_nba_total_slate(tmp_path, "2026-11-19", _client(events))
    contract = result["priced_contracts"][0]
    artifact, _ = forecast_cli._load_exact_artifact_contract("nba-total-margin-v9")
    served, zeroed = _direct(NBATotalModelV9, "predict_totals", artifact, total_line=197.5)

    over_prob = served.probability("over")
    selection = "over" if over_prob >= 0.5 else "under"
    assert contract["selection"] == selection
    assert contract["model_probability_raw"] == round(over_prob if selection == "over" else 1 - over_prob, 6)
    assert contract["line"] == 197.5

    features = contract["model_input_snapshot"]["features"]
    for key, value in served.feature_basis.items():
        assert features[key] == value
    assert features["expected_total_pre_availability"] == round(zeroed.inference_inputs["mean"], 4)
    assert features["availability_total_component"] == pytest.approx(
        served.inference_inputs["mean"] - zeroed.inference_inputs["mean"], abs=1e-4
    )
    assert features["b2b_away"] is True
    assert features["current_season_games_played_min"] == 2
    # No midpoint field on this row: the mid falls back to (bid + ask) / 2.
    assert features["decision_market_mid_long"] == pytest.approx(0.485)
    assert features["decision_market_mid_short"] == pytest.approx(0.515)


def test_season_opener_counts_zero_current_season_games():
    """TrendEngine alone counts last season's trailing run before a team's first game."""
    from model_prediction.features.trends import TrendEngine

    last_season_only = [g for g in HISTORY if g.event_start_utc < "2026-06-01"]
    trend = TrendEngine(last_season_only, offseason_gap_days=60.0, isolate_postseason=True)
    components = forecast_cli._nba_schedule_components(
        last_season_only, CLIPPERS, SPURS, TIP_ESPN, trend=trend, offseason_gap_days=60.0
    )
    assert trend.team_trend(CLIPPERS).current_season_games_played > 0
    assert components["current_season_games_away"] == 0
    assert components["current_season_games_home"] == 0
    assert components["current_season_games_played_min"] == 0


# ---------------------------------------------------------------------------- B2


def _total_contract(event_id: str, selection: str, edge: float, features: dict | None) -> dict:
    return {
        "event_id": event_id,
        "event_start_utc": TIP_POLY,
        "away_team": "Away",
        "home_team": "Home",
        "market_type": "total",
        "selection": selection,
        "line": 228.5,
        "model_probability": 0.60,
        "model_uncertainty": 0.03,
        "model_version": "nba-total-margin-v9",
        "model_artifact_hash": "test-hash",
        "model_qualified": True,
        "market_lineage": {},
        "market_slug": event_id,
        "rationale": "Test",
        "executable_ask": round(0.60 - edge, 6),
        "observed_at_utc": OBSERVED,
        "model_input_snapshot": None if features is None else {"features": features},
    }


def _run_total_logging(monkeypatch, tmp_path, contracts: list[dict]) -> tuple[list, object, object, dict]:
    monkeypatch.setattr(forecast_cli, "utc_now", lambda: NOW)
    monkeypatch.setattr(forecast_cli.PickRequest, "validate", lambda self, now: None)
    monkeypatch.setattr(
        forecast_cli, "_forecast_nba_total_slate", lambda *args: {"priced_contracts": contracts}
    )
    monkeypatch.setattr(forecast_cli, "unit_policy", lambda config: object())
    monkeypatch.setattr(forecast_cli, "_is_registered_serving_model", lambda *args: True)
    monkeypatch.setattr(
        forecast_cli,
        "evaluate_eligibility",
        lambda *args, **kwargs: SimpleNamespace(decision="CALL", qualified=True),
    )
    writes: list = []
    monkeypatch.setattr(
        forecast_cli,
        "_append_secondary_ledger",
        lambda ledger, request, eligibility, observed_at, source: writes.append((ledger, request.event_id)),
    )
    flat = SimpleNamespace(exposure=lambda *args, **kwargs: object())
    main = object()
    result = forecast_cli._forecast_nba_total_sport(
        data_root=tmp_path,
        args_date="2026-11-19",
        config={
            "models": {"NBA": {"min_edge": 0.05}},
            "project": {"maximum_unreviewed_market_disagreement": 0.10},
        },
        registry=SimpleNamespace(resolve=lambda *args: SimpleNamespace(canonical_team_id="t")),
        bans=None,
        flat_ledger=flat,
        main_ledger=main,
    )
    return writes, flat, main, result


def test_thin_sample_over_floor_reads_the_stored_snapshot_key(monkeypatch, tmp_path):
    contracts = [
        # True current-season count wins over the engine's lifetime count.
        _total_contract(
            "thin_true", "over", 0.07, {"current_season_games_played_min": 6, "current_season_games_min": 220}
        ),
        # Legacy snapshot without the new key: the stored engine field is read (B2 alone).
        _total_contract("thin_legacy", "over", 0.07, {"current_season_games_min": 6}),
        _total_contract("thin_big_edge", "over", 0.095, {"current_season_games_played_min": 6}),
        _total_contract(
            "mature", "over", 0.07, {"current_season_games_played_min": 20, "current_season_games_min": 220}
        ),
        _total_contract("thin_under", "under", 0.07, {"current_season_games_played_min": 6}),
        _total_contract("no_snapshot", "over", 0.07, None),
        _total_contract("garbage", "over", 0.07, {"current_season_games_played_min": "n/a"}),
        _total_contract("null_value", "over", 0.07, {"current_season_games_played_min": None}),
    ]
    writes, flat, main, result = _run_total_logging(monkeypatch, tmp_path, contracts)
    assert result["skipped"] == []
    # Flat calls everything, gated or not.
    assert [event_id for ledger, event_id in writes if ledger is flat] == [c["event_id"] for c in contracts]
    assert [event_id for ledger, event_id in writes if ledger is main] == [
        "thin_big_edge",
        "mature",
        "thin_under",
        "no_snapshot",
        "garbage",
        "null_value",
    ]


# ------------------------------------------------------------------- Flat invariant


def _run_real_slate_logging(monkeypatch, tmp_path, market: str, rows: list[dict], decision: str) -> tuple:
    events = [_event("401", CLIPPERS, SPURS), _event("499", NUGGETS, "Los Angeles Lakers")]
    _patch_slate(monkeypatch, market=market, rows=rows, events=events)
    priced: list[str] = []
    real_slate = getattr(forecast_cli, f"_forecast_nba_{market}_slate")

    def slate(*args):
        result = real_slate(*args)
        priced.extend(c["event_id"] for c in result["priced_contracts"])
        return result

    monkeypatch.setattr(forecast_cli, f"_forecast_nba_{market}_slate", slate)
    monkeypatch.setattr(forecast_cli.PickRequest, "validate", lambda self, now: None)
    monkeypatch.setattr(forecast_cli, "unit_policy", lambda config: object())
    monkeypatch.setattr(forecast_cli, "_is_registered_serving_model", lambda *args: True)
    monkeypatch.setattr(
        forecast_cli,
        "evaluate_eligibility",
        lambda *args, **kwargs: SimpleNamespace(decision=decision, qualified=decision == "CALL"),
    )
    # The fixture quotes carry no archive lineage, so every call is downgraded to a paper call.
    monkeypatch.setattr(
        forecast_cli,
        "_downgrade_unserved",
        lambda eligibility: SimpleNamespace(decision=eligibility.decision, qualified=False),
    )
    writes: list = []
    monkeypatch.setattr(
        forecast_cli,
        "_append_secondary_ledger",
        lambda ledger, request, eligibility, observed_at, source: writes.append(
            (ledger, request.event_id, json.loads(request.model_input_snapshot_json or "null"))
        ),
    )
    flat = SimpleNamespace(exposure=lambda *args, **kwargs: object(), rows=lambda **kwargs: [])
    main = SimpleNamespace(rows=lambda **kwargs: [])
    sport = getattr(forecast_cli, f"_forecast_nba_{market}_sport")
    sport(
        data_root=tmp_path,
        args_date="2026-11-19",
        config={
            "models": {"NBA": {"min_edge": 0.05}},
            "project": {"maximum_unreviewed_market_disagreement": 0.10},
        },
        registry=SimpleNamespace(resolve=lambda *args: SimpleNamespace(canonical_team_id="t")),
        bans=None,
        flat_ledger=flat,
        main_ledger=main,
    )
    return priced, [(event_id, snap) for ledger, event_id, snap in writes if ledger is flat]


@pytest.mark.parametrize("decision", ["CALL", "NO_CALL"])
@pytest.mark.parametrize(
    ("market", "rows"),
    [("spread", [_spread_row("Los Angeles Clippers")]), ("total", [_total_row()])],
)
def test_every_priced_nba_contract_reaches_flat(monkeypatch, tmp_path, market, rows, decision):
    """Operator rule: Flat calls everything. The unquoted Lakers game is unmatched, not dropped."""
    priced, flat_rows = _run_real_slate_logging(monkeypatch, tmp_path, market, rows, decision)
    assert priced == ["401"]
    assert [event_id for event_id, _ in flat_rows] == priced
    # The Flat row carries the new components in its stored snapshot.
    assert "current_season_games_played_min" in flat_rows[0][1]["features"]
