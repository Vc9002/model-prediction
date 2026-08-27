"""Deep audit of every feature and every model artifact (read-only).

Feature layer: for each feature name, report whether it exists as a
walk-forward row field, appears in a training variant, is served at
prediction time (inline / dispatch / provider), carries an availability
flag, and has direct test coverage.

Model layer: every JSON artifact under config/models/ (production,
frozen, challengers) self-hash-checked and cross-referenced against
production.yaml wiring and serving feature coverage; rebuild and
archived models listed with their status.

Run from the research worktree with the runtime env for ledger reads.
"""

from __future__ import annotations

import dataclasses
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction import learned_forward
from model_prediction.config import PROJECT_ROOT
from model_prediction.production_canary import load_production_config
from model_prediction.production_registry import compute_artifact_hash
from model_prediction.runtime_paths import RuntimePaths
from model_prediction.validation import FEATURE_VARIANTS, ValidationRow

OUT = PROJECT_ROOT / "outputs" / "research" / "feature_model_audit.json"

# Documented known-mismatch artifacts — deliberate, never re-signed
# evidence. The stored artifact_hash records the payload at signing time;
# later provenance annotations are appended WITHOUT re-signing so the
# edit itself stays detectable. Re-signing archived/quarantine evidence
# is prohibited (docs/FEATURE_MODEL_AUDIT.md "Archive integrity"; the
# quarantine commits preserve the candidate for audit). These are not
# corruption findings:
#   - archive/wnba-spread-baseline-v1.json: `_retired*` fields appended at
#     archive time.
#   - research/mlb-v9-candidate-1.json: status/invalidation_reason/
#     replacement appended by the quarantine commit (preserved for audit,
#     never promoted). Its embedded hash was written under the writer's
#     default-separators convention, so it never matched
#     compute_artifact_hash even at generation.
KNOWN_MISMATCH_ARTIFACTS = frozenset(
    {
        "config/models/archive/wnba-spread-baseline-v1.json",
        "config/models/research/mlb-v9-candidate-1.json",
    }
)


def _serving_feature_names() -> set[str]:
    """Every feature learned_forward can produce at prediction time."""
    src = open(PROJECT_ROOT / "src/model_prediction/learned_forward.py").read()
    body = src[src.index("def _compute_features") : src.index("def _init_providers")]
    served = set(re.findall(r'features\["([a-z_0-9]+)"\]', body))
    served |= set(re.findall(r'"([a-z_0-9]+)":', body))  # dict-literal keys (elo/trend/defense)
    served |= set(re.findall(r'if "([a-z_0-9]+)" in wanted', body))
    # schedule features are served via the dynamic matchup_schedule_load
    # update (features.update({name: value ...})) — not literal assignments
    if "matchup_schedule_load" in body:
        served |= {"rest_disparity", "back_to_back_gap", "games_last_7_gap", "schedule_available"}
    learned_forward._init_providers()
    served |= set(learned_forward._FEATURE_PROVIDERS)
    return served


def main() -> int:
    row_fields = {f.name for f in dataclasses.fields(ValidationRow)}
    served = _serving_feature_names()
    variant_features: dict[str, list[str]] = {}
    for name, feats in FEATURE_VARIANTS.items():
        if name != "soccer_3way":
            variant_features[name] = list(feats)
    all_variant = {f for feats in variant_features.values() for f in feats}

    # direct test coverage: feature names mentioned in tests/
    test_mentions: dict[str, int] = {}
    for test_file in Path("tests").rglob("*.py"):
        text = test_file.read_text(encoding="utf-8", errors="ignore")
        for feature in sorted(row_fields | all_variant):
            # quoted literal OR its serving function (starter_history's
            # *_gap_live helpers) counts as direct coverage
            if f'"{feature}"' in text or f"'{feature}'" in text or f"{feature}_live" in text:
                test_mentions[feature] = test_mentions.get(feature, 0) + 1

    features_report = {}
    gaps = []
    for feature in sorted(row_fields | all_variant):
        in_row = feature in row_fields
        in_variant = [v for v, fs in variant_features.items() if feature in fs]
        is_served = feature in served
        has_availability = f"{feature.replace('_gap', '')}_available" in row_fields or (
            feature
            in (
                "park_factor",
                "weather_factor",
                "bullpen_weakness_gap",
                "bullpen_fatigue_gap",
                "probable_starter_era_gap",
            )
        )
        features_report[feature] = {
            "in_row": in_row,
            "variants": in_variant,
            "served": is_served,
            "availability_flag": has_availability,
            "test_files": test_mentions.get(feature, 0),
        }
        if in_variant and not is_served:
            gaps.append(f"FEATURE GAP: {feature} is in variants {in_variant} but has no serving path")
        if in_variant and not in_row:
            gaps.append(f"FEATURE GAP: {feature} is in variants {in_variant} but is not a row field")
        if in_variant and not test_mentions.get(feature):
            gaps.append(f"FEATURE GAP: {feature} has zero direct test mentions")

    # model layer: every JSON artifact under config/models/
    models_report = {}
    for path in sorted((PROJECT_ROOT / "config/models").rglob("*.json")):
        rel = str(path.relative_to(PROJECT_ROOT))
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            models_report[rel] = {"error": str(error)}
            continue
        stored = payload.get("artifact_hash")
        computed = compute_artifact_hash(payload) if isinstance(payload, dict) else None
        model_version = payload.get("model_version") if isinstance(payload, dict) else None
        feature_names = (
            ((payload.get("market_models") or {}).get("moneyline") or {}).get("feature_names")
            if isinstance(payload, dict)
            else None
        ) or []
        unserved = [f for f in feature_names if f not in served]
        models_report[rel] = {
            "hash_valid": stored == computed,
            "known_hash_mismatch": rel in KNOWN_MISMATCH_ARTIFACTS,
            "model_version": model_version,
            "feature_names": feature_names,
            "unserved_features": unserved,
            "qualified": (
                (payload.get("qualification") or {}).get("qualified") if isinstance(payload, dict) else None
            ),
        }
        if stored is not None and stored != computed and rel not in KNOWN_MISMATCH_ARTIFACTS:
            gaps.append(f"MODEL GAP: {rel} hash mismatch")
        if unserved:
            if "rebuild" not in str(model_version):
                gaps.append(f"MODEL GAP: {rel} ({model_version}) uses unserved features {unserved}")

    config = load_production_config(repo_root=PROJECT_ROOT)
    production_entries = config.get("prediction_service", {}).get("models", [])
    wired_paths = {str(e.get("artifact")) for e in production_entries if e.get("artifact")}
    # config/model.yaml wires the research/market artifacts separately
    import yaml as _yaml

    model_yaml = _yaml.safe_load((PROJECT_ROOT / "config/model.yaml").read_text(encoding="utf-8")) or {}
    extra_refs = set()
    for section in model_yaml.values():
        if isinstance(section, dict):
            for value in section.values():
                if isinstance(value, str) and value.startswith("config/models/"):
                    extra_refs.add(value)
                elif isinstance(value, dict):
                    for v2 in value.values():
                        if isinstance(v2, str) and v2.startswith("config/models/"):
                            extra_refs.add(v2)
    rollback_refs = {
        str(e.get("rollback_model")) + ".json" for e in production_entries if e.get("rollback_model")
    }
    for rel in models_report:
        if (
            "challengers/" not in rel
            and "archive/" not in rel
            and not rel.endswith(".previous.json")
            and rel not in wired_paths
            and rel not in extra_refs
            and Path(rel).name not in rollback_refs
        ):
            gaps.append(
                f"MODEL GAP: {rel} is not wired into production.yaml/config/model.yaml and is not a challenger"
            )

    # ledger evidence per model_version
    paths = RuntimePaths.resolve(repo_root=PROJECT_ROOT)
    conn = sqlite3.connect(paths.ledgers_db)
    for rel, info in models_report.items():
        version = info.get("model_version")
        if version:
            n = conn.execute("SELECT count(*) FROM ledger_records WHERE model_id = ?", (version,)).fetchone()[
                0
            ]
            info["ledger_rows"] = n
    conn.close()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {"features": features_report, "models": models_report, "gaps": gaps}, indent=2, default=str
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"features audited: {len(features_report)} | models audited: {len(models_report)}")
    print(f"gaps found: {len(gaps)}")
    for gap in gaps:
        print(" -", gap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
