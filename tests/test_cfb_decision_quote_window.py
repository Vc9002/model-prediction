"""Which archived NCAAF quotes may price a decision (quick 261121-dac).

The day-ahead capture files D+1/D+2 books under the game's own date. A decision on that
date may only use quotes observed on the game date and no later than the decision.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from model_prediction.models import college_football
from model_prediction.models.college_football import (
    _latest_capture_per_market,
    _load_decision_snapshot_rows,
    _match_cfb_moneyline,
    _match_cfb_spread,
)
from tests.test_cfb_serving_parity import nb_rollback_model

GAME_DATE = "2030-09-07"  # a Saturday; decisions below are made on it


def _row(slug: str, market_type: str, observed: str, long_ask: float, short_ask: float, **extra) -> dict:
    return {
        "provider": "polymarket_us",
        "reconstructed": False,
        "usage": "prospective_executable_bbo",
        "timestamp_valid": True,
        "market_state": "MARKET_STATE_OPEN",
        "market_slug": slug,
        "market_type": market_type,
        "event_title": "Alabama Crimson Tide vs. Georgia Bulldogs",
        "team": "Alabama Crimson Tide",
        "event_start_utc": f"{GAME_DATE}T23:30:00Z",
        "observed_at_utc": observed,
        "long": {"ask": long_ask, "bid": long_ask - 0.02},
        "short": {"ask": short_ask, "bid": short_ask - 0.02},
        **extra,
    }


def _write(root: Path, day: str, rows: list[dict]) -> Path:
    path = root / "odds" / "ncaaf" / day / "polymarket_snapshots.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def test_decision_reads_ignore_quotes_observed_after_the_decision(tmp_path):
    decision = datetime(2030, 9, 7, 18, tzinfo=UTC)
    path = _write(
        tmp_path,
        GAME_DATE,
        [
            _row("ml", "moneyline", f"{GAME_DATE}T15:00:00Z", 0.40, 0.62),
            _row("ml", "moneyline", f"{GAME_DATE}T17:00:00Z", 0.45, 0.57),
            _row("ml", "moneyline", f"{GAME_DATE}T19:00:00Z", 0.60, 0.42),  # after the decision
        ],
    )
    rows = _latest_capture_per_market(_load_decision_snapshot_rows(path, GAME_DATE, decision))
    quote, long_side = _match_cfb_moneyline("Alabama Crimson Tide", "Georgia Bulldogs", rows)
    assert quote is not None and long_side == "away"
    assert quote["observed_at_utc"] == f"{GAME_DATE}T17:00:00Z"
    assert quote["long"]["ask"] == 0.45


def test_a_day_ahead_rung_never_outranks_todays_rung(tmp_path):
    """Yesterday's rung A sat at 50c; today's capture shortlisted rung B instead. The
    balance selector has no age bound, so without the game-day floor A would price the
    pick at a day-old ask."""
    decision = datetime(2030, 9, 7, 18, tzinfo=UTC)
    path = _write(
        tmp_path,
        GAME_DATE,
        [
            # 2030-09-06 20:00 ET, the day before: filed under the game date by the day-ahead run.
            _row("sp-a", "spread", "2030-09-07T00:00:00Z", 0.50, 0.52, line=-3.5),
            _row("sp-b", "spread", f"{GAME_DATE}T15:00:00Z", 0.46, 0.56, line=-6.5),
        ],
    )
    rows = _latest_capture_per_market(_load_decision_snapshot_rows(path, GAME_DATE, decision))
    quote, _anchor = _match_cfb_spread("Alabama Crimson Tide", "Georgia Bulldogs", rows)
    assert quote is not None and quote["market_slug"] == "sp-b"


def test_a_forecast_run_before_game_day_uses_that_days_quotes(tmp_path):
    """A manual ``forecast --date <tomorrow>`` (2026-10-21 priced 29 NCAAF rows for 10-22/10-23)
    decides on the day before the game: that day's books are its decision quotes, and only
    rungs from earlier days are excluded."""
    decision = datetime(2030, 9, 6, 22, tzinfo=UTC)  # Friday 18:00 ET, game on Saturday
    path = _write(
        tmp_path,
        GAME_DATE,
        [
            # Thursday 2030-09-05 16:00 ET: two days before the game.
            _row("sp-a", "spread", "2030-09-05T20:00:00Z", 0.50, 0.52, line=-3.5),
            # Friday 2030-09-06 11:00 ET.
            _row("sp-b", "spread", "2030-09-06T15:00:00Z", 0.46, 0.56, line=-6.5),
        ],
    )
    rows = _latest_capture_per_market(_load_decision_snapshot_rows(path, GAME_DATE, decision))
    quote, _anchor = _match_cfb_spread("Alabama Crimson Tide", "Georgia Bulldogs", rows)
    assert quote is not None and quote["market_slug"] == "sp-b"


def test_forecast_reads_only_its_own_date_and_prices_from_the_newest_valid_quote(tmp_path, monkeypatch):
    """End to end through build_cfb_slate: an earlier-dated file holding a newer in-window
    row for the same slug, a post-decision row, and a day-ahead row are all ignored."""
    decision = datetime(2030, 9, 7, 18, tzinfo=UTC)
    _write(
        tmp_path,
        GAME_DATE,
        [
            _row("ml", "moneyline", "2030-09-06T16:00:00Z", 0.30, 0.72),  # day-ahead
            _row("ml", "moneyline", f"{GAME_DATE}T16:00:00Z", 0.45, 0.57),  # the decision quote
            _row("ml", "moneyline", f"{GAME_DATE}T19:00:00Z", 0.60, 0.42),  # after the decision
        ],
    )
    _write(tmp_path, "2030-09-06", [_row("ml", "moneyline", f"{GAME_DATE}T17:00:00Z", 0.20, 0.82)])
    event = {
        "id": "fixture",
        "date": f"{GAME_DATE}T23:30:00Z",
        "competitions": [
            {
                "competitors": [
                    {"homeAway": "home", "team": {"displayName": "Georgia Bulldogs"}},
                    {"homeAway": "away", "team": {"displayName": "Alabama Crimson Tide"}},
                ],
                "odds": [],
            }
        ],
    }
    client = SimpleNamespace(scoreboard=lambda *_: {"events": [event]})
    monkeypatch.setattr(college_football, "_load_cfb_history", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "model_prediction.features.multi_sport_weather.resolve_cfb_game_weather",
        lambda *_args, **_kwargs: (70.0, 0.0, 0.0, True),
    )

    def predict(_self, _history, _game, **_kwargs):
        return [
            SimpleNamespace(
                market_type="moneyline",
                probabilities={"home": 0.6, "away": 0.4},
                uncertainty=0.1,
                model_version="cfb-structural-v4",
                feature_basis={},
                inference_inputs={},
                rationale="fixture",
            )
        ]

    monkeypatch.setattr(college_football.CFBStructuralV4Model, "predict_matchup", predict)
    slate = college_football.build_cfb_slate(
        tmp_path, GAME_DATE, client=client, observed_at=decision, model=nb_rollback_model()
    )
    moneylines = [row for row in slate["priced_contracts"] if row["market_type"] == "moneyline"]
    assert moneylines
    for row in moneylines:
        assert row["sportsbook"] == "polymarket_us"
        assert row["observed_at_utc"] == f"{GAME_DATE}T16:00:00Z"
        assert row["executable_ask"] in (0.45, 0.57)
