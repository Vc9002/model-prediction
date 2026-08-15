"""Tests for the frozen PIT feature table freezer (consolidation B/C)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from model_prediction.feature_freezer import freeze_features
from model_prediction.ingest import PARSER_VERSION, Ingestor


class _FakeESPN:
    def __init__(self) -> None:
        self.calls = 0

    def scoreboard(self, league: str, game_date: str):
        self.calls += 1
        return {
            "events": [
                {
                    "id": f"e{self.calls}",
                    "date": f"{game_date}T23:00:00Z",
                    "competitions": [
                        {
                            "status": {"type": {"completed": True, "state": "post"}},
                            "competitors": [
                                {"homeAway": "away", "score": "3",
                                 "team": {"id": "1", "displayName": "Aways"}},
                                {"homeAway": "home", "score": "5",
                                 "team": {"id": "2", "displayName": "Homes"}},
                            ],
                        }
                    ],
                }
            ]
        }


def test_ingest_rows_carry_raw_provenance(tmp_path) -> None:
    """Every normalized row traces to its raw snapshot (item 7): source,
    content hash of the exact payload, and parser version."""
    fake = _FakeESPN()
    ingestor = Ingestor(tmp_path, client=fake, rate_limit_seconds=0)
    ingestor.ingest_scores("mlb", "2020-01-01")

    line = ingestor.processed_path("mlb").read_text().strip().splitlines()[0]
    row = json.loads(line)
    assert row["raw_source"] == "espn:MLB:2020-01-01"
    assert row["parser_version"] == PARSER_VERSION
    # The hash must match the exact raw payload the ingestor cached (the
    # fake returns a fresh event id per call, so recomputing from a second
    # call would hash a different payload — read the cache file instead).
    import hashlib

    cached = json.loads(
        (tmp_path / "raw" / "mlb" / "2020-01-01" / "scores_mlb.json").read_text(
            encoding="utf-8"
        )
    )
    expected = hashlib.sha256(
        json.dumps(cached, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert row["raw_hash"] == expected


def test_freeze_writes_rows_and_manifest(tmp_path) -> None:
    """The frozen table + manifest let an experiment cite its dataset.

    Integration smoke against real MLB history: data/ was untracked in
    the K consolidation (2026-08-15), so the machine-local historical
    data only exists on the operator's machine — skip in clean checkouts
    (CI) rather than depending on untracked files.
    """
    if not (Path("data") / "historical" / "mlb_games_all.jsonl").is_file():
        pytest.skip("machine-local MLB history not present (data/ untracked since K)")
    out = tmp_path / "features" / "pit_mlb.jsonl"
    manifest = freeze_features(
        sport="mlb", out_path=out, data_root=Path("data")
    )

    assert out.is_file()
    assert manifest["rows"] > 0
    assert manifest["dataset_hash"] and manifest["feature_schema_hash"]
    assert manifest["builder"] == "validation.build_walk_forward_rows"
    manifest_path = out.with_suffix(out.suffix + ".manifest.json")
    assert manifest_path.is_file()
    # The manifest's hashes verify against the files it describes.
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["dataset_hash"] == manifest["dataset_hash"]


def test_freeze_empty_history_writes_empty_table(tmp_path) -> None:
    out = tmp_path / "pit.jsonl"
    manifest = freeze_features(sport="nfl", out_path=out, data_root=tmp_path)
    assert manifest["rows"] == 0
    assert out.read_text(encoding="utf-8") == ""
