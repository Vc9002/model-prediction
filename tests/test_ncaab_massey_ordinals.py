"""Synthetic-fixture tests for the Kaggle Massey ordinals loader (no Kaggle download needed)."""

from __future__ import annotations

from datetime import date

import pytest

from model_prediction.data_sources.ncaab_massey_ordinals import (
    NO_CALL_NO_RANKING,
    NO_CALL_STALE,
    NO_CALL_TOO_FEW_SYSTEMS,
    MasseyOrdinals,
    build_kaggle_crosswalk,
    load_day_zero,
    load_kaggle_team_spellings,
)
from model_prediction.data_sources.ncaab_team_crosswalk import NO_CALL_COLLISION, EspnTeam


@pytest.fixture
def files(tmp_path):
    (tmp_path / "MSeasons.csv").write_text(
        "Season,DayZero,RegionW,RegionX,RegionY,RegionZ\n2025,11/04/2024,E,W,S,M\n2026,11/03/2025,E,W,S,M\n",
        encoding="utf-8",
    )
    rows = ["Season,RankingDayNum,SystemName,TeamID,OrdinalRank"]
    # 2026 DayZero 2025-11-03: day 9 = 11-12, day 16 = 11-19.
    for system, rank9, rank16 in (("POM", 5, 3), ("SAG", 7, 4), ("MAS", 6, 6), ("MOR", 9, 8), ("XYZ", 1, 1)):
        rows.append(f"2026,9,{system},1181,{rank9}")
        rows.append(f"2026,16,{system},1181,{rank16}")
    rows.append("2025,133,POM,1181,2")  # last season's final ranking
    (tmp_path / "MMasseyOrdinals.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return tmp_path


def test_day_zero_and_strictly_before_rank(files):
    assert load_day_zero(files / "MSeasons.csv")[2026] == date(2025, 11, 3)
    ords = MasseyOrdinals.from_csv(files / "MMasseyOrdinals.csv", files / "MSeasons.csv")
    assert "XYZ" not in ords.systems()  # filtered to the consensus list while streaming
    # Ranking dated 11-19 is not usable ON 11-19, only after.
    hit = ords.rank_as_of(1181, "POM", date(2025, 11, 19))
    assert (hit.rank, hit.ranking_date, hit.reason) == (5, date(2025, 11, 12), "OK")
    assert ords.rank_as_of(1181, "POM", date(2025, 11, 20)).rank == 3
    assert ords.rank_as_of(1181, "POM", date(2025, 11, 12)).reason == NO_CALL_STALE  # only last March's
    assert ords.rank_as_of(1181, "SAG", date(2025, 11, 12)).reason == NO_CALL_NO_RANKING
    assert ords.rank_as_of(9999, "POM", date(2025, 11, 20)).reason == NO_CALL_NO_RANKING


def test_consensus_median_and_min_systems(files):
    ords = MasseyOrdinals.from_csv(files / "MMasseyOrdinals.csv", files / "MSeasons.csv", seasons=[2026])
    hit = ords.consensus_as_of(1181, date(2025, 11, 20))
    assert hit.reason == "OK" and hit.median_rank == pytest.approx(5.0)  # median of 3,4,6,8
    assert ords.consensus_as_of(1181, date(2025, 11, 20), min_systems=5).reason == NO_CALL_TOO_FEW_SYSTEMS
    assert ords.consensus_as_of(1181, date(2025, 11, 12)).reason == NO_CALL_TOO_FEW_SYSTEMS


def test_bad_header_fails_closed(tmp_path):
    (tmp_path / "MSeasons.csv").write_text("Season,DayZero\n2026,11/03/2025\n", encoding="utf-8")
    (tmp_path / "MMasseyOrdinals.csv").write_text("Season,Day,System,Team,Rank\n", encoding="utf-8")
    with pytest.raises(ValueError):
        MasseyOrdinals.from_csv(tmp_path / "MMasseyOrdinals.csv", tmp_path / "MSeasons.csv")


def test_kaggle_crosswalk_traps(tmp_path):
    (tmp_path / "MTeamSpellings.csv").write_text(
        "TeamNameSpelling,TeamID\nconnecticut,1163\nuconn,1163\ncentral conn,1148\ncentral connecticut,1148\n"
        "miami fl,1274\nmiami (fl),1274\nmiami oh,1275\nmiami ohio,1275\nmiami,1999\n",
        encoding="utf-8",
    )
    espn = [
        EspnTeam("41", "UConn Huskies", "UConn", "UConn", "UConn"),
        EspnTeam(
            "2115", "Central Connecticut Blue Devils", "Central Connecticut", "C Connecticut", "C Connecticut"
        ),
        EspnTeam("2390", "Miami Hurricanes", "Miami", "Miami", "Miami"),
        EspnTeam("193", "Miami (OH) RedHawks", "Miami (OH)", "Miami OH", "Miami OH"),
    ]
    overrides = {"connecticut": "41", "central conn": "2115", "miami fl": "2390", "miami oh": "193"}
    xw = build_kaggle_crosswalk(load_kaggle_team_spellings(tmp_path / "MTeamSpellings.csv"), espn, overrides)
    assert xw["1163"].espn_team_id == "41"
    assert xw["1148"].espn_team_id == "2115"
    assert xw["1275"].espn_team_id == "193"
    # A bare "miami" team lands on Miami FL's location alias: the collision declines both.
    assert xw["1274"].reason == xw["1999"].reason == NO_CALL_COLLISION
