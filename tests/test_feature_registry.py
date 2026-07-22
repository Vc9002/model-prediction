from __future__ import annotations

import json
from pathlib import Path

from model_prediction.models.learned_market import artifact_hash


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config" / "tested_features.json"
ABLATION_PATH = ROOT / "config" / "models" / "production-feature-ablation-2026-07-22.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_feature_registry_is_complete_unique_and_points_to_existing_code() -> None:
    registry = _load(REGISTRY_PATH)
    features = registry["features"]
    names = [feature["name"] for feature in features]

    assert registry["schema_version"] == "1"
    assert registry["last_updated"] == "2026-07-22"
    assert len(features) == 23
    assert len(names) == len(set(names))
    assert registry["retention_policy"]["threshold"] == 0.0
    assert set(feature["verdict"] for feature in features) <= {
        "keep",
        "remove",
        "remove_candidate",
        "reject",
        "exclude",
        "untested",
    }
    for feature in features:
        assert feature["name"]
        assert feature["status"]
        assert feature["evidence_grade"] in registry["evidence_grades"]
        assert (ROOT / feature["file"]).is_file(), feature["name"]


def test_registry_ablation_summary_exactly_matches_hashed_source_and_retention_policy() -> None:
    registry = _load(REGISTRY_PATH)
    report = _load(ABLATION_PATH)
    stored_hash = report.pop("artifact_hash")
    assert stored_hash == artifact_hash(report)

    expected = []
    for sport, model in report["models"].items():
        if model["status"] != "evaluated":
            continue
        for feature, result in model["leave_one_out"].items():
            delta = result["paired_uncertainty"]["candidate_minus_baseline"]
            keep = result["validation_brier_delta"] > 0 or (
                delta["brier_score"] > 0 and delta["log_loss"] > 0
            )
            expected.append(
                {
                    "sport": sport,
                    "model_version": model["artifact_version"],
                    "feature": feature,
                    "strict_decision": result["decision"],
                    "retention_decision": "KEEP" if keep else "REMOVE CANDIDATE",
                    "validation_brier_delta": result["validation_brier_delta"],
                    "holdout_brier_delta": delta["brier_score"],
                    "holdout_log_loss_delta": delta["log_loss"],
                    "point_in_time_provenance": result["provenance"]["status"],
                    "production_safe": result["provenance"]["status"] == "verified",
                    "economic_claim_allowed": False,
                }
            )

    assert registry["production_ablation_summary"] == expected
    registered = {feature["name"] for feature in registry["features"]}
    assert {item["feature"] for item in expected} <= registered
    blocked_keeps = [
        item
        for item in expected
        if item["retention_decision"] == "KEEP" and item["point_in_time_provenance"] == "blocked"
    ]
    assert {(item["sport"], item["feature"]) for item in blocked_keeps} == {
        ("mlb", "park_factor"),
        ("mlb", "weather_factor"),
    }


def test_feature_registry_documentation_matches_machine_readable_counts() -> None:
    registry = _load(REGISTRY_PATH)
    docs = (ROOT / "docs" / "FEATURE_REGISTRY.md").read_text(encoding="utf-8")
    counts: dict[str, int] = {}
    for feature in registry["features"]:
        verdict = feature["verdict"]
        counts[verdict] = counts.get(verdict, 0) + 1

    assert "23 features tracked" in docs
    assert f"{counts['keep']} keep" in docs
    assert f"{counts['remove'] + counts['remove_candidate']} remove candidates" in docs
    assert f"{counts['reject']} reject" in docs
    assert f"{counts['exclude']} exclude" in docs
    assert f"{counts['untested']} untested" in docs
