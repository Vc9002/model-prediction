"""Hash-bound market-blend serving policy and chronological OOF gate.

The model remains the source of ``p_model``. This module owns a separate
decision-time policy, ``p_blend = w*p_model + (1-w)*p_market``. Every training
and acceptance input comes from an exact-byte SHA-256-verified experiment spec;
there are no code-default weights, sample bars, bootstrap inputs, or gate
thresholds.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from .config import PROJECT_ROOT
from .runtime_paths import RuntimePaths

POLICY_SCHEMA_VERSION = "market_blend_policy_v1"
SPEC_SCHEMA_VERSION = "market_blend_stage1_experiment_spec_v1"


class MarketBlendBlockedError(ValueError):
    """The experiment or serving policy cannot be trusted."""


def canonical_hash(payload: dict[str, Any]) -> str:
    canonical = {key: value for key, value in payload.items() if key != "artifact_hash"}
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def canonical_config_logical_hash(payload: bytes) -> str:
    """Hash exact config bytes with the producer's versioned domain separator."""
    return hashlib.sha256(b"model_prediction_config_v1\0" + payload).hexdigest()


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class Stage1ExperimentSpec:
    raw: dict[str, Any]
    exact_bytes_sha256: str

    @classmethod
    def from_bytes(cls, payload: bytes, expected_sha256: str) -> Stage1ExperimentSpec:
        if not _is_sha256(expected_sha256):
            raise MarketBlendBlockedError("experiment spec expected hash is not a SHA-256")
        actual = hashlib.sha256(payload).hexdigest()
        if actual != expected_sha256:
            raise MarketBlendBlockedError("experiment spec exact-byte hash mismatch")
        try:
            raw = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MarketBlendBlockedError("experiment spec is not valid UTF-8 JSON") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != SPEC_SCHEMA_VERSION:
            raise MarketBlendBlockedError("unsupported Stage 1 experiment spec schema")
        required = {
            "scope",
            "lineage_semantics",
            "weight_grid",
            "folds",
            "bootstrap",
            "acceptance",
            "evidence",
        }
        if required - raw.keys():
            raise MarketBlendBlockedError(
                f"experiment spec missing sections: {sorted(required - raw.keys())}"
            )
        grid = raw["weight_grid"]
        if (
            not isinstance(grid, list)
            or not grid
            or any(
                not isinstance(weight, (int, float)) or not math.isfinite(weight) or not 0 <= weight <= 1
                for weight in grid
            )
        ):
            raise MarketBlendBlockedError("experiment spec weight_grid is invalid")
        if len(grid) != len(set(grid)):
            raise MarketBlendBlockedError("experiment spec weight_grid contains duplicates")
        folds = raw["folds"]
        for key in ("n_splits", "min_rows", "min_dates", "min_oof_rows"):
            if not isinstance(folds.get(key), int) or folds[key] <= 0:
                raise MarketBlendBlockedError(f"experiment spec folds.{key} must be a positive integer")
        fraction = folds.get("initial_train_fraction")
        if not isinstance(fraction, (int, float)) or not 0 < fraction < 1:
            raise MarketBlendBlockedError(
                "experiment spec folds.initial_train_fraction must be between 0 and 1"
            )
        bootstrap = raw["bootstrap"]
        if not isinstance(bootstrap.get("n_resamples"), int) or bootstrap["n_resamples"] <= 0:
            raise MarketBlendBlockedError("experiment spec bootstrap.n_resamples must be positive")
        if not isinstance(bootstrap.get("seed"), int):
            raise MarketBlendBlockedError("experiment spec bootstrap.seed must be an integer")
        if bootstrap.get("cluster") != "event_date_utc":
            raise MarketBlendBlockedError("experiment spec bootstrap.cluster must be event_date_utc")
        acceptance = raw["acceptance"]
        if not isinstance(acceptance, dict) or not acceptance:
            raise MarketBlendBlockedError("experiment spec acceptance conditions are missing")
        for metric, condition in acceptance.items():
            if metric not in {"brier_delta", "log_loss_delta", "bootstrap_p_better"}:
                raise MarketBlendBlockedError(f"unsupported acceptance metric: {metric}")
            if not isinstance(condition, dict) or condition.get("operator") not in {"lt", "lte", "gt", "gte"}:
                raise MarketBlendBlockedError(f"invalid acceptance operator for {metric}")
            threshold = condition.get("threshold")
            if not isinstance(threshold, (int, float)) or not math.isfinite(threshold):
                raise MarketBlendBlockedError(f"invalid acceptance threshold for {metric}")
        evidence = raw["evidence"]
        for key in (
            "accepted_market_sources",
            "accepted_market_provenance",
            "accepted_ledger_decisions",
            "accepted_reason_codes",
            "accepted_record_types",
            "accepted_call_types",
            "accepted_record_sources",
            "required_decision_payload_fields",
        ):
            values = evidence.get(key)
            if (
                not isinstance(values, list)
                or not values
                or any(not isinstance(value, str) for value in values)
            ):
                raise MarketBlendBlockedError(f"experiment spec evidence.{key} is invalid")
        if evidence.get("lineage_manifest_schema") != "market_blend_lineage_manifest_v2":
            raise MarketBlendBlockedError("experiment spec evidence.lineage_manifest_schema is unsupported")
        for key in (
            "require_config_hash",
            "require_timestamp_valid",
            "require_prestart_quote",
            "reject_reconstructed",
            "require_quote_at_or_before_decision",
            "require_decision_at_or_before_created",
            "require_created_prestart",
            "require_cryptographic_model_lineage",
            "require_cryptographic_config_lineage",
            "require_empty_corrective_action",
            "require_explicit_not_backfill",
            "require_market_snapshot_hash",
            "require_archived_market_snapshot",
        ):
            if evidence.get(key) is not True:
                raise MarketBlendBlockedError(f"experiment spec evidence.{key} must be true")
        if raw["scope"] != {
            "allowed_sport_market_pairs": [{"sport": "mlb", "market": "total"}],
            "serving_integration": "flat_cli_measured_edge_totals_v3_only",
        }:
            raise MarketBlendBlockedError("Stage 1 scope must be exactly the integrated MLB totals path")
        if raw["lineage_semantics"] != {
            "model_logical_hash": "mlb_canonical_json_without_artifact_hash_sha256",
            "config_logical_hash": "model_prediction_config_v1_nul_exact_bytes_sha256",
        }:
            raise MarketBlendBlockedError("unsupported Stage 1 lineage semantics")
        return cls(raw=raw, exact_bytes_sha256=actual)


def load_stage1_experiment_spec(
    spec_path: str | Path, expected_hash_path: str | Path
) -> Stage1ExperimentSpec:
    path = Path(spec_path)
    hash_path = Path(expected_hash_path)
    if not path.is_file() or not hash_path.is_file():
        raise MarketBlendBlockedError("experiment spec and exact-byte hash sidecar are required")
    expected = hash_path.read_text(encoding="utf-8").strip()
    return Stage1ExperimentSpec.from_bytes(path.read_bytes(), expected)


@dataclass(frozen=True)
class BlendPolicyEntry:
    sport: str
    market: str
    weight: float
    model_artifact_hash: str
    config_hash: str
    evidence_dataset_hash: str
    experiment_spec_hash: str
    implementation_hash: str
    lineage_manifest_hash: str
    training_inputs: dict[str, Any]
    fold_definition: dict[str, Any]
    oof_metrics: dict[str, Any]
    gate_status: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> BlendPolicyEntry:
        entry = cls(
            sport=str(raw["sport"]).lower(),
            market=str(raw["market"]).lower(),
            weight=float(raw["weight"]),
            model_artifact_hash=str(raw["model_artifact_hash"]),
            config_hash=str(raw["config_hash"]),
            evidence_dataset_hash=str(raw["evidence_dataset_hash"]),
            experiment_spec_hash=str(raw["experiment_spec_hash"]),
            implementation_hash=str(raw["implementation_hash"]),
            lineage_manifest_hash=str(raw["lineage_manifest_hash"]),
            training_inputs=dict(raw["training_inputs"]),
            fold_definition=dict(raw["fold_definition"]),
            oof_metrics=dict(raw["oof_metrics"]),
            gate_status=str(raw["gate_status"]),
        )
        if not 0.0 <= entry.weight <= 1.0:
            raise MarketBlendBlockedError("blend weight must be between 0 and 1")
        for name, value in (
            ("model_artifact_hash", entry.model_artifact_hash),
            ("config_hash", entry.config_hash),
            ("evidence_dataset_hash", entry.evidence_dataset_hash),
            ("experiment_spec_hash", entry.experiment_spec_hash),
            ("implementation_hash", entry.implementation_hash),
            ("lineage_manifest_hash", entry.lineage_manifest_hash),
        ):
            if not _is_sha256(value):
                raise MarketBlendBlockedError(f"{name} is not a valid SHA-256")
        if entry.gate_status != "passed":
            raise MarketBlendBlockedError("only a passed OOF gate may be loaded for serving")
        return entry


@dataclass(frozen=True)
class BlendAudit:
    sport: str
    market: str
    model_probability: float | None
    market_probability: float | None
    blended_probability: float
    weight: float
    model_artifact_hash: str
    config_hash: str
    policy_artifact_hash: str
    experiment_spec_hash: str


@dataclass(frozen=True)
class MarketBlendPolicy:
    policy_id: str
    entries: tuple[BlendPolicyEntry, ...]
    artifact_hash: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> MarketBlendPolicy:
        if raw.get("schema_version") != POLICY_SCHEMA_VERSION:
            raise MarketBlendBlockedError(f"unsupported market-blend schema: {raw.get('schema_version')!r}")
        stored_hash = raw.get("artifact_hash")
        if not _is_sha256(stored_hash) or stored_hash != canonical_hash(raw):
            raise MarketBlendBlockedError("market-blend artifact hash mismatch")
        entries = tuple(BlendPolicyEntry.from_dict(item) for item in raw.get("entries", []))
        if not entries:
            raise MarketBlendBlockedError("market-blend artifact has no serving entries")
        keys = [(entry.sport, entry.market) for entry in entries]
        if len(keys) != len(set(keys)):
            raise MarketBlendBlockedError("duplicate (sport, market) policy entry")
        return cls(str(raw["policy_id"]), entries, stored_hash)

    @classmethod
    def load(
        cls, path: str | Path, *, runtime_paths: RuntimePaths, report_path: str | Path
    ) -> MarketBlendPolicy:
        """Load only a policy whose immutable report and registry approval agree."""
        policy_path = Path(path)
        report_file = Path(report_path)
        if not policy_path.is_file() or not report_file.is_file():
            raise MarketBlendBlockedError("policy and immutable gate report are both required")
        policy = cls.from_dict(json.loads(policy_path.read_text(encoding="utf-8")))
        report = json.loads(report_file.read_text(encoding="utf-8"))
        experiment_id = report.get("experiment_id")
        if (
            report.get("candidate_policy_artifact_hash") != policy.artifact_hash
            or report.get("candidate_policy_path") != str(policy_path)
            or not isinstance(experiment_id, str)
        ):
            raise MarketBlendBlockedError("gate report does not bind this policy artifact")
        registry_db = runtime_paths.runs_db.resolve()
        if registry_db.parent != runtime_paths.runtime_root.resolve():
            raise MarketBlendBlockedError("experiment registry is outside canonical runtime root")
        if not registry_db.is_file():
            raise MarketBlendBlockedError("canonical experiment registry is missing")
        # The repository identity is fixed here rather than accepted from a
        # caller alongside an arbitrary registry path.
        if runtime_paths.repo_root.resolve() != PROJECT_ROOT.resolve():
            raise MarketBlendBlockedError("runtime paths do not belong to this repository")
        conn = sqlite3.connect(f"file:{registry_db}?immutable=1", uri=True)
        try:
            row = conn.execute(
                "SELECT status, artifact_hashes FROM experiments WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None or row[0] != "completed":
            raise MarketBlendBlockedError("canonical registry has not completed this policy")
        try:
            artifact_hashes = json.loads(row[1] or "{}")
        except json.JSONDecodeError as exc:
            raise MarketBlendBlockedError("registry artifact hashes are invalid") from exc
        if artifact_hashes.get("candidate_policy") != policy.artifact_hash:
            raise MarketBlendBlockedError("registry does not bind this exact policy hash")
        return policy

    def apply(
        self,
        *,
        sport: str,
        market: str,
        model_probability: float,
        market_probability: float | None,
        model_artifact_hash: str,
        config_hash: str | None,
    ) -> BlendAudit:
        key = (sport.lower(), market.lower())
        entry = next((item for item in self.entries if (item.sport, item.market) == key), None)
        if entry is None:
            raise MarketBlendBlockedError(f"no passed blend policy for sport={key[0]} market={key[1]}")
        if market_probability is None:
            raise MarketBlendBlockedError("market probability is missing at the decision boundary")
        if config_hash is None:
            raise MarketBlendBlockedError("serving config hash is missing")
        if model_artifact_hash != entry.model_artifact_hash:
            raise MarketBlendBlockedError("model artifact hash does not match blend policy")
        if config_hash != entry.config_hash:
            raise MarketBlendBlockedError("config hash does not match blend policy")
        for name, value in (
            ("model_probability", model_probability),
            ("market_probability", market_probability),
        ):
            if not math.isfinite(value) or not 0.0 < value < 1.0:
                raise MarketBlendBlockedError(f"{name} must be finite and strictly between 0 and 1")
        blended = entry.weight * model_probability + (1.0 - entry.weight) * market_probability
        return BlendAudit(
            sport=key[0],
            market=key[1],
            model_probability=model_probability,
            market_probability=market_probability,
            blended_probability=blended,
            weight=entry.weight,
            model_artifact_hash=model_artifact_hash,
            config_hash=config_hash,
            policy_artifact_hash=self.artifact_hash,
            experiment_spec_hash=entry.experiment_spec_hash,
        )

    def experiment_spec_hash_for(self, sport: str, market: str) -> str | None:
        """Return the exact spec identity for an auditable blocked decision."""
        key = (sport.lower(), market.lower())
        entry = next((item for item in self.entries if (item.sport, item.market) == key), None)
        return entry.experiment_spec_hash if entry is not None else None


@dataclass(frozen=True)
class SettledBlendEvidence:
    pick_id: str
    event_id: str
    event_start_utc: str
    sport: str
    market: str
    model_probability: float
    market_probability: float
    outcome: int
    model_artifact_hash: str
    config_hash: str | None
    config_byte_sha256: str | None
    config_path: str | None
    model_artifact_byte_sha256: str | None
    model_artifact_path: str | None
    quote_observed_at_utc: str | None
    timestamp_valid: bool | None
    market_source: str | None
    market_provenance: str | None
    is_reconstructed: bool | None
    decision_observed_at_utc: str | None
    ledger_created_at_utc: str | None
    record_source: str | None
    ledger_decision: str | None
    reason_code: str | None
    record_type: str | None
    call_type: str | None
    corrective_action: str | None
    is_backfill: bool | None
    model_artifact_bytes_verified: bool
    config_bytes_verified: bool
    model_logical_hash_manifest_verified: bool = False
    config_logical_hash_manifest_verified: bool = False
    model_lineage_binding_verified: bool = False
    config_lineage_binding_verified: bool = False
    decision_payload_json_valid: bool = True
    model_artifact_lineage_verified: bool = True
    market_snapshot_hash: str | None = None
    market_snapshot_hash_projection_verified: bool = True
    market_snapshot_archive_path: str | None = None
    market_snapshot_record_id: str | None = None
    market_snapshot_archive_verified: bool = False


def evidence_dataset_hash(rows: Sequence[SettledBlendEvidence]) -> str:
    payload = [asdict(row) for row in sorted(rows, key=lambda row: row.pick_id)]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _parse_utc(value: str | None, field: str) -> datetime:
    if not value:
        raise ValueError(f"missing_{field}")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"malformed_{field}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"naive_{field}")
    try:
        return parsed.astimezone(UTC)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"non_normalizable_{field}") from exc


def _log_loss(probabilities: Sequence[float], outcomes: Sequence[int]) -> float:
    clipped = [min(1.0 - 1e-12, max(1e-12, value)) for value in probabilities]
    return -mean(
        outcome * math.log(probability) + (1 - outcome) * math.log(1 - probability)
        for probability, outcome in zip(clipped, outcomes, strict=True)
    )


def _brier(probabilities: Sequence[float], outcomes: Sequence[int]) -> float:
    return mean(
        (probability - outcome) ** 2 for probability, outcome in zip(probabilities, outcomes, strict=True)
    )


def _learn_weight(rows: Sequence[SettledBlendEvidence], weight_grid: Sequence[float]) -> float:
    outcomes = [row.outcome for row in rows]
    scored = []
    for weight in weight_grid:
        blended = [weight * row.model_probability + (1.0 - weight) * row.market_probability for row in rows]
        scored.append((_log_loss(blended, outcomes), weight))
    return min(scored, key=lambda item: (item[0], -item[1]))[1]


def _integrity_blockers(
    rows: Sequence[SettledBlendEvidence], spec: Stage1ExperimentSpec
) -> tuple[list[str], dict[str, tuple[datetime, datetime]]]:
    folds_spec = spec.raw["folds"]
    evidence_spec = spec.raw["evidence"]
    blockers: list[str] = []
    instants: dict[str, tuple[datetime, datetime]] = {}
    event_starts: dict[str, datetime] = {}
    if len(rows) < folds_spec["min_rows"]:
        blockers.append(f"insufficient_settled_rows:{len(rows)}<{folds_spec['min_rows']}")
    if len({row.pick_id for row in rows}) != len(rows):
        blockers.append("duplicate_pick_id")
    keys = {(row.sport.lower(), row.market.lower()) for row in rows}
    if len(keys) != 1:
        blockers.append("mixed_sport_market_evidence")
    model_hashes = {row.model_artifact_hash for row in rows}
    if len(model_hashes) != 1:
        blockers.append(f"mixed_model_artifact_hashes:{len(model_hashes)}")
    if any(not _is_sha256(value) for value in model_hashes):
        blockers.append("invalid_model_artifact_hash")
    if any(not row.model_artifact_lineage_verified for row in rows):
        blockers.append("model_artifact_hash_payload_mismatch")
    if any(row.config_hash is None for row in rows):
        blockers.append("missing_historical_config_hash_lineage")
    config_hashes = {row.config_hash for row in rows if row.config_hash is not None}
    if len(config_hashes) > 1:
        blockers.append(f"mixed_config_hashes:{len(config_hashes)}")
    if config_hashes and any(not _is_sha256(value) for value in config_hashes):
        blockers.append("invalid_config_hash")

    accepted_sources = set(evidence_spec["accepted_market_sources"])
    accepted_provenance = set(evidence_spec["accepted_market_provenance"])
    accepted_decisions = set(evidence_spec["accepted_ledger_decisions"])
    accepted_reason_codes = set(evidence_spec["accepted_reason_codes"])
    accepted_record_types = set(evidence_spec["accepted_record_types"])
    accepted_call_types = set(evidence_spec["accepted_call_types"])
    accepted_record_sources = set(evidence_spec["accepted_record_sources"])
    for row in rows:
        if not row.decision_payload_json_valid:
            blockers.append("invalid_decision_payload_json")
        if row.outcome not in (0, 1):
            blockers.append("non_binary_outcome")
        if row.model_probability is None:
            blockers.append("missing_model_probability")
        elif not math.isfinite(row.model_probability) or not 0.0 < row.model_probability < 1.0:
            blockers.append("invalid_model_probability")
        if row.market_probability is None:
            blockers.append("missing_market_probability")
        elif not math.isfinite(row.market_probability) or not 0.0 < row.market_probability < 1.0:
            blockers.append("invalid_market_probability")
        event_start = None
        quote_observed = None
        decision_observed = None
        ledger_created = None
        try:
            event_start = _parse_utc(row.event_start_utc, "event_start_utc")
            event_starts[row.pick_id] = event_start
        except ValueError as exc:
            blockers.append(str(exc))
        try:
            quote_observed = _parse_utc(row.quote_observed_at_utc, "quote_observed_at_utc")
        except ValueError as exc:
            blockers.append(str(exc))
        try:
            decision_observed = _parse_utc(row.decision_observed_at_utc, "decision_observed_at_utc")
        except ValueError as exc:
            blockers.append(str(exc))
        try:
            ledger_created = _parse_utc(row.ledger_created_at_utc, "ledger_created_at_utc")
        except ValueError as exc:
            blockers.append(str(exc))
        if event_start is not None and quote_observed is not None:
            instants[row.pick_id] = (event_start, quote_observed)
            if quote_observed >= event_start:
                blockers.append("market_quote_not_prestart")
        if (
            quote_observed is not None
            and decision_observed is not None
            and quote_observed > decision_observed
        ):
            blockers.append("market_quote_after_decision")
        if (
            decision_observed is not None
            and ledger_created is not None
            and decision_observed > ledger_created
        ):
            blockers.append("decision_observed_after_ledger_creation")
        if ledger_created is not None and event_start is not None and ledger_created >= event_start:
            blockers.append("ledger_record_not_prestart")
        if row.timestamp_valid is None:
            blockers.append("missing_market_quote_timestamp_valid")
        elif row.timestamp_valid is not True:
            blockers.append("invalid_market_quote_timestamp")
        if row.market_source is None:
            blockers.append("missing_market_quote_source")
        elif row.market_source not in accepted_sources:
            blockers.append(f"unacceptable_market_quote_source:{row.market_source}")
        if row.market_provenance is None:
            blockers.append("missing_market_quote_provenance")
        elif row.market_provenance not in accepted_provenance:
            blockers.append(f"unacceptable_market_quote_provenance:{row.market_provenance}")
        if row.is_reconstructed is None:
            blockers.append("missing_market_quote_reconstructed_flag")
        elif row.is_reconstructed is not False:
            blockers.append("reconstructed_market_quote")
        if row.market_snapshot_hash is None:
            blockers.append("missing_market_snapshot_hash")
        elif not _is_sha256(row.market_snapshot_hash):
            blockers.append("invalid_market_snapshot_hash")
        if not row.market_snapshot_hash_projection_verified:
            blockers.append("market_snapshot_hash_projection_mismatch")
        if not row.market_snapshot_archive_path:
            blockers.append("missing_market_snapshot_archive_path")
        if not row.market_snapshot_record_id:
            blockers.append("missing_market_snapshot_record_id")
        if not row.market_snapshot_archive_verified:
            blockers.append("market_snapshot_archive_unverifiable")
        if row.record_source is None:
            blockers.append("missing_record_source")
        elif row.record_source not in accepted_record_sources:
            blockers.append(f"unacceptable_record_source:{row.record_source}")
        if row.ledger_decision not in accepted_decisions:
            blockers.append(f"unacceptable_ledger_decision:{row.ledger_decision}")
        if row.reason_code not in accepted_reason_codes:
            blockers.append(f"unacceptable_reason_code:{row.reason_code}")
        if row.record_type is None:
            blockers.append("missing_record_type")
        elif row.record_type not in accepted_record_types:
            blockers.append(f"invalid_record_type:{row.record_type}")
        if row.call_type is None:
            blockers.append("missing_call_type")
        elif row.call_type not in accepted_call_types:
            blockers.append(f"invalid_call_type:{row.call_type}")
        if row.corrective_action not in (None, ""):
            blockers.append("corrective_row")
        if row.is_backfill is None:
            blockers.append("missing_backfill_flag")
        elif row.is_backfill is not False:
            blockers.append("backfill_row")
        if not row.model_logical_hash_manifest_verified:
            blockers.append("model_logical_hash_absent_from_lineage_manifest")
        elif not row.model_lineage_binding_verified:
            blockers.append("model_producer_lineage_binding_mismatch")
        elif not row.model_artifact_bytes_verified:
            blockers.append("model_artifact_byte_sha256_unverifiable")
        if not row.config_logical_hash_manifest_verified:
            blockers.append("config_logical_hash_absent_from_lineage_manifest")
        elif not row.config_lineage_binding_verified:
            blockers.append("config_producer_lineage_binding_mismatch")
        elif not row.config_bytes_verified:
            blockers.append("config_byte_sha256_unverifiable")

    event_dates = {event_start.date() for event_start in event_starts.values()}
    if len(event_dates) < folds_spec["min_dates"]:
        blockers.append(f"insufficient_distinct_dates:{len(event_dates)}<{folds_spec['min_dates']}")
    return sorted(set(blockers)), instants


def _date_cluster_bootstrap(
    deltas: Sequence[tuple[str, float]], *, seed: int, n_resamples: int
) -> dict[str, Any]:
    by_date: dict[str, list[float]] = defaultdict(list)
    for date, delta in deltas:
        by_date[date].append(delta)
    dates = sorted(by_date)
    rng = random.Random(seed)
    samples = [
        mean(value for date in (rng.choice(dates) for _ in dates) for value in by_date[date])
        for _ in range(n_resamples)
    ]
    samples.sort()
    low_idx = max(0, int(n_resamples * 0.025) - 1)
    high_idx = min(n_resamples - 1, int(n_resamples * 0.975) - 1)
    return {
        "n_dates": len(dates),
        "n_resamples": n_resamples,
        "observed_mean_brier_delta": mean(value for _date, value in deltas),
        "p_better": sum(sample < 0.0 for sample in samples) / n_resamples,
        "ci_2_5": samples[low_idx],
        "ci_97_5": samples[high_idx],
    }


def _condition_passes(value: float, operator: str, threshold: float) -> bool:
    return {
        "lt": value < threshold,
        "lte": value <= threshold,
        "gt": value > threshold,
        "gte": value >= threshold,
    }[operator]


def _descriptive_actual_call_diagnostic(
    rows: Sequence[SettledBlendEvidence], spec: Stage1ExperimentSpec
) -> dict[str, Any]:
    evidence_spec = spec.raw["evidence"]
    accepted_record_types = set(evidence_spec["accepted_record_types"])
    accepted_call_types = set(evidence_spec["accepted_call_types"])
    valid = [
        row
        for row in rows
        if row.outcome in (0, 1)
        and row.record_type in accepted_record_types
        and row.call_type in accepted_call_types
        and row.corrective_action in (None, "")
        and row.is_backfill is not True
        and all(
            value is not None and math.isfinite(value) and 0.0 < value < 1.0
            for value in (row.model_probability, row.market_probability)
        )
    ]
    diagnostic: dict[str, Any] = {
        "label": "RESEARCH_ONLY_DESCRIPTIVE_NOT_GATE_EVIDENCE",
        "population": "settled actual called picks after exact call-status filtering",
        "n_candidate_rows": len(rows),
        "n_input_calls": len(valid),
        "n_numeric_calls": len(valid),
        "weight_learning_performed": False,
        "policy_eligible": False,
    }
    if not valid:
        return diagnostic
    outcomes = [row.outcome for row in valid]
    model = [row.model_probability for row in valid]
    market = [row.market_probability for row in valid]
    diagnostic.update(
        {
            "model_only": {"brier": _brier(model, outcomes), "log_loss": _log_loss(model, outcomes)},
            "market_reference": {
                "brier": _brier(market, outcomes),
                "log_loss": _log_loss(market, outcomes),
            },
            "model_minus_market_brier": _brier(model, outcomes) - _brier(market, outcomes),
            "model_minus_market_log_loss": _log_loss(model, outcomes) - _log_loss(market, outcomes),
        }
    )
    return diagnostic


def fit_oof_market_blend(
    evidence: Iterable[SettledBlendEvidence], spec: Stage1ExperimentSpec
) -> dict[str, Any]:
    """Learn only from prior UTC dates and gate only on later OOF rows."""
    unsorted_rows = list(evidence)
    blockers, instants = _integrity_blockers(unsorted_rows, spec)
    report: dict[str, Any] = {
        "schema_version": POLICY_SCHEMA_VERSION,
        "experiment_spec_hash": spec.exact_bytes_sha256,
        "training_inputs": spec.raw,
        "status": "blocked" if blockers else "running",
        "blockers": blockers,
        "n_rows": len(unsorted_rows),
        "dataset_hash": evidence_dataset_hash(unsorted_rows),
        "fold_definition": dict(spec.raw["folds"]),
        "descriptive_diagnostic": _descriptive_actual_call_diagnostic(unsorted_rows, spec),
    }
    if blockers:
        report["verdict"] = "blocked_integrity"
        return report

    rows = sorted(
        unsorted_rows,
        key=lambda row: (instants[row.pick_id][0], row.pick_id),
    )
    folds_spec = spec.raw["folds"]
    grid = tuple(float(weight) for weight in spec.raw["weight_grid"])
    dates = sorted({instants[row.pick_id][0].date() for row in rows})
    initial_train_dates = max(1, int(len(dates) * folds_spec["initial_train_fraction"]))
    test_dates = dates[initial_train_dates:]
    fold_size = max(1, math.ceil(len(test_dates) / folds_spec["n_splits"]))
    oof: list[tuple[SettledBlendEvidence, float, float]] = []
    folds: list[dict[str, Any]] = []
    for fold_index, offset in enumerate(range(0, len(test_dates), fold_size), start=1):
        fold_test_dates = set(test_dates[offset : offset + fold_size])
        test_start = min(fold_test_dates)
        train = [row for row in rows if instants[row.pick_id][0].date() < test_start]
        test = [row for row in rows if instants[row.pick_id][0].date() in fold_test_dates]
        if not train or not test:
            continue
        weight = _learn_weight(train, grid)
        folds.append(
            {
                "fold": fold_index,
                "train_end": max(instants[row.pick_id][0].date() for row in train).isoformat(),
                "test_start": test_start.isoformat(),
                "test_end": max(fold_test_dates).isoformat(),
                "n_train": len(train),
                "n_test": len(test),
                "learned_weight": weight,
            }
        )
        oof.extend(
            (
                row,
                weight,
                weight * row.model_probability + (1.0 - weight) * row.market_probability,
            )
            for row in test
        )

    if len(oof) < folds_spec["min_oof_rows"]:
        report.update(
            status="blocked",
            verdict="blocked_insufficient_oof",
            blockers=[f"insufficient_oof_rows:{len(oof)}<{folds_spec['min_oof_rows']}"],
            folds=folds,
        )
        return report

    model_probs = [row.model_probability for row, _weight, _blend in oof]
    blend_probs = [blend for _row, _weight, blend in oof]
    outcomes = [row.outcome for row, _weight, _blend in oof]
    model_brier = _brier(model_probs, outcomes)
    blend_brier = _brier(blend_probs, outcomes)
    model_log_loss = _log_loss(model_probs, outcomes)
    blend_log_loss = _log_loss(blend_probs, outcomes)
    bootstrap_spec = spec.raw["bootstrap"]
    bootstrap = _date_cluster_bootstrap(
        [
            (
                instants[row.pick_id][0].date().isoformat(),
                (blend - row.outcome) ** 2 - (row.model_probability - row.outcome) ** 2,
            )
            for row, _weight, blend in oof
        ],
        seed=bootstrap_spec["seed"],
        n_resamples=bootstrap_spec["n_resamples"],
    )
    metrics = {
        "brier_delta": blend_brier - model_brier,
        "log_loss_delta": blend_log_loss - model_log_loss,
        "bootstrap_p_better": bootstrap["p_better"],
    }
    acceptance_results = {
        name: {
            **condition,
            "actual": metrics[name],
            "passed": _condition_passes(metrics[name], condition["operator"], condition["threshold"]),
        }
        for name, condition in spec.raw["acceptance"].items()
    }
    gate_passed = all(result["passed"] for result in acceptance_results.values())
    final_weight = _learn_weight(rows, grid) if gate_passed else None
    report.update(
        status="passed" if gate_passed else "failed",
        verdict="passed_oof_gate" if gate_passed else "failed_oof_gate",
        folds=folds,
        n_oof=len(oof),
        final_serving_weight=final_weight,
        model_artifact_hash=rows[0].model_artifact_hash,
        config_hash=rows[0].config_hash,
        acceptance_results=acceptance_results,
        oof_metrics={
            "model_only": {"brier": model_brier, "log_loss": model_log_loss},
            "blend": {"brier": blend_brier, "log_loss": blend_log_loss},
            **metrics,
            "bootstrap": bootstrap,
        },
    )
    return report


def build_policy_artifact(policy_id: str, gate_reports: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Create an artifact only from passed, hash-complete OOF reports."""
    entries = []
    for report in gate_reports:
        if report.get("status") != "passed" or report.get("final_serving_weight") is None:
            raise MarketBlendBlockedError("cannot build a serving artifact from an uncleared gate")
        config_hash = report.get("config_hash")
        model_hash = report.get("model_artifact_hash")
        spec_hash = report.get("experiment_spec_hash")
        if any(not _is_sha256(value) for value in (config_hash, model_hash, spec_hash)):
            raise MarketBlendBlockedError("gate report lacks verified model/config/spec hashes")
        entries.append(
            {
                "sport": report["sport"],
                "market": report["market"],
                "weight": report["final_serving_weight"],
                "model_artifact_hash": model_hash,
                "config_hash": config_hash,
                "evidence_dataset_hash": report["dataset_hash"],
                "experiment_spec_hash": spec_hash,
                "implementation_hash": report["implementation_hash"],
                "lineage_manifest_hash": report["lineage_manifest_hash"],
                "training_inputs": report["training_inputs"],
                "fold_definition": report["fold_definition"],
                "oof_metrics": report["oof_metrics"],
                "gate_status": "passed",
            }
        )
    artifact: dict[str, Any] = {
        "schema_version": POLICY_SCHEMA_VERSION,
        "policy_id": policy_id,
        "entries": entries,
    }
    artifact["artifact_hash"] = canonical_hash(artifact)
    return artifact
