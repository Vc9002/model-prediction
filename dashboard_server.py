#!/usr/bin/env python3
"""Local operations dashboard server for the model-prediction system.

DD-5 (fixed): this used to be a 5,358-line monolith with all business
logic plus HTTP routing in one file. It's now a thin entrypoint over the
`model_prediction.dashboard` package (common/picks/evidence/status/matrix/
backtests/orders/jobs/routes) -- see MASTER.md DD-5 and
`src/model_prediction/dashboard/__init__.py` for the module split. This
file re-exports every symbol from that package so every old
`dashboard_server._X` import (including the test suite's direct symbol
imports/monkeypatches) keeps resolving unchanged.

Serves dashboard.html plus a small JSON API computed from the project's data
files. View clearing and order status use local dashboard state. Real order
submission remains behind the model-prediction CLI's hard gate and a separate,
exact-ticket confirmation in the UI.

Run:  python3 dashboard_server.py  [--port 8765]
Then open http://127.0.0.1:8765/
"""

from __future__ import annotations

import argparse
import os
import subprocess  # noqa: F401 -- re-export for compat (tests patch dashboard_server.subprocess.run)
import sys
from http.server import ThreadingHTTPServer

import yaml  # noqa: F401 -- re-export for compat (tests read dashboard_server.yaml)

from model_prediction.dashboard import data_service_handle  # noqa: F401 -- re-export for compat
from model_prediction.dashboard.backtests import (  # noqa: F401 -- re-export for compat
    _lines_match,
    _pick_quote,
    _row_line,
    _row_matches_snapshot_event,
    _row_selected_team,
    _spread_side_for_row,
    _team_matches,
    _total_side_for_row,
    backtests,
    market_snapshots,
    odds_summary,
)
from model_prediction.dashboard.common import (  # noqa: F401 -- re-export for compat
    _ACTION_LOCK,
    _CACHE,
    _CACHE_LOCK,
    _CONFIG_LOCK,
    _DASHBOARD_CACHE,
    _DASHBOARD_TOKEN,
    _ENV_PATH,
    _FLAT_PICKS_CACHE,
    _JOBS,
    _JOBS_LOCK,
    _LAST_ACTION,
    _MAIN_LEDGER_SPORTS,
    _MARKET_QUESTION_CACHE,
    _MARKET_QUESTION_LOCK,
    _ORDER_LOCK,
    _ORDER_PREVIEWS,
    _PICKS_CACHE,
    _RUNNER,
    ARCHIVE_FILE,
    CONFIG_FILE,
    DASH_DIR,
    DASHBOARD_PORT,
    DATA,
    EASTERN,
    FEATURE_REGISTRY_FILE,
    GATEWAY,
    JOBS_FILE,
    LOG_FILE,
    ORDERS_FILE,
    OUTPUTS,
    PID_FILE,
    PORTFOLIO_HISTORY_FILE,
    ROOT,
    SPORTS,
    _cached,
    _config_payload,
    _count_lines,
    _inject_dashboard_token,
    _log,
    _manual_research_eligibility,
    _number,
    _read_json,
    _resolve_runner,
    _row_has_banned_team,
    _runner_env,
    _runtime_paths,
    _runtime_paths_cache,
    _set_unit_value_usd,
    _today,
    _unit_value_usd,
    rebuild_view,
)
from model_prediction.dashboard.evidence import (  # noqa: F401 -- re-export for compat
    _artifact_evidence,
    _artifact_hash,
    _backfill_aliases,
    _deduplicate_ledger_rows,
    _feature_attribution,
    _feature_registry_evidence,
    _ledger_deduplication_key,
    _ledger_evidence_for_source,
    _locked_backfill_evidence,
    _model_evidence_from_rows,
    _model_owns_row,
    _normalized_line,
    _pnl_evidence,
    _production_canary_status,
    _production_model_spec,
    _read_evidence_ledger,
    _read_model_ledger_rows,
    _rolling_declared_hash,
    _version_ledger_evidence,
    model_ledger_comparison,
    production_evidence,
    record_model_ledger_decision,
)
from model_prediction.dashboard.jobs import (  # noqa: F401 -- re-export for compat
    _REBUILD_VIEWS,
    _hydrate_jobs,
    _job_status_for_returncode,
    _latest_persisted_action,
    _load_persisted_jobs,
    _persist_jobs,
    job_status,
    start_action,
)
from model_prediction.dashboard.matrix import (  # noqa: F401 -- re-export for compat
    _config_production_artifact_path,
    _ml_cell,
    _newest_validation,
    _production_artifact,
    matrix,
)
from model_prediction.dashboard.orders import (  # noqa: F401 -- re-export for compat
    InvalidSportError,
    _action_command,
    _activity_link,
    _activity_on_or_after,
    _activity_outcome_side,
    _all_ledger_rows_for_price_scan,
    _amount_value,
    _audit_tail,
    _auto_adjust_unit_value,
    _dashboard_order_status,
    _decode_command_output,
    _decorate_pick,
    _dedupe_picks,
    _event_already_started,
    _filled_entry_for_pick,
    _human_market_name,
    _latest_order_for_pick,
    _live_bbo,
    _live_model_links,
    _load_archive,
    _load_orders,
    _load_portfolio_history,
    _model_version_rank,
    _net_position_quantity,
    _normalize_live_activity,
    _order_readiness,
    _pick_identity,
    _portfolio_history_summary,
    _public_market_question,
    _reconcile_orders,
    _reconcile_orders_locked,
    _safe_sport,
    _save_archive,
    _save_orders,
    _save_portfolio_history,
    _selected_short_pnl,
    _suggested_units,
    _team_name_index,
    archive_action,
    bets_view,
    dashboard_picks,
    dedupe_ledger,
    history_picks,
    live_gateway_slate,
    live_portfolio_view,
    open_picks,
    preview_order,
    preview_position_sell,
    submit_order,
    submit_position_sell,
    today_picks,
)
from model_prediction.dashboard.picks import (  # noqa: F401 -- re-export for compat
    _find_pick_by_id,
    _flat_ledger_paths,
    _main_ledger_paths,
    _parse_picks,
    _parse_research_picks,
    _performance_breakdown,
    _performance_game_key,
    _pick_is_scored,
    _pick_pnl,
    _pick_probability,
    _read_split_picks,
    _research_ledger_paths,
    performance,
    performance_for_sport,
    read_flat_picks,
    read_picks,
)
from model_prediction.dashboard.routes import (
    Handler,
)
from model_prediction.dashboard.status import (  # noqa: F401 -- re-export for compat
    _daily_pipeline_status,
    _data_inventory,
    status,
)

__all__ = ["main"]


def main() -> None:
    arguments = argparse.ArgumentParser()
    arguments.add_argument("--port", type=int, default=DASHBOARD_PORT)
    options = arguments.parse_args()

    DASH_DIR.mkdir(exist_ok=True)
    _hydrate_jobs()
    server = None
    my_pid = os.getpid()
    try:
        ThreadingHTTPServer.daemon_threads = True
        ThreadingHTTPServer.allow_reuse_address = True
        server = ThreadingHTTPServer(("127.0.0.1", options.port), Handler)
        PID_FILE.write_text(str(my_pid))  # Write only after successful bind
        print(f"dashboard: http://127.0.0.1:{options.port}/  (Ctrl-C to stop)")
        print(f"dashboard: session token (for direct API calls): {_DASHBOARD_TOKEN}")
        server.serve_forever()
    except OSError as exc:
        if exc.errno == 48:
            print(f"dashboard: port {options.port} busy — is another instance running?", file=sys.stderr)
        else:
            raise
    except KeyboardInterrupt:
        print("\ndashboard stopped")
    finally:
        if server is not None:
            server.server_close()
        if PID_FILE.exists():
            try:
                if PID_FILE.read_text().strip() == str(my_pid):
                    PID_FILE.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    main()
