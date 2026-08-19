"""Misc command helpers.

Mechanical extraction from the former cli.py monolith (DD-6 split, stage
4). Polymarket slate capture, daily-summary machinery, ledger helpers,
chain verification, and team bans. The logger is defined HERE for the
same BLE001-exemption reason as the other cli modules.
"""

from __future__ import annotations

import argparse
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from ..bans import TeamBanList
from ..config import PROJECT_ROOT, ledger_path
from ..data_sources.kalshi import DEFERRED_MESSAGE as KALSHI_DEFERRED_MESSAGE
from ..data_sources.polymarket_us import (
    POLYMARKET_SPORT_LEAGUES,
    PolymarketUSClient,
    capture_slate_snapshots,
)
from ..domain import EASTERN, parse_utc, utc_now
from ..runtime_paths import rolling_models_root
from .state import SPORTS

logger = logging.getLogger("model_prediction.cli")


def _polymarket_slate(args, config) -> dict:
    if args.provider == "kalshi":
        return {"provider": "kalshi", "status": "deferred", "note": KALSHI_DEFERRED_MESSAGE}
    client = PolymarketUSClient()
    game_date = date.fromisoformat(args.date)
    league_errors: dict[str, str] = {}
    if args.league:
        events = {args.league.upper(): client.slate(args.league, game_date, args.timezone)}
    elif args.all:
        events = {}
        # Every sport's leagues, flattened into one list and fetched through
        # ONE shared pool. Deliberately NOT "9 sports concurrently, each
        # calling sport_slate which itself fans its own leagues out
        # concurrently" -- that nests two pools sharing the same
        # PolymarketUSClient connection pool, and at peak (e.g. soccer's ~30
        # leagues alongside esports'/tennis'/etc.) pushes concurrent
        # in-flight requests well past what the gateway tolerates: measured
        # directly, request latency is flat around 0.6s per call up through
        # ~16 concurrent requests, then degrades to 3-5s per call (and
        # starts raising SSL EOF connection errors) at 24-32. One flat pool
        # capped at 16 keeps every fetch (30+ leagues) under that ceiling
        # instead of silently multiplying concurrency across nested pools.
        all_leagues = [(sport, league) for sport in SPORTS for league in POLYMARKET_SPORT_LEAGUES[sport]]

        def _fetch_league_slate(entry: tuple[str, str]) -> tuple[str, str, list[dict[str, Any]], str | None]:
            sport, league = entry
            try:
                return sport, league, client.slate(league, game_date, args.timezone), None
            except httpx.HTTPError as exc:
                return sport, league, [], str(exc)[:200]

        with ThreadPoolExecutor(max_workers=min(16, len(all_leagues))) as pool:
            for sport, league, league_events, error in pool.map(_fetch_league_slate, all_leagues):
                events[league] = league_events
                if error is not None:
                    league_errors[league] = error
    elif args.sport:
        result = client.sport_slate(args.sport, game_date, args.timezone)
        events = result.events
        league_errors = result.errors
    else:
        raise ValueError("provide --sport, --league, or --all")
    bbo_capture = (
        {"status": "disabled"}
        if getattr(args, "no_snapshot_bbo", False)
        else capture_slate_snapshots(
            client,
            events,
            Path(ledger_path(config)).parent,
            args.date,
        )
    )
    return {
        "provider": "polymarket_us",
        "game_date": args.date,
        "timezone": args.timezone,
        "events_by_league": events,
        "event_count": sum(len(items) for items in events.values()),
        "league_fetch_errors": league_errors,
        "prospective_bbo_capture": bbo_capture,
        "note": (
            "Public market discovery plus prospective executable-BBO storage. "
            "Event-list quotes remain indicative."
        ),
    }


def _research_models_dir() -> Path:
    """Rolling-first artifact directory for research retraining/forecast.

    The scheduled cycle retrains esports/KBO/NPB ratings artifacts every
    run; those live under the runtime root's models/ so the checked-in
    config/models copies stay frozen promoted artifacts. Readers fall
    back to the frozen copies until the first rolling copy exists.
    """
    rolling = rolling_models_root(PROJECT_ROOT)
    if rolling.is_dir() and any(rolling.iterdir()):
        return rolling
    return PROJECT_ROOT / "config" / "models"


def _clear_today_open(
    ledger, date_str: str, by_event_date: bool = False, leagues: set[str] | None = None
) -> list[str]:
    """Remove open picks for a date before re-forecasting, via the audited path.

    When by_event_date is True, also removes open picks whose event_start_utc
    matches date_str — used for flat ledger to prevent duplicate forecast runs.
    All removals go through ``PickLedger.remove_open_rows`` so they hold the
    ledger lock and append ``pick_removed`` audit events.

    leagues: when given (casefolded league names), only rows in those leagues
    are considered for removal. ``ledger`` here is frequently a
    MultiSportPickLedger spanning every sport that shares one Main/Flat file
    pair -- without this filter, re-forecasting a single sport (e.g.
    `forecast --sport tennis --log`) clears every OTHER sport's still-open
    today rows too, since they all live in the same underlying ledger, and
    only the requested sport gets regenerated afterward. Found 2026-08-03
    when a single-sport flat-forecast run silently wiped that day's real
    open MLB/WNBA/tennis Main picks.

    Only removes picks for games that HAVEN'T STARTED YET. Re-running the
    pipeline intraday is meant to refresh not-yet-started candidates with
    fresher data — a game that started between two runs is not a stale
    candidate to replace, it's a live/settling position. Without this guard,
    a later same-day run would clear its still-open row and never recreate
    it (the new forecast pass correctly excludes started games), silently
    losing the record of a pick that was made legitimately pregame.

    Passes allow_staked_removal=True on every call: this project is
    shadow-only (no real orders are ever placed from a forecast run — see
    POLICY.md), so a "QUALIFIED_SHADOW_CALL, units > 0" row is never actual
    staked money on EITHER ledger. Without this, an MLB pick strong enough to
    independently clear real eligibility (not just the flat-mode diagnostic
    override) would get silently frozen forever — never replaced by any
    later re-forecast, on the main ledger exactly as much as on flat.
    """
    now = utc_now()
    to_remove = []
    for row in ledger.rows():
        if row.get("status") != "open":
            continue
        if leagues is not None and str(row.get("league", "")).casefold() not in leagues:
            continue
        created = str(row.get("created_at_utc", "") or "")
        event = str(row.get("event_start_utc", "") or "")
        if not (created.startswith(date_str) or (by_event_date and event.startswith(date_str))):
            continue
        try:
            started = parse_utc(event) <= now
        except ValueError:
            started = False  # can't verify timing — don't risk deleting it
        if started:
            continue
        to_remove.append(row["pick_id"])
    if not to_remove:
        return []
    return ledger.remove_open_rows(
        to_remove, reason=f"re-forecast replacement for {date_str}", allow_staked_removal=True
    )


def _drift_check(settled_qualified: list, config: dict) -> dict:
    """Compare live settled hit rate against model holdout for each sport."""
    import json
    import math
    from collections import defaultdict

    by_sport = defaultdict(lambda: {"wins": 0, "losses": 0})
    for row in settled_qualified:
        sport = str(row.get("league") or row.get("sport") or "?").upper()
        if row.get("result") == "win":
            by_sport[sport]["wins"] += 1
        elif row.get("result") == "loss":
            by_sport[sport]["losses"] += 1

    drift = {}
    for sport_name, counts in by_sport.items():
        n = counts["wins"] + counts["losses"]
        if n < 10:
            drift[sport_name] = {"status": "insufficient_sample", "n": n}
            continue

        live_hr = counts["wins"] / n
        holdout_hr = None
        model_config = (config.get("models") or {}).get(sport_name, {})
        artifact_rel = model_config.get("production_artifact", "")
        if artifact_rel:
            model_path = PROJECT_ROOT / artifact_rel
            if model_path.exists():
                try:
                    artifact = json.loads(model_path.read_text())
                    holdout_hr = artifact.get("qualification", {}).get("hit_rate")
                except (json.JSONDecodeError, KeyError):
                    pass

        if holdout_hr is None:
            drift[sport_name] = {"status": "no_holdout_reference", "live_hr": round(live_hr, 4), "n": n}
            continue

        # 2-sigma check
        se = math.sqrt(holdout_hr * (1 - holdout_hr) / n)
        z = (live_hr - holdout_hr) / se if se > 0 else 0
        status = "drifting" if z < -2.0 else ("excelling" if z > 2.0 else "on_track")

        drift[sport_name] = {
            "status": status,
            "live_hr": round(live_hr, 4),
            "holdout_hr": round(holdout_hr, 4),
            "n": n,
            "z_score": round(z, 2),
        }

    return drift


def _summary(config, ledger) -> dict:
    rows = ledger.rows()
    today = utc_now().astimezone(EASTERN).date().isoformat()

    def _et_day(value: str) -> str:
        try:
            return parse_utc(value).astimezone(EASTERN).date().isoformat()
        except (KeyError, ValueError):
            return ""

    created_today = [row for row in rows if _et_day(row["created_at_utc"]) == today]
    settled_today = [row for row in rows if row["settled_at_utc"] and _et_day(row["settled_at_utc"]) == today]
    open_rows = [row for row in rows if row["status"] == "open"]
    settled_qualified = [
        row for row in rows if row["status"] == "settled" and row["record_type"] == "QUALIFIED_SHADOW_CALL"
    ]
    pnl = sum(float(row["pnl_units"] or 0) for row in settled_qualified)
    clv_values = [float(row["probability_clv"]) for row in rows if row["probability_clv"]]
    measured_edge_rows = [row for row in rows if row["model_version"].startswith("measured-edge")]
    measured_edge_settled = [row for row in measured_edge_rows if row["status"] == "settled"]
    return {
        "date_et": today,
        "picks_logged_today": len(created_today),
        "picks_settled_today": len(settled_today),
        "open_picks": len(open_rows),
        "open_units": round(
            sum(
                float(row["units"] or 0) for row in open_rows if row["record_type"] == "QUALIFIED_SHADOW_CALL"
            ),
            2,
        ),
        "qualified_pnl_units_all_time": round(pnl, 4),
        "mean_probability_clv": round(sum(clv_values) / len(clv_values), 6) if clv_values else None,
        "bankroll_reference_units": config["bankroll"]["reference_units"],
        "research_progress": {
            "measured_edge_forward_picks": len(measured_edge_rows),
            "measured_edge_settled": len(measured_edge_settled),
            "iteration_policy": "continuous",
            "promotion_requires": "versioned walk-forward ablation and locked holdout",
        },
        "model_drift": _drift_check(settled_qualified, config),
        "note": "Shadow research accounting only; no real-money authorization.",
    }


def _row_artifact_qualified(row: dict[str, str], config: dict) -> bool:
    """Whether a ledger row's backing model ARTIFACT is genuinely qualified,
    not just config-declared.

    ``config/model.yaml`` can set ``status: shadow_qualified`` with an
    explicit ``qualification_override`` for a league whose artifact itself
    never cleared holdout validation (e.g. MLB v6, 2026-07:
    ``qualification.meets_primary_holdout_metrics`` is false --
    "running live for observation" ahead of any real promotion decision).
    That's a legitimate, documented choice for shadow logging, but
    real-money execution (PolymarketExecutor.execute's ``artifact_qualified``
    gate) must not treat an override-only qualification as equivalent to a
    genuinely validated one.

    Two artifact schemas exist in this project (verified against real
    artifact files, not assumed): learned MLB/NBA/WNBA/NFL artifacts record
    ``qualification.meets_primary_holdout_metrics``; Elo-baseline artifacts
    (esports, KBO, NPB -- identifiable by ``k``+``ratings`` fields, the same
    shape check dashboard_server.py's ``_ml_cell`` uses) record a top-level
    ``qualified_for_betting`` instead. Neither schema has a top-level
    ``qualified`` field -- checking one that doesn't exist would silently
    return False for every artifact, including genuinely qualified ones.

    Fails closed (returns False) whenever the artifact can't be loaded or
    matched to the exact version this row was computed from -- an
    unverifiable claim of qualification is not qualification.
    """
    model_config = config.get("models", {}).get(str(row.get("league", "")).upper(), {})
    artifact_value = model_config.get("production_artifact")
    if not artifact_value:
        return False
    artifact_path = Path(artifact_value)
    if not artifact_path.is_absolute():
        artifact_path = PROJECT_ROOT / artifact_path
    if not artifact_path.exists():
        return False
    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    row_hash = str(row.get("model_artifact_hash") or "")
    if row_hash and row_hash != str(artifact.get("artifact_hash", "")):
        # The config's current production artifact isn't the exact version
        # this row was priced from (e.g. a newer artifact has since been
        # promoted) -- can't verify qualification against a version that no
        # longer matches, so refuse to claim it.
        return False
    if "k" in artifact and "ratings" in artifact:
        return bool(artifact.get("qualified_for_betting", False))
    return bool(artifact.get("qualification", {}).get("meets_primary_holdout_metrics", False))


def _verify_chain(events_path, ledger) -> dict:
    """Audit-chain link/hash verification plus ledger<->audit reconciliation."""
    import hashlib

    previous = "0" * 64
    lines = 0
    breaks: list[dict] = []
    created: set[str] = set()
    removed: set[str] = set()
    path = Path(events_path)
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                lines += 1
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    breaks.append({"line": number, "kind": "unparseable"})
                    continue
                if event.get("previous_hash") != previous:
                    breaks.append({"line": number, "kind": "broken_link"})
                canonical = {key: value for key, value in event.items() if key != "event_hash"}
                digest = hashlib.sha256(
                    json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
                ).hexdigest()
                if digest != event.get("event_hash"):
                    breaks.append({"line": number, "kind": "hash_mismatch"})
                previous = event.get("event_hash", previous)
                event_type = event.get("event_type")
                if event_type in ("pick_created", "research_observation_created"):
                    created.add(str(event.get("subject_id")))
                elif event_type == "pick_removed":
                    removed.add(str(event.get("subject_id")))
    ledger_ids = {row["pick_id"] for row in ledger.rows()}
    deleted_unaudited = created - removed - ledger_ids
    return {
        "audit_lines": lines,
        "breaks": breaks,
        "break_count": len(breaks),
        "ledger_rows": len(ledger_ids),
        "audited_created": len(created),
        "audited_removed": len(removed),
        "rows_missing_creation_event": sorted(ledger_ids - created),
        "created_but_absent_without_removal_event": len(deleted_unaudited),
        "chain_intact": not breaks,
        "reconciled": not deleted_unaudited and not (ledger_ids - created),
        "note": (
            "created_but_absent_without_removal_event counts historical deletions "
            "made before the audited remove_open_rows path existed."
        ),
    }


def _handle_ban(args: argparse.Namespace, bans: TeamBanList) -> object:
    if args.ban_command == "list":
        return [asdict(entry) for entry in bans.list()]
    team, banned = bans.check(args.league, args.team)
    if args.ban_command == "check":
        return {
            "league": team.league.value,
            "canonical_team_id": team.canonical_team_id,
            "canonical_name": team.canonical_name,
            "banned": banned,
        }
    entry, changed = (
        bans.add(args.league, args.team, args.reason, args.review_after)
        if args.ban_command == "add"
        else bans.remove(args.league, args.team)
    )
    return {**asdict(entry), "changed": changed, "banned": args.ban_command == "add"}
