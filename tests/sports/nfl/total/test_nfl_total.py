from model_prediction.cli.forecast import _select_nfl_total_market
from model_prediction.sports.nfl.total import NFLStructuralForecast, NFLStructuralV5Engine


def test_nfl_total_imports():
    assert NFLStructuralForecast is not None
    assert NFLStructuralV5Engine is not None


def test_select_nfl_total_market_ignores_quarter_and_team_totals():
    candidates = [
        {"market_slug": "tsc-nfl-mia-min-2026-10-04-4q-11pt5", "line": 11.5, "long": {"ask": 0.50}},
        {"market_slug": "tsc-nfl-mia-min-2026-10-04-tt-min-33pt5", "line": 33.5, "long": {"ask": 0.50}},
        {"market_slug": "tsc-nfl-mia-min-2026-10-04-total-50pt5", "line": 50.5, "long": {"ask": 0.52}},
    ]
    selected = _select_nfl_total_market(candidates)
    assert selected is not None
    assert selected["market_slug"] == "tsc-nfl-mia-min-2026-10-04-total-50pt5"


def test_forecast_nfl_total_sport_end_to_end(tmp_path, monkeypatch):
    import json
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from model_prediction.cli.forecast import _forecast_nfl_total_sport
    from model_prediction.config import load_config
    from model_prediction.domain import League, MarketType

    game_date = "2026-10-25"
    observed_at = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
    event_start = "2026-10-25T17:00:00Z"

    odds_dir = tmp_path / "odds" / "nfl" / game_date
    odds_dir.mkdir(parents=True, exist_ok=True)
    snap_file = odds_dir / "polymarket_snapshots.jsonl"
    snap_row = {
        "event_id": "99901",
        "event_slug": "nfl-kc-buf-2026-10-25",
        "event_start_utc": event_start,
        "event_title": "KC Chiefs vs. BUF Bills",
        "league": "NFL",
        "line": 46.5,
        "long": {"ask": 0.46, "bid": 0.45, "price": 0.46},
        "short": {"ask": 0.54, "bid": 0.53, "price": 0.54},
        "market_id": "m999",
        "market_slug": "tsc-nfl-kc-buf-2026-10-25-total-46pt5",
        "market_state": "MARKET_STATE_OPEN",
        "market_type": "total",
        "observed_at_utc": "2026-10-25T11:59:00Z",
        "provider": "polymarket_us",
        "reconstructed": False,
        "team": "Kansas City Chiefs",
        "timestamp_valid": True,
        "usage": "prospective_executable_bbo",
    }
    snap_file.write_text(json.dumps(snap_row) + "\n", encoding="utf-8")

    scoreboard = {
        "events": [
            {
                "id": "99901",
                "date": event_start,
                "competitions": [
                    {
                        "competitors": [
                            {"homeAway": "home", "team": {"displayName": "Buffalo Bills", "name": "Bills"}},
                            {
                                "homeAway": "away",
                                "team": {"displayName": "Kansas City Chiefs", "name": "Chiefs"},
                            },
                        ]
                    }
                ],
            }
        ]
    }

    from model_prediction.data_sources.espn import ESPNClient

    monkeypatch.setattr(ESPNClient, "scoreboard", lambda self, league, date: scoreboard)

    class MockLedger:
        def __init__(self):
            self.appended = []

        def append_evaluated(self, request, eligibility, now=None):
            self.appended.append((request, eligibility))

        def exposure(self, *args, **kwargs):
            return SimpleNamespace(exposure=0.0)

    class MockRegistry:
        def resolve(self, league, team, date):
            return SimpleNamespace(canonical_team_id=team)

    cfg = load_config()
    bans = SimpleNamespace(check=lambda *a: (None, False))
    main_ledger = MockLedger()
    flat_ledger = MockLedger()

    result = _forecast_nfl_total_sport(
        data_root=tmp_path,
        args_date=game_date,
        config=cfg,
        registry=MockRegistry(),
        bans=bans,
        main_ledger=main_ledger,
        flat_ledger=flat_ledger,
        observed_at=observed_at,
    )

    assert result["model_version"] == "nfl-total-structural-v5"
    assert result["model_qualified"] is True
    assert result["market_candidates"] == 1
    assert len(flat_ledger.appended) == 1
    req, _el = flat_ledger.appended[0]
    assert req.league == League.NFL
    assert req.market_type == MarketType.TOTAL
    assert req.model_version == "nfl-total-structural-v5"
    assert req.selection in ("over", "under")
    assert req.event_id == "99901"
