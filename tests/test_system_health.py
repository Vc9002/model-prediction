"""Tests for evidence-based system health (consolidation A-3, part 1)."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from model_prediction.production_canary import _compute_artifact_hash
from model_prediction.run_supervisor import RunSupervisor
from model_prediction.system_health import system_health


def _iso_days_ago(days: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data), encoding="utf-8")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _make_artifact(model_id: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model_version": model_id,
        "sport": "wnba",
        "schema_version": "1",
        "market_models": {"moneyline": {"confidence_threshold": 0.5}},
        "qualification": {"hit_rate": 0.6, "calls": 10, "hits": 6},
    }
    payload["artifact_hash"] = _compute_artifact_hash(payload)
    return payload


def _make_repo(tmp_path: Path) -> Path:
    """A valid repo: v3 production config + one artifact."""
    repo = tmp_path / "repo"
    _write_yaml(
        repo / "config" / "production.yaml",
        {
            "schema_version": "3",
            "prediction_service": {
                "enabled": True,
                "mode": "production",
                "primary": {
                    "sport": "WNBA",
                    "market": "moneyline",
                    "model_id": "wnba-elo-trend-lr-v4",
                },
                "models": [
                    {
                        "model_id": "wnba-elo-trend-lr-v4",
                        "sport": "WNBA",
                        "market": "moneyline",
                        "implementation": "json_artifact",
                        "artifact": "config/models/wnba-elo-trend-lr-v4.json",
                        "enabled": True,
                    }
                ],
                "fallback_action": "no_prediction",
            },
            "execution": {"automated_orders": False, "manual_orders_only": True},
            "health": {"max_data_age_minutes": 120},
        },
    )
    _write_json(
        repo / "config/models/wnba-elo-trend-lr-v4.json",
        _make_artifact("wnba-elo-trend-lr-v4"),
    )
    return repo


def _seed_successful_run(repo: Path, worker: str = "daily") -> None:
    sup = RunSupervisor(
        repo_root=repo, db_path=repo / "data" / "runs.db", heartbeat_interval_seconds=0.05
    )
    sup.run_worker(worker, command=[sys.executable, "-c", "pass"])
    sup.close()


def _seed_prediction_state(repo: Path, minutes_ago: float) -> None:
    _write_json(
        repo / "data" / "production_state.json",
        {
            "last_prediction_utc": (
                datetime.now(UTC) - timedelta(minutes=minutes_ago)
            ).isoformat()
        },
    )


def _seed_game(repo: Path, sport: str, days_ago: float) -> None:
    path = repo / "data" / "historical" / f"{sport}_games_all.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"event_id": f"{sport}-1", "event_start_utc": _iso_days_ago(days_ago)})
            + "\n"
        )


def _seed_market(repo: Path, sport: str, days_ago: float) -> None:
    d = (datetime.now(UTC) - timedelta(days=days_ago)).date().isoformat()
    (repo / "data" / "odds" / sport / d).mkdir(parents=True, exist_ok=True)


# ── aggregation ─────────────────────────────────────────────────────────────


def test_fully_healthy_system_is_healthy(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    for worker in ("daily", "production", "rebuild-shadow"):
        _seed_successful_run(repo, worker)
    _seed_prediction_state(repo, minutes_ago=5)
    _seed_game(repo, "mlb", days_ago=1)
    _seed_game(repo, "nfl", days_ago=90)  # offseason: informational, not stale
    _seed_market(repo, "mlb", days_ago=1)

    report = system_health(repo_root=repo, runtime_root=repo / "data")

    assert report["status"] == "HEALTHY", report["reasons"]
    assert report["checks"]["registry"]["primary"] == "wnba-elo-trend-lr-v4"
    assert report["checks"]["runs"]["daily"]["status"] == "completed"


def test_failed_worker_run_is_down(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    sup = RunSupervisor(
        repo_root=repo, db_path=repo / "data" / "runs.db", heartbeat_interval_seconds=0.05
    )
    sup.run_worker("daily", command=[sys.executable, "-c", "import sys; sys.exit(1)"])
    sup.close()

    report = system_health(repo_root=repo, runtime_root=repo / "data")

    assert report["status"] == "DOWN"
    assert any("last run failed" in r for r in report["reasons"])


def test_worker_never_run_under_supervisor_degrades(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)

    report = system_health(repo_root=repo, runtime_root=repo / "data")

    assert report["status"] == "DEGRADED"
    assert any("never run" in r for r in report["reasons"])


def test_stale_prediction_degrades(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _seed_prediction_state(repo, minutes_ago=300)  # max_data_age_minutes=120

    report = system_health(repo_root=repo, runtime_root=repo / "data")

    assert report["status"] == "DEGRADED"
    assert any("last production prediction" in r for r in report["reasons"])


def test_recently_active_then_quiet_sport_is_flagged_offseason_is_not(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _seed_game(repo, "mlb", days_ago=10)  # quiet for 10 days: stale
    _seed_game(repo, "nfl", days_ago=200)  # offseason: informational

    report = system_health(repo_root=repo, runtime_root=repo / "data")

    assert report["status"] == "DEGRADED"
    assert any("mlb game capture stale" in r for r in report["reasons"])
    assert not any("nfl" in r for r in report["reasons"])
    assert report["checks"]["game_capture"]["mlb"]["stale"] is True
    assert report["checks"]["game_capture"]["nfl"]["stale"] is False


def test_latest_event_is_a_max_scan_not_the_last_line(tmp_path: Path) -> None:
    """Historical JSONL files are append-ordered by INGEST time: a
    backfilled batch of older games can land at the end of the file after
    newer events (confirmed live 2026-08-14: the 07-19/21 reconciliation
    appended 31 games after the 08-13 games). The health check must take
    the max event_start_utc, not the last line."""
    repo = _make_repo(tmp_path)
    _seed_game(repo, "mlb", days_ago=1)  # appended first
    _seed_game(repo, "mlb", days_ago=50)  # backfill appended LAST

    report = system_health(repo_root=repo, runtime_root=repo / "data")

    assert report["checks"]["game_capture"]["mlb"]["age_hours"] < 24 * 2
    assert report["checks"]["game_capture"]["mlb"]["stale"] is False


def test_stale_market_snapshots_flagged(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _seed_market(repo, "wnba", days_ago=5)

    report = system_health(repo_root=repo, runtime_root=repo / "data")

    assert report["status"] == "DEGRADED"
    assert any("wnba market snapshots stale" in r for r in report["reasons"])


def test_broken_registry_primary_is_down(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "config/models/wnba-elo-trend-lr-v4.json").unlink()

    report = system_health(repo_root=repo, runtime_root=repo / "data")

    assert report["status"] == "DOWN"
    assert any("registry failed to load" in r for r in report["reasons"])
