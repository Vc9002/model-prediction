import json
from datetime import UTC, datetime

import pytest

from model_prediction import learned_forward
from model_prediction.data_sources import espn_probables
from model_prediction.features.base import FeatureStore
from model_prediction.learned_forward import build_learned_moneyline_slate
from model_prediction.models.learned_market import build_artifact


class FakeESPN:
    def scoreboard(self, league: str, game_date: str) -> dict:
        assert league == "MLB"
        assert game_date == "2026-07-17"
        return {
            "events": [
                {
                    "id": "future-1",
                    "date": "2026-07-17T23:00:00Z",
                    "competitions": [
                        {
                            "competitors": [
                                {
                                    "homeAway": "away",
                                    "team": {"displayName": "Away Team"},
                                    "probables": [
                                        {
                                            "name": "probableStartingPitcher",
                                            "athlete": {"displayName": "Away Pitcher Name"},
                                        }
                                    ],
                                },
                                {
                                    "homeAway": "home",
                                    "team": {"displayName": "Home Team"},
                                    "probables": [
                                        {
                                            "name": "probableStartingPitcher",
                                            "athlete": {"displayName": "Home Pitcher Name"},
                                        }
                                    ],
                                },
                            ]
                        }
                    ],
                }
            ]
        }


class FakeMultiLeagueESPN:
    """Records call order and returns one distinct event per league."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def scoreboard(self, league: str, game_date: str) -> dict:
        assert game_date == "2026-07-17"
        self.calls.append(league)
        return {
            "events": [
                {
                    "id": f"future-{league}",
                    "date": "2026-07-17T23:00:00Z",
                    "competitions": [
                        {
                            "competitors": [
                                {"homeAway": "away", "team": {"displayName": f"{league} Away"}},
                                {"homeAway": "home", "team": {"displayName": f"{league} Home"}},
                            ]
                        }
                    ],
                }
            ]
        }


def _write_history(root) -> None:
    path = root / "processed/mlb/games.jsonl"
    path.parent.mkdir(parents=True)
    rows = [
        {
            "event_id": f"history-{index}",
            "event_start_utc": f"2026-05-{index % 28 + 1:02d}T12:00:00Z",
            "league": "MLB",
            "away_team": "Away Team",
            "home_team": "Home Team",
            "away_score": 2 + index % 2,
            "home_score": 5,
        }
        for index in range(60)
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _write_artifact(path, *, qualified: bool, sport: str = "mlb") -> None:
    artifact = build_artifact(
        sport=sport,
        model_version="mlb-elo-trend-lr-test",
        market_models={
            "moneyline": {
                "feature_names": ["elo_probability", "trend_gap"],
                "coefficients": [0.0, 0.0],
                "intercept": 3.0,
                "confidence_threshold": 0.8,
                "positive_class": "home",
            }
        },
        training={"market_inputs_used": False},
        qualification={"qualified": qualified},
    )
    path.write_text(json.dumps(artifact), encoding="utf-8")


def test_forward_slate_uses_artifact_gate_and_point_in_time_features(tmp_path) -> None:
    _write_history(tmp_path)
    artifact_path = tmp_path / "artifact.json"
    _write_artifact(artifact_path, qualified=True)

    candidates, skipped, scheduled = build_learned_moneyline_slate(
        sport="mlb",
        game_date="2026-07-17",
        store=FeatureStore(tmp_path),
        client=FakeESPN(),
        artifact_path=artifact_path,
        observed_at=datetime(2026, 7, 17, 12, tzinfo=UTC),
    )

    assert scheduled == 1
    assert skipped == []
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.selection == "home"
    assert candidate.call is True
    assert candidate.action == "QUALIFIED_SHADOW_CALL"
    assert candidate.feature_basis["history_games"] == 60
    assert len(candidate.feature_snapshot_hash) == 64


def test_unqualified_artifact_can_only_create_research_observation(tmp_path) -> None:
    _write_history(tmp_path)
    artifact_path = tmp_path / "artifact.json"
    _write_artifact(artifact_path, qualified=False)

    candidates, _, _ = build_learned_moneyline_slate(
        sport="mlb",
        game_date="2026-07-17",
        store=FeatureStore(tmp_path),
        client=FakeESPN(),
        artifact_path=artifact_path,
        observed_at=datetime(2026, 7, 17, 12, tzinfo=UTC),
    )

    assert candidates[0].action == "QUALIFIED_SHADOW_CALL"  # All calls treated equally; user decides


def test_pitcher_gap_served_from_history_and_starter_gap_defaults_neutral_when_unresolved(
    tmp_path, monkeypatch
) -> None:
    """Train/serve unification: pitcher_era_gap is the shared rolling
    runs-allowed gap computed from cached history (never an ESPN starter
    lookup). starter_era_gap (real live provider added 2026-08-04, see
    features/starter_history.py) must default to neutral 0.0 + an
    unavailable-features note when its underlying mlb_statsapi history is
    unresolvable, exactly matching validation.py's own training-time
    fallback for the identical case (_load_starter_era_map.get(event_id,
    0.0)) -- never fail the whole game closed, which would silently
    diverge live behavior from what was actually walk-forward validated."""
    _write_history(tmp_path)
    artifact_path = tmp_path / "artifact.json"
    artifact = build_artifact(
        sport="mlb",
        model_version="mlb-pitcher-gap-test",
        market_models={
            "moneyline": {
                "feature_names": ["pitcher_era_gap"],
                "coefficients": [-1.0],
                "intercept": 3.0,
                "confidence_threshold": 0.8,
                "positive_class": "home",
            }
        },
        training={"market_inputs_used": False},
        qualification={"qualified": False},
    )
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    def espn_must_not_be_called(*args, **kwargs) -> float:
        raise AssertionError("pitcher_era_gap must never consult ESPN probables")

    monkeypatch.setattr(espn_probables, "espn_pitcher_era_gap", espn_must_not_be_called)
    learned_forward._FEATURE_PROVIDERS.clear()

    candidates, skipped, scheduled = build_learned_moneyline_slate(
        sport="mlb",
        game_date="2026-07-17",
        store=FeatureStore(tmp_path),
        client=FakeESPN(),
        artifact_path=artifact_path,
        observed_at=datetime(2026, 7, 17, 12, tzinfo=UTC),
    )
    assert scheduled == 1
    assert len(candidates) == 1  # gap computed from history, no skip
    assert "pitcher_era_gap" in candidates[0].feature_basis

    starter_artifact = build_artifact(
        sport="mlb",
        model_version="mlb-starter-required-test",
        market_models={
            "moneyline": {
                "feature_names": ["starter_era_gap"],
                "coefficients": [-1.0],
                "intercept": 3.0,
                "confidence_threshold": 0.8,
                "positive_class": "home",
            }
        },
        training={"market_inputs_used": False},
        qualification={"qualified": False},
    )
    starter_path = tmp_path / "starter-artifact.json"
    starter_path.write_text(json.dumps(starter_artifact), encoding="utf-8")
    learned_forward._slate_cache.clear()
    candidates, skipped, scheduled = build_learned_moneyline_slate(
        sport="mlb",
        game_date="2026-07-17",
        store=FeatureStore(tmp_path),
        client=FakeESPN(),
        artifact_path=starter_path,
        observed_at=datetime(2026, 7, 17, 12, tzinfo=UTC),
    )

    learned_forward._FEATURE_PROVIDERS.clear()
    assert scheduled == 1
    assert skipped == []  # neutral default, not a skip
    assert len(candidates) == 1
    assert candidates[0].feature_basis["starter_era_gap"] == 0.0
    assert "NO_CALL_STARTER_ERA_GAP" in candidates[0].unavailable_features[0]


def test_starter_era_gap_served_live_from_real_matching_starter_history(tmp_path, monkeypatch) -> None:
    """The other side of the fallback test above: when both confirmed
    starters DO have resolvable real history, starter_era_gap must compute
    the real value (features/starter_history.py), not the neutral default —
    proving the end-to-end wiring (ESPN probable name -> mlb_statsapi
    snapshot lookup -> _compute_features) actually works, not just that it
    fails safe when it can't."""
    _write_history(tmp_path)
    snapshot_path = tmp_path / "mlb_statsapi_snapshots.jsonl"

    def _start(date_str, player_id, name, side, earned_runs):
        other = "away" if side == "home" else "home"
        return {
            "game_start_utc": date_str,
            side: {
                "team_name": "X",
                "pitcher_order": [player_id],
                "players": [
                    {
                        "player_id": player_id,
                        "name": name,
                        "pitching": {"inningsPitched": "6.0", "earnedRuns": earned_runs},
                    }
                ],
            },
            other: {"team_name": "Y", "pitcher_order": [], "players": []},
        }

    # Home starter: 1 ER/start (ERA 1.5). Away starter: 4 ER/start (ERA 6.0).
    # gap = home - away = 1.5 - 6.0 = -4.5 -- unambiguously a real computed
    # value, not the 0.0 neutral fallback the test above already covers.
    rows = [_start(f"2026-05-{d:02d}T18:00:00Z", 1, "Home Pitcher Name", "home", 1) for d in (1, 8, 15)] + [
        _start(f"2026-05-{d:02d}T18:00:00Z", 2, "Away Pitcher Name", "away", 4) for d in (1, 8, 15)
    ]
    snapshot_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    # starter_era_gap_live's snapshot_path default is bound at function-
    # definition time, not looked up dynamically -- patching the module
    # constant alone doesn't reach a call that omits snapshot_path=, so
    # patch the name learned_forward's own `from ... import` binding
    # points at instead, forcing the real function to always use this
    # test's isolated path.
    from model_prediction.features import starter_history

    starter_history._STARTER_INDEX_CACHE.clear()
    real_starter_era_gap_live = starter_history.starter_era_gap_live
    monkeypatch.setattr(
        learned_forward,
        "starter_era_gap_live",
        lambda home, away, decision: real_starter_era_gap_live(
            home, away, decision, snapshot_path=snapshot_path
        ),
    )

    artifact_path = tmp_path / "artifact.json"
    artifact = build_artifact(
        sport="mlb",
        model_version="mlb-starter-era-live-test",
        market_models={
            "moneyline": {
                "feature_names": ["starter_era_gap"],
                "coefficients": [-1.0],
                "intercept": 3.0,
                "confidence_threshold": 0.8,
                "positive_class": "home",
            }
        },
        training={"market_inputs_used": False},
        qualification={"qualified": False},
    )
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    learned_forward._FEATURE_PROVIDERS.clear()
    learned_forward._slate_cache.clear()

    candidates, skipped, scheduled = build_learned_moneyline_slate(
        sport="mlb",
        game_date="2026-07-17",
        store=FeatureStore(tmp_path),
        client=FakeESPN(),
        artifact_path=artifact_path,
        observed_at=datetime(2026, 7, 17, 12, tzinfo=UTC),
    )

    learned_forward._FEATURE_PROVIDERS.clear()
    assert scheduled == 1
    assert skipped == []
    assert len(candidates) == 1
    assert candidates[0].feature_basis["starter_era_gap"] == pytest.approx(-4.5)
    assert candidates[0].unavailable_features == ()


def test_bullpen_weakness_gap_served_live_from_real_relief_functions(tmp_path, monkeypatch) -> None:
    """v7 (2026-07-30) added bullpen_weakness_gap to the production moneyline
    model -- confirmed live that no feature provider existed for it at all
    (every real game skipped with "missing learned features"). This locks in
    the fix: the feature must be computed live via features/bullpen.py's
    real team_recent_relief_lines/bullpen_profile (the same functions
    Measured Edge already serves live with), not left unwired."""
    _write_history(tmp_path)
    artifact_path = tmp_path / "artifact.json"
    artifact = build_artifact(
        sport="mlb",
        model_version="mlb-bullpen-gap-test",
        market_models={
            "moneyline": {
                "feature_names": ["bullpen_weakness_gap"],
                "coefficients": [-1.0],
                "intercept": 3.0,
                "confidence_threshold": 0.8,
                "positive_class": "home",
            }
        },
        training={"market_inputs_used": False},
        qualification={"qualified": False},
    )
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    learned_forward._FEATURE_PROVIDERS.clear()
    learned_forward._slate_cache.clear()

    candidates, skipped, scheduled = build_learned_moneyline_slate(
        sport="mlb",
        game_date="2026-07-17",
        store=FeatureStore(tmp_path),
        client=FakeESPN(),
        artifact_path=artifact_path,
        observed_at=datetime(2026, 7, 17, 12, tzinfo=UTC),
    )
    assert scheduled == 1
    assert skipped == []  # not "missing learned features" -- the regression this guards against
    assert len(candidates) == 1
    assert "bullpen_weakness_gap" in candidates[0].feature_basis
    assert isinstance(candidates[0].feature_basis["bullpen_weakness_gap"], float)


def test_starter_fip_and_kbb_gaps_home_minus_away_sign_convention(tmp_path) -> None:
    """Feature audit 2026-08-16: starter_fip_gap / starter_kbb_gap had zero
    direct test coverage. Both must be (home - away), computed from each
    pitcher's last N starts strictly before the decision time."""
    import json

    from model_prediction.features.starter_history import (
        starter_fip_gap_live,
        starter_kbb_gap_live,
    )

    def snapshot(day, side, pitcher_id, name, innings, so, bb, hr, hbp, bf=24):
        return {
            "game_start_utc": f"2026-07-{day:02d}T00:00:00Z",
            side: {
                "pitcher_order": [pitcher_id],
                "players": [
                    {
                        "player_id": pitcher_id,
                        "name": name,
                        "pitching": {
                            "inningsPitched": innings,
                            "strikeOuts": so,
                            "baseOnBalls": bb,
                            "homeRuns": hr,
                            "hitBatsmen": hbp,
                            "battersFaced": bf,
                        },
                    }
                ],
            },
        }

    snapshots = [
        # Home starter: 6 IP, 24 BF, 9 K, 1 BB, 0 HR, 0 HBP per start (elite FIP & K-BB%)
        snapshot(1, "home", "h1", "Home Ace", "6.0", 9, 1, 0, 0, bf=24),
        snapshot(5, "home", "h1", "Home Ace", "6.0", 9, 1, 0, 0, bf=24),
        # Away starter: 5 IP, 25 BF, 3 K, 4 BB, 2 HR, 1 HBP per start (poor FIP & K-BB%)
        snapshot(2, "away", "a1", "Away Arms", "5.0", 3, 4, 2, 1, bf=25),
        snapshot(6, "away", "a1", "Away Arms", "5.0", 3, 4, 2, 1, bf=25),
    ]
    snap_path = tmp_path / "game_snapshots.jsonl"
    snap_path.write_text("\n".join(json.dumps(s) for s in snapshots) + "\n", encoding="utf-8")

    decision = datetime(2026, 7, 10, tzinfo=UTC)
    fip_gap = starter_fip_gap_live("Home Ace", "Away Arms", decision, snapshot_path=snap_path)
    # home FIP = (0 + 3*1 - 2*9)/6 + 3.10 = (3-18)/6 + 3.10 = 0.60
    # away FIP = (13*2 + 3*5 - 2*3)/5 + 3.10 = (26+15-6)/5 + 3.10 = 10.10
    # gap = 0.60 - 10.10 = -9.50 (home better -> negative, home-minus-away)
    assert fip_gap == pytest.approx(-9.5, abs=0.01)

    kbb_gap = starter_kbb_gap_live("Home Ace", "Away Arms", decision, snapshot_path=snap_path)
    # home K-BB% = (9-1)/24 = 0.3333; away = (3-4)/25 = -0.0400; gap = 0.3733
    assert kbb_gap == pytest.approx(0.3733, abs=0.01)


def test_multi_league_scoreboard_fetch_is_concurrent_but_order_preserving(tmp_path) -> None:
    """2026-08-23 perf fix: soccer passes up to 18 ESPN leagues via `leagues=`.
    They must be fetched concurrently (not one-at-a-time), but the combined
    event list must stay in the same league order as a sequential fetch would
    have produced, so downstream dedup/ordering stays deterministic."""
    root = tmp_path / "data"
    path = root / "processed/soccer/games.jsonl"
    path.parent.mkdir(parents=True)
    rows = [
        {
            "event_id": f"history-{index}",
            "event_start_utc": f"2026-05-{index % 28 + 1:02d}T12:00:00Z",
            "league": "SOCCER",
            "away_team": "Away Team",
            "home_team": "Home Team",
            "away_score": 1,
            "home_score": 2,
        }
        for index in range(60)
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    artifact_path = tmp_path / "soccer_artifact.json"
    _write_artifact(artifact_path, qualified=True, sport="soccer")

    fake = FakeMultiLeagueESPN()
    leagues = ("EPL", "LA_LIGA", "BUNDESLIGA")
    candidates, skipped, scheduled = build_learned_moneyline_slate(
        sport="soccer",
        game_date="2026-07-17",
        store=FeatureStore(root),
        client=fake,
        artifact_path=artifact_path,
        observed_at=datetime(2026, 7, 17, 12, tzinfo=UTC),
        leagues=leagues,
    )

    assert set(fake.calls) == set(leagues)
    assert scheduled == len(leagues)
    event_ids_in_order = [c.event_id for c in candidates] if candidates else [s["event_id"] for s in skipped]
    assert event_ids_in_order == [f"future-{league}" for league in leagues]
