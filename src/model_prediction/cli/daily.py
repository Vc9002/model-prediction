"""Daily pipeline command group.

Mechanical extraction from the former cli.py monolith (DD-6 split, stage
6). The daily orchestrator's entire branch moves verbatim: nested helper
functions stay nested, nonlocal result variables stay nonlocal, all seven
ThreadPoolExecutor pools and every function-local import move intact.
The logger is defined HERE for the same BLE001-exemption reason as the
other cli modules.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any

from ..college_basketball_forward import forecast_ncaab_sport
from ..config import PROJECT_ROOT, ledger_path, polymarket_edge_enabled
from ..daily_lock import acquire_lock, release_lock
from ..data_sources.espn_probables import capture_probable_starter_snapshot
from ..data_sources.espn_wnba_injuries import capture_espn_event_injuries
from ..data_sources.mlb_injuries import (
    capture_roster_snapshot,
    capture_transactions_snapshot,
    team_id_for_name,
)
from ..data_sources.mlb_lineups import capture_and_store as capture_lineups_and_store
from ..data_sources.wnba_injuries import capture_latest_report
from ..domain import LEARNED_PRODUCTION_SPORTS, utc_now
from ..esports import forecast_esports_slate
from ..euroleague_forward import forecast_euroleague_sport
from ..ledger import defer_sqlite_xlsx_exports
from ..main_ledgers import MultiSportPickLedger
from ..mlb_baseline_refresh import refresh_if_due
from ..nbl_forward import forecast_nbl_sport
from ..research_ledgers import existing_research_ledgers
from ..runtime_paths import RuntimePaths
from .commands import (
    _clear_today_open,
    _polymarket_slate,
    _research_models_dir,
    _summary,
)
from .forecast import (
    _forecast_cfb_sport,
    _forecast_darts_sport,
    _forecast_hockey_sport,
    _forecast_international_sport,
    _forecast_learned_sport,
    _forecast_mlb_nrfi_flat,
    _forecast_mlb_totals_flat,
    _forecast_nba_spread_sport,
    _forecast_nba_total_sport,
    _forecast_nfl_spread_sport,
    _forecast_nfl_total_sport,
    _forecast_soccer_sport,
    _forecast_tennis_sport,
    _forecast_wnba_spread_sport,
    _forecast_wnba_total_sport,
    _log_esports_forecast,
    _refresh_esports_ratings,
    _refresh_international_baseball_ratings,
)
from .settle import _settle_all_unsettled
from .state import (
    DAILY_ADDITIONAL_SPORTS,
    DAILY_INTERNATIONAL_BASEBALL_SPORTS,
    DAILY_LEARNED_SPORTS,
    ESPORTS_TITLES,
)

logger = logging.getLogger("model_prediction.cli")


class DailyIntegrityError(RuntimeError):
    """Raised after fail-soft work completes when a material substep failed."""

    def __init__(self, report: dict[str, Any]) -> None:
        self.report = report
        errors = "; ".join(report.get("material_errors", []))
        super().__init__(f"daily completed with material substep errors: {errors}")


def _polymarket_order_payload(order: Any) -> dict[str, Any]:
    """Translate the actual PolymarketOrderDecision contract for the ledger."""
    return {
        "market_id": order.market_id,
        "question": order.question,
        "side": order.side,
        "target_selection": order.target_selection,
        "target_side": order.target_side,
        "selection_label": order.selection_label,
        "home_team": order.home_team,
        "away_team": order.away_team,
        "order_price": order.order_price,
        "model_probability": order.model_probability,
        "market_price": order.market_price,
        "edge": order.edge,
        "ev_pct": order.expected_value_pct,
        "stake_units": order.stake_units,
        "is_maker": order.is_maker,
        "reason": order.reason,
        "event_start_utc": order.event_start_utc,
        "observed_at_utc": order.observed_at_utc,
    }


def _contains_error_status(value: Any) -> bool:
    if isinstance(value, dict):
        if str(value.get("status") or "").casefold() in {"error", "failed"}:
            return True
        return any(_contains_error_status(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_error_status(item) for item in value)
    return False


def _material_daily_errors(report: dict[str, Any]) -> list[str]:
    """Return errors that must make the unattended daily run non-green.

    Capture/enrichment tasks remain fail-soft. Forecast, settlement, and
    automated edge-ledger failures are material because silently losing any
    of those steps produces incomplete decision or grading state.
    """
    errors: list[str] = []
    forecasts = report.get("step2_3_forecast_and_log") or {}
    if isinstance(forecasts, dict):
        for workflow, result in forecasts.items():
            if _contains_error_status(result):
                errors.append(f"forecast:{workflow}")
    for step in (
        "step4_settlement",
        "step7_flat_settlement",
        "step8_research_settlement",
        "step9_gated_research_settlement",
        "step10_polymarket_edge_record",
        "step11_polymarket_edge_settle",
        "ledger_coverage",
    ):
        if _contains_error_status(report.get(step)):
            errors.append(step)
    return errors


def _finalize_daily_report(report: dict[str, Any]) -> dict[str, Any]:
    material_errors = _material_daily_errors(report)
    report["status"] = "error" if material_errors else "ok"
    report["material_errors"] = material_errors
    if material_errors:
        logger.error("Daily integrity failure after fail-soft completion: %s", material_errors)
        raise DailyIntegrityError(report)
    return report


def _skip_auto_buyer_for_verification() -> bool:
    """Return whether this invocation explicitly suppresses live purchases.

    The normal scheduled environment does not set this variable, so production
    behavior is unchanged.  It exists for full pipeline verification runs that
    must exercise forecasting and settlement without creating a second set of
    real-money orders.
    """
    return os.environ.get("MODEL_PREDICTION_SKIP_AUTO_BUYER") == "1"


def _auto_buyer_is_decoupled() -> bool:
    """Whether the independent short-cycle worker owns purchase evaluation."""
    return os.environ.get("MODEL_PREDICTION_AUTO_BUYER_DECOUPLED") == "1"


def _with_timed_exports(function):
    """Include deferred workbook rebuilding in the reported daily duration."""

    @wraps(function)
    def wrapped(*args, **kwargs):
        started = time.monotonic()
        with defer_sqlite_xlsx_exports():
            report = function(*args, **kwargs)
            export_started = time.monotonic()
        finished = time.monotonic()
        timing = report.setdefault("timing", {})
        timing["xlsx_export_seconds"] = round(finished - export_started, 1)
        timing["total_seconds"] = round(finished - started, 1)
        return report

    return wrapped


def _with_process_lock(function):
    """Ensure a non-blocking process lock on daily.lock during execution."""

    @wraps(function)
    def wrapped(*args, **kwargs):
        # When run_daily.ps1/sh wraps execution, the outer process already owns daily.lock
        if os.environ.get("MODEL_PREDICTION_DAILY_LOCK_HELD") == "1":
            return function(*args, **kwargs)

        data_root = kwargs.get("data_root")
        if data_root is None and len(args) > 6:
            data_root = args[6]

        if data_root is not None and str(data_root) not in {"data", "."}:
            lock_path = Path(data_root) / "daily_pipeline.lock"
        else:
            paths = RuntimePaths.resolve(require_external_runtime=False)
            lock_path = paths.daily_lock
        lock_handle = acquire_lock(lock_path)
        if lock_handle is None:
            logger.warning(
                "Daily pipeline lock %s is already held by another process; aborting concurrent run.",
                lock_path,
            )
            return {
                "status": "aborted",
                "reason": "concurrent_daily_process_locked",
                "lock_path": str(lock_path),
            }
        try:
            return function(*args, **kwargs)
        finally:
            release_lock(lock_handle)

    return wrapped


@_with_process_lock
@_with_timed_exports
def run_daily(args, config, registry, bans, ledger, audit, data_root) -> dict:
    # Wall-clock markers for the daily-run timing report (2026-08-26 perf
    # instrumentation — additive only, no behavior change; the report dict
    # carries per-phase elapsed seconds so slow phases are visible in the
    # daily log without a profiler).
    _t_start = time.monotonic()
    _t_pool_end = _t_start
    _t_forecast_end = _t_start
    _t_settle_end = _t_start
    # Self-throttled to weekly (see mlb_baseline_refresh's module
    # docstring) -- safe to call every daily cron cycle since it's a
    # cheap no-op most of the time, but keeps park factors and league
    # rates from silently drifting stale for months between manual runs.
    try:
        mlb_baseline_refresh_result = refresh_if_due(data_root, PROJECT_ROOT)
    except (OSError, ValueError):
        logger.warning("MLB baseline refresh failed", exc_info=True)
        mlb_baseline_refresh_result = {"status": "error"}
    slate_args = argparse.Namespace(
        provider="polymarket",
        league=None,
        all=True,
        sport=None,
        date=args.date,
        timezone="America/New_York",
        no_snapshot_bbo=False,
    )
    # Run slate/BBO capture, WNBA availability, priors, soccer scores,
    # and MLB probables concurrently. These are independent I/O tasks.
    from ..data_sources.api_football import collect_soccer_scores

    wnba_priors_result = {"status": "skipped"}
    mlb_probables_result: dict[str, Any] = {}
    soccer_collection = {}
    bet_better_result: dict[str, Any] = {}

    def _capture_wnba():
        try:
            from ..data_sources.espn import ESPNClient

            wnba_scoreboard = ESPNClient().scoreboard("WNBA", args.date)
            wnba_event_ids = [str(event["id"]) for event in wnba_scoreboard.get("events", [])]
            if wnba_event_ids:
                capture_latest_report(data_root, observed_at=utc_now())
                for event_id in wnba_event_ids:
                    try:
                        capture_espn_event_injuries(
                            data_root,
                            event_id=event_id,
                            client=ESPNClient(),
                            observed_at=utc_now(),
                        )
                    except Exception:
                        logger.warning(
                            "WNBA per-event injury capture failed for event %s",
                            event_id,
                            exc_info=True,
                        )
        except Exception:
            logger.warning("WNBA injury report capture failed for %s", args.date, exc_info=True)

    def _build_priors():
        nonlocal wnba_priors_result
        try:
            from ..data_sources.espn import ESPNClient
            from ..features.base import FeatureStore
            from ..wnba_availability_evaluation import build_and_save_priors

            wnba_priors_result = build_and_save_priors(
                store=FeatureStore(data_root),
                client=ESPNClient(),
                game_date=args.date,
                data_root=data_root,
            )
        except Exception:
            logger.warning("WNBA availability prior build failed for %s", args.date, exc_info=True)

    def _collect_soccer():
        nonlocal soccer_collection
        try:
            # data_root must be passed explicitly: every sibling capture in
            # this file writes to the caller's data_root (the scheduler-
            # resolved root under launchd), and collect_soccer_scores's
            # default is PROJECT_ROOT/data -- under launchd env vars those
            # two diverge, silently splitting soccer captures into the repo
            # checkout. Fixed 2026-08-26 before the API-FOOTBALL key ever
            # landed, so no data was written to the wrong tree.
            soccer_collection = collect_soccer_scores(data_root=data_root, days_from=3)
            if soccer_collection.get("status") == "no_api_key":
                logger.info("missing API_FOOTBALL_KEY — falling back to keyless ESPN soccer ingest")
                from datetime import date as dt_date
                from datetime import timedelta as dt_timedelta

                import httpx

                from ..ingest import Ingestor

                ingestor = Ingestor(data_root, rate_limit_seconds=0.0)
                current_date = dt_date.fromisoformat(args.date)
                dates = [
                    (current_date - dt_timedelta(days=offset)).isoformat() for offset in range(3, -1, -1)
                ]

                def _ingest_date(d_str: str) -> int:
                    try:
                        res = ingestor.ingest_scores("soccer", d_str)
                        return int(res.get("new_games_appended", 0))
                    except (OSError, ValueError, httpx.HTTPError) as e:
                        logger.warning("ESPN soccer ingest failed for %s: %s", d_str, e)
                        return 0

                with ThreadPoolExecutor(max_workers=min(4, len(dates))) as pool:
                    appended_counts = list(pool.map(_ingest_date, dates))
                appended_total = sum(appended_counts)
                soccer_collection = {
                    "status": "espn_fallback",
                    "source": "espn",
                    "new_games_appended": appended_total,
                }
        except Exception:
            logger.warning("Soccer score collection failed", exc_info=True)

    def _collect_bet_better():
        # Research-only cross-check capture (see data_sources/bet_better.py):
        # keyless, fail-soft, snapshots under data/providers/bet_better/ --
        # never written to any ledger and never consumed by a live decision.
        nonlocal bet_better_result
        try:
            from ..data_sources.bet_better import collect_bet_better_models

            bet_better_result = collect_bet_better_models(data_root=data_root)
        except Exception:
            logger.warning("Bet Better model-feed capture failed", exc_info=True)
            bet_better_result = {"status": "error"}

    nfl_injuries_result: dict[str, Any] = {"status": "skipped"}

    def _capture_nfl_injuries():
        nonlocal nfl_injuries_result
        try:
            from ..data_sources.espn_nfl_injuries import capture_espn_nfl_injuries

            nfl_injuries_result = capture_espn_nfl_injuries(data_root)
        except Exception:
            logger.warning("NFL injury report capture failed for %s", args.date, exc_info=True)
            nfl_injuries_result = {"status": "error"}

    nba_injuries_result: dict[str, Any] = {"status": "skipped"}

    def _capture_nba_injuries():
        nonlocal nba_injuries_result
        try:
            from ..data_sources.espn_nba_injuries import capture_espn_nba_injuries

            nba_injuries_result = capture_espn_nba_injuries(data_root)
        except Exception:
            logger.warning("NBA injury report capture failed for %s", args.date, exc_info=True)
            nba_injuries_result = {"status": "error"}

    nhl_injuries_result: dict[str, Any] = {"status": "skipped"}

    def _capture_nhl_injuries():
        nonlocal nhl_injuries_result
        try:
            from ..data_sources.espn_nhl_injuries import capture_espn_nhl_injuries

            nhl_injuries_result = capture_espn_nhl_injuries(data_root)
        except Exception:
            logger.warning("NHL injury report capture failed for %s", args.date, exc_info=True)
            nhl_injuries_result = {"status": "error"}

    cfb_injuries_result: dict[str, Any] = {"status": "skipped"}

    def _capture_cfb_injuries():
        nonlocal cfb_injuries_result
        try:
            from ..data_sources.espn_cfb_injuries import capture_espn_cfb_injuries

            cfb_injuries_result = capture_espn_cfb_injuries(data_root)
        except Exception:
            logger.warning("CFB injury report capture failed for %s", args.date, exc_info=True)
            cfb_injuries_result = {"status": "error"}

    cfb_weather_result: dict[str, Any] = {"status": "skipped"}

    def _capture_cfb_weather():
        nonlocal cfb_weather_result
        try:
            from ..features.multi_sport_weather import capture_cfb_pregame_weather

            cfb_weather_result = capture_cfb_pregame_weather(data_root, args.date)
        except Exception:
            logger.warning("CFB pregame weather capture failed for %s", args.date, exc_info=True)
            cfb_weather_result = {"status": "error"}

    def _capture_mlb_probables():
        nonlocal mlb_probables_result
        try:
            mlb_probables_result = capture_probable_starter_snapshot(
                args.date,
            )
        except (OSError, TypeError, ValueError):
            logger.warning(
                "MLB probable-starter capture failed for %s",
                args.date,
                exc_info=True,
            )

    mlb_lineups_result: dict[str, Any] = {"status": "skipped"}

    def _capture_mlb_lineups():
        # Lineups cannot be backfilled -- a completed boxscore says
        # who played, never what was announced pregame. So this
        # capture is fail-soft (never blocks the daily run) but its
        # misses are permanent, unlike every other step here.
        nonlocal mlb_lineups_result
        try:
            mlb_lineups_result = capture_lineups_and_store(args.date)
        except Exception:
            # Matches every sibling capture in this pool. A narrow
            # tuple here would let one malformed schedule row (e.g.
            # a missing gamePk raising KeyError) escape the future
            # and abort the ENTIRE daily run -- settlement and every
            # other sport included. Found by code review 2026-08-19.
            logger.warning("MLB lineup capture failed for %s", args.date, exc_info=True)
            mlb_lineups_result = {"status": "error"}

    mlb_availability_result: dict[str, Any] = {"status": "skipped"}

    def _capture_mlb_availability():
        # Shadow feature (features/mlb_player_availability.py) --
        # only captures raw roster/transaction data here; per-matchup
        # feature computation happens lazily, only if a future
        # artifact requests these feature names.
        nonlocal mlb_availability_result
        try:
            from ..data_sources.espn import ESPNClient

            mlb_scoreboard = ESPNClient().scoreboard("MLB", args.date)
            team_ids: set[int] = set()
            for event in mlb_scoreboard.get("events", []):
                competitors = event.get("competitions", [{}])[0].get("competitors", [])
                for competitor in competitors:
                    name = (competitor.get("team") or {}).get("displayName", "")
                    try:
                        team_ids.add(team_id_for_name(name))
                    except ValueError:
                        continue
            if not team_ids:
                mlb_availability_result = {"status": "no_games", "date": args.date}
                return
            now = utc_now()
            capture_roster_snapshot(data_root, sorted(team_ids), observed_at=now)
            # 60-day lookback covers even a 60-Day IL stint's full
            # placement-to-activation span; the end_date deliberately
            # extends through today's real date (not just args.date)
            # so one capture can serve any past cutoff_date's
            # point-in-time reconstruction, not only today's slate.
            transactions_start = (now.date() - timedelta(days=60)).isoformat()
            transactions_end = now.date().isoformat()
            txn_snapshot = capture_transactions_snapshot(
                data_root,
                sorted(team_ids),
                transactions_start,
                transactions_end,
                observed_at=now,
            )
            mlb_availability_result = {
                "status": "captured",
                "team_count": len(team_ids),
                "transaction_entries": len(txn_snapshot.get("entries", [])),
            }
        except Exception:
            logger.warning("MLB availability capture failed for %s", args.date, exc_info=True)
            mlb_availability_result = {"status": "error"}

    mlb_starter_snapshot_result: dict[str, Any] = {"status": "skipped"}

    def _capture_mlb_starter_snapshots():
        # Keeps data/mlb_statsapi/game_snapshots.jsonl current --
        # features/starter_history.py's live starter_era_gap provider
        # (added 2026-08-04) reads real per-starter innings/earned-runs
        # history from this file. Before this capture step existed,
        # the file was a one-time static dump (last refreshed
        # 2026-07-20) that would have gone stale the instant a live
        # provider started depending on it -- same silent-staleness
        # bug class as the NPB destructive-overwrite incident.
        # 3-day lookback (not just yesterday) so one skipped/failed
        # run auto-heals on the next, matching the historical-game
        # ingestion step's own reasoning above.
        nonlocal mlb_starter_snapshot_result
        try:
            from ..data_sources.mlb_statsapi import MLBStatsAPIClient, collect_game_snapshots

            lookback_start = (datetime.fromisoformat(args.date).date() - timedelta(days=3)).isoformat()
            result = collect_game_snapshots(
                MLBStatsAPIClient(),
                lookback_start,
                args.date,
                data_root / "mlb_statsapi" / "game_snapshots.jsonl",
                progress_every=0,
            )
            mlb_starter_snapshot_result = {
                "status": "captured",
                "scheduled": result.scheduled,
                "written": result.written,
                "skipped": len(result.skipped),
            }
        except Exception:
            logger.warning("MLB starter snapshot capture failed for %s", args.date, exc_info=True)
            mlb_starter_snapshot_result = {"status": "error"}

    capture_task_seconds: dict[str, float] = {}

    def _timed(name, fn, *fn_args):
        started = time.monotonic()
        try:
            return fn(*fn_args)
        finally:
            capture_task_seconds[name] = round(time.monotonic() - started, 1)

    with ThreadPoolExecutor(max_workers=16) as io_pool:
        f0 = io_pool.submit(_timed, "polymarket_slate", _polymarket_slate, slate_args, config)
        f1 = io_pool.submit(_timed, "wnba", _capture_wnba)
        f2 = io_pool.submit(_timed, "priors", _build_priors)
        f3 = io_pool.submit(_timed, "soccer", _collect_soccer)
        f4 = io_pool.submit(_timed, "mlb_probables", _capture_mlb_probables)
        f5 = io_pool.submit(_timed, "mlb_availability", _capture_mlb_availability)
        f6 = io_pool.submit(_timed, "mlb_starter_snapshots", _capture_mlb_starter_snapshots)
        f7 = io_pool.submit(_timed, "mlb_lineups", _capture_mlb_lineups)
        f8 = io_pool.submit(_timed, "bet_better", _collect_bet_better)
        f9 = io_pool.submit(_timed, "nfl_injuries", _capture_nfl_injuries)
        f10 = io_pool.submit(_timed, "nba_injuries", _capture_nba_injuries)
        f11 = io_pool.submit(_timed, "nhl_injuries", _capture_nhl_injuries)
        f12 = io_pool.submit(_timed, "cfb_injuries", _capture_cfb_injuries)
        f13 = io_pool.submit(_timed, "cfb_weather", _capture_cfb_weather)
        for f in (f1, f2, f3, f4, f5, f6, f7, f8, f9, f10, f11, f12, f13):
            f.result()  # Wait for all, surface exceptions
        try:
            slate = f0.result()
        except Exception:
            # Real bug fixed 2026-08-02: this used to be an unhandled
            # f0.result() -- a transient Polymarket network error here
            # crashed the *entire* daily job (MLB, WNBA, NBA, NFL,
            # soccer, esports, KBO, NPB, tennis, none of it ran) even
            # though every per-sport forecast below fetches its own
            # market data independently and doesn't actually need this
            # step's result. This capture is a prospective BBO/event
            # snapshot for reporting, not a hard prerequisite for the
            # forecasts that follow -- log loudly and degrade instead
            # of taking down work that would have otherwise succeeded.
            logger.exception("Polymarket slate/BBO capture failed for %s", args.date)
            slate = {
                "status": "error",
                "event_count": 0,
                "events_by_league": {},
                "prospective_bbo_capture": {},
            }
    _t_pool_end = time.monotonic()

    # Statcast aggregates consume game_snapshots.jsonl, which the MLB
    # capture steps above write inside the pool -- so this rebuild must run
    # after the pool, never inside it (a race here would silently drop the
    # run's newest snapshots). Fail-soft: aggregate freshness is a research
    # input, never a blocker for the daily run.
    statcast_aggregates_result: dict[str, Any] = {"status": "skipped"}
    try:
        from ..statcast_aggregates import build_statcast_game_aggregates

        pitcher_frame, batter_frame = build_statcast_game_aggregates(data_root)
        statcast_aggregates_result = {
            "status": "ok",
            "pitcher_rows": len(pitcher_frame),
            "batter_rows": len(batter_frame),
        }
    except Exception:
        logger.warning("Statcast aggregate rebuild failed", exc_info=True)
        statcast_aggregates_result = {"status": "error"}

    cleared_ledgers: list[Any] = [ledger]
    _clear_today_open(ledger, args.date, by_event_date=True)
    # Also clear and forecast for flat ledger
    flat_ledger = MultiSportPickLedger(data_root, flat=True)
    cleared_ledgers.append(flat_ledger)
    _clear_today_open(flat_ledger, args.date, by_event_date=True)
    max_data_age = float(config["project"].get("maximum_data_age_hours", 12))
    max_disagreement = float(config["project"].get("maximum_unreviewed_market_disagreement", 0.10))
    # A single decision timestamp makes the learned-slate cache reusable
    # when the same candidates are re-logged to the flat ledger. Before
    # this, two calls a few milliseconds apart rebuilt every feature
    # and repeated upstream reads for no decision-quality benefit.
    forecast_observed_at = utc_now()
    data_directory = Path(ledger_path(config)).parent
    # Esports/KBO/NPB write to Main and Flat since the 2026-10-22 ledger merge, so the
    # Main/Flat clears above already cover them; the legacy research/gated ledgers
    # are no longer written and are not cleared.
    # Compute forecasts once, log to both ledgers (compute > log main > log research > log flat)
    forecast_result = {}
    workers = min(len(DAILY_LEARNED_SPORTS), 5)
    # Seconds from pool start to each sport's completion (a queued sport's
    # figure includes its wait for a free worker).
    forecast_sport_done_seconds: dict[str, float] = {}
    _forecast_pool_started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for sport in DAILY_LEARNED_SPORTS:
            futures[
                pool.submit(
                    _forecast_learned_sport,
                    sport,
                    args.date,
                    True,
                    config,
                    registry,
                    bans,
                    ledger,
                    maximum_data_age_hours=max_data_age,
                    maximum_unreviewed_disagreement=max_disagreement,
                    research_ledger=None,
                    gated_ledger=None,
                    observed_at=forecast_observed_at,
                )
            ] = sport
        for future in as_completed(futures):
            sport = futures[future]
            forecast_sport_done_seconds[sport] = round(time.monotonic() - _forecast_pool_started, 1)
            try:
                forecast_result[sport] = future.result()
            except Exception as exc:  # noqa: BLE001 — a failed sport must degrade to an error dict, never take down the whole forecast
                forecast_result[sport] = {
                    "sport": sport,
                    "status": "error",
                    "reason": str(exc),
                    "logged": 0,
                    "candidates": [],
                }
    # Re-log computed candidates to flat ledger (flat mode, no edge gate)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        flat_futures = {}
        for sport in DAILY_LEARNED_SPORTS:
            result = forecast_result.get(sport, {})
            candidates = result.get("candidates", [])
            if candidates:
                flat_futures[
                    pool.submit(
                        _forecast_learned_sport,
                        sport,
                        args.date,
                        True,
                        config,
                        registry,
                        bans,
                        flat_ledger,
                        maximum_data_age_hours=max_data_age,
                        maximum_unreviewed_disagreement=max_disagreement,
                        flat_mode=True,
                        exposure_ledger=ledger,
                        observed_at=forecast_observed_at,
                    )
                ] = sport
        for future in as_completed(flat_futures):
            sport = flat_futures[future]
            try:
                forecast_result[f"_flat_{sport}"] = future.result()
            except Exception:
                logger.warning("Flat forecast failed for sport %s", sport, exc_info=True)
                forecast_result[f"_flat_{sport}"] = {
                    "sport": sport,
                    "status": "error",
                    "reason": "flat forecast failed",
                    "logged": 0,
                }

    # MLB totals, soccer, tennis, esports, and international baseball
    # are independent of each other and of the four learned sports
    # above (already logged by the pool before this point) -- they
    # used to run one after another and were the dominant share of
    # daily's wall-clock time (soccer alone fans out to 18 ESPN
    # leagues, tennis reads the largest historical dataset in the
    # project). Run them concurrently instead. This is safe to do
    # because every one of these already guards its own ledger
    # appends with the same in-process _LEDGER_LOCK the four learned
    # sports' pool above already relies on for concurrent writes to
    # the same ledger files (see _log_esports_forecast's lock
    # comment, mirrored in _forecast_soccer_sport/_forecast_tennis_sport/
    # _forecast_international_sport/_forecast_mlb_totals_flat).
    def _mlb_totals_task() -> None:
        try:
            forecast_result["mlb_totals"] = _forecast_mlb_totals_flat(
                args.date,
                True,
                config,
                registry,
                bans,
                flat_ledger,
                audit,
                main_ledger=ledger,
            )
        except Exception as exc:
            logger.warning("MLB totals flat forecast failed", exc_info=True)
            forecast_result["mlb_totals"] = {
                "sport": "mlb_totals",
                "status": "error",
                "reason": str(exc),
                "logged": 0,
            }

    def _mlb_nrfi_task() -> None:
        try:
            forecast_result["mlb_nrfi"] = _forecast_mlb_nrfi_flat(
                args.date,
                True,
                config,
                registry,
                bans,
                flat_ledger,
                audit,
                main_ledger=ledger,
                data_root=data_directory,
            )
        except Exception as exc:
            logger.warning("MLB NRFI flat forecast failed", exc_info=True)
            forecast_result["mlb_nrfi"] = {
                "sport": "mlb_nrfi",
                "status": "error",
                "reason": str(exc),
                "logged": 0,
            }

    def _soccer_task() -> None:
        # Soccer: Main+Flat only (operator directive 2026-08-03).
        # Previously uncaught here (a crash would have taken down
        # tennis/esports/international-baseball/settlement below it
        # too) -- now caught like its siblings so one failing block
        # can't wedge the concurrent pool's other independent tasks.
        try:
            forecast_result["soccer"] = _forecast_soccer_sport(
                data_root=data_directory,
                args_date=args.date,
                config=config,
                flat_ledger=flat_ledger,
                main_ledger=ledger,
            )
        except Exception as exc:
            logger.warning("Soccer forecast failed", exc_info=True)
            forecast_result["soccer"] = {
                "sport": "soccer",
                "status": "error",
                "reason": str(exc),
                "logged": 0,
            }
            return
        # Not a hard failure -- these are separate leagues in one daily
        # run, and a loud warning here (rather than an exception) is what
        # would have surfaced the KBO/NPB silent-zero-picks incident
        # immediately instead of it running unnoticed for months.
        _priced_soccer = forecast_result["soccer"].get("priced_contracts") or []
        if _priced_soccer and not forecast_result["soccer"].get("logged"):
            logger.warning(
                "zero rows logged for soccer despite %d priced contracts",
                len(_priced_soccer),
            )

    def _wnba_spread_task() -> None:
        # WNBA spread: Main+Flat, same routing as MLB spread/total
        # (operator directive 2026-08-03). Independent of WNBA
        # moneyline (already logged by the learned-sports pool above).
        try:
            forecast_result["wnba_spread"] = _forecast_wnba_spread_sport(
                data_root=data_directory,
                args_date=args.date,
                config=config,
                registry=registry,
                bans=bans,
                main_ledger=ledger,
                flat_ledger=flat_ledger,
            )
            _priced_wnba_spread = forecast_result["wnba_spread"].get("priced_contracts") or []
            if _priced_wnba_spread and not forecast_result["wnba_spread"].get("logged"):
                logger.warning(
                    "zero rows logged for wnba_spread despite %d priced contracts",
                    len(_priced_wnba_spread),
                )
        except Exception as exc:
            logger.warning("WNBA spread forecast failed", exc_info=True)
            forecast_result["wnba_spread"] = {
                "sport": "wnba_spread",
                "status": "error",
                "reason": str(exc),
                "logged": 0,
            }

    def _wnba_total_task() -> None:
        try:
            forecast_result["wnba_total"] = _forecast_wnba_total_sport(
                data_root=data_directory,
                args_date=args.date,
                config=config,
                registry=registry,
                bans=bans,
                main_ledger=ledger,
                flat_ledger=flat_ledger,
            )
            _priced_wnba_total = forecast_result["wnba_total"].get("priced_contracts") or []
            if _priced_wnba_total and not forecast_result["wnba_total"].get("logged"):
                logger.warning(
                    "zero rows logged for wnba_total despite %d priced contracts",
                    len(_priced_wnba_total),
                )
        except Exception as exc:
            logger.warning("WNBA total forecast failed", exc_info=True)
            forecast_result["wnba_total"] = {
                "sport": "wnba_total",
                "status": "error",
                "reason": str(exc),
                "logged": 0,
            }

    def _nba_spread_task() -> None:
        try:
            forecast_result["nba_spread"] = _forecast_nba_spread_sport(
                data_root=data_directory,
                args_date=args.date,
                config=config,
                registry=registry,
                bans=bans,
                main_ledger=ledger,
                flat_ledger=flat_ledger,
            )
            _priced_nba_spread = forecast_result["nba_spread"].get("priced_contracts") or []
            if _priced_nba_spread and not forecast_result["nba_spread"].get("logged"):
                logger.warning(
                    "zero rows logged for nba_spread despite %d priced contracts",
                    len(_priced_nba_spread),
                )
        except Exception as exc:
            logger.warning("NBA spread forecast failed", exc_info=True)
            forecast_result["nba_spread"] = {
                "sport": "nba_spread",
                "status": "error",
                "reason": str(exc),
                "logged": 0,
            }

    def _nba_total_task() -> None:
        try:
            forecast_result["nba_total"] = _forecast_nba_total_sport(
                data_root=data_directory,
                args_date=args.date,
                config=config,
                registry=registry,
                bans=bans,
                main_ledger=ledger,
                flat_ledger=flat_ledger,
            )
            _priced_nba_total = forecast_result["nba_total"].get("priced_contracts") or []
            if _priced_nba_total and not forecast_result["nba_total"].get("logged"):
                logger.warning(
                    "zero rows logged for nba_total despite %d priced contracts",
                    len(_priced_nba_total),
                )
        except Exception as exc:
            logger.warning("NBA total forecast failed", exc_info=True)
            forecast_result["nba_total"] = {
                "sport": "nba_total",
                "status": "error",
                "reason": str(exc),
                "logged": 0,
            }

    def _nfl_spread_task() -> None:
        try:
            forecast_result["nfl_spread"] = _forecast_nfl_spread_sport(
                data_root=data_directory,
                args_date=args.date,
                config=config,
                registry=registry,
                bans=bans,
                main_ledger=ledger,
                flat_ledger=flat_ledger,
                observed_at=forecast_observed_at,
            )
            _priced_nfl_spread = forecast_result["nfl_spread"].get("priced_contracts") or []
            if _priced_nfl_spread and not forecast_result["nfl_spread"].get("logged"):
                logger.warning(
                    "zero rows logged for nfl_spread despite %d priced contracts; skipped=%s",
                    len(_priced_nfl_spread),
                    forecast_result["nfl_spread"].get("skipped"),
                )
        except Exception as exc:
            logger.warning("NFL spread forecast failed", exc_info=True)
            forecast_result["nfl_spread"] = {
                "sport": "nfl_spread",
                "status": "error",
                "reason": str(exc),
                "logged": 0,
            }

    def _nfl_total_task() -> None:
        try:
            forecast_result["nfl_total"] = _forecast_nfl_total_sport(
                data_root=data_directory,
                args_date=args.date,
                config=config,
                registry=registry,
                bans=bans,
                main_ledger=ledger,
                flat_ledger=flat_ledger,
                observed_at=forecast_observed_at,
            )
            _priced_nfl_total = forecast_result["nfl_total"].get("priced_contracts") or []
            if _priced_nfl_total and not forecast_result["nfl_total"].get("logged"):
                logger.warning(
                    "zero rows logged for nfl_total despite %d priced contracts; skipped=%s",
                    len(_priced_nfl_total),
                    forecast_result["nfl_total"].get("skipped"),
                )
        except Exception as exc:
            logger.warning("NFL total forecast failed", exc_info=True)
            forecast_result["nfl_total"] = {
                "sport": "nfl_total",
                "status": "error",
                "reason": str(exc),
                "logged": 0,
            }

    def _tennis_task() -> None:
        try:
            forecast_result["tennis"] = _forecast_tennis_sport(
                data_root=data_directory,
                args_date=args.date,
                config=config,
                main_ledger=ledger,
                flat_ledger=flat_ledger,
            )
            _priced_tennis = forecast_result["tennis"].get("priced_contracts") or []
            if _priced_tennis and not forecast_result["tennis"].get("logged"):
                logger.warning(
                    "zero rows logged for tennis despite %d priced contracts",
                    len(_priced_tennis),
                )
        except Exception as exc:
            logger.warning("Tennis forecast failed", exc_info=True)
            forecast_result["tennis"] = {
                "sport": "tennis",
                "status": "error",
                "reason": str(exc),
                "logged": 0,
            }

    def _cfb_task() -> None:
        try:
            forecast_result["ncaaf"] = _forecast_cfb_sport(
                data_root=data_directory,
                args_date=args.date,
                config=config,
                main_ledger=ledger,
                flat_ledger=flat_ledger,
            )
            _priced_cfb = forecast_result["ncaaf"].get("priced_contracts") or []
            if _priced_cfb and not forecast_result["ncaaf"].get("logged"):
                logger.warning(
                    "zero rows logged for ncaaf despite %d priced contracts",
                    len(_priced_cfb),
                )
        except Exception as exc:
            logger.warning("NCAAF forecast failed", exc_info=True)
            forecast_result["ncaaf"] = {
                "sport": "ncaaf",
                "status": "error",
                "reason": str(exc),
                "logged": 0,
            }

    def _esports_title_task(title: str) -> None:
        forecast_result[title] = forecast_esports_slate(
            data_root=data_directory,
            artifact_dir=_research_models_dir(),
            title=title,
            game_date=args.date,
        )
        # Merged 2026-10-22: every candidate goes to Flat and the gated CALL subset to
        # Main (the old research / gated-research ledgers are no longer written).
        _esports_logged = _log_esports_forecast(
            forecast_result[title],
            config,
            flat_ledger,
            flat_mode=False,
            gated_ledger=ledger,
        )
        _priced_esports = forecast_result[title].get("priced_contracts") or []
        if _priced_esports and not _esports_logged:
            logger.warning(
                "zero rows logged for %s despite %d priced contracts",
                title,
                len(_priced_esports),
            )

    def _esports_block() -> None:
        try:
            forecast_result["_esports_ratings_refresh"] = _refresh_esports_ratings(data_directory)
        except Exception:
            logger.warning("Esports ratings refresh failed", exc_info=True)
        # Titles are independent of each other once ratings are
        # refreshed above -- fan them out too instead of one at a time.
        with ThreadPoolExecutor(max_workers=len(ESPORTS_TITLES)) as title_pool:
            title_futures = {title_pool.submit(_esports_title_task, title): title for title in ESPORTS_TITLES}
            for future in as_completed(title_futures):
                title = title_futures[future]
                try:
                    future.result()
                except Exception:
                    logger.warning("Esports forecast failed for title %s", title, exc_info=True)
                    forecast_result[title] = {
                        "sport": title,
                        "status": "error",
                        "reason": "esports forecast failed",
                        "logged": 0,
                    }

    def _intl_baseball_league_task(league: str) -> None:
        # Merged 2026-10-22: Flat gets every candidate, Main the gated CALL subset.
        forecast_result[league] = _forecast_international_sport(
            data_root=data_directory,
            artifact_dir=_research_models_dir(),
            league=league,
            args_date=args.date,
            config=config,
            research_ledger=flat_ledger,
            gated_ledger=ledger,
        )
        _priced_intl = forecast_result[league].get("priced_contracts") or []
        if _priced_intl and not forecast_result[league].get("logged"):
            logger.warning(
                "zero rows logged for %s despite %d priced contracts",
                league,
                len(_priced_intl),
            )

    def _intl_baseball_block() -> None:
        try:
            forecast_result["_international_baseball_ratings_refresh"] = (
                _refresh_international_baseball_ratings(data_directory)
            )
        except Exception:
            logger.warning("International baseball ratings refresh failed", exc_info=True)
        # International baseball — logged to research/gated/flat ledgers
        with ThreadPoolExecutor(max_workers=len(DAILY_INTERNATIONAL_BASEBALL_SPORTS)) as league_pool:
            league_futures = {
                league_pool.submit(_intl_baseball_league_task, league): league
                for league in DAILY_INTERNATIONAL_BASEBALL_SPORTS
            }
            for future in as_completed(league_futures):
                league = league_futures[future]
                try:
                    future.result()
                except Exception:
                    logger.warning(
                        "International baseball forecast failed for league %s",
                        league,
                        exc_info=True,
                    )
                    forecast_result[league] = {
                        "sport": league,
                        "status": "error",
                        "reason": "international baseball forecast failed",
                        "logged": 0,
                    }

    def _additional_sport_task(sport: str, forecast_function) -> None:
        try:
            if sport == "hockey":
                from ..hockey_history import refresh_hockey_history

                forecast_result["_hockey_history_refresh"] = refresh_hockey_history(data_directory, args.date)
            elif sport == "darts":
                from ..darts_history import refresh_darts_history

                forecast_result["_darts_history_refresh"] = refresh_darts_history(data_directory, args.date)
            result = forecast_function(
                data_root=data_directory,
                args_date=args.date,
                config=config,
                research_ledger=None,
                main_ledger=ledger,
                flat_ledger=flat_ledger,
            )
            if result.get("errors"):
                result["status"] = "error"
            forecast_result[sport] = result
        except Exception as exc:
            logger.warning("%s forecast failed", sport, exc_info=True)
            forecast_result[sport] = {"status": "error", "reason": str(exc), "logged": 0}

    with ThreadPoolExecutor(max_workers=16) as research_pool:
        research_futures_map = {
            research_pool.submit(_mlb_totals_task): "mlb_totals",
            research_pool.submit(_mlb_nrfi_task): "mlb_nrfi",
            research_pool.submit(_soccer_task): "soccer",
            research_pool.submit(_tennis_task): "tennis",
            research_pool.submit(_cfb_task): "cfb",
            research_pool.submit(_wnba_spread_task): "wnba_spread",
            research_pool.submit(_wnba_total_task): "wnba_total",
            research_pool.submit(_nba_spread_task): "nba_spread",
            research_pool.submit(_nba_total_task): "nba_total",
            research_pool.submit(_nfl_spread_task): "nfl_spread",
            research_pool.submit(_nfl_total_task): "nfl_total",
            research_pool.submit(_esports_block): "esports",
            research_pool.submit(_intl_baseball_block): "intl_baseball",
            **{
                research_pool.submit(_additional_sport_task, sport, forecast_function): sport
                for sport, forecast_function in zip(
                    DAILY_ADDITIONAL_SPORTS,
                    (
                        _forecast_hockey_sport,
                        _forecast_darts_sport,
                        forecast_ncaab_sport,
                        forecast_euroleague_sport,
                        forecast_nbl_sport,
                    ),
                    strict=True,
                )
            },
        }

        _research_pool_started = time.monotonic()
        for rf in as_completed(research_futures_map):
            name = research_futures_map[rf]
            forecast_sport_done_seconds[name] = round(time.monotonic() - _research_pool_started, 1)
            try:
                rf.result()
            except Exception:
                logger.exception(
                    "Forecasting task wrapper %s failed; rolling back open ledgers from pre-clear snapshots",
                    name,
                )
                from .commands import _rollback_pre_clear

                for cl in cleared_ledgers:
                    _rollback_pre_clear(cl)
                raise
    flat_result = {
        sport: forecast_result.get(f"_flat_{sport}", forecast_result.get(sport, {}))
        for sport in DAILY_LEARNED_SPORTS
    }
    for result in forecast_result.values():
        result.pop("candidates", None)
    # Read back stored Polymarket odds snapshots for per-sport summaries.
    # Tennis/KBO/NPB are captured and forecasted the same as every
    # other sport (see BBO_CAPTURE_SPORTS in polymarket_us.py) but
    # were missing from LEARNED_PRODUCTION_SPORTS, so this summary
    # silently never showed their snapshot counts even though their
    # capture and forecasting worked correctly the whole time.
    odds_by_sport = {}
    for sport in (*LEARNED_PRODUCTION_SPORTS, "tennis", "kbo", "npb"):
        odds_sport = "esports" if sport in ESPORTS_TITLES else sport
        snap_path = (
            Path(ledger_path(config)).parent / "odds" / odds_sport / args.date / "polymarket_snapshots.jsonl"
        )
        if snap_path.exists():
            snaps = [
                json.loads(line)
                for line in snap_path.read_text(encoding="utf-8").strip().split("\n")
                if line.strip()
            ]
            odds_by_sport[sport] = {
                "snapshots": len(snaps),
                "moneyline_snapshots": sum(1 for s in snaps if s.get("market_type") == "moneyline"),
                "spread_snapshots": sum(1 for s in snaps if s.get("market_type") == "spread"),
                "total_snapshots": sum(1 for s in snaps if s.get("market_type") == "total"),
                "with_executable_bbo": sum(
                    1
                    for s in snaps
                    if s.get("long", {}).get("ask") is not None and s.get("short", {}).get("ask") is not None
                ),
            }
    for result in flat_result.values():
        result.pop("candidates", None)
    _research_settlement: dict[str, Any]
    _gated_settlement: dict[str, Any]
    _t_forecast_end = time.monotonic()
    if args.skip_settlement:
        settlement = {"status": "skipped", "reason": "caller_settled_before daily"}
        flat_settlement = settlement
        _research_settlement = settlement
        _gated_settlement = settlement
    else:
        # A postponed/canceled game never becomes "completed" under its
        # original event_id -- ESPN issues a new event_id for any
        # reschedule -- so leaving void_postponed off here means an
        # affected pick sits "open" forever with no automatic path to
        # resolution; only a manually-run `settle --void-postponed`
        # would ever clear it. Auto-void in the unattended daily run.
        from ..data_sources.espn import ESPNClient

        settle_args = argparse.Namespace(
            all_unsettled=True,
            void_postponed=True,
        )
        espn_settle = ESPNClient()
        scoreboard_cache: dict[tuple[str, str], dict | Exception] = {}
        settlement = _settle_all_unsettled(
            settle_args,
            config,
            ledger,
            espn=espn_settle,
            scoreboard_cache=scoreboard_cache,
        )
        flat_settlement = _settle_all_unsettled(
            settle_args,
            config,
            flat_ledger,
            espn=espn_settle,
            scoreboard_cache=scoreboard_cache,
        )
        research_ledgers_list = list(existing_research_ledgers(data_directory))
        gated_ledgers_list = list(existing_research_ledgers(data_directory, gated=True))

        def _settle_ledger_entry(sport_ledger):
            return sport_ledger.path.stem, _settle_all_unsettled(
                settle_args,
                config,
                sport_ledger,
                espn=espn_settle,
                scoreboard_cache=scoreboard_cache,
            )

        with ThreadPoolExecutor(max_workers=min(8, max(1, len(research_ledgers_list)))) as pool:
            _research_settlement = dict(pool.map(_settle_ledger_entry, research_ledgers_list))

        with ThreadPoolExecutor(max_workers=min(8, max(1, len(gated_ledgers_list)))) as pool:
            _gated_settlement = dict(pool.map(_settle_ledger_entry, gated_ledgers_list))
    _t_settle_end = time.monotonic()
    # Per-step wall time of everything after settlement (~850s in the
    # 2026-10-24 07:30 run, with no breakdown of which step owns it).
    post_step_seconds: dict[str, float] = {}
    _lap_start = _t_settle_end

    def _lap(name: str) -> None:
        nonlocal _lap_start
        now = time.monotonic()
        post_step_seconds[name] = round(now - _lap_start, 1)
        _lap_start = now

    # Step 4b: Automated Model Calibration & Drift Monitor
    calibration_result: dict[str, Any] = {"status": "skipped"}
    if not args.skip_settlement:
        try:
            from ..calibration_pipeline import run_full_calibration_pipeline

            cal_report = run_full_calibration_pipeline(
                data_root=data_directory,
                min_sample=20,
                update_artifacts=True,
            )
            calibration_result = {
                "status": "ok",
                "system_status": cal_report.get("system_status"),
                "total_evaluated_models": cal_report.get("total_evaluated_models"),
                "max_calibration_drift": cal_report.get("max_calibration_drift"),
                "severe_drift_count": cal_report.get("severe_drift_count"),
                "moderate_drift_count": cal_report.get("moderate_drift_count"),
            }
        except Exception as exc:
            logger.warning("Automated model calibration failed", exc_info=True)
            calibration_result = {"status": "error", "reason": str(exc)}

    _lap("calibration_4b")

    # Step 10 & 11: Automated Polymarket CLOB Edge Scanner & Settlement
    poly_edge_record_result: dict[str, Any] = {"status": "skipped"}
    poly_edge_settle_result: dict[str, Any] = {"status": "skipped"}
    if polymarket_edge_enabled(config, "scanner"):
        try:
            from ..portfolio.polymarket_ledger import record_polymarket_orders
            from ..portfolio.polymarket_scanner import PolymarketSlateScanner

            scanner = PolymarketSlateScanner(min_edge=0.025, bankroll=1000.0)
            scan_res = scanner.scan_directory(
                base_dir=data_directory / "odds",
                require_model=True,
                pregame_only=True,
            )
            if scan_res.actionable_orders:
                orders_payload = [_polymarket_order_payload(order) for order in scan_res.actionable_orders]
                poly_edge_record_result = record_polymarket_orders(orders_payload, data_root=data_directory)
            else:
                poly_edge_record_result = {"status": "ok", "recorded_count": 0, "actionable_orders": 0}
        except Exception:
            logger.warning("Automated Polymarket edge scan/record failed", exc_info=True)
            poly_edge_record_result = {"status": "error"}
    else:
        poly_edge_record_result = {"status": "disabled", "reason": "operator_disabled"}

    if not polymarket_edge_enabled(config, "ledger"):
        poly_edge_settle_result = {"status": "disabled", "reason": "operator_disabled"}
    elif not args.skip_settlement:
        try:
            from ..portfolio.polymarket_ledger import settle_polymarket_ledger_rows

            poly_edge_settle_result = settle_polymarket_ledger_rows(data_root=data_directory)
        except Exception:
            logger.warning("Automated Polymarket edge settlement failed", exc_info=True)
            poly_edge_settle_result = {"status": "error"}
    elif poly_edge_record_result.get("status") == "error":
        # Keep the skipped settlement truthful when --skip-settlement was
        # explicit; the record failure remains independently material.
        poly_edge_settle_result = {"status": "skipped"}

    _lap("polymarket_edge_scan_settle")

    # Step 11b: Automated Auto-Buyer Settlement & Pre-Cycle Fallback Reconciliation
    #
    # run_auto_buyer_cycle() (step 12) performs its own pre-cycle reconcile +
    # settle pass immediately before evaluating picks, whenever the buyer is
    # actually going to run. Doing that same live-order reconciliation here
    # too -- with no state change in between -- doubles the live exchange
    # calls (order-status lookups, cancel-eligibility checks) for zero
    # benefit. So this step is the *only* reconciliation pass when the buyer
    # cycle won't run this invocation (disabled, or verification override),
    # and defers to step 12's internal pass otherwise.
    from ..portfolio.auto_executor import load_auto_buyer_state

    _auto_buyer_will_run = not _skip_auto_buyer_for_verification() and load_auto_buyer_state().get(
        "enabled", False
    )

    auto_buyer_settle_result: dict[str, Any] = (
        {"status": "deferred_to_cycle", "reason": "auto_buyer_cycle_runs_own_reconcile_and_settle"}
        if _auto_buyer_will_run
        else {"status": "skipped"}
    )
    if not args.skip_settlement and not _auto_buyer_will_run and not _skip_auto_buyer_for_verification():
        try:
            from ..portfolio.auto_buyer_ledger import (
                reconcile_pending_auto_buyer_fallbacks,
                settle_auto_buyer_ledger,
            )

            reconcile_pending_auto_buyer_fallbacks(data_directory)
            auto_buyer_settle_result = settle_auto_buyer_ledger(data_directory)
        except Exception:
            logger.warning("Automated Auto-Buyer settlement failed", exc_info=True)
            auto_buyer_settle_result = {"status": "error"}

    _lap("auto_buyer_settle_11b")

    # Step 12: Automated Polymarket Buyer (if enabled in dashboard state)
    auto_buyer_result: dict[str, Any]
    if _auto_buyer_is_decoupled():
        logger.info("Auto-Buyer purchase cycle delegated to independent worker")
        auto_buyer_result = {
            "status": "skipped",
            "reason": "independent_auto_buyer_worker",
        }
    elif _skip_auto_buyer_for_verification():
        logger.info("Auto-Buyer skipped by explicit verification override")
        auto_buyer_result = {
            "status": "skipped",
            "reason": "verification_override",
        }
    else:
        auto_buyer_result = {"status": "skipped"}
        try:
            from ..portfolio.auto_executor import run_auto_buyer_cycle

            auto_state = load_auto_buyer_state()
            if auto_state.get("enabled", False):
                logger.info(
                    "Auto-Buyer is ENABLED (mode=%s). Executing automated purchase cycle...",
                    auto_state.get("mode", "paper"),
                )
                # No execute_override here -- real order placement must be
                # gated by the persisted enabled+mode state, never forced,
                # so a scheduled run can never place real orders while the
                # dashboard is set to paper mode.
                buyer_run = run_auto_buyer_cycle(forecast_date=args.date)
                # Surface the full rejection breakdown, not just the headline
                # counts -- without this, `whitelisted_evaluated` looks like
                # "candidates the buyer had a real shot at" when it actually
                # includes every already-settled historical row still sitting
                # in the picks files for a whitelisted model. Investigating
                # "why did the buyer skip so much" is impossible after the
                # fact without these per-reason counts on record.
                auto_buyer_result = {
                    "status": buyer_run.get("status", "executed"),
                    "mode": buyer_run.get("mode"),
                    "orders_count": buyer_run.get("orders_count", 0),
                    "total_spend_usd": buyer_run.get("total_spend_usd", 0.0),
                    "total_evaluated": buyer_run.get("total_evaluated", 0),
                    "rejected_not_open": buyer_run.get("rejected_not_open", 0),
                    "whitelisted_evaluated": buyer_run.get("whitelisted_count", 0),
                    "rejected_not_whitelisted": buyer_run.get("rejected_not_whitelisted", 0),
                    "rejected_blacklist": buyer_run.get("rejected_blacklist", 0),
                    "rejected_disabled_sport_market": buyer_run.get("rejected_disabled_sport_market", 0),
                    "rejected_unsupported_market": buyer_run.get("rejected_unsupported_market", 0),
                    "rejected_started": buyer_run.get("rejected_started", 0),
                    "rejected_future_slate": buyer_run.get("rejected_future_slate", 0),
                    "rejected_dedup": buyer_run.get("rejected_dedup", 0),
                    "rejected_min_topup": buyer_run.get("rejected_min_topup", 0),
                    "rejected_unmapped_market": buyer_run.get("rejected_unmapped_market", 0),
                    "rejected_stale_quote": buyer_run.get("rejected_stale_quote", 0),
                    "rejected_closed_market": buyer_run.get("rejected_closed_market", 0),
                    "rejected_low_edge": buyer_run.get("rejected_low_edge", 0),
                    "rejected_unrealistic_edge": buyer_run.get("rejected_unrealistic_edge", 0),
                    "rejected_budget": buyer_run.get("rejected_budget", 0),
                    "reason": buyer_run.get("reason"),
                }
            else:
                auto_buyer_result = {"status": "disabled", "reason": "auto_buyer_disabled"}
        except Exception:
            logger.warning("Automated Polymarket buyer cycle failed", exc_info=True)
            auto_buyer_result = {"status": "error"}

    _lap("auto_buyer_cycle_12")

    # Step 12b: reconcile any resting Auto-Buyer fallback orders (IOC
    # partial/zero fills left resting at the same price -- see DEBUG.md's
    # 2026-09-03 entry) against live exchange state, and cancel any that
    # are still open once their event has started.
    fallback_reconcile_result: dict[str, Any] = {"status": "skipped"}
    if not _skip_auto_buyer_for_verification():
        try:
            from ..portfolio.auto_buyer_ledger import reconcile_pending_auto_buyer_fallbacks

            fallback_reconcile_result = {
                "status": "ok",
                **reconcile_pending_auto_buyer_fallbacks(data_directory),
            }
        except Exception:
            logger.warning("Auto-Buyer fallback reconciliation failed", exc_info=True)
            fallback_reconcile_result = {"status": "error"}
    _lap("fallback_reconcile_12b")

    report = {
        "timing": {
            "post_step_seconds": post_step_seconds,
            "total_seconds": round(time.monotonic() - _t_start, 1),
            "capture_pool_seconds": round(_t_pool_end - _t_start, 1),
            "capture_task_seconds": capture_task_seconds,
            "forecast_sport_done_seconds": forecast_sport_done_seconds,
            "forecast_seconds": round(_t_forecast_end - _t_pool_end, 1),
            "settlement_seconds": round(_t_settle_end - _t_forecast_end, 1),
            "post_settlement_seconds": round(time.monotonic() - _t_settle_end, 1),
        },
        "date": args.date,
        "step0_mlb_baseline_refresh": mlb_baseline_refresh_result,
        "step1_polymarket_search": {
            "status": slate.get("status", "ok"),
            "event_count": slate["event_count"],
            "leagues_with_events": [league for league, items in slate["events_by_league"].items() if items],
            "bbo_capture": slate.get("prospective_bbo_capture", {}),
        },
        "step2_3_forecast_and_log": forecast_result,
        "step3b_polymarket_odds_snapshots": odds_by_sport,
        "step4_settlement": settlement,
        "step4b_calibration": calibration_result,
        "step1b_soccer_scores": soccer_collection,
        "step1e_bet_better_models": bet_better_result,
        "step1f_nfl_injuries": nfl_injuries_result,
        "step1g_nba_injuries": nba_injuries_result,
        "step1h_nhl_injuries": nhl_injuries_result,
        "step1i_cfb_injuries": cfb_injuries_result,
        "step1j_cfb_weather": cfb_weather_result,
        "step1c_mlb_probable_starters": {
            "captured_events": len(mlb_probables_result),
            "archive": "data/point_in_time/mlb_probable_starters.jsonl",
        },
        "step5_summary": _summary(config, ledger),
        "step5b_wnba_availability": {
            "priors": wnba_priors_result,
        },
        "step5c_mlb_availability": mlb_availability_result,
        "step1d_mlb_lineups": mlb_lineups_result,
        "step5d_mlb_starter_snapshots": mlb_starter_snapshot_result,
        "step5e_statcast_aggregates": statcast_aggregates_result,
        "step6_flat_forecast_and_log": flat_result,
        "step7_flat_settlement": flat_settlement,
        "step8_research_settlement": _research_settlement,
        "step9_gated_research_settlement": _gated_settlement,
        "step10_polymarket_edge_record": poly_edge_record_result,
        "step11_polymarket_edge_settle": poly_edge_settle_result,
        "step11b_auto_buyer_settle": auto_buyer_settle_result,
        "step12_auto_polymarket_buyer": auto_buyer_result,
        "step12b_auto_buyer_fallback_reconcile": fallback_reconcile_result,
    }
    _lap("report_build")
    from ..ledger_coverage import build_report

    try:
        coverage = build_report(
            PROJECT_ROOT,
            RuntimePaths.resolve(repo_root=PROJECT_ROOT).ledgers_db,
            args.date,
            report,
        )
        report["ledger_coverage"] = coverage
        coverage_path = PROJECT_ROOT / "outputs/health/ledger_model_coverage.json"
        coverage_path.parent.mkdir(parents=True, exist_ok=True)
        coverage_path.write_text(json.dumps(coverage, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        logger.warning("Ledger coverage verification failed", exc_info=True)
        report["ledger_coverage"] = {"status": "error", "reason": str(exc)}
    _lap("ledger_coverage")
    return _finalize_daily_report(report)
