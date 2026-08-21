"""The single production model registry.

One source of truth for what is in production and how each model's contract
resolves. This replaces the split personality where
``config/production.yaml`` listed 13 models in ``allowed_models`` but
validation, health, and the predict cycle only ever saw the single
``primary`` canary:

- every entry carries ``model_id``, ``sport``, ``market``, implementation
  type (``json_artifact`` / ``code_backed_model`` / ``rating_engine``),
  artifact path, verified hash, feature-schema version, enabled state, and
  rollback model;
- loading validates **every enabled entry** and fails *that model* closed
  (``load_error`` recorded, resolution refused) when its contract cannot
  resolve — a broken secondary model must never silently look served;
- the **primary**'s failure is a hard ``ValueError``, matching the
  canary's fail-closed primary contract;
- legacy ``schema_version`` 1/2 configs (``allowed_models`` +
  ``artifact_map``) are still accepted and derive the same entries, so
  older fixtures and operators' existing files keep validating.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

IMPLEMENTATION_JSON_ARTIFACT = "json_artifact"
IMPLEMENTATION_CODE_BACKED = "code_backed_model"
IMPLEMENTATION_RATING_ENGINE = "rating_engine"
IMPLEMENTATION_TYPES = (
    IMPLEMENTATION_JSON_ARTIFACT,
    IMPLEMENTATION_CODE_BACKED,
    IMPLEMENTATION_RATING_ENGINE,
)

# Code-backed production models and their factory entry points. Kept as
# module data (rather than a per-consumer allowlist) so the registry can
# resolve them like any other contract; ``config/production.yaml`` v3
# declares these explicitly and can override the entry.
CODE_BACKED_ENTRYPOINTS: dict[str, str] = {
    "soccer-poisson-dc-v1": "model_prediction.models.soccer:soccer_model",
    "tennis-surface-elo-v1": "model_prediction.models.tennis:tennis_model",
}


def compute_artifact_hash(payload: dict[str, Any]) -> str:
    """SHA-256 of the canonical JSON form, excluding the embedded hash field.

    Matches the convention used in ``total_score._artifact_hash`` and
    ``international_baseball``: sort keys, compact separators, and skip the
    ``artifact_hash`` key so the hash isn't self-referential.
    """
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
    ) -> None:
        self.entries = entries
        self.primary = primary
        self.schema_version = schema_version
        self.fallback_action = fallback_action
        self.automated_orders = automated_orders
        self.manual_orders_only = manual_orders_only
        self.health = health or {}
        # sport -> market -> champion model_id. The champion is what
        # SERVES a sport/market; the primary is what the canary predict
        # cycle runs. They are separate notions on purpose.
        self.champions = champions or {}

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

        return cls(
            entries,
            primary,
            schema_version=str(config.get("schema_version", "unknown")),
            fallback_action=svc.get("fallback_action", "no_prediction"),
            automated_orders=bool(execution.get("automated_orders", False)),
            manual_orders_only=bool(execution.get("manual_orders_only", True)),
            health=config.get("health") or {},
            champions={str(s): dict(m) for s, m in champions.items()},
        )

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
        """Derive entries from the v1/v2 ``allowed_models`` + ``artifact_map``.

        Legacy configs only declare sport/market for the primary; every
        other model's identity comes from its own artifact (or the
        code-backed entrypoint table). Error messages keep the exact
        wording the canary validator has always raised.
        """
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
            # Legacy configs only declare sport/market for the primary, so
            # identity comes from each model's own artifact (or the
            # code-backed entrypoint table) — sport is deliberately NOT
            # copied from primary_spec here; the artifact's own field wins.
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
                # No artifact declared: only resolvable when a code-backed
                # entrypoint exists for this model id.
                raw["implementation"] = IMPLEMENTATION_CODE_BACKED
            entries[model_id] = ProductionModelRegistry._resolve_entry(raw, root)
        return entries, primary_id

    # --------------------------------------------------------- per-entry

    @staticmethod
    def _resolve_entry(raw: dict[str, Any], root: Path) -> ProductionModelEntry:
        """Resolve + validate one entry; failures land in ``load_error``."""
        model_id = raw["model_id"]
        implementation = raw.get("implementation", IMPLEMENTATION_JSON_ARTIFACT)
        if implementation not in IMPLEMENTATION_TYPES:
            return ProductionModelEntry(
                model_id=model_id,
                sport=raw.get("sport", ""),
                market=raw.get("market", ""),
                implementation=implementation,
                enabled=bool(raw.get("enabled", True)),
                rollback_model=raw.get("rollback_model"),
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
            )

        # code_backed_model / rating_engine: the contract is a resolvable
        # Python entry point ("pkg.mod:attr").
        entry = raw.get("entry") or CODE_BACKED_ENTRYPOINTS.get(model_id)
        if not entry:
            return ProductionModelEntry(
                model_id=model_id,
                sport=raw.get("sport", ""),
                market=raw.get("market", ""),
                implementation=implementation,
                enabled=bool(raw.get("enabled", True)),
                rollback_model=raw.get("rollback_model"),
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
        )

    # -------------------------------------------------------------- access

    def resolve(self, sport: str, market: str) -> ProductionModelEntry | None:
        """Return the enabled, resolved entry for a sport+market, or None.

        Refuses entries that failed validation — resolution is the
        fail-closed gate, not just a lookup.
        """
        for entry in self.entries.values():
            if (
                entry.available
                and entry.sport.lower() == sport.lower()
                and entry.market.lower() == market.lower()
            ):
                return entry
        return None

    def champion(self, sport: str, market: str) -> ProductionModelEntry | None:
        """The model that SERVES a sport+market, or None.

        Explicit ``champions`` pointers win; without one, falls back to
        the unique registered entry (legacy configs). A champion whose
        contract failed validation resolves to None — fail closed, never
        silently serve a broken champion.
        """
        model_id = (self.champions.get(sport.upper()) or {}).get(market)
        if model_id:
            entry = self.entries.get(model_id)
            if entry is not None and entry.available:
                return entry
            return None
        return self.resolve(sport, market)

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
