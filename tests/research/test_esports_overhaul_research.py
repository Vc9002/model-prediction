"""Invariant tests for the isolated esports research harness."""

from model_prediction.esports_overhaul_research import (
    audit_history,
    canonical_cohort,
    chronological_features,
    chronological_partition,
    clustered_verdict,
    utc_date,
)


def match(i, start, end, winner="a"):
    return {
        "match_id": str(i),
        "start_utc": start,
        "end_utc": end,
        "team1_id": "a",
        "team2_id": "b",
        "winner_id": winner,
        "best_of": 3,
        "tier": "a",
    }


def test_no_result_update_until_strictly_before_next_start():
    rows = [
        match(1, "2024-01-01T10:00:00Z", "2024-01-01T12:00:00Z"),
        match(2, "2024-01-01T11:00:00Z", "2024-01-01T13:00:00Z"),
        match(3, "2024-01-01T12:00:00Z", "2024-01-01T14:00:00Z"),
        match(4, "2024-01-01T12:01:00Z", "2024-01-01T14:01:00Z"),
    ]
    x = chronological_features(rows, k=32, half_life=None)
    assert list(x[:3, 0]) == [0, 0, 0]
    assert x[3, 0] > 0
    assert list(x[:3, 2]) == [0, 0, 0]


def test_flat_canonical_and_newest_inside_tier():
    base = {
        "sport": "cs2",
        "event_id": "1",
        "market_type": "moneyline",
        "selection": "home",
        "line": None,
        "model_id": "v6",
        "result": "win",
    }
    rows = [
        dict(base, pick_id="m", ledger_tier="main", created_at_utc="2025-03-03T00:00:00Z"),
        dict(base, pick_id="f1", ledger_tier="flat", created_at_utc="2025-03-01T00:00:00Z"),
        dict(base, pick_id="f2", ledger_tier="flat", created_at_utc="2025-03-02T00:00:00Z"),
    ]
    cohort, audit = canonical_cohort(rows)
    assert [r["pick_id"] for r in cohort] == ["f2"]
    assert audit["duplicates_removed"] == 2


def test_conflicting_duplicate_and_missing_end_excluded():
    a = match(1, "2024-01-01T10:00:00Z", "2024-01-01T12:00:00Z")
    b = dict(a, winner_id="b")
    c = dict(a, match_id="2", end_utc=None)
    rows, audit = audit_history([a, b, c])
    assert not rows
    assert audit["excluded"]["conflicting_match_id"] == 2
    assert audit["excluded"]["missing_end"] == 1


def test_verdict_requires_interval_and_practical_effect():
    assert clustered_verdict([-0.1, 0.1], ["a", "b"], threshold=0.002)["verdict"] != "eligible_improvement"
    assert (
        clustered_verdict([0.001] * 40, [str(i) for i in range(40)], threshold=0.002)["verdict"]
        != "eligible_improvement"
    )
    assert (
        clustered_verdict([0.01] * 40, [str(i) for i in range(40)], threshold=0.002)["verdict"]
        == "eligible_improvement"
    )


def test_utc_split_is_disjoint_for_offset_midnight_event():
    rows = [
        match(1, "2024-01-01T00:30:00+02:00", "2024-01-01T01:00:00+02:00"),
        match(2, "2024-01-01T01:00:00Z", "2024-01-01T03:00:00Z"),
    ]
    train, validation = chronological_partition(rows, "2024-01-01", "2024-01-02")
    assert train.tolist() == [0]
    assert validation.tolist() == [1]
    assert utc_date(rows[0]["start_utc"]) == "2023-12-31"
    assert not set(train) & set(validation)


def test_contract_normalization_and_numerical_newest_time():
    base = {
        "sport": "cs2",
        "event_id": "1",
        "market_type": "moneyline",
        "selection": "home",
        "model_id": "v6",
        "result": "win",
        "ledger_tier": "flat",
    }
    a = dict(base, pick_id="older", line=None, created_at_utc="2025-01-01T12:00:00+02:00")
    b = dict(base, pick_id="newer", line="", market_type=" moneyline ", created_at_utc="2025-01-01T11:00:00Z")
    cohort, audit = canonical_cohort([a, b])
    assert [r["pick_id"] for r in cohort] == ["newer"]
    assert audit["duplicates_removed"] == 1
    a["line"], b["line"] = "0", 0
    assert len(canonical_cohort([a, b])[0]) == 1


def test_git_source_resolution_requires_exact_bytes(tmp_path):
    import hashlib
    import subprocess

    from model_prediction.esports_overhaul_research import resolve_git_sources

    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init")
    path = tmp_path / "src/model_prediction/esports.py"
    path.parent.mkdir(parents=True)
    content = b"# immutable incumbent source\n"
    path.write_bytes(content)
    git("add", "src/model_prediction/esports.py")
    git("-c", "user.name=Research Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
    sha = hashlib.sha256(content).hexdigest()
    resolved = resolve_git_sources(tmp_path, {"esports.py": {sha, "0" * 64}})
    assert resolved[("esports.py", sha)]["bytes"] == content
    assert ("esports.py", "0" * 64) not in resolved


def test_git_source_resolution_finds_unreachable_exact_blob(tmp_path):
    import hashlib
    import subprocess

    from model_prediction.esports_overhaul_research import resolve_git_sources

    def git(*args, input=None):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args], check=True, capture_output=True, input=input
        )

    git("init")
    content = b"# archived but deliberately unreachable\n"
    blob = git("hash-object", "-w", "--stdin", input=content).stdout.decode().strip()
    sha = hashlib.sha256(content).hexdigest()
    resolved = resolve_git_sources(tmp_path, {"esports.py": {sha}})
    assert resolved[("esports.py", sha)]["bytes"] == content
    assert resolved[("esports.py", sha)]["blob"] == blob
    assert resolved[("esports.py", sha)]["commit"] is None


def test_residual_keeps_fixed_elo_logit_offset():
    import numpy as np

    from model_prediction.esports_overhaul_research import LogisticResidual

    x = np.column_stack([np.linspace(-2, 2, 100), np.zeros((100, 3))])
    y = np.array([i % 3 != 0 for i in range(100)], dtype=int)
    model = LogisticResidual(0.1).fit(x, y)
    shifted = x.copy()
    shifted[:, 0] += 0.7
    assert np.allclose(model.decision_function(shifted) - model.decision_function(x), 0.7)
    assert model.parameters()["fixed_elo_offset_coefficient"] == 1


def test_malformed_snapshot_classification_and_grouping_fail_closed(tmp_path, monkeypatch):
    import pytest

    import model_prediction.esports_overhaul_research as research

    malformed = {"pick_id": "x", "ledger_tier": "flat", "feature_payload_json": "{"}
    wrong_type = {"pick_id": "y", "ledger_tier": "flat", "feature_payload_json": "[]"}
    for row, reason in ((malformed, "malformed_snapshot_json"), (wrong_type, "invalid_snapshot_structure")):
        with pytest.raises((ValueError, TypeError), match=reason):
            research.snapshot_from_row(row)
    monkeypatch.setattr(research, "resolve_git_sources", lambda *args: {})
    monkeypatch.setattr(
        research,
        "_replay_subprocess",
        lambda rows, root: {
            "rows": {
                r["pick_id"] + ":" + r["ledger_tier"]: {"mismatch": "malformed_snapshot_json"} for r in rows
            },
            "code_sha256": {},
        },
    )
    result = research.archived_control_replays([malformed, wrong_type], tmp_path, tmp_path, tmp_path)
    assert len(result["rows"]) == 2
    assert not result["source_groups"][0]["historical_sources_recovered"]
