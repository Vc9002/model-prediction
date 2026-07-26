"""Tests for audit.py -- the hash-chained append-only audit log.

Zero direct test coverage previously existed for the hash-chaining
algorithm itself (only incidental exercise via other modules' fixtures),
despite it being the one property (tamper-evidence) the whole audit system
exists to provide.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from model_prediction.audit import AuditLockTimeout, AuditLog, _acquire_exclusive_lock


def _recompute_hash(event: dict) -> str:
    canonical = {key: value for key, value in event.items() if key != "event_hash"}
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def test_first_event_chains_from_the_genesis_hash(tmp_path) -> None:
    log = AuditLog(tmp_path / "events.jsonl")
    event = log.append("pick_created", "pick-1", {"units": 1.0})
    assert event["previous_hash"] == "0" * 64
    assert event["event_hash"] == _recompute_hash(event)


def test_chain_links_each_event_to_the_prior_ones_hash(tmp_path) -> None:
    log = AuditLog(tmp_path / "events.jsonl")
    first = log.append("pick_created", "pick-1", {})
    second = log.append("pick_settled", "pick-1", {"result": "win"})
    third = log.append("pick_removed", "pick-1", {"reason": "test"})
    assert second["previous_hash"] == first["event_hash"]
    assert third["previous_hash"] == second["event_hash"]
    # Every event's own hash is a genuine function of its own content.
    for event in (first, second, third):
        assert event["event_hash"] == _recompute_hash(event)


def test_events_returns_every_appended_event_in_order(tmp_path) -> None:
    log = AuditLog(tmp_path / "events.jsonl")
    log.append("pick_created", "a", {})
    log.append("pick_created", "b", {})
    log.append("pick_created", "c", {})
    subjects = [event["subject_id"] for event in log.events()]
    assert subjects == ["a", "b", "c"]


def test_events_on_a_missing_file_returns_empty_list(tmp_path) -> None:
    log = AuditLog(tmp_path / "does_not_exist.jsonl")
    assert log.events() == []


def test_tampering_with_a_payload_after_the_fact_is_detectable(tmp_path) -> None:
    """Simulates the exact check cli.py's _verify_chain performs."""
    path = tmp_path / "events.jsonl"
    log = AuditLog(path)
    log.append("pick_created", "pick-1", {"units": 1.0})

    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["payload"]["units"] = 999.0  # attacker edits the recorded stake
    path.write_text(json.dumps(tampered) + "\n", encoding="utf-8")

    on_disk = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert on_disk["event_hash"] != _recompute_hash(on_disk)


def test_previous_hash_break_is_detectable_if_a_line_is_deleted(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    log = AuditLog(path)
    log.append("pick_created", "a", {})
    log.append("pick_created", "b", {})
    log.append("pick_created", "c", {})

    lines = path.read_text(encoding="utf-8").splitlines()
    # Delete the middle line -- "c" now claims to chain from "a", not "b".
    path.write_text(lines[0] + "\n" + lines[2] + "\n", encoding="utf-8")

    remaining = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert remaining[1]["previous_hash"] != remaining[0]["event_hash"]


def test_append_flushes_and_fsyncs_so_a_reader_sees_it_immediately(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    log = AuditLog(path)
    log.append("pick_created", "pick-1", {})
    # A second, independent AuditLog instance reading the same path must see it.
    assert len(AuditLog(path).events()) == 1


def test_lock_acquire_times_out_when_another_holder_never_releases(tmp_path) -> None:
    lock_path = tmp_path / "held.lock"
    holder = lock_path.open("a+")
    import fcntl

    fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
    try:
        waiter = lock_path.open("a+")
        with pytest.raises(AuditLockTimeout):
            _acquire_exclusive_lock(waiter.fileno(), lock_path, timeout=0.3)
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()
