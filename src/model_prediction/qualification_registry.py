"""Unified Model Qualification Registry and Research Control Plane.

Exposes explicit, non-positional provenance fields:
- historical_backtest_n
- pit_replay_n
- live_prospective_n
- synthetic_n
- required_live_prospective_n
- build_status (PLANNED | IMPLEMENTED | VALIDATED_OFFLINE | FROZEN | CAPTURING_PROSPECTIVE)
- evidence_status (UNVERIFIED | HISTORICAL_ONLY | PREDICTIVELY_QUALIFIED | MARKET_QUALIFIED | PROSPECTIVELY_QUALIFIED | DEGRADED)
- replacement_priority (LOW | MEDIUM | HIGH | CRITICAL)
- verdict (PROMOTE | CONTINUE | REJECT | SERVING_HEALTHY | REQUIRES_REPLACEMENT | EVALUATION_READY)
- next_action (BUILD_CHALLENGER | RUN_OFFLINE_EVALUATION | FREEZE_CHALLENGER | START_PROSPECTIVE_CAPTURE | COLLECT_PROSPECTIVE | RUN_FINAL_GATE | START_NEXT_GENERATION)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .champion_challenger import load_settled_predictions
from .config import PROJECT_ROOT
from .model_lifecycle import (
    SUPPORTED_MARKETS,
    ChallengerBuildStatus,
    EvidenceOrigin,
    EvidenceStatus,
    NextAction,
    ReplacementPriority,
    ServingStatus,
)
from .production_registry import ProductionModelRegistry


@dataclass(frozen=True)
class MarketQualificationSummary:
    """Explicit qualification status and research control plane state for one sport/market."""

    sport: str
    market: str
    champion_model_id: str
    challenger_model_id: str | None
    build_status: str
    rollback_model_id: str | None
    serving_status: str
    evidence_status: str
    replacement_priority: str
    champion_artifact_hash: str | None
    challenger_artifact_hash: str | None
    historical_backtest_n: int
    pit_replay_n: int
    live_prospective_n: int
    synthetic_n: int
    required_live_prospective_n: int
    delta_logloss: float | None
    delta_brier: float | None
    p_better: float | None
    clv_diff: float | None
    verdict: str  # PROMOTE | CONTINUE | REJECT | SERVING_HEALTHY | REQUIRES_REPLACEMENT | EVALUATION_READY
    next_action: str  # BUILD_CHALLENGER | RUN_OFFLINE_EVALUATION | FREEZE_CHALLENGER | START_PROSPECTIVE_CAPTURE | COLLECT_PROSPECTIVE | RUN_FINAL_GATE | START_NEXT_GENERATION
    last_evaluated_utc: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sport": self.sport,
            "market": self.market,
            "champion_model_id": self.champion_model_id,
            "challenger_model_id": self.challenger_model_id,
            "build_status": self.build_status,
            "rollback_model_id": self.rollback_model_id,
            "serving_status": self.serving_status,
            "evidence_status": self.evidence_status,
            "replacement_priority": self.replacement_priority,
            "champion_artifact_hash": self.champion_artifact_hash,
            "challenger_artifact_hash": self.challenger_artifact_hash,
            "historical_backtest_n": self.historical_backtest_n,
            "pit_replay_n": self.pit_replay_n,
            "live_prospective_n": self.live_prospective_n,
            "synthetic_n": self.synthetic_n,
            "required_live_prospective_n": self.required_live_prospective_n,
            "delta_logloss": self.delta_logloss,
            "delta_brier": self.delta_brier,
            "p_better": self.p_better,
            "clv_diff": self.clv_diff,
            "verdict": self.verdict,
            "next_action": self.next_action,
            "last_evaluated_utc": self.last_evaluated_utc,
        }


def generate_qualification_registry(
    repo_root: Path | str | None = None,
) -> list[MarketQualificationSummary]:
    """Generate canonical qualification summaries for every supported sport and market."""
    root = Path(repo_root) if repo_root is not None else PROJECT_ROOT
    registry = ProductionModelRegistry.load(root)
    now_utc = datetime.now(UTC).isoformat()

    summaries: list[MarketQualificationSummary] = []

    for sport, markets in SUPPORTED_MARKETS.items():
        for market in sorted(markets):
            contract = registry.lifecycle_contract(sport, market)
            champ_entry = registry.champion(sport, market)
            chall_entry = registry.challenger(sport, market)

            champ_id = (
                contract.champion_model_id
                if contract
                else (champ_entry.model_id if champ_entry else "UNKNOWN")
            )
            chall_id = (
                contract.challenger_model_id if contract else (chall_entry.model_id if chall_entry else None)
            )
            rollback_id = (
                contract.rollback_model_id
                if contract
                else (champ_entry.rollback_model if champ_entry else None)
            )
            serving = (
                contract.serving_status
                if contract
                else (champ_entry.serving_status if champ_entry else ServingStatus.PRODUCTION.value)
            )
            evidence = (
                contract.evidence_status
                if contract
                else (champ_entry.evidence_status if champ_entry else EvidenceStatus.HISTORICAL_ONLY.value)
            )
            priority = (
                contract.replacement_priority
                if contract
                else (champ_entry.replacement_priority if champ_entry else ReplacementPriority.MEDIUM.value)
            )

            champ_hash = champ_entry.artifact_hash if champ_entry else None
            chall_hash = chall_entry.artifact_hash if chall_entry else None

            # Preregistered qualification requirements: Initial N >= 300, Full N >= 500
            required_live_prospective_n = 500 if sport in {"MLB", "WNBA", "NCAAF"} else 300

            # Classify settled picks provenance
            historical_backtest_n = 0
            pit_replay_n = 0
            live_prospective_n = 0
            synthetic_n = 0

            try:
                settled = load_settled_predictions(sport, market, repo_root=root, model_version=champ_id)
                for r in settled:
                    origin = r.get("evidence_origin")
                    if origin == EvidenceOrigin.LIVE_PROSPECTIVE.value:
                        live_prospective_n += 1
                    elif origin == EvidenceOrigin.HISTORICAL_BACKTEST.value:
                        historical_backtest_n += 1
                    elif origin == EvidenceOrigin.SYNTHETIC.value:
                        synthetic_n += 1
                    else:
                        pit_replay_n += 1
            except (OSError, ValueError, KeyError, RuntimeError):
                historical_backtest_n = 0
                pit_replay_n = 0
                live_prospective_n = 0
                synthetic_n = 0

            # Determine challenger build status and next action
            if chall_id is None:
                build_status = ChallengerBuildStatus.PLANNED.value
                verdict = (
                    "REQUIRES_REPLACEMENT" if evidence == EvidenceStatus.DEGRADED.value else "SERVING_HEALTHY"
                )
                next_action = (
                    NextAction.BUILD_CHALLENGER.value
                    if evidence == EvidenceStatus.DEGRADED.value
                    else NextAction.START_NEXT_GENERATION.value
                )
            elif chall_id in {
                "soccer-poisson-dc-v2",
                "tennis-surface-elo-v2",
                "cfb-structural-v2",
                "mlb-structural-runline-v4",
                "wnba-spread-structural-v3",
                "wnba-total-possession-v3",
                "mlb-moneyline-market-residual-v10",
                "mlb-moneyline-v9-residual",
            }:
                build_status = ChallengerBuildStatus.IMPLEMENTED.value
                verdict = "EVALUATION_READY"
                next_action = NextAction.RUN_OFFLINE_EVALUATION.value
            elif chall_id in {"mlb-moneyline-v9-frozen", "wnba-moneyline-v5"}:
                build_status = ChallengerBuildStatus.CAPTURING_PROSPECTIVE.value
                if live_prospective_n >= required_live_prospective_n:
                    verdict = "EVALUATION_READY"
                    next_action = NextAction.RUN_FINAL_GATE.value
                else:
                    verdict = "CONTINUE"
                    next_action = NextAction.COLLECT_PROSPECTIVE.value
            elif (root / "config" / "models" / f"{chall_id}.json").is_file() or (
                root / "config" / "models" / "research" / f"{chall_id}.json"
            ).is_file():
                build_status = ChallengerBuildStatus.FROZEN.value
                verdict = "CONTINUE"
                next_action = NextAction.START_PROSPECTIVE_CAPTURE.value
            else:
                build_status = ChallengerBuildStatus.PLANNED.value
                verdict = "CONTINUE"
                next_action = NextAction.BUILD_CHALLENGER.value

            summary = MarketQualificationSummary(
                sport=sport,
                market=market,
                champion_model_id=champ_id,
                challenger_model_id=chall_id,
                build_status=build_status,
                rollback_model_id=rollback_id,
                serving_status=serving,
                evidence_status=evidence,
                replacement_priority=priority,
                champion_artifact_hash=champ_hash,
                challenger_artifact_hash=chall_hash,
                historical_backtest_n=historical_backtest_n,
                pit_replay_n=pit_replay_n,
                live_prospective_n=live_prospective_n,
                synthetic_n=synthetic_n,
                required_live_prospective_n=required_live_prospective_n,
                delta_logloss=None,
                delta_brier=None,
                p_better=None,
                clv_diff=None,
                verdict=verdict,
                next_action=next_action,
                last_evaluated_utc=now_utc,
            )
            summaries.append(summary)

    return summaries


def format_qualification_markdown_table(
    summaries: list[MarketQualificationSummary],
) -> str:
    """Format qualification summaries as a GitHub markdown table with explicit named fields."""
    headers = [
        "Sport",
        "Market",
        "Champion",
        "Challenger",
        "Build Status",
        "PIT Replay N",
        "Live Prosp N",
        "Req Prosp N",
        "Evidence Status",
        "Priority",
        "Verdict",
        "Next Action",
    ]
    rows = [
        f"| {' | '.join(headers)} |",
        f"| {' | '.join(['---'] * len(headers))} |",
    ]

    for s in summaries:
        chall_display = f"`{s.challenger_model_id}`" if s.challenger_model_id else "—"
        row = [
            s.sport,
            s.market,
            f"`{s.champion_model_id}`",
            chall_display,
            s.build_status.upper(),
            str(s.pit_replay_n),
            str(s.live_prospective_n),
            str(s.required_live_prospective_n),
            s.evidence_status.upper(),
            s.replacement_priority.upper(),
            f"**{s.verdict}**",
            f"`{s.next_action}`",
        ]
        rows.append(f"| {' | '.join(row)} |")

    return "\n".join(rows)


def main() -> int:
    """Print the unified qualification report to stdout."""
    summaries = generate_qualification_registry()
    print("# Unified Production Model Qualification Registry (Research Control Plane)\n")
    print(format_qualification_markdown_table(summaries))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
