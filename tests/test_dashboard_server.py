from __future__ import annotations

import json
import subprocess
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


def test_matrix_labels_total_score_artifact_as_research_only(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "outputs"
    output.mkdir()
    (output / "total-score-validation.json").write_text(
        json.dumps(
            {
                "sports": {
                    "nfl": {
                        "status": "research_score_model_candidate",
                        "training": {"holdout_rows": 101},
                        "locked_holdout": {
                            "mae": 11.2,
                            "baseline_mae": 11.7,
                            "mae_gain_vs_rolling_league_mean": 0.5,
                            "mae_gain_95pct_interval": [-0.2, 1.2],
                        },
                        "market_qualification": {"reason": "BLOCKED_MISSING_LINES"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (output / "learned-model-validation-v2.json").write_text(
        json.dumps(
            {
                "sports": {
                    "nfl": {
                        "multi_market_readiness": {
                            "spread": "BLOCKED_MISSING_HISTORICAL_CONTRACT_LINES",
                            "total": "BLOCKED_MISSING_HISTORICAL_CONTRACT_LINES",
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard_server, "OUTPUTS", output)

    cell = dashboard_server.matrix()["grid"]["nfl"]["total"]

    assert cell["state"] == "research_total_candidate"
    assert cell["holdout_rows"] == 101
    assert cell["qualification"] == "BLOCKED_MISSING_LINES"


def test_resting_order_preview_and_submit_persist_exchange_id(monkeypatch, tmp_path: Path) -> None:
    pick = {
        "pick_id": "qualified-1",
        "status": "open",
        "record_type": "QUALIFIED_SHADOW_CALL",
        "units": 1.0,
    }
    quote = {
        "market_slug": "nfl-example",
        "side": "long",
        "ask": 0.60,
        "fresh": True,
        "market_state": "MARKET_STATE_OPEN",
    }
    monkeypatch.setattr(dashboard_server, "ORDERS_FILE", tmp_path / "orders.json")
    monkeypatch.setattr(dashboard_server, "read_picks", lambda: [pick])
    monkeypatch.setattr(dashboard_server, "_pick_quote", lambda row: quote)
    monkeypatch.setenv("POLYMARKET_KEY_ID", "test-key")
    monkeypatch.setenv("POLYMARKET_SECRET_KEY", "test-secret")
    monkeypatch.setattr(
        dashboard_server.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0,
            stdout=json.dumps({"status": "submitted", "order_id": "exchange-123", "order_state": "open"}),
            stderr="",
        ),
    )

    preview = dashboard_server.preview_order(
        {"pick_id": "qualified-1", "price": 0.55, "size_shares": 10}
    )
    result = dashboard_server.submit_order({"nonce": preview["nonce"]})

    assert preview["status"] == "preview"
    assert result["status"] == "submitted"
    saved = json.loads((tmp_path / "orders.json").read_text(encoding="utf-8"))["orders"]
    assert saved[-1]["order_id"] == "exchange-123"
    assert saved[-1]["price"] == 0.55


def test_resting_order_refuses_crossing_the_current_ask(monkeypatch) -> None:
    pick = {
        "pick_id": "qualified-2",
        "status": "open",
        "record_type": "QUALIFIED_SHADOW_CALL",
        "units": 1.0,
    }
    monkeypatch.setattr(dashboard_server, "read_picks", lambda: [pick])
    monkeypatch.setattr(
        dashboard_server,
        "_pick_quote",
        lambda row: {
            "market_slug": "nfl-example",
            "side": "long",
            "ask": 0.60,
            "fresh": True,
            "market_state": "MARKET_STATE_OPEN",
        },
    )
    monkeypatch.setenv("POLYMARKET_KEY_ID", "test-key")
    monkeypatch.setenv("POLYMARKET_SECRET_KEY", "test-secret")

    result = dashboard_server.preview_order(
        {"pick_id": "qualified-2", "price": 0.60, "size_shares": 10}
    )

    assert result["status"] == "refused"
    assert "below the current ask" in result["error"]


def test_resting_order_enforces_seven_fifty_per_unit_cost_cap(monkeypatch) -> None:
    pick = {
        "pick_id": "qualified-cap",
        "status": "open",
        "record_type": "QUALIFIED_SHADOW_CALL",
        "units": 1.0,
    }
    monkeypatch.setattr(dashboard_server, "read_picks", lambda: [pick])
    monkeypatch.setattr(
        dashboard_server,
        "_pick_quote",
        lambda row: {
            "market_slug": "nfl-example",
            "side": "long",
            "ask": 0.60,
            "fresh": True,
            "market_state": "MARKET_STATE_OPEN",
        },
    )
    monkeypatch.setattr(dashboard_server, "_unit_value_usd", lambda: 7.5)
    monkeypatch.setenv("POLYMARKET_KEY_ID", "test-key")
    monkeypatch.setenv("POLYMARKET_SECRET_KEY", "test-secret")

    result = dashboard_server.preview_order(
        {"pick_id": "qualified-cap", "price": 0.55, "size_shares": 14}
    )

    assert result["status"] == "refused"
    assert "1U cap ($7.50)" in result["error"]


def test_today_and_ledger_hide_archived_duplicates_and_keep_latest(monkeypatch, tmp_path: Path) -> None:
    old = {
        "pick_id": "old-v2",
        "event_id": "game-1",
        "event_start_utc": "2026-07-17T23:30:00Z",
        "created_at_utc": "2026-07-17T10:00:00Z",
        "league": "WNBA",
        "market_type": "moneyline",
        "selection": "away",
        "model_version": "v2",
        "status": "open",
    }
    latest = {
        **old,
        "pick_id": "latest-v3",
        "created_at_utc": "2026-07-17T14:00:00Z",
        "model_version": "v3",
    }
    archived_only = {
        **old,
        "pick_id": "archived-only",
        "event_id": "game-2",
        "selection": "home",
    }
    archive_path = tmp_path / "archive.json"
    archive_path.write_text(
        json.dumps({"pick_ids": ["old-v2", "archived-only"]}), encoding="utf-8"
    )
    monkeypatch.setattr(dashboard_server, "ARCHIVE_FILE", archive_path)
    monkeypatch.setattr(dashboard_server, "read_picks", lambda: [old, latest, archived_only])
    monkeypatch.setattr(dashboard_server, "_decorate_pick", lambda row: row)

    ledger = dashboard_server.dashboard_picks()
    today = dashboard_server.today_picks("2026-07-17")

    assert [row["pick_id"] for row in ledger] == ["latest-v3", "archived-only"]
    assert [row["pick_id"] for row in today["picks"]] == ["latest-v3"]
    assert today["count"] == 1
