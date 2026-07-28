from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

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


def test_settled_pick_keeps_its_decision_time_model_size() -> None:
    open_row = {
        "status": "open",
        "model_probability": 0.672,
        "market_implied_probability": 0.64,
    }
    settled_row = {**open_row, "status": "settled", "result": "loss"}

    assert dashboard_server._suggested_units(open_row) == 2.0
    assert dashboard_server._suggested_units(settled_row) == 2.0


def test_main_stops_cleanly_on_keyboard_interrupt(monkeypatch, capsys) -> None:
    server = Mock()
    server.serve_forever.side_effect = KeyboardInterrupt
    monkeypatch.setattr(dashboard_server, "ThreadingHTTPServer", Mock(return_value=server))
    monkeypatch.setattr("sys.argv", ["dashboard_server.py"])

    dashboard_server.main()

    assert "dashboard stopped" in capsys.readouterr().out
    server.server_close.assert_called_once_with()


def test_latest_persisted_action_returns_newest_finished_job(
    monkeypatch, tmp_path: Path
) -> None:
    jobs = tmp_path / "jobs.json"
    jobs.write_text(
        json.dumps(
            {
                "old": {
                    "action": "run_tests",
                    "status": "failed",
                    "started_at": "2026-07-20T10:00:00",
                },
                "running": {
                    "action": "run_tests",
                    "status": "running",
                    "started_at": "2026-07-20T12:00:00",
                },
                "new": {
                    "action": "run_tests",
                    "status": "ok",
                    "started_at": "2026-07-20T11:00:00",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard_server, "JOBS_FILE", jobs)

    result = dashboard_server._latest_persisted_action("run_tests")

    assert result is not None
    assert result["status"] == "ok"
    assert result["started_at"] == "2026-07-20T11:00:00"


def test_matrix_reports_wiring_and_features_not_validation_stats() -> None:
    """The Matrix tab answers "is this model wired into daily, and what does
    it run on" -- not hit rates, MAE, or promotion-gate status. Locks in the
    2026-07-28 redesign so a future change doesn't silently reintroduce
    validation-metric cells."""
    result = dashboard_server.matrix()

    assert "grid" not in result
    assert "baseball" not in result
    assert "basketball" not in result
    assert "esports" not in result
    rows = result["rows"]
    by_sport = {row["sport"]: row for row in rows}

    mlb_totals = next(r for r in rows if r["sport"] == "MLB" and r["market"] == "Totals & Spread")
    assert mlb_totals["wired"] is True
    assert mlb_totals["ledger"] == "Flat only"

    legacy = next(r for r in rows if r["market"] == "Moneyline (legacy)")
    assert legacy["wired"] is False

    btts = next(r for r in rows if r["market"] == "BTTS")
    assert btts["wired"] is False

    dead_esports = next(r for r in rows if "CoD" in r["sport"])
    assert dead_esports["wired"] is False

    for row in rows:
        for forbidden_key in ("hit_rate", "brier", "calls", "mae", "qualification"):
            assert forbidden_key not in row

    assert "Tennis" in by_sport
    assert by_sport["Tennis"]["wired"] is True


def test_metricless_qualified_artifact_is_not_rendered_as_qualified() -> None:
    cell = dashboard_server._ml_cell(
        {},
        {
            "model_version": "mlb-example-v1",
            "qualified": True,
            "market_models": {
                "moneyline": {
                    "feature_names": ["elo_probability"],
                    "confidence_threshold": 0.55,
                }
            },
        },
    )

    assert cell["state"] == "tested_not_qualified"
    assert cell["calls"] is None
    assert cell["readiness"] == "ARTIFACT_QUALIFIED_FLAG_WITHOUT_LOCKED_HOLDOUT_METRICS"


def test_newest_validation_loads_dedicated_research_grids(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "learned-model-validation.json").write_text(
        json.dumps({"sports": {}}), encoding="utf-8"
    )
    (tmp_path / "international-baseball-baseline-validation.json").write_text(
        json.dumps(
            {
                "leagues": {
                    "kbo": {
                        "model_version": "kbo-v1",
                        "locked_test": {
                            "accuracy_decisive": 0.55,
                            "calls": 100,
                            "brier_settlement": 0.24,
                            "units_at_minus_110": 5.0,
                            "observations": 102,
                            "ties": 2,
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "esports-baseline-validation.json").write_text(
        json.dumps(
            {
                "titles": {
                    "lol": {
                        "model_version": "lol-v1",
                        "chosen": {"confidence_threshold": 0.05},
                        "locked_test": {
                            "selected_matches": {
                                "accuracy": 0.69,
                                "calls": 200,
                                "brier": 0.20,
                                "units_at_minus_110": 12.0,
                            }
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard_server, "OUTPUTS", tmp_path)

    validation, source = dashboard_server._newest_validation()

    kbo = validation["baseball_grid"]["kbo"]["moneyline"]
    lol = validation["esports_grid"]["lol"]["moneyline"]
    assert kbo == {
        "state": "research_only",
        "hit_rate": 0.55,
        "calls": 100,
        "brier": 0.24,
        "units": 0.0,
        "diagnostic_units": 5.0,
        "observations": 102,
        "ties": 2,
        "model_version": "kbo-v1",
        "qualified_for_betting": False,
    }
    assert lol["state"] == "research_only"
    assert lol["hit_rate"] == 0.69
    assert lol["units"] == 0.0
    assert "international-baseball-baseline-validation.json" in source
    assert "esports-baseline-validation.json" in source


def test_production_artifact_falls_back_to_active_config_path(
    monkeypatch, tmp_path: Path
) -> None:
    artifact = tmp_path / "active.json"
    artifact.write_text(json.dumps({"model_version": "active-v1"}), encoding="utf-8")
    monkeypatch.setattr(
        dashboard_server,
        "_config_production_artifact_path",
        lambda _sport: str(artifact),
    )

    loaded = dashboard_server._production_artifact({"sports": {"wnba": {}}}, "wnba")

    assert loaded["model_version"] == "active-v1"


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
    monkeypatch.setattr(dashboard_server, "_unit_value_usd", lambda: 7.5)
    monkeypatch.setenv("POLYMARKET_KEY_ID", "test-key")
    monkeypatch.setenv("POLYMARKET_SECRET_KEY", "test-secret")
    monkeypatch.setattr(
        dashboard_server.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0,
            stdout=json.dumps(
                {
                    "status": "submitted",
                    "order_id": "exchange-123",
                    "order_state": "open",
                    "exchange_price": 0.45,
                }
            ),
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
    assert saved[-1]["exchange_price"] == 0.45
    assert saved[-1]["price_basis"] == "selected_outcome_probability"
    assert saved[-1]["exchange_price_basis"] == "long_side_probability"


def test_submit_parses_success_after_interactive_prompt(monkeypatch, tmp_path: Path) -> None:
    pick = {
        "pick_id": "qualified-prompt",
        "status": "open",
        "record_type": "QUALIFIED_SHADOW_CALL",
        "units": 1.0,
    }
    quote = {
        "market_slug": "wnba-prompt",
        "side": "long",
        "ask": 0.60,
        "fresh": True,
        "market_state": "MARKET_STATE_OPEN",
    }
    monkeypatch.setattr(dashboard_server, "ORDERS_FILE", tmp_path / "orders.json")
    monkeypatch.setattr(dashboard_server, "read_picks", lambda: [pick])
    monkeypatch.setattr(dashboard_server, "_pick_quote", lambda row: quote)
    monkeypatch.setattr(dashboard_server, "_unit_value_usd", lambda: 7.5)
    monkeypatch.setenv("POLYMARKET_KEY_ID", "test-key")
    monkeypatch.setenv("POLYMARKET_SECRET_KEY", "test-secret")
    accepted = {
        "status": "submitted",
        "order_id": "exchange-resting-1",
        "order_state": None,
        "raw_response": {"id": "exchange-resting-1", "executions": []},
    }
    monkeypatch.setattr(
        dashboard_server.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0,
            stdout="Order: BUY ... Confirm? (Y/N): " + json.dumps(accepted), stderr="",
        ),
    )

    preview = dashboard_server.preview_order(
        {"pick_id": pick["pick_id"], "price": 0.55, "size_shares": 10}
    )
    result = dashboard_server.submit_order({"nonce": preview["nonce"]})

    assert result["status"] == "submitted"
    assert result["order_id"] == "exchange-resting-1"
    assert dashboard_server._load_orders()["orders"][-1]["status"] == "submitted"


def test_reconcile_order_marks_exchange_cancellation_and_unlocks_retry(
    monkeypatch, tmp_path: Path
) -> None:
    orders_path = tmp_path / "orders.json"
    orders_path.write_text(
        json.dumps(
            {
                "orders": [
                    {
                        "pick_id": "model-pick-1",
                        "status": "submitted",
                        "order_id": "exchange-canceled-1",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard_server, "ORDERS_FILE", orders_path)
    monkeypatch.setattr(dashboard_server, "_resolve_runner", lambda: ["runner"])
    monkeypatch.setattr(
        dashboard_server.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=json.dumps(
                {
                    "status": "live",
                    "orders": [
                        {
                            "order_id": "exchange-canceled-1",
                            "order_state": "ORDER_STATE_CANCELED",
                            "cum_quantity": 0,
                            "leaves_quantity": 0,
                        }
                    ],
                    "observed_at_utc": "2026-07-17T16:00:00Z",
                }
            ),
            stderr="",
        ),
    )
    dashboard_server._CACHE.clear()

    dashboard_server._reconcile_orders()

    saved = json.loads(orders_path.read_text(encoding="utf-8"))["orders"][0]
    assert saved["status"] == "canceled"
    assert saved["order_state"] == "ORDER_STATE_CANCELED"
    assert saved["last_checked_at_utc"] == "2026-07-17T16:00:00Z"


def test_reconcile_order_preserves_submission_when_exchange_is_unavailable(
    monkeypatch, tmp_path: Path
) -> None:
    orders_path = tmp_path / "orders.json"
    orders_path.write_text(
        json.dumps(
            {"orders": [{"status": "submitted", "order_id": "exchange-unknown"}]}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard_server, "ORDERS_FILE", orders_path)
    monkeypatch.setattr(dashboard_server, "_resolve_runner", lambda: ["runner"])
    monkeypatch.setattr(
        dashboard_server.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=3, stdout="", stderr='{"status":"refused"}'
        ),
    )
    dashboard_server._CACHE.clear()

    dashboard_server._reconcile_orders()

    saved = json.loads(orders_path.read_text(encoding="utf-8"))["orders"][0]
    assert saved["status"] == "submitted"


def test_position_sell_refuses_more_than_available(monkeypatch) -> None:
    monkeypatch.setattr(
        dashboard_server,
        "live_portfolio_view",
        lambda: {
            "status": "live",
            "open": {
                "positions": [
                    {
                        "market_slug": "mlb-held",
                        "side": "short",
                        "available_quantity": 18.0,
                    }
                ]
            },
        },
    )
    monkeypatch.setattr(
        dashboard_server,
        "_live_bbo",
        lambda slug: {"short": {"bid": 0.35, "ask": 0.37}},
    )

    result = dashboard_server.preview_position_sell(
        {"market_slug": "mlb-held", "side": "short", "price": 0.50, "size_shares": 19}
    )

    assert result["status"] == "refused"
    assert "18" in result["error"]


def test_position_sell_previews_verified_holding(monkeypatch) -> None:
    monkeypatch.setattr(
        dashboard_server,
        "live_portfolio_view",
        lambda: {
            "status": "live",
            "open": {
                "positions": [
                    {
                        "market_slug": "mlb-held",
                        "side": "short",
                        "available_quantity": 18.0,
                    }
                ]
            },
        },
    )
    monkeypatch.setattr(
        dashboard_server,
        "_live_bbo",
        lambda slug: {"short": {"bid": 0.35, "ask": 0.37}},
    )

    result = dashboard_server.preview_position_sell(
        {"market_slug": "mlb-held", "side": "short", "price": 0.50, "size_shares": 18}
    )

    assert result["status"] == "preview"
    assert result["verified_available_quantity"] == 18.0


def test_buy_at_current_ask_submits_marketable_ioc_limit(monkeypatch, tmp_path: Path) -> None:
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
    monkeypatch.setattr(dashboard_server, "ORDERS_FILE", tmp_path / "orders.json")
    monkeypatch.setattr(dashboard_server, "_unit_value_usd", lambda: 7.5)
    submitted = {}

    def fake_run(command, **kwargs):
        submitted["command"] = command
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=json.dumps(
                {"status": "submitted", "order_id": "ioc-123", "order_state": "ORDER_STATE_FILLED"}
            ),
            stderr="",
        )

    monkeypatch.setattr(dashboard_server.subprocess, "run", fake_run)

    result = dashboard_server.preview_order(
        {"pick_id": "qualified-2", "price": 0.60, "size_shares": 10}
    )

    assert result["status"] == "preview"
    assert result["order_type"] == "limit_ioc"
    assert result["execution_mode"] == "marketable_limit"
    assert result["reference_ask"] == 0.60

    placed = dashboard_server.submit_order({"nonce": result["nonce"]})

    assert placed["status"] == "submitted"
    order_type_index = submitted["command"].index("--order-type")
    assert submitted["command"][order_type_index + 1] == "limit_ioc"


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


def test_active_model_positive_edge_research_pick_is_manually_buyable(monkeypatch) -> None:
    row = {
        "record_type": "RESEARCH_OBSERVATION",
        "status": "open",
        "league": "WNBA",
        "model_version": "wnba-elo-trend-lr-v3",
        "model_probability": 0.58,
        "market_implied_probability": 0.48,
    }
    monkeypatch.setattr(
        dashboard_server,
        "_config_payload",
        lambda: {
            "execution": {
                "allow_manual_research_orders": True,
                "manual_research_require_active_model": True,
                "manual_research_require_positive_edge": True,
            },
            "models": {"WNBA": {"active_production_version": "wnba-elo-trend-lr-v3"}},
        },
    )
    monkeypatch.setattr(dashboard_server, "_row_has_banned_team", lambda item: False)
    monkeypatch.setattr(dashboard_server, "_suggested_units", lambda item: 1.25)
    monkeypatch.setenv("POLYMARKET_KEY_ID", "test-key")
    monkeypatch.setenv("POLYMARKET_SECRET_KEY", "test-secret")
    quote = {"fresh": True, "market_state": "MARKET_STATE_OPEN"}

    ready, reason = dashboard_server._order_readiness(row, quote)

    assert ready is True
    assert reason == "ready"


def test_manual_research_limit_cannot_exceed_model_probability(monkeypatch) -> None:
    pick = {
        "pick_id": "manual-edge-cap",
        "status": "open",
        "record_type": "RESEARCH_OBSERVATION",
        "units": 0,
        "model_probability": 0.58,
    }
    monkeypatch.setattr(dashboard_server, "read_picks", lambda: [pick])
    monkeypatch.setattr(
        dashboard_server,
        "_decorate_pick",
        lambda row: {
            **row,
            "buy_ready": True,
            "buy_block_reason": "ready",
            "quote": {"market_slug": "wnba-example", "side": "long", "ask": 0.64},
        },
    )
    monkeypatch.setattr(
        dashboard_server,
        "_config_payload",
        lambda: {"execution": {"manual_research_require_positive_edge": True}},
    )

    result = dashboard_server.preview_order(
        {"pick_id": "manual-edge-cap", "price": 0.58, "size_shares": 10}
    )

    assert result["status"] == "refused"
    assert "below the model probability" in result["error"]


def test_manual_control_can_buy_at_ask_when_positive_edge_gate_is_disabled(monkeypatch) -> None:
    pick = {
        "pick_id": "manual-market-price",
        "status": "open",
        "record_type": "RESEARCH_OBSERVATION",
        "units": 0,
        "model_probability": 0.58,
    }
    monkeypatch.setattr(dashboard_server, "read_picks", lambda: [pick])
    monkeypatch.setattr(
        dashboard_server,
        "_decorate_pick",
        lambda row: {
            **row,
            "buy_ready": True,
            "buy_block_reason": "ready",
            "quote": {"market_slug": "wnba-example", "side": "long", "ask": 0.64},
        },
    )
    monkeypatch.setattr(dashboard_server, "_suggested_units", lambda row: 1.25)
    monkeypatch.setattr(dashboard_server, "_unit_value_usd", lambda: 7.5)
    monkeypatch.setattr(
        dashboard_server,
        "_config_payload",
        lambda: {"execution": {"manual_research_require_positive_edge": False}},
    )

    result = dashboard_server.preview_order(
        {"pick_id": "manual-market-price", "price": 0.64, "size_shares": 10}
    )

    assert result["status"] == "preview"
    assert result["order_type"] == "limit_ioc"


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
    monkeypatch.setattr(dashboard_server, "_decorate_pick", lambda row, *args: row)

    ledger = dashboard_server.dashboard_picks()
    today = dashboard_server.today_picks("2026-07-17")

    assert [row["pick_id"] for row in ledger] == ["latest-v3", "archived-only"]
    assert [row["pick_id"] for row in today["picks"]] == ["latest-v3"]
    assert today["count"] == 1


def test_scan_prices_targets_all_unique_open_visible_ledger_contracts(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(dashboard_server, "_resolve_runner", lambda: ["model-prediction"])
    archive_path = tmp_path / "archive.json"
    archive_path.write_text(json.dumps({"pick_ids": ["archived"]}), encoding="utf-8")
    monkeypatch.setattr(dashboard_server, "ARCHIVE_FILE", archive_path)
    monkeypatch.setattr(
        dashboard_server,
        "read_picks",
        lambda: [
            {"pick_id": "today", "event_id": "game-1", "league": "WNBA", "status": "open", "event_start_utc": "2026-07-17T23:30:00Z"},
            {"pick_id": "tomorrow", "event_id": "game-2", "league": "MLB", "status": "open", "event_start_utc": "2026-07-18T20:10:00Z"},
            {"pick_id": "settled", "event_id": "game-3", "league": "MLB", "status": "settled", "event_start_utc": "2026-07-17T20:10:00Z"},
            {"pick_id": "archived", "event_id": "game-4", "league": "NFL", "status": "open", "event_start_utc": "2026-07-18T20:10:00Z"},
        ],
    )
    quotes = {
        "today": {"market_slug": "wnba-game-1"},
        "tomorrow": {"market_slug": "mlb-game-2"},
        "settled": {"market_slug": "mlb-finished"},
        "archived": {"market_slug": "nfl-hidden"},
    }
    monkeypatch.setattr(dashboard_server, "_pick_quote", lambda row: quotes[row["pick_id"]])

    command = dashboard_server._action_command(
        "refresh_prices", {"date": "2026-07-17"}
    )

    assert command == [
        "model-prediction",
        "polymarket-ledger-prices",
        "--date",
        "2026-07-17",
        "--contract",
        "wnba@2026-07-17=wnba-game-1",
        "--contract",
        "mlb@2026-07-18=mlb-game-2",
    ]
    assert "--all" not in command


def test_portfolio_uses_only_exchange_confirmed_positions_and_persists_activity(
    monkeypatch, tmp_path: Path
) -> None:
    history_file = tmp_path / "portfolio_history.json"
    monkeypatch.setattr(dashboard_server, "PORTFOLIO_HISTORY_FILE", history_file)
    monkeypatch.setattr(dashboard_server, "_today", lambda: "2026-07-17")
    monkeypatch.setattr(dashboard_server, "_resolve_runner", lambda: ["model-prediction"])
    monkeypatch.setattr(
        dashboard_server,
        "_live_model_links",
        lambda: {
            ("market-1", "long"): {
                "pick_id": "model-pick-1",
                "model_version": "wnba-v3",
            }
        },
    )
    monkeypatch.setenv("POLYMARKET_KEY_ID", "test-key")
    monkeypatch.setenv("POLYMARKET_SECRET_KEY", "test-secret")
    raw = {
        "status": "live",
        "source": "polymarket_us_authenticated_portfolio",
        "observed_at_utc": "2026-07-17T15:00:00Z",
        "positions": {
            "market-1": {
                "netPositionDecimal": "3.5",
                "cost": {"value": "1.75", "currency": "USD"},
                "cashValue": {"value": "2.10", "currency": "USD"},
                "realized": {"value": "0.20", "currency": "USD"},
                "updateTime": "2026-07-17T14:59:00Z",
                "marketMetadata": {"title": "Away at Home", "outcome": "Away"},
            },
            "closed-market": {"netPositionDecimal": "0"},
        },
        "activities": [
            {
                "trade": {
                    "id": "trade-1",
                    "marketSlug": "market-1",
                    "price": {"value": "0.50", "currency": "USD"},
                    "qtyDecimal": "3.5",
                    "costBasis": {"value": "1.75", "currency": "USD"},
                    "realizedPnl": {"value": "0.00", "currency": "USD"},
                    "updateTime": "2026-07-17T14:58:00Z",
                    "outcomeSide": "OUTCOME_SIDE_YES",
                }
            },
            {
                "positionResolution": {
                    "marketSlug": "old-market",
                    "tradeId": "resolution-1",
                    "side": "POSITION_RESOLUTION_SIDE_LONG",
                    "updateTime": "2026-07-17T14:00:00Z",
                    "beforePosition": {"netPositionDecimal": "2"},
                    "afterPosition": {
                        "netPositionDecimal": "0",
                        "realized": {"value": "1.00", "currency": "USD"},
                    },
                }
            },
        ],
        "balances": [{"currency": "USD", "buyingPower": 25.0}],
    }
    monkeypatch.setattr(
        dashboard_server.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout=json.dumps(raw), stderr=""
        ),
    )

    result = dashboard_server.live_portfolio_view()

    assert result["status"] == "live"
    assert result["open"]["count"] == 1
    assert result["open"]["positions"][0]["market_slug"] == "market-1"
    assert result["open"]["positions"][0]["model_pick"]["pick_id"] == "model-pick-1"
    assert result["recent_history"]["trade_count"] == 1
    assert result["recent_history"]["settlement_count"] == 1
    assert result["history_start_date"] == "2026-07-17"
    assert history_file.exists()


def test_opposite_side_exchange_activity_is_not_linked_to_model_pick() -> None:
    links = {
        ("wnba-market", "short"): {
            "pick_id": "phoenix-pick",
            "model_version": "wnba-v3",
            "side": "short",
        }
    }

    connecticut = dashboard_server._normalize_live_activity(
        {
            "positionResolution": {
                "marketSlug": "wnba-market",
                "tradeId": "connecticut-settlement",
                "side": "POSITION_RESOLUTION_SIDE_LONG",
                "updateTime": "2026-07-18T04:00:00Z",
            }
        },
        links,
    )
    phoenix = dashboard_server._normalize_live_activity(
        {
            "trade": {
                "id": "phoenix-fill",
                "marketSlug": "wnba-market",
                "intent": "ORDER_INTENT_BUY_SHORT",
                "updateTime": "2026-07-17T20:00:00Z",
            }
        },
        links,
    )

    assert connecticut["outcome_side"] == "long"
    assert connecticut["model_pick"] is None
    assert phoenix["outcome_side"] == "short"
    assert phoenix["model_pick"]["pick_id"] == "phoenix-pick"


def test_market_slugs_are_rendered_as_readable_names(monkeypatch) -> None:
    monkeypatch.setattr(
        dashboard_server,
        "_team_name_index",
        lambda: {
            ("wnba", "conn"): "Connecticut Sun",
            ("wnba", "phx"): "Phoenix Mercury",
            ("mlb", "sd"): "San Diego Padres",
            ("mlb", "kc"): "Kansas City Royals",
        },
    )
    monkeypatch.setattr(dashboard_server, "_public_market_question", lambda slug: None)

    assert dashboard_server._human_market_name("aec-wnba-conn-phx-2026-07-17") == (
        "WNBA · Connecticut Sun @ Phoenix Mercury · Moneyline"
    )
    assert dashboard_server._human_market_name("tsc-mlb-sd-kc-2026-07-17-9pt5") == (
        "MLB · San Diego Padres @ Kansas City Royals · Total 9.5"
    )


def test_player_prop_market_uses_public_question(monkeypatch) -> None:
    monkeypatch.setattr(
        dashboard_server,
        "_public_market_question",
        lambda slug: "Will Kyle Tucker record at least 1 hits + runs + RBIs?",
    )

    name = dashboard_server._human_market_name(
        "astatc-mlb-lad-nyy-2026-07-17-hrr-kyltuc-gte1"
    )

    assert name == (
        "Kyle Tucker · 1+ hits + runs + RBIs · "
        "Los Angeles Dodgers @ New York Yankees"
    )


def test_unit_value_update_is_atomic_persistent_and_audited(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "model.yaml"
    config_path.parent.mkdir()
    config_path.write_text(
        "bankroll:\n  unit_value_usd: 7.5\n  reference_units: 5\n",
        encoding="utf-8",
    )
    data_path = tmp_path / "data"
    monkeypatch.setattr(dashboard_server, "CONFIG_FILE", config_path)
    monkeypatch.setattr(dashboard_server, "DATA", data_path)

    result = dashboard_server._set_unit_value_usd(25)

    saved = dashboard_server.yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert result["status"] == "ok"
    assert result["previous_unit_value_usd"] == 7.5
    assert result["unit_value_usd"] == 25
    assert saved["bankroll"]["unit_value_usd"] == 25
    events = [json.loads(line) for line in (data_path / "events.jsonl").read_text().splitlines()]
    assert events[-1]["event_type"] == "unit_value_updated"
    assert events[-1]["payload"]["unit_value_usd"] == 25


def test_unit_value_update_rejects_invalid_amounts() -> None:
    with pytest.raises(ValueError, match="between"):
        dashboard_server._set_unit_value_usd(0)


def test_portfolio_history_ignores_everything_before_fixed_start_date(
    monkeypatch, tmp_path: Path
) -> None:
    history_file = tmp_path / "portfolio_history.json"
    history_file.write_text(
        json.dumps(
            {
                "history_start_date": "2026-07-17",
                "activities": [
                    {
                        "activity_id": "trade:old",
                        "type": "trade",
                        "occurred_at_utc": "2026-07-17T03:59:59Z",
                    },
                    {
                        "activity_id": "trade:new",
                        "type": "trade",
                        "occurred_at_utc": "2026-07-17T04:00:00Z",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard_server, "PORTFOLIO_HISTORY_FILE", history_file)

    result = dashboard_server._load_portfolio_history()

    assert result["history_start_date"] == "2026-07-17"
    assert [item["activity_id"] for item in result["activities"]] == ["trade:new"]


def test_portfolio_never_falls_back_to_model_picks_when_authentication_fails(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        dashboard_server, "PORTFOLIO_HISTORY_FILE", tmp_path / "portfolio_history.json"
    )
    monkeypatch.setattr(dashboard_server, "_resolve_runner", lambda: ["model-prediction"])
    monkeypatch.setenv("POLYMARKET_KEY_ID", "test-key")
    monkeypatch.setenv("POLYMARKET_SECRET_KEY", "invalid-secret")
    monkeypatch.setattr(
        dashboard_server,
        "read_picks",
        lambda: [{"pick_id": "research-row", "status": "open"}],
    )
    monkeypatch.setattr(
        dashboard_server.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=3,
            stdout="",
            stderr=json.dumps({"status": "refused", "error": "invalid API secret"}),
        ),
    )

    result = dashboard_server.live_portfolio_view()

    assert result["status"] == "unavailable"
    assert result["open"]["count"] == 0
    assert result["open"]["positions"] == []


def test_pick_quote_freezes_last_valid_pregame_price(monkeypatch, tmp_path: Path) -> None:
    data = tmp_path / "data"
    path = data / "odds" / "wnba" / "2026-07-18" / "polymarket_snapshots.jsonl"
    path.parent.mkdir(parents=True)
    base = {
        "market_slug": "aec-wnba-ny-ind-2026-07-18",
        "market_type": "moneyline",
        "market_state": "MARKET_STATE_OPEN",
        "event_start_utc": "2026-07-19T00:00:00Z",
        "long": {"description": "Liberty", "ask": 0.53},
        "short": {"description": "Fever", "ask": 0.48},
    }
    snapshots = [
        {**base, "observed_at_utc": "2026-07-18T23:56:27Z", "timestamp_valid": True},
        {
            **base,
            "observed_at_utc": "2026-07-19T00:06:16Z",
            "timestamp_valid": False,
            "short": {"description": "Fever", "ask": 0.50},
        },
        {
            **base,
            "observed_at_utc": "2026-07-19T00:07:00Z",
            "timestamp_valid": True,
            "short": {"description": "Fever", "ask": 0.51},
        },
    ]
    path.write_text("\n".join(json.dumps(item) for item in snapshots) + "\n", encoding="utf-8")
    monkeypatch.setattr(dashboard_server, "DATA", data)
    row = {
        "league": "WNBA",
        "market_type": "moneyline",
        "away_team": "New York Liberty",
        "home_team": "Indiana Fever",
        "selection": "home",
        "event_start_utc": "2026-07-19T00:00:00Z",
    }

    quote = dashboard_server._pick_quote(row)

    assert quote is not None
    assert quote["ask"] == 0.48
    assert quote["observed_at_utc"] == "2026-07-18T23:56:27Z"
    assert quote["price_role"] == "pregame_close"
    assert quote["seconds_before_start"] == 213


def test_filled_entry_uses_selected_side_exchange_fill(monkeypatch) -> None:
    monkeypatch.setattr(
        dashboard_server,
        "_load_orders",
        lambda: {
            "orders": [
                {
                    "pick_id": "indiana",
                    "action": "buy",
                    "status": "filled",
                    "market_slug": "aec-wnba-ny-ind-2026-07-18",
                    "side": "short",
                    "price": 0.44,
                    "size_shares": 25.56,
                    "cum_quantity": 25.56,
                    "submitted_at_utc": "2026-07-18T15:44:02Z",
                }
            ]
        },
    )
    monkeypatch.setattr(
        dashboard_server,
        "_load_portfolio_history",
        lambda: {
            "activities": [
                {
                    "activity_id": "trade:fill",
                    "type": "trade",
                    "market_slug": "aec-wnba-ny-ind-2026-07-18",
                    "occurred_at_utc": "2026-07-18T15:52:08Z",
                    "price": 0.56,
                    "quantity": 25.56,
                },
                {
                    "activity_id": "trade:resolution",
                    "type": "trade",
                    "market_slug": "aec-wnba-ny-ind-2026-07-18",
                    "occurred_at_utc": "2026-07-19T02:01:55Z",
                    "price": 0.01,
                    "quantity": 25.56,
                },
            ]
        },
    )

    entry = dashboard_server._filled_entry_for_pick({"pick_id": "indiana"})

    assert entry == {
        "price": 0.44,
        "basis": "exchange_trade",
        "side": "short",
        "market_slug": "aec-wnba-ny-ind-2026-07-18",
        "activity_id": "trade:fill",
    }


def test_short_activity_displays_selected_price_and_pnl() -> None:
    links = {
        ("aec-wnba-ny-ind-2026-07-18", "short"): {
            "pick_id": "indiana",
            "side": "short",
            "model_version": "wnba-v3",
        }
    }
    activity = dashboard_server._normalize_live_activity(
        {
            "trade": {
                "id": "resolution-trade",
                "marketSlug": "aec-wnba-ny-ind-2026-07-18",
                "price": {"value": "0.01", "currency": "USD"},
                "qtyDecimal": "25.56",
                "realizedPnl": {"value": "-11.23", "currency": "USD"},
                "updateTime": "2026-07-19T02:01:55Z",
            }
        },
        links,
    )

    assert activity["outcome_side"] == "short"
    assert activity["price"] == 0.99
    assert activity["exchange_price"] == 0.01
    assert activity["realized_pnl_usd"] == 11.23
    assert activity["exchange_realized_pnl_usd"] == -11.23
    assert activity["model_pick"]["pick_id"] == "indiana"

    ordinary_trade = dashboard_server._selected_short_pnl(0.38, -6.36)
    assert ordinary_trade == -6.36


def test_settlement_reports_realized_delta_not_cumulative_total() -> None:
    activity = dashboard_server._normalize_live_activity(
        {
            "positionResolution": {
                "marketSlug": "example",
                "side": "POSITION_RESOLUTION_SIDE_LONG",
                "updateTime": "2026-07-19T02:00:00Z",
                "beforePosition": {"realized": {"value": "4.00"}},
                "afterPosition": {"realized": {"value": "9.50"}},
            }
        },
        {},
    )

    assert activity["realized_pnl_usd"] == 5.5
    assert activity["cumulative_realized_pnl_usd"] == 9.5
    assert activity["pnl_basis"] == "position_realized_delta"


def test_team_total_history_name_uses_exact_exchange_question(monkeypatch) -> None:
    monkeypatch.setattr(
        dashboard_server,
        "_public_market_question",
        lambda slug: "Will France score more than 0.5 goals in the first half of FRA vs ENG?",
    )

    name = dashboard_server._human_market_name(
        "tsc-fwc-fra-eng-2026-07-18-ttg-fh-fra-0pt5"
    )

    assert name == "Will France score more than 0.5 goals in the first half of FRA vs ENG"


def test_cached_short_trade_is_side_adjusted_in_history_summary(monkeypatch) -> None:
    links = {
        ("aec-wnba-ny-ind-2026-07-18", "short"): {
            "pick_id": "indiana",
            "side": "short",
            "model_version": "wnba-v3",
        }
    }
    monkeypatch.setattr(dashboard_server, "_human_market_name", lambda slug, title="": slug)

    summary = dashboard_server._portfolio_history_summary(
        [
            {
                "activity_id": "trade:legacy",
                "type": "trade",
                "market_slug": "aec-wnba-ny-ind-2026-07-18",
                "price": 0.56,
                "quantity": 25.56,
                "realized_pnl_usd": None,
                "outcome_side": None,
                "model_pick": None,
            }
        ],
        "cached",
        links,
    )

    trade = summary["activities"][0]
    assert trade["price"] == 0.44
    assert trade["exchange_price"] == 0.56
    assert trade["outcome_side"] == "short"
    assert trade["model_pick"]["pick_id"] == "indiana"
