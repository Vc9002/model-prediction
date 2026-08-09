"""Tests for scripts/check_obsolete_paths.py.

Not wired into the blocking CI gate yet -- the current tree has pre-existing
tracked runtime artifacts (dashboard/server.log, old validation reports)
that already embed the legacy checkout path, and fixing those is real,
separate cleanup tied to the live cutover itself, not this path-refactor
change. These tests cover the checker's own logic against isolated fixture
repos, not the real tree.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from check_obsolete_paths import find_obsolete_path_references


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    return repo


def _commit_all(repo: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "test"], cwd=repo, check=True)


def test_flags_a_real_reference_in_tracked_source(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "config.py").write_text('ROOT = "/Users/vincentc9002/model prediction"\n')
    _commit_all(repo)
    errors = find_obsolete_path_references(repo, "/Users/vincentc9002/model prediction")
    assert len(errors) == 1
    assert "config.py:1" in errors[0]


def test_clean_tree_has_no_findings(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "config.py").write_text('ROOT = "/Users/vincentc9002/model-prediction"\n')
    _commit_all(repo)
    assert find_obsolete_path_references(repo, "/Users/vincentc9002/model prediction") == []


def test_markdown_documentation_is_allowed_to_mention_the_old_path(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "MIGRATION.md").write_text("Retired /Users/vincentc9002/model prediction on this date.\n")
    _commit_all(repo)
    assert find_obsolete_path_references(repo, "/Users/vincentc9002/model prediction") == []


def test_untracked_files_are_not_scanned(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("clean\n")
    _commit_all(repo)
    (repo / "untracked.py").write_text('X = "/Users/vincentc9002/model prediction"\n')
    assert find_obsolete_path_references(repo, "/Users/vincentc9002/model prediction") == []
