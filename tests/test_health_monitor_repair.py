"""Scheduled health monitor reads and repairs the canonical runtime odds store."""

import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from model_prediction.data_sources import polymarket_us
from model_prediction.runtime_paths import RuntimePaths
from scripts import health_and_data_gap_monitor as monitor


def test_odds_audit_reads_runtime_root_and_eastern_date(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    old_file = runtime / "odds" / "esports" / "2020-01-01" / "polymarket_snapshots.jsonl"
    old_file.parent.mkdir(parents=True)
    old_file.write_text("{}\n", encoding="utf-8")
    misleading = repo / "data" / "odds" / "esports" / today / "polymarket_snapshots.jsonl"
    misleading.parent.mkdir(parents=True)
    misleading.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(monitor, "PROJECT_ROOT", repo)

    result = monitor.audit_odds_freshness(RuntimePaths(repo, runtime))
    assert result["status"] == "WARN"
    assert result["stale_active_sports"] == ["esports"]
    assert result["sports"]["esports"]["latest_date"] == "2020-01-01"


def test_switched_off_sports_are_not_stale_odds(monkeypatch, tmp_path):
    """The 'basketball' odds directory is the Euroleague/NBL capture. Both were switched
    off 2026-11-13, so its silence is scope, not a feed gap; an in-scope quiet sport is."""
    import yaml

    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    (repo / "config").mkdir(parents=True)
    (repo / "config" / "model.yaml").write_text(
        yaml.safe_dump(
            {
                "models": {
                    "NBA": {"enabled": True},
                    "EUROLEAGUE": {"enabled": True, "daily_forecast_enabled": False},
                    "NBL": {"enabled": True, "daily_forecast_enabled": False},
                }
            }
        ),
        encoding="utf-8",
    )
    for sport in ("basketball", "nba"):
        f = runtime / "odds" / sport / "2020-01-01" / "polymarket_snapshots.jsonl"
        f.parent.mkdir(parents=True)
        f.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(monitor, "PROJECT_ROOT", repo)

    assert monitor.switched_off_labels() == {"euroleague", "nbl", "basketball"}
    result = monitor.audit_odds_freshness(RuntimePaths(repo, runtime))

    assert result["sports"]["basketball"]["status"] == "SWITCHED_OFF"
    assert result["sports"]["nba"]["status"] == "STALE"
    assert result["stale_active_sports"] == ["nba"]


def test_auto_heal_uses_sport_slate_and_capture_signature(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    paths = RuntimePaths(repo, runtime)
    monkeypatch.setattr(monitor, "PROJECT_ROOT", repo)
    calls = []

    class FakeClient:
        def __init__(self, *, client, max_attempts):
            assert max_attempts == 1

        def sport_slate(self, sport, game_date):
            calls.append(("slate", sport, game_date.isoformat()))
            return SimpleNamespace(events={"CS2": [{"event_id": "one"}]}, errors={})

    def capture(client, events, data_root, game_date, *, max_workers):
        calls.append(("capture", events, data_root, game_date, max_workers))
        out = runtime / "odds" / "esports" / game_date / "polymarket_snapshots.jsonl"
        out.parent.mkdir(parents=True)
        out.write_text("{}\n", encoding="utf-8")
        return {"captured": 1, "failures": 0}

    monkeypatch.setattr(polymarket_us, "PolymarketUSClient", FakeClient)
    monkeypatch.setattr(polymarket_us, "capture_slate_snapshots", capture)
    result = monitor.auto_heal_data_gaps(paths, {"stale_active_sports": ["esports"]})

    assert calls[0][:2] == ("slate", "esports")
    assert calls[1] == ("capture", {"CS2": [{"event_id": "one"}]}, runtime, calls[0][2], 2)
    assert result["healing_failures"] == []
    assert monitor.audit_odds_freshness(paths)["status"] == "OK"


def test_monitor_reports_failed_daily_worker_from_runtime(tmp_path):
    paths = RuntimePaths(tmp_path / "repo", tmp_path / "runtime")
    assert monitor.audit_daily_worker(paths)["status"] == "UNKNOWN"
    paths.runs_db.parent.mkdir(parents=True)
    with sqlite3.connect(paths.runs_db) as conn:
        conn.execute("CREATE TABLE runs (worker TEXT, status TEXT, exit_code INTEGER, started_at_utc TEXT)")
        conn.executemany(
            "INSERT INTO runs VALUES (?, ?, ?, ?)",
            [
                ("daily", "completed", 0, "2026-10-25T05:00:00Z"),
                ("daily", "failed", 1, "2026-10-26T11:30:00Z"),
            ],
        )
    assert monitor.audit_daily_worker(paths) == {
        "status": "failed",
        "exit_code": 1,
        "started_at_utc": "2026-10-26T11:30:00Z",
    }


def test_warehouse_rows_do_not_hide_stale_active_quotes(tmp_path):
    paths = RuntimePaths(tmp_path / "repo", tmp_path / "runtime")
    paths.market_quotes_db.parent.mkdir(parents=True)
    yesterday = (datetime.now(ZoneInfo("America/New_York")).date() - timedelta(days=1)).isoformat()
    with sqlite3.connect(paths.market_quotes_db) as conn:
        conn.execute(
            "CREATE TABLE market_quotes (sport TEXT, observed_at_utc TEXT, depth_liquidity_usd REAL)"
        )
        conn.execute("INSERT INTO market_quotes VALUES ('nba', '2026-10-24T20:00:00Z', 100)")
    result = monitor.audit_market_quotes_warehouse(paths)
    assert result["status"] == "STALE"
    assert result["stale_active_sports"] == ["nba"]
    with sqlite3.connect(paths.market_quotes_db) as conn:
        conn.execute("INSERT INTO market_quotes VALUES (?, ?, 100)", ("nba", yesterday + "T20:00:00Z"))
    assert monitor.audit_market_quotes_warehouse(paths)["status"] == "HEALTHY"
