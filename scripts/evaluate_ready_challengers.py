"""Generic, Registry-Driven Champion-Challenger Evaluation Battery.

Dynamically evaluates all challenger models marked EVALUATION_READY in the
Unified Qualification Registry against their respective production champions.
Enforces strict paired evaluation, provenance classification, and artifact persistence.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from model_prediction.champion_challenger import PairedComparison, load_settled_predictions
from model_prediction.model_lifecycle import (
    NextAction,
    load_challenger_evidence,
)
from model_prediction.qualification_registry import generate_qualification_registry

logger = logging.getLogger(__name__)


def evaluate_market_challenger(
    root: Path,
    sport: str,
    market: str,
    champ_id: str,
    chall_id: str,
) -> dict[str, Any]:
    """Dynamically evaluate a single market's champion vs challenger."""
    # 1. Load settled champion predictions
    settled_champ = load_settled_predictions(sport, market, repo_root=root, model_version=champ_id)
    settled_map = {
        r.get("event_id") or r.get("game_id"): r
        for r in settled_champ
        if (r.get("event_id") or r.get("game_id")) and r.get("result") in {"win", "loss", "push"}
    }

    # 2. Load challenger predictions & classify provenance
    chall_evidence = load_challenger_evidence(
        sport=sport,
        market=market,
        challenger_model_id=chall_id,
        repo_root=root,
    )
    chall_map = {r.get("event_id"): r for r in chall_evidence if r.get("event_id")}

    common_eids = sorted(set(settled_map) & set(chall_map))

    champ_rows = []
    chall_rows = []

    for eid in common_eids:
        r_champ = settled_map[eid]
        r_chall = chall_map[eid]
        date = r_champ.get("event_start_utc") or r_champ.get("observed_at_utc") or ""
        outcome = 1 if r_champ.get("result") == "win" else 0

        champ_rows.append(
            {
                "event_id": eid,
                "date": date,
                "probability": float(r_champ.get("model_probability") or 0.5),
                "outcome": outcome,
                "called": True,
                "evidence_origin": r_champ.get("evidence_origin", "historical_backtest"),
            }
        )
        chall_rows.append(
            {
                "event_id": eid,
                "date": date,
                "probability": float(r_chall.get("model_prob") or r_chall.get("probability") or 0.5),
                "outcome": outcome,
                "called": True,
                "evidence_origin": r_chall.get("evidence_origin", "pit_replay"),
            }
        )

    if not champ_rows:
        return {
            "sport": sport,
            "market": market,
            "champion": champ_id,
            "challenger": chall_id,
            "status": "AWAITING_PAIRED_REPLAY_DATA",
            "n_paired": 0,
            "pit_replay_n": 0,
            "live_prospective_n": 0,
            "verdict": "CONTINUE",
            "recommendation": f"Challenger {chall_id} is IMPLEMENTED; awaiting walk-forward PIT replay dataset generation.",
        }

    comparison = PairedComparison(champ_rows, chall_rows)
    metrics = comparison.compute()
    verdict = comparison.promotion_eligible(min_events=50, min_dates=2)

    return {
        "sport": sport,
        "market": market,
        "champion": champ_id,
        "challenger": chall_id,
        "status": "EVALUATED",
        "n_paired": metrics["n_events"],
        "n_dates": metrics["n_dates"],
        "pit_replay_n": metrics["pit_replay_n"],
        "live_prospective_n": metrics["live_prospective_n"],
        "metrics": metrics,
        "verdict": verdict.status.upper(),
        "recommendation": verdict.recommendation,
    }


def evaluate_all_ready_challengers(root: Path | None = None) -> list[dict[str, Any]]:
    """Scan qualification registry and execute paired evaluations for all ready challengers."""
    repo_root = root or Path(__file__).resolve().parent.parent
    summaries = generate_qualification_registry(repo_root)

    results: list[dict[str, Any]] = []

    for summary in summaries:
        if summary.next_action != NextAction.RUN_OFFLINE_EVALUATION.value:
            continue
        if not summary.challenger_model_id:
            continue

        res = evaluate_market_challenger(
            root=repo_root,
            sport=summary.sport,
            market=summary.market,
            champ_id=summary.champion_model_id,
            chall_id=summary.challenger_model_id,
        )
        results.append(res)

    return results


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    print("# Registry-Driven Champion-Challenger Evaluation Battery\n")

    results = evaluate_all_ready_challengers(root)

    for r in results:
        print(f"## {r['sport']} / {r['market']}")
        print(f"- **Champion**: `{r['champion']}`")
        print(f"- **Challenger**: `{r['challenger']}`")
        print(
            f"- **Paired Sample**: {r.get('n_paired', 0)} (PIT Replay: {r.get('pit_replay_n', 0)}, Live Prosp: {r.get('live_prospective_n', 0)})"
        )
        print(f"- **Verdict**: **{r['verdict']}**")
        print(f"- **Recommendation**: {r['recommendation']}\n")

    output_path = root / "outputs/research/champion_challenger_evaluation_battery.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Results saved to {output_path}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
