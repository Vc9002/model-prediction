"""NBA v10 forward research track (outputs/nba/v10_forward/PREREG_FORWARD.md).

Covers: the live injury capture's two timestamps and the index's fetch-time rule, fail isolation of
the worker extras (exception, hang, budget), the T-C2 status-change detector (20-minute threshold,
point-in-time minutes, report-gap and parse rules) and the test-A observation selection.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from model_prediction import nba_pretip_capture_worker as worker
from model_prediction import nba_v10_forward as fwd
from model_prediction import nba_v10_forward_report as rep
from model_prediction.data_sources import nba_injury_live as live
from model_prediction.data_sources.nba_injury_report import ParsedReport
from model_prediction.features.nba_player_ratings import Candidate, GameBox, PlayerLine
from model_prediction.nba_v10_g2 import ConsensusCapture


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


class _Clock:
    """Advances on every read, so "stamped after the response" is observable."""

    def __init__(self, start: datetime, step: timedelta = timedelta(seconds=1)) -> None:
        self.now = start
        self.step = step

    def __call__(self) -> datetime:
        self.now += self.step
        return self.now


# --------------------------------------------------------------------------- injury capture
def _row(team: str, player: str | None, status: str, *, away: str = "BOS", home: str = "SAC") -> dict:
    return {
        "page": 1,
        "game_date": "2026-11-21",
        "game_time_et": "10:00 (ET)",
        "matchup": f"{away}@{home}",
        "away_tricode": away,
        "home_tricode": home,
        "team": team,
        "team_tricode": team,
        "player_name": player,
        "status": status,
        "reason": "Injury/Illness",
    }


def test_live_capture_stamps_fetch_after_response_and_publishes_at_header_time(tmp_path, monkeypatch):
    calls: list[str] = []

    def fake_parse(body: bytes) -> ParsedReport:
        report = ParsedReport(header_time_text="11/21/26 04:20 PM", n_pages=1)
        report.rows = [_row("BOS", "Tatum, Jayson", "Out")]
        return report

    def http_get(url: str) -> tuple[int, bytes]:
        calls.append(url.rsplit("/", 1)[1])
        if url.endswith("Injury-Report_2026-11-21_04_15PM.pdf"):
            return 200, b"%PDF-1.4 fake"
        return 403, b""

    monkeypatch.setattr(live, "parse_report", fake_parse)
    clock = _Clock(_utc("2026-11-21T21:25:00"))  # 16:25 ET
    summary = live.capture_injury_reports(tmp_path, now=clock, http_get=http_get)
    assert summary["stored"] == 1
    assert calls[0] == "Injury-Report_2026-11-21_04_15PM.pdf"  # newest slot first
    stored = live.load_reports(tmp_path, ["2026-11-21"])
    assert len(stored) == 1
    row = stored[0]
    # The content is as of the header (16:20 ET), later than the 16:15 filename slot.
    assert row["filename_time_utc"] == "2026-11-21T21:15:00+00:00"
    assert row["published_at_utc"] == "2026-11-21T21:20:00+00:00"
    # Fetch time is stamped after the response: strictly after the run started.
    assert datetime.fromisoformat(row["fetched_at_utc"]) > _utc("2026-11-21T21:25:01")
    rows = live.load_rows(tmp_path, ["2026-11-21"])
    assert rows[0]["fetched_at_utc"] == row["fetched_at_utc"]
    # The stored slot is not probed again; a 403 slot is (it may be published later).
    calls.clear()
    live.capture_injury_reports(tmp_path, now=clock, http_get=http_get)
    assert "Injury-Report_2026-11-21_04_15PM.pdf" not in calls
    assert "Injury-Report_2026-11-21_04_00PM.pdf" in calls


def _report(name: str, published: str, fetched: str, **extra) -> dict:
    return {
        "filename": name,
        "published_at_utc": published,
        "fetched_at_utc": fetched,
        "parse_error": None,
        "n_unplaced": 0,
        **extra,
    }


def _rows_for(report: dict, rows: list[dict]) -> list[dict]:
    return [
        {
            **r,
            "report_filename": report["filename"],
            "fetched_at_utc": report["fetched_at_utc"],
            "consistent": True,
        }
        for r in rows
    ]


def test_index_uses_fetched_at_not_published_at():
    r1 = _report("A.pdf", "2026-11-21T20:00:00+00:00", "2026-11-21T20:05:00+00:00")
    r2 = _report("B.pdf", "2026-11-21T20:15:00+00:00", "2026-11-21T20:40:00+00:00")
    rows = _rows_for(r1, [_row("BOS", "Tatum, Jayson", "Out"), _row("SAC", None, "NOT YET SUBMITTED")])
    rows += _rows_for(r2, [_row("BOS", "Tatum, Jayson", "Available"), _row("SAC", None, "NOT YET SUBMITTED")])

    def status_at(decision: datetime) -> str:
        index = fwd.live_injury_index([r1, r2], rows, decision)
        team = index.team_report("2026-11-21", "BOS", "SAC", "BOS", decision)
        return dict(team.players or ())["Tatum, Jayson"]

    # B was published at 20:15 but we only had it at 20:40: a 20:30 decision must not see it.
    assert status_at(_utc("2026-11-21T20:30:00")) == "out"
    assert status_at(_utc("2026-11-21T20:41:00")) == "available"


def test_report_fetched_after_a_newer_one_is_dropped_as_stale():
    newer = _report("NEW.pdf", "2026-11-21T20:15:00+00:00", "2026-11-21T20:20:00+00:00")
    older = _report("OLD.pdf", "2026-11-21T20:00:00+00:00", "2026-11-21T20:25:00+00:00")
    rows = _rows_for(
        newer, [_row("BOS", "Tatum, Jayson", "Available"), _row("SAC", None, "NOT YET SUBMITTED")]
    )
    rows += _rows_for(older, [_row("BOS", "Tatum, Jayson", "Out"), _row("SAC", None, "NOT YET SUBMITTED")])
    decision = _utc("2026-11-21T20:30:00")
    index = fwd.live_injury_index([newer, older], rows, decision)
    team = index.team_report("2026-11-21", "BOS", "SAC", "BOS", decision)
    assert dict(team.players or ())["Tatum, Jayson"] == "available"


# --------------------------------------------------------------------------- fail isolation
def _fake_paths(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        data_root=tmp_path, runtime_root=tmp_path, repo_root=tmp_path, research_root=tmp_path / "research"
    )


CAPTURE_RESULT = {
    "status": "partial",
    "games_in_window": 2,
    "espn_records_written": 2,
    "errors": [{"kind": "x"}],
}


def _run_worker(monkeypatch, tmp_path, capsys, capture_result, argv=()):
    monkeypatch.setattr(worker, "capture_nba_pretip", lambda *a, **k: dict(capture_result))
    monkeypatch.setattr(worker, "_open_nba_pick_slugs", lambda root: frozenset())
    monkeypatch.setattr(worker.RuntimePaths, "resolve", staticmethod(lambda **k: _fake_paths(tmp_path)))
    exits: list[int] = []
    monkeypatch.setattr(worker.os, "_exit", lambda code: exits.append(code))
    started = time.monotonic()
    code = worker.main(list(argv))
    elapsed = time.monotonic() - started
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    return code, printed, exits, elapsed


@pytest.mark.parametrize("capture_status,expected_code", [("partial", 0), ("error", 1)])
def test_exception_in_forward_steps_leaves_capture_result_and_exit_code(
    monkeypatch, tmp_path, capsys, capture_status, expected_code
):
    def boom(*a, **k):
        raise RuntimeError("forward exploded")

    monkeypatch.setattr(fwd, "run_forward", boom)
    capture = {**CAPTURE_RESULT, "status": capture_status}
    code, printed, exits, _ = _run_worker(monkeypatch, tmp_path, capsys, capture)
    assert code == expected_code
    assert exits == []
    assert {k: v for k, v in printed.items() if k != "nba_v10_forward"} == capture
    assert printed["nba_v10_forward"]["status"] == "error"
    assert "forward exploded" in printed["nba_v10_forward"]["error"]


def test_hanging_forward_step_times_out_without_holding_the_worker(monkeypatch, tmp_path, capsys):
    release = threading.Event()

    def hang(*a, **k):
        release.wait(30)
        return {"status": "ok"}

    monkeypatch.setattr(fwd, "run_forward", hang)
    monkeypatch.setattr(worker, "FORWARD_MIN_BUDGET_SECONDS", 0.0)
    monkeypatch.setattr(worker, "FORWARD_JOIN_GRACE_SECONDS", 0.1)
    try:
        code, printed, exits, elapsed = _run_worker(
            monkeypatch, tmp_path, capsys, CAPTURE_RESULT, argv=["--forward-budget-seconds", "0.3"]
        )
    finally:
        release.set()
    assert elapsed < 5
    assert printed["nba_v10_forward"]["status"] == "timeout"
    assert {k: v for k, v in printed.items() if k != "nba_v10_forward"} == CAPTURE_RESULT
    assert exits == [0] and code == 0  # leaves with the capture's own exit code


def test_forward_skipped_when_the_capture_used_the_budget(monkeypatch, tmp_path, capsys):
    called: list[bool] = []
    monkeypatch.setattr(fwd, "run_forward", lambda *a, **k: called.append(True) or {"status": "ok"})
    monkeypatch.setattr(worker, "_PROCESS_START", time.monotonic() - 235.0)
    code, printed, _, _ = _run_worker(monkeypatch, tmp_path, capsys, CAPTURE_RESULT)
    assert printed["nba_v10_forward"]["status"] == "skipped_budget"
    assert called == [] and code == 0


def test_no_forward_flag_prints_the_capture_result_only(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(fwd, "run_forward", lambda *a, **k: pytest.fail("must not run"))
    code, printed, _, _ = _run_worker(monkeypatch, tmp_path, capsys, CAPTURE_RESULT, argv=["--no-forward"])
    assert printed == CAPTURE_RESULT and code == 0


def test_run_forward_step_errors_are_recorded_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(
        live, "capture_injury_reports", lambda *a, **k: (_ for _ in ()).throw(OSError("disk"))
    )
    cfg = fwd.ForwardConfig(tmp_path, tmp_path, tmp_path, tmp_path / "out", tmp_path / "inj")
    out = fwd.run_forward(cfg, deadline=time.monotonic() + 30, clock=lambda: _utc("2026-11-21T21:00:00"))
    assert out["injury"]["status"] == "error"
    assert out["window_games"] == 0


# --------------------------------------------------------------------------- T-C2 detector
def _entry(**players: tuple[str, bool]) -> dict:
    return {fwd.normalize_name(name): (name, status, ok) for name, (status, ok) in players.items()}


def test_status_changes_rules():
    prev = {"BOS": _entry(**{"Tatum, Jayson": ("Questionable", True), "White, Derrick": ("Probable", True)})}
    new = {"BOS": _entry(**{"Tatum, Jayson": ("Out", True)})}
    clean = fwd.status_changes(prev, new, "BOS", clean_parse=True)
    assert {(c["player"], c["from"], c["to"]) for c in clean} == {
        ("Tatum, Jayson", "questionable", "out"),
        ("White, Derrick", "probable", "unlisted"),
    }
    assert next(c for c in clean if c["player"] == "Tatum, Jayson")["crosses_group"] is False
    # A disappearance can be a parse glitch: it only counts on clean parses.
    unclean = fwd.status_changes(prev, new, "BOS", clean_parse=False)
    assert [c["player"] for c in unclean] == ["Tatum, Jayson"]
    # A team that has not submitted yields nothing.
    assert fwd.status_changes(prev, {"BOS": "NOT YET SUBMITTED"}, "BOS", clean_parse=True) == []


def _forward_state(minutes: dict[str, tuple[float, int]], candidates: list[Candidate]) -> fwd.ForwardState:
    stub = SimpleNamespace(candidates=lambda team_id, season: candidates)
    return fwd.ForwardState(
        day="2026-11-21",
        states={"margin": stub, "total": stub},  # type: ignore[dict-item]
        coefs={},
        artifact_hash="h",
        team_ids={"Boston Celtics": "2", "Sacramento Kings": "23"},
        last_game={"2": ("2026-11-20", True), "23": ("2026-11-20", True)},
        recent_missing={},
        minutes_last10=minutes,
    )


def test_qualify_uses_the_20_minute_average_and_three_played_games():
    cands = [
        Candidate("1", "Jayson Tatum", 33.0, 1.0),
        Candidate("2", "Bench Guy", 19.0, 1.0),
        Candidate("3", "Back From Injury", 30.0, 0.4),
    ]
    fs = _forward_state({"1": (33.2, 10), "2": (19.99, 10), "3": (30.0, 2)}, cands)

    def q(player: str) -> bool:
        return fwd.qualify(fs, "2", 2027, {"player": player, "to": "out"})["qualifying"]

    assert q("Tatum, Jayson") is True
    assert q("Guy, Bench") is False  # 19.99 < 20.0
    assert q("Injury, Back From") is False  # only 2 played games
    assert q("Nobody, Unknown") is False  # not a rotation candidate


def _game(day: str, minutes: float, eid: str) -> GameBox:
    stats = tuple(0.0 for _ in range(11))
    line = PlayerLine("7", "P", True, minutes, True, stats)
    return GameBox(eid, day, f"{day}T23:00Z", 2027, "regular-season", "1", "2", "H", "A", 100, 90, (line,))


def test_last10_minutes_is_point_in_time():
    games = [_game("2026-11-19", 10.0, "a"), _game("2026-11-20", 20.0, "b"), _game("2026-11-21", 60.0, "c")]
    assert fwd.last10_minutes(games, "2026-11-21") == {"7": (15.0, 2)}  # the report's own date excluded


def test_detect_tc2_needs_consecutive_reports_and_a_qualifying_change(monkeypatch):
    cands = [Candidate("1", "Jayson Tatum", 33.0, 1.0)]
    fs = _forward_state({"1": (33.2, 10)}, cands)
    monkeypatch.setattr(
        fwd,
        "predict_game",
        lambda fs, game, idx: {
            v: {
                "margin": -2.0
                if idx.reports["BOS"].players
                and dict(idx.reports["BOS"].players).get("Tatum, Jayson") == "out"
                else 1.0,
                "total": 220.0,
                "reason": None,
            }
            for v in fwd.VARIANTS
        },
    )
    game = {
        "event_id": "401",
        "event_start_utc": "2026-11-22T03:00Z",
        "home_team": "Sacramento Kings",
        "away_team": "Boston Celtics",
    }
    base = [_row("BOS", "Tatum, Jayson", "Questionable"), _row("SAC", "Sabonis, Domantas", "Probable")]
    after = [_row("BOS", "Tatum, Jayson", "Out"), _row("SAC", "Sabonis, Domantas", "Probable")]
    r0 = _report("R0.pdf", "2026-11-21T20:30:00+00:00", "2026-11-21T20:31:00+00:00")
    r1 = _report("R1.pdf", "2026-11-21T20:45:00+00:00", "2026-11-21T20:52:00+00:00")
    rows = _rows_for(r0, base) + _rows_for(r1, after)
    now = _utc("2026-11-21T21:00:00")
    events, scanned = fwd.detect_tc2(fs, [game], [r0, r1], rows, [], now, set())
    assert scanned == ["R0.pdf", "R1.pdf"]
    assert len(events) == 1
    ev = events[0]
    assert ev["published_at_utc"] == r1["published_at_utc"] and ev["fetched_at_utc"] == r1["fetched_at_utc"]
    assert ev["delta"]["margin"] == pytest.approx(-3.0)
    assert ev["before_reason"] == "no_before_quote"
    # Already processed: nothing again.
    assert fwd.detect_tc2(fs, [game], [r0, r1], rows, [], now, {"R0.pdf", "R1.pdf"})[0] == []
    # A 30-minute hole between reports dates no event from the later one.
    r1_late = _report("R1.pdf", "2026-11-21T21:00:00+00:00", "2026-11-21T21:02:00+00:00")
    rows_late = _rows_for(r0, base) + _rows_for(r1_late, after)
    assert (
        fwd.detect_tc2(fs, [game], [r0, r1_late], rows_late, [], _utc("2026-11-21T21:05:00"), set())[0] == []
    )
    # A report fetched after "now" is not usable yet.
    assert fwd.detect_tc2(fs, [game], [r0, r1], rows, [], _utc("2026-11-21T20:50:00"), set())[0] == []


# --------------------------------------------------------------------------- Polymarket conventions
def _pm_market(kind: str, line, long_team: str | None, long_desc: str, bid: float, ask: float):
    market = {
        "market_slug": f"{kind}-slug",
        "market_type": kind,
        "line": line,
        "sides": [
            {
                "is_long": True,
                "team": long_team,
                "description": long_desc,
                "line": None if kind != "spread" else float(long_desc),
            },
            {"is_long": False, "team": None, "description": "other", "line": None},
        ],
    }
    raw = {
        "slug": f"{kind}-slug",
        "status": "MARKET_STATUS_OPEN",
        "active": True,
        "closed": False,
        "bestBidQuote": {"value": str(bid)},
        "bestAskQuote": {"value": str(ask)},
    }
    return market, raw


def test_market_quote_home_margin_and_over_conventions():
    # Away team +3.5 long at 51.5c: home covers -3.5 with 48.5%, contract line 3.5 (home margin > 3.5).
    m, raw = _pm_market("spread", 3.5, "Portland Trail Blazers", "+3.5", 0.51, 0.52)
    q = fwd.market_quote(m, raw, "New York Knicks", "Portland Trail Blazers")
    assert q["contract_line"] == 3.5 and q["p_high"] == pytest.approx(0.485)
    # Home team +2.5 long at 60c: home covers with 60%, home margin > -2.5.
    m, raw = _pm_market("spread", 2.5, "New York Knicks", "+2.5", 0.59, 0.61)
    q = fwd.market_quote(m, raw, "New York Knicks", "Portland Trail Blazers")
    assert q["contract_line"] == -2.5 and q["p_high"] == pytest.approx(0.60)
    m, raw = _pm_market("total", 215.5, None, "Under", 0.40, 0.42)
    q = fwd.market_quote(m, raw, "New York Knicks", "Portland Trail Blazers")
    assert q["contract_line"] == 215.5 and q["p_high"] == pytest.approx(0.59)
    # A long side naming neither team (or an unknown total side) is declined, not guessed.
    m, raw = _pm_market("spread", 3.5, "Boston Celtics", "+3.5", 0.51, 0.52)
    assert fwd.market_quote(m, raw, "New York Knicks", "Portland Trail Blazers") is None
    m, raw = _pm_market("total", 215.5, None, "O 215.5", 0.40, 0.42)
    assert fwd.market_quote(m, raw, "New York Knicks", "Portland Trail Blazers") is None
    # Wider than 10c: no mid.
    m, raw = _pm_market("total", 215.5, None, "Over", 0.30, 0.45)
    assert fwd.market_quote(m, raw, "New York Knicks", "Portland Trail Blazers") is None


# --------------------------------------------------------------------------- test A selection
def _cap(event: str, market: str, observed: str, line: float, p_low: float = 0.5) -> ConsensusCapture:
    return ConsensusCapture(event, market, _utc(observed), _utc("2026-11-22T00:30:00"), line, p_low, 1)


def test_line_movement_uses_the_earliest_decision_with_x_and_y():
    sig = {"spread": 13.792, "total": 17.615}
    caps = {
        ("E1", "spread"): [
            _cap("E1", "spread", "2026-11-21T16:00:00", 3.0),
            _cap("E1", "spread", "2026-11-21T18:00:00", 4.0),
            _cap("E1", "spread", "2026-11-22T00:15:00", 5.5),  # the T-10 benchmark (tip 00:30)
        ]
    }
    tip = _utc("2026-11-22T00:30:00")
    decisions = [
        # No consensus within 15 minutes before it: skipped.
        {"event_id": "E1", "t": _utc("2026-11-21T15:00:00"), "tip": tip, "model": {"spread": 9.0}},
        {"event_id": "E1", "t": _utc("2026-11-21T18:05:00"), "tip": tip, "model": {"spread": 7.0}},
        {"event_id": "E1", "t": _utc("2026-11-21T16:10:00"), "tip": tip, "model": {"spread": 6.0}},
        {"event_id": "E1", "t": _utc("2026-11-21T16:20:00"), "tip": tip, "model": {"spread": None}},
    ]
    after_tip = _utc("2026-11-22T01:00:00")
    obs = rep.line_movement_observations(decisions, caps, sig, after_tip)["spread"]
    assert len(obs) == 1
    o = obs[0]
    assert o["t"] == _utc("2026-11-21T16:10:00")  # earliest usable, not the first listed
    assert o["x"] == pytest.approx(6.0 - 3.0) and o["y"] == pytest.approx(5.5 - 3.0)
    # Before tip nothing is scored (the T-10 line is not final).
    assert rep.line_movement_observations(decisions, caps, sig, _utc("2026-11-22T00:20:00"))["spread"] == []
    # A T-10 benchmark older than tip - 40 min is stale: no observation.
    stale = {("E1", "spread"): caps[("E1", "spread")][:2]}
    assert rep.line_movement_observations(decisions[2:3], stale, sig, after_tip)["spread"] == []


def test_verdict_a_is_pending_below_400_and_uses_only_the_first_400():
    obs = [
        {"tip": _utc("2026-11-21T00:00:00") + timedelta(hours=i), "x": float(i % 7), "y": float(i % 7)}
        for i in range(450)
    ]
    as_of = _utc("2027-01-01T00:00:00")
    assert rep.verdict_a(obs[:399], as_of)["verdict"] == "PENDING"
    v = rep.verdict_a(obs, as_of)
    assert v["verdict"] == "PASS" and v["n"] == 400 and v["after_evaluation"] == 50
