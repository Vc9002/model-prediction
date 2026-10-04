"""Generic, Registry-Driven Champion-Challenger Evaluation Battery.

Dynamically evaluates all challenger models marked EVALUATION_READY in the
Unified Qualification Registry against their respective production champions.
Enforces strict paired evaluation, provenance classification, and artifact persistence.
"""

from __future__ import annotations

import json
import logging
import math
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from model_prediction.champion_challenger import PairedComparison
from model_prediction.model_lifecycle import (
    NextAction,
)
from model_prediction.models.learned_market import LearnedMarketArtifact
from model_prediction.qualification_registry import generate_qualification_registry
from model_prediction.research_incumbent_evaluation import (
    deduplicate,
    probability,
    read_records,
    replay,
    timestamp,
)
from model_prediction.runtime_paths import RuntimePaths

logger = logging.getLogger(__name__)


def decision_context(row: dict[str, Any]) -> tuple[Any, ...]:
    """Exact selected contract and decision time; event ID alone is insufficient."""
    payload = json.loads(row["decision_payload_json"])
    start = timestamp(row["event_start_utc"])
    observed = timestamp(payload.get("observed_at_utc") or row["created_at_utc"])
    if observed >= start:
        raise ValueError("decision_not_pregame")
    line = row.get("line")
    if row["market_type"] in {"spread", "total", "nrfi"}:
        if line is None or line == "" or isinstance(line, bool) or not math.isfinite(float(line)):
            raise ValueError("missing_or_invalid_exact_line")
        line = float(line)
    elif line not in (None, ""):
        raise ValueError("unexpected_line")
    else:
        line = None
    if not row.get("event_id") or not payload.get("home_team") or not payload.get("away_team"):
        raise ValueError("missing_participant_identity")
    allowed = {
        "moneyline": {"home", "away", "draw"},
        "spread": {"home", "away"},
        "total": {"over", "under"},
        "nrfi": {"nrfi", "yrfi"},
    }
    if row["selection"] not in allowed.get(row["market_type"], set()):
        raise ValueError("unknown_selection")
    if not row.get("model_artifact_hash"):
        raise ValueError("missing_artifact_identity")
    return (
        str(row["event_id"]),
        start.isoformat(),
        row["market_type"],
        row["selection"],
        line,
        observed.isoformat(),
        payload["home_team"],
        payload["away_team"],
    )


def paired_decisions(
    champion: list[dict[str, Any]], challenger: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    excluded: Counter[str] = Counter()

    def index(rows: list[dict[str, Any]], side: str) -> dict[tuple[Any, ...], dict[str, Any]]:
        groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for row in rows:
            try:
                if row.get("result") not in {"win", "loss"}:
                    raise ValueError("push_or_unsettled")
                probability(row.get("model_probability"))
                key = decision_context(row)
                groups.setdefault(key, []).append(row)
            except (ValueError, KeyError, TypeError):
                excluded[f"{side}_invalid_or_push"] += 1
        output = {}
        for key, values in groups.items():
            if (
                len({(v["model_artifact_hash"], float(v["model_probability"]), v["result"]) for v in values})
                != 1
            ):
                excluded[f"{side}_conflicting_context"] += 1
            else:
                output[key] = values[0]
        return output

    champions, challengers = index(champion, "champion"), index(challenger, "challenger")
    # First incumbent decision per event, chosen before availability of a
    # challenger match. No retrospective selection of the convenient timestamp.
    earliest: dict[str, tuple[Any, ...]] = {}
    for key in sorted(champions, key=lambda k: (k[5], str(k))):
        earliest.setdefault(key[0], key)
    champ_rows, chall_rows = [], []
    for key in earliest.values():
        champ, chall = champions[key], challengers.get(key)
        if chall is None:
            excluded["no_exact_challenger_context"] += 1
            continue
        if champ["result"] != chall["result"]:
            excluded["outcome_disagreement"] += 1
            continue
        common = {
            "event_id": key[0],
            "date": timestamp(key[1]).date().isoformat(),
            "outcome": int(champ["result"] == "win"),
            "called": True,
            "evidence_origin": "historical_backtest",
        }
        champ_rows.append(
            common
            | {
                "probability": probability(champ["model_probability"]),
                "called": champ.get("decision") == "CALL",
            }
        )
        chall_rows.append(
            common
            | {
                "probability": probability(chall["model_probability"]),
                "called": chall.get("decision") == "CALL",
            }
        )
    return champ_rows, chall_rows, dict(excluded)


def evaluate_market_challenger(
    root: Path,
    sport: str,
    market: str,
    champ_id: str,
    chall_id: str,
    *,
    database: Path | None = None,
) -> dict[str, Any]:
    """Compare canonical settled decisions only after incumbent reproduction.

    Horizon rows require their own artifact/source replay pipeline; this entry
    point no longer silently substitutes an XLSX cache or guesses their labels.
    Statistical comparisons here do not establish executable profitability.
    """
    database = database or RuntimePaths.resolve(repo_root=root, require_external_runtime=True).ledgers_db
    records = read_records(database, [champ_id, chall_id])
    records = [r for r in records if r["sport"].upper() == sport.upper() and r["market_type"] == market]
    rows, duplicate_counts = deduplicate(records)
    champion = [r for r in rows if r["model_id"] == champ_id]
    challenger = [r for r in rows if r["model_id"] == chall_id]
    base = {
        "sport": sport,
        "market": market,
        "champion": champ_id,
        "challenger": chall_id,
        "source_database": str(database.resolve()),
        "deduplication": duplicate_counts,
        "champion_rows": len(champion),
        "challenger_rows": len(challenger),
        "n_paired": 0,
        "pit_replay_n": 0,
        "live_prospective_n": 0,
        "metrics": None,
        "economics": None,
        "promote": False,
    }
    artifact = None
    if market == "moneyline" and sport.upper() in {"WNBA", "MLB", "NBA", "NFL"}:
        registry = yaml.safe_load((root / "config/production.yaml").read_text())["prediction_service"][
            "models"
        ]
        registered = next((r for r in registry if r["model_id"] == champ_id), None)
        if registered is not None:
            artifact = LearnedMarketArtifact.load(root / registered["artifact"])
    parities = [replay(r, artifact) for r in champion]
    replay_counts = dict(Counter(r["status"] for r in parities))
    base["replay_counts"] = replay_counts
    if (
        not champion
        or any(r["status"] != "PASS" for r in parities)
        or duplicate_counts.get("conflicting_context_rows")
        or duplicate_counts.get("invalid_context")
    ):
        return base | {
            "status": "AWAITING_PAIRED_REPLAY_DATA",
            "verdict": "COMPARISON_BLOCKED",
            "incumbent_reproduction": "NO_DATA" if not champion else "FAIL_OR_UNSUPPORTED",
            "recommendation": "Recover exact incumbent inputs/artifacts and resolve context conflicts before ranking candidates.",
        }
    if sport.upper() in {"KBO", "NPB"}:
        return base | {
            "status": "AWAITING_SETTLEMENT_VALUE_EVALUATION",
            "verdict": "COMPARISON_BLOCKED",
            "incumbent_reproduction": "PASS",
            "recommendation": "These ledger probabilities are expected settlement values with half-value draws. Use three-way outcomes and exact settlement economics, not binary win/loss calibration.",
        }
    champ_rows, chall_rows, exclusions = paired_decisions(champion, challenger)
    base.update(incumbent_reproduction="PASS", pair_exclusions=exclusions)
    if not champ_rows:
        return base | {
            "status": "AWAITING_PAIRED_REPLAY_DATA",
            "verdict": "CONTINUE",
            "recommendation": "No exact settled challenger decisions match the incumbent's first event decisions.",
        }
    metrics = PairedComparison(champ_rows, chall_rows).compute()
    return base | {
        "status": "EVALUATED_RESEARCH_ONLY",
        "n_paired": metrics["n_events"],
        "n_dates": metrics["n_dates"],
        "metrics": metrics,
        "verdict": "VALIDATION_REQUIRED",
        "recommendation": "Paired retrospective metrics only. Candidate artifact/source validation and matched executable economics are still required.",
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

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output_path = root / f"outputs/research/champion_challenger_evaluation_battery_{stamp}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(results, indent=2, allow_nan=False) + "\n")
    print(f"Results saved to {output_path}")
    return 0 if results and all(r["status"] == "EVALUATED_RESEARCH_ONLY" for r in results) else 2


if __name__ == "__main__":
    import sys

    sys.exit(main())
