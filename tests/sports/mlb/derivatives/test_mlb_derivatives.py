import json
from datetime import UTC, datetime
from types import SimpleNamespace

from model_prediction.cli import forecast as cli_forecast
from model_prediction.domain import MarketType
from model_prediction.sports.mlb.derivatives import (
    MLBFirstInningModel,
    MLBHalfInningInteractionModel,
    MLBHalfInningModel,
    MLBNRFIModel,
    NRFIPrediction,
)


def test_mlb_derivatives_imports():
    assert MLBNRFIModel is not None
    assert NRFIPrediction is not None
    assert MLBFirstInningModel is not None
    assert MLBHalfInningModel is not None
    assert MLBHalfInningInteractionModel is not None


class _MockLedger:
    def __init__(self):
        self.appended = []

    def append_call(self, request, *args, **kwargs):
        self.appended.append((request, args, kwargs))
        return {"pick_id": f"pick-{len(self.appended)}"}

    def append_evaluated(self, request, eligibility, now=None):
        self.appended.append((request, eligibility, now))

    def exposure(self, *args, **kwargs):
        return SimpleNamespace(exposure=0.0)


def test_forecast_mlb_nrfi_with_polymarket_snapshot(tmp_path, monkeypatch):
    from model_prediction.models import mlb_first_inning_live

    monkeypatch.setattr(
        cli_forecast,
        "utc_now",
        lambda: datetime(2026, 10, 7, 12, tzinfo=UTC),
    )
    monkeypatch.setattr(
        mlb_first_inning_live,
        "live_first_inning_features",
        lambda **_kwargs: {
            "away_starter_opp_1st_runs": 0.40,
            "home_starter_opp_1st_runs": 0.42,
            "away_team_1st_scored_away": 0.40,
            "home_team_1st_scored_home": 0.45,
            "away_team_1st_allowed_away": 0.42,
            "home_team_1st_allowed_home": 0.43,
            "park_1st_runs": 0.95,
            "away_starter_fip": 3.2,
            "home_starter_fip": 3.1,
            "away_starter_k_pct": 0.28,
            "home_starter_k_pct": 0.29,
            "away_starter_bb_pct": 0.05,
            "home_starter_bb_pct": 0.05,
            "away_top3_composite": 0.09,
            "home_top3_composite": 0.09,
            "away_starter_starts": 18.0,
            "home_starter_starts": 20.0,
            "away_starter_days_rest": 5.0,
            "home_starter_days_rest": 5.0,
        },
    )

    client = SimpleNamespace(
        scoreboard=lambda *_args: {
            "events": [
                {
                    "id": "141439",
                    "date": "2026-10-07T20:00:00Z",
                    "competitions": [
                        {
                            "venue": {"fullName": "Guaranteed Rate Field"},
                            "competitors": [
                                {
                                    "homeAway": "away",
                                    "team": {"displayName": "Cleveland Guardians"},
                                    "probables": [],
                                },
                                {
                                    "homeAway": "home",
                                    "team": {"displayName": "Chicago White Sox"},
                                    "probables": [],
                                },
                            ],
                        }
                    ],
                }
            ]
        }
    )

    # Write prospective Polymarket snapshot
    odds_dir = tmp_path / "odds" / "mlb" / "2026-10-07"
    odds_dir.mkdir(parents=True, exist_ok=True)
    snap_row = {
        "event_id": "poly-1",
        "market_type": "nrfi",
        "market_slug": "astatc-mlb-cle-cws-2026-10-07-yrfi",
        "event_title": "Game 3: CLE Guardians vs. CHI White Sox",
        "team": "Chicago White Sox",
        "event_start_utc": "2026-10-07T20:00:00Z",
        "observed_at_utc": "2026-10-07T12:00:00Z",
        "provider": "polymarket_us",
        "timestamp_valid": True,
        "reconstructed": False,
        "usage": "prospective_executable_bbo",
        "long": {
            "description": "Yes",
            "ask": 0.44,
        },
        "short": {
            "description": "No",
            "ask": 0.52,
        },
    }
    (odds_dir / "polymarket_snapshots.jsonl").write_text(json.dumps(snap_row) + "\n", encoding="utf-8")

    flat_ledger = _MockLedger()
    main_ledger = _MockLedger()

    res = cli_forecast._forecast_mlb_nrfi_flat(
        "2026-10-07",
        True,
        {},
        None,
        None,
        flat_ledger,
        None,
        main_ledger=main_ledger,
        client=client,
        data_root=tmp_path,
    )

    assert res["logged"] == 1
    assert len(flat_ledger.appended) == 1
    req = flat_ledger.appended[0][0]
    assert req.sportsbook == "polymarket_us"
    assert req.market_type == MarketType.NRFI
    assert req.selection in ("nrfi", "yrfi")
    assert req.model_version == "mlb-nrfi-v4"
    assert req.market_snapshot_hash is not None
    assert "polymarket_snapshots.jsonl" in req.market_snapshot_archive_path
