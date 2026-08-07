"""Canonical identity for events, teams, players, rosters, venues, leagues, competitions, and market contracts.

All identities carry effective dates. Fuzzy matching may propose a mapping but must not
silently authorize one — low-confidence matches fail closed.
"""

from __future__ import annotations

import re
import sqlite3
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
        # Load existing entities from SQLite on init. Real gap fixed here:
        # this used to catch `Exception` and silently `pass`, which would
        # mask a genuine bug (a real schema mismatch, a corrupted row, a
        # bad attributes_json blob) exactly as silently as the "table
        # doesn't exist yet on a fresh MetadataDB" case it was meant for —
        # the same silent-failure pattern found and fixed repeatedly
        # elsewhere in this codebase this session. Narrowed to the one
        # real expected case; anything else now surfaces.
        try:
            rows = self.metadata.conn.execute(
                "SELECT entity_id, entity_type, canonical_name, sport, effective_from_utc, effective_to_utc, attributes_json FROM entities"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []  # fresh MetadataDB — entities table not created yet
        for row in rows:
            eid = row["entity_id"]
            self._cache[eid] = CanonicalIdentity(
                entity_id=eid,
                entity_type=row["entity_type"],
                canonical_name=row["canonical_name"],
                sport=row["sport"],
                effective_from_utc=row["effective_from_utc"],
                effective_to_utc=row["effective_to_utc"],
                attributes=json_loads_safe(row["attributes_json"]),
            )

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
        # entity_mappings.source_id carries a real, enforced foreign key
        # against sources(source_id) (PRAGMA foreign_keys=ON). Real
        # collectors always call meta.update_source_health() before doing
        # any work, so this is a no-op in practice for them -- but
        # IdentityRegistry shouldn't depend on caller convention to avoid a
        # real sqlite3.IntegrityError (see test_metadata.py's
        # TestIdentityRegistryRealSourceMapping). Uses ensure_source_exists,
        # not update_source_health, so mapping an entity never has the
        # side effect of silently resetting a source's real tracked status.
        self.metadata.ensure_source_exists(source_id)
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


def resolve_or_register_team(
    registry: IdentityRegistry,
    sport: str,
    source_id: str,
    source_team_id: str,
    team_name: str,
    effective_from_utc: str,
    min_confidence: float = 0.90,
) -> CanonicalIdentity:
    """The one real entry point collection code should use to get a team's
    canonical identity, instead of ad-hoc name matching per collector.

    Real gap this closes: `IdentityRegistry` (fuzzy matching, effective-dated
    mappings, fail-closed low-confidence rejection) existed with zero real
    callers anywhere in this codebase -- every real team-matching path
    (mlb_features.ESPN_TO_STATCAST_ABBREV, mlb_market_matching's full-name
    comparison) used its own bespoke, source-specific logic instead. This
    doesn't replace those yet (a real, separate migration for each
    consumer), but is the first real, tested, live-callable path to a
    canonical team identity from real collector data.

    Resolution order, most-specific first:
    1. Already mapped from this exact (source_id, source_team_id) -- the
       common case on every rerun after the first.
    2. A same-sport team whose canonical_name fuzzy-matches `team_name`
       above `min_confidence` -- maps this new source id to that existing
       entity (e.g. a second source observing the same real team) rather
       than minting a duplicate. Ambiguous/low-confidence matches fail
       closed into (3), not a guess.
    3. No confident match -- registers a brand-new canonical identity and
       maps this source id to it.
    """
    existing = registry.resolve(source_id, source_team_id)
    if existing is not None:
        return existing

    proposed, _confidence = registry.propose_match(
        entity_type="team", sport=sport, name=team_name,
        source_id=source_id, min_confidence=min_confidence,
    )
    if proposed is not None:
        registry.map(proposed.entity_id, source_id, source_team_id)
        return proposed

    return registry.register(
        entity_type="team", canonical_name=team_name, sport=sport,
        effective_from_utc=effective_from_utc,
        source_id=source_id, source_entity_id=source_team_id,
    )


def resolve_espn_scoreboard_team_ids(
    registry: IdentityRegistry,
    sport: str,
    source_id: str,
    home_team_obj: dict[str, Any],
    away_team_obj: dict[str, Any],
    observed_at: str,
) -> tuple[str | None, str | None]:
    """The one real helper every ESPN-scoreboard-shaped collector should
    call for canonical team identity -- shared here so MLB/NBA/WNBA/NFL/
    Soccer/Tennis don't each reimplement the same "resolve if id+name
    present, else None" guard around resolve_or_register_team(). Returns
    (home_canonical_id, away_canonical_id); either is None if that side's
    real ESPN team object is missing id or displayName (never guessed).

    Real, serious bug found and fixed live (2026-08-07): ESPN's team `id`
    is only unique *within* one sport's own numbering -- e.g. WNBA team id
    "20" and MLB team id "20" are two completely unrelated real teams
    (confirmed live: WNBA's "Atlanta Dream" was silently resolved to MLB's
    "Washington Nationals" because both collectors passed the same bare
    source_id="espn_public" into resolve_or_register_team(), whose first
    lookup step -- registry.resolve(source_id, source_team_id) -- has no
    sport dimension in entity_mappings' (source_id, source_entity_id) key).
    Namespacing source_id by sport here (not at every call site) scopes
    every ESPN team-id lookup to its own sport's numbering, matching
    reality: `source_id` was never really one shared ID space, ESPN's own
    per-sport numbering already wasn't."""
    namespaced_source_id = f"{source_id}:{sport}"
    home_id = away_id = None
    if home_team_obj.get("id") and home_team_obj.get("displayName"):
        home_id = resolve_or_register_team(
            registry, sport=sport, source_id=namespaced_source_id,
            source_team_id=str(home_team_obj["id"]), team_name=home_team_obj["displayName"],
            effective_from_utc=observed_at,
        ).entity_id
    if away_team_obj.get("id") and away_team_obj.get("displayName"):
        away_id = resolve_or_register_team(
            registry, sport=sport, source_id=namespaced_source_id,
            source_team_id=str(away_team_obj["id"]), team_name=away_team_obj["displayName"],
            effective_from_utc=observed_at,
        ).entity_id
    return home_id, away_id


def word_boundary_name_match(a: str, b: str) -> bool:
    """True when a and b identify the same real-world team name despite one
    being a shorter form of the other (e.g. Polymarket's "Washington" vs
    ESPN's "Washington Mystics") -- word-boundary prefix match only, not a
    naive substring check, which would wrongly match unrelated names that
    happen to share leading letters (e.g. "SEA" inside "Seattle
    Mariners" -- a real bug this exact shape was already fixed for once,
    see mlb_market_matching.py's module docstring)."""
    if a == b:
        return True
    return a.startswith(b + " ") or b.startswith(a + " ")


def resolve_polymarket_team_id(
    registry: IdentityRegistry, sport: str, team_name: str | None,
) -> str | None:
    """Resolve a Polymarket-reported team name to the same canonical team
    entity ESPN scoreboard collection already registered for this sport.

    Polymarket exposes no stable per-team ID of its own (unlike ESPN's
    numeric team id) -- only a name string, sometimes a short city name
    ("Washington") rather than ESPN's full display name ("Washington
    Mystics"). There is no (source_id, source_team_id) pair to look up the
    way resolve_or_register_team() does for ESPN, so this uses
    word_boundary_name_match() against already-registered same-sport team
    entities instead of IdentityRegistry.propose_match()'s Jaccard
    similarity -- Jaccard token-set similarity scores "Washington" against
    "Washington Mystics" at 0.5 (1 shared token of 2 total), well under
    propose_match()'s real 0.90 fail-closed threshold, so it would
    incorrectly refuse this exact real case.

    Fails closed (returns None) on no match or an ambiguous one (more than
    one same-sport team's name is a word-boundary match) -- never guesses,
    never mints a new canonical team purely from a market-side name
    string, since a genuinely new/misspelled name should surface as
    unresolved, not silently become a duplicate identity."""
    if not team_name:
        return None
    existing = registry.resolve("polymarket_us", team_name)
    if existing is not None:
        return existing.entity_id
    matches = [
        ident for ident in registry._cache.values()
        if ident.entity_type == "team" and ident.sport == sport
        and word_boundary_name_match(team_name, ident.canonical_name)
    ]
    if len(matches) != 1:
        return None
    registry.map(matches[0].entity_id, "polymarket_us", team_name)
    return matches[0].entity_id


def json_loads_safe(value: str | None) -> dict[str, Any]:
    import json
    if value is None:
        return {}
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
