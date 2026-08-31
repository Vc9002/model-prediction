"""Dashboard routes module (extracted from dashboard_server.py, DD-5).

Part of the dashboard_server.py -> dashboard/ package split. See MASTER.md
DD-5 and dashboard/__init__.py for the re-export shim that keeps the old
`dashboard_server` import path and test-suite symbol references working.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

try:
    from openpyxl import load_workbook
except ImportError:  # pragma: no cover
    load_workbook = None  # type: ignore[assignment]


from http.server import BaseHTTPRequestHandler

from model_prediction.dashboard.backtests import (
    backtests,
    market_snapshots,
    odds_summary,
)
from model_prediction.dashboard.common import (
    _DASHBOARD_TOKEN,
    OUTPUTS,
    ROOT,
    _cached,
    _inject_dashboard_token,
    _set_unit_value_usd,
    _today,
    rebuild_view,
)
from model_prediction.dashboard.data_service import (
    handle as data_service_handle,
)
from model_prediction.dashboard.evidence import (
    _production_canary_status,
    model_ledger_comparison,
    production_evidence,
    record_model_ledger_decision,
)
from model_prediction.dashboard.jobs import (
    _REBUILD_VIEWS,
    job_status,
    start_action,
)
from model_prediction.dashboard.matrix import (
    matrix,
)
from model_prediction.dashboard.orders import (
    InvalidSportError,
    _audit_tail,
    _auto_adjust_unit_value,
    _decorate_pick,
    _load_archive,
    _load_orders,
    _load_portfolio_history,
    _safe_sport,
    archive_action,
    bets_view,
    dashboard_picks,
    dedupe_ledger,
    history_picks,
    live_gateway_slate,
    open_picks,
    preview_order,
    preview_position_sell,
    submit_order,
    submit_position_sell,
    today_picks,
)
from model_prediction.dashboard.picks import (
    _parse_research_picks,
    explain_pick,
    performance_for_sport,
    read_flat_picks,
    read_picks,
    read_polymarket_picks,
    read_v9_flat_picks,
)
from model_prediction.dashboard.status import (
    _capture_health_summary,
    _clv_summary,
    _drawdown_summary,
    status,
)
from model_prediction.portfolio.auto_buyer_ledger import (
    AUTO_BUYER_LOG_PATH,
    read_auto_buyer_ledger,
    settle_auto_buyer_ledger,
    summarize_auto_buyer_performance,
)
from model_prediction.portfolio.auto_executor import (
    load_auto_buyer_state,
    run_auto_buyer_cycle,
    toggle_auto_buyer,
)
from model_prediction.portfolio.polymarket_scanner import PolymarketSlateScanner


class Handler(BaseHTTPRequestHandler):
    def _send(self, payload, content_type="application/json", code=200) -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload, default=str).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client disconnected — nothing to do

    def _send_head(self, payload, content_type="application/json", code=200) -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def log_message(self, fmt, *args):  # quiet
        pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        query = {key: values[0] for key, values in params.items()}
        sport_filter = query.get("sport")
        route = parsed.path
        try:
            if route in ("/", "/dashboard.html"):
                page = ROOT / "dashboard.html"
                if page.exists():
                    self._send(_inject_dashboard_token(page.read_bytes()), "text/html; charset=utf-8")
                else:
                    self._send({"error": "dashboard.html missing"}, code=404)
            elif route == "/api/status":
                self._send(_cached("status", 30, status))
            elif route.startswith("/api/data/"):
                # SQL-backed read-only data service (consolidation C):
                # paginated predictions, counts, runs, promotions, health,
                # and the cheap change fingerprint. No Excel, no mutation.
                data_route = route.removeprefix("/api/data/")
                try:
                    self._send(data_service_handle(data_route, parse_qs(parsed.query)))
                except KeyError:
                    self._send({"error": f"unknown data route: {data_route}"}, code=404)
            elif route == "/api/matrix":
                self._send(_cached("matrix", 60, matrix))
            elif route == "/api/production-evidence":
                self._send(_cached("production-evidence", 30, production_evidence))
            elif route == "/api/production-canary":
                self._send(_cached("production-canary", 15, _production_canary_status))
            elif route == "/api/model-ledgers":
                self._send(_cached("model-ledgers", 30, model_ledger_comparison))
            elif route == "/api/picks":
                self._send(_cached("picks", 30, dashboard_picks))
            elif route == "/api/flat-picks":

                def _flat_picks_decorated():
                    flat = read_flat_picks()
                    orders = _load_orders()
                    portfolio = _load_portfolio_history()
                    archived = set(_load_archive().get("pick_ids", []))
                    return [_decorate_pick(row, orders, portfolio, archived_ids=archived) for row in flat]

                self._send(_cached("flat-picks", 30, _flat_picks_decorated))
            elif route == "/api/v9-picks":

                def _v9_picks_decorated():
                    v9_rows = read_v9_flat_picks()
                    orders = _load_orders()
                    portfolio = _load_portfolio_history()
                    archived = set(_load_archive().get("pick_ids", []))
                    return [_decorate_pick(row, orders, portfolio, archived_ids=archived) for row in v9_rows]

                self._send(_cached("v9-picks", 10, _v9_picks_decorated))
            elif route in ("/api/polymarket-picks", "/api/picks/polymarket"):

                def _poly_picks_decorated():
                    poly_rows = read_polymarket_picks()
                    orders = _load_orders()
                    portfolio = _load_portfolio_history()
                    archived = set(_load_archive().get("pick_ids", []))
                    return [
                        _decorate_pick(row, orders, portfolio, archived_ids=archived) for row in poly_rows
                    ]

                self._send(_cached("polymarket-picks", 10, _poly_picks_decorated))
            elif route == "/api/performance":
                sport = str(query.get("sport") or "").strip()
                archived = set(_load_archive().get("pick_ids", []))
                self._send(
                    _cached(
                        f"performance:{sport.casefold() or 'all'}",
                        30,
                        lambda: performance_for_sport(read_picks(), sport, archived_ids=archived),
                    )
                )
            elif route == "/api/flat-performance":
                sport = str(query.get("sport") or "").strip()
                flat = read_flat_picks()
                archived = set(_load_archive().get("pick_ids", []))
                self._send(
                    _cached(
                        f"flat-performance:{sport.casefold() or 'all'}",
                        30,
                        lambda: performance_for_sport(flat, sport, archived_ids=archived),
                    )
                )
            elif route == "/api/research-performance":
                sport = str(query.get("sport") or "").strip()
                research = _parse_research_picks()
                archived = set(_load_archive().get("pick_ids", []))
                self._send(
                    _cached(
                        f"research-performance:{sport.casefold() or 'all'}",
                        30,
                        lambda: performance_for_sport(research, sport, archived_ids=archived),
                    )
                )
            elif route == "/api/research-picks":

                def _research_decorated():
                    orders = _load_orders()
                    portfolio = _load_portfolio_history()
                    archived = set(_load_archive().get("pick_ids", []))
                    return [
                        _decorate_pick(r, orders, portfolio, archived_ids=archived)
                        for r in _parse_research_picks()
                    ]

                self._send(_cached("research-picks", 60, _research_decorated))
            elif route == "/api/gated-research-performance":
                sport = str(query.get("sport") or "").strip()
                archived = set(_load_archive().get("pick_ids", []))
                self._send(
                    _cached(
                        f"gated-research-performance:{sport.casefold() or 'all'}",
                        60,
                        lambda: performance_for_sport(
                            _parse_research_picks(gated=True), sport, archived_ids=archived
                        ),
                    )
                )
            elif route == "/api/gated-research-picks":

                def _gated_research_decorated():
                    orders = _load_orders()
                    portfolio = _load_portfolio_history()
                    archived = set(_load_archive().get("pick_ids", []))
                    return [
                        _decorate_pick(r, orders, portfolio, archived_ids=archived)
                        for r in _parse_research_picks(gated=True)
                    ]

                self._send(_cached("gated-research-picks", 60, _gated_research_decorated))
            elif route == "/api/backtests":
                self._send(_cached("backtests", 60, backtests))
            elif route == "/api/backtest":
                name = Path(query.get("file", "")).name
                path = OUTPUTS / name
                if path.exists() and path.suffix == ".json":
                    self._send(path.read_bytes())
                else:
                    self._send({"error": "not found"}, code=404)
            elif route == "/api/market":
                sport = _safe_sport(query.get("sport", "mlb"))
                day = query.get("date") or _today()
                self._send(_cached(f"market:{sport}:{day}", 60, lambda: market_snapshots(sport, day)))
            elif route == "/api/live":
                sport = _safe_sport(query.get("sport", "mlb"))
                day = query.get("date") or _today()
                self._send(_cached(f"live:{sport}:{day}", 120, lambda: live_gateway_slate(sport, day)))
            elif route == "/api/audit":
                self._send(_cached("audit", 60, _audit_tail))
            elif route == "/api/job":
                self._send(job_status(str(query.get("id", ""))))
            elif route == "/api/today":
                day = query.get("date") or _today()
                self._send(_cached(f"today:{day}", 20, lambda: today_picks(day)))
            elif route == "/api/odds":
                odds_sport = query.get("sport")
                self._send(
                    _cached(
                        f"odds:{odds_sport or 'all'}",
                        30,
                        lambda: odds_summary(odds_sport if odds_sport else None),
                    )
                )
            elif route == "/api/open":
                self._send(_cached("open", 15, open_picks))
            elif route == "/api/history":
                days = int(query.get("days", "30"))
                history_sport = query.get("sport")
                self._send(
                    _cached(
                        f"history:{days}:{history_sport or 'all'}",
                        30,
                        lambda: history_picks(days, history_sport),
                    )
                )
            elif route == "/api/bets":
                self._send(_cached("bets", 15, bets_view))
            elif route == "/api/orders":
                self._send(_load_orders())
            elif route == "/api/clv":
                clv_sport = query.get("sport")
                self._send(_cached(f"clv:{clv_sport or 'all'}", 60, lambda: _clv_summary(clv_sport)))
            elif route == "/api/capture_health":
                self._send(_cached("capture_health", 60, _capture_health_summary))
            elif route == "/api/polymarket/scan":
                sport_param = query.get("sport")
                sport_filter = None if not sport_param or sport_param == "all" else sport_param
                date_filter = query.get("date")
                bankroll = float(query.get("bankroll", "1000.0"))
                min_edge = float(query.get("min_edge", "0.025"))
                maker = query.get("maker", "false").lower() in ("true", "1", "yes")
                require_model = query.get("require_model", "true").lower() in ("true", "1", "yes")
                pregame_only = query.get("pregame_only", "true").lower() in ("true", "1", "yes")
                timeframe = query.get("timeframe", "today").lower()
                today_only = timeframe in ("today", "24h", "true", "1")
                max_start_hours = (
                    24.0 if timeframe in ("today", "24h") else (48.0 if timeframe == "48h" else None)
                )
                max_age_param = query.get("max_age", "60")
                try:
                    max_age_minutes = (
                        None
                        if not max_age_param or max_age_param.lower() in ("all", "none")
                        else int(max_age_param)
                    )
                except ValueError:
                    max_age_minutes = 60
                live = query.get("live", "false").lower() in ("true", "1", "yes")

                if live:
                    try:
                        from datetime import date

                        from model_prediction.data_sources.polymarket_us import (
                            PolymarketUSClient,
                            capture_slate_snapshots,
                        )

                        client = PolymarketUSClient()
                        today_utc = datetime.now(UTC).strftime("%Y-%m-%d")
                        sports_to_fetch = (
                            [sport_filter]
                            if sport_filter and sport_filter != "all"
                            else ["mlb", "wnba", "tennis", "soccer", "esports", "kbo", "npb"]
                        )
                        all_events = {}
                        for s in sports_to_fetch:
                            try:
                                res_s = client.sport_slate(s, date.fromisoformat(today_utc))
                                for lg, evs in res_s.events.items():
                                    all_events[lg] = evs
                            except Exception:  # noqa: BLE001, S110
                                pass
                        if all_events:
                            capture_slate_snapshots(client, all_events, ROOT / "data", today_utc)
                    except Exception:  # noqa: BLE001, S110
                        pass

                def _do_scan():
                    scanner = PolymarketSlateScanner(
                        bankroll=bankroll,
                        min_edge=min_edge,
                    )
                    res = scanner.scan_directory(
                        base_dir=ROOT / "data" / "odds",
                        sport_filter=sport_filter,
                        date_filter=date_filter,
                        prefer_maker=maker,
                        require_model=require_model,
                        pregame_only=pregame_only,
                        today_only=today_only,
                        timeframe=timeframe,
                        max_start_hours=max_start_hours,
                        max_age_minutes=max_age_minutes,
                    )
                    return {
                        "as_of_utc": res.as_of_utc,
                        "total_markets_scanned": res.total_markets_scanned,
                        "actionable_orders_count": res.actionable_orders_count,
                        "total_capital_staked": res.total_capital_staked,
                        "orders": [
                            {
                                "market_id": o.market_id,
                                "question": o.question,
                                "side": o.side,
                                "target_selection": o.target_selection,
                                "target_side": o.target_side,
                                "selection_label": o.selection_label,
                                "home_team": o.home_team,
                                "away_team": o.away_team,
                                "order_price": o.order_price,
                                "model_probability": o.model_probability,
                                "market_price": o.market_price,
                                "edge": o.edge,
                                "ev_pct": o.expected_value_pct,
                                "stake_units": o.stake_units,
                                "kelly_fraction": o.kelly_fraction_recommended,
                                "is_maker": o.is_maker,
                                "reason": o.reason,
                                "event_start_utc": o.event_start_utc,
                                "observed_at_utc": o.observed_at_utc,
                                "depth_imbalance": getattr(o, "depth_imbalance", 0.5),
                                "spread_cents": getattr(o, "spread_cents", 0.0),
                            }
                            for o in res.actionable_orders
                        ],
                    }

                cache_key = f"polymarket:scan:{sport_filter or 'all'}:{date_filter or 'all'}:{bankroll}:{min_edge}:{maker}:{require_model}:{pregame_only}:{timeframe}:{max_age_minutes}"
                if live:
                    self._send(_do_scan())
                else:
                    self._send(_cached(cache_key, 10, _do_scan))
            elif route == "/api/polymarket/rehearsal":
                from dataclasses import asdict

                from model_prediction.portfolio.execution_rehearsal import ExecutionRehearsalRunner

                sport_param = query.get("sport")
                r_sport_filter = None if not sport_param or sport_param == "all" else sport_param
                r_date_filter = query.get("date")
                r_bankroll = float(query.get("bankroll", "1000.0"))
                r_min_edge = float(query.get("min_edge", "0.025"))
                r_maker = query.get("maker", "false").lower() in ("true", "1", "yes")
                r_require_model = query.get("require_model", "false").lower() in ("true", "1", "yes")

                runner = ExecutionRehearsalRunner(bankroll=r_bankroll, min_edge=r_min_edge)
                rehearsal_report = runner.run_rehearsal(
                    sport_filter=r_sport_filter,
                    date_filter=r_date_filter,
                    prefer_maker=r_maker,
                    require_model=r_require_model,
                )
                self._send(asdict(rehearsal_report))
            elif route == "/api/health":
                self._send({"ok": True, "at": datetime.now(UTC).isoformat()[:19]})
            elif route == "/api/auto-buyer/status":
                self._send(load_auto_buyer_state())
            elif route == "/api/auto-buyer/summary":
                self._send(_cached("auto-buyer-summary", 5, summarize_auto_buyer_performance))
            elif route == "/api/auto-buyer/ledger":
                self._send(_cached("auto-buyer-ledger", 5, read_auto_buyer_ledger))
            elif route == "/api/auto-buyer/log":
                if AUTO_BUYER_LOG_PATH.exists():
                    try:
                        lines = AUTO_BUYER_LOG_PATH.read_text(encoding="utf-8").splitlines()
                        self._send({"lines": lines[-200:]})
                    except OSError:
                        self._send({"lines": []})
                else:
                    self._send({"lines": []})
            elif route == "/api/drawdown":
                self._send(
                    _cached(f"drawdown:{sport_filter or 'all'}", 10, lambda: _drawdown_summary(sport_filter))
                )
            elif route == "/api/export/picks":
                import csv
                import io

                ledger_type = (params.get("ledger") or ["main"])[0].lower()
                if ledger_type == "flat":
                    raw_rows = read_flat_picks()
                elif ledger_type == "polymarket":
                    raw_rows = read_polymarket_picks()
                else:
                    raw_rows = read_picks()

                output = io.StringIO()
                if raw_rows:
                    fieldnames = list(raw_rows[0].keys())
                    writer = csv.DictWriter(output, fieldnames=fieldnames)
                    writer.writeheader()
                    for r in raw_rows:
                        writer.writerow(r)
                csv_bytes = output.getvalue().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{ledger_type}_picks.csv"')
                self.send_header("Content-Length", str(len(csv_bytes)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(csv_bytes)
                return
            elif route == "/api/picks/explanation":
                pick_id = query.get("pick_id")
                if not pick_id:
                    self._send({"error": "pick_id parameter required"}, code=400)
                else:
                    self._send(_cached(f"pick_exp:{pick_id}", 30, lambda: explain_pick(pick_id)))
            elif route.startswith("/api/rebuild/"):
                view = route.removeprefix("/api/rebuild/")
                if view in _REBUILD_VIEWS:
                    self._send(_cached(f"rebuild:{view}", 30, lambda: rebuild_view(view)))
                else:
                    self._send({"error": "unknown route"}, code=404)
            else:
                self._send({"error": "unknown route"}, code=404)
        except InvalidSportError as error:
            self._send({"error": str(error)}, code=400)
        except Exception as error:  # noqa: BLE001 - route handler boundary, always returns a response
            self._send({"error": f"{type(error).__name__}: {error}"}, code=500)

    def do_HEAD(self) -> None:
        route = urlparse(self.path).path
        if route.startswith("/api/data/"):
            self._send_head({"ok": True})
            return
        if route.startswith("/api/rebuild/"):
            view = route.removeprefix("/api/rebuild/")
            if view in _REBUILD_VIEWS:
                try:
                    self._send_head(_cached(f"rebuild:{view}", 30, lambda: rebuild_view(view)))
                except Exception as error:  # noqa: BLE001 - route handler boundary, always returns a response
                    self._send_head({"error": f"{type(error).__name__}: {error}"}, code=500)
                return
        self._send_head({"error": "unknown route"}, code=404)

    def _reject_rebuild_mutation(self) -> bool:
        if urlparse(self.path).path.startswith("/api/rebuild/"):
            self._send(
                {"error": "method not allowed", "allowed_methods": ["GET", "HEAD"]},
                code=405,
            )
            return True
        return False

    def _local_origin_ok(self) -> bool:
        """Reject cross-site (CSRF) POSTs: browser requests from any web page
        carry an Origin header; only same-host origins (or none — curl, CLI)
        are allowed to hit state-changing routes on this local server."""
        origin = str(self.headers.get("Origin") or "")
        if not origin:
            return True
        host = str(self.headers.get("Host") or "")
        return origin in (f"http://{host}", f"https://{host}") or origin.startswith(
            ("http://127.0.0.1:", "http://localhost:")
        )

    def do_POST(self) -> None:
        if self._reject_rebuild_mutation():
            return
        if not self._local_origin_ok():
            self._send({"status": "refused", "error": "cross-origin request rejected"}, code=403)
            return
        if not secrets.compare_digest(str(self.headers.get("X-Dashboard-Token") or ""), _DASHBOARD_TOKEN):
            self._send({"status": "refused", "error": "missing or invalid dashboard session token"}, code=401)
            return
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            payload = {}
        if payload.get("confirm") is not True:
            self._send(
                {"status": "refused", "error": "confirmation required: resend with confirm=true"}, code=400
            )
            return
        if parsed.path == "/api/archive":
            action = str(payload.get("action"))
            scope = payload.get("pick_ids") if action == "clear_ids" else str(payload.get("scope", ""))
            self._send(archive_action(action, scope or []))
        elif parsed.path == "/api/dedupe":
            self._send(dedupe_ledger())
        elif parsed.path == "/api/model-ledgers/decision":
            self._send(record_model_ledger_decision(payload))
        elif parsed.path == "/api/action":
            self._send(start_action(str(payload.get("action")), payload))
        elif parsed.path == "/api/order/preview":
            self._send(preview_order(payload))
        elif parsed.path == "/api/order/preview-position":
            self._send(preview_position_sell(payload))
        elif parsed.path == "/api/order/submit-position":
            self._send(submit_position_sell(payload))
        elif parsed.path == "/api/order/submit":
            self._send(submit_order(payload))
        elif parsed.path == "/api/settings/unit-value":
            try:
                self._send(_set_unit_value_usd(payload.get("unit_value_usd")))
            except (OSError, RuntimeError, ValueError, yaml.YAMLError) as error:
                self._send({"status": "refused", "error": str(error)}, code=400)
        elif parsed.path == "/api/settings/auto-unit-value":
            pct = float(payload.get("pct", 10))
            self._send(_auto_adjust_unit_value(pct))
        elif parsed.path == "/api/polymarket/record":
            from model_prediction.portfolio.polymarket_ledger import record_polymarket_orders

            orders = payload.get("orders") or []
            res = record_polymarket_orders(orders)
            self._send(res)
        elif parsed.path == "/api/polymarket/settle":
            from model_prediction.portfolio.polymarket_ledger import settle_polymarket_ledger_rows

            res = settle_polymarket_ledger_rows()
            self._send(res)
        elif parsed.path == "/api/auto-buyer/toggle":
            enabled = payload.get("enabled")
            self._send(toggle_auto_buyer(enabled))
        elif parsed.path == "/api/auto-buyer/run":
            exec_override = payload.get("execute")
            self._send(run_auto_buyer_cycle(execute_override=exec_override, force=True))
        elif parsed.path == "/api/auto-buyer/settle":
            self._send(settle_auto_buyer_ledger())
        else:
            self._send({"error": "unknown route"}, code=404)

    def do_PUT(self) -> None:
        if not self._reject_rebuild_mutation():
            self._send({"error": "unknown route"}, code=404)

    def do_PATCH(self) -> None:
        if not self._reject_rebuild_mutation():
            self._send({"error": "unknown route"}, code=404)

    def do_DELETE(self) -> None:
        if not self._reject_rebuild_mutation():
            self._send({"error": "unknown route"}, code=404)
