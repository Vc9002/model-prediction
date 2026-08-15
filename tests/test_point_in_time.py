from model_prediction.point_in_time import SourceRecord, records_available_at


def record(observed: str, effective: str, kind: str) -> SourceRecord:
    return SourceRecord.create(observed, effective, "test", "/fixture", {}, {"kind": kind})


def test_future_injury_lineup_closing_and_correction_are_excluded() -> None:
    decision = "2026-07-13T12:00:00Z"
    records = [
        record("2026-07-13T10:00:00Z", "2026-07-13T09:00:00Z", "pregame"),
        record("2026-07-13T12:01:00Z", "2026-07-13T11:00:00Z", "injury_after"),
        record("2026-07-13T12:05:00Z", "2026-07-13T11:30:00Z", "lineup_after"),
        record("2026-07-13T14:00:00Z", "2026-07-13T14:00:00Z", "closing_odds"),
        record("2026-07-14T00:00:00Z", "2026-07-13T08:00:00Z", "corrected_later"),
        record("2026-07-13T11:00:00Z", "2026-07-14T00:00:00Z", "rescheduled_future_effective"),
    ]
    available = records_available_at(records, decision)
    assert [item.payload["kind"] for item in available] == ["pregame"]
