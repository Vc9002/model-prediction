"""The dashboard's parsed-snapshot cache must stay bounded (2026-11-13).

Before this, every polymarket_snapshots.jsonl the dashboard ever touched stayed
resident as Python objects (543 MB on disk -> ~2.5 GB parsed) and the process
was the top consumer in Windows low-virtual-memory events.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

backtests = importlib.import_module("model_prediction.dashboard.backtests")


def _write_snapshot_file(path: Path, rows: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for i in range(rows):
            handle.write(
                json.dumps(
                    {
                        "slug": f"m-{i}",
                        "market_type": "moneyline",
                        "observed_at_utc": "2026-11-12T20:00:00+00:00",
                        "padding": "x" * 400,
                    }
                )
                + "\n"
            )
    return path


def test_snapshot_cache_evicts_least_recently_used_under_the_byte_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(backtests, "_SNAPSHOT_FILE_CACHE", backtests.OrderedDict())
    monkeypatch.setattr(backtests, "_SNAPSHOT_CACHE_BYTES", 0)
    files = [_write_snapshot_file(tmp_path / f"d{i}" / "polymarket_snapshots.jsonl", 50) for i in range(4)]
    one = files[0].stat().st_size
    # Room for two files, not three.
    monkeypatch.setattr(backtests, "_SNAPSHOT_CACHE_MAX_BYTES", int(one * 2.5))

    assert len(backtests._load_snapshot_file(files[0])) == 50
    backtests._load_snapshot_file(files[1])
    assert list(backtests._SNAPSHOT_FILE_CACHE) == [files[0], files[1]]

    backtests._load_snapshot_file(files[0])  # touch -> most recently used
    backtests._load_snapshot_file(files[2])  # evicts files[1], the LRU entry
    assert list(backtests._SNAPSHOT_FILE_CACHE) == [files[0], files[2]]
    assert backtests._SNAPSHOT_CACHE_BYTES <= backtests._SNAPSHOT_CACHE_MAX_BYTES

    # Evicted files still read correctly; a cache miss is a re-parse, never a loss.
    assert len(backtests._load_snapshot_file(files[1])) == 50
    assert files[0] not in backtests._SNAPSHOT_FILE_CACHE


def test_snapshot_cache_reparses_a_changed_file_and_keeps_one_entry_per_path(tmp_path, monkeypatch):
    monkeypatch.setattr(backtests, "_SNAPSHOT_FILE_CACHE", backtests.OrderedDict())
    monkeypatch.setattr(backtests, "_SNAPSHOT_CACHE_BYTES", 0)
    path = _write_snapshot_file(tmp_path / "d" / "polymarket_snapshots.jsonl", 3)
    assert len(backtests._load_snapshot_file(path)) == 3
    before = backtests._SNAPSHOT_CACHE_BYTES

    _write_snapshot_file(path, 6)
    import os

    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 10))
    assert len(backtests._load_snapshot_file(path)) == 6
    assert len(backtests._SNAPSHOT_FILE_CACHE) == 1
    assert backtests._SNAPSHOT_CACHE_BYTES == path.stat().st_size != before
