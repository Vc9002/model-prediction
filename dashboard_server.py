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
import socket
import sqlite3
import subprocess  # noqa: F401 -- re-export for compat (tests patch dashboard_server.subprocess.run)
import sys
import time
from http.server import ThreadingHTTPServer

# numpy and scipy each bundle an OpenBLAS that commits a per-thread buffer pool
# sized to the core count at import (~740 MB private on this 12-core host):
# private bytes after `import dashboard_server` measured 903 MB -> 160 MB with one
# thread, working set unchanged. The dashboard does no heavy linear algebra.
# Jobs the dashboard launches inherit it through _runner_env (os.environ copy),
# matching run_supervisor._worker_env. setdefault so an operator-set value
# wins; this must stay above the
# model_prediction imports below because they load numpy transitively.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

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
    _SWR,
    _TTL_OVERRIDE,
    _WARMING,
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
    _cache_rebuild,
    _cache_snapshot_load,
    _cache_snapshot_save,
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
from model_prediction.runtime_paths import RuntimePaths
from model_prediction.stealth_runtime import cooperative_sleep, enable_background_priority

__all__ = ["main"]

import threading as _threading
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor

# The 7 warmer threads used to rebuild concurrently. A probe of 4 concurrent
# passes with the cache retained grew working set 617 -> 719 -> 710 -> 1032 MB
# (1.7 GB peak) with unchanged cache contents, because stacked build peaks
# fragment the heap; sequential builds peaked ~1.6 GB and settled ~560 MB.
# Taken only in _warm around _cache_rebuild (warm lock, then the per-key build
# lock); never inside dashboard/common.py, so the SWR request path never waits on it.
_WARM_BUILD_LOCK = _threading.Lock()


def _canonical_ledger_signal() -> int:
    """MAX(rowid) of the canonical ledger's ledger_events, or -1 when unreadable.

    The canonical ledger is a ~1.5 GB WAL database whose file mtime changes even
    on reads, so its stat is useless as a change signal. ledger_events is
    append-only with one row per mutation (RuntimeLedgerStore.apply writes the
    record and its event in one transaction; DashboardCache keys on COUNT(*) of
    the same table); MAX(rowid) is an index seek, and mode=ro never creates the file or
    checkpoints the WAL. Resolved on every call rather than through
    _runtime_paths(), which caches the first resolution process-wide and would
    pin a test's temporary root for the rest of the process. Fails soft without
    logging: a missing ledger is a normal env-less state.
    """
    try:
        db = RuntimePaths.resolve(repo_root=ROOT, require_external_runtime=True).ledgers_db
        conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0)
        try:
            value = conn.execute("SELECT MAX(rowid) FROM ledger_events").fetchone()[0]
        finally:
            conn.close()
        return int(value or 0)
    except Exception:  # noqa: BLE001
        return -1


def _warm_fingerprint() -> tuple:
    """Cheap change detector: (mtime, size) of the ledgers + recent data dirs,
    plus the canonical SQLite ledger's event signal."""
    stats = []
    paths = [
        *_main_ledger_paths(),
        *_flat_ledger_paths(),
        *_research_ledger_paths(),
        *_research_ledger_paths(gated=True),
        DASH_DIR / "jobs.json",
    ]
    for directory in (DATA / "historical", DATA / "flat"):
        try:
            paths.extend(p for p in directory.iterdir() if p.is_file())
        except OSError:
            pass
    for path in paths:
        try:
            st = path.stat()
            stats.append((str(path), st.st_mtime_ns, st.st_size))
        except OSError:
            stats.append((str(path), 0, 0))
    stats.append(("ledgers.db:ledger_events", _canonical_ledger_signal(), 0))
    return tuple(stats)


# Keys whose builds read live inputs the fingerprint cannot see keep the old
# 300 s age rebuild: "picks"/"flat-picks" decorate rows with Polymarket quote
# snapshots (rewritten every 10 min by the capture tasks), compute quote.fresh,
# game-started and buy_ready at build time, and run exchange order
# reconciliation; "status" reads the daily pipeline logs. Only the heavy,
# ledger-and-artifact-driven views (production evidence is the 43 s xlsx parse)
# wait 1800 s when nothing they watch has changed.
_WARM_FAST_KEYS = frozenset({"picks", "flat-picks", "status"})
_WARM_CHANGE_MAX_DELAY = 300.0


def _warm_max_age(key: str) -> float:
    return 300.0 if key in _WARM_FAST_KEYS else 1800.0


def _warm_caches(forever: bool = False) -> None:
    """Pre-compute all expensive endpoints in parallel background threads.

    One pass by default, so the first browser request hits the cache instead of
    blocking on cold Excel/JSONL reads. With forever=True (the server) each entry
    is then kept fresh and snapshotted to disk whenever its source files change.
    """
    warmers = [
        ("status", 30, status),
        ("matrix", 60, matrix),
        ("production-evidence", 30, production_evidence),
        ("model-ledgers", 30, model_ledger_comparison),
        ("picks", 120, dashboard_picks),
        ("flat-picks", 120, read_flat_picks),
        # Same key and builder as /api/performance (routes.py); performance()
        # itself needs a picks list, so the bare function can never be warmed.
        (
            "performance:all",
            30,
            lambda: performance_for_sport(
                read_picks(), "", archived_ids=set(_load_archive().get("pick_ids", []))
            ),
        ),
    ]

    first_pass: set[str] = set()
    if forever:
        for key, _ttl, _builder in warmers:
            _TTL_OVERRIDE[key] = _warm_max_age(key) + 60

    def _warm(item):
        # Rebuild a key only when its source files changed (debounced so a
        # half-written ledger isn't read) or after its max age. Requests are
        # always served from the cache (stale-while-revalidate), so opening the
        # dashboard never waits on a build.
        key, _ttl, builder = item
        max_age = _warm_max_age(key)
        built_at, built_fp = 0.0, None
        pending_fp, pending_n = None, 0
        pending_since: float | None = None  # first poll that differed from the built fingerprint
        while True:
            try:
                fp = _warm_fingerprint()
                with _CACHE_LOCK:
                    present = key in _CACHE
                if fp != built_fp:
                    if pending_since is None:
                        pending_since = time.monotonic()
                    if fp == pending_fp:
                        pending_n += 1
                    else:
                        pending_fp, pending_n = fp, 1
                # The debounce must not hold a real change back indefinitely: a
                # daily run can change the fingerprint on every 30 s poll for
                # half an hour, which would leave only the 1800 s age rebuild.
                # Measured from when the change was first seen (not from the last
                # build) so the first change after a quiet spell is still debounced.
                stale_change = (
                    pending_since is not None and time.monotonic() - pending_since >= _WARM_CHANGE_MAX_DELAY
                )
                changed = fp != built_fp and (built_fp is None or pending_n >= 2 or stale_change)
                if not present or changed or time.monotonic() - built_at > max_age:
                    with _WARM_BUILD_LOCK:
                        _cache_rebuild(key, builder)
                    built_at, built_fp = time.monotonic(), fp
                    pending_fp, pending_n, pending_since = None, 0, None
                    first_pass.add(key)
                    if forever and len(first_pass) == len(warmers):
                        _WARMING["on"] = False
                    if forever:
                        try:
                            _cache_snapshot_save([k for k, _t, _b in warmers])
                        except Exception as exc:  # noqa: BLE001
                            _log(f"cache snapshot save failed (non-fatal): {exc!r}")
            except Exception as exc:  # noqa: BLE001
                _log(f"cache warmer: {key} failed (non-fatal): {exc!r}")
                built_at = time.monotonic()  # back off instead of hot-looping on errors
                first_pass.add(key)  # a failing key must not pin the "warming" banner on
                if forever and len(first_pass) == len(warmers):
                    _WARMING["on"] = False
            if not forever:
                return
            cooperative_sleep(30)

    # concurrent.futures registers an atexit hook (threading._register_atexit)
    # that joins every executor worker at interpreter shutdown. The forever loops
    # never return, so running them in a pool hung every exit of main() (Ctrl-C, a
    # serve_forever error, pytest), and builders that submit to their own executors
    # logged "cannot schedule new futures after interpreter shutdown" every 30 s.
    # Plain daemon threads are abandoned at shutdown instead (a builder's own
    # pool still finishes its in-flight items, so exit can wait for one build and
    # may log that line once). One-pass mode returns, so it keeps the pool.
    if forever:
        threads = [
            _threading.Thread(target=_warm, args=(item,), daemon=True, name=f"cache-warmer:{item[0]}")
            for item in warmers
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return

    with _ThreadPoolExecutor(max_workers=len(warmers)) as pool:
        list(pool.map(_warm, warmers))


def _bind_single_instance(server: ThreadingHTTPServer) -> None:
    """Bind and listen so that a second dashboard on the same port fails instead of sharing it.

    On Windows SO_REUSEADDR (what ``allow_reuse_address`` sets) lets a second socket bind a port a
    live server is already listening on, so a second launch (start_dashboard.ps1 falls back to a
    direct start when the task's server is not listening within 3 s) ran a second server silently.
    SO_EXCLUSIVEADDRUSE makes that bind fail. POSIX keeps SO_REUSEADDR, which there only skips
    TIME_WAIT and never admits a second listener.
    """
    if sys.platform == "win32":
        server.allow_reuse_address = False
        server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        server.allow_reuse_address = True
    try:
        server.server_bind()
        server.server_activate()
    except OSError:
        server.server_close()
        raise


def main() -> None:
    enable_background_priority()
    arguments = argparse.ArgumentParser()
    arguments.add_argument("--port", type=int, default=DASHBOARD_PORT)
    options = arguments.parse_args()

    DASH_DIR.mkdir(exist_ok=True)
    _hydrate_jobs()
    server = None
    my_pid = os.getpid()
    try:
        ThreadingHTTPServer.daemon_threads = True
        server = ThreadingHTTPServer(("127.0.0.1", options.port), Handler, bind_and_activate=False)
        _bind_single_instance(server)
        PID_FILE.write_text(str(my_pid))  # Write only after successful bind
        _SWR["on"] = True  # serve stale + refresh in background (server process only)
        loaded = _cache_snapshot_load()
        _WARMING["on"] = loaded > 0  # cleared by the warmer after its first full pass
        _log(f"cache snapshot: loaded {loaded} entries")
        _threading.Thread(
            target=_warm_caches, kwargs={"forever": True}, daemon=True, name="cache-warmer"
        ).start()
        print(f"dashboard: http://127.0.0.1:{options.port}/  (Ctrl-C to stop)")
        print(f"dashboard: session token (for direct API calls): {_DASHBOARD_TOKEN}")
        server.serve_forever()
    except OSError as exc:
        # 10013 (WSAEACCES) is what Windows returns when the holder bound the port exclusively.
        if exc.errno in (48, 10048) or getattr(exc, "winerror", None) in (10048, 10013):
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
