"""Tests for the experiment registry CLI contract (consolidation B/C)."""

from __future__ import annotations


from model_prediction import experiment_registry


def test_record_cli_passes_status_and_git_sha(monkeypatch, tmp_path) -> None:
    """The record CLI used to silently drop --status and --git-sha (the
    API accepted them; main() never passed them) — every experiment
    recorded as 'completed' with no git provenance. Found 2026-08-16 in
    a pre-burn-in smoke of the registry."""
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(tmp_path / "runtime"))
    captured = {}

    def fake_record(**kwargs):
        captured.update(kwargs)
        return {
            "experiment_id": "exp-test",
            "status": kwargs["status"],
            "git_sha": kwargs["git_sha"],
        }

    monkeypatch.setattr(experiment_registry, "record", fake_record)
    code = experiment_registry.main(
        [
            "record",
            "--model-id",
            "m",
            "--status",
            "queued",
            "--git-sha",
            "abc123",
        ]
    )
    assert code == 0
    assert captured["status"] == "queued"
    assert captured["git_sha"] == "abc123"


def test_void_round_trip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(tmp_path / "runtime"))
    row = experiment_registry.record(model_id="m", status="queued")
    voided = experiment_registry.void(row["experiment_id"], "test")
    assert voided["status"] == "void"
    assert experiment_registry.show(row["experiment_id"])["status"] == "void"
