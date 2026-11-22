"""NBA pre-tip capture (quick-261120-npc): ESPN odds + Polymarket quotes near tip, devig helper, worker.

Fixtures under tests/fixtures/nba_pretip/ are real payloads recorded 2026-11-21 ~04:30 UTC and trimmed
(links, logos and player props removed): the ESPN NBA scoreboard for 2026-11-21 (three scheduled games,
DraftKings odds), the 2026-11-20 scoreboard (final and live games, odds stripped), the Polymarket NBA
event list, the LAC @ DAL event detail and the LAC @ DAL moneyline /book.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from model_prediction.data_sources import nba_pretip_capture as cap
from model_prediction.data_sources import polymarket_us
from model_prediction.devig import devig, devig_power, overround
from model_prediction.run_supervisor import DEFAULT_WORKER_TIMEOUTS, WORKERS

FIXTURES = Path(__file__).parent / "fixtures" / "nba_pretip"
LAC_DAL = "nba-lac-dal-2026-11-21"
LAC_DAL_ML = "aec-nba-lac-dal-2026-11-21"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _scoreboard(name: str = "espn_scoreboard_20261121.json") -> dict:
    return _load(name)


def _event(event_id: str) -> dict:
    return next(e for e in _scoreboard()["events"] if e["id"] == event_id)


class _Clock:
    """Advances one second per read, so stamps taken after a response differ from earlier ones."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


def _espn_get(payload: dict, status: int = 200):
    calls: list[str] = []

    def get(url: str) -> tuple[int, bytes]:
        calls.append(url)
        return status, json.dumps(payload).encode()

    get.calls = calls  # type: ignore[attr-defined]
    return get


def _polymarket_client(calls: list[str]) -> polymarket_us.PolymarketUSClient:
    """The real client over a mock transport serving the recorded gateway payloads (404 otherwise)."""
    events = _load("polymarket_nba_events_20261121.json")
    detail = _load(f"polymarket_event_detail_{LAC_DAL}.json")
    book = _load(f"polymarket_book_{LAC_DAL_ML}.json")

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        if path == "/v2/leagues/nba/events":
            return httpx.Response(200, json=events)
        if path == f"/v1/events/slug/{LAC_DAL}":
            return httpx.Response(200, json=detail, headers={"age": "4"})
        if path == f"/v1/markets/{LAC_DAL_ML}/book":
            return httpx.Response(200, json=book)
        return httpx.Response(404, json={"error": "not found"})

    return polymarket_us.PolymarketUSClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)), max_attempts=1
    )


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    monkeypatch.setattr(polymarket_us, "CAPTURE_MIN_REQUEST_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(polymarket_us, "EVENT_DETAIL_MIN_REQUEST_INTERVAL_SECONDS", 0.0)


def _walk_keys(node, out: set[str]) -> set[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            out.add(key)
            _walk_keys(value, out)
    elif isinstance(node, list):
        for value in node:
            _walk_keys(value, out)
    return out


# --------------------------------------------------------------------------- devig
def test_price_to_probability_converts_on_read_and_fails_closed() -> None:
    assert cap.price_to_probability("-650") == pytest.approx(650 / 750)
    assert cap.price_to_probability("+474") == pytest.approx(100 / 574)
    assert cap.price_to_probability(-110.0) == pytest.approx(110 / 210)
    assert cap.price_to_probability("EVEN") == 0.5
    for unusable in (None, "OFF", "", "50", "-99", True, "abc"):
        assert cap.price_to_probability(unusable) is None


def test_devig_methods() -> None:
    even = (110 / 210, 110 / 210)
    assert devig(even) == pytest.approx((0.5, 0.5))
    assert devig(even, "power") == pytest.approx((0.5, 0.5))
    assert overround(even) == pytest.approx(0.047619, abs=1e-6)
    heavy = (650 / 750, 100 / 574)  # DraftKings MIL @ PHI moneyline, 2026-11-21
    multiplicative = devig(heavy)
    power = devig_power(heavy)
    assert sum(multiplicative) == pytest.approx(1.0)
    assert sum(power) == pytest.approx(1.0)
    assert multiplicative[0] == pytest.approx(0.8326, abs=1e-4)
    assert power[0] == pytest.approx(0.8541, abs=1e-4)  # power moves more margin onto the longshot
    assert devig((0.5, None)) is None
    assert devig((0.0, 0.6)) is None
    with pytest.raises(ValueError):
        devig(even, "shin")


# --------------------------------------------------------------------------- ESPN parsing (real payload)
def test_parse_provider_odds_real_draftkings_payload() -> None:
    game = cap.parse_scoreboard_event(_event("401910023"))  # MIL @ PHI
    assert game is not None
    assert (game["away_abbr"], game["home_abbr"], game["home_nickname"]) == ("MIL", "PHI", "76ers")
    (provider,) = [cap.parse_provider_odds(item) for item in game["odds_items"]]
    assert provider["provider_name"] == "DraftKings" and provider["provider_id"] == "100"
    assert provider["spread"]["home_line"] == -11.5 and provider["spread"]["away_line"] == 11.5
    assert provider["spread"]["lines_consistent"] is True
    assert provider["spread"]["home_prob_raw"] == pytest.approx(115 / 215, abs=1e-6)
    assert provider["spread"]["home_fair"] + provider["spread"]["away_fair"] == pytest.approx(1.0)
    assert provider["total"]["line"] == 229.5 and provider["total"]["line_open"] == 228.5
    assert provider["moneyline"]["home_prob_raw"] == pytest.approx(650 / 750, abs=1e-6)
    assert provider["moneyline"]["home_prob_raw_open"] == pytest.approx(446 / 546, abs=1e-6)
    # Prices are stored only as probabilities: no price field or key survives the parse.
    keys = _walk_keys(provider, set())
    assert not {k for k in keys if "odds" in k.lower() or "american" in k.lower()}
    for value in (provider["moneyline"], provider["spread"], provider["total"]):
        for key, number in value.items():
            if key.endswith(("_raw", "_fair", "_raw_open")) and number is not None:
                assert 0.0 < number < 1.0, key


def test_consensus_uses_modal_line_and_skips_live_and_incomplete_books() -> None:
    game = cap.parse_scoreboard_event(_event("401910025"))  # LAC @ DAL
    real = cap.parse_provider_odds(game["odds_items"][0])
    other_line = json.loads(json.dumps(real))
    other_line["provider_name"] = "other"
    other_line["spread"]["home_line"] = -6.5
    live = json.loads(json.dumps(real))
    live["is_live"] = True
    live["moneyline"]["home_fair"] = 0.99
    incomplete = json.loads(json.dumps(real))
    incomplete["moneyline"]["home_fair"] = None
    incomplete["moneyline"]["away_fair"] = None
    result = cap.consensus([real, real, other_line, live, incomplete])
    assert result["spread"]["home_line"] == -5.5
    assert result["spread"]["n_books"] == 3  # real twice + incomplete (spread is complete there)
    assert result["moneyline"]["n_books"] == 3  # live and incomplete excluded
    assert result["moneyline"]["home"] == pytest.approx(real["moneyline"]["home_fair"])
    assert result["providers_live_excluded"] == 1


def test_match_polymarket_event_by_tip_and_nicknames() -> None:
    pm_events = _load("polymarket_nba_events_20261121.json")["events"]
    game = cap.parse_scoreboard_event(
        _event("401910025")
    )  # ESPN "LA Clippers" vs Polymarket title "LA Clippers"
    event, reason = cap.match_polymarket_event(game, pm_events)
    assert reason == "matched" and event["slug"] == LAC_DAL
    # A shifted tip time never matches.
    shifted = {**game, "event_start_utc": "2026-11-22T03:00Z"}
    assert cap.match_polymarket_event(shifted, pm_events) == (None, "NO_CALL_NO_POLYMARKET_EVENT")
    # Two candidates are ambiguous: fail closed.
    lac_dal = next(e for e in pm_events if e["slug"] == LAC_DAL)
    assert cap.match_polymarket_event(game, [lac_dal, dict(lac_dal, slug="dup")])[1] == (
        "NO_CALL_AMBIGUOUS_POLYMARKET_EVENT"
    )


# --------------------------------------------------------------------------- end to end
def test_capture_writes_espn_records_and_canonical_polymarket_snapshots(tmp_path, monkeypatch) -> None:
    clock = _Clock(datetime(2026, 11, 22, 0, 40, tzinfo=UTC))  # 20 minutes before LAC @ DAL and MEM @ MIN
    # The shared capture stamps quotes and decides "started" from utc_now(); pin it to the same clock,
    # or the test silently changes meaning once real time passes the fixture's tip.
    monkeypatch.setattr(polymarket_us, "utc_now", clock)
    calls: list[str] = []
    espn = _espn_get(_scoreboard())
    result = cap.capture_nba_pretip(
        tmp_path, window_minutes=25, now=clock, http_get=espn, polymarket_client=_polymarket_client(calls)
    )

    assert espn.calls == [cap.SCOREBOARD_URL.format(date="20261121")]
    assert result["games_in_window"] == 2
    assert result["skipped_started"] == 1  # MIL @ PHI tipped at 18:00 UTC
    statuses = {row["matchup"]: row for row in result["games"]}
    assert statuses["LAC @ DAL"]["polymarket"] == "matched"
    # No event-detail fixture for MEM @ MIN: that game fails closed, LAC @ DAL is unaffected.
    assert statuses["MEM @ MIN"]["polymarket"] == "NO_CALL_POLYMARKET_DETAIL_UNAVAILABLE"
    assert result["status"] == "partial"
    assert result["espn_records_written"] == 2

    espn_path = tmp_path / "odds" / "nba" / "2026-11-21" / "espn_odds.jsonl"
    records = [json.loads(line) for line in espn_path.read_text(encoding="utf-8").splitlines()]
    lac = next(r for r in records if r["event_id"] == "401910025")
    assert lac["source"] == "espn_scoreboard_odds"
    assert lac["home_team"] == "Dallas Mavericks" and lac["away_team"] == "LA Clippers"
    assert lac["polymarket"] == {"match_status": "matched", "event_slug": LAC_DAL, "event_id": "240450"}
    assert lac["consensus"]["spread"] == {"home_line": -5.5, "home": 0.5, "away": 0.5, "n_books": 1}
    assert lac["record_hash"] == cap._record_hash(lac)
    observed = datetime.fromisoformat(lac["observed_at_utc"])
    assert observed < datetime.fromisoformat("2026-11-22T01:00:00+00:00")
    assert lac["minutes_to_tip"] == 20.0  # 19.97 minutes, rounded to 0.1
    assert not {k for k in _walk_keys(records, set()) if "american" in k.lower() or k == "odds"}

    snap_path = tmp_path / "odds" / "nba" / "2026-11-21" / "polymarket_snapshots.jsonl"
    snaps = [json.loads(line) for line in snap_path.read_text(encoding="utf-8").splitlines()]
    slugs = {s["market_slug"] for s in snaps}
    assert LAC_DAL_ML in slugs
    assert not any(slug.startswith("astatc-") for slug in slugs)  # player props are never captured
    for snap in snaps:
        assert snap["provider"] == "polymarket_us" and snap["league"] == "NBA"
        assert snap["event_start_utc"] == "2026-11-22T01:00:00Z" and snap["observed_at_utc"]
        assert snap["event_title"] == "LA Clippers vs. DAL Mavericks"
        assert snap["timestamp_valid"] is True
        assert snap["observed_at_utc"].startswith("2026-11-22T00:4")
    book_row = next(
        s
        for s in snaps
        if s["market_slug"] == LAC_DAL_ML
        and s["reconstruction_status_source"] != "live_gateway_event_detail_quote"
    )
    assert (book_row["long"]["bid"], book_row["long"]["ask"]) == (0.34, 0.35)
    assert book_row["long"]["ask_size"] == pytest.approx(111391.45)
    assert book_row["long"]["midpoint"] == pytest.approx(0.345)
    # The ESPN spread rung (Clippers +5.5) was quoted from the event detail.
    rung = next(s for s in snaps if s["market_slug"] == "asc-nba-lac-dal-2026-11-21-pos-5pt5")
    assert (rung["long"]["bid"], rung["long"]["ask"]) == (0.49, 0.51)
    assert f"/v1/markets/{LAC_DAL_ML}/book" in calls


def test_espn_window_writes_whole_slate_without_polymarket_calls(tmp_path) -> None:
    # 17:00Z: MIL @ PHI tips in 60 minutes, LAC @ DAL and MEM @ MIN in 8 hours -- all outside the
    # 25-minute Polymarket window, all inside a 12-hour ESPN window. A daily run at this hour needs
    # a fresh consensus anchor for every game, so ESPN odds are written for all three.
    clock = _Clock(datetime(2026, 11, 21, 17, 0, tzinfo=UTC))
    calls: list[str] = []
    result = cap.capture_nba_pretip(
        tmp_path,
        window_minutes=25,
        espn_window_minutes=720,
        now=clock,
        http_get=_espn_get(_scoreboard()),
        polymarket_client=_polymarket_client(calls),
    )
    assert result["games_in_window"] == 0
    assert result["espn_only_records_written"] == 3
    assert calls == []
    records = [
        json.loads(line)
        for line in (tmp_path / "odds" / "nba" / "2026-11-21" / "espn_odds.jsonl")
        .read_text("utf-8")
        .splitlines()
    ]
    assert len(records) == 3 and len({r["event_id"] for r in records}) == 3
    for record in records:
        assert record["polymarket"] is None
        assert record["record_hash"] == cap._record_hash(record)
        assert datetime.fromisoformat(record["observed_at_utc"]) < datetime.fromisoformat(
            record["event_start_utc"]
        )


def test_espn_window_defaults_to_the_polymarket_window(tmp_path) -> None:
    clock = _Clock(datetime(2026, 11, 21, 17, 0, tzinfo=UTC))
    result = cap.capture_nba_pretip(
        tmp_path, window_minutes=25, now=clock, http_get=_espn_get(_scoreboard()), polymarket_client=None
    )
    assert result["espn_only_records_written"] == 0
    assert not (tmp_path / "odds").exists()


def test_no_game_in_window_makes_no_polymarket_request(tmp_path) -> None:
    clock = _Clock(datetime(2026, 11, 21, 4, 26, tzinfo=UTC))  # recorded: games final or live
    calls: list[str] = []
    result = cap.capture_nba_pretip(
        tmp_path,
        now=clock,
        http_get=_espn_get(_scoreboard("espn_scoreboard_20261120_final_and_live.json")),
        polymarket_client=_polymarket_client(calls),
    )
    assert result["status"] == "ok" and result["games_in_window"] == 0
    assert result["skipped_not_scheduled"] == 2
    assert calls == []
    assert not (tmp_path / "odds").exists()


def test_game_without_espn_odds_writes_no_espn_record(tmp_path) -> None:
    board = _scoreboard()
    for event in board["events"]:
        event["competitions"][0]["odds"] = []
    clock = _Clock(datetime(2026, 11, 22, 0, 40, tzinfo=UTC))
    result = cap.capture_nba_pretip(
        tmp_path, now=clock, http_get=_espn_get(board), polymarket_client=_polymarket_client([])
    )
    assert {row["espn"] for row in result["games"]} == {"NO_ESPN_ODDS"}
    assert result["espn_records_written"] == 0
    assert not (tmp_path / "odds" / "nba" / "2026-11-21" / "espn_odds.jsonl").exists()


def test_scoreboard_failure_is_an_error_and_never_raises(tmp_path) -> None:
    result = cap.capture_nba_pretip(
        tmp_path, now=_Clock(datetime(2026, 11, 22, 0, 40, tzinfo=UTC)), http_get=_espn_get({}, status=503)
    )
    assert result["status"] == "error"
    assert result["errors"][0]["error"] == "HTTP 503"


# --------------------------------------------------------------------------- worker
def test_worker_is_registered_with_the_supervisor() -> None:
    command = WORKERS["nba-pretip-capture"]
    assert command[-2:] == ["-m", "model_prediction.nba_pretip_capture_worker"]
    assert importlib.util.find_spec("model_prediction.nba_pretip_capture_worker") is not None
    assert DEFAULT_WORKER_TIMEOUTS["nba-pretip-capture"] <= 600


def test_worker_exit_codes(monkeypatch, tmp_path, capsys) -> None:
    from model_prediction import nba_pretip_capture_worker as worker

    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setattr(worker, "_open_nba_pick_slugs", lambda _root: frozenset())
    seen: dict = {}

    def fake_capture(data_root, **kwargs):
        seen["root"] = Path(data_root)
        return {"status": seen.get("next", "partial")}

    monkeypatch.setattr(worker, "capture_nba_pretip", fake_capture)
    # --no-forward: the NBA v10 research extras (network) are covered in test_nba_v10_forward.py.
    assert worker.main(["--window-minutes", "30", "--no-forward"]) == 0
    assert seen["root"] == tmp_path.resolve()
    seen["next"] = "error"
    assert worker.main(["--no-forward"]) == 1
    assert json.loads(capsys.readouterr().out.splitlines()[-1]) == {"status": "error"}


def test_worker_fails_closed_without_runtime_root(monkeypatch) -> None:
    from model_prediction import nba_pretip_capture_worker as worker

    monkeypatch.delenv("MODEL_PREDICTION_RUNTIME_ROOT", raising=False)
    monkeypatch.setattr(worker.RuntimePaths, "resolve", _raise_without_root)
    with pytest.raises(RuntimeError):
        worker.main([])


def _raise_without_root(**kwargs):
    assert kwargs.get("require_external_runtime") is True
    raise RuntimeError("MODEL_PREDICTION_RUNTIME_ROOT is required")


def test_forward_extras_stay_off_in_the_0030_memory_window(monkeypatch) -> None:
    from zoneinfo import ZoneInfo

    from model_prediction import nba_pretip_capture_worker as worker

    et = ZoneInfo("America/New_York")
    assert worker._in_memory_pressure_window(datetime(2026, 11, 22, 0, 15, tzinfo=et))
    assert worker._in_memory_pressure_window(datetime(2026, 11, 22, 1, 19, tzinfo=et))
    assert not worker._in_memory_pressure_window(datetime(2026, 11, 22, 1, 20, tzinfo=et))
    assert not worker._in_memory_pressure_window(datetime(2026, 11, 22, 0, 14, tzinfo=et))
    monkeypatch.setattr(worker, "_in_memory_pressure_window", lambda now: True)
    assert worker.run_forward_extras(None, None) == {"status": "skipped_memory_window"}
