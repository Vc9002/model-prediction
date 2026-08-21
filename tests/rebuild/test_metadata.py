"""Tests for MetadataDB (src/model_prediction/rebuild/metadata.py).

Real bug fixed: update_source_health() was UPDATE-only against the
`sources` table, and register_source() (the only INSERT path) had zero
real callers anywhere in this codebase -- every collector calls
update_source_health() on every real collection, against a row that never
existed. With PRAGMA foreign_keys=ON (set in MetadataDB.__init__),
entity_mappings.source_id's real foreign key against sources(source_id)
meant IdentityRegistry.map() would fail closed for any real source, since
none had ever actually been inserted into `sources`.
"""

from __future__ import annotations

from model_prediction.rebuild.identity import IdentityRegistry
from model_prediction.rebuild.metadata import MetadataDB


class TestUpdateSourceHealth:
    def test_creates_the_source_row_if_it_does_not_exist(self, tmp_path):
        meta = MetadataDB(str(tmp_path / "test.db"))

        meta.update_source_health("espn_public", "active")

        rows = meta.source_status()
        assert any(r["source_id"] == "espn_public" and r["status"] == "active" for r in rows)

    def test_repeated_calls_update_the_same_row_not_duplicate(self, tmp_path):
        meta = MetadataDB(str(tmp_path / "test.db"))

        meta.update_source_health("espn_public", "active")
        meta.update_source_health("espn_public", "degraded", "timeout")

        rows = [r for r in meta.source_status() if r["source_id"] == "espn_public"]
        assert len(rows) == 1
        assert rows[0]["status"] == "degraded"
        assert rows[0]["last_error"] == "timeout"

    def test_explicit_register_source_is_not_overwritten_by_health_update(self, tmp_path):
        meta = MetadataDB(str(tmp_path / "test.db"))
        meta.register_source("espn_public", "ESPN Public API", "official_public")

        meta.update_source_health("espn_public", "active")

        rows = [r for r in meta.source_status() if r["source_id"] == "espn_public"]
        assert rows[0]["tier"] == "official_public"  # INSERT OR IGNORE must not clobber it


class TestIdentityRegistryRealSourceMapping:
    """The actual real bug this was blocking: mapping an entity to a real
    collector source (e.g. espn_public) requires that source to already
    exist in `sources`, because entity_mappings has a real, enforced
    foreign key against it."""

    def test_mapping_to_a_source_only_seen_via_update_source_health_succeeds(self, tmp_path):
        meta = MetadataDB(str(tmp_path / "test.db"))
        # This is exactly what every real collector does before any
        # identity work -- no explicit register_source() call.
        meta.update_source_health("espn_public", "active")
        registry = IdentityRegistry(meta)

        identity = registry.register(
            entity_type="team",
            canonical_name="Seattle Mariners",
            sport="mlb",
            effective_from_utc="2026-01-01",
            source_id="espn_public",
            source_entity_id="12",
        )

        resolved = registry.resolve("espn_public", "12")
        assert resolved is not None
        assert resolved.entity_id == identity.entity_id
        assert resolved.canonical_name == "Seattle Mariners"
