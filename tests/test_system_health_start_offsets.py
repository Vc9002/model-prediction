"""Stale-open watcher compares event starts as instants, not strings (NBA review follow-up).

Moneyline ledger rows store starts in the ``-0500`` offset form (``2026-11-19T20:00:00-0500``) while
other rows use ``Z``. SQLite compared the stored text with an ISO cutoff, so a -0500 start read five
hours early and a game 20 h old was reported as stuck past 24 h.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from model_prediction.runtime_paths import RuntimePaths
from model_prediction.system_health import _stale_open_rows

EST = timezone(timedelta(hours=-5))


def test_offset_form_starts_are_compared_as_instants(tmp_path: Path) -> None:
    paths = RuntimePaths(repo_root=tmp_path, runtime_root=tmp_path / "data")
    paths.ledgers_db.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    rows = [
        # 20 h ago in the moneyline -0500 form: NOT stale (its text reads 25 h ago).
        ("ml-20h", "nba", (now - timedelta(hours=20)).astimezone(EST).strftime("%Y-%m-%dT%H:%M:%S%z")),
        # 30 h ago, -0500 form: stale (>24 h), not >72 h.
        ("ml-30h", "nba", (now - timedelta(hours=30)).astimezone(EST).strftime("%Y-%m-%dT%H:%M:%S%z")),
        # 80 h ago, Z form: stale and >72 h.
        ("sp-80h", "nfl", (now - timedelta(hours=80)).strftime("%Y-%m-%dT%H:%MZ")),
        ("bad", "nba", "not a time"),
    ]
    connection = sqlite3.connect(paths.ledgers_db)
    connection.execute(
        "CREATE TABLE ledger_records (pick_id TEXT, sport TEXT, event_start_utc TEXT, market_type TEXT, status TEXT)"
    )
    connection.executemany(
        "INSERT INTO ledger_records VALUES (?, ?, ?, 'moneyline', 'open')",
        rows,
    )
    connection.commit()
    connection.close()

    check = _stale_open_rows(paths)
    assert [row["pick_id"] for row in check["samples"]] == ["sp-80h", "ml-30h"]
    assert check["open_over_24h"] == 2
    assert check["open_over_72h"] == 1
    assert check["by_sport"] == {"nfl": 1, "nba": 1}
    assert check["unparseable_start"] == 1
    # Stored values are read, never rewritten.
    connection = sqlite3.connect(paths.ledgers_db)
    stored = [r[0] for r in connection.execute("SELECT event_start_utc FROM ledger_records ORDER BY rowid")]
    connection.close()
    assert stored == [row[2] for row in rows]
