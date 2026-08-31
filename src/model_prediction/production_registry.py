"""The single production model registry.

One source of truth for what is in production and how each model's contract
resolves. Governs the champion-challenger architecture:
- Every supported sport/market always has exactly one production-serving champion.
- Challengers, rollbacks, and research queues are tracked explicitly.
- Weak evidence (degraded) sets high/critical replacement priority but NEVER
  removes the market from production serving.
- Loading validates every enabled entry and fails closed per model when its contract
  cannot resolve.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .model_lifecycle import (
    SUPPORTED_MARKETS,
    EvidenceStatus,
    ModelLifecycleContract,
    ReplacementPriority,
    ServingStatus,
    validate_production_lifecycle,
)

IMPLEMENTATION_JSON_ARTIFACT = "json_artifact"
IMPLEMENTATION_CODE_BACKED = "code_backed_model"
IMPLEMENTATION_RATING_ENGINE = "rating_engine"
IMPLEMENTATION_TYPES = (
    IMPLEMENTATION_JSON_ARTIFACT,
    IMPLEMENTATION_CODE_BACKED,
    IMPLEMENTATION_RATING_ENGINE,
)

# Code-backed production models and their factory entry points.
CODE_BACKED_ENTRYPOINTS: dict[str, str] = {
    "soccer-poisson-dc-v1": "model_prediction.models.soccer:soccer_model",
    "tennis-surface-elo-v1": "model_prediction.models.tennis:tennis_model",
    "college-football-v1": "model_prediction.models.college_football:cfb_model",
    "cfb-spread-v1": "model_prediction.models.college_football:cfb_model",
    "cfb-total-v1": "model_prediction.models.college_football:cfb_model",
}


def compute_artifact_hash(payload: dict[str, Any]) -> str:
    """SHA-256 of the canonical JSON form, excluding the embedded hash field."""
    canonical = {k: v for k, v in payload.items() if k != "artifact_hash"}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class ProductionModelEntry:
    """One production model's resolved contract."""

    model_id: str
    sport: str
    market: str
    implementation: str
    artifact: str | None = None
    artifact_hash: str | None = None
    feature_schema_version: str | None = None
    enabled: bool = True
    rollback_model: str | None = None
    entry: str | None = None
    load_error: str | None = None
    serving_status: str = ServingStatus.PRODUCTION.value
    evidence_status: str = EvidenceStatus.HISTORICAL_ONLY.value
    replacement_priority: str = ReplacementPriority.MEDIUM.value
    challenger_model_id: str | None = None

    @property
    def available(self) -> bool:
        """Resolvable AND enabled — the only entries that may be served."""
        return self.enabled and self.load_error is None


class ProductionModelRegistry:
    """Loaded + validated view of ``config/production.yaml``."""

    def __init__(
        self,
        entries: dict[str, ProductionModelEntry],
        primary: ProductionModelEntry,
        *,
        schema_version: str,
        fallback_action: str,
        automated_orders: bool,
        manual_orders_only: bool,
        health: dict[str, Any] | None = None,
        champions: dict[str, dict[str, str]] | None = None,
        challengers: dict[str, dict[str, str]] | None = None,
        contracts: dict[str, dict[str, ModelLifecycleContract]] | None = None,
        research_queue: list[dict[str, Any]] | None = None,
        blocked_workflows: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.entries = entries
        self.primary = primary
        self.schema_version = schema_version
        self.fallback_action = fallback_action
        self.automated_orders = automated_orders
        self.manual_orders_only = manual_orders_only
        self.health = health or {}
        # sport -> market -> champion model_id
        self.champions = champions or {}
        # sport -> market -> challenger model_id
        self.challengers = challengers or {}
        # sport -> market -> ModelLifecycleContract
        self.contracts = contracts or {}
        # Structured research queue
        self.research_queue = research_queue or []
        # Blocked workflows
        self.blocked_workflows = blocked_workflows or {}

    # ------------------------------------------------------------- loading

    @classmethod
    def load(cls, repo_root: Path | str) -> ProductionModelRegistry:
        """Load ``config/production.yaml`` and validate every entry."""
        root = Path(repo_root)
        config_path = root / "config" / "production.yaml"
        if not config_path.is_file():
            raise FileNotFoundError(f"production config not found at {config_path}")
        with open(config_path, "r", encoding="utf-8") as fh:
            config: dict[str, Any] = yaml.safe_load(fh) or {}
        return cls.from_config(config, repo_root=root)

    @classmethod
    def from_config(cls, config: dict[str, Any], *, repo_root: Path | str) -> ProductionModelRegistry:
        """Build + validate a registry from an already-loaded config dict."""
        root = Path(repo_root)
        svc = config.get("prediction_service")
        if not isinstance(svc, dict):
            raise ValueError("prediction_service section missing or not a mapping")  # noqa: TRY004

        explicit_models = svc.get("models")
        if isinstance(explicit_models, list):
            entries, primary_id = cls._entries_from_explicit(svc, explicit_models, root)
            cls._validate_explicit_mirrors(svc, explicit_models)
        else:
            entries, primary_id = cls._entries_from_legacy(svc, root)

        primary = entries.get(primary_id)
        if primary is None:
            raise ValueError(f"primary.model_id '{primary_id}' is not a registered production model")
        if primary.load_error is not None:
            raise ValueError(f"primary model '{primary_id}' failed validation: {primary.load_error}")

        execution = config.get("execution") or {}

        champions = svc.get("champions") or {}
        if not isinstance(champions, dict):
            raise ValueError("prediction_service.champions must be a mapping")  # noqa: TRY004
        for sport, markets in champions.items():
            if not isinstance(markets, dict):
                raise ValueError(f"champions[{sport}] must be a mapping of market -> model_id")  # noqa: TRY004
            for model_id in markets.values():
                if model_id not in entries:
                    raise ValueError(f"champions references unknown model '{model_id}' for {sport}")
            for market, model_id in markets.items():
                entry = entries[model_id]
                if (
                    entry.sport.casefold() != str(sport).casefold()
                    or entry.market.casefold() != str(market).casefold()
                ):
                    raise ValueError(
                        f"champions[{sport}][{market}] points to '{model_id}' with contract "
                        f"{entry.sport}/{entry.market}"
                    )

        challengers = svc.get("challengers") or {}
        if not isinstance(challengers, dict):
            challengers = {}

        research_queue = svc.get("research_queue") or []
        if not isinstance(research_queue, list):
            research_queue = []

        # Construct lifecycle contracts
        contracts: dict[str, dict[str, ModelLifecycleContract]] = {}
        for sport, markets in champions.items():
            sport_key = str(sport).upper()
            contracts[sport_key] = {}
            for market, champ_id in markets.items():
                champ_entry = entries.get(champ_id)
                chall_id = (challengers.get(sport_key) or {}).get(market)
                rollback_id = champ_entry.rollback_model if champ_entry else None

                contracts[sport_key][market] = ModelLifecycleContract(
                    sport=sport_key,
                    market=market,
                    champion_model_id=champ_id,
                    challenger_model_id=chall_id,
                    rollback_model_id=rollback_id,
                    serving_status=champ_entry.serving_status
                    if champ_entry
                    else ServingStatus.PRODUCTION.value,
                    evidence_status=champ_entry.evidence_status
                    if champ_entry
                    else EvidenceStatus.HISTORICAL_ONLY.value,
                    replacement_priority=champ_entry.replacement_priority
                    if champ_entry
                    else ReplacementPriority.MEDIUM.value,
                    champion_artifact_hash=champ_entry.artifact_hash if champ_entry else None,
                    challenger_artifact_hash=entries[chall_id].artifact_hash if chall_id in entries else None,
                    rollback_artifact_hash=entries[rollback_id].artifact_hash
                    if rollback_id and rollback_id in entries
                    else None,
                )

        blocked_workflows = cls._parse_blocked_workflows(svc, entries)

        return cls(
            entries,
            primary,
            schema_version=str(config.get("schema_version", "unknown")),
            fallback_action=svc.get("fallback_action", "no_prediction"),
            automated_orders=bool(execution.get("automated_orders", False)),
            manual_orders_only=bool(execution.get("manual_orders_only", True)),
            health=config.get("health") or {},
            champions={str(s): dict(m) for s, m in champions.items()},
            challengers={str(s): dict(m) for s, m in challengers.items()}
            if isinstance(challengers, dict)
            else {},
            contracts=contracts,
            research_queue=list(research_queue) if isinstance(research_queue, list) else [],
            blocked_workflows=blocked_workflows,
        )

    @staticmethod
    def _validate_explicit_mirrors(svc: dict[str, Any], models: list[Any]) -> None:
        """Require legacy mirrors, when present, to match ``models`` exactly."""
        model_ids = [str(raw.get("model_id")) for raw in models if isinstance(raw, dict)]
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("prediction_service.models contains duplicate model_id values")

        if "allowed_models" in svc:
            allowed = svc.get("allowed_models")
            if not isinstance(allowed, list) or allowed != model_ids:
                raise ValueError(
                    "prediction_service.allowed_models must exactly mirror models model_id order"
                )

        if "artifact_map" in svc:
            artifact_map = svc.get("artifact_map")
            if not isinstance(artifact_map, dict):
                raise ValueError("prediction_service.artifact_map must be a mapping")
            expected = {
                str(raw["model_id"]): str(raw["artifact"])
                for raw in models
                if isinstance(raw, dict)
                and raw.get("implementation", IMPLEMENTATION_JSON_ARTIFACT) == IMPLEMENTATION_JSON_ARTIFACT
                and raw.get("artifact")
            }
            if artifact_map != expected:
                raise ValueError(
                    "prediction_service.artifact_map must exactly mirror json_artifact entries in models"
                )

    @staticmethod
    def _parse_blocked_workflows(
        svc: dict[str, Any], entries: dict[str, ProductionModelEntry]
    ) -> dict[str, dict[str, Any]]:
        raw_workflows = svc.get("blocked_workflows") or []
        if not isinstance(raw_workflows, list):
            raise ValueError("prediction_service.blocked_workflows must be a list")  # noqa: TRY004
        blocked: dict[str, dict[str, Any]] = {}
        for raw in raw_workflows:
            if not isinstance(raw, dict):
                raise ValueError("blocked workflow entry must be a mapping")  # noqa: TRY004
            model_id = raw.get("model_id")
            reason = raw.get("reason")
            if not isinstance(model_id, str) or not model_id.strip():
                raise ValueError("blocked workflow entry missing model_id")
            if model_id in blocked:
                raise ValueError(f"duplicate blocked workflow '{model_id}'")
            if model_id in entries:
                raise ValueError(f"blocked workflow '{model_id}' is also a registered production model")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(f"blocked workflow '{model_id}' is missing a reason")
            blocked[model_id] = dict(raw)
        return blocked

    @staticmethod
    def _entries_from_explicit(
        svc: dict[str, Any], models: list[Any], root: Path
    ) -> tuple[dict[str, ProductionModelEntry], str]:
        """Build entries from a v3 ``models:`` list. Fail-closed per model."""
        primary_spec = svc.get("primary") or {}
        primary_id = primary_spec.get("model_id", "")
        if not isinstance(primary_id, str) or not primary_id.strip():
            raise ValueError("prediction_service.primary.model_id is missing or empty")

        entries: dict[str, ProductionModelEntry] = {}
        for raw in models:
            if not isinstance(raw, dict):
                raise ValueError(f"production model entry must be a mapping: {raw!r}")  # noqa: TRY004
            model_id = raw.get("model_id")
            if not isinstance(model_id, str) or not model_id.strip():
                raise ValueError("production model entry missing model_id")
            if model_id in entries:
                raise ValueError(f"duplicate production model entry '{model_id}'")
            entries[model_id] = ProductionModelRegistry._resolve_entry(raw, root)
        if not entries:
            raise ValueError("prediction_service.models must contain at least one model")
        if primary_id not in entries:
            raise ValueError(
                f"primary.model_id '{primary_id}' is not in prediction_service.models {sorted(entries)}"
            )
        return entries, primary_id

    @staticmethod
    def _entries_from_legacy(svc: dict[str, Any], root: Path) -> tuple[dict[str, ProductionModelEntry], str]:
        """Derive entries from the v1/v2 ``allowed_models`` + ``artifact_map``."""
        allowed = svc.get("allowed_models")
        if not isinstance(allowed, list) or len(allowed) == 0:
            raise ValueError("prediction_service.allowed_models must be a non-empty list")

        primary_spec = svc.get("primary")
        if not isinstance(primary_spec, dict):
            raise ValueError("prediction_service.primary missing or not a mapping")  # noqa: TRY004
        primary_id = primary_spec.get("model_id")
        if not isinstance(primary_id, str) or not primary_id.strip():
            raise ValueError("prediction_service.primary.model_id is missing or empty")
        if primary_id not in allowed:
            raise ValueError(f"primary.model_id '{primary_id}' is not in allowed_models {allowed}")

        artifact_map = svc.get("artifact_map") or {}
        primary_artifact = primary_spec.get("artifact")

        entries: dict[str, ProductionModelEntry] = {}
        for model_id in allowed:
            raw: dict[str, Any] = {"model_id": model_id}
            if model_id == primary_id:
                raw.update(
                    {
                        "market": primary_spec.get("market", ""),
                        "artifact": primary_artifact or artifact_map.get(model_id),
                    }
                )
            elif model_id in artifact_map:
                raw["artifact"] = artifact_map[model_id]
            else:
                raw["implementation"] = IMPLEMENTATION_CODE_BACKED
            entries[model_id] = ProductionModelRegistry._resolve_entry(raw, root)
        return entries, primary_id

    # --------------------------------------------------------- per-entry

    @staticmethod
    def _resolve_entry(raw: dict[str, Any], root: Path) -> ProductionModelEntry:
        """Resolve + validate one entry; failures land in ``load_error``."""
        model_id = raw["model_id"]
        implementation = raw.get("implementation", IMPLEMENTATION_JSON_ARTIFACT)
        serving_status = str(
            raw.get(
                "serving_status",
                ServingStatus.PRODUCTION.value if raw.get("enabled", True) else ServingStatus.RETIRED.value,
            )
        )
        evidence_status = str(raw.get("evidence_status", EvidenceStatus.HISTORICAL_ONLY.value))
        replacement_priority = str(raw.get("replacement_priority", ReplacementPriority.MEDIUM.value))
        challenger_model_id = raw.get("challenger_model") or raw.get("challenger")

        if implementation not in IMPLEMENTATION_TYPES:
            return ProductionModelEntry(
                model_id=model_id,
                sport=raw.get("sport", ""),
                market=raw.get("market", ""),
                implementation=implementation,
                enabled=bool(raw.get("enabled", True)),
                rollback_model=raw.get("rollback_model"),
                serving_status=serving_status,
                evidence_status=evidence_status,
                replacement_priority=replacement_priority,
                challenger_model_id=challenger_model_id,
                load_error=(
                    f"unknown implementation type {implementation!r}; expected one of {IMPLEMENTATION_TYPES}"
                ),
            )

        if implementation == IMPLEMENTATION_JSON_ARTIFACT:
            artifact_rel = raw.get("artifact")
            if not artifact_rel:
                return ProductionModelEntry(
                    model_id=model_id,
                    sport=raw.get("sport", ""),
                    market=raw.get("market", ""),
                    implementation=implementation,
                    enabled=bool(raw.get("enabled", True)),
                    rollback_model=raw.get("rollback_model"),
                    serving_status=serving_status,
                    evidence_status=evidence_status,
                    replacement_priority=replacement_priority,
                    challenger_model_id=challenger_model_id,
                    load_error="json_artifact entry has no artifact path",
                )
            artifact_path = root / artifact_rel
            if not artifact_path.is_file():
                return ProductionModelEntry(
                    model_id=model_id,
                    sport=raw.get("sport", ""),
                    market=raw.get("market", ""),
                    implementation=implementation,
                    artifact=str(artifact_rel),
                    enabled=bool(raw.get("enabled", True)),
                    rollback_model=raw.get("rollback_model"),
                    serving_status=serving_status,
                    evidence_status=evidence_status,
                    replacement_priority=replacement_priority,
                    challenger_model_id=challenger_model_id,
                    load_error=f"artifact file not found at {artifact_path}",
                )
            try:
                payload: dict[str, Any] = json.loads(artifact_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                return ProductionModelEntry(
                    model_id=model_id,
                    sport=raw.get("sport", ""),
                    market=raw.get("market", ""),
                    implementation=implementation,
                    artifact=str(artifact_rel),
                    enabled=bool(raw.get("enabled", True)),
                    rollback_model=raw.get("rollback_model"),
                    serving_status=serving_status,
                    evidence_status=evidence_status,
                    replacement_priority=replacement_priority,
                    challenger_model_id=challenger_model_id,
                    load_error=f"artifact unreadable: {exc}",
                )
            embedded_hash = payload.get("artifact_hash")
            computed_hash = compute_artifact_hash(payload)
            if embedded_hash != computed_hash:
                return ProductionModelEntry(
                    model_id=model_id,
                    sport=raw.get("sport", ""),
                    market=raw.get("market", ""),
                    implementation=implementation,
                    artifact=str(artifact_rel),
                    enabled=bool(raw.get("enabled", True)),
                    rollback_model=raw.get("rollback_model"),
                    serving_status=serving_status,
                    evidence_status=evidence_status,
                    replacement_priority=replacement_priority,
                    challenger_model_id=challenger_model_id,
                    load_error=(
                        f"artifact_hash mismatch: embedded '{embedded_hash}' != computed '{computed_hash}'"
                    ),
                )
            artifact_model_id = payload.get("model_version")
            if artifact_model_id != model_id:
                return ProductionModelEntry(
                    model_id=model_id,
                    sport=raw.get("sport", ""),
                    market=raw.get("market", ""),
                    implementation=implementation,
                    artifact=str(artifact_rel),
                    enabled=bool(raw.get("enabled", True)),
                    rollback_model=raw.get("rollback_model"),
                    serving_status=serving_status,
                    evidence_status=evidence_status,
                    replacement_priority=replacement_priority,
                    challenger_model_id=challenger_model_id,
                    load_error=(
                        f"model_id mismatch: config says '{model_id}', artifact says '{artifact_model_id}'"
                    ),
                )
            return ProductionModelEntry(
                model_id=model_id,
                sport=raw.get("sport") or str(payload.get("sport", "")),
                market=raw.get("market", ""),
                implementation=implementation,
                artifact=str(artifact_rel),
                artifact_hash=computed_hash,
                feature_schema_version=str(payload.get("schema_version", "unknown")),
                enabled=bool(raw.get("enabled", True)),
                rollback_model=raw.get("rollback_model"),
                serving_status=serving_status,
                evidence_status=evidence_status,
                replacement_priority=replacement_priority,
                challenger_model_id=challenger_model_id,
            )

        # code_backed_model / rating_engine
        entry = raw.get("entry") or CODE_BACKED_ENTRYPOINTS.get(model_id)
        if not entry:
            return ProductionModelEntry(
                model_id=model_id,
                sport=raw.get("sport", ""),
                market=raw.get("market", ""),
                implementation=implementation,
                enabled=bool(raw.get("enabled", True)),
                rollback_model=raw.get("rollback_model"),
                serving_status=serving_status,
                evidence_status=evidence_status,
                replacement_priority=replacement_priority,
                challenger_model_id=challenger_model_id,
                load_error=f"{implementation} entry has no entry point declared",
            )
        try:
            _resolve_entrypoint(entry)
        except (ImportError, AttributeError, ValueError) as exc:
            return ProductionModelEntry(
                model_id=model_id,
                sport=raw.get("sport", ""),
                market=raw.get("market", ""),
                implementation=implementation,
                entry=entry,
                enabled=bool(raw.get("enabled", True)),
                rollback_model=raw.get("rollback_model"),
                serving_status=serving_status,
                evidence_status=evidence_status,
                replacement_priority=replacement_priority,
                challenger_model_id=challenger_model_id,
                load_error=f"entry point {entry!r} does not resolve: {exc}",
            )
        return ProductionModelEntry(
            model_id=model_id,
            sport=raw.get("sport", ""),
            market=raw.get("market", ""),
            implementation=implementation,
            entry=entry,
            enabled=bool(raw.get("enabled", True)),
            rollback_model=raw.get("rollback_model"),
            serving_status=serving_status,
            evidence_status=evidence_status,
            replacement_priority=replacement_priority,
            challenger_model_id=challenger_model_id,
        )

    # -------------------------------------------------------------- access

    def resolve(self, sport: str, market: str) -> ProductionModelEntry | None:
        """Return the enabled, resolved entry for a sport+market, or None."""
        for entry in self.entries.values():
            if (
                entry.available
                and entry.sport.lower() == sport.lower()
                and entry.market.lower() == market.lower()
            ):
                return entry
        return None

    def champion(self, sport: str, market: str) -> ProductionModelEntry | None:
        """The model that SERVES a sport+market, or None."""
        model_id = (self.champions.get(sport.upper()) or {}).get(market)
        if model_id:
            entry = self.entries.get(model_id)
            if entry is not None and entry.available:
                return entry
            return None
        return self.resolve(sport, market)

    def challenger(self, sport: str, market: str) -> ProductionModelEntry | None:
        """The frozen prospective challenger model for a sport+market, or None."""
        model_id = (self.challengers.get(sport.upper()) or {}).get(market)
        if model_id:
            entry = self.entries.get(model_id)
            if entry is not None and entry.available:
                return entry
        return None

    def rollback(self, sport: str, market: str) -> ProductionModelEntry | None:
        """The rollback model for a sport+market's champion, or None."""
        champ = self.champion(sport, market)
        if champ is not None and champ.rollback_model:
            return self.entries.get(champ.rollback_model)
        return None

    def lifecycle_contract(self, sport: str, market: str) -> ModelLifecycleContract | None:
        """The lifecycle contract for a sport+market, or None."""
        return (self.contracts.get(sport.upper()) or {}).get(market)

    def all_lifecycle_contracts(self) -> list[ModelLifecycleContract]:
        """All registered lifecycle contracts across sports and markets."""
        result: list[ModelLifecycleContract] = []
        for sport_contracts in self.contracts.values():
            result.extend(sport_contracts.values())
        return result

    def validate_lifecycle_invariants(
        self,
        supported_markets: dict[str, set[str]] = SUPPORTED_MARKETS,
    ) -> list[str]:
        """Verify that every supported market has an active, valid serving champion."""
        return validate_production_lifecycle(
            self.contracts,
            supported_markets=supported_markets,
            entries_by_id=self.entries,
        )

    def available_entries(self) -> list[ProductionModelEntry]:
        """Enabled entries whose contract resolved."""
        return [e for e in self.entries.values() if e.available]

    def problem_entries(self) -> list[ProductionModelEntry]:
        """Entries that are disabled or failed contract validation."""
        return [e for e in self.entries.values() if not e.available]


def _resolve_entrypoint(entry: str) -> Any:
    """Import ``pkg.mod:attr`` and return the attribute (raises on failure)."""
    if ":" not in entry:
        raise ValueError(f"entry point must be 'pkg.mod:attr', got {entry!r}")
    module_name, attr_name = entry.split(":", 1)
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
