"""Forward NCAAF pregame capture: append-only, observed after the response, fail-soft, RuntimePaths-rooted.

Payloads are the real ESPN responses probed on 2026-11-15 (scoreboard for 2026-11-15 groups=80, core odds and the
league injury feed), not hand-written fixtures; the network is faked.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from model_prediction.data_sources import espn_cfb_pregame_capture as cap
from model_prediction.runtime_paths import RuntimePaths

FIX = Path(__file__).parent / "fixtures" / "espn_cfb_pregame"
EVENT_ID = "401856887"  # Ole Miss at Oklahoma, kickoff 2026-11-15T18:00Z
KICKOFF = datetime(2026, 11, 15, 18, 0, tzinfo=UTC)


def _bytes(name: str) -> bytes:
    return (FIX / name).read_bytes()


class FakeNet:
    """Serves the real payloads and records every URL; the scoreboard serves only the fixture date."""

    def __init__(
        self,
        *,
        fail_kinds: tuple[str, ...] = (),
        raise_kinds: tuple[str, ...] = (),
        clock: Clock | None = None,
    ) -> None:
        self.urls: list[str] = []
        self.clock = clock  # when set, every response takes 30 s and its return time is recorded
        self.returned: dict[str, datetime] = {}
        self.fail_kinds = fail_kinds
        self.raise_kinds = raise_kinds

    def __call__(self, url: str) -> tuple[int, bytes]:
        self.urls.append(url)
        kind = "scoreboard" if "scoreboard" in url else "injuries" if "injuries" in url else "core_odds"
        if self.clock is not None:
            self.clock.t += timedelta(seconds=30)
            self.returned[kind] = self.clock.t
        if kind in self.raise_kinds:
            raise ConnectionError("boom")
        if kind in self.fail_kinds:
            return 503, b"{}"
        if kind == "scoreboard":
            return (
                (200, _bytes("scoreboard_20261115.json"))
                if "dates=20261115" in url
                else (200, b'{"events":[]}')
            )
        if kind == "injuries":
            return 200, _bytes("league_injuries.json")
        assert EVENT_ID in url
        return 200, _bytes("core_odds_401856887.json")


class Clock:
    """Advances 1 second per read, so observed_at_utc must reflect when each response returned."""

    def __init__(self, start: datetime) -> None:
        self.t = start

    def __call__(self) -> datetime:
        self.t += timedelta(seconds=1)
        return self.t


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _run(tmp_path: Path, net: FakeNet, clock: Clock, **kw: Any) -> dict[str, Any]:
    kw.setdefault("days", 1)
    return cap.capture_cfb_pregame_snapshots(
        tmp_path, "2026-11-15", now=clock, http_get=net, sleep=lambda _s: None, **kw
    )


def test_writes_one_hashed_record_with_lines_and_probabilities_only(tmp_path: Path) -> None:
    clock = Clock(KICKOFF - timedelta(hours=10))
    net = FakeNet()
    result = _run(tmp_path, net, clock)
    assert (
        result["status"] == "ok"
        and result["records_written"] == 1
        and result["qb_depth"] == "endpoint_unavailable"
    )
    path = cap.capture_path(tmp_path)
    (rec,) = _lines(path)
    assert rec["event_id"] == EVENT_ID and rec["qb_depth"] == "endpoint_unavailable"
    assert rec["record_hash"] == cap._record_hash(rec)
    sb = rec["scoreboard"]["line"]
    assert (sb["provider_name"], sb["over_under"], sb["home_spread_line"]) == ("DraftKings", 48.5, -2.5)
    (prov,) = rec["core_odds"]["providers"]
    assert (
        prov["total_open"],
        prov["total_current"],
        prov["home_spread_open"],
        prov["home_spread_current"],
    ) == (
        53.5,
        48.5,
        -4.0,
        -2.5,
    )
    assert prov["is_live"] is False and 0.0 < prov["over_prob_current"] < 1.0
    assert prov["over_prob_current"] == pytest.approx(112 / 212)  # -112 as an implied probability
    assert rec["injuries"]["by_team"] == {"145": [], "201": []}
    text = path.read_text(encoding="utf-8")
    for forbidden in ('"overOdds"', '"underOdds"', '"moneyLine"', '"american"', '"-112', '"+120'):
        assert forbidden not in text  # no American price is ever stored


def test_observed_at_follows_each_response_with_an_advancing_clock(tmp_path: Path) -> None:
    clock = Clock(KICKOFF - timedelta(hours=10))
    t_before = clock.t
    net = FakeNet(clock=clock)
    _run(tmp_path, net, clock)
    (rec,) = _lines(cap.capture_path(tmp_path))
    stamps = [
        datetime.fromisoformat(rec[k]["observed_at_utc"]) for k in ("scoreboard", "injuries", "core_odds")
    ]
    assert all(s > t_before for s in stamps)
    # each block is stamped AFTER its own response returned (a 30 s fake latency separates the two moments)
    for stamp, kind in zip(stamps, ("scoreboard", "injuries", "core_odds"), strict=True):
        assert stamp > net.returned[kind], kind
    assert stamps[0] < stamps[1] < stamps[2]  # one distinct time per response, in call order
    assert datetime.fromisoformat(rec["observed_at_utc"]) == max(stamps)
    assert max(stamps) < KICKOFF


def test_append_only_across_calls(tmp_path: Path) -> None:
    clock = Clock(KICKOFF - timedelta(hours=10))
    _run(tmp_path, FakeNet(), clock)
    first = cap.capture_path(tmp_path).read_text(encoding="utf-8")
    _run(tmp_path, FakeNet(), clock)
    both = cap.capture_path(tmp_path).read_text(encoding="utf-8")
    assert both.startswith(first) and len(both.splitlines()) == 2
    recs = _lines(cap.capture_path(tmp_path))
    assert recs[0]["observed_at_utc"] < recs[1]["observed_at_utc"]


def test_started_games_are_skipped_and_nothing_is_fetched_for_them(tmp_path: Path) -> None:
    clock = Clock(KICKOFF + timedelta(minutes=5))
    net = FakeNet()
    result = _run(tmp_path, net, clock)
    assert result["records_written"] == 0 and result["skipped_started"] == 1
    assert not cap.capture_path(tmp_path).exists()
    assert not any("odds" in u for u in net.urls)


def test_a_record_observed_at_or_after_kickoff_is_dropped(tmp_path: Path) -> None:
    clock = Clock(KICKOFF - timedelta(seconds=9))  # kickoff passes while the last response is in flight
    result = _run(tmp_path, FakeNet(), clock)
    assert result["records_written"] == 0 and result["dropped_after_kickoff"] == 1
    assert not cap.capture_path(tmp_path).exists()


@pytest.mark.parametrize("kind", ["scoreboard", "injuries", "core_odds"])
def test_an_exception_in_the_fetch_returns_a_status_and_never_raises(tmp_path: Path, kind: str) -> None:
    clock = Clock(KICKOFF - timedelta(hours=10))
    result = _run(tmp_path, FakeNet(raise_kinds=(kind,)), clock)
    assert result["status"] in ("partial", "ok")
    assert any(e["kind"] == kind for e in result["errors"])
    if kind == "scoreboard":
        assert result["records_written"] == 0
    elif kind == "core_odds":
        (rec,) = _lines(cap.capture_path(tmp_path))
        assert rec["core_odds"] is None and rec["scoreboard"]["line"] is not None


def test_http_errors_are_recorded_not_raised(tmp_path: Path) -> None:
    result = _run(tmp_path, FakeNet(fail_kinds=("scoreboard",)), Clock(KICKOFF - timedelta(hours=10)))
    assert result["status"] == "partial" and result["errors"][0]["error"] == "HTTP 503"


def test_unexpected_exceptions_are_swallowed_into_an_error_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("path resolution blew up")

    monkeypatch.setattr(cap, "capture_path", explode)
    result = cap.capture_cfb_pregame_snapshots(tmp_path, "2026-11-15", http_get=FakeNet(), days=1)
    assert result["status"] == "error" and "path resolution" in result["error"]
    # a write failure (the target's parent is a file) is also a status, not an exception
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(cap, "capture_path", lambda _r: blocker / "sub" / cap.CAPTURE_FILENAME)
    ok = _run(tmp_path, FakeNet(), Clock(KICKOFF - timedelta(hours=10)))
    assert ok["status"] == "partial" and ok["records_written"] == 0 and ok["errors"][-1]["kind"] == "write"


def test_request_and_event_budgets_are_hard_limits(tmp_path: Path) -> None:
    net = FakeNet()
    result = _run(tmp_path, net, Clock(KICKOFF - timedelta(hours=10)), max_requests=1)
    assert (
        len(net.urls) == 1 and result["truncated"] == "max_requests"
    )  # the scoreboard used the whole budget
    net2 = FakeNet()
    capped = _run(tmp_path, net2, Clock(KICKOFF - timedelta(hours=10)), max_events=0)
    assert capped["records_written"] == 0 and capped["events_over_cap"] == 1
    assert not any("odds" in u for u in net2.urls)


def test_only_upcoming_scheduled_fbs_events_are_captured_from_the_real_scoreboard() -> None:
    board = json.loads(_bytes("scoreboard_20261115.json"))
    parsed = [cap.parse_scoreboard_event(e) for e in board["events"]]
    assert all(p is not None for p in parsed)
    assert [p["event_id"] for p in parsed if p] == [EVENT_ID]
    assert parsed[0]["status"] == "STATUS_SCHEDULED" and parsed[0]["home_team"] == "Oklahoma Sooners"
    assert cap.parse_scoreboard_event({"id": "x"}) is None


def test_american_prices_become_probabilities_and_junk_becomes_none() -> None:
    assert cap.american_to_probability("-110") == pytest.approx(110 / 210)
    assert cap.american_to_probability("+154") == pytest.approx(100 / 254)
    assert cap.american_to_probability(120) == pytest.approx(100 / 220)
    for junk in (None, "", "OFF", "EVEN", "12", float("nan")):
        assert cap.american_to_probability(junk) is None


# ----------------------------------------------------------------------------- RuntimePaths routing
def test_repo_local_data_root_resolves_to_the_runtime_root_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(runtime))
    repo_data = RuntimePaths.resolve().repo_root / "data"
    standalone = cap.capture_path(None)
    assert (
        standalone
        == RuntimePaths.resolve(require_external_runtime=True).data_root
        / cap.CAPTURE_DIRNAME
        / cap.CAPTURE_FILENAME
    )
    assert (
        cap.capture_path(repo_data) == standalone
    )  # the legacy repo-local argument is rerouted, never split
    explicit = tmp_path / "isolated"
    assert (
        cap.capture_path(explicit) == explicit / cap.CAPTURE_DIRNAME / cap.CAPTURE_FILENAME
    )  # used as given
    assert runtime.resolve() in standalone.resolve().parents


def test_a_standalone_call_without_a_runtime_root_fails_closed_as_a_status() -> None:
    result = cap.capture_cfb_pregame_snapshots(None, "2026-11-15", http_get=FakeNet(), days=1)
    assert result["status"] == "error" and "MODEL_PREDICTION_RUNTIME_ROOT" in result["error"]
