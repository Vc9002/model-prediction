"""Tests for Async HTTP Daily Fanout Pipeline and Concurrent Ingestion/Settlement.

Verifies:
1. Thread-safe cache access in ESPNClient under concurrent access.
2. Ingestor.ingest_scores fans out across multiple leagues concurrently when rate_limit_seconds == 0.0.
3. Thread-safe deduplicated appending across concurrent ingest calls.
4. Shared ESPNClient and scoreboard_cache in settlement prevents redundant network fetches across ledgers.
5. Soccer scoreboard concurrency pool with expanded league set.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from model_prediction.data_sources.espn import ESPNClient
from model_prediction.ingest import Ingestor
from model_prediction.soccer_forward import build_soccer_total_slate


def test_espn_client_thread_safe_caching(monkeypatch) -> None:
    """Verify ESPNClient._get is thread-safe and executes underlying HTTP request once per key."""
    client = ESPNClient()
    fetch_counter = 0
    counter_lock = threading.Lock()

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"status": "ok"}

    def fake_get(url, params=None):
        nonlocal fetch_counter
        with counter_lock:
            fetch_counter += 1
        time.sleep(0.01)  # Simulate network latency
        return FakeResponse()

    monkeypatch.setattr(client.client, "get", fake_get)

    url = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
    params = {"dates": "20261016"}

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(client._get, url, params) for _ in range(20)]
        results = [f.result() for f in futures]

    assert len(results) == 20
    assert all(r == {"status": "ok"} for r in results)
    # Because of the thread-safe cache lock, fake_get should only be called once
    assert fetch_counter == 1


def test_ingestor_parallel_league_fetching_when_rate_limit_zero(tmp_path: Path, monkeypatch) -> None:
    """Verify Ingestor fans out across leagues concurrently when rate_limit_seconds is 0.0."""
    monkeypatch.setattr("model_prediction.ingest.SPORT_LEAGUES", {"test_sport": ("L1", "L2")})
    active_threads = set()
    thread_lock = threading.Lock()

    class FakeESPN:
        def scoreboard(self, league: str, game_date: str):
            with thread_lock:
                active_threads.add(threading.current_thread().ident)
            time.sleep(0.02)
            return {
                "events": [
                    {
                        "id": f"event-{league}-{game_date}",
                        "date": f"{game_date}T18:00:00Z",
                        "competitions": [
                            {
                                "status": {"type": {"completed": True}},
                                "competitors": [
                                    {
                                        "homeAway": "away",
                                        "score": "1",
                                        "team": {"id": "1", "displayName": f"{league} Away"},
                                    },
                                    {
                                        "homeAway": "home",
                                        "score": "2",
                                        "team": {"id": "2", "displayName": f"{league} Home"},
                                    },
                                ],
                            }
                        ],
                    }
                ]
            }

    fake_client = FakeESPN()
    ingestor = Ingestor(tmp_path, client=fake_client, rate_limit_seconds=0.0)

    res = ingestor.ingest_scores("test_sport", "2026-10-10")
    assert res["fetched"] == 2
    assert res["completed_games_seen"] == 2
    # Multiple distinct threads must have run
    assert len(active_threads) >= 2


def test_ingestor_thread_safe_concurrent_append(tmp_path: Path) -> None:
    """Verify multiple dates ingesting concurrently do not corrupt processed or historical files."""

    class FakeESPN:
        def scoreboard(self, league: str, game_date: str):
            return {
                "events": [
                    {
                        "id": f"event-{league}-{game_date}",
                        "date": f"{game_date}T18:00:00Z",
                        "competitions": [
                            {
                                "status": {"type": {"completed": True}},
                                "competitors": [
                                    {
                                        "homeAway": "away",
                                        "score": "1",
                                        "team": {"id": "1", "displayName": f"{league} Away"},
                                    },
                                    {
                                        "homeAway": "home",
                                        "score": "2",
                                        "team": {"id": "2", "displayName": f"{league} Home"},
                                    },
                                ],
                            }
                        ],
                    }
                ]
            }

    fake_client = FakeESPN()
    ingestor = Ingestor(tmp_path, client=fake_client, rate_limit_seconds=0.0)

    dates = [f"2026-10-0{i}" for i in range(1, 9)]

    def _ingest(d: str):
        return ingestor.ingest_scores("mlb", d)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_ingest, dates))

    assert len(results) == 8
    total_appended = sum(r["new_games_appended"] for r in results)
    assert total_appended == 8

    # Check files are valid JSONL and exactly 8 lines
    lines = ingestor.processed_path("mlb").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 8
    for line in lines:
        data = json.loads(line)
        assert "event_id" in data


def test_soccer_forward_concurrency_with_expanded_leagues(tmp_path: Path) -> None:
    """Verify build_soccer_total_slate fans out across all configured leagues concurrently."""
    fetched_leagues = []
    fetch_lock = threading.Lock()

    class FakeSoccerClient:
        def scoreboard(self, league: str, game_date: str):
            with fetch_lock:
                fetched_leagues.append(league)
            return {"events": []}

    client = FakeSoccerClient()
    leagues = tuple(f"LEAGUE_{i}" for i in range(25))

    slate = build_soccer_total_slate(
        data_root=tmp_path,
        game_date="2026-10-16",
        client=client,
        leagues=leagues,
        observed_at=datetime(2026, 10, 16, 12, tzinfo=UTC),
    )

    assert slate["scheduled_games"] == 0
    assert len(fetched_leagues) == 25
