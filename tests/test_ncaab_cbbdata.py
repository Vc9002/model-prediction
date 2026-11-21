"""Network-free tests for the cbbdata client, Torvik as-of lookups and the NCAAB team crosswalk."""

from __future__ import annotations

import io
import json
import logging
from datetime import date

import httpx
import polars as pl
import pytest

from model_prediction.data_sources.ncaab_cbbdata import (
    NO_CALL_NO_SNAPSHOT,
    NO_CALL_STALE,
    NO_CALL_UNKNOWN_TEAM,
    CBBDataAuthError,
    CBBDataClient,
    CBBDataSchemaError,
    TorvikRatingsArchive,
    build_torvik_crosswalk,
    parse_ratings_archive,
)
from model_prediction.data_sources.ncaab_team_crosswalk import (
    NO_CALL_AMBIGUOUS,
    NO_CALL_COLLISION,
    NO_CALL_UNMATCHED,
    EspnTeam,
    TeamCrosswalk,
)

SECRET = "k" * 64

ESPN = [
    EspnTeam("41", "UConn Huskies", "UConn", "UConn", "UConn"),
    EspnTeam(
        "2115", "Central Connecticut Blue Devils", "Central Connecticut", "C Connecticut", "C Connecticut"
    ),
    EspnTeam("113", "Massachusetts Minutemen", "Massachusetts", "UMass", "UMass"),
    EspnTeam("2349", "UMass Lowell River Hawks", "UMass Lowell", "UMass Lowell", "UMass Lowell"),
    EspnTeam("2390", "Miami Hurricanes", "Miami", "Miami", "Miami"),
    EspnTeam("193", "Miami (OH) RedHawks", "Miami (OH)", "Miami OH", "Miami OH"),
    EspnTeam("30", "USC Trojans", "USC", "USC", "USC"),
    EspnTeam("2579", "South Carolina Gamecocks", "South Carolina", "South Carolina", "South Carolina"),
    EspnTeam("2599", "St. John's Red Storm", "St. John's", "St John's", "St John's"),
    EspnTeam("2608", "Saint Mary's Gaels", "Saint Mary's", "Saint Mary's", "Saint Mary's"),
    EspnTeam("2350", "Loyola Chicago Ramblers", "Loyola Chicago", "Loyola Chicago", "Loyola Chicago"),
    EspnTeam("2352", "Loyola Maryland Greyhounds", "Loyola Maryland", "Loyola MD", "Loyola MD"),
    EspnTeam("2351", "Loyola Marymount Lions", "Loyola Marymount", "LMU", "LMU"),
    EspnTeam("2026", "App State Mountaineers", "App State", "App State", "App State"),
]
_ESPN_IDS = {t.team_id for t in ESPN}


def _overrides():
    from model_prediction.data_sources.ncaab_team_crosswalk import DEFAULT_OVERRIDES

    return {k: v for k, v in DEFAULT_OVERRIDES.items() if v in _ESPN_IDS}


def _archive_parquet(rows: list[dict]) -> bytes:
    buf = io.BytesIO()
    pl.DataFrame(rows).write_parquet(buf)
    return buf.getvalue()


ARCHIVE_ROWS = [
    {"team": "Duke", "date": date(2025, 11, 10), "adj_o": 120.0, "adj_d": 95.0, "barthag": 0.9, "year": 2026},
    {
        "team": "Duke",
        "date": date(2025, 11, 11),
        "adj_o": 121.0,
        "adj_d": 94.0,
        "barthag": 0.91,
        "year": 2026,
    },
    {
        "team": "Duke",
        "date": date(2025, 11, 12),
        "adj_o": 125.0,
        "adj_d": 90.0,
        "barthag": 0.95,
        "year": 2026,
    },
]


def test_parse_archive_and_strictly_before_lookup():
    archive = TorvikRatingsArchive(parse_ratings_archive(_archive_parquet(ARCHIVE_ROWS)))
    hit = archive.as_of("Duke", date(2025, 11, 12))
    # The 11-12 snapshot may already contain the 11-12 game: only 11-11 is servable.
    assert hit.reason == "OK" and hit.snapshot is not None
    assert hit.snapshot.snapshot_date == date(2025, 11, 11)
    assert hit.snapshot.adj_em == pytest.approx(27.0)
    assert archive.as_of("Duke", date(2025, 11, 10)).reason == NO_CALL_NO_SNAPSHOT
    assert archive.as_of("Duke", date(2026, 2, 1)).reason == NO_CALL_STALE
    assert archive.as_of("Nobody", date(2025, 11, 12)).reason == NO_CALL_UNKNOWN_TEAM


def test_archive_accepts_string_dates_and_rejects_missing_columns():
    rows = [{"team": "Duke", "date": "20251110", "adj_o": 1.0, "adj_d": 2.0}]
    assert parse_ratings_archive(_archive_parquet(rows))[0].snapshot_date == date(2025, 11, 10)
    with pytest.raises(CBBDataSchemaError):
        parse_ratings_archive(_archive_parquet([{"team": "Duke", "date": "2025-11-10", "adj_o": 1.0}]))
    with pytest.raises(CBBDataSchemaError):
        parse_ratings_archive(b'{"message":"nope"}')


def test_client_fails_closed_without_key():
    with pytest.raises(CBBDataAuthError):
        CBBDataClient(environ={})
    keyless = CBBDataClient(
        environ={},
        require_key=False,
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
    )
    with pytest.raises(CBBDataAuthError):
        keyless.torvik_ratings_archive(2026)


def test_client_caches_with_provenance_and_never_writes_key(tmp_path, caplog):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.params["key"] == SECRET
        return httpx.Response(200, content=_archive_parquet(ARCHIVE_ROWS))

    client = CBBDataClient(
        cache_root=tmp_path,
        environ={"CBBDATA_API_KEY": SECRET},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with caplog.at_level(logging.INFO, logger="httpx"):
        first = client.torvik_ratings_archive(2026)
        second = client.torvik_ratings_archive(2026)
    assert len(calls) == 1 and len(first) == len(second) == 3
    files = list(tmp_path.rglob("*"))
    metas = [p for p in files if p.name.endswith(".meta.json")]
    assert len(metas) == 1
    meta = json.loads(metas[0].read_text(encoding="utf-8"))
    assert meta["fetched_at_utc"] and meta["sha256"] and meta["params"] == {"year": 2026}
    for path in files:
        assert SECRET not in path.name
        if path.is_file():
            assert SECRET.encode() not in path.read_bytes()
    assert SECRET not in caplog.text


def test_client_reports_rejected_key_without_leaking_it(tmp_path):
    client = CBBDataClient(
        api_key=SECRET,
        cache_root=tmp_path,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(403, json={"message": "bad"}))
        ),
    )
    with pytest.raises(CBBDataAuthError) as info:
        client.torvik_ratings_archive(2026)
    assert SECRET not in str(info.value)
    assert not list(tmp_path.rglob("*.parquet"))


def test_torvik_crosswalk_by_espn_id_with_rename_and_collision():
    rows = [
        {"torvik_team": "Connecticut", "espn_id": "41", "espn_display": "UConn Huskies"},
        {
            "torvik_team": "Central Connecticut",
            "espn_id": "2115",
            "espn_display": "Central Connecticut Blue Devils",
        },
        {
            "torvik_team": "Appalachian St.",
            "espn_id": "2026",
            "espn_display": "Appalachian State Mountaineers",
        },
        {"torvik_team": "Miami FL", "espn_id": "193", "espn_display": "Miami Hurricanes"},  # re-pointed id
        {"torvik_team": "USC", "espn_id": "30", "espn_display": "USC Trojans"},
        {"torvik_team": "Southern Cal", "espn_id": "30", "espn_display": "USC Trojans"},
        {"torvik_team": "Nowhere", "espn_id": None, "espn_display": None},
    ]
    xw = build_torvik_crosswalk(rows, ESPN)
    assert xw["Connecticut"].espn_team_id == "41"
    assert xw["Central Connecticut"].espn_team_id == "2115"
    assert xw["Appalachian St."].espn_team_id == "2026"
    assert xw["Miami FL"].espn_team_id is None
    assert xw["USC"].reason == xw["Southern Cal"].reason == NO_CALL_COLLISION
    assert xw["Nowhere"].reason == NO_CALL_UNMATCHED


@pytest.mark.parametrize(
    ("spellings", "expected"),
    [
        (["connecticut", "uconn"], "41"),
        (["central connecticut", "central conn", "ccsu"], "2115"),
        (["massachusetts", "umass"], "113"),
        (["umass lowell", "mass lowell"], "2349"),
        (["miami fl", "miami (fl)"], "2390"),
        (["miami oh", "miami (oh)", "miami ohio"], "193"),
        (["southern california", "usc"], "30"),
        (["south carolina"], "2579"),
        (["st john's", "st. john's"], "2599"),
        (["saint mary's", "st mary's ca"], "2608"),
        (["loyola chicago", "loyola-chicago"], "2350"),
        (["loyola md"], "2352"),
        (["loyola marymount", "lmu"], "2351"),
    ],
)
def test_name_crosswalk_resolves_collision_traps(spellings, expected):
    assert TeamCrosswalk(ESPN, _overrides()).resolve(spellings).espn_team_id == expected


def test_name_crosswalk_declines_ambiguous_unmatched_and_collisions():
    xw = TeamCrosswalk(ESPN, _overrides())
    # "miami" alone hits Miami FL's location, "miami oh" hits Miami OH: the source team is ambiguous.
    assert xw.resolve(["miami", "miami oh"]).reason == NO_CALL_AMBIGUOUS
    assert xw.resolve(["loyola"]).reason == NO_CALL_UNMATCHED  # never guessed from a prefix
    assert xw.resolve(["st marys"]).reason == NO_CALL_UNMATCHED  # Saint Mary's or Mount St. Mary's
    mapping = xw.build_mapping({"1": ["uconn"], "2": ["connecticut"], "3": ["usc"]})
    assert mapping["1"].reason == mapping["2"].reason == NO_CALL_COLLISION
    assert mapping["3"].espn_team_id == "30"


def test_overrides_must_point_at_known_espn_ids():
    with pytest.raises(ValueError):
        TeamCrosswalk(ESPN, {"somewhere": "999999"})
