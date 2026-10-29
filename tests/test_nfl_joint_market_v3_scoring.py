"""Prospective scoring uses only explicit final outcomes and exact lines."""

import pytest

from scripts.evaluate_nfl_joint_market_v3 import score_forecast


def _fixture() -> tuple[dict, dict, dict]:
    forecast = {
        "event_id": "game-1",
        "event_start_utc": "2026-11-01T18:00:00Z",
        "contracts": {
            "moneyline": {"selection": "home", "line": None, "ask": 0.6},
            "spread": {"selection": "home", "line": -3.5, "ask": 0.52},
            "total": {"selection": "over", "line": 44.5, "ask": 0.51},
        },
        "selected_probabilities": {"moneyline": 0.6, "spread": 0.55, "total": 0.58},
    }
    packet = {
        "event_id": "game-1",
        "markets": {
            "moneyline": {
                "model_probability": 0.52,
                "quote": {"no_vig_selected": 0.59, "long_ask": 0.6, "short_ask": 0.41},
            },
            "spread": {
                "model_probability": 0.62,
                "quote": {"long_ask": 0.52, "short_ask": 0.49},
            },
            "total": {
                "model_probability": 0.50,
                "quote": {"long_ask": 0.51, "short_ask": 0.50},
            },
        },
    }
    final = {
        "event_id": "game-1", "status": "completed", "home_score": 28, "away_score": 21,
        "raw_hash": "official-scoreboard-digest",
    }
    return forecast, packet, final


def test_scores_all_markets_on_explicit_final() -> None:
    forecast, packet, final = _fixture()
    scored = score_forecast(forecast, packet, final)
    assert {market: row["outcome"] for market, row in scored.items()} == {
        "moneyline": 1, "spread": 1, "total": 1
    }
    assert scored["spread"]["incumbent_brier"] == pytest.approx((0.62 - 1) ** 2)
    assert scored["spread"]["market_no_vig_probability"] == pytest.approx(0.52 / 1.01)


def test_rejects_unplayed_and_skips_push() -> None:
    forecast, packet, final = _fixture()
    final["status"] = "in_progress"
    with pytest.raises(ValueError, match="event_not_completed"):
        score_forecast(forecast, packet, final)
    final["status"] = "completed"
    final["home_score"] = 24
    final["away_score"] = 21
    forecast["contracts"]["spread"]["line"] = -3.0
    assert score_forecast(forecast, packet, final)["spread"]["status"] == "push_excluded"
