"""Run the measured-edge MLB-totals Stage 1 gate on settled ledger evidence.

The ledger is one read-only transaction snapshot. The runner requires an exact-byte-hashed
experiment spec, never infers historical provenance, and writes only immutable,
content-addressed reports/candidate artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.config import PROJECT_ROOT
from model_prediction.data_sources.mlb_market_odds import (
    load_verified_mlb_market_snapshot,
)
from model_prediction.experiment_registry import complete, record, void
from model_prediction.market_blend import (
    MarketBlendBlockedError,
    SettledBlendEvidence,
    Stage1ExperimentSpec,
    build_policy_artifact,
    canonical_config_logical_hash,
    fit_oof_market_blend,
    load_stage1_experiment_spec,
)
from model_prediction.models.mlb import canonical_mlb_artifact_hash
from model_prediction.runtime_ledger_store import verify_runtime_ledger_connection
from model_prediction.runtime_paths import RuntimePaths

IMPLEMENTATION_RELATIVE_PATHS = (
    "src/model_prediction/market_blend.py",
    "src/model_prediction/rebuild/decision.py",
    "src/model_prediction/rebuild/mlb_market_matching.py",
    "src/model_prediction/rebuild/mlb_shadow_pipeline.py",
    "src/model_prediction/rebuild/shadow_ledger.py",
    "src/model_prediction/domain.py",
    "src/model_prediction/ledger.py",
    "src/model_prediction/forward.py",
    "src/model_prediction/data_sources/polymarket_us.py",
    "src/model_prediction/data_sources/mlb_market_odds.py",
    "src/model_prediction/runtime_ledger_store.py",
    "src/model_prediction/models/mlb.py",
    "src/model_prediction/rebuild/economic.py",
    "src/model_prediction/cli.py",
    "src/model_prediction/experiment_registry.py",
    "src/model_prediction/runtime_paths.py",
    "src/model_prediction/eligibility.py",
    "src/model_prediction/pricing.py",
    "scripts/market_blend_stage1.py",
)


def _load_lineage_manifest(
    manifest_path: Path | None, hash_path: Path | None, *, expected_schema: str
) -> tuple[dict[tuple[str, str], dict[str, Any]], str | None]:
    if manifest_path is None and hash_path is None:
        return {}, None
    if manifest_path is None or hash_path is None:
        raise MarketBlendBlockedError(
            "lineage manifest and exact-byte SHA-256 sidecar must be supplied together"
        )
    expected = hash_path.read_text(encoding="utf-8").strip()
    payload = manifest_path.read_bytes()
    actual = hashlib.sha256(payload).hexdigest()
    if len(expected) != 64 or actual != expected:
        raise MarketBlendBlockedError("lineage manifest exact-byte hash mismatch")
    raw = json.loads(payload)
    if raw.get("schema_version") != expected_schema:
        raise MarketBlendBlockedError("unsupported lineage manifest schema")
    entries: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in raw.get("artifacts", []):
        kind = entry.get("kind")
        logical_hash = entry.get("logical_hash")
        byte_sha256 = entry.get("byte_sha256")
        if (
            kind not in {"model", "config"}
            or not isinstance(logical_hash, str)
            or not logical_hash
            or not isinstance(byte_sha256, str)
            or len(byte_sha256) != 64
            or not isinstance(entry.get("path"), str)
            or not entry["path"]
        ):
            raise MarketBlendBlockedError("invalid lineage manifest artifact entry")
        try:
            int(byte_sha256, 16)
        except ValueError as exc:
            raise MarketBlendBlockedError("invalid lineage byte_sha256") from exc
        key = (kind, logical_hash)
        if key in entries:
            raise MarketBlendBlockedError("duplicate lineage manifest artifact entry")
        entries[key] = entry
    return entries, actual


def _lineage_status(
    entries: dict[tuple[str, str], dict[str, Any]],
    kind: str,
    logical_hash: str | None,
    producer_byte_sha256: str | None,
    producer_path: str | None,
) -> tuple[bool, bool, bool]:
    if logical_hash is None or (kind, logical_hash) not in entries:
        return False, False, False
    entry = entries[(kind, logical_hash)]
    artifact_path = entry["path"]
    path = Path(artifact_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    recorded_path = Path(producer_path).resolve() if producer_path else None
    binding_verified = (
        recorded_path is not None
        and path.resolve() == recorded_path
        and producer_byte_sha256 == entry["byte_sha256"]
    )
    if not binding_verified or not path.is_file():
        return True, False, False
    payload = path.read_bytes()
    actual_byte_sha256 = hashlib.sha256(payload).hexdigest()
    if kind == "config":
        derived_logical_hash = canonical_config_logical_hash(payload)
    else:
        try:
            artifact = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            artifact = None
        derived_logical_hash = canonical_mlb_artifact_hash(artifact) if isinstance(artifact, dict) else None
    binding_verified = binding_verified and derived_logical_hash == logical_hash
    bytes_verified = actual_byte_sha256 == entry["byte_sha256"] == producer_byte_sha256
    return True, binding_verified, bytes_verified


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    return None


def _fetch_settled(
    conn: sqlite3.Connection,
    *,
    sport: str,
    market: str,
    tier: str,
    spec: Stage1ExperimentSpec,
    lineage_entries: dict[tuple[str, str], dict[str, Any]],
    approved_snapshot_roots: tuple[Path, ...],
) -> tuple[list[SettledBlendEvidence], set[str]]:
    conn.row_factory = sqlite3.Row
    evidence_spec = spec.raw["evidence"]
    decisions = tuple(evidence_spec["accepted_ledger_decisions"])
    reasons = tuple(evidence_spec["accepted_reason_codes"])
    rows = conn.execute(
        """SELECT *
           FROM ledger_records
           WHERE sport = ? AND market_type = ? AND ledger_tier = ?
             AND status = 'settled'""",
        (sport.lower(), market.lower(), tier.lower()),
    ).fetchall()

    evidence: list[SettledBlendEvidence] = []
    model_ids: set[str] = set()
    rows = [row for row in rows if row["decision"] in decisions and row["reason_code"] in reasons]
    for row in rows:
        payload: dict[str, Any] = {}
        payload_valid = False
        if row["decision_payload_json"]:
            try:
                decoded = json.loads(row["decision_payload_json"])
                if isinstance(decoded, dict):
                    payload = decoded
                    payload_valid = True
            except json.JSONDecodeError:
                pass
        model_ids.add(str(row["model_id"] or ""))
        config_hash = payload.get("config_hash")
        model_hash = str(row["model_artifact_hash"] or "")
        model_logical_verified, model_binding_verified, model_bytes_verified = _lineage_status(
            lineage_entries,
            "model",
            model_hash,
            payload.get("model_artifact_byte_sha256"),
            payload.get("model_artifact_path"),
        )
        config_logical_verified, config_binding_verified, config_bytes_verified = _lineage_status(
            lineage_entries,
            "config",
            config_hash,
            payload.get("config_byte_sha256"),
            payload.get("config_path"),
        )
        archive_verified = False
        try:
            load_verified_mlb_market_snapshot(
                archive_path=payload.get("market_snapshot_archive_path"),
                record_id=payload.get("market_snapshot_record_id"),
                approved_roots=approved_snapshot_roots,
                expected_snapshot_hash=payload.get("market_snapshot_hash"),
                event_id=str(row["event_id"] or ""),
                observed_at_utc=str(payload.get("market_quote_observed_at_utc") or ""),
                provider=str(payload.get("market_quote_source") or ""),
                market_type=str(row["market_type"] or ""),
                selection=str(row["selection"] or ""),
                line=row["line"],
                american_odds=int(payload.get("american_odds")),
            )
            archive_verified = True
        except (TypeError, ValueError):
            pass
        row_keys = set(row.keys())
        projected_snapshot_hash = row["market_snapshot_hash"] if "market_snapshot_hash" in row_keys else None
        evidence.append(
            SettledBlendEvidence(
                pick_id=str(row["pick_id"]),
                event_id=str(row["event_id"] or ""),
                event_start_utc=str(row["event_start_utc"] or ""),
                sport=str(row["sport"]),
                market=str(row["market_type"]),
                model_probability=(
                    float(row["model_probability"]) if row["model_probability"] is not None else None
                ),
                market_probability=(
                    float(row["market_probability"]) if row["market_probability"] is not None else None
                ),
                outcome=1 if row["result"] == "win" else 0 if row["result"] == "loss" else -1,
                model_artifact_hash=model_hash,
                config_hash=config_hash,
                config_byte_sha256=payload.get("config_byte_sha256"),
                config_path=payload.get("config_path"),
                model_artifact_byte_sha256=payload.get("model_artifact_byte_sha256"),
                model_artifact_path=payload.get("model_artifact_path"),
                quote_observed_at_utc=payload.get("market_quote_observed_at_utc"),
                timestamp_valid=_optional_bool(payload.get("market_quote_timestamp_valid")),
                market_source=payload.get("market_quote_source"),
                market_provenance=payload.get("market_quote_provenance"),
                is_reconstructed=_optional_bool(payload.get("market_quote_reconstructed")),
                decision_observed_at_utc=payload.get("observed_at_utc"),
                ledger_created_at_utc=row["created_at_utc"],
                record_source=payload.get("record_source"),
                ledger_decision=row["decision"],
                reason_code=row["reason_code"],
                record_type=payload.get("record_type"),
                call_type=payload.get("call_type"),
                corrective_action=payload.get("corrective_action"),
                is_backfill=_optional_bool(payload.get("is_backfill")),
                model_artifact_bytes_verified=model_bytes_verified,
                config_bytes_verified=config_bytes_verified,
                model_logical_hash_manifest_verified=model_logical_verified,
                config_logical_hash_manifest_verified=config_logical_verified,
                model_lineage_binding_verified=model_binding_verified,
                config_lineage_binding_verified=config_binding_verified,
                decision_payload_json_valid=payload_valid,
                model_artifact_lineage_verified=(
                    payload_valid and payload.get("model_artifact_hash") == row["model_artifact_hash"]
                ),
                market_snapshot_hash=payload.get("market_snapshot_hash"),
                market_snapshot_hash_projection_verified=(
                    payload_valid and payload.get("market_snapshot_hash") == projected_snapshot_hash
                ),
                market_snapshot_archive_path=payload.get("market_snapshot_archive_path"),
                market_snapshot_record_id=payload.get("market_snapshot_record_id"),
                market_snapshot_archive_verified=archive_verified,
            )
        )
    return evidence, {model_id for model_id in model_ids if model_id}


def _unverified_actual_call_diagnostic(
    conn: sqlite3.Connection,
    *,
    sport: str,
    market: str,
    tier: str,
    spec: Stage1ExperimentSpec,
) -> dict[str, Any]:
    """Describe actual calls when integrity blocks training, never acceptance.

    The rows remain inside the gate's one read transaction, but projection
    integrity has not cleared. The explicit label prevents these aggregates
    from being mistaken for gate evidence or used to learn a weight.
    """
    evidence_spec = spec.raw["evidence"]
    decisions = tuple(evidence_spec["accepted_ledger_decisions"])
    reasons = tuple(evidence_spec["accepted_reason_codes"])
    placeholders = ",".join("?" for _ in decisions)
    reason_placeholders = ",".join("?" for _ in reasons)
    rows = conn.execute(
        f"""SELECT model_probability, market_probability, result,
                   decision_payload_json
            FROM ledger_records
            WHERE sport = ? AND market_type = ? AND ledger_tier = ?
              AND status = 'settled'
              AND decision IN ({placeholders})
              AND reason_code IN ({reason_placeholders})""",
        (sport.lower(), market.lower(), tier.lower(), *decisions, *reasons),
    ).fetchall()
    accepted_record_types = set(evidence_spec["accepted_record_types"])
    accepted_call_types = set(evidence_spec["accepted_call_types"])
    called: list[sqlite3.Row] = []
    for row in rows:
        try:
            payload = json.loads(row["decision_payload_json"] or "")
        except json.JSONDecodeError:
            continue
        if (
            isinstance(payload, dict)
            and payload.get("record_type") in accepted_record_types
            and payload.get("call_type") in accepted_call_types
            and payload.get("corrective_action") in (None, "")
            and payload.get("is_backfill") is False
        ):
            called.append(row)
    numeric = [
        row
        for row in called
        if row["result"] in {"win", "loss"}
        and all(
            isinstance(value, (int, float)) and math.isfinite(value) and 0.0 < value < 1.0
            for value in (row["model_probability"], row["market_probability"])
        )
    ]
    diagnostic: dict[str, Any] = {
        "label": "RESEARCH_ONLY_UNVERIFIED_LEDGER_DESCRIPTIVE_NOT_GATE_EVIDENCE",
        "population": "settled actual called picks from integrity-blocked projection",
        "n_candidate_rows": len(rows),
        "n_input_calls": len(called),
        "n_numeric_calls": len(numeric),
        "weight_learning_performed": False,
        "policy_eligible": False,
    }
    if numeric:
        outcomes = [1 if row["result"] == "win" else 0 for row in numeric]
        model = [float(row["model_probability"]) for row in numeric]
        market_values = [float(row["market_probability"]) for row in numeric]

        def scores(probabilities: list[float]) -> dict[str, float]:
            return {
                "brier": sum(
                    (probability - outcome) ** 2
                    for probability, outcome in zip(probabilities, outcomes, strict=True)
                )
                / len(outcomes),
                "log_loss": -sum(
                    outcome * math.log(probability) + (1 - outcome) * math.log(1 - probability)
                    for probability, outcome in zip(probabilities, outcomes, strict=True)
                )
                / len(outcomes),
            }

        diagnostic["model_only"] = scores(model)
        diagnostic["market_reference"] = scores(market_values)
    return diagnostic


def _git_sha() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _implementation_manifest(spec_path: Path) -> tuple[list[dict[str, str]], str]:
    """Hash every Stage 1 production-path file and make the manifest explicit."""
    resolved_spec = spec_path.resolve()
    try:
        spec_relative = str(resolved_spec.relative_to(PROJECT_ROOT.resolve()))
    except ValueError as exc:
        raise ValueError("experiment spec must live inside the repository") from exc
    relative_paths = (*IMPLEMENTATION_RELATIVE_PATHS, spec_relative)
    manifest = []
    aggregate = hashlib.sha256()
    for relative in relative_paths:
        path = PROJECT_ROOT / relative
        payload = path.read_bytes()
        file_hash = hashlib.sha256(payload).hexdigest()
        manifest.append({"path": relative, "sha256": file_hash})
        aggregate.update(relative.encode())
        aggregate.update(b"\0")
        aggregate.update(payload)
        aggregate.update(b"\0")
    return manifest, aggregate.hexdigest()


def _write_immutable_json(path: Path, payload: dict[str, Any]) -> None:
    """Stage, fsync, then atomically publish without replacing an existing path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, default=str) + "\n"
    staged_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".staged",
            delete=False,
        ) as handle:
            staged_path = Path(handle.name)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(staged_path, path)
    except FileExistsError as exc:
        raise FileExistsError(f"immutable artifact already exists: {path}") from exc
    finally:
        if staged_path is not None:
            staged_path.unlink(missing_ok=True)


def run_gate(
    *,
    db_path: Path,
    sport: str,
    market: str,
    tier: str,
    spec: Stage1ExperimentSpec,
    spec_path: Path,
    lineage_entries: dict[tuple[str, str], dict[str, Any]],
    lineage_manifest_hash: str | None,
    runtime_root: Path,
    report_dir: Path,
    policy_dir: Path | None = None,
    record_experiment: bool = True,
) -> tuple[dict[str, Any], dict[str, Any] | None, Path]:
    if not record_experiment and policy_dir is not None:
        raise MarketBlendBlockedError("policy output requires the canonical experiment registry")
    runtime_paths = RuntimePaths(repo_root=PROJECT_ROOT, runtime_root=runtime_root)
    allowed_pairs = {
        (item["sport"], item["market"]) for item in spec.raw["scope"]["allowed_sport_market_pairs"]
    }
    requested_pair = (sport.lower(), market.lower())
    if requested_pair not in allowed_pairs:
        raise MarketBlendBlockedError(
            f"unsupported Stage 1 sport/market pair: {requested_pair[0]}/{requested_pair[1]}"
        )
    if db_path.resolve() != runtime_paths.ledgers_db.resolve():
        raise MarketBlendBlockedError(
            "ledger DB is not the canonical ledgers.db under the supplied runtime root"
        )
    if not db_path.is_file():
        raise FileNotFoundError(f"settled ledger not found: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        ledger_integrity = verify_runtime_ledger_connection(conn)
        if ledger_integrity["status"] == "verified":
            evidence, model_ids = _fetch_settled(
                conn,
                sport=sport,
                market=market,
                tier=tier,
                spec=spec,
                lineage_entries=lineage_entries,
                approved_snapshot_roots=(runtime_root, PROJECT_ROOT / "data"),
            )
            report = fit_oof_market_blend(evidence, spec)
        else:
            model_ids = set()
            blockers = [f"runtime_ledger_integrity:{problem}" for problem in ledger_integrity["problems"]]
            identity = json.dumps(ledger_integrity, sort_keys=True, separators=(",", ":")).encode()
            report = {
                "schema_version": "market_blend_policy_v1",
                "experiment_spec_hash": spec.exact_bytes_sha256,
                "training_inputs": spec.raw,
                "status": "blocked",
                "blockers": blockers,
                "n_rows": 0,
                "dataset_hash": hashlib.sha256(identity).hexdigest(),
                "fold_definition": dict(spec.raw["folds"]),
                "descriptive_diagnostic": _unverified_actual_call_diagnostic(
                    conn, sport=sport, market=market, tier=tier, spec=spec
                ),
                "verdict": "blocked_ledger_integrity",
            }
    finally:
        if conn.in_transaction:
            conn.rollback()
        conn.close()
    report["runtime_ledger_integrity"] = ledger_integrity
    implementation_manifest, implementation_hash = _implementation_manifest(spec_path)
    report.update(
        sport=sport.lower(),
        market=market.lower(),
        ledger_tier=tier.lower(),
        source="runtime SQLite ledger, read-only transaction snapshot of settled win/loss rows",
        historical_model_ids=sorted(model_ids),
        activation="disabled_by_default",
        implementation_manifest=implementation_manifest,
        implementation_hash=implementation_hash,
        lineage_manifest_hash=lineage_manifest_hash,
    )

    policy_artifact = None
    policy_path = None
    if report["status"] == "passed" and policy_dir is not None:
        policy_artifact = build_policy_artifact(f"market-blend-{sport.lower()}-{market.lower()}-v1", [report])
        policy_path = policy_dir / (
            f"market_blend_{sport.lower()}_{market.lower()}_{policy_artifact['artifact_hash']}.json"
        )
    report["candidate_policy_path"] = str(policy_path) if policy_path else None
    report["candidate_policy_artifact_hash"] = policy_artifact["artifact_hash"] if policy_artifact else None

    report_name = (
        f"stage1_gate_{sport.lower()}_{market.lower()}_{tier.lower()}_"
        f"{report['dataset_hash'][:12]}_{implementation_hash[:12]}_"
        f"{spec.exact_bytes_sha256[:12]}_"
        f"{(lineage_manifest_hash or 'no-lineage')[:12]}.json"
    )
    report_path = report_dir / report_name
    if report_path.exists():
        raise FileExistsError(f"immutable artifact already exists: {report_path}")

    experiment = None
    try:
        if record_experiment:
            incumbent = next(iter(model_ids)) if len(model_ids) == 1 else None
            experiment = record(
                model_id=f"market-blend-{sport.lower()}-{market.lower()}",
                incumbent_id=incumbent,
                dataset_hash=report["dataset_hash"],
                feature_schema_hash=None,
                fold_definition=report["fold_definition"],
                hyperparameters={
                    "ledger_tier": tier.lower(),
                    "activation": "disabled_by_default",
                    "experiment_spec_hash": spec.exact_bytes_sha256,
                    "lineage_manifest_hash": lineage_manifest_hash,
                    "training_inputs": spec.raw,
                    "implementation_manifest": implementation_manifest,
                },
                calibrator="convex_market_blend",
                oof_metrics=report.get("oof_metrics"),
                artifact_hashes={
                    "evidence_dataset": report["dataset_hash"],
                    "stage1_implementation": implementation_hash,
                    "experiment_spec": spec.exact_bytes_sha256,
                    **(
                        {"lineage_manifest": lineage_manifest_hash}
                        if lineage_manifest_hash is not None
                        else {}
                    ),
                    **(
                        {"candidate_policy": policy_artifact["artifact_hash"]}
                        if policy_artifact is not None
                        else {}
                    ),
                },
                verdict=report["verdict"],
                git_sha=_git_sha(),
                status="running",
                repo_root=PROJECT_ROOT,
                runtime_root=runtime_paths.runtime_root,
            )
            report["experiment_id"] = experiment["experiment_id"]

        report["report_path"] = str(report_path)
        if policy_path is not None and policy_artifact is not None:
            _write_immutable_json(policy_path, policy_artifact)
        _write_immutable_json(report_path, report)
        if experiment is not None:
            experiment = complete(
                experiment["experiment_id"],
                repo_root=PROJECT_ROOT,
                runtime_root=runtime_paths.runtime_root,
            )
    except Exception as exc:
        if experiment is not None:
            void(
                experiment["experiment_id"],
                f"Stage 1 immutable output finalization failed: {type(exc).__name__}: {exc}",
                repo_root=PROJECT_ROOT,
                runtime_root=runtime_paths.runtime_root,
            )
        raise
    return report, experiment, report_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--sport", required=True)
    parser.add_argument("--market", required=True)
    parser.add_argument("--tier", required=True)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--spec-sha256", required=True, type=Path)
    parser.add_argument("--report-dir", required=True, type=Path)
    parser.add_argument("--policy-dir", type=Path)
    parser.add_argument("--lineage-manifest", type=Path)
    parser.add_argument("--lineage-manifest-sha256", type=Path)
    parser.add_argument("--no-registry", action="store_true")
    args = parser.parse_args(argv)
    if args.no_registry and args.policy_dir is not None:
        parser.error("--no-registry cannot be combined with --policy-dir")
    runtime_paths = RuntimePaths(repo_root=PROJECT_ROOT, runtime_root=args.runtime_root)
    db_path = args.db or runtime_paths.ledgers_db

    spec = load_stage1_experiment_spec(args.spec, args.spec_sha256)
    lineage_entries, lineage_manifest_hash = _load_lineage_manifest(
        args.lineage_manifest,
        args.lineage_manifest_sha256,
        expected_schema=spec.raw["evidence"]["lineage_manifest_schema"],
    )
    report, _experiment, report_path = run_gate(
        db_path=db_path,
        sport=args.sport,
        market=args.market,
        tier=args.tier,
        spec=spec,
        spec_path=args.spec,
        report_dir=args.report_dir,
        lineage_entries=lineage_entries,
        lineage_manifest_hash=lineage_manifest_hash,
        runtime_root=args.runtime_root,
        policy_dir=args.policy_dir,
        record_experiment=not args.no_registry,
    )
    print(json.dumps(report, indent=2, default=str))
    print(f"written to {report_path}")
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    sys.exit(main())
