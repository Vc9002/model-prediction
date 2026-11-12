"""NCAAF serving-path regressions for the 2026-11-11 model review (findings #1, #8, #9, #12, #13, LOW).

Every test here imports the object production actually calls (``build_cfb_slate`` /
``cfb_model`` / ``_forecast_cfb_sport``), not a sibling class a test happened to pin.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from model_prediction.cfb_replay import replay_inputs
from model_prediction.config import PROJECT_ROOT
from model_prediction.features import multi_sport_weather as weather
from model_prediction.features.cfb_features import CFBMatchupFeatures
from model_prediction.features.cfb_qb_availability import (
    STATUS_STARTER_PROBABILITY,
    load_latest_injury_snapshot,
    passing_leader_from_competitor,
    resolve_cfb_qb_availability,
)
from model_prediction.models import college_football
from model_prediction.models.cfb_structural_v4 import (
    CFB_V4_N_SIMULATIONS,
    CFBStructuralV4Model,
    load_cfb_structural_v4_artifact,
    main_gates_from_artifact,
)
from model_prediction.models.college_football import (
    MODEL_VERSION,
    CollegeFootballModel,
    build_cfb_slate,
    cfb_model,
    cfb_serving_code_hash,
    model_week_from_espn_event,
)

ARTIFACT_PATH = PROJECT_ROOT / "config" / "models" / "cfb-structural-v4.json"
DECISION_AT = datetime(2026, 11, 14, 14, tzinfo=UTC)
KICKOFF = "2026-11-14T20:00:00Z"
ESPN_ODDS = {
    "provider": {"name": "consensus"},
    "moneyline": {"home": {"close": {"odds": "-150"}}, "away": {"close": {"odds": "+130"}}},
    "pointSpread": {
        "home": {"close": {"line": "-3.5", "odds": "-110"}},
        "away": {"close": {"line": "3.5", "odds": "-110"}},
    },
    "total": {
        "over": {"close": {"line": "50.5", "odds": "-110"}},
        "under": {"close": {"line": "50.5", "odds": "-110"}},
    },
}


def _event(
    *,
    home: str = "Miami Hurricanes",
    away: str = "Duke Blue Devils",
    neutral: bool = False,
    venue: dict | None = None,
    season_type: int = 2,
    week: int = 12,
    leaders: bool = False,
    odds: dict | None = ESPN_ODDS,
) -> dict:
    def competitor(side: str, name: str, team_id: str, qb_id: str, qb_name: str) -> dict:
        comp = {"homeAway": side, "team": {"id": team_id, "displayName": name}}
        if leaders:
            comp["leaders"] = [
                {
                    "name": "passingLeader",
                    "leaders": [
                        {
                            "displayValue": "200 YDS",
                            "athlete": {
                                "id": qb_id,
                                "displayName": qb_name,
                                "position": {"abbreviation": "QB"},
                            },
                        }
                    ],
                }
            ]
        return comp

    competition = {
        "neutralSite": neutral,
        "competitors": [
            competitor("home", home, "1", "qb-home", "Home Starter"),
            competitor("away", away, "2", "qb-away", "Away Starter"),
        ],
        "odds": [odds] if odds else [],
    }
    if venue is not None:
        competition["venue"] = venue
    return {
        "id": "cfb-parity-1",
        "date": KICKOFF,
        "season": {"year": 2026, "type": season_type},
        "week": {"number": week},
        "competitions": [competition],
    }


def _slate(tmp_path: Path, event: dict, **kwargs):
    client = SimpleNamespace(scoreboard=lambda *_a, **_k: {"events": [event]})
    return build_cfb_slate(tmp_path, "2026-11-14", client=client, observed_at=DECISION_AT, **kwargs)


# --------------------------------------------------------------------------- #1 serving parity
def test_production_factory_is_the_v4_class_parameterised_from_the_artifact() -> None:
    artifact = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    model = cfb_model()
    assert isinstance(model, CFBStructuralV4Model)
    assert model.version == artifact["model_version"] == MODEL_VERSION
    assert model.margin_sd == artifact["margin_sd"]
    assert model.total_sd == artifact["total_sd"]
    assert model.home_advantage_points == artifact["home_advantage_points"]
    assert model.distribution_type.value == artifact["distribution"]
    assert model.n_simulations == CFB_V4_N_SIMULATIONS == 200_000


def test_serving_entry_point_records_the_artifact_margin_sd_not_the_legacy_constant(tmp_path) -> None:
    """The slate builder's replay inputs must carry the v4 class's effective SDs for the
    priced line, derived from the artifact's 14.5 base. The retired implementation
    recorded 16.2 x widening under the same model label."""
    artifact = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    slate = _slate(tmp_path, _event())
    spread = next(c for c in slate["priced_contracts"] if c["market_type"] == "spread")
    features = spread["feature_basis"]
    served = cfb_model()
    expected_margin_sd = served.compute_effective_margin_sd(
        spread_home_line=-3.5,
        uncertainty=float(features["uncertainty"]),
        is_conference_matchup=bool(features["is_conference_matchup"]),
    )
    params = spread["inference_inputs"]["parameters"]
    assert params["margin_sd"] == pytest.approx(expected_margin_sd)
    assert params["total_sd"] == pytest.approx(
        served.compute_effective_total_sd(
            projected_total=float(features["projected_total"]),
            total_line=50.5,
            uncertainty=float(features["uncertainty"]),
        )
    )
    assert params["n_simulations"] == CFB_V4_N_SIMULATIONS
    legacy = CollegeFootballModel(margin_sd=16.2).compute_effective_margin_sd(
        spread_home_line=-3.5,
        uncertainty=float(features["uncertainty"]),
        is_conference_matchup=bool(features["is_conference_matchup"]),
    )
    assert params["margin_sd"] != pytest.approx(legacy)
    assert expected_margin_sd / (1.0 + 2.0 * (float(features["uncertainty"]) - 0.04)) / (
        0.9135 if features["is_conference_matchup"] else 1.0
    ) == pytest.approx(artifact["margin_sd"], abs=1e-3)
    assert spread["model_version"] == artifact["model_version"]
    # And the recorded inputs replay to the recorded probabilities (settlement contract).
    assert replay_inputs(spread["inference_inputs"], "spread")["home"] == pytest.approx(
        spread["model_probability"] if spread["selection"] == "home" else 1.0 - spread["model_probability"],
        abs=2e-6,
    )


def test_parity_test_detects_a_mis_parameterised_serving_model(tmp_path) -> None:
    """Mutation check: hand the slate builder the legacy 16.2 model and the recorded
    margin_sd no longer matches the artifact-derived value."""
    artifact = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    slate = _slate(tmp_path, _event(), model=CollegeFootballModel(margin_sd=16.2))
    spread = next(c for c in slate["priced_contracts"] if c["market_type"] == "spread")
    features = spread["feature_basis"]
    base = (
        spread["inference_inputs"]["parameters"]["margin_sd"]
        / (1.0 + 2.0 * (float(features["uncertainty"]) - 0.04))
        / (0.9135 if features["is_conference_matchup"] else 1.0)
    )
    assert base != pytest.approx(artifact["margin_sd"], abs=1e-3)


class _RecordingArtifact(dict):
    def __init__(self, payload: dict) -> None:
        super().__init__(payload)
        self.seen: set[str] = set()

    def get(self, key, default=None):  # type: ignore[override]
        self.seen.add(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self.seen.add(key)
        return super().__getitem__(key)

    def __contains__(self, key) -> bool:
        self.seen.add(key)
        return super().__contains__(key)


_METADATA_KEYS = {
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
}


def test_every_v4_artifact_parameter_is_consumed_by_the_served_model_or_main_gates() -> None:
    """Review #9: gates.block_fcs / max_spread_line / max_moneyline_dog_odds were declared
    and read nowhere. Now the served model reads its parameters and the Main gate helper
    reads every gate key."""
    artifact = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    recorder = _RecordingArtifact(artifact)
    gates_recorder = _RecordingArtifact(artifact["gates"])
    recorder["gates"] = gates_recorder
    CollegeFootballModel.from_artifact(recorder)
    gates = main_gates_from_artifact(recorder)
    unread = set(artifact) - _METADATA_KEYS - recorder.seen
    assert not unread, f"artifact parameters the serving path never reads: {sorted(unread)}"
    unread_gates = set(artifact["gates"]) - gates_recorder.seen
    assert not unread_gates, f"gate keys nothing enforces: {sorted(unread_gates)}"
    assert gates.block_fcs is True
    assert gates.max_spread_line == 21.5
    assert gates.min_moneyline_ask == pytest.approx(1 / 3, abs=1e-6)  # +200 dog == 33.3% ask floor


def test_artifact_declared_features_are_all_emitted_by_the_feature_dict() -> None:
    artifact = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    feat = CollegeFootballModel().extractor.extract_features(
        history=[],
        away_team="Duke Blue Devils",
        home_team="Miami Hurricanes",
        event_id="x",
        game_start_utc=KICKOFF,
        season_year=2026,
        week=12,
    )
    emitted = set(feat.to_dict())
    missing = [name for name in artifact["features"] if name not in emitted]
    assert not missing, missing
    assert set(artifact["features"]) <= {f.name for f in CFBMatchupFeatures.__dataclass_fields__.values()}


def test_from_artifact_fails_closed_on_undeclared_or_unread_parameters() -> None:
    artifact = load_cfb_structural_v4_artifact()
    with pytest.raises(ValueError, match="never produces"):
        CFBStructuralV4Model.from_artifact({**artifact, "features": [*artifact["features"], "not_a_feature"]})
    with pytest.raises(ValueError, match="never reads"):
        CFBStructuralV4Model.from_artifact({**artifact, "mystery_knob": 1.0})
    with pytest.raises(ValueError, match="artifact_hash mismatch"):
        bad = Path(ARTIFACT_PATH.parent / "cfb-structural-v4.json").read_text(encoding="utf-8")
        tampered = json.loads(bad)
        tampered["margin_sd"] = 16.2
        tmp = Path(PROJECT_ROOT / "outputs" / "_tampered_cfb_artifact_test.json")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        try:
            tmp.write_text(json.dumps(tampered), encoding="utf-8")
            load_cfb_structural_v4_artifact(tmp)
        finally:
            tmp.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #8a / #8b ask integrity
def _pm_row(slug: str, observed: str, long_ask: float | None, short_ask: float | None, **extra) -> dict:
    row = {
        "event_title": "Duke vs. Miami",
        "league": "NCAAF",
        "market_slug": slug,
        "market_type": "moneyline",
        "observed_at_utc": observed,
        "provider": "polymarket_us",
        "team": "Duke",
        "line": None,
        "long": {"ask": long_ask, "description": "Blue Devils"},
        "short": {"ask": short_ask, "description": "Hurricanes"},
        "timestamp_valid": True,
        "reconstructed": False,
        "usage": "prospective_executable_bbo",
    }
    row.update(extra)
    return row


def test_moneyline_match_returns_the_newest_capture_not_the_first_row() -> None:
    rows = [
        _pm_row("aec-cfb-duke-miami", "2026-11-14T05:00:00Z", 0.30, 0.72),
        _pm_row("aec-cfb-duke-miami", "2026-11-14T13:00:00Z", 0.34, 0.68),
        _pm_row("aec-cfb-duke-miami", "2026-11-14T09:00:00Z", 0.32, 0.70),
    ]
    row, side = college_football._match_cfb_moneyline("Duke Blue Devils", "Miami Hurricanes", rows)
    assert row is not None and row["observed_at_utc"] == "2026-11-14T13:00:00Z"
    assert side == "away"


@pytest.mark.parametrize("market_type", ["moneyline", "spread", "total"])
def test_one_sided_polymarket_books_are_ignored_not_completed_as_one_minus_ask(market_type) -> None:
    extra = {"market_type": market_type, "line": 3.5 if market_type != "moneyline" else None}
    one_sided = [_pm_row(f"x-{market_type}", "2026-11-14T13:00:00Z", 0.47, None, **extra)]
    if market_type == "moneyline":
        assert college_football._match_cfb_moneyline("Duke Blue Devils", "Miami Hurricanes", one_sided) == (
            None,
            None,
        )
    elif market_type == "spread":
        assert college_football._match_cfb_spread("Duke Blue Devils", "Miami Hurricanes", one_sided) == (
            None,
            None,
        )
    else:
        assert college_football._match_cfb_total("Duke Blue Devils", "Miami Hurricanes", one_sided) is None


def test_latest_capture_per_market_keeps_one_row_per_slug() -> None:
    rows = [
        _pm_row("a", "2026-11-14T05:00:00Z", 0.40, 0.62),
        _pm_row("a", "2026-11-14T12:00:00Z", 0.44, 0.58),
        _pm_row("b", "2026-11-14T06:00:00Z", 0.50, 0.52),
    ]
    kept = college_football._latest_capture_per_market(rows)
    assert {r["market_slug"]: r["observed_at_utc"] for r in kept} == {
        "a": "2026-11-14T12:00:00Z",
        "b": "2026-11-14T06:00:00Z",
    }


# --------------------------------------------------------------------------- #8c provenance tag
def test_consensus_priced_contracts_are_labelled_as_indicative(tmp_path) -> None:
    slate = _slate(tmp_path, _event())
    assert slate["priced_contracts"]
    for contract in slate["priced_contracts"]:
        assert contract["sportsbook"] == "espn_consensus"
        assert contract["feature_basis"]["quote_provenance"] == "espn_consensus_indicative"
        assert "not an executable CLOB ask" in contract["rationale"]


# --------------------------------------------------------------------------- #13 week / neutral site
def test_postseason_events_use_a_fully_decayed_week() -> None:
    bowl = {"season": {"year": 2025, "type": 3}, "week": {"number": 1}}
    assert model_week_from_espn_event(bowl, "2025-12-31") == (2025, 15, 3)
    regular = {"season": {"year": 2026, "type": 2}, "week": {"number": 1}}
    assert model_week_from_espn_event(regular, "2026-08-30") == (2026, 1, 2)


def test_slate_records_schedule_context_for_a_bowl(tmp_path) -> None:
    slate = _slate(
        tmp_path, _event(season_type=3, week=1, neutral=True, venue={"fullName": "X", "indoor": True})
    )
    ctx = slate["priced_contracts"][0]["feature_basis"]["schedule_context"]
    assert ctx == {
        "espn_season_type": 3,
        "espn_week": 1,
        "model_week": 15,
        "venue_name": "X",
        "venue_indoor": True,
    }
    assert slate["priced_contracts"][0]["feature_basis"]["away_preseason_prior_weight"] == 0.01


def _weather_row(venue_name: str, home_team: str, observed: str) -> dict:
    hours = [f"2026-11-14T{h:02d}:00" for h in range(24)]
    return {
        "venue_name": venue_name,
        "sport": "ncaaf",
        "date": "2026-11-14",
        "is_indoor": False,
        "latitude": 27.9,
        "longitude": -82.5,
        "elevation_m": 10.0,
        "hourly": {
            "time": hours,
            "temperature_2m": [20.0] * 24,
            "wind_speed_10m": [30.0] * 24,
            "precipitation": [0.0] * 24,
        },
        "source": "open_meteo:forecast",
        "observed_at_utc": observed,
        "home_team": home_team,
    }


def test_neutral_site_weather_comes_from_the_venue_not_the_home_stadium(tmp_path) -> None:
    # Syracuse plays in a dome; at a neutral outdoor venue that fact must not "verify" the weather.
    none = weather.resolve_cfb_game_weather(
        tmp_path,
        "Syracuse Orange",
        "2026-11-14",
        event_start_utc=KICKOFF,
        observed_at_utc=DECISION_AT.isoformat(),
        is_neutral_site=True,
        venue_name="Raymond James Stadium",
        venue_indoor=False,
    )
    assert none == (None, None, None, False)
    # ESPN says the neutral venue is indoor: dome constants, flagged as such.
    prov: dict = {}
    assert weather.resolve_cfb_game_weather(
        tmp_path,
        "Michigan Wolverines",
        "2026-11-14",
        is_neutral_site=True,
        venue_name="AT&T Stadium",
        venue_indoor=True,
        provenance_out=prov,
    ) == (70.0, 0.0, 0.0, True)
    assert prov["source"] == "espn_venue:indoor"
    # A capture keyed on the venue name resolves.
    weather.store_weather_observations(
        tmp_path,
        "ncaaf",
        [_weather_row("Raymond James Stadium", "Syracuse Orange", "2026-11-14T10:00:00+00:00")],
    )
    temp, wind, precip, dome = weather.resolve_cfb_game_weather(
        tmp_path,
        "Syracuse Orange",
        "2026-11-14",
        event_start_utc=KICKOFF,
        observed_at_utc=DECISION_AT.isoformat(),
        is_neutral_site=True,
        venue_name="Raymond James Stadium",
        venue_indoor=False,
    )
    assert (temp, wind, precip, dome) == (68.0, 18.6, 0.0, False)
    # Home-stadium branch is unchanged for non-neutral games.
    assert weather.resolve_cfb_game_weather(tmp_path, "Syracuse Orange", "2026-11-14") == (
        70.0,
        0.0,
        0.0,
        True,
    )


def test_capture_targets_neutral_venues_by_espn_name(monkeypatch, tmp_path) -> None:
    captured: list[tuple[str, str]] = []

    def fake_row(home_name, start, profile, game_date):
        captured.append((home_name, profile.venue_name))
        return _weather_row(profile.venue_name, home_name, "2026-11-14T10:00:00+00:00")

    monkeypatch.setattr(weather, "_pregame_forecast_row", fake_row)
    future = (datetime.now(UTC) + timedelta(days=2)).strftime("%Y-%m-%dT20:00:00Z")
    scoreboard = {
        "events": [
            _event(neutral=True, venue={"fullName": "Raymond James Stadium", "indoor": False})
            | {"date": future},
            _event(neutral=True, venue={"fullName": "AT&T Stadium", "indoor": True})
            | {"date": future, "id": "2"},
            _event(neutral=True, venue={"fullName": "Nowhere Field", "indoor": False})
            | {"date": future, "id": "3"},
        ]
    }
    result = weather.capture_cfb_pregame_weather(tmp_path, "2026-11-14", scoreboard=scoreboard)
    assert captured == [("Miami Hurricanes", "Raymond James Stadium")]
    assert result["neutral_venue_unresolved"] == 1
    assert result["captured"] == 1


def test_slate_neutral_dome_home_team_outdoor_bowl_is_weather_unverified(tmp_path) -> None:
    event = _event(
        home="Syracuse Orange", neutral=True, venue={"fullName": "Camping World Stadium", "indoor": False}
    )
    slate = _slate(tmp_path, event)
    total = next(c for c in slate["priced_contracts"] if c["market_type"] == "total")
    assert total["weather_inputs_verified"] is False


# --------------------------------------------------------------------------- #13 QB channel
def _snapshot(observed: str, status: str, athlete_id: str = "qb-home", name: str = "Home Starter") -> dict:
    return {
        "schema_version": "1",
        "sport": "ncaaf",
        "source": "espn_cfb_injuries",
        "observed_at_utc": observed,
        "total_injuries": 1,
        "entries": [
            {
                "team": "Miami Hurricanes",
                "team_abbreviation": "MIA",
                "player_name": name,
                "athlete_id": athlete_id,
                "position": "QB",
                "status": status,
            }
        ],
    }


def test_passing_leader_parses_the_scoreboard_competitor() -> None:
    event = _event(leaders=True)
    home = next(c for c in event["competitions"][0]["competitors"] if c["homeAway"] == "home")
    assert passing_leader_from_competitor(home) == ("qb-home", "Home Starter")
    assert passing_leader_from_competitor({"leaders": []}) == (None, None)


def test_qb_availability_mapping_and_point_in_time_rules() -> None:
    at = DECISION_AT
    fresh = _snapshot((at - timedelta(hours=3)).isoformat(), "Out")
    out = resolve_cfb_qb_availability(
        fresh,
        team="Miami Hurricanes",
        starter_athlete_id="qb-home",
        starter_name="Home Starter",
        observed_at=at,
    )
    assert (out.starter_prob, out.status, out.reason) == (0.0, "Out", "starter_listed_on_report")
    questionable = resolve_cfb_qb_availability(
        _snapshot((at - timedelta(hours=3)).isoformat(), "Questionable"),
        team="Miami Hurricanes",
        starter_athlete_id="qb-home",
        starter_name="Home Starter",
        observed_at=at,
    )
    assert questionable.starter_prob == STATUS_STARTER_PROBABILITY["Questionable"] == pytest.approx(0.7778)
    # Report captured after the decision time can never be used.
    later = resolve_cfb_qb_availability(
        _snapshot((at + timedelta(minutes=1)).isoformat(), "Out"),
        team="Miami Hurricanes",
        starter_athlete_id="qb-home",
        starter_name="Home Starter",
        observed_at=at,
    )
    assert (later.starter_prob, later.reason) == (1.0, "snapshot_after_decision_time")
    stale = resolve_cfb_qb_availability(
        _snapshot((at - timedelta(hours=60)).isoformat(), "Out"),
        team="Miami Hurricanes",
        starter_athlete_id="qb-home",
        starter_name="Home Starter",
        observed_at=at,
    )
    assert (stale.starter_prob, stale.reason) == (1.0, "snapshot_stale")
    # Another team's QB with the same name is not our starter.
    other = resolve_cfb_qb_availability(
        fresh,
        team="Duke Blue Devils",
        starter_athlete_id="qb-away",
        starter_name="Home Starter",
        observed_at=at,
    )
    assert (other.starter_prob, other.status) == (1.0, "not_listed")
    assert (
        resolve_cfb_qb_availability(
            None,
            team="Miami Hurricanes",
            starter_athlete_id="qb-home",
            starter_name="Home Starter",
            observed_at=at,
        ).reason
        == "no_injury_snapshot_before_decision"
    )
    assert (
        resolve_cfb_qb_availability(
            fresh, team="Miami Hurricanes", starter_athlete_id=None, starter_name=None, observed_at=at
        ).reason
        == "starter_unresolved"
    )


def test_latest_snapshot_is_chosen_by_its_own_observed_at(tmp_path) -> None:
    root = tmp_path / "availability" / "ncaaf" / "espn_injury_snapshots" / "2026-11-14"
    root.mkdir(parents=True)
    (root / "a.json").write_text(json.dumps(_snapshot("2026-11-14T06:00:00+00:00", "Out")), encoding="utf-8")
    (root / "b.json").write_text(
        json.dumps(_snapshot("2026-11-14T12:00:00+00:00", "Questionable")), encoding="utf-8"
    )
    (root / "c.json").write_text(json.dumps(_snapshot("2026-11-14T15:00:00+00:00", "Out")), encoding="utf-8")
    chosen = load_latest_injury_snapshot(tmp_path, DECISION_AT)
    assert chosen is not None and chosen["observed_at_utc"] == "2026-11-14T12:00:00+00:00"
    assert load_latest_injury_snapshot(tmp_path, datetime(2026, 11, 14, 5, tzinfo=UTC)) is None


def test_slate_populates_the_qb_channel_from_the_captured_report(tmp_path) -> None:
    root = tmp_path / "availability" / "ncaaf" / "espn_injury_snapshots" / "2026-11-14"
    root.mkdir(parents=True)
    (root / "a.json").write_text(json.dumps(_snapshot("2026-11-14T10:00:00+00:00", "Out")), encoding="utf-8")
    with_report = _slate(tmp_path, _event(leaders=True))
    without = _slate(tmp_path, _event(leaders=False))
    fb = next(c for c in with_report["priced_contracts"] if c["market_type"] == "spread")["feature_basis"]
    fb0 = next(c for c in without["priced_contracts"] if c["market_type"] == "spread")["feature_basis"]
    assert fb["qb_availability"]["home"]["status"] == "Out"
    assert fb["qb_availability"]["home"]["starter_prob"] == 0.0
    assert fb["qb_availability"]["away"]["status"] == "not_listed"
    assert fb["home_qb_value_adjustment"] == -4.5
    assert fb0["home_qb_value_adjustment"] == 0.0
    assert fb0["qb_availability"]["home"]["reason"] == "starter_unresolved"
    assert fb["uncertainty"] >= fb0["uncertainty"] + 0.04 - 1e-9 or fb["uncertainty"] == pytest.approx(0.22)
    assert fb["projected_margin_home"] < fb0["projected_margin_home"]


# --------------------------------------------------------------------------- #9 / #12 Main gates
def _contract(market: str, selection: str, line, **overrides) -> dict:
    base = {
        "event_id": "fixture-game",
        "event_start_utc": "2030-09-07T20:00:00Z",
        "away_team": "Alabama Crimson Tide",
        "home_team": "Georgia Bulldogs",
        "market_type": market,
        "selection": selection,
        "line": line,
        "executable_ask": 0.55,
        "model_probability": 0.60,
        "model_uncertainty": 0.10,
        "edge_vs_executable_ask": 0.05,
        "model_version": "cfb-structural-v4",
        "rationale": "fixture",
        "weather_inputs_verified": True,
        "feature_basis": {"is_fbs_vs_fcs": False, "projected_total": 60.0},
    }
    base.update(overrides)
    return base


def _run_forecast(tmp_path, monkeypatch, contracts, model_cfg=None, run: str = "run"):
    from model_prediction.cli import forecast
    from model_prediction.ledger import PickLedger

    # Ledgers persist to disk, so each call in a test gets its own directory.
    tmp_path = tmp_path / run
    tmp_path.mkdir()
    fixture = {"status": "research", "model_code_hash": "fixture", "priced_contracts": contracts}
    monkeypatch.setattr(college_football, "build_cfb_slate", lambda **kwargs: fixture)
    monkeypatch.setattr(forecast, "utc_now", lambda: datetime(2030, 9, 7, 18, tzinfo=UTC))
    flat = PickLedger(tmp_path / "flat.xlsx", audit_path=tmp_path / "ef.jsonl", tier="flat", sport="ncaaf")
    main = PickLedger(tmp_path / "main.xlsx", audit_path=tmp_path / "em.jsonl", tier="main", sport="ncaaf")
    cfg = {"min_edge": 0.005, "status": "shadow_qualified"}
    cfg.update(model_cfg or {})
    result = forecast._forecast_cfb_sport(
        data_root=tmp_path,
        args_date="2030-09-07",
        config={"models": {"NCAAF": cfg}, "project": {"maximum_data_age_hours": 24}, "bankroll": {}},
        flat_ledger=flat,
        main_ledger=main,
    )
    return result, flat, main


def test_artifact_gates_block_fcs_heavy_spreads_and_long_dogs_from_main(tmp_path, monkeypatch) -> None:
    contracts = [
        _contract("moneyline", "home", None, feature_basis={"is_fbs_vs_fcs": True}),
        _contract("spread", "home", -24.5),
        _contract(
            "moneyline", "away", None, executable_ask=0.30, model_probability=0.36, event_id="dog-game"
        ),
        _contract("total", "over", 48.5),
    ]
    result, _flat, main = _run_forecast(tmp_path, monkeypatch, contracts)
    assert result["flat_logged"] == 4
    assert [row["market_type"] for row in main.rows()] == ["total"]
    reasons = {r["reason"] for r in result["main_rejections"]}
    assert {"fcs_blocked", "spread_line_above_max", "moneyline_ask_below_floor"} <= reasons


def test_total_gates_come_from_config_and_the_dead_edge_clause_is_gone(tmp_path, monkeypatch) -> None:
    hot = _contract(
        "total", "over", 48.5, model_probability=0.70, executable_ask=0.55, edge_vs_executable_ask=0.15
    )
    # No config block: the former hardcoded p > 0.62 gate no longer exists; max_edge still applies.
    result, _, main = _run_forecast(tmp_path, monkeypatch, [hot], run="hot")
    assert main.rows() == []
    assert {r["reason"] for r in result["main_rejections"]} == {"excessive_edge"}
    moderate = _contract("total", "under", 57.5, model_probability=0.60, executable_ask=0.55)
    result, _, main = _run_forecast(tmp_path, monkeypatch, [moderate], run="ungated")
    assert len(main.rows()) == 1
    result, _, main = _run_forecast(
        tmp_path,
        monkeypatch,
        [moderate],
        model_cfg={"main_total_gates": {"under_min_line": 56.5}},
        run="gated",
    )
    assert main.rows() == []
    assert {r["reason"] for r in result["main_rejections"]} == {"total_under_line_above_config_min"}


def test_live_config_carries_the_total_gates_and_dropped_the_dead_keys() -> None:
    import yaml

    cfg = yaml.safe_load((PROJECT_ROOT / "config" / "model.yaml").read_text(encoding="utf-8"))["models"][
        "NCAAF"
    ]
    assert cfg["main_total_gates"] == {
        "max_model_probability": 0.62,
        "under_min_line": 56.5,
        "min_projection_gap": 1.5,
    }
    for dead in ("max_moneyline_american_odds", "blowout_spread_edge_cap", "spread_sd"):
        assert dead not in cfg


# --------------------------------------------------------------------------- LOW items
def test_code_revision_hashes_the_serving_source_not_the_version_string(tmp_path) -> None:
    digest = cfb_serving_code_hash()
    assert len(digest) == 16 and int(digest, 16) >= 0
    assert digest != hashlib.sha256(MODEL_VERSION.encode("utf-8")).hexdigest()[:16]
    assert _slate(tmp_path, _event())["model_code_hash"] == digest


def test_shared_rng_advances_across_games_and_replays_exactly() -> None:
    model = cfb_model()
    game = SimpleNamespace(
        event_id="e",
        event_start_utc=KICKOFF,
        away_team="Duke Blue Devils",
        home_team="Miami Hurricanes",
        spread_home_line=-3.5,
        total_line=50.5,
    )
    first = model.predict_matchup([], game)
    second = model.predict_matchup([], game)
    assert first[0].inference_inputs["rng_state_before"] != second[0].inference_inputs["rng_state_before"]
    for group in (first, second):
        for prediction in group:
            assert (
                replay_inputs(prediction.inference_inputs, prediction.market_type) == prediction.probabilities
            )
    assert model.distribution_engine.margin_sd == model.margin_sd
