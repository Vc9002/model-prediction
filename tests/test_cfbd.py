"""Network-free tests for the CollegeFootballData client and normalizers (NCAAF v7 research).

Fixtures under ``tests/fixtures/cfbd`` are real CFBD rows trimmed from recorded responses (raw rows
keep the provider moneylines; the normalized output must not)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from model_prediction.data_sources import cfbd

FIXTURES = Path(__file__).parent / "fixtures" / "cfbd"
SECRET = "k" * 40
ESPN_IDS = {"61", "62", "2393", "36", "265", "399", "2429"}


def _fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _client(tmp_path, handler, **kwargs) -> cfbd.CFBDClient:
    return cfbd.CFBDClient(
        environ={"CFBD_API_KEY": SECRET},
        cache_root=tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _s: None,
        **kwargs,
    )


# ------------------------------------------------------------------------------------------- client
def test_no_key_fails_closed():
    with pytest.raises(cfbd.CFBDAuthError):
        cfbd.CFBDClient(environ={})


def test_bearer_auth_cache_ledger_and_no_key_on_disk(tmp_path):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["Authorization"] == f"Bearer {SECRET}"
        assert SECRET not in str(request.url)
        return httpx.Response(
            200,
            json=[{"year": 2023, "team": "Georgia", "talent": 977.87}],
            headers={"X-CallLimit-Remaining": "2500"},
        )

    client = _client(tmp_path, handler)
    first = client.fetch("talent", {"year": 2023})
    second = client.fetch("talent", {"year": 2023})
    assert first == second and len(seen) == 1
    assert client.remaining == 2500 and client.ledger_calls() == 1
    meta = client.cached_meta("talent", {"year": 2023})
    assert meta is not None and meta["params"] == {"year": 2023} and meta["fetched_at_utc"]
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert SECRET not in path.read_text(encoding="utf-8")


def test_call_ceiling_counts_every_attempt_across_processes(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=[], headers={"X-CallLimit-Remaining": "2500"})

    _client(tmp_path, handler, max_calls=2).fetch("talent", {"year": 2020})
    # a new client on the same cache root sees the first process's attempt
    again = _client(tmp_path, handler, max_calls=2)
    again.fetch("talent", {"year": 2021})
    with pytest.raises(cfbd.CFBDQuotaError):
        again.fetch("talent", {"year": 2022})
    assert len(calls) == 2


def test_quota_floor_and_429_stop(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"X-CallLimit-Remaining": "150"})

    client = _client(tmp_path, handler)
    with pytest.raises(cfbd.CFBDQuotaError):
        client.fetch("lines", {"year": 2023})
    with pytest.raises(cfbd.CFBDQuotaError, match="floor"):
        client.fetch("lines", {"year": 2024})  # 150 < 200: refused before any request
    assert client.ledger_calls() == 1


def test_server_error_is_retried_and_each_attempt_is_ledgered(tmp_path):
    statuses = iter([503, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(statuses), json=[], headers={"X-CallLimit-Remaining": "2400"})

    client = _client(tmp_path, handler)
    assert client.fetch("ratings/sp", {"year": 2020}) == []
    assert client.ledger_calls() == 2


def test_refused_endpoint_raises_auth_error(tmp_path):
    client = _client(tmp_path, lambda request: httpx.Response(401, headers={"X-CallLimit-Remaining": "2400"}))
    with pytest.raises(cfbd.CFBDAuthError):
        client.fetch("ratings/sp", {"year": 2022})


# ----------------------------------------------------------------------------------------- identity
def test_team_directory_prefers_real_team_over_stubs_and_declines_unknowns():
    directory = cfbd.CFBDTeamDirectory(_fixture("teams.json"), ESPN_IDS)
    assert directory.resolve("Georgia") == cfbd.TeamMatch(61, "61", cfbd.MATCHED_ESPN_ID)
    # "Charlotte" is the FBS 49ers (2429) and an unclassified stub (3253)
    assert directory.resolve("Charlotte").cfbd_team_id == 2429
    # "Albany" exists only on stubs in /teams; the season tables mean UAlbany (ESPN 399)
    assert directory.resolve("Albany") == cfbd.TeamMatch(399, "399", cfbd.MATCHED_ESPN_ID)
    assert directory.resolve("Nowhere State").status == cfbd.NO_CALL_UNMATCHED_NAME
    unseen = cfbd.CFBDTeamDirectory(_fixture("teams.json"), ESPN_IDS - {"61"}).resolve("Georgia")
    assert unseen.cfbd_team_id == 61 and unseen.espn_team_id is None
    assert unseen.status == cfbd.NO_CALL_ESPN_ID_UNSEEN


# -------------------------------------------------------------------------------------------- lines
EVENTS = {
    "401523990": ("36", "265"),  # Colorado State v Washington State, same orientation
    "400876049": ("62", "2393"),  # 2016 Hawaii Bowl: ESPN home is Hawai'i, CFBD home is Middle Tennessee
    "401856873": ("12", "9"),
}
AS_OF = datetime(2026, 11, 21, 20, 0, tzinfo=UTC)


def test_moneyline_becomes_probability():
    assert cfbd.moneyline_implied_prob(-218) == pytest.approx(218 / 318)
    assert cfbd.moneyline_implied_prob(180) == pytest.approx(100 / 280)
    assert cfbd.moneyline_implied_prob(50) is None
    assert cfbd.moneyline_implied_prob(None) is None


def test_parse_lines_orientation_close_and_no_american_odds():
    raw = _fixture("lines.json")
    rows = cfbd.parse_lines(raw, EVENTS, AS_OF)
    by = {(r["cfbd_game_id"], r["provider"]): r for r in rows}

    dk = by[(401523990, "DraftKings")]
    assert dk["espn_match_status"] == cfbd.EVENT_SAME_ORIENTATION and dk["espn_event_id"] == "401523990"
    assert dk["completed"] and dk["spread_home_close"] == 11 and dk["spread_home_open"] == 14
    assert dk["spread_espn_home_close"] == 11 and dk["total_close"] == 55.5 and dk["total_open"] == 57.5
    assert dk["home_ml_implied_prob_close"] == pytest.approx(100 / 395)
    assert dk["away_ml_implied_prob_close"] == pytest.approx(440 / 540)
    assert dk["home_ml_novig_prob_close"] < 0.5 and dk["ml_spread_sign_conflict"] is False
    assert dk["observed_at_utc"] == dk["start_utc"] == "2023-09-02T23:00:00Z"
    assert dk["as_published_verified"] is False

    bowl = [r for r in rows if r["cfbd_game_id"] == 400876049]
    assert bowl and all(r["espn_match_status"] == cfbd.EVENT_SWAPPED for r in bowl)
    assert all(r["spread_espn_home_close"] == -r["spread_home_close"] for r in bowl)

    future = [r for r in rows if r["cfbd_game_id"] == 401856873]
    assert future and not any(r["completed"] for r in future)
    for r in future:  # the stored number is today's line, not a close
        assert r["spread_home_close"] is None and r["total_close"] is None
        assert r["home_ml_implied_prob_close"] is None and r["home_score"] is None
    assert future[0]["spread_home_open"] == -4.5

    american = {
        line[k] for g in raw for line in g["lines"] for k in ("homeMoneyline", "awayMoneyline") if line[k]
    }
    for r in rows:
        for key, value in r.items():
            if "prob" in key and value is not None:
                assert 0.0 < value < 1.0
            assert not (isinstance(value, (int, float)) and "ml" in key and value in american)
        assert not any("moneyline" in key.lower() for key in r)


def test_event_with_other_teams_is_declined():
    raw = _fixture("lines.json")[:1]
    rows = cfbd.parse_lines(raw, {"401523990": ("36", "999")}, AS_OF)
    assert {r["espn_match_status"] for r in rows} == {cfbd.NO_CALL_EVENT_TEAM_MISMATCH}
    assert all(r["espn_event_id"] is None and r["spread_espn_home_close"] is None for r in rows)
    unseen = cfbd.parse_lines(raw, {}, AS_OF)
    assert {r["espn_match_status"] for r in unseen} == {cfbd.NO_CALL_EVENT_UNSEEN}


def test_scores_shortly_after_kickoff_are_not_read_as_final():
    raw = _fixture("lines.json")[:1]  # kicked off 2023-09-02T23:00Z with final scores present
    live = cfbd.parse_lines(raw, EVENTS, datetime(2023, 9, 3, 1, 0, tzinfo=UTC))
    assert not any(r["completed"] for r in live)
    assert all(r["spread_home_close"] is None and r["home_score"] is None for r in live)
    done = cfbd.parse_lines(raw, EVENTS, datetime(2023, 9, 3, 4, 0, tzinfo=UTC))
    assert all(r["completed"] for r in done)


# ------------------------------------------------------------------------------------------- priors
def test_preseason_poll_is_week_one_only():
    polls = cfbd.parse_preseason_polls(_fixture("rankings_2023.json"))
    assert polls[61] == {"ap": 1, "coaches": 1}
    assert 99 in polls and polls[99]["ap"] == 5  # LSU #5 preseason, gone from the week-2 top 5
    assert all(set(v) <= {"ap", "coaches"} for v in polls.values())


def test_portal_cutoff_on_entry_date():
    rows = _fixture("portal_2023.json")
    agg, stats = cfbd.aggregate_portal(rows, datetime(2023, 8, 26, 18, 30, tzinfo=UTC))
    assert agg["Georgia"].out_count == 4 and agg["Georgia"].in_count == 2
    assert stats.after_cutoff == 0 and stats.counted == 6
    early, early_stats = cfbd.aggregate_portal(rows, datetime(2023, 1, 1, tzinfo=UTC))
    assert early["Georgia"].in_count == 2 and early["Georgia"].out_count == 0
    assert early["Georgia"].excluded_after_cutoff == 4 and early_stats.after_cutoff == 4
    undated = [dict(rows[0], transferDate=None)]
    _, undated_stats = cfbd.aggregate_portal(undated, datetime(2023, 8, 26, tzinfo=UTC))
    assert undated_stats.undated == 1 and undated_stats.counted == 0


def test_recruiting_window_weights_and_missing_classes():
    rec = cfbd.parse_recruiting(_fixture("recruiting_teams.json"))
    win = cfbd.recruiting_window(rec, 2023, "Georgia")
    pts = [rec[(y, "Georgia")][1] for y in (2023, 2022, 2021, 2020)]
    assert win["recruiting_classes_present"] == 4
    assert win["recruiting_points_4yr_weighted"] == pytest.approx(
        0.15 * pts[0] + 0.25 * pts[1] + 0.30 * pts[2] + 0.30 * pts[3]
    )
    # the window is classes Y..Y-3: 2026 has only the 2023 class in the fixture
    assert cfbd.recruiting_window(rec, 2026, "Georgia")["recruiting_points_4yr_weighted"] is None


def test_sp_skips_national_averages():
    sp = cfbd.parse_sp(_fixture("sp_2022.json"))
    assert set(sp) == {(2022, "Georgia")} and sp[(2022, "Georgia")]["rating"] == 35.3


def test_team_season_prior_row():
    directory = cfbd.CFBDTeamDirectory(_fixture("teams.json"), ESPN_IDS)
    observed = datetime(2023, 8, 26, 18, 30, tzinfo=UTC)
    portal, _ = cfbd.aggregate_portal(_fixture("portal_2023.json"), observed)
    inputs = cfbd.SeasonInputs(
        returning=cfbd.parse_returning(_fixture("returning_2023.json")),
        talent=cfbd.parse_talent(_fixture("talent.json")),
        recruiting=cfbd.parse_recruiting(_fixture("recruiting_teams.json")),
        sp=cfbd.parse_sp(_fixture("sp_2022.json")),
        polls=cfbd.parse_preseason_polls(_fixture("rankings_2023.json")),
        portal=portal,
    )
    rows = cfbd.build_team_season_priors(
        2023, observed, directory, inputs, fetched_at_utc="2026-11-21T19:41:15Z"
    )
    georgia = next(r for r in rows if r["cfbd_team"] == "Georgia")
    assert georgia["espn_team_id"] == "61" and georgia["cfbd_team_id"] == 61
    assert georgia["returning_off_ppa_pct"] == 0.43 and georgia["returning_def_pct"] is None
    assert georgia["talent"] == 977.87 and georgia["talent_prev_season"] == 989.76
    assert georgia["sp_prev_season_rating"] == 35.3  # 2022 final feeds 2023, never 2023's own table
    assert georgia["preseason_ap_rank"] == 1
    assert georgia["portal_out_count"] == 4 and georgia["portal_net"] == -2
    assert georgia["observed_at_utc"] == "2023-08-26T18:30:00Z"
    assert georgia["as_published_verified"] is False and georgia["in_season_fetch"] is False
    # ranked but in no table (LSU, CFBD id 99) still gets a row, keyed by id
    lsu = next(r for r in rows if r["cfbd_team_id"] == 99)
    assert lsu["preseason_ap_rank"] == 5 and lsu["espn_match_status"] == cfbd.NO_CALL_ESPN_ID_UNSEEN
