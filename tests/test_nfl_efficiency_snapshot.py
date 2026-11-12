"""Point-in-time NFL source-vintage and projected-QB invariants."""

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from model_prediction.cli.forecast import _load_nfl_injuries_known_at, _nfl_situational_features
from model_prediction.data_sources.nfl_efficiency_snapshot import (
    TEAM_COLUMNS,
    build_efficiency_frames,
    capture_nfl_efficiency,
    load_efficiency_snapshot,
    projected_qbs,
)
from model_prediction.features.nfl_efficiency import NFLEfficiencyInputError, NFLEfficiencyStore


class _Response:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        pass


class _Client:
    def __init__(self, payloads: list[bytes]) -> None:
        self.payloads = iter(payloads)

    def get(self, _url: str) -> _Response:
        return _Response(next(self.payloads))


_CORE_COLUMNS = {"attempts", "carries", "passing_epa", "rushing_epa", "sacks_suffered"}


def _fixtures() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    players = pl.DataFrame(
        {
            "game_id": ["old", "future"],
            "team": ["DAL", "DAL"],
            "opponent_team": ["PHI", "PHI"],
            "player_display_name": ["Dak Prescott", "Dak Prescott"],
            "position": ["QB", "QB"],
            "attempts": [25, 50],
            "carries": [2, 5],
            "passing_epa": [5.0, 100.0],
            "rushing_epa": [1.0, 100.0],
            "sacks_suffered": [2, 5],
            "passing_cpoe": [1.0, 20.0],
            # build_efficiency_frames requires the full engine column contract
            **{col: [1, 1] for col in TEAM_COLUMNS if col not in _CORE_COLUMNS},
        }
    )
    schedule = pl.DataFrame(
        {
            "game_id": ["old", "future"],
            "gameday": ["2026-10-20", "2026-10-28"],
            "home_score": [24, None],
            "away_score": [17, None],
            "season": [2026, 2026],
        }
    )
    depth = pl.DataFrame(
        {
            "dt": ["2026-10-26T12:00:00Z", "2026-10-28T12:00:00Z"],
            "team": ["DAL", "DAL"],
            "player_name": ["Dak Prescott", "Sam Howell"],
            "pos_abb": ["QB", "QB"],
            "pos_rank": [1, 1],
        }
    )
    return players, schedule, depth


def test_unplayed_game_and_future_depth_chart_excluded() -> None:
    players, schedule, depth = _fixtures()
    observed = datetime(2026, 10, 27, 17, tzinfo=UTC)
    team, qb = build_efficiency_frames(players, schedule, observed)
    assert team.height == 1 and qb.height == 1
    assert team["passing_epa"].to_list() == [5.0]
    assert projected_qbs(depth, observed) == {"DAL": "Dak Prescott"}


def test_live_release_capture_cannot_be_backdated(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot be backdated"):
        capture_nfl_efficiency(tmp_path, observed_at=datetime(2026, 10, 1, tzinfo=UTC))


def test_capture_is_asof_hash_checked_and_team_coverage_required(tmp_path: Path) -> None:
    players, schedule, depth = _fixtures()
    observed = datetime(2026, 10, 27, 17, tzinfo=UTC)
    payloads = []
    for frame in (players, schedule, depth):
        buffer = BytesIO()
        frame.write_parquet(buffer)
        payloads.append(buffer.getvalue())
    client = _Client(payloads)
    report = capture_nfl_efficiency(tmp_path, observed_at=observed, client=client)
    assert report["latest_gameday"] == "2026-10-20"
    assert report["projected_qb_updated_utc"]["DAL"] == "2026-10-26T12:00:00Z"
    assert load_efficiency_snapshot(tmp_path, observed - timedelta(seconds=1)) is None
    store = NFLEfficiencyStore(tmp_path, observed_at=observed)
    assert store.covers_last_game("Dallas Cowboys", date(2026, 10, 20))
    assert not store.covers_last_game("Dallas Cowboys", date(2026, 10, 26))
    snapshot = Path(str(report["snapshot_path"]))
    with (snapshot / "team.parquet").open("ab") as file:
        file.write(b"tampered")
    assert load_efficiency_snapshot(tmp_path, observed) is None


def test_late_utc_game_uses_eastern_game_date_and_verified_injuries(tmp_path: Path) -> None:
    game = SimpleNamespace(
        event_start_utc="2026-10-27T00:15:00Z",
        home_team="Dallas Cowboys",
        away_team="Philadelphia Eagles",
        home_starter_name="Dak Prescott",
        away_starter_name="Tanner McKee",
    )
    dates, _ = _nfl_situational_features([game])
    assert dates["Dallas Cowboys"] == date(2026, 10, 26)
    observed = datetime(2026, 10, 27, 17, tzinfo=UTC)
    payload = {"observed_at_utc": observed.isoformat(), "entries": [{"player_name": "Dak Prescott"}]}
    payload["snapshot_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    folder = tmp_path / "availability/nfl/espn_injury_snapshots/2026-10-27"
    folder.mkdir(parents=True)
    path = folder / "capture.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    entries, source = _load_nfl_injuries_known_at(tmp_path, observed)
    assert entries[0]["player_name"] == "Dak Prescott"
    assert source is not None and source["sha256"] == payload["snapshot_sha256"]
    payload["entries"][0]["player_name"] = "Injected"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert _load_nfl_injuries_known_at(tmp_path, observed) == ([], None)


def _two_sided_fixtures() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Two completed DAL-PHI games with a QB row per side carrying every engine column."""
    players = pl.DataFrame(
        {
            "game_id": ["g1", "g1", "g2", "g2"],
            "team": ["DAL", "PHI", "DAL", "PHI"],
            "opponent_team": ["PHI", "DAL", "PHI", "DAL"],
            "player_display_name": ["Dak Prescott", "Jalen Hurts", "Dak Prescott", "Jalen Hurts"],
            "position": ["QB"] * 4,
            "attempts": [30, 28, 33, 25],
            "carries": [20, 25, 18, 27],
            "passing_epa": [6.0, -2.0, 4.0, 1.0],
            "rushing_epa": [1.0, 2.0, -0.5, 1.5],
            "sacks_suffered": [1, 4, 2, 3],
            "passing_cpoe": [2.0, -1.0, 1.5, 0.5],
            "passing_first_downs": [12, 9, 13, 8],
            "rushing_first_downs": [5, 7, 4, 6],
            "passing_20": [4, 2, 5, 2],
            "rushing_12": [2, 3, 1, 3],
            "passing_tds": [2, 1, 3, 1],
            "rushing_tds": [1, 1, 0, 2],
            "fg_made": [1, 2, 2, 1],
            "fumble_recovery_own": [1, 0, 0, 1],
            "fumble_recovery_opp": [1, 0, 1, 0],
            "fumbles_total": [1, 1, 0, 1],
            "def_fumbles_forced": [1, 1, 1, 0],
        }
    )
    schedule = pl.DataFrame(
        {
            "game_id": ["g1", "g2"],
            "gameday": ["2026-10-12", "2026-10-19"],
            "home_score": [24, 20],
            "away_score": [17, 27],
            "season": [2026, 2026],
        }
    )
    depth = pl.DataFrame(
        {
            "dt": ["2026-10-26T12:00:00Z", "2026-10-26T12:00:00Z"],
            "team": ["DAL", "PHI"],
            "player_name": ["Dak Prescott", "Jalen Hurts"],
            "pos_abb": ["QB", "QB"],
            "pos_rank": [1, 1],
        }
    )
    return players, schedule, depth


def test_snapshot_team_frame_feeds_engine_rather_than_defaults(tmp_path: Path) -> None:
    """Regression for the 2026-10-27 -> 2026-11-11 outage: a snapshot-built team frame
    must drive real efficiency inputs, never the league-average defaults tuple."""
    players, schedule, depth = _two_sided_fixtures()
    observed = datetime(2026, 10, 27, 17, tzinfo=UTC)
    payloads = []
    for frame in (players, schedule, depth):
        buffer = BytesIO()
        frame.write_parquet(buffer)
        payloads.append(buffer.getvalue())
    capture_nfl_efficiency(tmp_path, observed_at=observed, client=_Client(payloads))
    store = NFLEfficiencyStore(tmp_path, observed_at=observed)
    assert store.source_manifest is not None  # the snapshot, not the legacy parquet, is loaded
    matchup = store.get_matchup_efficiency("Dallas Cowboys", "Philadelphia Eagles", as_of_date="2026-10-27")
    assert matchup.home_prior_games == 2
    assert matchup.away_prior_games == 2
    # DAL: 63 attempts / 38 carries -> pass ratio 0.624, not the 0.58 default
    assert abs(matchup.home_pass_ratio - 63 / 101) < 1e-3  # engine rounds to 4dp
    # In a closed two-team fixture PHI's pass-rush rate equals DAL's sacks-allowed rate
    # (both 3/66), so pressure disparity is 0 by construction; assert on inputs whose
    # defaults differ from these fixtures instead: first-down rate (34/101 vs 0.30) and
    # turnover luck (recoveries 3 of 5 fumbles -> -0.1667 vs 0.0).
    assert abs(matchup.home_first_down_rate - 34 / 101) < 1e-3
    assert matchup.home_turnover_luck != 0.0
    assert matchup.home_pass_epa != 0.0


def test_team_frame_missing_engine_column_fails_closed(tmp_path: Path) -> None:
    """A schema-incomplete frame (the pre-fix four-column snapshot) must raise, not default."""
    store = NFLEfficiencyStore(tmp_path)
    with pytest.raises(NFLEfficiencyInputError, match="frame_unavailable"):
        store.get_matchup_efficiency("Dallas Cowboys", "Philadelphia Eagles", as_of_date="2026-10-27")
    store._team_df = pl.DataFrame(
        {
            "game_id": ["g1"],
            "gameday": ["2026-10-12"],
            "team": ["DAL"],
            "opponent_team": ["PHI"],
            "attempts": [30],
            "carries": [20],
            "passing_epa": [6.0],
            "rushing_epa": [1.0],
        }
    )
    with pytest.raises(NFLEfficiencyInputError, match="sacks_suffered"):
        store.get_matchup_efficiency("Dallas Cowboys", "Philadelphia Eagles", as_of_date="2026-10-27")
