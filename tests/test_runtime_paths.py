"""Tests for the canonical repo-root/runtime-root path resolver."""

from __future__ import annotations

from pathlib import Path

import pytest

from model_prediction.runtime_paths import RuntimePathError, RuntimePaths


def test_default_dev_fallback_colocates_runtime_under_repo_data(monkeypatch, tmp_path):
    monkeypatch.delenv("MODEL_PREDICTION_RUNTIME_ROOT", raising=False)
    paths = RuntimePaths.resolve(repo_root=tmp_path)
    assert paths.runtime_root == (tmp_path / "data").resolve()
    assert paths.rebuild_root == (tmp_path / "data" / "rebuild").resolve()


def test_explicit_env_override_wins(monkeypatch, tmp_path):
    external = tmp_path / "external-runtime"
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(external))
    paths = RuntimePaths.resolve(repo_root=tmp_path / "repo")
    assert paths.runtime_root == external.resolve()
    assert paths.rebuild_root == (external / "rebuild").resolve()


def test_versioned_evidence_always_stays_under_repo_root(monkeypatch, tmp_path):
    external = tmp_path / "external-runtime"
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(external))
    repo = tmp_path / "repo"
    paths = RuntimePaths.resolve(repo_root=repo)
    assert paths.rebuild_output_root == (repo / "outputs" / "rebuild").resolve()


def test_all_mutable_state_paths_derive_from_runtime_root(tmp_path):
    paths = RuntimePaths(repo_root=tmp_path / "repo", runtime_root=tmp_path / "runtime")
    root = (tmp_path / "runtime" / "rebuild").resolve()
    assert paths.rebuild_raw_root == root / "raw"
    assert paths.rebuild_normalized_root == root / "normalized"
    assert paths.rebuild_feature_root == root / "features"
    assert paths.rebuild_market_root == root / "markets"
    assert paths.rebuild_resume_root == root / "resume_state"
    assert paths.rebuild_shadow_db == root / "shadow.db"
    assert paths.rebuild_metadata_db == root / "metadata.db"
    assert paths.log_root == (tmp_path / "runtime" / "logs").resolve()


def test_runtime_root_cannot_be_the_production_source_package(tmp_path):
    with pytest.raises(RuntimePathError, match="production source package"):
        RuntimePaths(repo_root=tmp_path, runtime_root=tmp_path / "src" / "model_prediction")


def test_runtime_root_cannot_live_inside_a_git_directory(tmp_path):
    with pytest.raises(RuntimePathError, match=r"\.git directory"):
        RuntimePaths(repo_root=tmp_path, runtime_root=tmp_path / ".git" / "runtime")


def test_for_test_never_touches_real_repo_or_runtime_directories(tmp_path):
    paths = RuntimePaths.for_test(tmp_path)
    assert paths.repo_root != Path.cwd()
    assert str(paths.repo_root).startswith(str(tmp_path))
    assert str(paths.runtime_root).startswith(str(tmp_path))
    assert paths.repo_root.is_dir()
    assert paths.runtime_root.is_dir()


def test_for_test_gives_distinct_repo_and_runtime_roots(tmp_path):
    paths = RuntimePaths.for_test(tmp_path)
    assert paths.repo_root != paths.runtime_root


def test_resolve_does_not_depend_on_current_working_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("MODEL_PREDICTION_RUNTIME_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    other_dir = tmp_path / "elsewhere"
    other_dir.mkdir()
    paths = RuntimePaths.resolve(repo_root=other_dir)
    assert paths.repo_root == other_dir.resolve()
    assert paths.runtime_root == (other_dir / "data").resolve()


def test_repo_root_and_runtime_root_paths_with_spaces_are_supported(tmp_path):
    repo = tmp_path / "model prediction"
    runtime = tmp_path / "model prediction runtime"
    paths = RuntimePaths(repo_root=repo, runtime_root=runtime)
    assert paths.repo_root == repo.resolve()
    assert paths.rebuild_shadow_db == (runtime / "rebuild" / "shadow.db").resolve()
