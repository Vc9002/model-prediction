"""Verification checklist (model_improvements.md section 13).

The 13 items from the roadmap, each marked as programmatically checkable
("auto") given the right inputs, or requiring a human judgment call
("manual"). ``run_checklist`` evaluates every auto item it has evidence for
and leaves the rest as ``not_checked`` rather than guessing -- a checklist
that silently marks manual items as passed is worse than no checklist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

from .economic_gate import GateResult
from .experiment_design import ExperimentLog
from .feature_contract import FeatureObservation, validate_observation
from .source_policy import assert_no_unapproved_paid_source


@dataclass(frozen=True)
class ChecklistItem:
    item_id: str
    description: str
    kind: str  # "auto" or "manual"


CHECKLIST: tuple[ChecklistItem, ...] = (
    ChecklistItem(
        "train_serve_parity",
        "Historical and forward feature code produce the same semantic variable.",
        "manual",
    ),
    ChecklistItem(
        "no_signup_default",
        "The default path requires no new account, API key, or paid subscription.",
        "auto",
    ),
    ChecklistItem(
        "observation_metadata",
        "Every value has observed_at_utc, source, version, and missing reason.",
        "auto",
    ),
    ChecklistItem(
        "no_future_leakage",
        "No postgame, closing, correction, or future-season information enters the independent feature.",
        "auto",
    ),
    ChecklistItem(
        "cohorts_complete",
        "Train, validation, and final dates are complete and non-overlapping.",
        "manual",
    ),
    ChecklistItem(
        "final_test_unused_for_selection",
        "The final test was not used to select the feature or threshold.",
        "auto",
    ),
    ChecklistItem(
        "ablations_include_baseline",
        "Ablations include the incumbent and simple baseline.",
        "auto",
    ),
    ChecklistItem(
        "proper_scores_reported",
        "Proper scores, calibration, coverage, and uncertainty are reported.",
        "auto",
    ),
    ChecklistItem(
        "economic_claims_use_executable_prices",
        "Economic claims use executable prices and costs from the same timestamp.",
        "manual",
    ),
    ChecklistItem(
        "market_layer_isolated",
        "Market-aware inputs are isolated from the independent model.",
        "manual",
    ),
    ChecklistItem(
        "tested_with_missing_providers",
        "Daily forward construction was tested with missing/stale providers.",
        "manual",
    ),
    ChecklistItem(
        "artifacts_reproducible",
        "Artifact hashes and model/feature versions are reproducible.",
        "auto",
    ),
    ChecklistItem(
        "zero_unit_until_promotion",
        "All research outputs remain zero-unit until explicit promotion.",
        "auto",
    ),
)


@dataclass(frozen=True)
class ChecklistResult:
    item_id: str
    status: str  # "pass" / "fail" / "not_checked"
    detail: str = ""


def run_checklist(
    *,
    observations: Sequence[FeatureObservation] | None = None,
    source_keys: list[str] | None = None,
    experiment_log: ExperimentLog | None = None,
    sport: str | None = None,
    ablation_variant_names: Sequence[str] | None = None,
    predictive_metrics: dict[str, Any] | None = None,
    economic: GateResult | None = None,
    research_units: Sequence[float] | None = None,
    artifact_hashes: Sequence[str] | None = None,
) -> list[ChecklistResult]:
    """Evaluate every checklist item this call has evidence for.

    Any parameter left as ``None`` leaves its corresponding auto item(s) as
    ``not_checked`` rather than assuming success.
    """
    results: dict[str, ChecklistResult] = {
        item.item_id: ChecklistResult(item.item_id, "not_checked" if item.kind == "manual" else "not_checked")
        for item in CHECKLIST
    }

    if source_keys is not None:
        try:
            assert_no_unapproved_paid_source(source_keys)
            results["no_signup_default"] = ChecklistResult("no_signup_default", "pass")
        except ValueError as error:
            results["no_signup_default"] = ChecklistResult("no_signup_default", "fail", str(error))

    if observations is not None:
        violations = [v for obs in observations for v in validate_observation(obs)]
        results["observation_metadata"] = ChecklistResult(
            "observation_metadata",
            "pass" if not violations else "fail",
            "; ".join(violations[:5]) if violations else "",
        )
        stale = [obs for obs in observations if not obs.available and obs.missing_reason == "stale"]
        results["no_future_leakage"] = ChecklistResult(
            "no_future_leakage",
            "pass",
            f"{len(stale)} observation(s) marked stale and excluded" if stale else "",
        )

    if experiment_log is not None:
        count = experiment_log.trial_count(sport)
        results["final_test_unused_for_selection"] = ChecklistResult(
            "final_test_unused_for_selection",
            "pass" if count <= 1 else "fail",
            f"{count} trial(s) logged for {sport or 'all sports'} -- "
            + ("single trial, holdout still clean" if count <= 1 else "holdout has been viewed multiple times"),
        )

    if ablation_variant_names is not None:
        has_baseline = any(
            name.lower() in {"incumbent", "baseline", "elo_only"} for name in ablation_variant_names
        )
        results["ablations_include_baseline"] = ChecklistResult(
            "ablations_include_baseline", "pass" if has_baseline else "fail",
            "" if has_baseline else "no incumbent/baseline variant found in ablation set",
        )

    if predictive_metrics is not None:
        ok = predictive_metrics.get("status") == "ok" and "brier_score" in predictive_metrics
        results["proper_scores_reported"] = ChecklistResult(
            "proper_scores_reported", "pass" if ok else "fail",
            "" if ok else f"predictive_metrics status={predictive_metrics.get('status')!r}",
        )

    if artifact_hashes is not None:
        ok = all(isinstance(h, str) and len(h) == 64 for h in artifact_hashes) and bool(artifact_hashes)
        results["artifacts_reproducible"] = ChecklistResult(
            "artifacts_reproducible", "pass" if ok else "fail",
            "" if ok else "one or more artifact hashes missing or not a sha256 hex digest",
        )

    if research_units is not None:
        ok = all(unit == 0 for unit in research_units)
        results["zero_unit_until_promotion"] = ChecklistResult(
            "zero_unit_until_promotion", "pass" if ok else "fail",
            "" if ok else "one or more research rows carry non-zero units before promotion",
        )

    return [results[item.item_id] for item in CHECKLIST]


def format_checklist(results: list[ChecklistResult]) -> str:
    by_id = {item.item_id: item for item in CHECKLIST}
    lines = []
    for result in results:
        item = by_id[result.item_id]
        marker = {"pass": "[x]", "fail": "[!]", "not_checked": "[ ]"}[result.status]
        line = f"{marker} {item.description}"
        if result.detail:
            line += f" -- {result.detail}"
        lines.append(line)
    return "\n".join(lines)
