from pathlib import Path

import pytest
from test_backtester import seed_games

from model_prediction.validation import (
    ValidationRow,
    _add_legacy_backfill,
    _grade,
    build_production_artifact,
    build_walk_forward_rows,
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
        ValidationRow("2026-01-01", f"jan-{index}", 0, 0.7, 0, 1, 1, False, False) for index in range(9)
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
        ValidationRow("2026-01-15", f"jan-{index}", 0, 0.7, 0, 1, 1, False, False) for index in range(10)
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


def test_bullpen_weakness_gap_requires_two_prior_games_and_uses_pit_history(tmp_path, monkeypatch) -> None:
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
        # The pipeline rounds the weakness index to 6 decimals INSIDE
        # bullpen_profile and this closed form rounds at the END, so the
        # two can differ by 1e-6 purely from rounding order. That was
        # masked while LEAGUE_RELIEF_ERA was 4.059 and surfaced when the
        # 2026-08-19 daily refresh recomputed the prior to 4.0598. Compare
        # with a 1e-6 tolerance: it still catches real drift in the
        # shrinkage math, but stops failing on rounding-order noise.
        assert abs(gap2 - round(home_weakness - away_weakness, 6)) <= 1e-6
    finally:
        validation_module._BULLPEN_MAP = None


def test_train_serve_parity_for_v9_features(tmp_path, monkeypatch) -> None:
    """The three MLB v9 features must compute IDENTICAL values in walk-forward
    training (validation.py) and live serving (learned_forward.py). The
    2026-08-13 audit found real train/serve definition skews on all three:
    residual_trend_gap trained on a league-wide rate (serving was already
    team-specific), park_factor trained on the PIT factor while the v8
    artifact's live serving stays on the static table (hence separate
    park_factor / park_factor_pit names), and bullpen_fatigue_gap trained on
    a 3-calendar-day window while serving used the last-N-games lookback."""
    import json
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from model_prediction import learned_forward
    from model_prediction import validation as validation_module
    from model_prediction.features.base import FeatureStore
    from model_prediction.features.elo_ratings import build_elo
    from model_prediction.features.trends import TrendEngine
    from model_prediction.learned_forward import _compute_features

    HOME, AWAY, NEUTRAL = "Colorado Rockies", "Away Team", "Neutral Team"
    store = FeatureStore(tmp_path)

    def game(eid, ts, home, away, hs, as_):
        return {
            "event_id": eid,
            "event_start_utc": ts,
            "league": "MLB",
            "home_team": home,
            "away_team": away,
            "home_score": hs,
            "away_score": as_,
        }

    games_rows = []
    # 40 pre-window games (home loses every one: 3-4). A league-wide home
    # win rate would therefore be very different from the Rockies' own
    # 1.000 -- exactly the skew the residual_trend_gap assertions must catch.
    for i in range(40):
        if i % 2:
            home, away = NEUTRAL, AWAY
        else:
            home, away = AWAY, NEUTRAL
        games_rows.append(game(f"hist-{i}", f"2026-05-{i % 28 + 1:02d}T12:00:00Z", home, away, 3, 4))
    # 29 window games (2026-06-18..2026-07-16). Rockies home on even days
    # plus 07-01 (all Rockies-home games are wins, 8-1); Away Team home on
    # the other odd days (home loses, 1-8). All totals = 9 except the
    # neutral games (7), so the PIT park factor is non-trivial.
    window_days = [f"2026-06-{d:02d}" for d in range(18, 31)] + [f"2026-07-{d:02d}" for d in range(1, 17)]
    for day in window_days:
        # 07-01/07-15/07-16 are Rockies-home so the snapshot crosswalk
        # (home/away team names) matches the games file for those snaps.
        if day in ("2026-07-01", "2026-07-15", "2026-07-16") or int(day[-2:]) % 2 == 0:
            home, away, hs, as_ = HOME, AWAY, 8, 1
        else:
            home, away, hs, as_ = AWAY, HOME, 1, 8
        games_rows.append(game(f"win-{day}", f"{day}T12:00:00Z", home, away, hs, as_))
    target = game("target-1", "2026-07-17T23:00:00Z", HOME, AWAY, 5, 3)
    games_rows.append(target)

    games_path = tmp_path / "processed/mlb/games.jsonl"
    games_path.parent.mkdir(parents=True)
    games_path.write_text("".join(json.dumps(r) + "\n" for r in games_rows), encoding="utf-8")
    # The validation map loaders key off PROJECT_ROOT (tmp_path) and read a
    # separate crosswalk file under data/processed/ -- same rows.
    crosswalk_path = tmp_path / "data/processed/mlb/games.jsonl"
    crosswalk_path.parent.mkdir(parents=True)
    crosswalk_path.write_text("".join(json.dumps(r) + "\n" for r in games_rows), encoding="utf-8")

    def side(team_name, ip):
        return {
            "team_name": team_name,
            "pitcher_order": [1, 101],
            "players": [{"player_id": 101, "pitching": {"inningsPitched": ip, "earnedRuns": 0}}],
        }

    # 07-01 is 16 days before the target -- outside the 3-day fatigue window,
    # so if serving ever reverts to the last-N-games lookback, its fatigue sum
    # jumps to home 8.0 - away 6.0 = +2.0 instead of -1.0 (asymmetric 5.0/2.0
    # lines) and the parity assertion below fails.
    snaps = [
        ("2026-07-01T12:00:00Z", "5.0", "2.0"),
        ("2026-07-15T12:00:00Z", "2.1", "1.0"),
        ("2026-07-16T12:00:00Z", "0.2", "3.0"),
        ("2026-07-17T23:00:00Z", "9.0", "9.0"),  # the query game itself: must not leak
    ]
    snap_path = tmp_path / "data/mlb_statsapi/game_snapshots.jsonl"
    snap_path.parent.mkdir(parents=True)
    with snap_path.open("w", encoding="utf-8") as handle:
        for ts, home_ip, away_ip in snaps:
            handle.write(
                json.dumps({"game_start_utc": ts, "home": side(HOME, home_ip), "away": side(AWAY, away_ip)})
                + "\n"
            )

    monkeypatch.setattr(validation_module, "PROJECT_ROOT", tmp_path)
    for cache in (
        "_BULLPEN_FATIGUE_MAP",
        "_BULLPEN_MAP",
        "_STARTER_ERA_MAP",
        "_STARTER_FIP_MAP",
        "_STARTER_KBB_MAP",
    ):
        setattr(validation_module, cache, None)

    try:
        # ── Training side: the real walk-forward row builder ───────────────
        rows = build_walk_forward_rows(store, "mlb")
        target_row = next(row for row in rows if row.event_id == "target-1")

        # ── Serving side: the real live feature computation ────────────────
        learned_forward._FEATURE_PROVIDERS.clear()
        real_lines = learned_forward.team_recent_relief_lines
        monkeypatch.setattr(
            learned_forward,
            "team_recent_relief_lines",
            lambda team, decision, **kwargs: real_lines(team, decision, snapshot_path=snap_path, **kwargs),
        )
        history = store.games_before("mlb", "2026-07-17")
        target_rec = next(g for g in store.load_games("mlb") if g.event_id == "target-1")
        elo = build_elo(history, "mlb")
        trends = TrendEngine(history)
        artifact = SimpleNamespace(
            raw={
                "market_models": {
                    "moneyline": {
                        "feature_names": [
                            "residual_trend_gap",
                            "park_factor",
                            "park_factor_pit",
                            "bullpen_fatigue_gap",
                        ]
                    }
                }
            }
        )
        served, unavailable = _compute_features(
            "mlb",
            artifact,
            HOME,
            AWAY,
            "target-1",
            "2026-07-17",
            target_rec.start,
            history,
            elo,
            trends.team_trend(HOME),
            trends.team_trend(AWAY),
            tmp_path,
            datetime(2026, 7, 17, 12, tzinfo=UTC),
        )

        # residual_trend_gap: team-specific 30-day window, >=10-team-game
        # gate (16 Rockies home games here), 0.0 fallback -- identical.
        assert target_row.residual_trend_gap != 0.0  # gate passed
        assert served["residual_trend_gap"] == pytest.approx(target_row.residual_trend_gap, abs=1e-6)
        # park_factor: static table on BOTH sides (v8 contract). Compares
        # against the live PARK_RUN_FACTORS entry rather than a hardcoded
        # literal, since that table is auto-regenerated daily
        # (mlb_baseline_refresh.refresh_park_factors) and its values drift.
        from model_prediction.features.park_factors import PARK_RUN_FACTORS

        assert served["park_factor"] == target_row.park_factor == PARK_RUN_FACTORS["Colorado Rockies"]
        # ...park_factor_pit: empirical PIT factor on BOTH sides, and it
        # must differ from the static value the v8 variants consume.
        assert served["park_factor_pit"] == target_row.park_factor_pit
        assert served["park_factor_pit"] != served["park_factor"]
        # bullpen_fatigue_gap: 3-calendar-day window, exact equality.
        assert target_row.bullpen_fatigue_gap == served["bullpen_fatigue_gap"] == -1.0
        assert all(
            name not in unavailable for name in artifact.raw["market_models"]["moneyline"]["feature_names"]
        )
    finally:
        learned_forward._FEATURE_PROVIDERS.clear()
        for cache in (
            "_BULLPEN_FATIGUE_MAP",
            "_BULLPEN_MAP",
            "_STARTER_ERA_MAP",
            "_STARTER_FIP_MAP",
            "_STARTER_KBB_MAP",
        ):
            setattr(validation_module, cache, None)


def test_bullpen_fatigue_gap_uses_trailing_calendar_window_not_self(tmp_path, monkeypatch) -> None:
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


def test_add_legacy_backfill_logs_read_failure_instead_of_silently_discarding(
    tmp_path, monkeypatch, caplog
) -> None:
    """DD-3 (deep debug audit, 2026-08-04): a genuine I/O failure reading
    the legacy Polymarket flat file used to be a bare `except OSError: pass`
    -- indistinguishable from a legacy file that legitimately contributed
    nothing further. Confirms the failure is now logged (still degrades
    gracefully rather than crashing validation reporting -- that part of
    the behavior is correct and unchanged, only the observability gap is
    closed)."""
    legacy_path = tmp_path / "polymarket_us_snapshots.jsonl"
    legacy_path.write_text('{"market_slug": "asc-nba-lal-bos"}\n', encoding="utf-8")

    real_open = Path.open

    def failing_open(self, *args, **kwargs):
        if self == legacy_path:
            raise OSError("simulated disk read failure")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)

    with caplog.at_level("WARNING"):
        spread_count, total_count = _add_legacy_backfill(tmp_path, "nba", 0, 0)

    assert spread_count == 0
    assert total_count == 0
    assert any(
        "failed reading" in record.message and "polymarket_us_snapshots.jsonl" in record.message
        for record in caplog.records
    )


def test_multi_market_readiness_logs_snapshot_read_failure(tmp_path, monkeypatch, caplog) -> None:
    """DD-3: same regression as the legacy-backfill test above, for
    multi_market_readiness's own Stage 2 snapshot scan (the other silent
    `except OSError: continue` this audit item flagged)."""
    store = seed_games(tmp_path)
    snapshot_dir = store.data_root / "odds" / "nba" / "2026-01-01"
    snapshot_dir.mkdir(parents=True)
    snapshot_path = snapshot_dir / "polymarket_snapshots.jsonl"
    snapshot_path.write_text('{"market_type": "spread", "market_slug": "asc-nba-x"}\n', encoding="utf-8")

    real_open = Path.open

    def failing_open(self, *args, **kwargs):
        if self == snapshot_path:
            raise OSError("simulated disk read failure")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)

    with caplog.at_level("WARNING"):
        readiness = multi_market_readiness(store, "nba")

    assert readiness["spread"] == "BLOCKED_MISSING_HISTORICAL_CONTRACT_LINES"
    assert any(
        "failed reading" in record.message and "polymarket_snapshots.jsonl" in record.message
        for record in caplog.records
    )


def test_adaptive_hfa_features_now_serve_and_match_training_definitions(tmp_path) -> None:
    """Feature audit 2026-08-16: elo_trend_adaptive_hfa's two features
    (elo_neutral_probability, trailing_home_win_rate_30d) had NO serving
    path — the variant could never serve. Both now compute in
    learned_forward with the exact training-side definitions."""
    from datetime import UTC, datetime, timedelta

    from model_prediction.features.elo_ratings import build_elo
    from model_prediction.features.trends import TrendEngine
    from model_prediction.learned_forward import _compute_features
    from model_prediction.validation import _trailing_home_rate

    HOME, AWAY = "Home Team", "Away Team"
    base = datetime(2026, 7, 1, tzinfo=UTC)
    history = []
    # 12 home games in the last 30 days: 9 wins, 1 tie (excluded), 2 losses
    for i in range(12):
        outcome = "win" if i < 9 else ("tie" if i == 9 else "loss")
        home_score = 5 if outcome in ("win", "tie") else 2
        away_score = 5 if outcome == "tie" else (2 if outcome == "win" else 5)
        history.append(
            type(
                "G",
                (),
                {
                    "event_id": f"g{i}",
                    "start": base + timedelta(days=i),
                    "home_team": HOME,
                    "away_team": AWAY,
                    "home_score": home_score,
                    "away_score": away_score,
                    "margin": home_score - away_score,
                },
            )()
        )
    game = type(
        "G",
        (),
        {
            "event_id": "target",
            "start": base + timedelta(days=13),
            "home_team": HOME,
            "away_team": AWAY,
            "home_score": 3,
            "away_score": 1,
            "margin": 2,
        },
    )()

    artifact = type(
        "A",
        (),
        {
            "raw": {
                "market_models": {
                    "moneyline": {
                        "feature_names": ["elo_neutral_probability", "trailing_home_win_rate_30d"],
                    }
                }
            },
        },
    )()
    elo = build_elo(history, "mlb")
    trends = TrendEngine(history)
    features, unavailable = _compute_features(
        "mlb",
        artifact,
        HOME,
        AWAY,
        game.event_id,
        "2026-07-14",
        game.start,
        history,
        elo,
        trends.team_trend(HOME),
        trends.team_trend(AWAY),
        tmp_path,
        game.start,
    )
    assert unavailable == ()
    assert features["elo_neutral_probability"] == pytest.approx(elo.expected_neutral_win(HOME, AWAY))
    expected_rate, expected_games = _trailing_home_rate(history, "2026-07-14", HOME)
    assert features["trailing_home_win_rate_30d"] == pytest.approx(expected_rate)
    assert expected_games == 11  # 12 games minus the excluded tie
