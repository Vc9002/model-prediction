import json
from datetime import timedelta

from model_prediction.domain import utc_now
from model_prediction.mlb_baseline_refresh import (
    compute_league_rates,
    compute_park_factors,
    refresh_if_due,
    update_league_rates_in_formula_spec,
    update_league_relief_era_in_bullpen_module,
    write_park_factors_file,
)


def _write_games(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def test_compute_park_factors_shrinks_thin_samples_toward_one(tmp_path):
    games_path = tmp_path / "mlb_games_all.jsonl"
    rows = []
    # Team A: strong hitter's park, 30 games averaging 12 runs (well above league).
    for i in range(30):
        rows.append(
            {
                "event_id": f"a{i}",
                "event_start_utc": f"2026-0{1 + i % 6}-01T00:00Z",
                "home_team": "Team A",
                "away_team": "Team X",
                "home_score": 6,
                "away_score": 6,
                "status": "completed",
            }
        )
    # Team B: only 20 games (right at the minimum), also high-scoring --
    # should shrink harder toward 1.0 than Team A despite an identical raw average.
    for i in range(20):
        rows.append(
            {
                "event_id": f"b{i}",
                "event_start_utc": f"2026-0{1 + i % 6}-01T00:00Z",
                "home_team": "Team B",
                "away_team": "Team X",
                "home_score": 6,
                "away_score": 6,
                "status": "completed",
            }
        )
    # League baseline: lots of neutral 4-4 games for Team C, keeps the league
    # average well below A/B's 12 so both parks show up as real outliers.
    for i in range(60):
        rows.append(
            {
                "event_id": f"c{i}",
                "event_start_utc": f"2026-0{1 + i % 6}-01T00:00Z",
                "home_team": "Team C",
                "away_team": "Team X",
                "home_score": 4,
                "away_score": 4,
                "status": "completed",
            }
        )
    _write_games(games_path, rows)

    factors, sizes, _start, _end, league_rptg = compute_park_factors(
        games_path, prior_games=50.0, min_games=20
    )
    assert set(factors) == {"Team A", "Team B", "Team C"}
    assert sizes["Team A"] == 30
    assert sizes["Team B"] == 20
    # Both A and B have identical raw run environments, but B has fewer games
    # and must shrink closer to 1.0 than A.
    assert factors["Team A"] > factors["Team B"] > 1.0
    assert league_rptg is not None and league_rptg > 0


def test_compute_park_factors_excludes_legacy_and_thin_parks(tmp_path):
    games_path = tmp_path / "mlb_games_all.jsonl"
    rows = [
        {
            "event_id": "z1",
            "event_start_utc": "2026-01-01T00:00Z",
            "home_team": "Oakland Athletics",
            "away_team": "Team X",
            "home_score": 4,
            "away_score": 4,
            "status": "completed",
        }
    ] * 25 + [
        {
            "event_id": f"w{i}",
            "event_start_utc": "2026-01-01T00:00Z",
            "home_team": "Too Few Games Team",
            "away_team": "Team X",
            "home_score": 4,
            "away_score": 4,
            "status": "completed",
        }
        for i in range(5)
    ]
    _write_games(games_path, rows)
    factors, _sizes, _start, _end, league_rptg = compute_park_factors(games_path, min_games=20)
    assert "Oakland Athletics" not in factors
    assert "Too Few Games Team" not in factors
    assert factors == {}
    assert league_rptg is None


def test_compute_league_rates_separates_starters_from_relievers(tmp_path):
    snap_path = tmp_path / "game_snapshots.jsonl"
    snapshot = {
        "game_start_utc": "2026-01-01T00:00:00Z",
        "home": {
            "team_name": "Home Team",
            "pitcher_order": [1, 101],
            "players": [
                {
                    "player_id": 1,
                    "pitching": {
                        "inningsPitched": "6.0",
                        "earnedRuns": 3,
                        "strikeOuts": 6,
                        "baseOnBalls": 2,
                        "battersFaced": 25,
                    },
                },
                {
                    "player_id": 101,
                    "pitching": {
                        "inningsPitched": "3.0",
                        "earnedRuns": 3,
                        "strikeOuts": 2,
                        "baseOnBalls": 1,
                        "battersFaced": 12,
                    },
                },
            ],
        },
        "away": {"team_name": "Away Team", "pitcher_order": [], "players": []},
    }
    with snap_path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot) + "\n")

    rates, n_games, _start, _end = compute_league_rates(snap_path)
    assert n_games == 1
    # Starter: 3 earned runs / 6 innings -> era 4.5
    assert rates["league_starter_era"] == 4.5
    assert rates["league_strikeout_rate"] == round(6 / 25, 4)
    assert rates["league_walk_rate"] == round(2 / 25, 4)
    # Reliever: 3 earned runs / 3 innings -> era 9.0
    assert rates["league_relief_era"] == 9.0


def test_update_league_rates_in_formula_spec_preserves_comments(tmp_path):
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(
        "# a hand-written comment that must survive\n"
        "league_runs_per_team_game: 4.5\n"
        "league_starter_era: 4.2\n"
        "other_field: 1.0\n",
        encoding="utf-8",
    )
    changed = update_league_rates_in_formula_spec(
        spec_path, {"league_starter_era": 4.19, "league_relief_era": 4.0}
    )
    assert changed is True
    text = spec_path.read_text(encoding="utf-8")
    assert "# a hand-written comment that must survive" in text
    assert "league_starter_era: 4.19" in text
    assert "league_runs_per_team_game: 4.5" in text  # untouched, not in the patch dict
    assert "other_field: 1.0" in text
    # league_relief_era doesn't live in this file -- must not be inserted.
    assert "league_relief_era" not in text


def test_update_league_relief_era_in_bullpen_module(tmp_path):
    bullpen_path = tmp_path / "bullpen.py"
    bullpen_path.write_text("LEAGUE_RELIEF_ERA = 4.10\nother = 1\n", encoding="utf-8")
    changed = update_league_relief_era_in_bullpen_module(bullpen_path, 4.05)
    assert changed is True
    assert "LEAGUE_RELIEF_ERA = 4.05" in bullpen_path.read_text(encoding="utf-8")


def test_write_park_factors_file_round_trips(tmp_path):
    out_path = tmp_path / "park_factors.py"
    write_park_factors_file(
        out_path,
        {"Team A": 1.05, "Team B": 0.95},
        {"Team A": 100, "Team B": 80},
        "2026-01-01",
        "2026-06-01",
        180,
    )
    namespace: dict = {}
    exec(compile(out_path.read_text(encoding="utf-8"), str(out_path), "exec"), namespace)
    assert namespace["PARK_RUN_FACTORS"] == {"Team A": 1.05, "Team B": 0.95}
    assert namespace["park_factor"]("Team A")["park_factor"] == 1.05
    assert namespace["park_factor"]("Unknown")["status"] == "unavailable_from_source"


def test_refresh_if_due_throttles_on_recent_state(tmp_path):
    data_root = tmp_path / "data"
    project_root = tmp_path / "project"
    (data_root / "historical").mkdir(parents=True)
    (data_root / "mlb_statsapi").mkdir(parents=True)
    (data_root / "historical" / "mlb_games_all.jsonl").write_text("", encoding="utf-8")
    (data_root / "mlb_statsapi" / "game_snapshots.jsonl").write_text("", encoding="utf-8")
    state_path = data_root / "mlb_baseline_refresh_state.json"
    state_path.write_text(
        json.dumps({"last_refreshed_utc": utc_now().isoformat()}), encoding="utf-8"
    )
    result = refresh_if_due(data_root, project_root, min_days=7.0)
    assert result["status"] == "skipped_recent"


def test_refresh_if_due_runs_when_forced_even_if_recent(tmp_path):
    data_root = tmp_path / "data"
    project_root = tmp_path / "project"
    (data_root / "historical").mkdir(parents=True)
    (data_root / "mlb_statsapi").mkdir(parents=True)
    (project_root / "src/model_prediction/features").mkdir(parents=True)
    (project_root / "config/models").mkdir(parents=True)
    games_path = data_root / "historical" / "mlb_games_all.jsonl"
    _write_games(
        games_path,
        [
            {
                "event_id": f"a{i}",
                "event_start_utc": "2026-01-01T00:00Z",
                "home_team": "Team A",
                "away_team": "Team X",
                "home_score": 5,
                "away_score": 5,
                "status": "completed",
            }
            for i in range(25)
        ],
    )
    (data_root / "mlb_statsapi" / "game_snapshots.jsonl").write_text("", encoding="utf-8")
    (project_root / "config/models/mlb-analyst-poisson-trend-v0.3.yaml").write_text(
        "league_runs_per_team_game: 4.5\n", encoding="utf-8"
    )
    (project_root / "src/model_prediction/features/bullpen.py").write_text(
        "LEAGUE_RELIEF_ERA = 4.10\n", encoding="utf-8"
    )
    state_path = data_root / "mlb_baseline_refresh_state.json"
    state_path.write_text(
        json.dumps({"last_refreshed_utc": (utc_now() - timedelta(days=1)).isoformat()}),
        encoding="utf-8",
    )

    result = refresh_if_due(data_root, project_root, min_days=7.0, force=True)
    assert result["status"] == "refreshed"
    assert (project_root / "src/model_prediction/features/park_factors.py").exists()
    saved_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved_state["last_refreshed_utc"] == result["refreshed_utc"]
