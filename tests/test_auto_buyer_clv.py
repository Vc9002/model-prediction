"""Unit tests for Auto-Buyer CLV tracking and settlement."""

import json

import pytest

from model_prediction.portfolio.auto_buyer_ledger import (
    _resolve_closing_probability_for_auto_buyer,
)


def test_auto_buyer_clv_resolution(tmp_path):
    """Verify Auto-Buyer closing snapshot lookup and CLV calculation."""
    snap_dir = tmp_path / "odds" / "nba" / "2026-11-08"
    snap_dir.mkdir(parents=True)
    snap_file = snap_dir / "polymarket_snapshots.jsonl"

    snap_data = {
        "event_id": "12345",
        "market_slug": "tsc-nba-bos-mia-2026-11-08-220pt5",
        "market_type": "total",
        "observed_at_utc": "2026-11-08T18:00:00Z",
        "long": {"ask": 0.55, "description": "Over"},
        "short": {"ask": 0.46, "description": "Under"},
    }
    snap_file.write_text(json.dumps(snap_data) + "\n", encoding="utf-8")

    close_p = _resolve_closing_probability_for_auto_buyer(
        data_root=tmp_path,
        sport="NBA",
        slug="tsc-nba-bos-mia-2026-11-08-220pt5",
        event_start_utc="2026-11-08T19:00:00Z",
        token_side="long",
        selection="over",
    )

    assert close_p == 0.55
    entry_p = 0.50
    clv = close_p - entry_p
    assert round(clv, 4) == 0.05


def _write_quotes(tmp_path, sport_dir, slug, quotes):
    folder = tmp_path / "odds" / sport_dir / "2026-11-08"
    folder.mkdir(parents=True)
    folder.joinpath("polymarket_snapshots.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "market_slug": slug,
                    "market_type": "total",
                    "observed_at_utc": observed,
                    "market_state": "MARKET_STATE_OPEN",
                    "event_start_utc": "2026-11-08T19:00:00Z",
                    "long": {"ask": ask, "description": "Over"},
                    "short": {"ask": round(1.01 - ask, 2), "description": "Under"},
                }
            )
            + "\n"
            for observed, ask in quotes
        ),
        encoding="utf-8",
    )


def _settle_one_buy(tmp_path, sport, quotes):
    """Settle one filled 'over' buy (filled 18:40Z at 0.50) through the real buyer settle pass."""
    from unittest.mock import MagicMock

    from model_prediction.portfolio.auto_buyer_ledger import settle_auto_buyer_ledger

    slug = f"tsc-{sport.lower()}-aaa-bbb-2026-11-08-140pt5"
    _write_quotes(tmp_path, sport.lower(), slug, quotes)
    record = {
        "order_id": f"PAPER-clv-{sport}",
        "market_slug": slug,
        "sport": sport,
        "market_type": "total",
        "line": 140.5,
        "selection": "over",
        "token_side": "long",
        "away_team": "Aaa Visitors",
        "home_team": "Bbb Hosts",
        "event_start_utc": "2026-11-08T19:00:00Z",
        "executed_at_utc": "2026-11-08T18:40:00.123456789Z",
        "shares": 10.0,
        "entry_price": 0.50,
        "cost_usd": 5.0,
        "unit_value_usd": 5.0,
        "units": 1.0,
        "status": "open",
        "result": "open",
        "fill_known": True,
    }
    path = tmp_path / "auto_buyer_ledger.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    espn = MagicMock()
    espn.scoreboard.return_value = {
        "events": [
            {
                "competitions": [
                    {
                        "status": {"type": {"completed": True}},
                        "competitors": [
                            {"homeAway": "away", "team": {"displayName": "Aaa Visitors"}, "score": "78"},
                            {"homeAway": "home", "team": {"displayName": "Bbb Hosts"}, "score": "71"},
                        ],
                    }
                ]
            }
        ]
    }
    assert settle_auto_buyer_ledger(data_root=tmp_path, espn=espn)["newly_settled"] == 1
    return json.loads(path.read_text(encoding="utf-8").splitlines()[0])


@pytest.mark.parametrize("sport", ["NCAAB", "NCAAF"])
def test_buyer_close_observed_at_or_before_the_fill_is_never_the_close(tmp_path, sport):
    # Only quotes up to the fill (18:40Z): the buy's own price must not come back as its close.
    row = _settle_one_buy(tmp_path, sport, [("2026-11-08T18:30:00Z", 0.48), ("2026-11-08T18:40:00Z", 0.50)])
    assert row["status"] == "settled"
    assert "closing_price" not in row
    assert row["probability_clv"] == ""


@pytest.mark.parametrize("sport", ["NCAAB", "NCAAF"])
def test_buyer_close_is_the_last_pre_tip_quote_after_the_fill(tmp_path, sport):
    row = _settle_one_buy(tmp_path, sport, [("2026-11-08T18:40:00Z", 0.50), ("2026-11-08T18:55:00Z", 0.56)])
    assert row["closing_price"] == "0.560000"
    assert row["probability_clv"] == "+0.060000"


def test_buyer_close_for_unlisted_sport_is_unchanged(tmp_path):
    row = _settle_one_buy(tmp_path, "NBA", [("2026-11-08T18:30:00Z", 0.48)])
    assert row["closing_price"] == "0.480000"


@pytest.mark.parametrize("decided_at_utc", [None, "", "not-a-time"])
def test_buyer_close_without_a_usable_fill_time_fails_closed(tmp_path, decided_at_utc):
    slug = "tsc-ncaab-aaa-bbb-2026-11-08-140pt5"
    _write_quotes(tmp_path, "ncaab", slug, [("2026-11-08T18:30:00Z", 0.48)])
    close_p = _resolve_closing_probability_for_auto_buyer(
        data_root=tmp_path,
        sport="NCAAB",
        slug=slug,
        event_start_utc="2026-11-08T19:00:00Z",
        token_side="long",
        selection="over",
        decided_at_utc=decided_at_utc,
    )
    assert close_p is None
