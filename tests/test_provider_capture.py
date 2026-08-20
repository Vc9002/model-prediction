import json
from datetime import UTC, datetime

from model_prediction.data_sources.provider_capture import ProviderEntry, write_provider_snapshot


def test_write_provider_snapshot_writes_matching_raw_and_snapshot_copies(tmp_path) -> None:
    observed = datetime(2026, 8, 19, 18, 30, tzinfo=UTC)
    entries = [
        ProviderEntry(
            source="balldontlie",
            source_entity_id="player-1",
            effective_at_utc="2026-08-19T12:00:00+00:00",
            observed_at_utc=observed.isoformat(),
            payload={"status": "day-to-day"},
        )
    ]

    payload, raw_path, snapshot_path = write_provider_snapshot(
        tmp_path, "balldontlie", "mlb", entries, observed_at=observed
    )

    assert raw_path.exists()
    assert snapshot_path.exists()
    assert raw_path.read_text() == snapshot_path.read_text()
    on_disk = json.loads(raw_path.read_text())
    assert on_disk["source"] == "balldontlie"
    assert on_disk["sport"] == "mlb"
    assert on_disk["entry_count"] == 1
    assert on_disk["entries"][0]["source_entity_id"] == "player-1"
    assert on_disk["entries"][0]["payload"] == {"status": "day-to-day"}
    assert payload == on_disk


def test_unavailable_entry_records_missing_reason_not_a_guess() -> None:
    entry = ProviderEntry(
        source="balldontlie",
        source_entity_id="player-2",
        effective_at_utc="2026-08-19T12:00:00+00:00",
        observed_at_utc="2026-08-19T18:30:00+00:00",
        payload={},
        available=False,
        missing_reason="NO_CALL_PROVIDER_TIMEOUT",
    )
    row = entry.as_dict()
    assert row["available"] is False
    assert row["missing_reason"] == "NO_CALL_PROVIDER_TIMEOUT"


def test_digest_is_stable_for_identical_entries_across_calls(tmp_path) -> None:
    observed = datetime(2026, 8, 19, 18, 30, tzinfo=UTC)
    entry = ProviderEntry(
        source="balldontlie",
        source_entity_id="player-1",
        effective_at_utc="2026-08-19T12:00:00+00:00",
        observed_at_utc=observed.isoformat(),
        payload={"status": "day-to-day"},
    )
    _, raw_path_a, _ = write_provider_snapshot(tmp_path, "balldontlie", "mlb", [entry], observed_at=observed)
    _, raw_path_b, _ = write_provider_snapshot(tmp_path, "balldontlie", "mlb", [entry], observed_at=observed)
    assert raw_path_a == raw_path_b
