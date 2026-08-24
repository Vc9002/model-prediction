from pathlib import Path

from scripts.forecast_mlb_v9_benchmark import _existing_event_ids

ROOT = Path(__file__).resolve().parents[1]


def test_v9_existing_event_scan_uses_public_rows_and_deduplicates() -> None:
    class Ledger:
        @staticmethod
        def rows():
            return [
                {"event_id": "game-1"},
                {"event_id": "game-1"},
                {"event_id": "game-2"},
                {"event_id": ""},
            ]

    assert _existing_event_ids(Ledger()) == {"game-1", "game-2"}


def test_daily_shell_gate_includes_every_material_child_exit() -> None:
    source = (ROOT / "scripts" / "run_daily.sh").read_text(encoding="utf-8")
    gate = source.rsplit("if [", 1)[1]

    for exit_variable in (
        "SETTLE_EXIT",
        "V9_SETTLE_EXIT",
        "INGEST_EXIT",
        "DAILY_EXIT",
        "V9_FORECAST_EXIT",
    ):
        assert f'"${exit_variable}" -ne 0' in gate
