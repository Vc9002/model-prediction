import json
from datetime import UTC
from pathlib import Path

from model_prediction.models.mlb_first_inning import (
    INTERACTION_FEATURE_NAMES,
    FirstInningGameRow,
    MLBHalfInningInteractionModel,
)
from model_prediction.production_registry import ProductionModelRegistry, compute_artifact_hash
from model_prediction.scalar_artifact_replay import build_snapshot, replay_snapshot


def test_mlb_nrfi_v4_artifact_hash_and_registry():
    # 1. Artifact Hash Verification
    repo_root = Path(__file__).resolve().parents[1]
    artifact_path = repo_root / "config/models/mlb-nrfi-v4.json"
    assert artifact_path.exists()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    embedded_hash = payload["artifact_hash"]
    computed_hash = compute_artifact_hash(payload)
    assert embedded_hash == computed_hash
    assert payload["model_version"] == "mlb-nrfi-v4"
    assert payload["method"] == "half_inning_interaction"
    assert payload["qualification"]["qualified"] is True
    assert payload["qualification"]["holdout_log_loss"] < 0.690

    # 2. Production Registry Champion Resolution
    registry = ProductionModelRegistry.load(repo_root)
    champ = registry.champion("MLB", "nrfi")
    assert champ.model_id == "mlb-nrfi-v4"
    assert champ.rollback_model == "mlb-nrfi-v3"
    assert champ.available is True
    assert "mlb-nrfi-v4" not in registry.blocked_workflows

    # 3. Model from_dict instantiation and prediction
    model = MLBHalfInningInteractionModel.from_dict(payload)
    assert len(model.coef_away) == len(INTERACTION_FEATURE_NAMES)
    assert len(model.coef_home) == len(INTERACTION_FEATURE_NAMES)
    assert len(model.scaler_mean_away) == len(INTERACTION_FEATURE_NAMES)
    assert len(model.scaler_mean_home) == len(INTERACTION_FEATURE_NAMES)
    assert model.fit_n_games >= 4000

    features = {
        "away_top3_composite": 0.107,
        "home_top3_composite": 0.107,
        "home_starter_opp_1st_runs": 0.516,
        "away_starter_opp_1st_runs": 0.467,
        "home_starter_fip": 3.88,
        "away_starter_fip": 3.88,
        "home_starter_k_pct": 0.22,
        "away_starter_k_pct": 0.22,
        "home_starter_bb_pct": 0.077,
        "away_starter_bb_pct": 0.077,
        "park_1st_runs": 1.01,
        "away_top3_same_hand_share": 0.55,
        "home_top3_same_hand_share": 0.55,
        "away_team_1st_scored_away": 0.453,
        "home_team_1st_scored_home": 0.549,
    }
    row = FirstInningGameRow(None, "2026-10-07T20:00:00Z", "Home", "Away", "Venue", features, 0, 0.0)
    p_nrfi = model.predict_p_nrfi(row)
    assert 0.35 < p_nrfi < 0.75
    p_yrfi = model.predict_p_yrfi(row)
    assert abs(p_nrfi + p_yrfi - 1.0) < 1e-9

    # 4. Scalar Artifact Replay
    snap = build_snapshot(
        payload,
        features,
        {},
        event_id="test-event-v4",
        event_start_utc="2026-10-07T20:00:00Z",
        observed_at_utc="2026-10-07T12:00:00Z",
    )
    replayed = replay_snapshot(snap)
    assert replayed["nrfi"] == round(p_nrfi, 4)
    assert replayed["yrfi"] == round(p_yrfi, 4)


class _MockLedger:
    def __init__(self):
        self.appended = []

    def append_evaluated(self, request, eligibility, now=None):
        self.appended.append((request, eligibility, now))

    def exposure(self, *args, **kwargs):
        from types import SimpleNamespace

        return SimpleNamespace(exposure=0.0)


def test_mlb_nrfi_v4_conviction_gating(tmp_path, monkeypatch):
    from datetime import datetime
    from types import SimpleNamespace

    from model_prediction.cli import forecast as cli_forecast
    from model_prediction.models import mlb_first_inning_live

    monkeypatch.setattr(
        cli_forecast,
        "utc_now",
        lambda: datetime(2026, 10, 7, 12, tzinfo=UTC),
    )

    # 1. High conviction case: strong pitchers -> high NRFI prob (> 0.555)
    high_conv_features = {
        "away_starter_opp_1st_runs": 0.30,
        "home_starter_opp_1st_runs": 0.30,
        "away_team_1st_scored_away": 0.35,
        "home_team_1st_scored_home": 0.35,
        "away_team_1st_allowed_away": 0.35,
        "home_team_1st_allowed_home": 0.35,
        "park_1st_runs": 0.90,
        "away_starter_fip": 2.8,
        "home_starter_fip": 2.7,
        "away_starter_k_pct": 0.32,
        "home_starter_k_pct": 0.33,
        "away_starter_bb_pct": 0.04,
        "home_starter_bb_pct": 0.04,
        "away_top3_composite": 0.08,
        "home_top3_composite": 0.08,
        "away_top3_same_hand_share": 0.50,
        "home_top3_same_hand_share": 0.50,
        "away_starter_starts": 20.0,
        "home_starter_starts": 22.0,
        "away_starter_days_rest": 5.0,
        "home_starter_days_rest": 5.0,
    }

    monkeypatch.setattr(
        mlb_first_inning_live,
        "live_first_inning_features",
        lambda **_kwargs: high_conv_features,
    )

    client = SimpleNamespace(
        scoreboard=lambda *_args: {
            "events": [
                {
                    "id": "event-high-conv",
                    "date": "2026-10-07T20:00:00Z",
                    "competitions": [
                        {
                            "venue": {"fullName": "Dodger Stadium"},
                            "competitors": [
                                {
                                    "homeAway": "away",
                                    "team": {"displayName": "San Francisco Giants"},
                                    "probables": [],
                                },
                                {
                                    "homeAway": "home",
                                    "team": {"displayName": "Los Angeles Dodgers"},
                                    "probables": [],
                                },
                            ],
                        }
                    ],
                }
            ]
        }
    )

    odds_dir = tmp_path / "odds" / "mlb" / "2026-10-07"
    odds_dir.mkdir(parents=True, exist_ok=True)
    snap_row = {
        "event_id": "poly-high",
        "market_type": "nrfi",
        "market_slug": "astatc-mlb-sfg-lad-2026-10-07-nrfi",
        "event_title": "SF Giants vs LA Dodgers",
        "team": "Los Angeles Dodgers",
        "event_start_utc": "2026-10-07T20:00:00Z",
        "observed_at_utc": "2026-10-07T12:00:00Z",
        "provider": "polymarket_us",
        "timestamp_valid": True,
        "reconstructed": False,
        "usage": "prospective_executable_bbo",
        "long": {"description": "Yes", "ask": 0.45},
        "short": {"description": "No", "ask": 0.52},
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
    # Main ledger receives the high-conviction call:
    assert len(main_ledger.appended) == 1
    assert main_ledger.appended[0][0].model_version == "mlb-nrfi-v4"
    assert main_ledger.appended[0][0].model_probability >= 0.555

    # 2. Coin-flip case (low conviction): P near 0.50 -> gated out of Main Ledger
    low_conv_features = {
        "away_starter_opp_1st_runs": 0.51,
        "home_starter_opp_1st_runs": 0.48,
        "away_team_1st_scored_away": 0.46,
        "home_team_1st_scored_home": 0.50,
        "away_team_1st_allowed_away": 0.45,
        "home_team_1st_allowed_home": 0.47,
        "park_1st_runs": 1.01,
        "away_starter_fip": 3.9,
        "home_starter_fip": 3.8,
        "away_starter_k_pct": 0.22,
        "home_starter_k_pct": 0.22,
        "away_starter_bb_pct": 0.08,
        "home_starter_bb_pct": 0.08,
        "away_top3_composite": 0.107,
        "home_top3_composite": 0.107,
        "away_top3_same_hand_share": 0.55,
        "home_top3_same_hand_share": 0.55,
        "away_starter_starts": 10.0,
        "home_starter_starts": 10.0,
        "away_starter_days_rest": 5.0,
        "home_starter_days_rest": 5.0,
    }
    monkeypatch.setattr(
        mlb_first_inning_live,
        "live_first_inning_features",
        lambda **_kwargs: low_conv_features,
    )

    flat_ledger_low = _MockLedger()
    main_ledger_low = _MockLedger()

    res_low = cli_forecast._forecast_mlb_nrfi_flat(
        "2026-10-07",
        True,
        {},
        None,
        None,
        flat_ledger_low,
        None,
        main_ledger=main_ledger_low,
        client=client,
        data_root=tmp_path,
    )

    assert res_low["logged"] == 1
    # Flat ledger receives the candidate:
    assert len(flat_ledger_low.appended) == 1
    # Main ledger GATES OUT the low-conviction call:
    assert len(main_ledger_low.appended) == 0
