"""NCAAF v7 live data (quick 261121-v7l): incremental discovery/fetch/rows, PIT serving, fail-closed, daily wiring.

The ESPN host is a ``httpx.MockTransport`` fake; the serving tests build a v7 store from the committed parity
fixture (outputs/ncaaf/cfb_v7_parity_fixture.json) and compare against ``price_game`` on the same inputs.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from model_prediction.cli import daily
from model_prediction.config import PROJECT_ROOT
from model_prediction.data_sources import cfb_pbp_live as live
from model_prediction.features import cfb_features_v7 as core
from model_prediction.models.cfb_structural_v7 import (
    NO_CALL_NO_CLASS,
    NO_CALL_PBP_MISSING,
    CFBStructuralV7Model,
    load_cfb_structural_v7_artifact,
    price_game,
    price_v7_slate,
)
from model_prediction.runtime_paths import RuntimePaths

FIXTURE = PROJECT_ROOT / "outputs" / "ncaaf" / "cfb_v7_parity_fixture.json"
PAD_FBS = [str(90000 + i) for i in range(live.MIN_FBS_TEAMS)]  # never-playing ids: the list must look real


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%MZ")


# =============================================================================================== fake ESPN
def _sb_event(
    eid: str, home: str, away: str, kickoff: datetime, state: str, hs: int = 0, as_: int = 0
) -> dict:
    name = {"final": "STATUS_FINAL", "pending": "STATUS_SCHEDULED", "in": "STATUS_IN_PROGRESS"}[state]
    return {
        "id": eid,
        "date": _iso(kickoff),
        "season": {"year": 2030},
        "competitions": [
            {
                "neutralSite": False,
                "status": {
                    "type": {
                        "name": name,
                        "completed": state == "final",
                        "state": "post" if state == "final" else "pre",
                    }
                },
                "competitors": [
                    {"homeAway": "home", "team": {"id": home}, "score": str(hs)},
                    {"homeAway": "away", "team": {"id": away}, "score": str(as_)},
                ],
            }
        ],
    }


def _summary(home: str, away: str, kickoff: datetime, hs: int, as_: int, *, plays: bool = True) -> dict:
    drives = []
    if plays:
        for team in (home, away):
            drives.append(
                {
                    "team": {"id": team},
                    "timeElapsed": {"displayValue": "2:00"},
                    "plays": [
                        {
                            "type": {"text": "Rush"},
                            "text": f"{team} run {i}",
                            "start": {"down": 1, "distance": 10, "yardsToEndzone": 60, "team": {"id": team}},
                            "statYardage": 4 + i,
                            "period": {"number": 1},
                            "clock": {"displayValue": f"1{i}:00"},
                            "homeScore": 0,
                            "awayScore": 0,
                        }
                        for i in range(3)
                    ],
                }
            )
    return {
        "header": {
            "competitions": [
                {
                    "date": _iso(kickoff),
                    "neutralSite": False,
                    "status": {"type": {"completed": True, "name": "STATUS_FINAL", "state": "post"}},
                    "competitors": [
                        {"homeAway": "home", "id": home, "score": str(hs)},
                        {"homeAway": "away", "id": away, "score": str(as_)},
                    ],
                }
            ]
        },
        "drives": {"previous": drives},
        "boxscore": {"teams": []},
    }


class FakeESPN:
    def __init__(self, fbs: list[str]) -> None:
        self.boards: dict[tuple[str, int], list[dict]] = defaultdict(list)
        self.summaries: dict[str, dict] = {}
        self.fbs = fbs
        self.summary_calls: list[str] = []
        self.scoreboard_calls: list[tuple[str, int]] = []

    clock: Now | None = None  # when set, every request takes a minute of wall time
    served_at: dict[tuple[str, Any], datetime]

    def handler(self, request: httpx.Request) -> httpx.Response:
        url, params = str(request.url), request.url.params
        if self.clock is not None:
            self.clock.at += timedelta(minutes=1)
            self.served_at = getattr(self, "served_at", {})
        if "/scoreboard" in url:
            key = (params["dates"], int(params["groups"]))
            self.scoreboard_calls.append(key)
            if self.clock is not None:
                self.served_at[("scoreboard", key)] = self.clock.at
            return httpx.Response(200, json={"events": self.boards.get(key, [])})
        if "/summary" in url:
            eid = params["event"]
            self.summary_calls.append(eid)
            if self.clock is not None:
                self.served_at[("summary", eid)] = self.clock.at
            if eid not in self.summaries:
                return httpx.Response(404)
            return httpx.Response(200, content=json.dumps(self.summaries[eid]).encode())
        if "/groups/80/teams" in url:
            items = [{"$ref": f"http://x/seasons/2030/teams/{t}?lang=en"} for t in self.fbs]
            return httpx.Response(200, json={"count": len(items), "pageCount": 1, "items": items})
        return httpx.Response(500)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def add(self, day: str, groups: tuple[int, ...], event: dict, summary: dict | None = None) -> None:
        for g in groups:
            self.boards[(day.replace("-", ""), g)].append(event)
        if summary is not None:
            self.summaries[event["id"]] = summary


class Now:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def _seeded_paths(tmp_path: Path) -> RuntimePaths:
    paths = RuntimePaths.for_test(tmp_path)
    paths.ncaaf_v7_root.mkdir(parents=True)
    (paths.ncaaf_v7_root / live.SEED_MARKER).write_text("{}", encoding="utf-8")
    return paths


SAT = datetime(2030, 8, 23, 23, 0, tzinfo=UTC)  # 19:00 ET Friday 08-23
FBS_A, FBS_B, FCS_C, FCS_D = "1", "2", "501", "502"


def _slate(fake: FakeESPN) -> None:
    fake.add(
        "2030-08-23",
        (80,),
        _sb_event("A", FBS_A, FBS_B, SAT, "final", 31, 24),
        _summary(FBS_A, FBS_B, SAT, 31, 24),
    )
    kick_b = SAT - timedelta(hours=5)
    fake.add(
        "2030-08-23",
        (81,),
        _sb_event("B", FCS_C, FCS_D, kick_b, "final", 17, 14),
        _summary(FCS_C, FCS_D, kick_b, 17, 14),
    )
    kick_e = datetime(2030, 8, 24, 12, 0, tzinfo=UTC)
    fake.add(
        "2030-08-24",
        (80, 81),
        _sb_event("E", FBS_A, FCS_C, kick_e, "final", 45, 3),
        _summary(FBS_A, FCS_C, kick_e, 45, 3),
    )
    kick_f = datetime(2030, 8, 25, 17, 0, tzinfo=UTC)
    fake.add("2030-08-25", (80,), _sb_event("F", FBS_B, FBS_A, kick_f, "pending"))


def _update(paths: RuntimePaths, fake: FakeESPN, now: Now, **kw: Any) -> dict:
    return live.update(paths, client=fake.client(), now_fn=now, min_interval=0.0, **kw)


def _pbp_rows(paths: RuntimePaths) -> list[dict]:
    return list(live._iter_jsonl(live.store_for(paths).pbp_path(2030)))


# =============================================================================================== update
def test_update_discovers_fcs_games_respects_the_buffer_and_is_incremental(tmp_path, monkeypatch):
    paths = _seeded_paths(tmp_path)
    fake = FakeESPN([FBS_A, FBS_B, *PAD_FBS])
    _slate(fake)
    now = Now(datetime(2030, 8, 24, 16, 0, tzinfo=UTC))  # E kicked off 4 h ago: final, but inside the buffer

    first = _update(paths, fake, now)
    assert first["status"] == "ok", first
    assert sorted(fake.summary_calls) == ["A", "B"]  # B is FCS-vs-FCS, listed only in group 81
    rows = _pbp_rows(paths)
    assert {(r["event_id"], r["team_id"]) for r in rows} == {
        ("A", FBS_A),
        ("A", FBS_B),
        ("B", FCS_C),
        ("B", FCS_D),
    }
    by_event = {r["event_id"]: r for r in rows}
    assert by_event["B"]["game_class"] == "FCS-FCS" and by_event["A"]["game_class"] == "FBS-FBS"
    for r in rows:
        assert r["observed_at_utc"] == "2030-08-24T16:00:00Z"
        assert r["kickoff_utc"] and r["off_success_rate_ng"] is not None

    # Second run: nothing new is fetched and no raw file is reprocessed.
    calls = {"n": 0}
    real_game_rows = live.game_rows

    def counting(*a, **k):
        calls["n"] += 1
        return real_game_rows(*a, **k)

    monkeypatch.setattr(live, "game_rows", counting)
    fake.summary_calls.clear()
    fake.scoreboard_calls.clear()
    now.at += timedelta(minutes=30)
    second = _update(paths, fake, now)
    assert fake.summary_calls == [] and calls["n"] == 0
    assert second["features"]["raw_processed"] == 0 and len(_pbp_rows(paths)) == 4
    # Closed dates are not rescanned: only today and tomorrow (2 groups each).
    assert sorted({d for d, _ in fake.scoreboard_calls}) == ["20300824", "20300825"]

    # Once E is past kickoff + 5 h, it alone is fetched.
    now.at = datetime(2030, 8, 24, 17, 30, tzinfo=UTC)
    _update(paths, fake, now)
    assert fake.summary_calls == ["E"]
    assert {r["event_id"] for r in _pbp_rows(paths)} == {"A", "B", "E"}


def test_observation_stamps_are_taken_after_the_response(tmp_path):
    """The clock advances a minute per request: a stamp captured before the call would predate the data."""
    paths = _seeded_paths(tmp_path)
    fake = FakeESPN([FBS_A, FBS_B, *PAD_FBS])
    _slate(fake)
    now = Now(datetime(2030, 8, 24, 16, 0, tzinfo=UTC))
    fake.clock = now
    _update(paths, fake, now)
    for row in _pbp_rows(paths):
        assert row["observed_at_utc"] == live._iso(fake.served_at[("summary", row["event_id"])])
    sched = live.read_schedule(live.store_for(paths))
    scanned_0823 = max(fake.served_at[("scoreboard", ("20300823", g))] for g in (80, 81))
    for eid in ("A", "B"):
        assert sched[eid]["observed_at_utc"] == live._iso(scanned_0823)
    ledger = live.read_scan_ledger(live.store_for(paths))
    assert ledger["2030-08-23"]["first_scanned_at_utc"] == live._iso(scanned_0823)


def test_no_plays_is_retried_until_a_day_after_kickoff(tmp_path):
    paths = _seeded_paths(tmp_path)
    fake = FakeESPN([FBS_A, FBS_B, *PAD_FBS])
    fake.add(
        "2030-08-23",
        (81,),
        _sb_event("B", FCS_C, FCS_D, SAT, "final", 17, 14),
        _summary(FCS_C, FCS_D, SAT, 17, 14, plays=False),
    )
    now = Now(datetime(2030, 8, 24, 16, 0, tzinfo=UTC))
    _update(paths, fake, now)
    now.at += timedelta(hours=1)
    _update(paths, fake, now)
    assert fake.summary_calls == ["B", "B"]  # 18 h after kickoff: not terminal yet
    now.at = SAT + timedelta(hours=25)
    _update(paths, fake, now)
    now.at += timedelta(hours=1)
    _update(paths, fake, now)
    assert fake.summary_calls == ["B", "B", "B"]  # fetched >= 24 h after kickoff: terminal, not fetched again
    store = live.store_for(paths)
    assert "B" in live.terminal_without_pbp(store, live.read_schedule(store))


def test_a_404_is_retried_until_a_day_after_kickoff_then_succeeds(tmp_path):
    """quick-261122-v71 (bug hunt #4): one transient summary 404 made a just-final game points-only forever."""
    paths = _seeded_paths(tmp_path)
    fake = FakeESPN([FBS_A, FBS_B, *PAD_FBS])
    fake.add("2030-08-23", (80,), _sb_event("A", FBS_A, FBS_B, SAT, "final", 31, 24))  # summary 404s
    now = Now(datetime(2030, 8, 24, 16, 0, tzinfo=UTC))
    fake.clock = now  # advancing clock: every request takes a minute
    _update(paths, fake, now)
    store = live.store_for(paths)
    assert fake.summary_calls == ["A"]
    assert "A" not in live.terminal_without_pbp(store, live.read_schedule(store))  # < 24 h: not terminal
    now.at += timedelta(hours=1)
    _update(paths, fake, now)
    assert fake.summary_calls == ["A", "A"]  # refetched on the next run
    fake.summaries["A"] = _summary(FBS_A, FBS_B, SAT, 31, 24)  # ESPN recovers
    now.at += timedelta(hours=1)
    _update(paths, fake, now)
    assert fake.summary_calls == ["A", "A", "A"]
    assert {r["event_id"] for r in _pbp_rows(paths)} == {"A"}
    assert "A" not in live.terminal_without_pbp(store, live.read_schedule(store))


def test_a_404_a_day_after_kickoff_is_terminal_and_not_refetched(tmp_path):
    paths = _seeded_paths(tmp_path)
    fake = FakeESPN([FBS_A, FBS_B, *PAD_FBS])
    fake.add("2030-08-23", (80,), _sb_event("A", FBS_A, FBS_B, SAT, "final", 31, 24))
    now = Now(SAT + timedelta(hours=25))
    _update(paths, fake, now)
    now.at += timedelta(hours=1)
    _update(paths, fake, now)
    assert fake.summary_calls == ["A"]  # fetched >= 24 h after kickoff: terminal, bounded
    store = live.store_for(paths)
    assert "A" in live.terminal_without_pbp(store, live.read_schedule(store))


def test_budget_stops_the_run_and_the_next_run_resumes(tmp_path):
    paths = _seeded_paths(tmp_path)
    fake = FakeESPN([FBS_A, FBS_B, *PAD_FBS])
    _slate(fake)
    now = Now(datetime(2030, 8, 24, 16, 0, tzinfo=UTC))
    ticks = {"t": 0.0}

    def clock() -> float:
        ticks["t"] += 1.0  # every clock read costs a second
        return ticks["t"]

    first = _update(paths, fake, now, budget_seconds=12.0, clock=clock, sleep=lambda s: None)
    assert first.get("budget_spent") is True
    assert fake.summary_calls == []
    ledger = live.read_scan_ledger(live.store_for(paths))
    assert 0 < len(ledger) < 11  # scanned dates were persisted before the budget ran out
    _update(paths, fake, now)
    assert sorted(fake.summary_calls) == ["A", "B"]


def test_held_lock_and_unseeded_root_skip(tmp_path):
    fake = FakeESPN([FBS_A, *PAD_FBS])
    now = Now(datetime(2030, 8, 24, 16, 0, tzinfo=UTC))
    unseeded = RuntimePaths.for_test(tmp_path / "u")
    assert _update(unseeded, fake, now)["reason"] == "not_seeded"
    paths = _seeded_paths(tmp_path / "s")
    lock = live._Lock(paths.ncaaf_v7_lock)
    assert lock.acquire()
    try:
        assert _update(paths, fake, now) == {"status": "skipped", "reason": "locked"}
    finally:
        lock.release()
    assert fake.summary_calls == [] and fake.scoreboard_calls == []


def test_network_failure_is_fail_soft(tmp_path):
    paths = _seeded_paths(tmp_path)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("espn down")

    out = live.update(
        paths,
        client=httpx.Client(transport=httpx.MockTransport(down)),
        now_fn=Now(datetime(2030, 8, 24, 16, 0, tzinfo=UTC)),
        min_interval=0.0,
    )
    assert out["status"] == "partial" and out["errors"]
    gap = live.scan_gap(live.store_for(paths), 2030, datetime(2030, 8, 24, 16, 0, tzinfo=UTC))
    assert gap is not None and "unscanned" in gap


# =============================================================================================== daily wiring
def test_daily_step_respects_the_ncaaf_switch_and_is_fail_soft(monkeypatch):
    monkeypatch.setattr(daily, "is_sport_daily_enabled", lambda config, sport: False)
    monkeypatch.setattr(
        live, "update_from_data_root", lambda *a, **k: pytest.fail("ran for a disabled sport")
    )
    assert daily._run_ncaaf_v7_pbp_update({}, "unused") == {
        "status": "skipped",
        "reason": "ncaaf_daily_forecast_disabled",
    }
    monkeypatch.setattr(daily, "is_sport_daily_enabled", lambda config, sport: True)

    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(live, "update_from_data_root", boom)
    result = daily._run_ncaaf_v7_pbp_update({}, "unused")
    assert result == {"status": "error"}
    assert daily._material_daily_errors({"step1o_ncaaf_v7_pbp": result}) == []


# =============================================================================================== serving
@pytest.fixture(scope="module")
def model() -> CFBStructuralV7Model:
    return CFBStructuralV7Model(load_cfb_structural_v7_artifact())


@pytest.fixture(scope="module")
def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _cfbd_rows(teams: list[str], season: int) -> list[dict]:
    return [
        {
            "season": season,
            "espn_team_id": t,
            "returning_off_ppa_pct": 0.3 + 0.01 * i,
            "talent_prev_season": 600 + 7 * i,
            "recruiting_points_4yr_weighted": 180 + 3 * (i % 7),
            "portal_out_stars_sum": 10 + (i % 5),
        }
        for i, t in enumerate(teams)
    ]


def _build_store(root: Path, fixture: dict) -> dict:
    """A v7 store holding exactly the fixture's games, observed 6 h after kickoff, every season date scanned."""
    store = live.Store(root / "research" / "ncaaf_v7")
    rows = fixture["rows"]
    by_season: dict[int, list[dict]] = defaultdict(list)
    events: dict[str, dict] = {}
    for r in rows:
        kick = datetime.fromisoformat(r["kickoff_utc"])
        r = dict(r, observed_at_utc=live._iso(kick + timedelta(hours=6)))
        by_season[int(r["season"])].append(r)
        ev = events.setdefault(
            r["event_id"], {"season": int(r["season"]), "kickoff": kick, "neutral": r["neutral_site"]}
        )
        side = "home" if r["is_home"] else "away"
        ev[f"{side}_id"], ev[f"{side}_score"] = r["team_id"], r["points_for"]
    for season, srows in by_season.items():
        live._append_jsonl(store.pbp_path(season), srows)
    live._append_jsonl(
        store.schedule,
        [
            {
                "event_id": eid,
                "season": ev["season"],
                "kickoff_utc": live._iso(ev["kickoff"]),
                "home_id": ev["home_id"],
                "away_id": ev["away_id"],
                "home_score": ev["home_score"],
                "away_score": ev["away_score"],
                "neutral_site": ev["neutral"],
                "status": live.FINAL,
                "observed_at_utc": live._iso(ev["kickoff"] + timedelta(hours=6)),
            }
            for eid, ev in events.items()
        ],
    )
    tg = [core.team_game_from_row(r) for r in rows]
    classes = core.season_classes(tg)
    for season in by_season:
        fbs = sorted(t for (t, s), c in classes.items() if s == season and c == core.FBS)
        live._write_json_atomic(
            store.classes_path(season),
            {"season": season, "observed_at_utc": f"{season}-08-01T00:00:00Z", "fbs_team_ids": fbs + PAD_FBS},
        )
    season = fixture["season"]
    first, last = live.season_dates(season)
    ledger, day = {}, first
    while day <= last:
        start = live._iso(datetime(day.year, day.month, day.day, 12, tzinfo=UTC))
        ledger[day.isoformat()] = {
            "first_scanned_at_utc": start,
            "scanned_at_utc": start,
            "events": [],
            "closed": True,
        }
        day += timedelta(days=1)
    live._write_json_atomic(store.scan_ledger, ledger)
    teams = sorted({t for (t, s) in classes if s == season})
    live._append_jsonl(store.cfbd_priors, _cfbd_rows(teams, season))
    return {"store": store, "events": events, "classes": classes, "teams": teams}


def _game(case: dict) -> dict:
    return {
        "event_id": f"{case['home_id']}-{case['away_id']}",
        "home_team_id": case["home_id"],
        "away_team_id": case["away_id"],
        "is_neutral_site": case["neutral"],
        "spread_home_line": case["spread_home_line"],
        "total_line": case["total_line"],
    }


def test_slate_pricing_equals_price_game_on_the_same_inputs(tmp_path, model, fixture):
    built = _build_store(tmp_path, fixture)
    rows = sorted(
        (core.team_game_from_row(r) for r in fixture["rows"]),
        key=lambda r: (r.kickoff, r.event_id, -r.home_sign),
    )
    covariates = core.standardise_covariates(_cfbd_rows(built["teams"], fixture["season"]))
    for case in fixture["cases"]:
        decision = datetime.fromisoformat(case["decision_utc"])
        results, summary = price_v7_slate(tmp_path, decision, [_game(case)], model=model)
        assert summary["priced"] == 1, results
        expected = price_game(
            model,
            rows,
            season=fixture["season"],
            home_id=case["home_id"],
            away_id=case["away_id"],
            neutral=case["neutral"],
            decision=decision,
            classes=built["classes"],
            covariates=covariates,
            spread_home_line=case["spread_home_line"],
            total_line=case["total_line"],
        )
        got = results[0]
        assert got["mu_margin"] == pytest.approx(expected.mu_margin, abs=1e-9)
        assert got["mu_total"] == pytest.approx(expected.mu_total, abs=1e-9)
        for key, value in expected.probabilities.items():
            assert got["probabilities"][key] == pytest.approx(value, abs=1e-9)


def _last_admitted_game(fixture: dict, built: dict, team: str, decision: datetime) -> str:
    games = [
        (ev["kickoff"], eid)
        for eid, ev in built["events"].items()
        if ev["season"] == fixture["season"]
        and team in (ev["home_id"], ev["away_id"])
        and core.admitted(ev["kickoff"], decision)
    ]
    return max(games)[1]


def _rewrite_pbp(store: live.Store, season: int, fn) -> None:
    path = store.pbp_path(season)
    rows = [fn(r) for r in live._iter_jsonl(path)]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows if r is not None), encoding="utf-8")


def test_a_game_finished_after_the_decision_never_enters(tmp_path, model, fixture):
    built = _build_store(tmp_path, fixture)
    case = fixture["cases"][-1]
    decision = datetime.fromisoformat(case["decision_utc"])
    base, _ = price_v7_slate(tmp_path, decision, [_game(case)], model=model)
    # A game of the home team that kicked off 4 h before T (unfinished by the buffer) with absurd stats, already
    # visible in the store (observed before T), and a finished one observed only after T.
    template = next(
        r for r in fixture["rows"] if r["team_id"] == case["home_id"] and r["season"] == fixture["season"]
    )
    absurd = {"off_success_rate_ng": 0.99, "off_yards_per_play_ng": 40.0, "points_for": 99}
    live_rows = [
        dict(
            template,
            **absurd,
            event_id="leak-live",
            kickoff_utc=live._iso(decision - timedelta(hours=4)),
            observed_at_utc=live._iso(decision - timedelta(minutes=1)),
        ),
        dict(
            template,
            **absurd,
            event_id="leak-late",
            kickoff_utc=live._iso(decision - timedelta(hours=30)),
            observed_at_utc=live._iso(decision + timedelta(hours=1)),
        ),
    ]
    live._append_jsonl(built["store"].pbp_path(fixture["season"]), live_rows)
    # The scoreboard already shows a 3 h-old game of the home team final, without play-by-play yet: inside the
    # buffer it neither feeds the ratings nor is demanded by the completeness check.
    live._append_jsonl(
        built["store"].schedule,
        [
            {
                "event_id": "fresh-final",
                "season": fixture["season"],
                "kickoff_utc": live._iso(decision - timedelta(hours=3)),
                "home_id": case["home_id"],
                "away_id": "77777",
                "home_score": 70,
                "away_score": 0,
                "neutral_site": False,
                "status": live.FINAL,
                "observed_at_utc": live._iso(decision - timedelta(minutes=2)),
            }
        ],
    )
    after, _ = price_v7_slate(tmp_path, decision, [_game(case)], model=model)
    assert after == base and after[0]["status"] == "ok"


def test_missing_pbp_for_a_team_fails_closed_for_its_games_only(tmp_path, model, fixture):
    built = _build_store(tmp_path, fixture)
    case = fixture["cases"][-1]
    other = next(
        c
        for c in fixture["cases"]
        if {c["home_id"], c["away_id"]}.isdisjoint({case["home_id"], case["away_id"]})
    )
    decision = datetime.fromisoformat(case["decision_utc"])
    gone = _last_admitted_game(fixture, built, case["home_id"], decision)
    _rewrite_pbp(built["store"], fixture["season"], lambda r: None if r["event_id"] == gone else r)
    results, _ = price_v7_slate(tmp_path, decision, [_game(case), _game(other)], model=model)
    assert results[0]["status"] == "no_call" and results[0]["reason"] == NO_CALL_PBP_MISSING
    assert gone in results[0]["detail"]
    assert results[1]["status"] == "ok"
    # ESPN confirmed it has no play-by-play for that game: it becomes a score-only row and the call comes back.
    # A 404 is terminal only when fetched >= 24 h after kickoff (quick-261122-v71, bug hunt #4).
    fetched = built["events"][gone]["kickoff"] + timedelta(hours=25)
    assert fetched <= decision
    live._append_jsonl(
        built["store"].manifest,
        [
            {
                "event_id": gone,
                "season": fixture["season"],
                "status": "http_404",
                "fetched_at_utc": live._iso(fetched),
            }
        ],
    )
    results, _ = price_v7_slate(tmp_path, decision, [_game(case)], model=model)
    assert results[0]["status"] == "ok"


def test_a_pbp_row_observed_after_the_decision_is_missing_at_the_decision(tmp_path, model, fixture):
    built = _build_store(tmp_path, fixture)
    case = fixture["cases"][-1]
    decision = datetime.fromisoformat(case["decision_utc"])
    late = _last_admitted_game(fixture, built, case["home_id"], decision)
    _rewrite_pbp(
        built["store"],
        fixture["season"],
        lambda r: (
            dict(r, observed_at_utc=live._iso(decision + timedelta(minutes=5)))
            if r["event_id"] == late
            else r
        ),
    )
    results, _ = price_v7_slate(tmp_path, decision, [_game(case)], model=model)
    assert results[0]["reason"] == NO_CALL_PBP_MISSING
    results, _ = price_v7_slate(tmp_path, decision + timedelta(minutes=10), [_game(case)], model=model)
    assert results[0]["status"] == "ok"


def test_a_stale_scheduled_game_is_demanded_and_a_postponed_one_is_not(tmp_path, model, fixture):
    """The update scanned a date before its games and then failed: the game still reads "scheduled" although its
    kickoff + 5 h has passed. It must be demanded (no-call), while a postponed game is never demanded."""
    built = _build_store(tmp_path, fixture)
    case = fixture["cases"][-1]
    decision = datetime.fromisoformat(case["decision_utc"])
    base, _ = price_v7_slate(tmp_path, decision, [_game(case)], model=model)

    def sched(eid: str, status: str, kickoff: datetime) -> dict:
        return {
            "event_id": eid,
            "season": fixture["season"],
            "kickoff_utc": live._iso(kickoff),
            "home_id": case["home_id"],
            "away_id": "77777",
            "home_score": None,
            "away_score": None,
            "neutral_site": False,
            "status": status,
            "observed_at_utc": live._iso(kickoff - timedelta(hours=8)),
        }

    live._append_jsonl(built["store"].schedule, [sched("ppd", live.POSTPONED, decision - timedelta(days=2))])
    results, _ = price_v7_slate(tmp_path, decision, [_game(case)], model=model)
    assert results == base and results[0]["status"] == "ok"
    live._append_jsonl(built["store"].schedule, [sched("stale", live.PENDING, decision - timedelta(hours=6))])
    results, _ = price_v7_slate(tmp_path, decision, [_game(case)], model=model)
    assert results[0]["reason"] == NO_CALL_PBP_MISSING and "stale" in results[0]["detail"]


def test_live_history_game_without_pbp_fails_closed(tmp_path, model, fixture):
    _build_store(tmp_path, fixture)
    case = fixture["cases"][-1]
    decision = datetime.fromisoformat(case["decision_utc"])
    history = tmp_path / "processed" / "ncaaf" / "games.jsonl"
    history.parent.mkdir(parents=True)
    history.write_text(
        json.dumps(
            {
                "event_id": "unseen-by-v7",
                "status": "completed",
                "season_year": fixture["season"],
                "event_start_utc": live._iso(decision - timedelta(days=1)),
                "home_team_id": case["home_id"],
                "away_team_id": "77777",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    results, _ = price_v7_slate(tmp_path, decision, [_game(case)], model=model)
    assert results[0]["reason"] == NO_CALL_PBP_MISSING and "unseen-by-v7" in results[0]["detail"]


def test_scan_gap_and_missing_class_list_no_call_the_whole_slate(tmp_path, model, fixture):
    built = _build_store(tmp_path, fixture)
    case = fixture["cases"][-1]
    decision = datetime.fromisoformat(case["decision_utc"])
    store = built["store"]
    ledger = live.read_scan_ledger(store)
    del ledger[(live.et_date(decision) - timedelta(days=3)).isoformat()]
    live._write_json_atomic(store.scan_ledger, ledger)
    results, summary = price_v7_slate(
        tmp_path, decision, [_game(c) for c in fixture["cases"][-3:]], model=model
    )
    assert summary["priced"] == 0 and summary["no_call"] == {NO_CALL_PBP_MISSING: 3}
    assert "unscanned" in results[0]["detail"]

    built = _build_store(tmp_path / "b", fixture)
    built["store"].classes_path(fixture["season"] - 1).unlink()  # last season's labels feed the prior
    results, summary = price_v7_slate(tmp_path / "b", decision, [_game(case)], model=model)
    assert results[0]["reason"] == NO_CALL_NO_CLASS


def test_sync_cfbd_adds_whole_new_seasons_only(tmp_path):
    paths = _seeded_paths(tmp_path)
    store = live.store_for(paths)
    live._append_jsonl(store.cfbd_priors, _cfbd_rows(["1", "2"], 2026))
    source = tmp_path / "source.jsonl"
    live._append_jsonl(source, _cfbd_rows(["1", "2", "3"], 2026) + _cfbd_rows(["1", "2", "3"], 2027))
    out = live.sync_cfbd(paths, source)
    assert out["seasons_added"] == [2027] and out["rows"] == 3
    assert len(live.read_cfbd_rows(store, 2026)) == 2 and len(live.read_cfbd_rows(store, 2027)) == 3
    assert live.sync_cfbd(paths, source)["rows"] == 0


def test_season_calendar():
    assert live.cfb_season(date(2027, 1, 10)) == 2026
    assert live.cfb_season(date(2026, 8, 29)) == 2026
    assert live.et_date(datetime(2026, 11, 22, 3, 0, tzinfo=UTC)) == date(2026, 11, 21)
