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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT, ledger_path
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
from ..main_ledgers import MultiSportPickLedger
from ..mlb_baseline_refresh import refresh_if_due
from ..research_ledgers import RESEARCH_LEDGER_SPORTS, existing_research_ledgers, research_ledger
from .commands import _clear_today_open, _polymarket_slate, _research_models_dir, _summary
from .forecast import (
    _forecast_international_sport,
    _forecast_learned_sport,
    _forecast_mlb_totals_flat,
    _forecast_soccer_sport,
    _forecast_tennis_sport,
    _forecast_wnba_spread_sport,
    _log_esports_forecast,
    _refresh_esports_ratings,
    _refresh_international_baseball_ratings,
)
from .settle import _settle_all_unsettled
from .state import DAILY_INTERNATIONAL_BASEBALL_SPORTS, DAILY_LEARNED_SPORTS, ESPORTS_TITLES

logger = logging.getLogger("model_prediction.cli")


def run_daily(args, config, registry, bans, ledger, audit, data_root) -> dict:
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
    from ..data_sources.odds_soccer_scores import collect_soccer_scores

    wnba_priors_result = {"status": "skipped"}
    mlb_probables_result: dict[str, Any] = {}
    soccer_collection = {}

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
            soccer_collection = collect_soccer_scores(days_from=3)
        except Exception:
            logger.warning("Soccer score collection failed", exc_info=True)

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

    with ThreadPoolExecutor(max_workers=8) as io_pool:
        f0 = io_pool.submit(_polymarket_slate, slate_args, config)
        f1 = io_pool.submit(_capture_wnba)
        f2 = io_pool.submit(_build_priors)
        f3 = io_pool.submit(_collect_soccer)
        f4 = io_pool.submit(_capture_mlb_probables)
        f5 = io_pool.submit(_capture_mlb_availability)
        f6 = io_pool.submit(_capture_mlb_starter_snapshots)
        f7 = io_pool.submit(_capture_mlb_lineups)
        for f in (f1, f2, f3, f4, f5, f6, f7):
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
    _clear_today_open(ledger, args.date, by_event_date=True)
    # Also clear and forecast for flat ledger
    flat_ledger = MultiSportPickLedger(data_root, flat=True)
    _clear_today_open(flat_ledger, args.date, by_event_date=True)
    max_data_age = float(config["project"].get("maximum_data_age_hours", 12))
    max_disagreement = float(config["project"].get("maximum_unreviewed_market_disagreement", 0.10))
    # A single decision timestamp makes the learned-slate cache reusable
    # when the same candidates are re-logged to the flat ledger. Before
    # this, two calls a few milliseconds apart rebuilt every feature
    # and repeated upstream reads for no decision-quality benefit.
    forecast_observed_at = utc_now()
    data_directory = Path(ledger_path(config)).parent
    for research_sport in RESEARCH_LEDGER_SPORTS:
        _clear_today_open(
            research_ledger(data_directory, research_sport),
            args.date,
            by_event_date=True,
        )
        _clear_today_open(
            research_ledger(data_directory, research_sport, gated=True),
            args.date,
            by_event_date=True,
        )
    # Compute forecasts once, log to both ledgers (compute > log main > log research > log flat)
    forecast_result = {}
    workers = min(len(DAILY_LEARNED_SPORTS), 5)
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
        except Exception:
            logger.warning("MLB totals flat forecast failed", exc_info=True)

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
        except Exception:
            logger.warning("Soccer forecast failed", exc_info=True)
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
        except Exception:
            logger.warning("WNBA spread forecast failed", exc_info=True)

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
        except Exception:
            logger.warning("Tennis forecast failed", exc_info=True)

    def _esports_title_task(title: str) -> None:
        forecast_result[title] = forecast_esports_slate(
            data_root=data_directory,
            artifact_dir=_research_models_dir(),
            title=title,
            game_date=args.date,
        )
        # Esports never reaches Main, so it no longer gets a Flat
        # row either (operator directive, 2026-08-03) -- Research is
        # already its "every candidate, no gate" companion, the same
        # relationship Flat has to Main.
        _esports_logged = _log_esports_forecast(
            forecast_result[title],
            config,
            research_ledger(data_directory, title),
            flat_mode=False,
            gated_ledger=research_ledger(data_directory, title, gated=True),
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

    def _intl_baseball_league_task(league: str) -> None:
        # KBO/NPB never reach Main, so no Flat row either (operator
        # directive, 2026-08-03) -- same reasoning as esports above.
        forecast_result[league] = _forecast_international_sport(
            data_root=data_directory,
            artifact_dir=_research_models_dir(),
            league=league,
            args_date=args.date,
            config=config,
            research_ledger=research_ledger(data_directory, league),
            gated_ledger=research_ledger(data_directory, league, gated=True),
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

    with ThreadPoolExecutor(max_workers=6) as research_pool:
        research_futures = [
            research_pool.submit(_mlb_totals_task),
            research_pool.submit(_soccer_task),
            research_pool.submit(_tennis_task),
            research_pool.submit(_wnba_spread_task),
            research_pool.submit(_esports_block),
            research_pool.submit(_intl_baseball_block),
        ]
        # Each task above already catches and logs its own real
        # forecast errors; .result() here only re-raises a bug in the
        # wrapper itself (e.g. a NameError), which should still stop
        # the run loudly rather than be swallowed.
        for future in as_completed(research_futures):
            future.result()
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
        settle_args = argparse.Namespace(
            all_unsettled=True,
            void_postponed=True,
        )
        settlement = _settle_all_unsettled(settle_args, config, ledger)
        flat_settlement = _settle_all_unsettled(
            settle_args,
            config,
            flat_ledger,
        )
        _research_settlement = {
            sport_ledger.path.stem: _settle_all_unsettled(
                settle_args,
                config,
                sport_ledger,
            )
            for sport_ledger in existing_research_ledgers(data_directory)
        }
        _gated_settlement = {
            sport_ledger.path.stem: _settle_all_unsettled(
                settle_args,
                config,
                sport_ledger,
            )
            for sport_ledger in existing_research_ledgers(
                data_directory,
                gated=True,
            )
        }

    # Step 10 & 11: Automated Polymarket CLOB Edge Scanner & Settlement
    poly_edge_record_result: dict[str, Any] = {"status": "skipped"}
    poly_edge_settle_result: dict[str, Any] = {"status": "skipped"}
    try:
        from ..portfolio.polymarket_ledger import (
            record_polymarket_orders,
            settle_polymarket_ledger_rows,
        )
        from ..portfolio.polymarket_scanner import PolymarketScanner

        scanner = PolymarketScanner(min_edge=0.025, bankroll=1000.0)
        scan_res = scanner.scan_directory(
            base_dir=data_directory / "odds",
            require_model=True,
            pregame_only=True,
        )
        if scan_res.actionable_orders:
            orders_payload = [
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
                    "ev_pct": o.ev_pct,
                    "stake_units": o.stake_units,
                    "is_maker": o.is_maker,
                    "reason": o.reason,
                    "event_start_utc": o.event_start_utc,
                    "observed_at_utc": o.observed_at_utc,
                }
                for o in scan_res.actionable_orders
            ]
            poly_edge_record_result = record_polymarket_orders(orders_payload, data_root=data_directory)
        else:
            poly_edge_record_result = {"status": "ok", "recorded_count": 0, "actionable_orders": 0}

        if not args.skip_settlement:
            poly_edge_settle_result = settle_polymarket_ledger_rows(data_root=data_directory)
    except Exception:
        logger.warning("Automated Polymarket edge scan/settle failed", exc_info=True)
        poly_edge_record_result = {"status": "error"}
        poly_edge_settle_result = {"status": "error"}

    return {
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
        "step1b_soccer_scores": soccer_collection,
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
        "step6_flat_forecast_and_log": flat_result,
        "step7_flat_settlement": flat_settlement,
        "step8_research_settlement": _research_settlement,
        "step9_gated_research_settlement": _gated_settlement,
        "step10_polymarket_edge_record": poly_edge_record_result,
        "step11_polymarket_edge_settle": poly_edge_settle_result,
    }
