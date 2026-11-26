"""Nightly Auto-Buyer file backups (copy only, dated, 30-day retention) and the dashboard's
single-instance bind (ops 2026-11-25). Temp dirs only; nothing touches the runtime root."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _backup_module():
    spec = importlib.util.spec_from_file_location(
        "backup_runtime_databases", ROOT / "scripts" / "backup_runtime_databases.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _seed_runtime(root: Path) -> dict[Path, bytes]:
    files = {
        Path("auto_buyer_ledger.jsonl"): b'{"order_id": "live-1"}\n',
        Path("auto_buyer_state.json"): b'{"enabled": true, "_state_version": 1230}\n',
        Path("logs/auto_buyer_cycle_history.jsonl"): b'{"pass": "cohort_live"}\n',
        Path("paper_auto_buyer/auto_buyer_ledger.jsonl"): b'{"order_id": "PAPER-1"}\n',
        Path("paper_auto_buyer/logs/auto_buyer_cycle_history.jsonl"): b'{"pass": "global"}\n',
    }
    for relative, data in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_bytes(data)
    return files


def test_auto_buyer_files_are_copied_dated_and_the_originals_untouched(tmp_path):
    backup = _backup_module()
    runtime, dest = tmp_path / "runtime", tmp_path / "backups"
    files = _seed_runtime(runtime)
    before = {rel: ((runtime / rel).stat().st_mtime_ns, (runtime / rel).read_bytes()) for rel in files}
    results = backup.backup_auto_buyer_files(
        runtime, dest, "20261126T080000Z", lock_path=tmp_path / "ab.lock"
    )
    assert [r["status"] for r in results] == ["ok"] * 5
    assert all(r["locked"] for r in results)
    for relative, data in files.items():
        assert (dest / "auto_buyer" / "20261126T080000Z" / relative).read_bytes() == data
        assert ((runtime / relative).stat().st_mtime_ns, (runtime / relative).read_bytes()) == before[
            relative
        ]


def test_missing_auto_buyer_file_is_skipped_not_failed(tmp_path):
    backup = _backup_module()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "auto_buyer_state.json").write_text("{}", encoding="utf-8")
    results = backup.backup_auto_buyer_files(runtime, tmp_path / "backups", "20261126T080000Z")
    assert sorted(r["status"] for r in results) == ["ok"] + ["skipped_missing"] * 4


def test_auto_buyer_copy_goes_ahead_when_the_buyer_lock_stays_held(tmp_path):
    from model_prediction.daily_lock import acquire_lock

    backup = _backup_module()
    runtime = tmp_path / "runtime"
    _seed_runtime(runtime)
    lock = tmp_path / "ab.lock"
    held = acquire_lock(lock)
    try:
        results = backup.backup_auto_buyer_files(
            runtime, tmp_path / "backups", "20261126T080000Z", lock_path=lock, lock_wait_seconds=0.2
        )
    finally:
        held.close()
    assert [r["status"] for r in results] == ["ok"] * 5
    assert not any(r["locked"] for r in results)


def test_retention_prunes_by_stamp_after_30_days_and_keeps_everything_else(tmp_path):
    backup = _backup_module()
    base = tmp_path / "backups" / "auto_buyer"
    now = datetime(2026, 12, 30, 8, 0, tzinfo=UTC)
    stamps = [(now - timedelta(days=d)).strftime("%Y%m%dT%H%M%SZ") for d in (45, 31, 29, 0)]
    for stamp in stamps:
        (base / stamp).mkdir(parents=True)
        (base / stamp / "auto_buyer_ledger.jsonl").write_text("x", encoding="utf-8")
    (base / "operator-keep").mkdir()
    (tmp_path / "backups" / "ledgers.20261101T080001Z.db").write_text("db", encoding="utf-8")
    pruned = backup.prune_auto_buyer_backups(tmp_path / "backups", 30, now)
    assert sorted(Path(p).name for p in pruned) == sorted(stamps[:2])
    assert sorted(p.name for p in base.iterdir()) == sorted([*stamps[2:], "operator-keep"])
    assert (tmp_path / "backups" / "ledgers.20261101T080001Z.db").exists()


def test_retention_never_prunes_the_only_copy(tmp_path):
    backup = _backup_module()
    old = tmp_path / "backups" / "auto_buyer" / "20250101T000000Z"
    old.mkdir(parents=True)
    assert backup.prune_auto_buyer_backups(tmp_path / "backups", 30, datetime(2026, 12, 1, tzinfo=UTC)) == []
    assert old.exists()


# ------------------------------------------------------------------ dashboard single instance


@pytest.mark.skipif(sys.platform != "win32", reason="SO_REUSEADDR admits a second listener only on Windows")
def test_second_dashboard_cannot_bind_a_port_a_live_dashboard_holds():
    import dashboard_server

    first = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler, bind_and_activate=False)
    dashboard_server._bind_single_instance(first)
    port = first.server_address[1]
    try:
        second = ThreadingHTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler, bind_and_activate=False)
        with pytest.raises(OSError):
            dashboard_server._bind_single_instance(second)
        # The pre-fix launch path (class-level allow_reuse_address) is refused too.
        old_style = ThreadingHTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler, bind_and_activate=False)
        old_style.allow_reuse_address = True
        with pytest.raises(OSError):
            old_style.server_bind()
        old_style.server_close()
    finally:
        first.server_close()


def test_dashboard_main_exits_quietly_when_the_port_is_taken(monkeypatch, capsys, tmp_path, patch_dash):
    import dashboard_server

    holder = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler, bind_and_activate=False)
    dashboard_server._bind_single_instance(holder)
    port = holder.server_address[1]
    monkeypatch.setattr("sys.argv", ["dashboard_server.py", "--port", str(port)])
    patch_dash("PID_FILE", tmp_path / "server.pid")
    patch_dash("LOG_FILE", tmp_path / "server.log")
    patch_dash("_hydrate_jobs", lambda: None)
    patch_dash("enable_background_priority", lambda: True)
    started: list[bool] = []
    monkeypatch.setattr(dashboard_server, "_warm_caches", lambda **_k: started.append(True))
    try:
        dashboard_server.main()
    finally:
        holder.server_close()
    assert "busy" in capsys.readouterr().err
    assert started == []
    assert not (tmp_path / "server.pid").exists()


def test_nightly_main_backs_up_databases_and_auto_buyer_files(monkeypatch, tmp_path):
    import sqlite3

    backup = _backup_module()
    runtime = tmp_path / "runtime"
    files = _seed_runtime(runtime)
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(runtime))
    from model_prediction.runtime_paths import RuntimePaths

    paths = RuntimePaths.resolve(require_external_runtime=True)
    for db in (paths.ledgers_db, paths.runs_db, paths.production_db):
        db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE t (x INTEGER)")
    old = runtime / "backups" / "auto_buyer" / "20200101T000000Z"
    old.mkdir(parents=True)
    monkeypatch.setattr("sys.argv", ["backup_runtime_databases.py"])
    assert backup.main() == 0
    dated = sorted((runtime / "backups" / "auto_buyer").iterdir())
    assert len(dated) == 1 and dated[0] != old  # the 2020 copy aged out, today's stays
    for relative, data in files.items():
        assert (dated[0] / relative).read_bytes() == data
        assert (runtime / relative).read_bytes() == data
    assert sorted(p.name.split(".")[0] for p in (runtime / "backups").glob("*.db")) == [
        "ledgers",
        "production",
        "runs",
    ]
