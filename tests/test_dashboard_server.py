from __future__ import annotations

import json
import os
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
                        "model": "linear_regression_on_elo_trend",
                        "train_observations": 380,
                        "validation_observations": 131,
                        "holdout_observations": 101,
                        "holdout": {"calls": 56, "hit_rate": 0.553571, "brier": 0.248164},
                        "reference_line": 46.0,
                        "threshold": 0.52,
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
    assert cell["train_rows"] == 380
    assert cell["validation_rows"] == 131
    assert cell["calls"] == 56
    assert cell["hit_rate"] == 0.553571
    assert cell["brier"] == 0.248164
    assert cell["reference_line"] == 46.0
    assert cell["threshold"] == 0.52
    assert cell["qualification"] == "BLOCKED_MISSING_LINES"


def test_matrix_uses_newest_artifact_backed_soccer_variant(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "outputs"
    output.mkdir()
    artifact = tmp_path / "soccer-model.json"
    artifact.write_text(
        json.dumps(
            {
                "model_version": "soccer-elo-trend-lr-v1",
                "market_models": {
                    "moneyline": {
                        "feature_names": ["elo_probability"],
                        "confidence_threshold": 0.54397949,
                    }
                },
                "qualification": {
                    "qualified": True,
                    "calls": 268,
                    "hit_rate": 0.600746,
                    "units_at_minus_110": 39.363636,
                },
            }
        ),
        encoding="utf-8",
    )
    stale = {
        "production_artifacts": {"soccer": str(artifact)},
        "sports": {
            "soccer": {
                "variants": {
                    "elo_trend": {
                        "features": ["elo_probability", "trend_gap"],
                        "primary_65": {
                            "learned_threshold": 0.50,
                            "locked_holdout": {
                                "qualified": True,
                                "calls": 432,
                                "hit_rate": 0.6875,
                            },
                        },
                    }
                }
            }
        },
    }
    current = {
        "production_artifacts": {"soccer": str(artifact)},
        "sports": {
            "soccer": {
                "variants": {
                    "elo_only": {
                        "features": ["elo_probability"],
                        "primary_65": {
                            "learned_threshold": 0.54397949,
                            "locked_holdout": {
                                "qualified": True,
                                "calls": 268,
                                "hit_rate": 0.600746,
                                "units_at_minus_110": 39.363636,
                            },
                        },
                    },
                    "elo_trend": {
                        "features": ["elo_probability", "trend_gap"],
                        "primary_65": {
                            "learned_threshold": 0.54135498,
                            "locked_holdout": {
                                "qualified": False,
                                "calls": 272,
                                "hit_rate": 0.599265,
                            },
                        },
                    },
                    "soccer_3way": {
                        "features": ["elo_probability", "trend_gap"],
                        "primary_65": {
                            "learned_threshold": 0.60980005,
                            "locked_holdout": {
                                "qualified": True,
                                "calls": 53,
                                "hit_rate": 0.660377,
                                "units_at_minus_110": 13.818182,
                            },
                        },
                    },
                }
            }
        },
    }
    (output / "learned-model-validation-final.json").write_text(
        json.dumps({"sports": {}}), encoding="utf-8"
    )
    stale_path = output / "soccer-validation.json"
    stale_path.write_text(json.dumps(stale), encoding="utf-8")
    current_path = output / "soccer-all-data.json"
    current_path.write_text(json.dumps(current), encoding="utf-8")
    os.utime(stale_path, (1, 1))
    os.utime(current_path, (2, 2))
    monkeypatch.setattr(dashboard_server, "OUTPUTS", output)

    result = dashboard_server.matrix()
    cell = result["grid"]["soccer"]["moneyline"]

    assert result["source"].endswith("soccer-all-data.json")
    assert cell["variant_name"] == "elo_only"
    assert cell["hit_rate"] == 0.600746
    assert cell["calls"] == 268
    assert cell["three_way"]["hit_rate"] == 0.660377
    assert cell["three_way"]["calls"] == 53


def test_matrix_marks_mlb_special_markets_blocked_from_readiness(monkeypatch) -> None:
    meta = {
        "multi_market_readiness": {
            "first_five_spread": "BLOCKED_F5_SPREAD",
            "first_five_total": "BLOCKED_F5_TOTAL",
            "yrfi_nrfi": "BLOCKED_YRFI_NRFI",
        }
    }
    validation = {"sports": {"mlb": meta}, "production_artifacts": {}}

    monkeypatch.setattr(dashboard_server, "_newest_validation", lambda: (validation, "test"))
    result = dashboard_server.matrix()["grid"]["mlb"]

    assert result["f5_spread"] == {
        "state": "blocked",
        "readiness": "BLOCKED_F5_SPREAD",
    }
    assert result["f5_total"]["state"] == "blocked"
    assert result["yrfi_nrfi"]["state"] == "blocked"


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
    monkeypatch.setattr(dashboard_server, "_decorate_pick", lambda row: row)

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
