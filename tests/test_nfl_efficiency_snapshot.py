"""Point-in-time NFL source-vintage and projected-QB invariants."""

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import polars as pl

from model_prediction.cli.forecast import _load_nfl_injuries_known_at, _nfl_situational_features
from model_prediction.data_sources.nfl_efficiency_snapshot import (
    build_efficiency_frames,
    capture_nfl_efficiency,
    load_efficiency_snapshot,
    projected_qbs,
)
from model_prediction.features.nfl_efficiency import NFLEfficiencyStore


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
