"""Hockey history must supply real timestamps to offseason-aware EWMA."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from model_prediction import hockey_forward
from model_prediction.cli import forecast as forecast_cli
from model_prediction.hockey_forward import _load_historical_games
from model_prediction.models.hockey import HockeyModel, UpcomingHockeyMatch


def test_json_history_runs_all_hockey_markets_across_offseason(tmp_path):
    rows = [
        {
            "start": start,
            "completed": True,
            "league": "NHL",
            "away_team": "Away",
            "home_team": "Home",
            "away_score": 2,
            "home_score": 4,
        }
        for start in ("2026-04-01T19:00:00Z", "2026-10-01T19:00:00Z")
    ]
    path = tmp_path / "processed" / "hockey" / "games.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    loaded = _load_historical_games(tmp_path)
    assert all(isinstance(game.start, datetime) for game in loaded)
    expected = [
        SimpleNamespace(**{**row, "start": datetime.fromisoformat(row["start"]).astimezone(UTC)})
        for row in rows
    ]
    model = HockeyModel()
    match = UpcomingHockeyMatch("test", "2026-10-25T19:00:00Z", "Away", "Home")
    predictions = model.predict_games(loaded, [match])
    assert predictions == model.predict_games(expected, [match])
    assert {p.market_type for p in predictions} == {"moneyline", "spread", "total"}


def test_invalid_history_start_is_excluded(tmp_path):
    path = tmp_path / "processed" / "hockey" / "games.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"completed": True, "start": "not-a-date"}), encoding="utf-8")
    assert _load_historical_games(tmp_path) == []


def test_hockey_main_respects_serving_artifact_max_edge(monkeypatch, tmp_path):
    now = datetime(2026, 10, 25, 12, tzinfo=UTC)
    contracts = [
        {
            "event_id": f"test-{edge}",
            "event_start_utc": "2026-10-26T20:00:00Z",
            "away_team": "Away",
            "home_team": "Home",
            "market_type": "moneyline",
            "selection": "home",
            "line": None,
            "model_probability": 0.6,
            "executable_ask": 0.6 - edge,
            "edge_vs_executable_ask": edge,
            "observed_at_utc": now.isoformat(),
            "model_version": "hockey-poisson-dc-v2",
            "market_slug": "test-market",
            "rationale": "Test",
        }
        for edge in (0.04, 0.16)
    ]
    monkeypatch.setattr(forecast_cli, "utc_now", lambda: now)
    monkeypatch.setattr(forecast_cli, "_is_registered_serving_model", lambda *args: True)
    monkeypatch.setattr(
        hockey_forward,
        "build_hockey_slate",
        lambda **kwargs: {"priced": contracts, "skipped": []},
    )
    appended = []
    monkeypatch.setattr(
        forecast_cli,
        "_append_secondary_ledger",
        lambda ledger, request, eligibility, observed_at, source: appended.append(
            (ledger, request.event_id, eligibility.decision, eligibility.reason_code, eligibility.units)
        ),
    )
    flat, main = object(), object()
    result = forecast_cli._forecast_hockey_sport(
        data_root=tmp_path,
        args_date="2026-10-26",
        config={"models": {"HOCKEY": {"min_edge": 0.02}}},
        flat_ledger=flat,
        main_ledger=main,
    )
    assert result["errors"] == []
    assert result["flat_logged"] == 2
    assert result["main_logged"] == 1
    assert [(row[1], row[2]) for row in appended if row[0] is main] == [("test-0.04", "CALL")]
    assert [row[2:] for row in appended if row[0] is flat and row[1] == "test-0.16"] == [
        ("NO_CALL", "NO_CALL_EXCESSIVE_EDGE", 0.0)
    ]
