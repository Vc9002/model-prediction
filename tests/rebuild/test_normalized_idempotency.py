"""FOUNDATION_COMPLETION.md Phase 2 acceptance tests: normalized/market/feature
storage must be atomic and idempotent, not silently duplicate rows on rerun.

Required by CLAUDE.md's own "Required Focused Tests" list
(tests/rebuild/test_normalized_idempotency.py) but never created until now —
NormalizedStore's primary-key dedupe (commit a4e03a1) shipped with regression
tests for `_dedupe_by_primary_key` directly, but nothing exercised the full
write()/write_books()/write_snapshot() round trip end to end, and
MarketStore/FeatureStore had no primary-key or versioning support at all
before this change.
"""

from __future__ import annotations

import json

import polars as pl

from model_prediction.rebuild import FeatureStore, MarketStore, NormalizedStore


def _row(event_id: str, status: str, observed_at: str) -> dict:
    return {
        "event_id": event_id,
        "status": status,
        "observed_at_utc": observed_at,
        "source": "espn_public",
    }


class TestNormalizedStoreIdempotency:
    def test_rerunning_the_same_collection_creates_no_duplicate_rows(self, tmp_path):
        store = NormalizedStore(tmp_path)
        df = pl.DataFrame([_row("401", "STATUS_SCHEDULED", "2026-08-06T10:00:00")])

        store.write("mlb", "scoreboard", df, primary_key=["event_id"])
        store.write("mlb", "scoreboard", df, primary_key=["event_id"])

        result = store.read("mlb", "scoreboard")
        assert result.height == 1

    def test_later_observation_of_same_key_updates_in_place_not_duplicates(self, tmp_path):
        store = NormalizedStore(tmp_path)
        scheduled = pl.DataFrame([_row("401", "STATUS_SCHEDULED", "2026-08-06T10:00:00")])
        final = pl.DataFrame([_row("401", "STATUS_FINAL", "2026-08-06T22:00:00")])

        store.write("mlb", "scoreboard", scheduled, primary_key=["event_id"])
        store.write("mlb", "scoreboard", final, primary_key=["event_id"])

        result = store.read("mlb", "scoreboard")
        assert result.height == 1
        assert result["status"][0] == "STATUS_FINAL"

    def test_write_is_atomic_no_partial_file_left_on_interruption(self, tmp_path, monkeypatch):
        """A write that fails mid-flight must not leave a corrupt/partial
        Parquet file at the real table path — the temp file must be cleaned
        up and the original (if any) left untouched."""
        store = NormalizedStore(tmp_path)
        original = pl.DataFrame([_row("401", "STATUS_SCHEDULED", "2026-08-06T10:00:00")])
        store.write("mlb", "scoreboard", original, primary_key=["event_id"])

        import model_prediction.rebuild.storage as storage_mod

        def _boom(df, path):
            raise RuntimeError("simulated crash mid-write")

        monkeypatch.setattr(storage_mod, "_atomic_write_parquet", _boom)
        broken = pl.DataFrame([_row("402", "STATUS_SCHEDULED", "2026-08-06T10:05:00")])
        try:
            store.write("mlb", "scoreboard", broken, primary_key=["event_id"])
            raised = False
        except RuntimeError:
            raised = True
        assert raised
        # Original data is intact and still readable — no partial file corrupted it.
        result = store.read("mlb", "scoreboard")
        assert result.height == 1
        assert result["event_id"][0] == "401"
        leftover_tmp = list((tmp_path / "mlb").glob(".*tmp"))
        assert leftover_tmp == []


class TestMarketStoreIdempotency:
    def _book_row(self, market_id: str, side: str, line, price: float, observed_at: str) -> dict:
        return {
            "market_id": market_id,
            "team_or_side": side,
            "line": line,
            "executable_price": price,
            "observed_at_utc": observed_at,
        }

    def test_rerunning_the_same_collection_creates_no_duplicate_rows(self, tmp_path):
        store = MarketStore(tmp_path)
        df = pl.DataFrame([self._book_row("m1", "home", None, 0.55, "2026-08-06T10:00:00")])

        store.write_books("mlb", "2026-08-06", df)
        store.write_books("mlb", "2026-08-06", df)

        result = store.read("mlb", "2026-08-06")
        assert result.height == 1

    def test_a_later_real_price_observation_is_appended_not_dropped(self, tmp_path):
        store = MarketStore(tmp_path)
        first = pl.DataFrame([self._book_row("m1", "home", None, 0.55, "2026-08-06T10:00:00")])
        later = pl.DataFrame([self._book_row("m1", "home", None, 0.58, "2026-08-06T11:00:00")])

        store.write_books("mlb", "2026-08-06", first)
        store.write_books("mlb", "2026-08-06", later)

        result = store.read("mlb", "2026-08-06").sort("observed_at_utc")
        assert result.height == 2
        assert result["executable_price"].to_list() == [0.55, 0.58]


class TestFeatureStoreVersioning:
    def test_write_snapshot_preserves_prior_versions(self, tmp_path):
        store = FeatureStore(tmp_path)
        early = pl.DataFrame({"event_id": ["401"], "feature_x": [1.0]})
        revised = pl.DataFrame({"event_id": ["401"], "feature_x": [2.0]})

        store.write_snapshot("mlb", "late", early, snapshot_hash="hash_v1")
        store.write_snapshot("mlb", "late", revised, snapshot_hash="hash_v2")

        # Latest read reflects the newest version...
        assert store.read("mlb", "late")["feature_x"][0] == 2.0
        # ...but the earlier version is still there, immutable, not overwritten.
        assert store.read_version("mlb", "late", "hash_v1")["feature_x"][0] == 1.0
        versions = store.list_versions("mlb", "late")
        assert {v["snapshot_hash"] for v in versions} == {"hash_v1", "hash_v2"}

    def test_writing_the_same_hash_twice_is_idempotent(self, tmp_path):
        store = FeatureStore(tmp_path)
        df = pl.DataFrame({"event_id": ["401"], "feature_x": [1.0]})

        store.write_snapshot("mlb", "late", df, snapshot_hash="hash_v1")
        store.write_snapshot("mlb", "late", df, snapshot_hash="hash_v1")

        versions = store.list_versions("mlb", "late")
        assert len(versions) == 1

    def test_meta_json_round_trips_for_the_immutable_version(self, tmp_path):
        store = FeatureStore(tmp_path)
        df = pl.DataFrame({"event_id": ["401"], "feature_x": [1.0]})
        store.write_snapshot("mlb", "late", df, snapshot_hash="hash_v1")

        meta = store.read_meta("mlb", "late")
        assert meta["snapshot_hash"] == "hash_v1"
        assert meta["row_count"] == 1

        manifest_path = tmp_path / "mlb" / "late" / "latest.json"
        manifest = json.loads(manifest_path.read_text())
        assert manifest["latest_snapshot_hash"] == "hash_v1"
