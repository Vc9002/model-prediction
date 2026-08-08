from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import dashboard_server
from dashboard.rebuild_status import RebuildStatusReader


def test_missing_rebuild_state_is_safe_and_creates_nothing(tmp_path: Path) -> None:
    reader = RebuildStatusReader(tmp_path)

    payloads = [
        reader.status(),
        reader.sports(),
        reader.benchmark(),
        reader.economics(),
        reader.runs(),
        reader.health(),
    ]

    assert all(payload["status"] in {"unavailable", "degraded"} for payload in payloads)
    assert reader.economics()["pnl"] is None
    assert reader.economics()["roi"] is None
    assert reader.economics()["clv"] is None
    assert list(tmp_path.rglob("*")) == []


def test_malformed_rebuild_artifacts_degrade_without_raising(tmp_path: Path) -> None:
    output = tmp_path / "outputs" / "rebuild"
    output.mkdir(parents=True)
    (output / "multisport_status.json").write_text("{not-json", encoding="utf-8")
    (output / "mlb_head_distribution_cartesian.json").write_text("[]", encoding="utf-8")

    reader = RebuildStatusReader(tmp_path)

    assert reader.sports()["status"] == "degraded"
    assert reader.benchmark()["status"] == "unavailable"
    assert reader.status()["status"] == "degraded"


def test_unconsumed_final_test_is_backend_redacted(tmp_path: Path) -> None:
    output = tmp_path / "outputs" / "rebuild"
    output.mkdir(parents=True)
    (output / "test_consumption_registry.json").write_text(
        json.dumps(
            {
                "active_tests": {
                    "mlb_moneyline_v2": {
                        "consumed": False,
                        "test_start": "2026-08-08T02:20Z",
                        "test_end": None,
                        "accuracy": 0.99,
                        "log_loss": 0.01,
                        "brier": 0.01,
                        "roi": 2.0,
                        "clv": 1.0,
                        "note": "secret aggregate performance",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    sealed = RebuildStatusReader(tmp_path).status()["sealed_tests"][0]

    assert sealed == {
        "test_id": "mlb_moneyline_v2",
        "consumed": False,
        "test_start": "2026-08-08T02:20Z",
        "test_end": None,
        "model_hash": None,
        "calibrator_hash": None,
        "prediction_count": 0,
        "coverage": None,
        "performance_hidden": True,
    }


def test_no_fill_economics_remain_null(tmp_path: Path) -> None:
    database = tmp_path / "data" / "rebuild" / "shadow.db"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE market_evaluations(id INTEGER);
            CREATE TABLE trade_decisions(action TEXT, reason_code TEXT);
            CREATE TABLE paper_orders(filled_units REAL, avg_fill_price REAL);
            CREATE TABLE settlements(paper_order_id INTEGER, pnl REAL);
            CREATE TABLE closing_prices(closing_price REAL);
            INSERT INTO trade_decisions VALUES ('NO_BET', 'stale_quote');
            INSERT INTO settlements VALUES (NULL, 99.0);
            """
        )

    payload = RebuildStatusReader(tmp_path).economics()

    assert payload["status"] == "ok"
    assert payload["bet"] == 0
    assert payload["no_bet"] == 1
    assert payload["fills"] == 0
    assert payload["pnl"] is None
    assert payload["roi"] is None
    assert payload["clv"] is None


def test_six_rebuild_get_routes_are_read_only(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_server, "rebuild_view", lambda name: {"view": name})
    dashboard_server._CACHE.clear()
    views = ("status", "sports", "benchmark", "economics", "runs", "health")

    for view in views:
        sent: list[tuple[object, int]] = []
        handler = dashboard_server.Handler.__new__(dashboard_server.Handler)
        handler.path = f"/api/rebuild/{view}"
        handler._send = (
            lambda payload, _content_type="application/json", code=200, _sent=sent: _sent.append(
                (payload, code)
            )
        )

        dashboard_server.Handler.do_GET(handler)

        assert sent == [({"view": view}, 200)]


def test_six_rebuild_head_routes_are_read_only(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_server, "rebuild_view", lambda name: {"view": name})
    dashboard_server._CACHE.clear()

    for view in ("status", "sports", "benchmark", "economics", "runs", "health"):
        sent: list[tuple[object, int]] = []
        handler = dashboard_server.Handler.__new__(dashboard_server.Handler)
        handler.path = f"/api/rebuild/{view}"
        handler._send_head = (
            lambda payload, _content_type="application/json", code=200, _sent=sent: _sent.append(
                (payload, code)
            )
        )

        dashboard_server.Handler.do_HEAD(handler)

        assert sent == [({"view": view}, 200)]


def test_rebuild_namespace_rejects_all_mutating_http_methods() -> None:
    for method in ("do_POST", "do_PUT", "do_PATCH", "do_DELETE"):
        sent: list[tuple[object, int]] = []
        handler = dashboard_server.Handler.__new__(dashboard_server.Handler)
        handler.path = "/api/rebuild/status"
        handler._send = (
            lambda payload, _content_type="application/json", code=200, _sent=sent: _sent.append(
                (payload, code)
            )
        )

        getattr(dashboard_server.Handler, method)(handler)

        assert sent == [
            ({"error": "method not allowed", "allowed_methods": ["GET", "HEAD"]}, 405)
        ]


def test_rebuild_ui_is_labeled_and_has_no_execution_controls() -> None:
    html = (Path(__file__).resolve().parents[1] / "dashboard.html").read_text(encoding="utf-8")
    rebuild = html.split('<section id="tab-rebuild"', 1)[1].split(
        '<section id="tab-evidence"', 1
    )[0]

    assert "CLEAN-SLATE REBUILD — SHADOW ONLY" in rebuild
    assert "NO LIVE EXECUTION" in rebuild
    assert "A rejected challenger is a research outcome, not a system failure." in rebuild
    assert "<button" not in rebuild
    assert "/api/rebuild/order" not in html
    assert "/api/rebuild/execute" not in html
    assert "/api/rebuild/promote" not in html
    assert 'if(b.dataset.tab==="rebuild")loadRebuild();' in html
