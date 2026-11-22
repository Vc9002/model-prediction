"""NCAAF settlement fixes from the 2026-11-22 settlement audit (outputs/ncaaf/bug_hunt/settlement.md).

1. Spread closes: the contract's anchor team ("NM State", "UMass", "San Jose State", "Appalachian
   State") is resolved by NCAAF identity and the event title's first team, not the strict raw-name
   match that left those rows without a close.
2. Moneyline closes: "Eagles" (Georgia Southern) and "Fightin' Blue Hens" (Delaware) outcomes are
   tied to the row by title position.
3. Contract-resolution settlement maps NCAAF slugs the same way the ESPN path does.
4. Synthetic resolution scores never grade a losing whole-number spread as a push.
5. ESPN name fallback (NCAAF): identity, never shared mascots.
6. ESPN name fallback (all sports): no mascot-only match, and two different matching games fail
   closed (NCAAB 53c03c58cbaa4a05, 2026-11-18).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from model_prediction.cli import settle as settle_module
from model_prediction.domain import MarketType, PickResult
from model_prediction.pricing import grade_pick

KICKOFF = "2026-11-21T21:00:00Z"
DECISION = "2026-11-21T19:03:36Z"


def _write_snapshots(root: Path, records: list[dict]) -> None:
    folder = root / "odds" / "ncaaf" / "2026-11-21"
    folder.mkdir(parents=True, exist_ok=True)
    folder.joinpath("polymarket_snapshots.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )


def _snapshot(
    slug: str, title: str, team: str, line, long: tuple[str, float], short: tuple[str, float]
) -> dict:
    return {
        "market_slug": slug,
        "market_type": "spread" if line is not None else "moneyline",
        "event_title": title,
        "team": team,
        "line": line,
        "observed_at_utc": "2026-11-21T20:55:04Z",
        "event_start_utc": KICKOFF,
        "market_state": "MARKET_STATE_OPEN",
        "long": {"description": long[0], "ask": long[1]},
        "short": {"description": short[0], "ask": short[1]},
    }


def _close(root: Path, slug: str, home: str, away: str, selection: str, line=None) -> float | None:
    probability, _ = settle_module._closing_probability_for_moneyline_pick(
        root, "ncaaf", slug, KICKOFF, home, away, selection, line=line, not_before_utc=DECISION
    )
    return probability


# ---------------------------------------------------------------- 1. spread close anchors


@pytest.mark.parametrize(
    ("title", "team", "line", "long", "short", "home", "away", "selection", "row_line", "expected"),
    [
        # e7572dd7ee2f4850: anchor "UMass" (+14.5); row UConn -14.5 is the short side.
        (
            "UMass vs. UConn",
            "UMass",
            14.5,
            ("+14.50", 0.5),
            ("-14.50", 0.455),
            "UConn Huskies",
            "Massachusetts Minutemen",
            "home",
            -14.5,
            0.455,
        ),
        # 46e5e2d2a4604e8a: "San Jose State" vs the ledger's "San José State Spartans".
        (
            "San Jose State vs. Nevada",
            "San Jose State",
            6.5,
            ("+6.50", 0.53),
            ("-6.50", 0.475),
            "Nevada Wolf Pack",
            "San José State Spartans",
            "away",
            6.5,
            0.53,
        ),
        # de52a88894054878: "Appalachian State" vs the ledger's "App State Mountaineers".
        (
            "Appalachian State vs. Marshall",
            "Appalachian State",
            -1.5,
            ("-1.50", 0.47),
            ("+1.50", 0.53),
            "Marshall Thundering Herd",
            "App State Mountaineers",
            "home",
            1.5,
            0.53,
        ),
    ],
)
def test_ncaaf_spread_close_resolves_the_anchor_by_identity(
    tmp_path, title, team, line, long, short, home, away, selection, row_line, expected
):
    slug = "asc-cfb-fixture-2026-11-21"
    _write_snapshots(tmp_path, [_snapshot(slug, title, team, line, long, short)])
    assert _close(tmp_path, slug, home, away, selection, row_line) == expected


def test_ncaaf_spread_close_on_another_games_contract_stays_blank(tmp_path):
    # 8321c28ba0ca456e: a Purdue @ Penn State row carrying the Penn vs. Brown contract.
    slug = "asc-cfb-penn-brwn-2026-10-31-pos-1pt5"
    _write_snapshots(
        tmp_path, [_snapshot(slug, "Penn vs. Brown", "Penn", 1.5, ("+1.50", 0.5), ("-1.50", 0.5))]
    )
    assert _close(tmp_path, slug, "Penn State Nittany Lions", "Purdue Boilermakers", "home", 1.5) is None


def test_ncaaf_spread_close_is_blank_when_the_row_line_belongs_to_the_other_rung(tmp_path):
    # 881b2c77229146ee: row "NM State +3.5", but the contract's NM State rung is -3.5 and the row's
    # entry is the "+3.50" (Sam Houston) ask. Neither side is the row's bet, so no close.
    slug = "asc-cfb-nmxst-smho-2026-10-21-neg-3pt5"
    _write_snapshots(
        tmp_path,
        [_snapshot(slug, "NM State vs. Sam Houston", "NM State", -3.5, ("-3.50", 0.26), ("+3.50", 0.745))],
    )
    assert _close(tmp_path, slug, "Sam Houston Bearkats", "New Mexico State Aggies", "away", 3.5) is None
    # The same contract does close the row it really belongs to (NM State -3.5).
    assert _close(tmp_path, slug, "Sam Houston Bearkats", "New Mexico State Aggies", "away", -3.5) == 0.26


# ---------------------------------------------------------------- 2. moneyline close nicknames


def test_ncaaf_moneyline_close_ties_directional_nickname_by_title(tmp_path):
    # 11d9a44b00e64ee1: "Southern" before "Eagles" made the strict match reject the long side.
    slug = "aec-cfb-gas-troy-2026-11-21"
    _write_snapshots(
        tmp_path,
        [
            _snapshot(
                slug,
                "Georgia Southern vs. Troy",
                "Georgia Southern",
                None,
                ("Eagles", 0.745),
                ("Trojans", 0.26),
            )
        ],
    )
    assert _close(tmp_path, slug, "Troy Trojans", "Georgia Southern Eagles", "away") == 0.745
    assert _close(tmp_path, slug, "Troy Trojans", "Georgia Southern Eagles", "home") == 0.26


def test_ncaaf_moneyline_close_ties_variant_nickname_by_title(tmp_path):
    # 18c67c5ffe454a55: Polymarket "Fightin' Blue Hens" vs ledger "Delaware Blue Hens".
    slug = "aec-cfb-msrst-del-2026-11-21"
    _write_snapshots(
        tmp_path,
        [
            _snapshot(
                slug,
                "Missouri State vs. Delaware",
                "Missouri State",
                None,
                ("Bears", 0.43),
                ("Fightin' Blue Hens", 0.575),
            )
        ],
    )
    assert _close(tmp_path, slug, "Delaware Blue Hens", "Missouri State Bears", "home") == 0.575


def test_ncaaf_moneyline_close_blank_when_nickname_contradicts_title(tmp_path):
    slug = "aec-cfb-gas-troy-2026-11-21"
    # Title says Georgia Southern is the long side, but the long outcome is named "Trojans".
    _write_snapshots(
        tmp_path,
        [_snapshot(slug, "Georgia Southern vs. Troy", "", None, ("Troy Trojans", 0.3), ("Eagles", 0.7))],
    )
    assert _close(tmp_path, slug, "Troy Trojans", "Georgia Southern Eagles", "home") is None


# ---------------------------------------------------------------- 3. resolution path slug mapping


class _FakeResolutionClient:
    def __init__(self, sides: list[dict]) -> None:
        self.sides = sides
        self.slugs: list[str] = []

    def market(self, slug: str) -> dict:
        self.slugs.append(slug)
        return {"status": "MARKET_STATUS_RESOLVED", "marketSides": self.sides}

    def book(self, slug: str) -> dict:
        return {"state": "MARKET_STATE_EXPIRED"}


class _FakeLedger:
    def __init__(self) -> None:
        self.settled: list[tuple] = []

    def settle(self, pick_id, away, home, closing_line, closing_odds, **kwargs):
        self.settled.append((pick_id, away, home))
        return {"result": "win"}

    def void(self, pick_id, reason):
        raise AssertionError("must not void")


def _resolution_row(rationale: str, **extra) -> dict:
    return {
        "pick_id": "p-ncaaf",
        "league": "NCAAF",
        "market_type": "moneyline",
        "selection": "away",
        "line": None,
        "home_team": "Troy Trojans",
        "away_team": "Georgia Southern Eagles",
        "event_start_utc": KICKOFF,
        "rationale": rationale,
        **extra,
    }


_GASO_SIDES = [
    {"description": "Georgia Southern", "team": {"name": "Georgia Southern"}, "price": "1"},
    {"description": "Troy", "team": {"name": "Troy"}, "price": "0"},
]


@pytest.mark.parametrize(
    "row_extra",
    [
        # v6.2: the shared extractor returns "anchor-blended".
        {"rationale": "challenger (anchor-blended): fixture (aec-cfb-gas-troy-2026-11-21)"},
        # Moneyline ledger slug carries the side suffix.
        {"rationale": "fixture", "market_slug": "aec-cfb-gas-troy-2026-11-21-away"},
    ],
)
def test_ncaaf_resolution_settles_on_the_contract_slug(tmp_path, row_extra):
    rationale = row_extra.pop("rationale")
    client = _FakeResolutionClient(_GASO_SIDES)
    ledger = _FakeLedger()
    result = settle_module._settle_by_resolution(
        _resolution_row(rationale, **row_extra), ledger, client=client, data_root=tmp_path
    )
    assert client.slugs == ["aec-cfb-gas-troy-2026-11-21"]
    assert result is not None and result.get("settled") is True
    assert ledger.settled == [("p-ncaaf", 1, 0)]  # away won


def test_ncaaf_resolution_declines_when_both_outcomes_match_the_team(tmp_path):
    sides = [
        {"description": "Wildcats", "team": {"name": "Wildcats"}, "price": "1"},
        {"description": "Wildcats", "team": {"name": "Wildcats"}, "price": "0"},
    ]
    client = _FakeResolutionClient(sides)
    ledger = _FakeLedger()
    row = _resolution_row(
        "fixture (aec-cfb-ksst-ariz-2026-11-21)",
        home_team="Kansas State Wildcats",
        away_team="Arizona Wildcats",
    )
    result = settle_module._settle_by_resolution(row, ledger, client=client, data_root=tmp_path)
    assert result is not None and "ambiguous" in result["reason"]
    assert ledger.settled == []


# ---------------------------------------------------------------- 4. synthetic resolution scores


@pytest.mark.parametrize("line", [-7.0, -6.5, -3.0, 0.0, 3.0, 3.5, 14.0])
@pytest.mark.parametrize("selection", ["home", "away"])
@pytest.mark.parametrize("won", [True, False])
def test_resolution_scores_grade_spreads_as_the_contract_paid(line, selection, won):
    away, home = settle_module._scores_for_outcome("spread", selection, line, won)
    expected = PickResult.WIN if won else PickResult.LOSS
    assert grade_pick(MarketType.SPREAD, selection, line, away, home) is expected


@pytest.mark.parametrize("line", [44.0, 44.5])
@pytest.mark.parametrize("selection", ["over", "under"])
@pytest.mark.parametrize("won", [True, False])
def test_resolution_scores_grade_totals_as_the_contract_paid(line, selection, won):
    away, home = settle_module._scores_for_outcome("total", selection, line, won)
    expected = PickResult.WIN if won else PickResult.LOSS
    assert grade_pick(MarketType.TOTAL, selection, line, away, home) is expected


# ---------------------------------------------------------------- 5/6. ESPN name fallback


class _FakeESPN:
    def __init__(self, events: list[dict]) -> None:
        self.payload = {"events": events}

    def scoreboard(self, league: str, game_day: str) -> dict:
        return self.payload


def _team(team_id: str, display: str, short: str, location: str, name: str) -> dict:
    return {
        "id": team_id,
        "displayName": display,
        "shortDisplayName": short,
        "location": location,
        "name": name,
    }


def _event(event_id: str, date: str, away: tuple[dict, int], home: tuple[dict, int]) -> dict:
    return {
        "id": event_id,
        "date": date,
        "competitions": [
            {
                "date": date,
                "status": {"type": {"name": "STATUS_FINAL", "completed": True}},
                "competitors": [
                    {"homeAway": "away", "score": str(away[1]), "team": away[0]},
                    {"homeAway": "home", "score": str(home[1]), "team": home[0]},
                ],
            }
        ],
    }


def _espn_row(league: str, away: str, home: str, start: str = KICKOFF) -> dict:
    return {
        "pick_id": f"p-{league}",
        "league": league,
        "away_team": away,
        "original_away_team": away,
        "home_team": home,
        "original_home_team": home,
        "event_id": "polymarket-event-id-not-on-espn",
        "event_start_utc": start,
    }


KSU = _team("2306", "Kansas State Wildcats", "Kansas State", "Kansas State", "Wildcats")
ARIZ = _team("12", "Arizona Wildcats", "Arizona", "Arizona", "Wildcats")
ISU = _team("66", "Iowa State Cyclones", "Iowa State", "Iowa State", "Cyclones")
IOWA = _team("2294", "Iowa Hawkeyes", "Iowa", "Iowa", "Hawkeyes")
UGA = _team("61", "Georgia Bulldogs", "Georgia", "Georgia", "Bulldogs")
MSST = _team("344", "Mississippi State Bulldogs", "Mississippi St", "Mississippi State", "Bulldogs")
AUB = _team("2", "Auburn Tigers", "Auburn", "Auburn", "Tigers")
LSU = _team("99", "LSU Tigers", "LSU", "LSU", "Tigers")
PSU = _team("213", "Penn State Nittany Lions", "Penn State", "Penn State", "Nittany Lions")
PENN = _team("219", "Penn Quakers", "Penn", "Penn", "Quakers")
PUR = _team("2509", "Purdue Boilermakers", "Purdue", "Purdue", "Boilermakers")
MIA = _team("2390", "Miami Hurricanes", "Miami", "Miami", "Hurricanes")
OHIO = _team("195", "Ohio Bobcats", "Ohio", "Ohio", "Bobcats")


@pytest.mark.parametrize(
    ("row_away", "row_home", "espn_away", "espn_home"),
    [
        ("Iowa State Cyclones", "Kansas State Wildcats", IOWA, ARIZ),
        ("Mississippi State Bulldogs", "LSU Tigers", UGA, AUB),
        ("Purdue Boilermakers", "Penn State Nittany Lions", PUR, PENN),
        # Phrase-level collision only identity catches: "Miami (OH)" contains "Miami".
        ("Miami (OH) RedHawks", "Ohio Bobcats", MIA, OHIO),
    ],
)
def test_ncaaf_name_fallback_never_pairs_namesakes(row_away, row_home, espn_away, espn_home):
    espn = _FakeESPN([_event("e1", KICKOFF, (espn_away, 10), (espn_home, 20))])
    row = _espn_row("NCAAF", row_away, row_home)
    assert settle_module._find_espn_result(espn, ("NCAAF",), "2026-11-21", row) is None


def test_ncaaf_name_fallback_matches_by_identity_and_exact_fcs_name():
    app = _team("2026", "Appalachian State Mountaineers", "App State", "Appalachian State", "Mountaineers")
    marshall = _team("276", "Marshall Thundering Herd", "Marshall", "Marshall", "Thundering Herd")
    mostate = _team("2623", "Missouri State Bears", "Missouri St", "Missouri State", "Bears")
    delaware = _team("48", "Delaware Blue Hens", "Delaware", "Delaware", "Blue Hens")
    espn = _FakeESPN(
        [
            _event("e1", KICKOFF, (app, 31), (marshall, 17)),
            _event("e2", KICKOFF, (mostate, 38), (delaware, 61)),
        ]
    )
    res = settle_module._find_espn_result(
        espn,
        ("NCAAF",),
        "2026-11-21",
        _espn_row("NCAAF", "App State Mountaineers", "Marshall Thundering Herd"),
    )
    assert res is not None and (res["away_score"], res["home_score"]) == (31, 17)
    res = settle_module._find_espn_result(
        espn, ("NCAAF",), "2026-11-21", _espn_row("NCAAF", "Missouri State Bears", "Delaware Blue Hens")
    )
    assert res is not None and (res["away_score"], res["home_score"]) == (38, 61)


# ESPN's own competitor records for 2026-11-18 (NCAAB scoreboard, groups=50).
SAMFORD = _team("2535", "Samford Bulldogs", "Samford", "Samford", "Bulldogs")
SC_UPSTATE = _team(
    "2908", "South Carolina Upstate Spartans", "SC Upstate", "South Carolina Upstate", "Spartans"
)
UNCG = _team("2430", "UNC Greensboro Spartans", "UNC Greensboro", "UNC Greensboro", "Spartans")
GWEBB = _team("2241", "Gardner-Webb Runnin' Bulldogs", "Gardner-Webb", "Gardner-Webb", "Runnin' Bulldogs")


def test_ncaab_name_fallback_does_not_settle_on_a_shared_mascot_game():
    """53c03c58cbaa4a05: Samford @ SC Upstate (real final 74-64) was graded 78-72 off UNCG @ Gardner-Webb."""
    espn = _FakeESPN(
        [
            _event("401911393", "2026-11-19T01:00Z", (UNCG, 78), (GWEBB, 72)),
            _event("401922283", "2026-11-19T01:30Z", (SAMFORD, 74), (SC_UPSTATE, 64)),
        ]
    )
    row = _espn_row("NCAAB", "Samford Bulldogs", "South Carolina Upstate Spartans", "2026-11-18T23:30:00Z")
    res = settle_module._find_espn_result(espn, ("NCAAB",), "2026-11-18", row)
    assert res is not None
    assert (res["away_score"], res["home_score"]) == (74, 64)


def test_ncaab_name_fallback_rejects_a_mascot_only_match():
    espn = _FakeESPN([_event("401911393", "2026-11-19T01:00Z", (UNCG, 78), (GWEBB, 72))])
    row = _espn_row("NCAAB", "Samford Bulldogs", "South Carolina Upstate Spartans", "2026-11-18T23:30:00Z")
    assert settle_module._find_espn_result(espn, ("NCAAB",), "2026-11-18", row) is None


def test_name_fallback_fails_closed_when_two_different_games_match():
    first = (_team("1", "Alpha", "Alpha", "Alpha", "A"), _team("2", "Beta", "Beta", "Beta", "B"))
    second = (_team("3", "Alpha", "Alpha", "Alpha", "A"), _team("4", "Beta", "Beta", "Beta", "B"))
    espn = _FakeESPN(
        [
            _event("g1", "2026-11-19T01:00Z", (first[0], 1), (first[1], 2)),
            _event("g2", "2026-11-19T01:30Z", (second[0], 3), (second[1], 4)),
        ]
    )
    row = _espn_row("NCAAB", "Alpha", "Beta", "2026-11-19T01:00:00Z")
    assert settle_module._find_espn_result(espn, ("NCAAB",), "2026-11-18", row) is None
