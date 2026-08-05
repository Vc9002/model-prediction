"""Canonical identity for events, teams, players, rosters, venues, leagues, competitions, and market contracts.

All identities carry effective dates. Fuzzy matching may propose a mapping but must not
silently authorize one — low-confidence matches fail closed.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

# ── Identity types ──────────────────────────────────────────────────────────

ENTITY_TYPES = (
    "event",
    "team",
    "player",
    "roster",
    "venue",
    "league",
    "competition",
    "market_contract",
)


@dataclass(frozen=True)
class CanonicalIdentity:
    """A stable, sport-scoped identity with effective dates."""
    entity_id: str
    entity_type: str  # one of ENTITY_TYPES
    canonical_name: str
    sport: str
    effective_from_utc: str
    effective_to_utc: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.entity_type not in ENTITY_TYPES:
            raise ValueError(f"entity_type must be one of {ENTITY_TYPES}")


@dataclass(frozen=True)
class SourceMapping:
    """Links a canonical entity to a source-specific identifier."""
    entity_id: str
    source_id: str
    source_entity_id: str
    confidence: float = 1.0  # 0.0 to 1.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")


# ── Name normalization ─────────────────────────────────────────────────────


def normalize_name(name: str) -> str:
    """Normalize a team/player/venue name for fuzzy matching."""
    lowered = name.casefold().strip()
    lowered = re.sub(r"[^\w\s]", "", lowered)
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered


def token_set(name: str) -> set[str]:
    """Extract normalized tokens from a name."""
    return set(normalize_name(name).split())


def jaccard_similarity(a: str, b: str) -> float:
    """Jaccard similarity between two tokenized names."""
    ta = token_set(a)
    tb = token_set(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# ── Identity registry ───────────────────────────────────────────────────────


class IdentityRegistry:
    """In-memory identity registry backed by MetadataDB.

    Fuzzy matching may propose a mapping via propose_match(), but low-confidence
    proposals fail closed — callers must explicitly accept them.
    """

    def __init__(self, metadata: Any) -> None:  # MetadataDB
        self.metadata = metadata
        self._cache: dict[str, CanonicalIdentity] = {}

    # ── registration ──────────────────────────────────────────────────

    def register(
        self,
        entity_type: str,
        canonical_name: str,
        sport: str,
        effective_from_utc: str,
        source_id: str | None = None,
        source_entity_id: str | None = None,
        attributes: dict[str, Any] | None = None,
        confidence: float = 1.0,
    ) -> CanonicalIdentity:
        """Register a canonical identity, optionally mapping a source ID."""
        entity_id = f"{sport}:{entity_type}:{uuid.uuid4().hex[:12]}"
        identity = CanonicalIdentity(
            entity_id=entity_id,
            entity_type=entity_type,
            canonical_name=canonical_name,
            sport=sport,
            effective_from_utc=effective_from_utc,
            attributes=attributes or {},
        )
        self.metadata.register_entity(
            entity_id, entity_type, canonical_name, sport,
            effective_from_utc, attributes,
        )
        if source_id and source_entity_id:
            self.map(entity_id, source_id, source_entity_id, confidence)
        self._cache[entity_id] = identity
        self.metadata.audit_event(
            "entity_registered",
            {"entity_id": entity_id, "entity_type": entity_type, "name": canonical_name, "sport": sport},
            entity_type=entity_type, entity_id=entity_id,
        )
        return identity

    def map(self, entity_id: str, source_id: str, source_entity_id: str, confidence: float = 1.0) -> SourceMapping:
        mapping = SourceMapping(entity_id, source_id, source_entity_id, confidence)
        self.metadata.map_entity(entity_id, source_id, source_entity_id, confidence)
        return mapping

    # ── lookup ─────────────────────────────────────────────────────────

    def resolve(self, source_id: str, source_entity_id: str) -> CanonicalIdentity | None:
        """Resolve a source entity to its canonical identity. Returns None if unmapped."""
        row = self.metadata.entity_by_source(source_id, source_entity_id)
        if row is None:
            return None
        return CanonicalIdentity(
            entity_id=row["entity_id"],
            entity_type=row["entity_type"],
            canonical_name=row["canonical_name"],
            sport=row["sport"],
            effective_from_utc=row["effective_from_utc"],
            effective_to_utc=row["effective_to_utc"],
            attributes=json_loads_safe(row["attributes_json"]),
        )

    # ── fuzzy matching ─────────────────────────────────────────────────

    def propose_match(
        self,
        entity_type: str,
        sport: str,
        name: str,
        source_id: str = "",
        min_confidence: float = 0.90,
    ) -> tuple[CanonicalIdentity | None, float]:
        """Propose a match for a name against the registry.

        Returns (identity, confidence). None means no match found.
        confidences below min_confidence always return None — fail closed.
        """
        best: CanonicalIdentity | None = None
        best_score = 0.0
        normalized = normalize_name(name)
        for ident in self._cache.values():
            if ident.entity_type != entity_type or ident.sport != sport:
                continue
            score = jaccard_similarity(normalized, ident.canonical_name)
            if score > best_score:
                best_score = score
                best = ident
        if best_score < min_confidence:
            return None, best_score
        return best, best_score


def json_loads_safe(value: str | None) -> dict[str, Any]:
    import json
    if value is None:
        return {}
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
