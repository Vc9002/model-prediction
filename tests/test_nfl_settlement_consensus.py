"""Scheduled settlement fills the NFL closing consensus from the runtime-root capture.

Settlement derives its data root from the ledger path (the checkout's legacy ``data/``) while the
ESPN consensus capture is filed under the runtime root. ``espn_odds_path`` reroutes the legacy root
at its single choke point (commit a19d42ce), which is what keeps ``_settle_all_unsettled`` from
reading an empty tree; that fix was unit-tested at the path level only. This pins it end to end:
no settlement-side reroute is needed, and removing the choke-point reroute makes this test fail
(quick task 261115-g9j, item 2).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from model_prediction.cli import settle as settle_module
from model_prediction.config import PROJECT_ROOT
from model_prediction.data_sources.espn_nfl_odds import capture_nfl_espn_odds
from model_prediction.domain import EASTERN, League, MarketType, ModelOrigin, PickRequest
from model_prediction.eligibility import EligibilityResult
from model_prediction.entities import CanonicalTeam
from model_prediction.ledger import PickLedger

EVENT_ID = "401873052"


def _espn_event(start: datetime) -> dict:
    return {
        "id": EVENT_ID,
        "date": start.strftime("%Y-%m-%dT%H:%MZ"),
        "competitions": [
            {
                "odds": [
                    {
                        "provider": {"name": "DraftKings"},
                        "pointSpread": {
                            "home": {"close": {"line": "-2.5", "odds": "-105"}},
                            "away": {"close": {"line": "+2.5", "odds": "-115"}},
                        },
                        "total": {
                            "over": {"close": {"line": "o40.5", "odds": "-118"}},
                            "under": {"close": {"line": "u40.5", "odds": "-102"}},
                        },
                        "moneyline": {
                            "home": {"close": {"odds": "-142"}},
                            "away": {"close": {"odds": "+120"}},
                        },
                    }
                ],
                "competitors": [
                    {"homeAway": "home", "team": {"displayName": "New York Giants"}},
                    {"homeAway": "away", "team": {"displayName": "Washington Commanders"}},
                ],
            }
        ],
    }


class _FakeESPN:
    def __init__(self, by_date):
        self.by_date = by_date

    def scoreboard(self, league, game_date):
        return {"events": self.by_date.get(game_date, [])}


def test_nfl_settlement_finds_the_closing_consensus_under_the_runtime_root(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(runtime))
    start = (datetime.now(UTC) - timedelta(hours=3)).replace(second=0, microsecond=0)
    # The daily job captures with the checkout's legacy data/ root; the file lands in the runtime root.
    capture = capture_nfl_espn_odds(
        PROJECT_ROOT / "data",
        start.astimezone(EASTERN).date().isoformat(),
        days=1,
        client=_FakeESPN({start.astimezone(EASTERN).date().isoformat(): [_espn_event(start)]}),
        now=start - timedelta(hours=2),
    )
    assert capture["records_written"] == 1, capture
    assert list(runtime.rglob("espn_odds.jsonl")), "capture must land under the runtime root"

    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    request = PickRequest(
        event_start_utc=start.isoformat(),
        event_id=EVENT_ID,
        league=League.NFL,
        away_team="Washington Commanders",
        home_team="New York Giants",
        market_type=MarketType.MONEYLINE,
        selection="home",
        line=None,
        sportsbook="polymarket_us_indicative",
        american_odds=-110,
        model_probability=0.6,
        model_uncertainty=0.05,
        model_version="nfl-elo-trend-lr-v4",
        rationale="fixture",
        risks="",
        model_origin=ModelOrigin.STATISTICAL_MODEL,
        model_enabled=True,
    )
    away = CanonicalTeam(
        "Washington Commanders", League.NFL, "Washington Commanders", "WAS", True, None, None, ()
    )
    home = CanonicalTeam("New York Giants", League.NFL, "New York Giants", "NYG", True, None, None, ())
    row = ledger.append_evaluated(
        request,
        EligibilityResult(False, "CALL", "PAPER_CALL_MODEL_UNVALIDATED", 1.0, 60, 0.05, 0.05, away, home),
        now=start - timedelta(hours=5),
    )
    monkeypatch.setattr(
        settle_module,
        "_find_espn_result",
        lambda *a, **k: {
            "status_name": "STATUS_FINAL",
            "completed": True,
            "away_score": 17,
            "home_score": 24,
        },
    )
    config = {
        "project": {
            # The legacy repo data/ root, exactly what scheduled settlement computes.
            "ledger_path": str(PROJECT_ROOT / "data" / "picks.xlsx"),
            "market_odds_snapshot_path": str(tmp_path / "no_market_odds.jsonl"),
        }
    }
    outcome = settle_module._settle_all_unsettled(
        SimpleNamespace(), config, ledger, espn=SimpleNamespace(), scoreboard_cache={}
    )
    assert [item["pick_id"] for item in outcome["settled"]] == [row["pick_id"]], outcome

    settled = next(item for item in ledger.rows() if item["pick_id"] == row["pick_id"])
    # De-vigged home moneyline from -142 / +120: a real probability, not the empty string.
    assert settled["closing_consensus_probability"] != ""
    assert 0.55 < float(settled["closing_consensus_probability"]) < 0.65
