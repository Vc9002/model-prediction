from __future__ import annotations

from pathlib import Path

from model_prediction.research_io import backup_before_overwrite


def test_backup_before_overwrite_copies_current_content(tmp_path: Path) -> None:
    path = tmp_path / "lol-tiered-elo-v5.json"
    path.write_text('{"ratings": {"T1": 1600}}', encoding="utf-8")

    backup_path = backup_before_overwrite(path)

    assert backup_path == tmp_path / "lol-tiered-elo-v5.previous.json"
    assert backup_path.read_text(encoding="utf-8") == '{"ratings": {"T1": 1600}}'
    # The original is untouched -- this only copies, the caller does the overwrite.
    assert path.read_text(encoding="utf-8") == '{"ratings": {"T1": 1600}}'


def test_backup_before_overwrite_is_a_noop_when_nothing_exists_yet(tmp_path: Path) -> None:
    path = tmp_path / "lol-tiered-elo-v5.json"

    backup_path = backup_before_overwrite(path)

    assert backup_path is None
    assert not (tmp_path / "lol-tiered-elo.previous.json").exists()


def test_backup_before_overwrite_keeps_only_the_immediately_prior_version(tmp_path: Path) -> None:
    """Real gap this closes: esports/KBO/NPB ratings artifacts are
    intentionally overwritten in place every day (unlike MLB's versioned
    production artifacts) -- nothing preserved the artifact a bad refresh
    replaced, so a corrupted day's ratings had no recovery path. One prior
    version is what was asked for, not a full history."""
    path = tmp_path / "npb-tie-aware-elo-v1.json"
    path.write_text("day-1", encoding="utf-8")
    backup_before_overwrite(path)
    path.write_text("day-2", encoding="utf-8")

    backup_path = backup_before_overwrite(path)
    path.write_text("day-3", encoding="utf-8")

    assert backup_path.read_text(encoding="utf-8") == "day-2"  # not day-1
