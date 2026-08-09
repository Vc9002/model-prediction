"""Exact, fail-closed artifact bundle for the sealed MLB moneyline v2 test.

The prospective test is a test of one fitted candidate, not a moving daily
refit.  This module binds every fitted object and every provenance input needed
to reproduce its probabilities into one cryptographically identified bundle.
Runtime inference only loads this bundle; training remains an explicit offline
operation.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .calibration import Calibrator, load_calibrator
from .config import default_repo_root
from .mlb_features import MLB_DIFFERENTIAL_FEATURES, MLB_INTENSITY_FEATURES
from .models import BootstrapMLBEnsemble, MLBTwoHeadModel, XGBoostTwoHeadModel
from .safety import assert_challenger_artifact_path
from .xgboost_stress import XGBoostChallenger

MLB_V2_TEST_ID = "mlb_moneyline_v2"
MLB_V2_CANDIDATE_VERSION = "mlb_moneyline_v2_frozen_v1"
MLB_V2_BUNDLE_DIRNAME = MLB_V2_CANDIDATE_VERSION
MLB_V2_MANIFEST = "manifest.json"
MLB_V2_SCHEMA_VERSION = "1"
FROZEN_HEAD_FAMILY = "xgboost"
FROZEN_DISTRIBUTION_METHOD = "negative_binomial"
FROZEN_CALIBRATOR_ARTIFACT_NAME = "mlb-xgb_two_head_negative_binomial-calibrator-v1.json"
XGB_DIRECT_FEATURES = list(dict.fromkeys(MLB_INTENSITY_FEATURES + MLB_DIFFERENTIAL_FEATURES))


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def current_code_revision(repo_root: str | Path | None = None) -> str:
    """Return the exact checked-out revision or fail closed outside Git."""
    root = Path(repo_root).resolve() if repo_root is not None else default_repo_root()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("cannot verify frozen MLB v2 code revision") from exc
    revision = result.stdout.strip()
    if len(revision) != 40:
        raise ValueError("invalid frozen MLB v2 code revision")
    return revision


@dataclass(frozen=True)
class FrozenCalibratorBundle:
    calibrator: Calibrator
    calibrator_hash: str
    base_model_hash: str
    dataset_hash: str
    artifact_sha256: str
    oof_probs: list[float] = field(default_factory=list)
    oof_labels: list[int] = field(default_factory=list)


def load_frozen_calibrator(
    artifact_path: str | Path | None = None,
    *,
    challenger_root: str | Path | None = None,
) -> FrozenCalibratorBundle:
    """Load and self-verify the persisted calibrator without fitting."""
    root = Path(challenger_root).resolve() if challenger_root is not None else (
        default_repo_root() / "config" / "models" / "challengers"
    ).resolve()
    path = Path(artifact_path).resolve() if artifact_path is not None else root / FROZEN_CALIBRATOR_ARTIFACT_NAME
    path = assert_challenger_artifact_path(path, root)
    artifact = json.loads(path.read_text())
    required = {"method", "parameters", "base_model_hash", "dataset_hash", "calibrator_hash"}
    missing = sorted(required - artifact.keys())
    if missing:
        raise ValueError(f"calibrator artifact missing fields: {', '.join(missing)}")
    identity_payload = {
        key: value for key, value in artifact.items()
        if key not in {"calibrator_hash", "oof_probs", "oof_labels"}
    }
    actual_hash = hashlib.sha256(
        json.dumps(identity_payload, sort_keys=True, default=str).encode()
    ).hexdigest()
    if actual_hash != artifact["calibrator_hash"]:
        raise ValueError("calibrator artifact hash mismatch")
    if len(artifact.get("oof_probs", [])) != len(artifact.get("oof_labels", [])):
        raise ValueError("calibrator artifact OOF arrays are misaligned")
    return FrozenCalibratorBundle(
        calibrator=load_calibrator(artifact["method"], artifact["parameters"]),
        calibrator_hash=str(artifact["calibrator_hash"]),
        base_model_hash=str(artifact["base_model_hash"]),
        dataset_hash=str(artifact["dataset_hash"]),
        artifact_sha256=_sha256_file(path),
        oof_probs=list(artifact.get("oof_probs", [])),
        oof_labels=list(artifact.get("oof_labels", [])),
    )


@dataclass(frozen=True)
class FrozenMLBV2Bundle:
    primary: XGBoostTwoHeadModel
    bootstrap: BootstrapMLBEnsemble
    sklearn_baseline: MLBTwoHeadModel
    xgb_direct: XGBoostChallenger
    calibrator: FrozenCalibratorBundle
    bundle_hash: str
    dataset_hash: str
    training_cutoff_utc: str
    training_rows: int
    code_revision: str
    dependency_hash: str
    bundle_path: Path
    test_id: str = MLB_V2_TEST_ID
    candidate_version: str = MLB_V2_CANDIDATE_VERSION


def _component_receipt(path: Path, bundle_filename: str) -> dict[str, str]:
    return {
        "path": path.name,
        "metadata_sha256": _sha256_file(path / "metadata.json"),
        "bundle_sha256": _sha256_file(path / bundle_filename),
    }


def write_frozen_mlb_v2_bundle(
    target: str | Path,
    *,
    primary: XGBoostTwoHeadModel,
    bootstrap: BootstrapMLBEnsemble,
    sklearn_baseline: MLBTwoHeadModel,
    xgb_direct: XGBoostChallenger,
    calibrator_path: str | Path,
    dataset_hash: str,
    training_cutoff_utc: str,
    training_rows: int,
    code_revision: str,
    dependency_manifest: str | Path,
) -> Path:
    """Seal already-fitted objects into a new immutable candidate directory.

    This function never trains or selects anything.  The caller must supply the
    already-fitted, pre-test objects and provenance.  Existing targets are
    rejected so a sealed candidate cannot be overwritten in place.
    """
    out = Path(target).resolve()
    if out.exists():
        raise FileExistsError(f"frozen MLB v2 bundle already exists: {out}")
    if not dataset_hash or not training_cutoff_utc or training_rows <= 0:
        raise ValueError("dataset hash, training cutoff, and positive training rows are required")
    if len(code_revision) != 40:
        raise ValueError("code_revision must be an exact 40-character Git SHA")

    out.mkdir(parents=True)
    primary.save(out / "primary")
    bootstrap.save(out / "bootstrap")
    sklearn_baseline.save(out / "sklearn_baseline")
    xgb_direct.save(out / "xgb_direct")
    calibrator_target = out / "calibrator.json"
    shutil.copyfile(Path(calibrator_path), calibrator_target)
    calibrator = load_frozen_calibrator(calibrator_target, challenger_root=out)

    primary_metadata = json.loads((out / "primary" / "metadata.json").read_text())
    primary_model_hash = str(primary_metadata.get("artifact_hash", ""))
    if calibrator.base_model_hash != primary_model_hash:
        raise ValueError("calibrator base_model_hash is not bound to the frozen primary model schema")
    if calibrator.dataset_hash != dataset_hash:
        raise ValueError("calibrator dataset_hash does not match the frozen training dataset")

    dependency_path = Path(dependency_manifest).resolve()
    manifest: dict[str, Any] = {
        "schema_version": MLB_V2_SCHEMA_VERSION,
        "test_id": MLB_V2_TEST_ID,
        "candidate_version": MLB_V2_CANDIDATE_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "head_family": FROZEN_HEAD_FAMILY,
        "distribution_method": FROZEN_DISTRIBUTION_METHOD,
        "feature_order": {
            "intensity": list(MLB_INTENSITY_FEATURES),
            "differential": list(MLB_DIFFERENTIAL_FEATURES),
            "xgb_direct": list(XGB_DIRECT_FEATURES),
        },
        "dataset": {
            "sha256": dataset_hash,
            "training_cutoff_utc": training_cutoff_utc,
            "training_rows": training_rows,
        },
        "code_revision": code_revision,
        "dependency": {
            "path": dependency_path.name,
            "sha256": _sha256_file(dependency_path),
        },
        "components": {
            "primary": {
                **_component_receipt(out / "primary", "model.joblib"),
                "model_metadata_hash": primary_model_hash,
            },
            "bootstrap": _component_receipt(out / "bootstrap", "bootstrap.joblib"),
            "sklearn_baseline": _component_receipt(out / "sklearn_baseline", "model.joblib"),
            "xgb_direct": _component_receipt(out / "xgb_direct", "model.joblib"),
            "calibrator": {
                "path": calibrator_target.name,
                "artifact_sha256": calibrator.artifact_sha256,
                "calibrator_hash": calibrator.calibrator_hash,
                "base_model_hash": calibrator.base_model_hash,
                "dataset_hash": calibrator.dataset_hash,
            },
        },
    }
    manifest["bundle_hash"] = _canonical_hash(manifest)
    (out / MLB_V2_MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
    return out


def _verify_component(root: Path, receipt: dict[str, Any], bundle_filename: str) -> Path:
    path = (root / str(receipt["path"])).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("frozen MLB v2 component escapes bundle root") from exc
    if _sha256_file(path / "metadata.json") != receipt.get("metadata_sha256"):
        raise ValueError(f"frozen MLB v2 metadata hash mismatch: {path.name}")
    if _sha256_file(path / bundle_filename) != receipt.get("bundle_sha256"):
        raise ValueError(f"frozen MLB v2 component hash mismatch: {path.name}")
    return path


def load_frozen_mlb_v2_bundle(
    challenger_root: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    expected_code_revision: str | None = None,
) -> FrozenMLBV2Bundle:
    """Load the exact sealed candidate and reject every provenance mismatch."""
    challenger = Path(challenger_root).resolve() if challenger_root is not None else (
        default_repo_root() / "config" / "models" / "challengers"
    ).resolve()
    root = assert_challenger_artifact_path(challenger / MLB_V2_BUNDLE_DIRNAME, challenger)
    manifest_path = assert_challenger_artifact_path(root / MLB_V2_MANIFEST, challenger)
    manifest = json.loads(manifest_path.read_text())
    expected_bundle_hash = manifest.get("bundle_hash")
    identity = {key: value for key, value in manifest.items() if key != "bundle_hash"}
    if not expected_bundle_hash or _canonical_hash(identity) != expected_bundle_hash:
        raise ValueError("frozen MLB v2 manifest hash mismatch")
    if manifest.get("schema_version") != MLB_V2_SCHEMA_VERSION:
        raise ValueError("unsupported frozen MLB v2 manifest schema")
    if manifest.get("test_id") != MLB_V2_TEST_ID or manifest.get("candidate_version") != MLB_V2_CANDIDATE_VERSION:
        raise ValueError("frozen MLB v2 cohort identity mismatch")
    if manifest.get("head_family") != FROZEN_HEAD_FAMILY:
        raise ValueError("frozen MLB v2 head family mismatch")
    if manifest.get("distribution_method") != FROZEN_DISTRIBUTION_METHOD:
        raise ValueError("frozen MLB v2 distribution mismatch")

    repository = Path(repo_root).resolve() if repo_root is not None else default_repo_root()
    revision = expected_code_revision or current_code_revision(repository)
    if manifest.get("code_revision") != revision:
        raise ValueError("frozen MLB v2 code revision mismatch")
    dependency = manifest.get("dependency", {})
    dependency_path = repository / str(dependency.get("path", ""))
    if _sha256_file(dependency_path) != dependency.get("sha256"):
        raise ValueError("frozen MLB v2 dependency manifest hash mismatch")

    components = manifest.get("components", {})
    primary_path = _verify_component(root, components["primary"], "model.joblib")
    bootstrap_path = _verify_component(root, components["bootstrap"], "bootstrap.joblib")
    baseline_path = _verify_component(root, components["sklearn_baseline"], "model.joblib")
    direct_path = _verify_component(root, components["xgb_direct"], "model.joblib")
    primary = XGBoostTwoHeadModel.load(primary_path)
    bootstrap = BootstrapMLBEnsemble.load(bootstrap_path)
    sklearn_baseline = MLBTwoHeadModel.load(baseline_path)
    xgb_direct = XGBoostChallenger.load(direct_path)

    calibrator_receipt = components["calibrator"]
    calibrator_path = (root / str(calibrator_receipt["path"])).resolve()
    if _sha256_file(calibrator_path) != calibrator_receipt.get("artifact_sha256"):
        raise ValueError("frozen MLB v2 calibrator file hash mismatch")
    calibrator = load_frozen_calibrator(calibrator_path, challenger_root=root)

    primary_metadata_hash = primary.to_artifact().get("artifact_hash", "")
    if primary_metadata_hash != components["primary"].get("model_metadata_hash"):
        raise ValueError("frozen MLB v2 primary model metadata mismatch")
    if calibrator.base_model_hash != primary_metadata_hash:
        raise ValueError("frozen MLB v2 calibrator is not bound to the primary model")
    dataset = manifest.get("dataset", {})
    if calibrator.dataset_hash != dataset.get("sha256"):
        raise ValueError("frozen MLB v2 calibrator dataset mismatch")
    if calibrator.calibrator_hash != calibrator_receipt.get("calibrator_hash"):
        raise ValueError("frozen MLB v2 calibrator identity mismatch")

    feature_order = manifest.get("feature_order", {})
    if feature_order.get("intensity") != list(MLB_INTENSITY_FEATURES):
        raise ValueError("frozen MLB v2 intensity feature order mismatch")
    if feature_order.get("differential") != list(MLB_DIFFERENTIAL_FEATURES):
        raise ValueError("frozen MLB v2 differential feature order mismatch")
    if feature_order.get("xgb_direct") != list(XGB_DIRECT_FEATURES):
        raise ValueError("frozen MLB v2 direct-model feature order mismatch")
    if primary._intensity_features != list(MLB_INTENSITY_FEATURES):
        raise ValueError("frozen MLB v2 primary intensity features mismatch")
    if primary._differential_features != list(MLB_DIFFERENTIAL_FEATURES):
        raise ValueError("frozen MLB v2 primary differential features mismatch")
    if primary.distribution.method != FROZEN_DISTRIBUTION_METHOD:
        raise ValueError("frozen MLB v2 primary distribution mismatch")
    if bootstrap._intensity_features != list(MLB_INTENSITY_FEATURES):
        raise ValueError("frozen MLB v2 bootstrap intensity features mismatch")
    if bootstrap._differential_features != list(MLB_DIFFERENTIAL_FEATURES):
        raise ValueError("frozen MLB v2 bootstrap differential features mismatch")
    if sklearn_baseline._intensity_features != list(MLB_INTENSITY_FEATURES):
        raise ValueError("frozen MLB v2 baseline intensity features mismatch")
    if sklearn_baseline._differential_features != list(MLB_DIFFERENTIAL_FEATURES):
        raise ValueError("frozen MLB v2 baseline differential features mismatch")
    if xgb_direct._feature_names != list(XGB_DIRECT_FEATURES):
        raise ValueError("frozen MLB v2 direct-model feature order mismatch")

    return FrozenMLBV2Bundle(
        primary=primary,
        bootstrap=bootstrap,
        sklearn_baseline=sklearn_baseline,
        xgb_direct=xgb_direct,
        calibrator=calibrator,
        bundle_hash=str(expected_bundle_hash),
        dataset_hash=str(dataset["sha256"]),
        training_cutoff_utc=str(dataset["training_cutoff_utc"]),
        training_rows=int(dataset["training_rows"]),
        code_revision=str(manifest["code_revision"]),
        dependency_hash=str(dependency["sha256"]),
        bundle_path=root,
    )
