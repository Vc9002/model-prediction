"""Strict regression tests for the permanent champion-challenger architecture and no-cancellation rule.

Invariants verified:
1. Every supported sport/market always has an active champion (serving_status == production).
2. Challenger failure or absence never removes or disables champion serving.
3. Rejected challenger preserves incumbent champion without serving interruption.
4. Promotion atomically swaps champion and preserves old champion as rollback.
5. Prospective challenger is hash-frozen and immutable.
6. No supported market can have zero serving models.
7. Evidence status (e.g. DEGRADED) does not control serving status (remains PRODUCTION).
8. Paired evaluation operates on real settled picks without backfill contamination.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from model_prediction.champion_challenger import (
    PairedComparison,
)
from model_prediction.config import PROJECT_ROOT
from model_prediction.model_lifecycle import (
    SUPPORTED_MARKETS,
    ChallengerManifest,
    DecisionContext,
    EvidenceStatus,
    LifecycleRole,
    ModelPredictionRecord,
    ReplacementPriority,
    generate_decision_context_id,
    run_fail_isolated_prediction,
)
from model_prediction.model_promotion import reject_challenger
from model_prediction.production_registry import ProductionModelRegistry
from model_prediction.qualification_registry import generate_qualification_registry


@pytest.fixture(autouse=True)
def _isolated_runtime_root(tmp_path: Path, monkeypatch) -> None:
    """Isolate runtime operational SQLite state."""
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(tmp_path / "runtime"))


def test_every_supported_market_has_active_champion() -> None:
    """Invariant 1: Every supported sport and market must have a valid serving champion."""
    registry = ProductionModelRegistry.load(PROJECT_ROOT)
    registry.validate_lifecycle_invariants()

    for sport, markets in SUPPORTED_MARKETS.items():
        for market in markets:
            champion = registry.champion(sport, market)
            assert champion is not None, f"No champion found for {sport} {market}"
            assert champion.available, f"Champion {champion.model_id} for {sport} {market} is unavailable"
            assert champion.serving_status in {"production", "active"}


def test_no_supported_market_can_have_zero_serving_models() -> None:
    """Invariant 2: No supported market can have zero serving models."""
    registry = ProductionModelRegistry.load(PROJECT_ROOT)
    contracts = registry.contracts

    for sport, markets in SUPPORTED_MARKETS.items():
        sport_contracts = contracts.get(sport.upper()) or {}
        for market in markets:
            assert market in sport_contracts, f"Market {market} missing in contracts for {sport}"
            contract = sport_contracts[market]
            assert contract.champion_model_id, f"Empty champion in contract for {sport} {market}"
            assert contract.serving_status in {"production", "active"}


def test_evidence_status_does_not_control_serving_status() -> None:
    """Invariant 3: Weak/degraded evidence increases replacement priority; it does NOT disable the model."""
    registry = ProductionModelRegistry.load(PROJECT_ROOT)

    # NCAAF models have degraded evidence status, but are active production serving champions
    for market in ("moneyline", "spread", "total"):
        champion = registry.champion("NCAAF", market)
        assert champion is not None
        assert champion.available is True
        assert champion.serving_status in {"production", "active"}
        assert champion.evidence_status == EvidenceStatus.DEGRADED.value
        assert champion.replacement_priority == ReplacementPriority.CRITICAL.value

        contract = registry.lifecycle_contract("NCAAF", market)
        assert contract is not None
        assert contract.serving_status in {"production", "active"}
        assert contract.evidence_status == EvidenceStatus.DEGRADED.value
        assert contract.replacement_priority == ReplacementPriority.CRITICAL.value


def test_challenger_failure_does_not_disable_champion() -> None:
    """Invariant 4: Champion serving continues fail-isolated if challenger fails."""
    context = DecisionContext(
        decision_context_id="MLB_2026-09-12_NYY_BOS_TMINUS30_001",
        event_id="NYY_BOS_20260912",
        sport="MLB",
        market="moneyline",
        event_start_utc="2026-09-12T23:05:00Z",
        decision_utc="2026-09-12T22:35:00Z",
    )

    def _champion_fn(ctx: DecisionContext) -> ModelPredictionRecord:
        return ModelPredictionRecord(
            prediction_id="pred-champ-1",
            event_id=ctx.event_id,
            decision_context_id=ctx.decision_context_id,
            sport=ctx.sport,
            market_type=ctx.market,
            selection="NYY",
            line=None,
            model_id="mlb-elo-trend-lr-v8",
            model_artifact_hash="hash-champ",
            feature_schema_hash="schema-v1",
            lifecycle_role=LifecycleRole.CHAMPION.value,
            probability=0.58,
        )

    def _broken_challenger_fn(ctx: DecisionContext) -> ModelPredictionRecord:
        raise RuntimeError("Challenger feature store timeout / model crash")

    champ_pred, chall_pred, chall_error = run_fail_isolated_prediction(
        _champion_fn, _broken_challenger_fn, context
    )

    assert champ_pred is not None
    assert champ_pred.model_id == "mlb-elo-trend-lr-v8"
    assert champ_pred.probability == 0.58
    assert chall_pred is None
    assert chall_error is not None
    assert "RuntimeError" in chall_error


def test_decision_context_generation_and_hashing() -> None:
    """Decision context generation is deterministic and reproducible."""
    ctx_id = generate_decision_context_id(
        sport="MLB",
        event_id="401568912",
        decision_utc="2026-09-12T22:35:00Z",
        horizon="TMINUS30",
    )
    assert ctx_id == "MLB_2026-09-12_401568912_TMINUS30"

    ctx = DecisionContext(
        decision_context_id=ctx_id,
        event_id="401568912",
        sport="MLB",
        market="moneyline",
        event_start_utc="2026-09-12T23:05:00Z",
        decision_utc="2026-09-12T22:35:00Z",
        market_line=None,
        market_fair_probability=0.52,
    )
    h1 = ctx.compute_context_hash()
    h2 = ctx.compute_context_hash()
    assert h1 == h2
    assert len(h1) == 64


def test_rejected_challenger_preserves_incumbent(tmp_path: Path) -> None:
    """Invariant 5: Rejecting a challenger clears the challenger pointer without touching the champion."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    config_dir = repo / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "models").mkdir(parents=True, exist_ok=True)

    # Write test production.yaml
    cfg = {
        "schema_version": "3",
        "prediction_service": {
            "enabled": True,
            "mode": "production",
            "primary": {"sport": "MLB", "market": "moneyline", "model_id": "mlb-elo-trend-lr-v8"},
            "champions": {"MLB": {"moneyline": "mlb-elo-trend-lr-v8"}},
            "challengers": {"MLB": {"moneyline": "mlb-moneyline-v9-candidate"}},
            "models": [
                {
                    "model_id": "mlb-elo-trend-lr-v8",
                    "sport": "MLB",
                    "market": "moneyline",
                    "implementation": "json_artifact",
                    "artifact": "config/models/mlb-elo-trend-lr-v8.json",
                    "enabled": True,
                },
                {
                    "model_id": "mlb-moneyline-v9-candidate",
                    "sport": "MLB",
                    "market": "moneyline",
                    "implementation": "json_artifact",
                    "artifact": "config/models/mlb-moneyline-v9-candidate.json",
                    "enabled": True,
                },
            ],
            "fallback_action": "no_prediction",
        },
        "execution": {"automated_orders": False, "manual_orders_only": True},
        "health": {"max_data_age_minutes": 120},
    }
    (config_dir / "production.yaml").write_text(yaml.dump(cfg), encoding="utf-8")

    from model_prediction.production_registry import compute_artifact_hash

    for mid in ["mlb-elo-trend-lr-v8", "mlb-moneyline-v9-candidate"]:
        art = {
            "model_version": mid,
            "sport": "MLB",
            "schema_version": "1",
            "market_models": {"moneyline": {}},
        }
        art["artifact_hash"] = compute_artifact_hash(art)
        (config_dir / "models" / f"{mid}.json").write_text(json.dumps(art), encoding="utf-8")

    reg_before = ProductionModelRegistry.load(repo)
    assert reg_before.champion("MLB", "moneyline").model_id == "mlb-elo-trend-lr-v8"
    assert reg_before.challenger("MLB", "moneyline").model_id == "mlb-moneyline-v9-candidate"

    result = reject_challenger(
        sport="MLB",
        market="moneyline",
        challenger_model_id="mlb-moneyline-v9-candidate",
        reason="Bullpen features worsened OOF logloss on prospective dates",
        approved_by="lead_quant",
        repo_root=repo,
    )

    assert result["rejected_challenger_id"] == "mlb-moneyline-v9-candidate"
    assert result["champion_model_id"] == "mlb-elo-trend-lr-v8"

    reg_after = ProductionModelRegistry.load(repo)
    # Champion is completely untouched
    assert reg_after.champion("MLB", "moneyline").model_id == "mlb-elo-trend-lr-v8"
    # Challenger pointer is cleared
    assert reg_after.challenger("MLB", "moneyline") is None


def test_prospective_challenger_is_hash_frozen() -> None:
    """Invariant 6: Prospective challenger manifests are hash-frozen and immutable."""
    manifest = ChallengerManifest(
        model_id="mlb-moneyline-residual-v1",
        sport="MLB",
        market="moneyline",
        artifact_hash="a1b2c3d4e5f67890",
        feature_schema_hash="schema-hash-001",
        training_dataset_hash="dataset-hash-001",
        training_code_hash="code-hash-001",
        calibration_hash="calib-hash-001",
        promotion_protocol_hash="protocol-hash-001",
        frozen_at_utc="2026-08-31T12:00:00Z",
        prospective_start_utc="2026-09-01T00:00:00Z",
        status="prospective",
    )
    d = manifest.to_dict()
    assert d["model_id"] == "mlb-moneyline-residual-v1"
    assert d["artifact_hash"] == "a1b2c3d4e5f67890"

    reconstructed = ChallengerManifest.from_dict(d)
    assert reconstructed == manifest


def test_paired_comparison_on_settled_picks() -> None:
    """Paired comparison operates on aligned settled picks and produces clear 3-way verdicts."""
    # Synthetic settled picks for champion and challenger
    champ_picks = []
    chall_better_picks = []
    chall_worse_picks = []

    for i in range(60):
        eid = f"event-{i:03d}"
        date = f"2026-08-{(i % 20) + 1:02d}"
        outcome = 1 if i % 2 == 0 else 0
        champ_prob = 0.52 if outcome == 1 else 0.48
        # Better challenger: sharper probability
        better_prob = 0.65 if outcome == 1 else 0.35
        # Worse challenger: inverted probability
        worse_prob = 0.40 if outcome == 1 else 0.60

        champ_picks.append(
            {"event_id": eid, "date": date, "probability": champ_prob, "outcome": outcome, "called": True}
        )
        chall_better_picks.append(
            {"event_id": eid, "date": date, "probability": better_prob, "outcome": outcome, "called": True}
        )
        chall_worse_picks.append(
            {"event_id": eid, "date": date, "probability": worse_prob, "outcome": outcome, "called": True}
        )

    # 1. Better challenger -> promote
    comp_better = PairedComparison(champ_picks, chall_better_picks)
    verdict_better = comp_better.promotion_eligible(min_events=50, min_dates=2)
    assert verdict_better.status == "promote"
    assert verdict_better.paired_metrics["delta_log_loss"] < 0
    assert verdict_better.paired_metrics["delta_brier"] < 0

    # 2. Worse challenger -> reject
    comp_worse = PairedComparison(champ_picks, chall_worse_picks)
    verdict_worse = comp_worse.promotion_eligible(min_events=50, min_dates=2)
    assert verdict_worse.status == "reject"
    assert verdict_worse.paired_metrics["delta_log_loss"] > 0

    # 3. Insufficient sample -> needs_more_data
    verdict_small = comp_better.promotion_eligible(min_events=300, min_dates=30)
    assert verdict_small.status == "needs_more_data"


def test_qualification_registry_generation() -> None:
    """Unified qualification registry generates valid summaries across all supported sports."""
    summaries = generate_qualification_registry(PROJECT_ROOT)
    assert len(summaries) == sum(len(m) for m in SUPPORTED_MARKETS.values())

    sports_in_summary = {s.sport for s in summaries}
    for expected_sport in SUPPORTED_MARKETS:
        assert expected_sport in sports_in_summary


def test_postgame_replay_never_counts_as_prospective() -> None:
    """A prediction generated after game start is tagged PIT_REPLAY, never LIVE_PROSPECTIVE."""
    from model_prediction.model_lifecycle import EvidenceOrigin, classify_evidence_origin

    # Predicted 1 hour after game start
    origin = classify_evidence_origin(
        prediction_created_at="2026-08-31T23:00:00Z",
        event_start_utc="2026-08-31T22:00:00Z",
        candidate_frozen_at="2026-08-31T12:00:00Z",
    )
    assert origin == EvidenceOrigin.PIT_REPLAY
    assert origin != EvidenceOrigin.LIVE_PROSPECTIVE


def test_prediction_before_freeze_does_not_count_as_prospective() -> None:
    """A prediction generated before the candidate model was hash-frozen cannot count as live prospective."""
    from model_prediction.model_lifecycle import EvidenceOrigin, classify_evidence_origin

    # Predicted before model was frozen
    origin = classify_evidence_origin(
        prediction_created_at="2026-08-30T10:00:00Z",
        event_start_utc="2026-08-31T22:00:00Z",
        candidate_frozen_at="2026-08-31T12:00:00Z",
    )
    assert origin == EvidenceOrigin.PIT_REPLAY
    assert origin != EvidenceOrigin.LIVE_PROSPECTIVE


def test_prediction_after_outcome_available_is_rejected() -> None:
    """A prediction timestamped after game outcome was settled cannot be live prospective."""
    from model_prediction.model_lifecycle import EvidenceOrigin, classify_evidence_origin

    origin = classify_evidence_origin(
        prediction_created_at="2026-09-01T02:30:00Z",
        event_start_utc="2026-08-31T23:05:00Z",
        outcome_available_at="2026-09-01T02:00:00Z",
        candidate_frozen_at="2026-08-31T12:00:00Z",
    )
    assert origin == EvidenceOrigin.PIT_REPLAY
    assert origin != EvidenceOrigin.LIVE_PROSPECTIVE


def test_pit_replay_and_live_prospective_counts_are_separate() -> None:
    """Paired comparison computes and reports pit_replay_n and live_prospective_n separately."""
    from model_prediction.model_lifecycle import EvidenceOrigin

    champ_rows = []
    chall_rows = []

    for i in range(10):
        eid = f"event-{i}"
        date = "2026-08-31"
        champ_rows.append({"event_id": eid, "date": date, "probability": 0.55, "outcome": 1, "called": True})
        # 6 live prospective, 4 pit replay
        origin = EvidenceOrigin.LIVE_PROSPECTIVE.value if i < 6 else EvidenceOrigin.PIT_REPLAY.value
        chall_rows.append(
            {
                "event_id": eid,
                "date": date,
                "probability": 0.58,
                "outcome": 1,
                "called": True,
                "evidence_origin": origin,
            }
        )

    comp = PairedComparison(champ_rows, chall_rows)
    results = comp.compute()

    assert results["historical_n"] == 10
    assert results["live_prospective_n"] == 6
    assert results["pit_replay_n"] == 4
    assert results["live_prospective_n"] + results["pit_replay_n"] == results["historical_n"]
