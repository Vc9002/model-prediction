"""Pregame input provenance and serving-policy guards for reported loss cohorts."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from model_prediction.cli import forecast as forecast_cli
from model_prediction.data_sources.cfb_data import resolve_team
from model_prediction.domain import RecordType
from model_prediction.features import multi_sport_weather as weather
from model_prediction.features.nba_player_availability import load_latest_nba_injury_snapshot


def test_cfb_weather_requires_a_hashed_pregame_forecast_at_the_event_hour(tmp_path):
    start = "2026-10-31T19:00:00Z"
    common = {
        "venue_name": "Camp Randall Stadium",
        "home_team": "Wisconsin Badgers",
        "date": "2026-10-31",
        "hourly": {
            "time": ["2026-10-31T19:00"],
            "temperature_2m": [5.0],
            "wind_speed_10m": [25.0],
            "precipitation": [2.54],
        },
    }
    rows = [
        {**common, "source": "open_meteo:archive", "observed_at_utc": "2026-10-30T12:00:00Z"},
        {**common, "source": "open_meteo:forecast", "observed_at_utc": "2026-10-31T20:00:00Z"},
    ]
    weather.store_weather_observations(tmp_path, "ncaaf", rows)
    assert weather.resolve_cfb_game_weather(
        tmp_path,
        "Wisconsin Badgers",
        "2026-10-31",
        event_start_utc=start,
        observed_at_utc="2026-10-31T18:00:00Z",
    ) == (None, None, None, False)

    weather.store_weather_observations(
        tmp_path,
        "ncaaf",
        [{**common, "source": "open_meteo:forecast", "observed_at_utc": "2026-10-31T17:00:00Z"}],
    )
    assert weather.resolve_cfb_game_weather(
        tmp_path,
        "Wisconsin Badgers",
        "2026-10-31",
        event_start_utc=start,
        observed_at_utc="2026-10-31T18:00:00Z",
    ) == (41.0, 15.5, 0.1, False)


def test_accented_espn_san_jose_name_matches_canonical_weather_evidence(tmp_path):
    assert resolve_team("San Jos\u00e9 State Spartans").canonical_name == "San Jose State Spartans"
    weather.store_weather_observations(
        tmp_path,
        "ncaaf",
        [
            {
                "venue_name": "CEFCU Stadium",
                "home_team": "San Jose State Spartans",
                "date": "2026-10-31",
                "source": "open_meteo:forecast",
                "observed_at_utc": "2026-10-30T17:00:00Z",
                "hourly": {
                    "time": ["2026-10-31T20:00"],
                    "temperature_2m": [15.0],
                    "wind_speed_10m": [8.0],
                    "precipitation": [0.0],
                },
            }
        ],
    )
    assert weather.resolve_cfb_game_weather(
        tmp_path,
        "San Jos\u00e9 State Spartans",
        "2026-10-31",
        event_start_utc="2026-10-31T20:00:00Z",
        observed_at_utc="2026-10-31T18:00:00Z",
    ) == (59.0, 5.0, 0.0, False)


def test_cfb_capture_stores_only_upcoming_forecast_weather(monkeypatch, tmp_path):
    start = datetime.now(UTC) + timedelta(days=1)
    event = {
        "date": start.isoformat(),
        "competitions": [
            {"competitors": [{"homeAway": "home", "team": {"displayName": "Wisconsin Badgers"}}]}
        ],
    }

    def fake_fetch(profile, game_date, *, client):
        return {
            "venue_name": profile.venue_name,
            "date": game_date,
            "source": "open_meteo:forecast",
            "observed_at_utc": datetime.now(UTC).isoformat(),
            "hourly": {"time": [start.strftime("%Y-%m-%dT%H:00")]},
        }

    monkeypatch.setattr(weather, "fetch_venue_hourly_weather", fake_fetch)
    result = weather.capture_cfb_pregame_weather(
        tmp_path,
        start.date().isoformat(),
        scoreboard={"events": [event]},
    )
    assert result["status"] == "ok"
    assert result["appended"] == 1


def test_cfb_main_requires_artifact_edge_and_pregame_weather(monkeypatch, tmp_path):
    from model_prediction.models import college_football as cfb_model

    now = datetime(2026, 10, 30, 12, tzinfo=UTC)
    contracts = []
    for event_id, market, edge, verified in [
        ("missing-weather", "total", 0.04, False),
        ("verified-weather", "total", 0.04, True),
        ("extreme-edge", "spread", 0.12, True),
    ]:
        contracts.append(
            {
                "event_id": event_id,
                "event_start_utc": "2026-10-31T19:00:00Z",
                "away_team": "Oregon Ducks",
                "home_team": "Wisconsin Badgers",
                "market_type": market,
                "selection": "over" if market == "total" else "home",
                "line": 51.5 if market == "total" else 3.5,
                "model_probability": 0.6,
                "model_uncertainty": 0.1,
                "model_version": "cfb-structural-v3",
                "rationale": "Test",
                "market_slug": event_id,
                "executable_ask": 0.6 - edge,
                "observed_at_utc": now.isoformat(),
                "edge_vs_executable_ask": edge,
                "sportsbook": "espn_consensus",
                "weather_inputs_verified": verified,
            }
        )
    monkeypatch.setattr(forecast_cli, "utc_now", lambda: now)
    monkeypatch.setattr(
        cfb_model,
        "build_cfb_slate",
        lambda **kwargs: {"priced_contracts": contracts, "model_code_hash": "test-hash"},
    )
    monkeypatch.setattr(forecast_cli, "unit_policy", lambda config: object())
    monkeypatch.setattr(
        forecast_cli,
        "evaluate_gated_research_eligibility",
        lambda *args, **kwargs: SimpleNamespace(
            decision="CALL", record_type=RecordType.QUALIFIED_SHADOW_CALL, reason_code="QUALIFIED"
        ),
    )
    writes = []
    monkeypatch.setattr(
        forecast_cli,
        "_append_secondary_ledger",
        lambda ledger, request, eligibility, observed_at, source: writes.append(
            (ledger, request.event_id)
        ),
    )
    flat = SimpleNamespace(exposure=lambda *args, **kwargs: object())
    main = object()
    result = forecast_cli._forecast_cfb_sport(
        data_root=tmp_path,
        args_date="2026-10-31",
        config={"models": {"NCAAF": {"active_production_version": "cfb-structural-v3", "min_edge": 0.02}}},
        flat_ledger=flat,
        main_ledger=main,
    )
    assert result["errors"] == []
    assert {event_id for ledger, event_id in writes if ledger is flat} == {
        "missing-weather",
        "verified-weather",
        "extreme-edge",
    }
    assert {event_id for ledger, event_id in writes if ledger is main} == {"verified-weather"}
    assert {row["reason"] for row in result["main_rejections"]} == {
        "pregame_weather_unverified",
        "excessive_edge",
    }


def test_soccer_negative_quote_edge_never_reaches_main(monkeypatch, tmp_path):
    from model_prediction.models import soccer_v3

    now = datetime(2026, 10, 30, 12, tzinfo=UTC)
    contract = {
        "event_id": "negative-edge",
        "event_start_utc": "2026-10-31T19:00:00Z",
        "away_team": "Away",
        "home_team": "Home",
        "market_type": "total",
        "selection": "over",
        "line": 1.5,
        "model_probability": 0.7,
        "model_uncertainty": 0.1,
        "model_version": "soccer-poisson-dc-v3",
        "rationale": "Test",
        "market_slug": "negative-edge",
        "executable_ask": 0.72,
        "observed_at_utc": now.isoformat(),
        "edge_vs_executable_ask": -0.02,
        "feature_basis": {"min_team_games": 20},
    }
    monkeypatch.setattr(forecast_cli, "utc_now", lambda: now)
    monkeypatch.setattr(soccer_v3, "soccer_model_for_version", lambda version: object())
    monkeypatch.setattr(forecast_cli.PickRequest, "validate", lambda self, now: None)
    monkeypatch.setattr(
        forecast_cli,
        "build_soccer_total_slate",
        lambda **kwargs: {"priced_contracts": [contract], "model_code_hash": "test-hash"},
    )
    monkeypatch.setattr(forecast_cli, "contract_snapshot_json", lambda *args, **kwargs: "{}")
    monkeypatch.setattr(forecast_cli, "unit_policy", lambda config: object())
    monkeypatch.setattr(
        forecast_cli,
        "evaluate_gated_research_eligibility",
        lambda *args, **kwargs: SimpleNamespace(decision="CALL"),
    )
    writes = []
    monkeypatch.setattr(
        forecast_cli,
        "_append_secondary_ledger",
        lambda ledger, request, eligibility, observed_at, source: writes.append((ledger, request.event_id)),
    )
    flat = SimpleNamespace(exposure=lambda *args, **kwargs: object())
    main = object()
    result = forecast_cli._forecast_soccer_sport(
        data_root=tmp_path,
        args_date="2026-10-31",
        config={
            "models": {
                "SOCCER": {
                    "status": "shadow_qualified",
                    "markets": ["total"],
                    "min_edge": 0.02,
                }
            },
            "project": {},
        },
        flat_ledger=flat,
        main_ledger=main,
    )
    assert result["errors"] == []
    assert [event_id for ledger, event_id in writes if ledger is flat] == ["negative-edge"]
    assert [event_id for ledger, event_id in writes if ledger is main] == []


def test_nba_total_extreme_disagreement_stays_in_flat(monkeypatch, tmp_path):
    now = datetime(2026, 10, 30, 12, tzinfo=UTC)
    contracts = []
    for event_id, edge, qualified in [
        ("normal", 0.07, True),
        ("extreme", 0.30, True),
        ("unserved", 0.07, False),
    ]:
        contracts.append(
            {
                "event_id": event_id,
                "event_start_utc": "2026-10-31T19:00:00Z",
                "away_team": "Away",
                "home_team": "Home",
                "market_type": "total",
                "selection": "under",
                "line": 228.5,
                "model_probability": 0.6,
                "model_uncertainty": 0.1,
                "model_version": "nba-total-margin-v5",
                "model_artifact_hash": "test-hash",
                "model_qualified": qualified,
                "market_lineage": {},
                "market_slug": event_id,
                "rationale": "Test",
                "executable_ask": 0.6 - edge,
                "observed_at_utc": now.isoformat(),
            }
        )
    monkeypatch.setattr(forecast_cli, "utc_now", lambda: now)
    monkeypatch.setattr(forecast_cli.PickRequest, "validate", lambda self, now: None)
    monkeypatch.setattr(
        forecast_cli,
        "_forecast_nba_total_slate",
        lambda *args: {"priced_contracts": contracts},
    )
    monkeypatch.setattr(forecast_cli, "unit_policy", lambda config: object())
    monkeypatch.setattr(forecast_cli, "_is_registered_serving_model", lambda *args: True)
    monkeypatch.setattr(
        forecast_cli,
        "evaluate_eligibility",
        lambda *args, **kwargs: SimpleNamespace(
            decision="CALL", record_type=RecordType.QUALIFIED_SHADOW_CALL
        ),
    )
    monkeypatch.setattr(
        forecast_cli,
        "_downgrade_unserved",
        lambda eligibility: SimpleNamespace(decision="CALL", record_type=RecordType.RESEARCH_OBSERVATION),
    )
    writes = []
    monkeypatch.setattr(
        forecast_cli,
        "_append_secondary_ledger",
        lambda ledger, request, eligibility, observed_at, source: writes.append((ledger, request.event_id)),
    )
    flat = SimpleNamespace(exposure=lambda *args, **kwargs: object())
    main = object()
    registry = SimpleNamespace(resolve=lambda *args: SimpleNamespace(canonical_team_id="test"))
    result = forecast_cli._forecast_nba_total_sport(
        data_root=tmp_path,
        args_date="2026-10-31",
        config={
            "models": {"NBA": {"min_edge": 0.05}},
            "project": {"maximum_unreviewed_market_disagreement": 0.10},
        },
        registry=registry,
        bans=None,
        flat_ledger=flat,
        main_ledger=main,
    )
    assert result["skipped"] == []
    assert {event_id for ledger, event_id in writes if ledger is flat} == {
        "normal",
        "extreme",
        "unserved",
    }
    assert [event_id for ledger, event_id in writes if ledger is main] == ["normal"]
    assert result["main_rejections"] == [
        {"event_id": "extreme", "reason": "market_disagreement_exceeds_limit"}
    ]


def test_nba_injury_reader_rejects_tampered_and_late_snapshots(tmp_path):
    directory = tmp_path / "availability" / "nba" / "espn_injury_snapshots" / "2026-10-30"
    directory.mkdir(parents=True)

    def write_snapshot(stamp, player, tamper=False):
        payload = {
            "observed_at_utc": stamp,
            "entries": [{"player_name": player}],
            "source": "espn_nba_injuries",
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        payload["snapshot_sha256"] = digest
        if tamper:
            payload["entries"][0]["player_name"] = "Altered"
        filename_stamp = datetime.fromisoformat(stamp).strftime("%Y%m%dT%H%M%S%z")
        (directory / f"nba-injuries-{filename_stamp}-{digest[:12]}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    write_snapshot("2026-10-30T15:00:00+00:00", "Verified")
    write_snapshot("2026-10-30T16:00:00+00:00", "Tampered", tamper=True)
    write_snapshot("2026-10-30T18:00:00+00:00", "Too late")
    result = load_latest_nba_injury_snapshot(
        tmp_path, observed_at=datetime(2026, 10, 30, 17, tzinfo=UTC)
    )
    assert result["entries"][0]["player_name"] == "Verified"


def test_qualified_nba_slate_keeps_unvalidated_availability_diagnostic_only(monkeypatch, tmp_path):
    from model_prediction.features import nba_player_availability
    from model_prediction.models import basketball

    now = datetime(2026, 10, 30, 12, tzinfo=UTC)
    start = "2026-10-31T19:00:00Z"
    event = {
        "id": "game-1",
        "date": start,
        "competitions": [
            {"competitors": [
                {"homeAway": "away", "team": {"displayName": "Miami Heat"}},
                {"homeAway": "home", "team": {"displayName": "Boston Celtics"}},
            ]}
        ],
    }
    quote = {
        "market_type": "total",
        "team": "Miami Heat",
        "event_start_utc": start,
        "line": 228.5,
        "market_slug": "game-1-total",
        "long": {"ask": 0.49},
        "short": {"ask": 0.52},
    }
    monkeypatch.setattr(forecast_cli, "utc_now", lambda: now)
    monkeypatch.setattr(
        forecast_cli.ProductionModelRegistry,
        "load",
        lambda root: SimpleNamespace(
            champion=lambda *args: SimpleNamespace(model_id="nba-total-margin-v5", available=True)
        ),
    )
    monkeypatch.setattr(
        forecast_cli, "_load_exact_artifact_contract",
        lambda version: ({"artifact_hash": "verified", "qualification": {"qualified": True}}, None),
    )
    monkeypatch.setattr(forecast_cli.FeatureStore, "games_before", lambda *args: [])
    monkeypatch.setattr(forecast_cli, "_read_polymarket_snapshot_rows", lambda path: [quote])
    monkeypatch.setattr(forecast_cli, "_quotes_known_at", lambda rows, observed: rows)
    monkeypatch.setattr(
        nba_player_availability,
        "compute_nba_matchup_availability",
        lambda *args, **kwargs: {"availability_points_gap": 4.8, "availability_total_loss": 12.0},
    )
    captured = []

    def fake_predict(self, history, upcoming):
        captured.extend(upcoming)
        return [SimpleNamespace(
            market_type="total", event_id="game-1", event_start_utc=start,
            away_team="Miami Heat", home_team="Boston Celtics",
            probability=lambda selection: 0.62, uncertainty=0.1, rationale="test",
            feature_basis={}, inference_inputs=None,
        )]

    monkeypatch.setattr(basketball.BasketballModel, "predict_games", fake_predict)
    result = forecast_cli._forecast_nba_total_slate(
        tmp_path, "2026-10-31", SimpleNamespace(scoreboard=lambda *args: {"events": [event]})
    )
    assert captured[0].availability_points_gap == 0.0
    assert captured[0].availability_total_loss == 0.0
    assert result["priced_contracts"][0]["research_availability"] == {
        "estimated_margin_adjustment": 4.8,
        "estimated_total_adjustment": 12.0,
        "applied_to_qualified_model": False,
    }
