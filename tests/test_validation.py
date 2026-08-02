import pytest
from test_backtester import seed_games

from model_prediction.validation import (
    ValidationRow,
    _grade,
    build_production_artifact,
    chronological_split,
    evaluate_reconstructed_mlb_moneyline,
    historical_pitcher_feature_audit,
    multi_market_readiness,
    run_sport_validation,
    write_production_artifacts,
)


def _fake_validation_report(sport: str = "nba") -> dict:
    return {
        "sport": sport,
        "threshold_source": "validation only",
        "split": {
            "train": {"start": "2024-01-01", "end": "2024-06-01", "observations": 100},
            "validation": {"start": "2024-06-02", "end": "2024-08-01", "observations": 60},
            "locked_holdout": {"start": "2024-08-02", "end": "2024-10-01", "observations": 80},
        },
        "variants": {
            "elo_trend": {
                "features": ["elo_probability", "trend_gap"],
                "coefficients": {"elo_probability": 2.5, "trend_gap": 0.1},
                "intercept": -1.2,
                "primary_65": {
                    "status": "evaluated",
                    "learned_threshold": 0.62,
                    "locked_holdout": {"qualified": True, "calls": 60, "hit_rate": 0.67},
                },
            }
        },
    }


def test_validation_uses_three_disjoint_chronological_cohorts(tmp_path) -> None:
    store = seed_games(tmp_path, count=480)
    report = run_sport_validation(store, "test")
    split = report["split"]

    assert split["train"]["end"] < split["validation"]["start"]
    assert split["validation"]["end"] < split["locked_holdout"]["start"]
    assert report["variants"]["elo_only"]["features"] == ["elo_probability"]


def test_confidence_gap_is_an_exact_reparameterization() -> None:
    from model_prediction.validation import confidence_gap_equivalence

    audit = confidence_gap_equivalence({"status": "evaluated", "learned_threshold": 0.62})

    assert audit["equivalent_gap_threshold"] == 0.24
    assert audit["changes_selection_order"] is False
    assert audit["decision"] == "REJECT_AS_REDUNDANT_GATE"


def test_chronological_split_rejects_empty_data() -> None:
    with pytest.raises(ValueError, match="empty"):
        chronological_split([])


def test_reconstructed_price_diagnostic_fails_closed_without_file(tmp_path) -> None:
    report = evaluate_reconstructed_mlb_moneyline(seed_games(tmp_path), tmp_path / "missing.jsonl")
    assert report["status"] == "unavailable"


def test_production_artifact_pins_audited_coefficients_threshold_and_qualification() -> None:
    report = {
        "sport": "nba",
        "threshold_source": "validation only",
        "split": {
            "train": {"start": "2024-01-01", "end": "2024-06-01", "observations": 100},
            "validation": {"start": "2024-06-02", "end": "2024-08-01", "observations": 60},
            "locked_holdout": {"start": "2024-08-02", "end": "2024-10-01", "observations": 80},
        },
        "variants": {
            "elo_trend": {
                "features": ["elo_probability", "trend_gap"],
                "coefficients": {"elo_probability": 2.5, "trend_gap": 0.1},
                "intercept": -1.2,
                "primary_65": {
                    "status": "evaluated",
                    "learned_threshold": 0.62,
                    "locked_holdout": {"qualified": True, "calls": 60, "hit_rate": 0.67},
                },
            }
        },
    }

    artifact = build_production_artifact(report)

    assert artifact["model_version"] == "nba-elo-trend-lr-v4"
    assert artifact["market_models"]["moneyline"]["coefficients"] == [2.5, 0.1]
    assert artifact["market_models"]["moneyline"]["confidence_threshold"] == 0.62
    assert artifact["qualification"]["qualified"] is True
    assert len(artifact["artifact_hash"]) == 64


def test_write_production_artifacts_refuses_to_overwrite_existing_version(tmp_path) -> None:
    """Real bug fixed 2026-08-02: LEARNED_ARTIFACT_VERSIONS silently drifted
    stale for mlb (still said v5 after production moved to v7), and this
    writer had no guard -- rerunning validate-models --write-artifacts would
    have silently overwritten the kept v5 rollback file in place. Every
    other sport's constant already matched its live production file, so a
    rerun there would have quietly rewritten the *active* artifact under the
    same filename. The writer must now refuse outright, regardless of
    whether the version constant is current."""
    report = {"sports": {"nba": _fake_validation_report("nba")}}

    first = write_production_artifacts(report, tmp_path)
    assert (tmp_path / "nba-elo-trend-lr-v4.json").exists()
    assert first["nba"] == str(tmp_path / "nba-elo-trend-lr-v4.json")

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        write_production_artifacts(report, tmp_path)


def test_primary_qualification_rejects_a_negative_called_month() -> None:
    outcomes = [1] * 20 + [0] * 5 + [1] * 11 + [0] * 14
    rows = [
        ValidationRow(
            "2025-12-01" if index < 25 else "2026-01-01",
            str(index),
            outcome,
            0.7,
            0.0,
            1.0,
            1.0,
            False,
            False,
        )
        for index, outcome in enumerate(outcomes)
    ]

    rows.append(ValidationRow("2026-02-01", "end", 0, 0.5, 0, 1, 1, False, False))
    result = _grade([0.7] * 50 + [0.5], rows, 0.6, qualification_eligible=True)

    assert result["hit_rate"] == 0.62
    assert result["every_called_month_positive_at_minus_110"] is False
    assert result["qualified"] is False
    assert "2026-01" in result["failures"][-1]


def test_month_with_fewer_than_ten_calls_is_reported_but_not_a_gate() -> None:
    rows = [
        ValidationRow("2025-12-01", str(index), outcome, 0.7, 0, 1, 1, False, False)
        for index, outcome in enumerate([1] * 41 + [0] * 9)
    ]
    rows.extend(
        ValidationRow("2026-01-01", f"jan-{index}", 0, 0.7, 0, 1, 1, False, False)
        for index in range(9)
    )
    rows.append(ValidationRow("2026-02-01", "end", 0, 0.5, 0, 1, 1, False, False))

    result = _grade([0.7] * 59 + [0.5], rows, 0.6, qualification_eligible=True)

    january = next(month for month in result["monthly_at_minus_110"] if month["month"] == "2026-01")
    assert january["calls"] == 9
    assert january["qualification_status"] == "insufficient_calls"
    assert result["qualified"] is True


def test_incomplete_final_month_is_provisional_even_with_ten_calls() -> None:
    rows = [
        ValidationRow("2025-12-01", str(index), outcome, 0.7, 0, 1, 1, False, False)
        for index, outcome in enumerate([1] * 41 + [0] * 9)
    ]
    rows.extend(
        ValidationRow("2026-01-15", f"jan-{index}", 0, 0.7, 0, 1, 1, False, False)
        for index in range(10)
    )

    result = _grade([0.7] * 60, rows, 0.6, qualification_eligible=True)

    january = next(month for month in result["monthly_at_minus_110"] if month["month"] == "2026-01")
    assert january["qualification_status"] == "partial_month"
    assert result["qualified"] is True


def test_historical_pitcher_audit_rejects_unversioned_retroactive_stats(tmp_path) -> None:
    import json

    path = tmp_path / "raw/mlb/2025-04-01/scores_mlb.json"
    path.parent.mkdir(parents=True)
    probable = {"playerId": "1", "statistics": [{"name": "ERA", "displayValue": "2.50"}]}
    payload = {
        "events": [
            {
                "competitions": [
                    {
                        "competitors": [
                            {"homeAway": "home", "probables": [probable]},
                            {"homeAway": "away", "probables": [probable]},
                        ]
                    }
                ]
            }
        ]
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    audit = historical_pitcher_feature_audit(seed_games(tmp_path))

    assert audit["both_starter_era_values"] == 1
    assert audit["point_in_time_valid"] is False
    assert audit["decision"] == "REJECT_HISTORICAL_PITCHER_FEATURES_LEAKAGE_RISK"


def test_bullpen_weakness_gap_requires_two_prior_games_and_uses_pit_history(
    tmp_path, monkeypatch
) -> None:
    import json

    import model_prediction.validation as validation_module
    from model_prediction.validation import _bullpen_weakness_gap

    def snapshot(event_start_utc, home_relief, away_relief):
        def side(team_name, relief):
            players = [
                {"player_id": 100 + i, "pitching": {"inningsPitched": ip, "earnedRuns": er}}
                for i, (ip, er) in enumerate(relief)
            ]
            return {
                "team_name": team_name,
                "pitcher_order": [1] + [100 + i for i in range(len(relief))],
                "players": players,
            }

        return {
            "game_start_utc": event_start_utc,
            "home": side("Home Team", home_relief),
            "away": side("Away Team", away_relief),
        }

    # Games 1-2: home bullpen throws thirds-notation "2.1" + "0.2" innings
    # (2 + 1/3 and 0 + 2/3 == 3.0 exactly) for 3 earned runs (era 9); away
    # bullpen throws 3.0 shutout innings (era 0). Game 3 is the query target,
    # with a deliberately extreme relief line of its own to prove it can't
    # leak into its own feature.
    games = [
        ("2026-01-01T00:00:00Z", [("2.1", 2), ("0.2", 1)], [("3.0", 0)]),
        ("2026-01-02T00:00:00Z", [("2.1", 2), ("0.2", 1)], [("3.0", 0)]),
        ("2026-01-03T00:00:00Z", [("1.0", 100)], [("1.0", 100)]),
    ]

    snap_path = tmp_path / "data/mlb_statsapi/game_snapshots.jsonl"
    snap_path.parent.mkdir(parents=True)
    crosswalk_path = tmp_path / "data/processed/mlb/games.jsonl"
    crosswalk_path.parent.mkdir(parents=True)

    with snap_path.open("w", encoding="utf-8") as handle:
        for start, home_relief, away_relief in games:
            handle.write(json.dumps(snapshot(start, home_relief, away_relief)) + "\n")

    event_ids = [f"evt-{i}" for i in range(len(games))]
    with crosswalk_path.open("w", encoding="utf-8") as handle:
        for (start, _, _), eid in zip(games, event_ids, strict=True):
            handle.write(
                json.dumps(
                    {
                        "event_start_utc": start,
                        "home_team": "Home Team",
                        "away_team": "Away Team",
                        "event_id": eid,
                    }
                )
                + "\n"
            )

    monkeypatch.setattr(validation_module, "PROJECT_ROOT", tmp_path)
    validation_module._BULLPEN_MAP = None
    try:
        gap0, available0 = _bullpen_weakness_gap(event_ids[0])
        gap1, available1 = _bullpen_weakness_gap(event_ids[1])
        assert (gap0, available0) == (0.0, False)
        assert (gap1, available1) == (0.0, False)

        gap2, available2 = _bullpen_weakness_gap(event_ids[2])
        assert available2 is True
        # Hand-computed from games 1-2 only: home era 9.0 (6 innings / 6 earned),
        # away era 0.0, league_relief_era 4.10, then credibility-weighted
        # shrinkage toward league_relief_era by BULLPEN_PRIOR_INNINGS=30
        # (features/bullpen.py -- 6 real innings is nowhere near enough to
        # trust an ERA at full confidence) -- game 3's own 100-earned-run
        # line must not shift this.
        from model_prediction.features.bullpen import BULLPEN_PRIOR_INNINGS, LEAGUE_RELIEF_ERA

        credibility = 6 / (6 + BULLPEN_PRIOR_INNINGS)
        home_weakness = (credibility * 9.0 + (1 - credibility) * LEAGUE_RELIEF_ERA) / LEAGUE_RELIEF_ERA
        away_weakness = (credibility * 0.0 + (1 - credibility) * LEAGUE_RELIEF_ERA) / LEAGUE_RELIEF_ERA
        assert gap2 == round(home_weakness - away_weakness, 6)
    finally:
        validation_module._BULLPEN_MAP = None


def test_bullpen_fatigue_gap_uses_trailing_calendar_window_not_self(
    tmp_path, monkeypatch
) -> None:
    import json

    import model_prediction.validation as validation_module
    from model_prediction.validation import _bullpen_fatigue_gap

    def snapshot(event_start_utc, home_relief_ip, away_relief_ip):
        def side(team_name, ip):
            return {
                "team_name": team_name,
                "pitcher_order": [1, 101],
                "players": [{"player_id": 101, "pitching": {"inningsPitched": ip, "earnedRuns": 0}}],
            }

        return {
            "game_start_utc": event_start_utc,
            "home": side("Home Team", home_relief_ip),
            "away": side("Away Team", away_relief_ip),
        }

    # Day 0 and day 5 are both within the 3-day window of day 2, but day 0 is
    # more than 3 days before day 5, so only day 2's relief work should count
    # toward the query game on day 5. The query game's own (extreme) relief
    # line must not leak into its own fatigue figure.
    games = [
        ("2026-02-01T00:00:00Z", "3.0", "2.0"),
        ("2026-02-03T00:00:00Z", "1.1", "0.2"),
        ("2026-02-06T00:00:00Z", "9.0", "9.0"),
    ]

    snap_path = tmp_path / "data/mlb_statsapi/game_snapshots.jsonl"
    snap_path.parent.mkdir(parents=True)
    crosswalk_path = tmp_path / "data/processed/mlb/games.jsonl"
    crosswalk_path.parent.mkdir(parents=True)

    with snap_path.open("w", encoding="utf-8") as handle:
        for start, home_ip, away_ip in games:
            handle.write(json.dumps(snapshot(start, home_ip, away_ip)) + "\n")

    event_ids = [f"evt-{i}" for i in range(len(games))]
    with crosswalk_path.open("w", encoding="utf-8") as handle:
        for (start, _, _), eid in zip(games, event_ids, strict=True):
            handle.write(
                json.dumps(
                    {
                        "event_start_utc": start,
                        "home_team": "Home Team",
                        "away_team": "Away Team",
                        "event_id": eid,
                    }
                )
                + "\n"
            )

    monkeypatch.setattr(validation_module, "PROJECT_ROOT", tmp_path)
    validation_module._BULLPEN_FATIGUE_MAP = None
    try:
        gap0, available0 = _bullpen_fatigue_gap(event_ids[0])
        assert (gap0, available0) == (0.0, True)

        gap2, available2 = _bullpen_fatigue_gap(event_ids[2])
        assert available2 is True
        # Only game 1 (day 2) is within 3 days of day 5; game 0 (day 0) is not,
        # and game 2's own 9.0/9.0 relief line must not leak into itself.
        assert gap2 == round((1 + 1 / 3) - (0 + 2 / 3), 6)
    finally:
        validation_module._BULLPEN_FATIGUE_MAP = None


def test_score_sports_multimarket_validation_requires_exact_lines(tmp_path) -> None:
    for sport in ("nba", "wnba", "nfl"):
        readiness = multi_market_readiness(seed_games(tmp_path), sport)

        assert readiness["model_parameters_changed"] is False
        assert readiness["spread"] == "BLOCKED_MISSING_HISTORICAL_CONTRACT_LINES"
        assert readiness["total"] == "BLOCKED_MISSING_HISTORICAL_CONTRACT_LINES"
