from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import dashboard_server


def _configure_archive(monkeypatch, tmp_path: Path, rows: list[dict]) -> Path:
    archive_path = tmp_path / "archive.json"
    monkeypatch.setattr(dashboard_server, "ARCHIVE_FILE", archive_path)
    monkeypatch.setattr(dashboard_server, "read_picks", lambda: rows)
    return archive_path


def test_clear_all_hides_open_zero_unit_research_rows(monkeypatch, tmp_path: Path) -> None:
    archive_path = _configure_archive(
        monkeypatch,
        tmp_path,
        [
            {
                "pick_id": "paper-open",
                "status": "open",
                "record_type": "QUALIFIED_SHADOW_CALL",
                "units": 0,
            },
            {
                "pick_id": "settled",
                "status": "settled",
                "record_type": "RESEARCH_OBSERVATION",
                "units": 0,
            },
        ],
    )

    result = dashboard_server.archive_action("clear", "all")

    assert result["status"] == "ok"
    assert result["archived_now"] == 2
    assert result["protected_open_staked"] == 0
    assert json.loads(archive_path.read_text())["pick_ids"] == ["paper-open", "settled"]


def test_clear_all_keeps_open_positive_unit_rows_visible(monkeypatch, tmp_path: Path) -> None:
    archive_path = _configure_archive(
        monkeypatch,
        tmp_path,
        [
            {
                "pick_id": "staked-open",
                "status": "open",
                "record_type": "QUALIFIED_SHADOW_CALL",
                "units": 1.0,
            },
            {
                "pick_id": "paper-open",
                "status": "open",
                "record_type": "QUALIFIED_SHADOW_CALL",
                "units": 0,
            },
        ],
    )

    result = dashboard_server.archive_action("clear", "all")

    assert result["status"] == "ok"
    assert result["archived_now"] == 1
    assert result["protected_open_staked"] == 1
    assert json.loads(archive_path.read_text())["pick_ids"] == ["paper-open"]


def test_main_stops_cleanly_on_keyboard_interrupt(monkeypatch, capsys) -> None:
    server = Mock()
    server.serve_forever.side_effect = KeyboardInterrupt
    monkeypatch.setattr(dashboard_server, "ThreadingHTTPServer", Mock(return_value=server))
    monkeypatch.setattr("sys.argv", ["dashboard_server.py"])

    dashboard_server.main()

    assert "dashboard stopped" in capsys.readouterr().out
    server.server_close.assert_called_once_with()
