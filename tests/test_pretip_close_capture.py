"""Pre-tip close capture (quick-261120-nip 3c-ii): open, in-window, archive-mapped contracts only.

No exchange request is ever made here: the client and, where named, the capture are fakes.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from model_prediction import auto_buyer_worker as worker
from model_prediction import pretip_close_capture as pc
from model_prediction.cli.settle import _closing_probability_for_moneyline_pick
from model_prediction.domain import iso_utc
from model_prediction.runtime_ledger_store import _SCHEMA

NOW = datetime(2026, 11, 21, 23, 0, tzinfo=UTC)
GAME_DATE = "2026-11-21"  # 23:00Z is 18:00 ET


def _ledger(tmp_path: Path, rows: list[dict], now: datetime = NOW) -> Path:
    path = tmp_path / "ledgers.db"
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    for i, row in enumerate(rows):
        payload = row.get("payload")
        if payload is None:
            payload = json.dumps(
                {
                    "model_input_snapshot_json": json.dumps(
                        {"features": {"research_quote_join.market_slug": row["slug"]}}
                    )
                }
            )
        conn.execute(
            "INSERT INTO ledger_records (pick_id, operation_id, ledger_tier, sport, event_start_utc, "
            "status, created_at_utc, decision_payload_json) VALUES (?,?,?,?,?,?,?,?)",
            (
                f"p{i}",
                f"op{i}",
                "flat",
                row.get("sport", "ncaab"),
                iso_utc(now + row["starts_in"]),
                row.get("status", "open"),
                "2026-11-21T12:00:00Z",
                payload,
            ),
        )
    conn.commit()
    conn.close()
    return path


def _archive(root: Path, game_date: str, records: list[dict]) -> Path:
    """The Daily's discovery records, in the real archive layout and field set."""
    path = root / "odds" / "ncaab" / game_date / "polymarket_snapshots.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    return path


def _daily_record(slug: str, *, start: str, observed: str, team: str = "Bbbb Home", line: float = -3.5):
    return {
        "event_id": "240470",
        "event_slug": "cbb-aaaa-bbbb-2026-11-21",
        "event_start_utc": start,
        "event_title": "Aaaa Away vs. Bbbb Home",
        "league": "NCAAB",
        "line": line,
        "market_id": "2752313",
        "market_slug": slug,
        "market_state": "MARKET_STATE_OPEN",
        "market_type": "spread",
        "observed_at_utc": observed,
        "team": team,
        "long": {"ask": 0.51, "description": "-3.50"},
        "short": {"ask": 0.51, "description": "+3.50"},
    }


class _FakeClient:
    """``snapshot(slug)`` in the shape ``PolymarketUSClient._build_snapshot`` returns."""

    def __init__(self, start: str, *, error: Exception | None = None, long_ask: float = 0.53) -> None:
        self.start = start
        self.error = error
        self.long_ask = long_ask
        self.snapshot_calls: list[str] = []

    def snapshot(self, slug: str) -> dict:
        self.snapshot_calls.append(slug)
        if self.error is not None:
            raise self.error
        return {
            "provider": "polymarket_us",
            "reconstructed": False,
            "reconstruction_status_source": "live_gateway_market_and_book_response",
            "market_id": "2752313",
            "market_slug": slug,
            "event_start_utc": self.start,
            "observed_at_utc": iso_utc(datetime.now(UTC)),
            "market_state": "MARKET_STATE_OPEN",
            "transact_time_utc": None,
            "line": -3.5,
            "team": "Bbbb Home",
            "long": {"description": "-3.50", "ask": self.long_ask, "bid": 0.5},
            "short": {"description": "+3.50", "ask": 0.49, "bid": 1 - self.long_ask},
        }

    def event_detail(self, slug: str) -> dict:
        raise AssertionError("the pre-tip capture must not crawl event detail")

    def sport_slate(self, *args, **kwargs):
        raise AssertionError("the pre-tip capture must not fetch the hydrated slate")


def _recording_capture(calls: list):
    def capture(client, wanted, runtime_root, game_date, **kwargs):
        calls.append({"wanted": wanted, "root": runtime_root, "date": game_date, **kwargs})
        return {"captured": sum(len(e["markets"]) for events in wanted.values() for e in events)}

    return capture


def test_only_open_in_window_archive_mapped_contracts_are_captured(tmp_path):
    start = iso_utc(NOW + timedelta(minutes=10))
    db = _ledger(
        tmp_path,
        [
            {"slug": "asc-cbb-aa-bb-2026-11-21-neg-3pt5", "starts_in": timedelta(minutes=10)},
            {"slug": "asc-cbb-started-2026-11-21", "starts_in": timedelta(minutes=-10)},
            {"slug": "asc-cbb-tips-now-2026-11-21", "starts_in": timedelta(seconds=30)},
            {"slug": "asc-cbb-later-2026-11-21", "starts_in": timedelta(hours=2)},
            {"slug": "asc-cbb-settled-2026-11-21", "starts_in": timedelta(minutes=10), "status": "settled"},
            {"slug": "aec-nba-other-sport-2026-11-21", "starts_in": timedelta(minutes=10), "sport": "nba"},
            # A malformed payload must not abort the query for every other row.
            {"slug": None, "starts_in": timedelta(minutes=10), "payload": "{not json"},
            # In the window, but the Daily never archived it: no guessed line, no capture.
            {"slug": "asc-cbb-never-archived-2026-11-21", "starts_in": timedelta(minutes=12)},
        ],
    )
    slugs = [
        "asc-cbb-aa-bb-2026-11-21-neg-3pt5",
        "asc-cbb-started-2026-11-21",
        "asc-cbb-tips-now-2026-11-21",
        "asc-cbb-later-2026-11-21",
        "asc-cbb-settled-2026-11-21",
    ]
    _archive(
        tmp_path,
        GAME_DATE,
        [_daily_record(slug, start=start, observed="2026-11-21T15:00:00Z") for slug in slugs]
        # A later Daily record for the wanted slug, written earlier in the file: max-scan wins.
        + [_daily_record(slugs[0], start=start, observed="2026-11-21T09:00:00Z", line=-9.5)],
    )
    calls: list = []
    result = pc.capture_pretip_closes(
        now=NOW,
        ledgers_db=db,
        runtime_root=tmp_path,
        client=_FakeClient(start),
        capture_fn=_recording_capture(calls),
    )
    assert result["status"] == "ok", result
    assert len(calls) == 1
    (events,) = calls[0]["wanted"].values()
    assert list(calls[0]["wanted"]) == ["NCAAB"]
    (event,) = events
    assert event["event_id"] == "240470" and event["event_slug"] == "cbb-aaaa-bbbb-2026-11-21"
    (market,) = event["markets"]
    # Copied from the newest archived record; no market_id, so the capture uses snapshot(slug).
    assert market == {
        "market_slug": "asc-cbb-aa-bb-2026-11-21-neg-3pt5",
        "market_type": "spread",
        "line": -3.5,
        "sides": [{"team": "Bbbb Home"}],
    }
    assert calls[0]["root"] == tmp_path and calls[0]["date"] == GAME_DATE
    assert 0 < calls[0]["max_capture_seconds"] <= pc.DEFAULT_MAX_CAPTURE_SECONDS
    assert result["in_window_rows"] == 3  # the mapped row, the malformed row, the unarchived row
    assert result["wanted_slugs"] == 2
    assert result["contracts"] == 1 and result["captured"] == 1
    assert result["dates"][GAME_DATE]["no_archived_record"] == ["asc-cbb-never-archived-2026-11-21"]


def test_no_row_in_window_makes_no_exchange_request(tmp_path, monkeypatch):
    db = _ledger(
        tmp_path,
        [
            {"slug": "asc-cbb-started-2026-11-21", "starts_in": timedelta(minutes=-10)},
            {"slug": "asc-cbb-later-2026-11-21", "starts_in": timedelta(hours=2)},
        ],
    )
    from model_prediction.data_sources import polymarket_us

    def _no_client(*_a, **_k):
        raise AssertionError("an idle cycle must not build an exchange client")

    monkeypatch.setattr(polymarket_us, "PolymarketUSClient", _no_client)
    result = pc.capture_pretip_closes(now=NOW, ledgers_db=db, runtime_root=tmp_path)
    assert result["status"] == "idle"
    assert result["captured"] == 0


def test_an_exhausted_deadline_builds_no_client_and_sends_no_request(tmp_path, monkeypatch):
    start = iso_utc(NOW + timedelta(minutes=10))
    slug = "asc-cbb-aa-bb-2026-11-21-neg-3pt5"
    db = _ledger(tmp_path, [{"slug": slug, "starts_in": timedelta(minutes=10)}])
    _archive(tmp_path, GAME_DATE, [_daily_record(slug, start=start, observed="2026-11-21T15:00:00Z")])
    from model_prediction.data_sources import polymarket_us

    def _no_client(*_a, **_k):
        raise AssertionError("no client may be built after the deadline")

    monkeypatch.setattr(polymarket_us, "PolymarketUSClient", _no_client)
    result = pc.capture_pretip_closes(now=NOW, ledgers_db=db, runtime_root=tmp_path, max_capture_seconds=0)
    assert result["status"] == "ok", result
    assert result["dates"][GAME_DATE]["reason"] == "time_budget_exhausted"
    assert result["captured"] == 0


def test_a_failing_book_fetch_is_a_failure_count_not_an_exception(tmp_path):
    start = iso_utc(NOW + timedelta(minutes=10))
    slug = "asc-cbb-aa-bb-2026-11-21-neg-3pt5"
    db = _ledger(tmp_path, [{"slug": slug, "starts_in": timedelta(minutes=10)}])
    _archive(tmp_path, GAME_DATE, [_daily_record(slug, start=start, observed="2026-11-21T15:00:00Z")])
    client = _FakeClient(start, error=httpx.ConnectError("exchange down"))
    result = pc.capture_pretip_closes(now=NOW, ledgers_db=db, runtime_root=tmp_path, client=client)
    assert result["status"] == "ok", result
    assert result["captured"] == 0
    assert result["dates"][GAME_DATE]["failures"] == 1
    assert client.snapshot_calls == [slug]  # one contract, one attempt through the fake


def test_a_raising_capture_returns_error_status_without_raising(tmp_path):
    start = iso_utc(NOW + timedelta(minutes=10))
    slug = "asc-cbb-aa-bb-2026-11-21-neg-3pt5"
    db = _ledger(tmp_path, [{"slug": slug, "starts_in": timedelta(minutes=10)}])
    _archive(tmp_path, GAME_DATE, [_daily_record(slug, start=start, observed="2026-11-21T15:00:00Z")])

    def _boom(*_a, **_k):
        raise httpx.ConnectError("exchange down")

    result = pc.capture_pretip_closes(
        now=NOW, ledgers_db=db, runtime_root=tmp_path, client=_FakeClient(start), capture_fn=_boom
    )
    assert result["status"] == "error"
    assert "ConnectError" in result["reason"]


def test_missing_ledger_is_skipped_not_an_error(tmp_path):
    result = pc.capture_pretip_closes(now=NOW, ledgers_db=tmp_path / "absent.db", runtime_root=tmp_path)
    assert result["status"] == "skipped"


def test_captured_close_is_the_spread_close_settlement_reads(tmp_path):
    # End to end through the real capture_slate_snapshots and the real settlement close lookup:
    # decided 3 h ago at the Daily's quote, tip in 12 minutes, the pre-tip book is the close.
    now = datetime.now(UTC).replace(microsecond=0)
    start = iso_utc(now + timedelta(minutes=12))
    decision = iso_utc(now - timedelta(hours=3))
    game_date = (now + timedelta(minutes=12)).astimezone(pc.EASTERN).date().isoformat()
    slug = f"asc-cbb-aaaa-bbbb-{game_date}-neg-3pt5"
    db = _ledger(tmp_path, [{"slug": slug, "starts_in": timedelta(minutes=12)}], now=now)
    # The Daily's own decision-time quote, observed before the decision: never the close.
    _archive(
        tmp_path,
        game_date,
        [_daily_record(slug, start=start, observed=iso_utc(now - timedelta(hours=3, minutes=1)))],
    )
    client = _FakeClient(start, long_ask=0.53)
    result = pc.capture_pretip_closes(now=now, ledgers_db=db, runtime_root=tmp_path, client=client)
    assert result["status"] == "ok", result
    assert result["captured"] == 1
    assert client.snapshot_calls == [slug]

    probability, _odds = _closing_probability_for_moneyline_pick(
        tmp_path,
        "ncaab",
        slug,
        start,
        "Bbbb Home",
        "Aaaa Away",
        "home",
        line=-3.5,
        not_before_utc=decision,
    )
    assert probability == pytest.approx(0.53)
    # The away side of the same rung reads the short ask.
    away, _ = _closing_probability_for_moneyline_pick(
        tmp_path, "ncaab", slug, start, "Bbbb Home", "Aaaa Away", "away", line=3.5, not_before_utc=decision
    )
    assert away == pytest.approx(0.49)


def _patch_worker(monkeypatch, tmp_path):
    monkeypatch.setattr(worker, "enable_background_priority", lambda: None)
    paths = SimpleNamespace(lock_root=tmp_path, ledgers_db=tmp_path / "ledgers.db", runtime_root=tmp_path)
    monkeypatch.setattr(worker.RuntimePaths, "resolve", lambda **_: paths)
    monkeypatch.setattr(
        worker, "acquire_lock", lambda _, **__: type("Lock", (), {"close": lambda self: None})()
    )
    monkeypatch.setattr(worker, "run_auto_buyer_cycle", lambda **_: {})
    monkeypatch.setattr(worker, "run_ncaab_spread_paper_trial", lambda: {"status": "disabled"})


def test_a_raising_pretip_capture_never_changes_the_worker_exit_code(monkeypatch, tmp_path, capsys):
    _patch_worker(monkeypatch, tmp_path)

    def _boom(**_):
        raise RuntimeError("capture exploded")

    monkeypatch.setattr(worker, "capture_pretip_closes", _boom)
    assert worker.main() == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["status"] == "ok"
    assert out["pretip_close_capture"]["status"] == "error"


def test_an_error_status_from_the_capture_is_not_a_worker_failure(monkeypatch, tmp_path, capsys):
    _patch_worker(monkeypatch, tmp_path)
    seen: dict = {}

    def _error(**kwargs):
        seen.update(kwargs)
        return {"status": "error", "reason": "exchange down"}

    monkeypatch.setattr(worker, "capture_pretip_closes", _error)
    assert worker.main() == 0
    assert seen["ledgers_db"] == tmp_path / "ledgers.db"
    assert 0 < seen["max_capture_seconds"] <= worker.PRETIP_CAPTURE_MAX_SECONDS
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["status"] == "ok"
