"""EuroLeague lane: club-code identity, history refresh, forecast and settlement."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from model_prediction.data_sources.euroleague import (
    club_names,
    normalize_game,
    refresh_euroleague_history,
    resolve_club,
)
from model_prediction.euroleague_forward import _main_line_markets, forecast_euroleague_sport
from model_prediction.main_ledgers import MultiSportPickLedger


def _api_game(code, home, away, hs, as_, when, played=True, hcode="BAS", acode="MAD"):
    return {
        "identifier": f"E2026_{code}",
        "played": played,
        "utcDate": when,
        "phaseType": {"code": "RS"},
        "season": {"year": 2026},
        "isNeutralVenue": False,
        "local": {"club": {"code": hcode, "name": home}, "score": hs},
        "road": {"club": {"code": acode, "name": away}, "score": as_},
    }


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.years = []

    def season_games(self, year):
        self.years.append(year)
        return [g for g in map(normalize_game, self.rows) if g]


def test_normalize_skips_unplayed_and_zero_scores():
    assert normalize_game(_api_game(1, "A", "B", 80, 70, "2026-10-01T18:00:00Z", played=False)) is None
    assert normalize_game(_api_game(2, "A", "B", 0, 0, "2026-10-01T18:00:00Z")) is None
    row = normalize_game(_api_game(3, "A", "B", 80, 70, "2026-10-01T18:00:00Z"))
    assert row["event_id"] == "euroleague:E2026_3" and row["home_code"] == "BAS"


def test_refresh_merges_sponsor_renames_by_club_code_and_is_idempotent(tmp_path):
    rows = [
        _api_game(1, "Cazoo Baskonia", "Real Madrid", 80, 90, "2025-10-01T18:00:00Z"),
        _api_game(2, "Kosner Baskonia", "Real Madrid", 85, 80, "2026-10-01T18:00:00Z"),
    ]
    client = FakeClient(rows)
    first = refresh_euroleague_history(tmp_path, "2026-10-21", client=client)
    assert client.years == [2022, 2023, 2024, 2025, 2026] and first["added"] == 2
    assert club_names(tmp_path)["BAS"] == "Kosner Baskonia"
    stored = [
        json.loads(line) for line in (tmp_path / "processed/euroleague/games.jsonl").read_text().splitlines()
    ]
    assert {r["home_team"] for r in stored} == {"Kosner Baskonia"}
    client.years.clear()
    second = refresh_euroleague_history(tmp_path, "2026-10-22", client=client)
    assert client.years == [2026] and second["added"] == 0


def test_resolve_club_is_exact_and_fails_closed():
    names = {"IST": "Anadolu Efes Istanbul", "ULK": "Fenerbahce Tarfin Istanbul"}
    assert resolve_club("Anadolu Efes SK", names) == "Anadolu Efes Istanbul"
    assert resolve_club("Fenerbahce Istanbul", names) == "Fenerbahce Tarfin Istanbul"
    assert resolve_club("Unknown Istanbul", names) is None
    assert resolve_club("Real Madrid", names) is None  # known alias, no history for the club


def test_main_line_markets_keeps_one_balanced_line_per_kind():
    def market(kind, line, price):
        return {
            "market_type": kind,
            "line": line,
            "sides": [{"price_probability": price}, {"price_probability": 1 - price}],
        }

    kept = _main_line_markets(
        [
            market("moneyline", None, 0.6),
            market("total", 150.5, 0.9),
            market("total", 160.5, 0.52),
            market("spread", 3.5, 0.55),
        ]
    )
    assert [(m["market_type"], m["line"]) for m in kept] == [
        ("moneyline", None),
        ("spread", 3.5),
        ("total", 160.5),
    ]


def test_forecast_logs_flat_only_and_dedupes(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_MIRROR", "0")
    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_AUTHORITY", "xlsx")
    now = datetime.now(UTC)
    rows = [
        _api_game(
            i,
            "Kosner Baskonia",
            "Real Madrid",
            78 + i % 5,
            82 + i % 4,
            (now - timedelta(days=i + 1)).isoformat(),
        )
        if i % 2 == 0
        else _api_game(
            i,
            "Real Madrid",
            "Kosner Baskonia",
            84 + i % 3,
            79 + i % 5,
            (now - timedelta(days=i + 1)).isoformat(),
            hcode="MAD",
            acode="BAS",
        )
        for i in range(1, 21)
    ]
    client = FakeClient(rows)
    start = (now + timedelta(days=1)).isoformat()
    event = {
        "event_id": "fixture",
        "event_start_utc": start,
        "markets": [
            {
                "market_type": "moneyline",
                "sides": [
                    {
                        "selection": "away",
                        "description": "Baskonia Vitoria-Gasteiz",
                        "price_probability": 0.30,
                    },
                    {"selection": "home", "description": "Real Madrid", "price_probability": 0.70},
                ],
            }
        ],
    }
    monkeypatch.setattr("model_prediction.euroleague_forward.PolymarketUSClient.slate", lambda *a: [event])
    monkeypatch.setattr(
        "model_prediction.euroleague_forward._parse_teams_from_event",
        lambda ev: ("Baskonia Vitoria-Gasteiz", "Real Madrid"),
    )
    flat = MultiSportPickLedger(tmp_path, flat=True)
    main = MultiSportPickLedger(tmp_path)
    kwargs = {
        "data_root": tmp_path,
        "args_date": now.date().isoformat(),
        "config": {"models": {"EUROLEAGUE": {"min_team_games": 5, "min_edge": 0.0}}},
        "flat_ledger": flat,
        "main_ledger": main,
        "history_client": client,
    }
    report = forecast_euroleague_sport(**kwargs)
    assert not report["errors"], report
    assert report["flat_logged"] == 1 and report["main_logged"] == 0
    assert flat.rows()[0]["model_version"] == "eurolg-tempo-v1"
    assert main.rows() == []
    assert forecast_euroleague_sport(**kwargs)["flat_logged"] == 0


def test_settle_euroleague_pick_from_cached_results(tmp_path):
    from model_prediction.cli.settle import _settle_euroleague_pick

    class Ledger:
        def settle(self, pick_id, away, home, line, odds):
            self.args = (pick_id, away, home)
            return {"result": "win"}

    path = tmp_path / "processed/euroleague/games.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "event_start_utc": "2026-10-22T18:00:00Z",
                "home_team": "Real Madrid",
                "away_team": "Kosner Baskonia",
                "home_score": 90,
                "away_score": 80,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    row = {
        "pick_id": "p1",
        "event_start_utc": "2026-10-22T17:30:00+00:00",
        "home_team": "Real Madrid",
        "away_team": "Kosner Baskonia",
    }
    ledger = Ledger()
    assert _settle_euroleague_pick(row, ledger, data_root=tmp_path) == {
        "pick_id": "p1",
        "settled": True,
        "result": "win",
    }
    assert ledger.args == ("p1", 80, 90)
    assert _settle_euroleague_pick({**row, "home_team": "Other"}, ledger, data_root=tmp_path) is None
