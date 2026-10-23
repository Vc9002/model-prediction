"""Wikipedia-sourced darts history: bracket parsing, caching, and name resolution."""

from __future__ import annotations

from model_prediction.darts_forward import _resolve_player
from model_prediction.darts_history import load_darts_history, refresh_darts_history
from model_prediction.data_sources.wikipedia_darts import parse_bracket_matches
from model_prediction.models.darts import DartsModel

BRACKET = """
{{16TeamBracket
| RD1='''First round'''<br>''(best of 11 legs)''<br>23 May
| RD2='''Final'''<br>''(best of 15 legs)''<br>25 May
| RD1-team01 = {{PDCFlag|Cameron Menzies|avg=93.39}}
| RD1-score01=6
| RD1-team02 = {{PDCFlag|Wesley Plaisier|avg=93.16}}
| RD1-score02=5
| RD1-team03 = [[Ryan Joyce]]
| RD1-score03= w/o
| RD1-team04 = {{PDCFlag|Jeffrey de Zwaan}}
| RD1-score04=
| RD2-team01 = {{PDCFlag|Cameron Menzies}}
| RD2-score01=8 <!-- final -->
| RD2-team02 = [[Jonny Clayton (darts player)|Jonny Clayton]]
| RD2-score02=10
}}
"""

SET_PLAY = """
| RD1='''First round'''<br>''(best of 5 sets)''<br>15 December
| RD1-team01 = {{PDCFlag|Luke Littler}}
| RD1-score01=3
| RD1-team02 = {{PDCFlag|Ryan Meikle}}
| RD1-score02=1
"""


def test_parse_bracket_matches_reads_legs_dates_and_skips_walkovers():
    matches = parse_bracket_matches(BRACKET, "2025 Test Open", 2025)
    assert [
        (m["player_a"], m["player_b"], m["legs_a"], m["legs_b"], m["winner"], m["match_date"])
        for m in matches
    ] == [
        ("Cameron Menzies", "Wesley Plaisier", 6, 5, "Cameron Menzies", "2025-05-23"),
        ("Cameron Menzies", "Jonny Clayton", 8, 10, "Jonny Clayton", "2025-05-25"),
    ]
    assert len({m["match_id"] for m in matches}) == 2


def test_parse_bracket_matches_skips_set_play():
    assert parse_bracket_matches(SET_PLAY, "2025 PDC World Darts Championship", 2025) == []


class FakeClient:
    def __init__(self) -> None:
        self.years: list[int] = []

    def year_matches(self, year: int) -> list[dict]:
        self.years.append(year)
        return parse_bracket_matches(BRACKET, f"{year} Test Open", year)


def test_refresh_bootstraps_then_only_current_season_and_dedupes(tmp_path):
    client = FakeClient()
    first = refresh_darts_history(tmp_path, "2026-10-21", client=client)
    assert client.years == [2024, 2025, 2026]
    assert first["matches"] == 6 and first["added"] == 6

    client.years.clear()
    second = refresh_darts_history(tmp_path, "2026-10-22", client=client)
    assert client.years == [2026]
    assert second["added"] == 0 and second["matches"] == 6

    history = load_darts_history(tmp_path)
    assert len(history) == 6
    model = DartsModel()
    model.fit(history)
    assert model.match_counts["Cameron Menzies"] == 6
    assert model.get_rating("Jonny Clayton") > model.get_rating("Wesley Plaisier")


def test_resolve_player_maps_market_names_onto_rated_names():
    known = ["Jonny Clayton", "Luke Humphries", "Luke Littler", "José de Sousa"]
    assert _resolve_player("Jonny Clayton", known) == "Jonny Clayton"
    assert _resolve_player("Jose de Sousa", known) == "José de Sousa"
    assert _resolve_player("Unknown Player", known) == "Unknown Player"
    # Two rated players share initial + surname-token logic only if both match: stay unresolved
    assert _resolve_player("L. Smith", known) == "L. Smith"


def test_load_history_merges_spelling_variants(tmp_path):
    import json

    from model_prediction.darts_history import history_path

    path = history_path(tmp_path)
    path.parent.mkdir(parents=True)
    rows = [
        {
            "match_id": "1",
            "match_date": "2025-01-01",
            "league": "DARTS_PDC",
            "player_a": "Dirk van Duijvenbode",
            "player_b": "Jose de Sousa",
            "legs_a": 6,
            "legs_b": 3,
            "winner": "Dirk van Duijvenbode",
        },
        {
            "match_id": "2",
            "match_date": "2025-01-02",
            "league": "DARTS_PDC",
            "player_a": "Dirk van Duijvenbode",
            "player_b": "Ryan Joyce",
            "legs_a": 6,
            "legs_b": 4,
            "winner": "Dirk van Duijvenbode",
        },
        {
            "match_id": "3",
            "match_date": "2025-01-03",
            "league": "DARTS_PDC",
            "player_a": "Dirk Van Duijvenbode",
            "player_b": "José de Sousa",
            "legs_a": 2,
            "legs_b": 6,
            "winner": "José de Sousa",
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    history = load_darts_history(tmp_path)
    assert {r.player_a for r in history} == {"Dirk van Duijvenbode"}
    assert history[2].winner == history[0].player_b


class _StubLedger:
    def __init__(self) -> None:
        self.settled: list[tuple] = []

    def settle(self, pick_id, away, home, line, odds, closing_raw_probability=None):
        self.settled.append((pick_id, away, home))
        return {"result": "win" if home > away else "loss"}


def _darts_history_file(tmp_path, rows):
    import json

    from model_prediction.darts_history import history_path

    path = history_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")


def _match(match_id, date, a, b, legs_a, legs_b):
    return {
        "match_id": match_id,
        "match_date": date,
        "league": "DARTS_PDC",
        "player_a": a,
        "player_b": b,
        "legs_a": legs_a,
        "legs_b": legs_b,
        "winner": a if legs_a > legs_b else b,
    }


def _pick(**overrides):
    row = {
        "pick_id": "p1",
        "league": "DARTS_PDC",
        "event_start_utc": "2026-10-22T17:00:00+00:00",
        "home_team": "Luke Woodhouse",
        "away_team": "Dirk van Duijvenbode",
        "rationale": "",
    }
    return row | overrides


def test_settle_darts_pick_grades_from_history_with_either_player_order(tmp_path):
    from model_prediction.cli.settle import _settle_darts_pick

    _darts_history_file(
        tmp_path, [_match("m1", "2026-10-22", "Dirk van Duijvenbode", "Luke Woodhouse", 4, 6)]
    )
    ledger = _StubLedger()
    result = _settle_darts_pick(_pick(), ledger, data_root=tmp_path)
    assert result == {"pick_id": "p1", "settled": True, "result": "win"}
    assert ledger.settled == [("p1", 4, 6)]  # away legs, home legs


def test_settle_darts_pick_stays_pending_without_a_nearby_result(tmp_path):
    from model_prediction.cli.settle import _settle_darts_pick

    ledger = _StubLedger()
    assert _settle_darts_pick(_pick(), ledger, data_root=tmp_path) is None
    _darts_history_file(
        tmp_path,
        [
            _match("old", "2026-10-01", "Luke Woodhouse", "Dirk van Duijvenbode", 6, 2),
            _match("other", "2026-10-22", "Luke Humphries", "Luke Littler", 6, 5),
        ],
    )
    assert _settle_darts_pick(_pick(), ledger, data_root=tmp_path) is None
    assert ledger.settled == []
