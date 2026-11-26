"""Automated Polymarket Execution Engine.

Executes forecast picks with strict safety gates:
1. Whitelist of empirical positive-EV models (Tennis, Soccer, WNBA ML, CS2/LoL Gated).
2. Explicit model and sport/market blocks, including all MLB moneylines.
3. Configurable unit value sizing (default 1U = $0.50 / 50 cents).
4. Point-in-time pregame verification (now < event_start_utc) and live quote freshness (< 5 min).
5. Hard daily spending limit and per-game exposure caps.
6. Single-order deduplication per pick_id against the append-only audit log.
7. Real-money live execution when enabled, with persistent dashboard ON/OFF control.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger("model_prediction.auto_executor")

from model_prediction.audit import AuditLog
from model_prediction.calibration_pipeline import calibrate_for_serving
from model_prediction.daily_lock import acquire_lock, release_lock
from model_prediction.data_sources.polymarket_execute import (
    ExecutionGateError,
    OrderSubmitUnknownError,
    OrderTicket,
    PolymarketExecutor,
    classify_gate_refusal,
)
from model_prediction.domain import iso_utc, parse_utc, utc_now
from model_prediction.portfolio.auto_buyer_ledger import event_key_from_slug, is_fallback_submit_unknown
from model_prediction.runtime_paths import RuntimePaths, load_dotenv_into_environ

_paths = RuntimePaths.resolve()
DATA = _paths.runtime_root
EASTERN = ZoneInfo("America/New_York")

# Auto-load .env credentials if present
load_dotenv_into_environ(_paths.repo_root / ".env")

DEFAULT_WHITELIST_MODELS: tuple[str, ...] = (
    # NBA: spread + moneyline. nba-moneyline-v9 was removed 2026-11-12 (placeholder
    # coefficients, rolled back from serving 2026-11-10, 4d5624ed). The serving
    # moneyline champion nba-elo-trend-lr-v5 (promoted 2026-11-13) is listed by
    # operator directive 2026-11-13 ("nba ml can stay in auto buyer"). Note: while
    # its registry evidence_status is historical_only, every NBA moneyline Main row
    # is a PAPER_CALL_MODEL_EVIDENCE_UNVERIFIED paper call, which the decision gate
    # below rejects -- so this entry buys nothing until that flag changes.
    # nba-total-margin-v9 was removed 2026-11-13 (NBA totals must not reach the
    # paper ledger; ("nba", "total") is also blocked in DISABLED_AUTO_BUYER_SPORT_MARKETS).
    # nba-spread-margin-v9 stays by operator directive 2026-11-13 ("keep nba
    # spread"), overriding the same-day loss-review pull (2W-7L in its 8-12% edge
    # bucket, calibration slope 0.19, under-dispersed vs the market line). That
    # evidence is still open; a market-relative replay is the next step.
    "nba-spread-margin-v9",
    "nba-elo-trend-lr-v5",
    # NFL (Latest Generation v7 Champions; NFL moneyline currently has no
    # active whitelisted champion -- nfl-elo-trend-lr-v4 is blacklisted below)
    "nfl-spread-structural-v7",
    # nfl-spread-structural-v7.2 (promoted 2026-11-23): v7 with the cover probability shrunk
    # (logit slope 0.378, fitted on validation by operator exception); v7 kept for older rows.
    "nfl-spread-structural-v7.2",
    "nfl-total-structural-v7",
    # nfl-total-structural-v7.1 (promoted 2026-11-23): v7 with the over probability shrunk
    # (logit slope 0.522); v7 stays listed so its open rows can still be bought.
    "nfl-total-structural-v7.1",
    # NCAAF: cfb-structural-v5 promoted 2026-11-13 (operator directive, Direct Model
    # Wiring policy). v4 no longer produces rows; it is not blacklisted. v5.2 (promoted
    # 2026-11-23) serves all three markets: v5 with totals shrunk to ~50%; v5 stays listed
    # for rows logged before the switch. The drawdown circuit breaker is keyed by model id,
    # so v5.2 starts with no quarantine history even though its spread/moneyline equal v5's.
    "cfb-structural-v5",
    "cfb-structural-v5.2",
    # WNBA (Dormant off-season)
    "wnba-elo-trend-lr-v4",
    "wnba-total-margin-v2",
    "wnba-spread-margin-v2",
    # MLB (Dormant off-season). mlb-structural-v10-frozen is blacklisted below
    # (pulled 2026-09-02 pending qualification review) -- not whitelisted here.
    "mlb-structural-runline-v4",
    "mlb-nrfi-v4",
    "mlb-nrfi-v3",
    "mlb-moneyline-v9-residual",
)

EXPLICIT_BLACKLIST_MODELS: tuple[str, ...] = (
    # CS / CS2 pulled per operator directive 2026-10-24 & 2026-11-08 audit (50 pushes, -0.1% ROI)
    "cs2-tiered-elo-v6",
    "cs2-spread-tiered-elo-v6",
    "cs2-total-tiered-elo-v6",
    # Esports decommissioned per operator audit 2026-11-08 (net -$24.23 across all esports, wide spreads, high push rate)
    "lol-tiered-elo-v6",
    "lol-spread-tiered-elo-v6",
    "lol-total-tiered-elo-v6",
    "lol-contextual-v7",
    "dota2-tiered-elo-v6",
    "dota2-spread-tiered-elo-v6",
    "dota2-total-tiered-elo-v6",
    "dota2-contextual-v7",
    "valorant-tiered-elo-v6",
    "valorant-spread-tiered-elo-v6",
    "valorant-total-tiered-elo-v6",
    "valorant-contextual-v7",
    "r6-contextual-v7",
    "rainbow_six-spread-tiered-elo-v6",
    "rainbow_six-total-tiered-elo-v6",
    # International Baseball decommissioned per operator audit 2026-11-08 (season ended, negative ROI, ESPN settlement friction)
    "kbo-baseball-v3",
    "npb-baseball-v3",
    # Prior model decommissions
    "measured-edge-totals-v3",  # MLB totals (degraded, negative CLV vs market close)
    "measured-edge-margin-v3",  # MLB run line (negative EV)
    "wnba-spread-margin-v1",  # Legacy WNBA spread (severe miscalibration)
    "wnba-total-margin-v1",  # Legacy WNBA totals (uncalibrated static SD)
    "nba-spread-margin-v1",  # Legacy NBA spread replaced by calibrated v2
    "nba-total-margin-v1",  # Legacy NBA totals replaced by calibrated v2
    "nba-spread-margin-v2",
    "nba-spread-margin-v3",
    "nba-spread-margin-v4",
    "nba-spread-margin-v5",
    "nba-total-margin-v2",
    "nba-total-margin-v3",
    "nba-total-margin-v4",
    "nba-total-margin-v5",
    "nba-elo-trend-lr-v4",
    "nfl-elo-trend-lr-v4",
    "nfl-spread-structural-v5",
    "nfl-total-structural-v5",
    "mlb-nrfi-v1",  # Legacy MLB NRFI
    "cfb-spread-v1",  # Legacy CFB spread (severe drift / degraded)
    "cfb-total-v1",  # CFB total (uncalibrated / degraded)
    "college-football-v1",  # Legacy CFB moneyline (uncalibrated favorite bias, -37.46U)
    # All MLB pulled 2026-09-02: mlb-elo-trend-lr-v8 shipped via operator
    # override despite qualified:false and a documented validation-Brier
    # regression vs its own incumbent (holdout number that promoted it was
    # exactly the "peeking at holdout" pattern docs/AGENTS.md warns against).
    # Live Auto-Buyer record confirmed it: 43% win rate / -$1.00 vs a claimed
    # 60.8% holdout hit rate. v7 also never cleared the qualification gate.
    # mlb-structural-v10-frozen and mlb-nrfi-v2 pulled alongside it pending
    # the same qualification review, not because they were shown bad live.
    "mlb-elo-trend-lr-v8",
    "mlb-structural-v10-frozen",
    "mlb-nrfi-v2",
    # Pulled 2026-09-07: same miscalibration signature as the MLB pull above.
    # Live Auto-Buyer record: 19 settled bets, 6W-13L (31.6% actual) against
    # a claimed average 60.3% win probability -- a 29-point overconfidence
    # gap. Realized P&L -$49.23.
    "soccer-poisson-dc-v1",
    # Esports underperformer pulled:
    "rainbow_six-tiered-elo-v6",  # 2W-8L (-68.1% ROI) in production ledgers
    # NCAAB decommissioned per operator directive 2026-11-09:
    "ncaab-spread-v6",
    "ncaab-spread-v7",
    "ncaab-moneyline-v7m",
    "ncaab-spread-v5",
    "ncaab-total-v6",
    "ncaab-total-v5",
    "ncaab-tempo-v4",
    "ncaab-tempo-v3",
    "ncaab-tempo-v2",
    "ncaab-tempo-v1",
    # Hockey / NHL decommissioned per operator directive 2026-11-09:
    "hockey-poisson-dc-v2",
    "hockey-poisson-dc-v1",
    # Darts decommissioned per operator directive 2026-11-09:
    "darts-markov-v2",
    "darts-markov-v1",
    # International Basketball decommissioned per operator directive 2026-11-09:
    "eurolg-tempo-v2",
    "eurolg-tempo-v1",
    "nbl-tempo-v2",
    "nbl-tempo-v1",
    # Legacy deprecated versions decommissioned in favor of latest champions:
    "nba-moneyline-v7",
    "nba-spread-margin-v7",
    "nba-total-margin-v7",
    "nba-spread-margin-v6",
    "nba-total-margin-v6",
    "nba-moneyline-v8",
    "nba-spread-margin-v8",
    "nba-total-margin-v8",
    "nfl-spread-structural-v6",
    "nfl-total-structural-v6",
    "cfb-structural-v3",
    "cfb-structural-v2",
)

AUTO_BUYER_STATE_FILE = DATA / "auto_buyer_state.json"
# Paper-mode picks are recorded through the same record/read/settle functions
# as live trades, just rooted at this separate directory -- never mixed into
# the real-money ledger, so paper and live P&L can never contaminate each other.
PAPER_AUTO_BUYER_DATA_ROOT = DATA / "paper_auto_buyer"
AUTO_BUYER_MODES = frozenset({"paper", "live"})
DEFAULT_AUTO_BUYER_MODE = "paper"
EXECUTABLE_AUTO_BUYER_MARKET_TYPES = frozenset({"moneyline", "spread", "total", "nrfi"})
ESPORTS_LEAGUES = frozenset({"cs2", "lol", "dota2", "valorant", "r6", "rainbow_six", "cod", "ow", "rl"})
DISABLED_AUTO_BUYER_SPORT_MARKETS = frozenset(
    {
        ("mlb", "moneyline"),
        # NBA totals pulled from the auto-buyer 2026-11-13 (operator directive).
        # The serving champion (nba-total-margin-v9) keeps forecasting for the
        # Main ledger; only execution is blocked.
        ("nba", "total"),
        # NFL spreads and totals were pulled and then restored the same day
        # (operator directive 2026-11-15: nothing comes off production, the paper
        # ledger needs picks to judge the model). Open evidence against both is in
        # docs/DEBUG.md (2026-11-15): spread edge >= 5% is 6-14 on settled quotes,
        # total edge >= 5% is 13-17 on the replay holdout. The input gate below
        # still blocks rows priced without verified weather or qualified inputs.
        ("soccer", "moneyline"),
        ("soccer", "total"),
        ("soccer", "spread"),
        ("tennis", "spread"),
        ("tennis", "moneyline"),
        ("tennis", "total"),
        ("ncaab", "total"),
        ("ncaab", "spread"),
        ("ncaab", "moneyline"),
        ("hockey", "total"),
        ("hockey", "spread"),
        ("hockey", "moneyline"),
        ("nhl", "total"),
        ("nhl", "spread"),
        ("nhl", "moneyline"),
        ("darts", "moneyline"),
        ("euroleague", "moneyline"),
        ("euroleague", "spread"),
        ("euroleague", "total"),
        ("nbl", "moneyline"),
        ("nbl", "spread"),
        ("nbl", "total"),
    }
)
DEFAULT_AUTO_BUYER_UNIT_VALUE_USD = 0.50
DEFAULT_AUTO_BUYER_MAX_EDGE = 0.12
DEFAULT_MAX_DAILY_SPEND_UNITS = 50.0
DEFAULT_MAX_GAME_STAKE_UNITS = 5.0
DEFAULT_MAX_EVENT_LOOKAHEAD_HOURS = 24.0
DEFAULT_MAX_MODEL_AGE_MINUTES = 24 * 60
MAX_AUTO_BUYER_QUOTE_AGE_SECONDS = 300
MAX_SOURCE_QUOTE_AGE_SECONDS = 6 * 3600

# Per-model cohorts (quick-261125-31d): operator-set state-file entries that let one model's rows in
# a saved-edge window through the categorical gates, in exactly one book. Only a valid, enabled
# ``mode: live`` cohort can reach the real-money order path from the scheduler.
COHORTS_STATE_KEY = "cohorts"
COHORT_MODES = ("paper", "live")
COHORT_MARKETS = frozenset({"moneyline", "spread", "total"})
COHORT_REQUIRED_KEYS = frozenset(
    {"model_id", "sport", "market", "min_saved_edge", "max_saved_edge_exclusive", "mode", "enabled"}
)
COHORT_OPTIONAL_KEYS = frozenset({"note", "approved_by", "approved_at_utc"})
# Phantom-edge guard for every cohort: a live-ask edge above 25% is far more often a bad quote than
# a mispriced market, and no window may reach above it. Same value as the NCAAB paper trial's sanity
# ceiling; the measured ML v7m cohort's largest saved edge is 19.6%, so it drops none of those rows.
COHORT_EXECUTION_EDGE_CEILING = 0.25
# Sports a cohort may name: the production six minus NCAAF, which every auto-buyer path dropped on
# 2026-11-24 (operator directive). The code-level always-off sports stay off for cohorts too.
COHORT_SPORTS = frozenset({"nba", "ncaab", "nfl", "mlb", "wnba"})
# Code-level categorical blocks a cohort can never lift (state-file blocks it can): MLB moneylines
# (pulled 2026-09-02) and NBA totals (operator 2026-11-13: must not reach the paper ledger).
COHORT_BLOCKED_SPORT_MARKETS = frozenset({("mlb", "moneyline"), ("nba", "total")})


@dataclass(frozen=True)
class BuyCohort:
    """One operator-approved cohort: model, sport/market, half-open saved-edge window, book."""

    model_id: str
    sport: str
    market: str
    min_saved_edge: float
    max_saved_edge_exclusive: float | None
    mode: str

    @property
    def label(self) -> str:
        upper = "inf" if self.max_saved_edge_exclusive is None else f"{self.max_saved_edge_exclusive:g}"
        return f"{self.model_id}:{self.market}:[{self.min_saved_edge:g},{upper}):{self.mode}"

    def contains(self, edge: float) -> bool:
        if not math.isfinite(edge) or edge < self.min_saved_edge:
            return False
        return self.max_saved_edge_exclusive is None or edge < self.max_saved_edge_exclusive


@dataclass(frozen=True)
class CohortParse:
    active: tuple[BuyCohort, ...]
    errors: tuple[str, ...]


def _cohort_edge(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _parse_one_cohort(entry: Any) -> tuple[BuyCohort | None, str | None]:
    """Validate one state entry. Returns (cohort, None), (None, error), or (None, None) when disabled."""
    if not isinstance(entry, dict):
        return None, f"entry is {type(entry).__name__}, not an object"
    keys = set(entry)
    missing = COHORT_REQUIRED_KEYS - keys
    if missing:
        # A missing upper-bound key must never silently mean "open-ended".
        return None, f"missing keys {sorted(missing)}"
    unknown = keys - COHORT_REQUIRED_KEYS - COHORT_OPTIONAL_KEYS
    if unknown:
        return None, f"unknown keys {sorted(unknown)}"
    enabled = entry["enabled"]
    if not isinstance(enabled, bool):
        return None, f"enabled must be true or false, got {enabled!r}"
    model_id = entry["model_id"]
    if not isinstance(model_id, str) or not model_id.strip():
        return None, "model_id must be a non-empty string"
    sport = entry["sport"]
    if not isinstance(sport, str) or sport.strip().lower() not in COHORT_SPORTS:
        return None, f"sport must be one of {sorted(COHORT_SPORTS)}, got {sport!r}"
    market = entry["market"]
    if not isinstance(market, str) or market not in COHORT_MARKETS:
        return None, f"market must be one of {sorted(COHORT_MARKETS)}, got {market!r}"
    if (sport.strip().lower(), market) in COHORT_BLOCKED_SPORT_MARKETS:
        return None, f"{sport}:{market} is blocked in code for every auto-buyer path"
    mode = entry["mode"]
    if not isinstance(mode, str) or mode not in COHORT_MODES:
        return None, f"mode must be exactly 'paper' or 'live', got {mode!r}"
    minimum = _cohort_edge(entry["min_saved_edge"])
    ceiling = COHORT_EXECUTION_EDGE_CEILING
    if minimum is None or not 0 <= minimum < ceiling:
        return (
            None,
            f"min_saved_edge must be a finite number in [0, {ceiling:g}), got {entry['min_saved_edge']!r}",
        )
    raw_max = entry["max_saved_edge_exclusive"]
    maximum: float | None = None
    if raw_max is not None:
        maximum = _cohort_edge(raw_max)
        if maximum is None or not minimum < maximum <= ceiling:
            return None, f"max_saved_edge_exclusive must be null or in (min, {ceiling:g}], got {raw_max!r}"
    if mode == "live" and maximum is None:
        return None, "a live cohort needs a finite max_saved_edge_exclusive (no open-ended real money)"
    if not enabled:
        return None, None
    return BuyCohort(model_id.strip(), sport.strip().lower(), market, minimum, maximum, mode), None


def _parse_cohorts(state: dict[str, Any]) -> CohortParse:
    """Strict, fail-closed parse of the state file's ``cohorts`` list.

    Every bad entry is ignored and named in ``errors`` (and logged); a value that is not a list
    yields no cohorts. Two enabled cohorts of the same model/sport/market with overlapping windows
    are both dropped: a row must belong to at most one book.
    """
    if COHORTS_STATE_KEY not in state:
        return CohortParse((), ())
    raw = state.get(COHORTS_STATE_KEY)
    errors: list[str] = []
    candidates: list[tuple[int, BuyCohort]] = []
    if not isinstance(raw, list):
        errors.append(f"cohorts must be a list, got {type(raw).__name__}")
    else:
        for index, entry in enumerate(raw):
            cohort, error = _parse_one_cohort(entry)
            if error is not None:
                errors.append(f"cohorts[{index}]: {error}")
            elif cohort is not None:
                candidates.append((index, cohort))
    dropped: set[int] = set()
    for position, (index_a, a) in enumerate(candidates):
        for index_b, b in candidates[position + 1 :]:
            if (a.model_id, a.sport, a.market) != (b.model_id, b.sport, b.market):
                continue
            a_max = math.inf if a.max_saved_edge_exclusive is None else a.max_saved_edge_exclusive
            b_max = math.inf if b.max_saved_edge_exclusive is None else b.max_saved_edge_exclusive
            if a.min_saved_edge < b_max and b.min_saved_edge < a_max:
                dropped.update({index_a, index_b})
                errors.append(f"cohorts[{index_a}] and cohorts[{index_b}]: overlapping windows, both dropped")
    for error in errors:
        logger.warning("auto-buyer cohort ignored: %s", error)
    return CohortParse(tuple(c for index, c in candidates if index not in dropped), tuple(errors))


@dataclass(frozen=True)
class AutoExecutionConfig:
    unit_value_usd: float = DEFAULT_AUTO_BUYER_UNIT_VALUE_USD
    min_model_probability: float = 0.50  # Must be favored outcome (>= 50% win probability)
    min_edge: float = 0.035  # Minimum edge over market ask
    max_edge: float = DEFAULT_AUTO_BUYER_MAX_EDGE  # Maximum edge (capped at 12% to prevent phantom edges)
    max_daily_spend_usd: float = 25.0  # Daily budget cap
    max_game_stake_usd: float = 2.50  # Max dollars per game/pick
    min_shares: float = 1.0  # Polymarket minimum share size
    whitelisted_models: tuple[str, ...] = DEFAULT_WHITELIST_MODELS
    blacklisted_models: tuple[str, ...] = EXPLICIT_BLACKLIST_MODELS
    disabled_sport_markets: tuple[tuple[str, str], ...] = ()
    execute_live: bool = False  # Dry-run by default unless enabled/requested
    mode: str = DEFAULT_AUTO_BUYER_MODE  # "paper" (simulated fills, recorded) or "live" (real orders)
    max_event_lookahead_hours: float = DEFAULT_MAX_EVENT_LOOKAHEAD_HOURS
    max_model_age_minutes: float = DEFAULT_MAX_MODEL_AGE_MINUTES
    require_model_timestamp: bool = False
    max_daily_loss_usd: float | None = None  # Drawdown circuit breaker; None = disabled
    edge_scaled_sizing_enabled: bool = True  # Optimized: scales stake by edge within the per-game cap
    edge_scaling_min_fraction: float = (
        0.5  # Stake fraction of the per-game cap used at min_edge, when enabled
    )
    min_tennis_edge: float | None = 0.05  # Sport-specific tightened edge for Tennis (5.0%)
    max_tennis_price: float | None = 0.68  # Sport-specific ceiling on market price for Tennis (68¢)
    min_ncaaf_edge: float | None = 0.02  # Sport-specific calibrated edge for NCAAF (2.0%)
    max_ncaaf_edge: float | None = 0.12  # Sport-specific blowout edge cap for NCAAF (12.0%)
    max_moneyline_price: float | None = 0.65  # Ceiling on moneyline buy price (odds <= -185 / implied > 65%)
    auto_quarantine_underperforming: bool = True  # Dynamic circuit breaker against underperforming models
    auto_quarantine_min_picks: int = 15  # Minimum sample before evaluating rolling drawdown
    auto_quarantine_max_loss_roi: float = (
        -0.10
    )  # Auto-quarantine models worse than -10% ROI on rolling window
    max_quote_age_seconds: float = MAX_AUTO_BUYER_QUOTE_AGE_SECONDS
    refresh_stale_quotes: bool = True
    capture_missing_slates: bool = True
    paper_ncaab_spread_v6_trial: bool = False
    ncaab_spread_v6_live_bucket: tuple[float, float] | None = None
    cohorts: tuple[BuyCohort, ...] = ()
    # Cohort passes index only their own book's auto-buyer ledger (see _build_bought_index).
    dedup_own_ledger_only: bool = False
    # C9: re-read the state file before every live submission and stop the pass unless `enabled`
    # is still exactly True, so the kill switch also stops a cycle that is already running.
    recheck_enabled_before_submit: bool = False
    # The account's buying power read once per live cycle (run_auto_buyer_cycle). A live pass stops
    # with an `insufficient_cash` alarm before any order that would take this pass's committed spend
    # above it. None: no cash check (paper, preview and direct test construction).
    available_cash_usd: float | None = None

    def __post_init__(self) -> None:
        if self.execute_live and self.mode != "live":
            object.__setattr__(self, "mode", "live")
        object.__setattr__(self, "cohorts", tuple(self.cohorts))
        # One book per config: an executing config can never carry a paper cohort, and a paper
        # config never a live one, so a paper row has no route to the order path.
        for cohort in self.cohorts:
            if not isinstance(cohort, BuyCohort) or cohort.mode != self.mode:
                raise ValueError(f"cohort {cohort!r} does not belong in a mode={self.mode!r} config")


@dataclass
class AutoExecutionResult:
    total_evaluated: int = 0
    rejected_not_open: int = 0
    whitelisted_count: int = 0
    rejected_not_whitelisted: int = 0
    rejected_not_qualified_decision: int = 0
    rejected_blacklist: int = 0
    rejected_future_slate: int = 0
    rejected_started: int = 0
    rejected_stale_open: int = 0
    rejected_stale_model: int = 0
    rejected_stale_quote: int = 0
    rejected_unmapped_market: int = 0
    rejected_unsupported_market: int = 0
    rejected_disabled_sport_market: int = 0
    rejected_input_unqualified: int = 0
    rejected_closed_market: int = 0
    rejected_below_win_probability: int = 0
    rejected_low_edge: int = 0
    rejected_unrealistic_edge: int = 0
    rejected_budget: int = 0
    rejected_dedup: int = 0
    rejected_min_topup: int = 0
    rejected_daily_loss_breaker: int = 0
    rejected_quarantined_model: int = 0
    rejected_live_calibration: int = 0
    # Live only: the row's tip time and the exchange snapshot's event start disagree by more than
    # TIP_TIME_TOLERANCE_SECONDS, the earlier of the two has passed, or the snapshot has no start.
    rejected_tip_time_mismatch: int = 0
    # Live rows aged 180-360 minutes that passed the model-age gate by the D-03 overnight extension.
    live_overnight_age_extended: int = 0
    cohort_matched: int = 0
    quarantined_models: list[str] = field(default_factory=list)
    submitted_orders: list[dict[str, Any]] = field(default_factory=list)
    dry_run_orders: list[dict[str, Any]] = field(default_factory=list)
    total_spend_usd: float = 0.0
    snapshot_captures: list[dict[str, Any]] = field(default_factory=list)
    # Live-pass alarms (unknown order, unrecorded order, fallback problem) and why live buying
    # stopped early in this pass, if it did.
    alarms: list[dict[str, Any]] = field(default_factory=list)
    live_halt_reason: str | None = None


INPUT_FLAG_FIELDS = ("input_qualified", "weather_inputs_verified")


def _parse_input_flag(value: Any) -> bool | None:
    """Tri-state parse: the ledger stores these flags as the strings "True"/"False"."""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower() if value is not None else ""
    if text in ("true", "1"):
        return True
    if text in ("false", "0"):
        return False
    return None


def _load_pick_input_flags(
    pick_ids: list[str], ledgers_db: Path | None = None
) -> dict[str, dict[str, bool]] | None:
    """Read each pick's model-input flags from the canonical ledger.

    The dashboard pick readers the buyer consumes do not project
    ``feature_payload_json``, so a row the forecast kept out of Main for
    unqualified inputs (stale efficiency data, no pregame weather) reaches the
    buyer looking identical to a clean one. Returns None when the ledger cannot
    be read, so the caller can fail closed.
    """
    db_path = Path(ledgers_db) if ledgers_db is not None else _paths.ledgers_db
    wanted = sorted({pid for pid in pick_ids if pid})
    flags: dict[str, dict[str, bool]] = {}
    if not wanted or not db_path.exists():
        return flags
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        try:
            for start in range(0, len(wanted), 500):
                chunk = wanted[start : start + 500]
                marks = ",".join("?" for _ in chunk)
                query = f"SELECT pick_id, feature_payload_json FROM ledger_records WHERE pick_id IN ({marks})"
                for pick_id, payload_json in conn.execute(query, chunk):
                    try:
                        features = (json.loads(payload_json or "{}") or {}).get("features") or {}
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(features, dict):
                        continue
                    entry = flags.setdefault(str(pick_id), {})
                    for name in INPUT_FLAG_FIELDS:
                        parsed = _parse_input_flag(features.get(name))
                        if parsed is not None:
                            # Main and Flat copies share a pick_id; any copy
                            # saying False wins.
                            entry[name] = entry.get(name, True) and parsed
        finally:
            conn.close()
    except sqlite3.Error:
        logger.warning("auto-buyer could not read pick input flags from %s", db_path, exc_info=True)
        return None
    return flags


def _has_unqualified_inputs(row: dict[str, Any], ledger_flags: dict[str, dict[str, bool]] | None) -> bool:
    """True when the forecast itself flagged this pick's inputs as not qualified.

    Explicit flags only: a pick whose model publishes no such flag is not
    blocked. Totals additionally need verified pregame weather, matching the
    Main ledger's ``pregame_weather_unverified`` rejection. When the ledger is
    unreadable, NFL spread/total picks fail closed because every one of them
    carries the flag.
    """
    sport, market = _auto_buyer_sport_market(row)
    stored = (ledger_flags or {}).get(str(row.get("pick_id") or ""), {})

    def flag(name: str) -> bool | None:
        own = _parse_input_flag(row.get(name))
        return own if own is not None else stored.get(name)

    if flag("input_qualified") is False:
        return True
    if market == "total" and flag("weather_inputs_verified") is False:
        return True
    return (
        ledger_flags is None
        and sport == "nfl"
        and market in ("spread", "total")
        and flag("input_qualified") is None
    )


def _load_pick_calibration_fields(
    pick_ids: list[str], ledgers_db: Path | None = None
) -> dict[str, list[dict[str, Any]]] | None:
    """Each pick's stored raw probability and calibration method, one entry per ledger copy.

    The dashboard rows the buyer reads carry the served ``model_probability`` only: the
    payload's ``model_probability_raw`` and ``calibration_method`` are not dashboard columns.
    The SQLite ``model_probability`` column holds the raw value. Returns None when the ledger
    cannot be read, so a live row fails closed.
    """
    db_path = Path(ledgers_db) if ledgers_db is not None else _paths.ledgers_db
    wanted = sorted({pid for pid in pick_ids if pid})
    fields: dict[str, list[dict[str, Any]]] = {}
    if not wanted:
        return fields
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        try:
            for start in range(0, len(wanted), 500):
                chunk = wanted[start : start + 500]
                marks = ",".join("?" for _ in chunk)
                query = (
                    "SELECT pick_id, model_probability, decision_payload_json FROM ledger_records "
                    f"WHERE pick_id IN ({marks})"
                )
                for pick_id, column_raw, payload_json in conn.execute(query, chunk):
                    try:
                        payload = json.loads(payload_json or "{}")
                    except (TypeError, ValueError):
                        payload = None
                    if not isinstance(payload, dict):
                        payload = {}
                    fields.setdefault(str(pick_id), []).append(
                        {
                            "model_probability_raw": payload.get("model_probability_raw"),
                            "calibration_method": payload.get("calibration_method"),
                            "column_model_probability": column_raw,
                        }
                    )
        finally:
            conn.close()
    except sqlite3.Error:
        logger.warning("auto-buyer could not read pick calibration from %s", db_path, exc_info=True)
        return None
    return fields


def _live_calibration_refusal(row: dict[str, Any], stored: list[dict[str, Any]] | None) -> str | None:
    """Why a live row's ``model_probability`` may not be the raw model output (B7), or None.

    The NCAAB forward calibrates inside the forecast and saves the served value as
    ``model_probability``, so ``calibrate_for_serving`` in the buyer only ever sees that already
    served number. A live cohort's edge window was approved on raw probabilities: refuse a
    non-identity method in any copy, any raw value that differs from the served one, and a row
    whose raw value cannot be found at all.
    """
    copies = [row, *(stored or [])]
    for copy in copies:
        method = str(copy.get("calibration_method") or "").strip()
        if method and method.lower() != "identity":
            return f"calibration_method {method}"
    try:
        served = float(row.get("model_probability"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "model_probability unreadable"
    if not math.isfinite(served):
        return "model_probability unreadable"
    found_raw = False
    for copy in copies:
        for key in ("model_probability_raw", "column_model_probability"):
            value = copy.get(key)
            if value in (None, ""):
                continue
            try:
                raw = float(value)
            except (TypeError, ValueError):
                return f"{key} unreadable"
            if not math.isfinite(raw) or abs(raw - served) > 1e-6:
                return f"{key} {raw} differs from served {served}"
            found_raw = found_raw or key == "model_probability_raw"
    if not found_raw:
        return "model_probability_raw missing"
    return None


def _auto_buyer_sport_market(row: dict[str, Any]) -> tuple[str, str]:
    """Return a normalized sport/market pair for categorical execution gates."""
    model_id = str(row.get("model_id") or row.get("model_version") or "").lower()
    sport = str(row.get("league") or row.get("sport") or "").strip().lower()
    if not sport and model_id.startswith("mlb-"):
        sport = "mlb"
    elif not sport and model_id.startswith("nfl-"):
        sport = "nfl"
    elif not sport and model_id.startswith("tennis-"):
        sport = "tennis"

    market_type = str(row.get("market_type") or "").strip().lower().replace("_", "")
    if market_type == "ml":
        market_type = "moneyline"
    return sport, market_type


# Ids the isolated NCAAB spread paper trial may name. ncaab-spread-v7 (2026-11-21) is the v6 artifact
# with the v7 margin engine; the trial's config file picks exactly one, and the buyer's whitelist
# (set to that one id by run_ncaab_spread_paper_trial) keeps the other out.
NCAAB_SPREAD_TRIAL_MODEL_IDS: tuple[str, ...] = ("ncaab-spread-v6", "ncaab-spread-v7")


def _is_paper_ncaab_spread_v6_trial(row: dict[str, Any], config: AutoExecutionConfig) -> bool:
    """Allow one quarantined model through only in the isolated paper run."""
    model_id = str(row.get("model_id") or row.get("model_version") or "")
    return (
        config.paper_ncaab_spread_v6_trial
        and config.mode == "paper"
        and not config.execute_live
        and model_id in NCAAB_SPREAD_TRIAL_MODEL_IDS
        and _auto_buyer_sport_market(row) == ("ncaab", "spread")
    )


def _game_key(row: dict[str, Any]) -> str:
    """One key per game across markets: event id, else the away/home/date matchup."""
    event_id = str(row.get("event_id") or "").strip()
    if event_id:
        return f"event:{event_id}"
    away = str(row.get("away_team") or "").strip().lower()
    home = str(row.get("home_team") or "").strip().lower()
    start = str(row.get("event_start_utc") or "")[:10]
    return f"matchup:{away}|{home}|{start}" if away and home and start else ""


def _row_in_cohort(row: dict[str, Any], cohort: BuyCohort) -> bool:
    """True when a saved pick row belongs to ``cohort``, with decision-time quote evidence."""
    if cohort.sport not in COHORT_SPORTS or (cohort.sport, cohort.market) in COHORT_BLOCKED_SPORT_MARKETS:
        return False
    if str(row.get("model_id") or row.get("model_version") or "") != cohort.model_id:
        return False
    if _auto_buyer_sport_market(row) != (cohort.sport, cohort.market):
        return False
    if str(row.get("status") or "").lower() != "open":
        return False
    provenance = str(row.get("market_quote_provenance") or "")
    # "decision_time_executable_quote" is the forecast's own timestamped Polymarket quote
    # (cli/forecast.py; NBA rows carry it), held to the same evidence as an archived BBO. A
    # sportsbook decision quote is not executable evidence and is refused.
    if provenance not in {"archived_two_sided_bbo", "decision_time_executable_quote", "indicative_slate"}:
        return False
    if provenance != "indicative_slate":
        if str(row.get("market_quote_timestamp_valid") or "").lower() not in {"true", "1"}:
            return False
        if not all(
            row.get(key)
            for key in (
                "market_quote_observed_at_utc",
                "market_snapshot_hash",
                "market_snapshot_archive_path",
            )
        ):
            return False
    elif str(row.get("market_quote_timestamp_valid") or "").lower() not in {"false", "0"}:
        return False
    try:
        edge = float(row["edge"])
    except (KeyError, TypeError, ValueError):
        return False
    if not cohort.contains(edge):
        return False
    if cohort.market in ("spread", "total"):
        try:
            line = float(row["line"])
        except (KeyError, TypeError, ValueError):
            return False
        if not math.isfinite(line):
            return False
    # Zero-unit rows are unquoted shadow rows with a manufactured edge; sizing would still lift
    # them to a one-share real order. Missing units keep the buyer's normal 1U default.
    units = row.get("units")
    if units is not None and not (isinstance(units, str) and not units.strip()):
        try:
            parsed_units = float(units)
        except (TypeError, ValueError):
            return False
        if not (math.isfinite(parsed_units) and parsed_units > 0):
            return False
    return True


def _legacy_ncaab_spread_v6_cohort(config: AutoExecutionConfig) -> BuyCohort | None:
    bucket = config.ncaab_spread_v6_live_bucket
    if not (bucket and config.execute_live and config.mode == "live"):
        return None
    return BuyCohort("ncaab-spread-v6", "ncaab", "spread", bucket[0], bucket[1], "live")


def _is_live_ncaab_spread_v6_bucket(row: dict[str, Any], config: AutoExecutionConfig) -> bool:
    """Exact operator-approved live cohort, with decision-time quote evidence."""
    cohort = _legacy_ncaab_spread_v6_cohort(config)
    return cohort is not None and _row_in_cohort(row, cohort)


def _match_cohort(row: dict[str, Any], config: AutoExecutionConfig) -> BuyCohort | None:
    """The config's cohort this row belongs to (state cohorts first, then the legacy v6 bucket)."""
    for cohort in config.cohorts:
        if _row_in_cohort(row, cohort):
            return cohort
    legacy = _legacy_ncaab_spread_v6_cohort(config)
    if legacy is not None and _row_in_cohort(row, legacy):
        return legacy
    return None


def _ncaab_spread_v6_live_bucket_from_state(state: dict[str, Any]) -> tuple[float, float] | None:
    raw = state.get("ncaab_spread_v6_live_bucket")
    if not isinstance(raw, dict) or raw.get("enabled") is not True:
        return None
    try:
        minimum = float(raw["min_saved_edge"])
        maximum = float(raw["max_saved_edge_exclusive"])
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (minimum, maximum)):
        return None
    if not (minimum == 0.05 and maximum == 0.08):
        return None
    return minimum, maximum


def _is_disabled_auto_buyer_sport_market(
    row: dict[str, Any],
    disabled_markets: tuple[tuple[str, str], ...] | frozenset[tuple[str, str]] = (),
) -> bool:
    target = _auto_buyer_sport_market(row)
    if target[0] in (
        "ncaab",
        "hockey",
        "nhl",
        "darts",
        "euroleague",
        "nbl",
        "intl_basketball",
        "tennis",
        "soccer",
    ):
        return True
    if target in DISABLED_AUTO_BUYER_SPORT_MARKETS:
        return True
    return bool(disabled_markets and target in disabled_markets)


def _capture_missing_active_snapshot_slates(
    picks: list[dict[str, Any]],
    *,
    config: AutoExecutionConfig,
    now: datetime,
    data_root: Path | str = DATA,
    client: Any | None = None,
    capture_fn: Callable[..., dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Capture absent sport/date snapshot files needed by the active 24-hour slate.

    The daily forecast captures its requested calendar date, while esports forecasts
    can legitimately include games after midnight Eastern. A live Auto-Buyer cycle
    must discover those next-day contracts before calling the local fail-closed
    mapper. Existing files are never rewritten or broadly refreshed here.
    """
    # Paper mode must capture quotes too. This function snapshots market data
    # only; it never submits an order.
    if config.mode not in AUTO_BUYER_MODES:
        return []

    root = Path(data_root)
    if root.resolve() == (_paths.repo_root / "data").resolve():
        root = _paths.runtime_root
    eastern = ZoneInfo("America/New_York")
    missing: set[tuple[str, str]] = set()
    for row in picks:
        model_id = str(row.get("model_id") or row.get("model_version") or "")
        in_cohort = _match_cohort(row, config) is not None
        if not in_cohort and (
            model_id in config.blacklisted_models or model_id not in config.whitelisted_models
        ):
            continue
        if not in_cohort and _is_disabled_auto_buyer_sport_market(row, config.disabled_sport_markets):
            continue
        if row.get("status") != "open":
            continue
        market_type = str(row.get("market_type") or "").lower()
        if market_type and market_type not in EXECUTABLE_AUTO_BUYER_MARKET_TYPES:
            continue
        try:
            event_start = parse_utc(str(row.get("event_start_utc") or ""))
        except (TypeError, ValueError):
            continue
        seconds_until_start = (event_start - now).total_seconds()
        if seconds_until_start <= 0 or seconds_until_start > config.max_event_lookahead_hours * 3600:
            continue
        raw_sport = str(row.get("league") or row.get("sport") or "").lower()
        if raw_sport in ESPORTS_LEAGUES:
            sport = "esports"
        elif raw_sport in ("euroleague", "nbl"):
            sport = "basketball"
        elif raw_sport in ("cfb", "college_football"):
            sport = "ncaaf"
        else:
            sport = raw_sport
        if not sport:
            continue
        game_date = event_start.astimezone(eastern).date().isoformat()
        snapshot_path = root / "odds" / sport / game_date / "polymarket_snapshots.jsonl"
        if not snapshot_path.exists():
            runtime_snap = _paths.runtime_root / "odds" / sport / game_date / "polymarket_snapshots.jsonl"
            if runtime_snap.exists():
                snapshot_path = runtime_snap
        is_missing = not snapshot_path.exists()
        is_stale = False
        max_quote_age = (
            config.max_quote_age_seconds
            if hasattr(config, "max_quote_age_seconds")
            else MAX_AUTO_BUYER_QUOTE_AGE_SECONDS
        )
        if not is_missing:
            try:
                age_seconds = now.timestamp() - snapshot_path.stat().st_mtime
                if age_seconds > max_quote_age and seconds_until_start <= 12 * 3600:
                    is_stale = True
            except OSError:
                pass
        if is_missing or is_stale:
            missing.add((sport, game_date))

    if not missing:
        return []

    from model_prediction.cli.settle import _extract_market_slug
    from model_prediction.data_sources.polymarket_us import (
        PolymarketUSClient,
        capture_slate_snapshots,
    )

    open_slugs = {slug for r in picks if (slug := _extract_market_slug(str(r.get("rationale", "")), r))}

    market_client = client or PolymarketUSClient()
    capture = capture_fn or capture_slate_snapshots
    results: list[dict[str, Any]] = []
    for sport, game_date in sorted(missing):
        try:
            slate = market_client.sport_slate(
                sport,
                date.fromisoformat(game_date),
                "America/New_York",
            )
            capture_kwargs: dict[str, Any] = {
                "horizon_hours": config.max_event_lookahead_hours,
                "ladder_keep": 2,
                "ladder_leagues": frozenset({"NBA", "NCAAF", "NCAAB", "NFL", "NHL", "AHL"}),
                "ladder_keep_slugs": frozenset(open_slugs),
            }
            try:
                capture_result = capture(market_client, slate.events, root, game_date, **capture_kwargs)
            except TypeError:
                capture_result = capture(market_client, slate.events, root, game_date)
            results.append(
                {
                    "sport": sport,
                    "game_date": game_date,
                    "status": capture_result.get("status"),
                    "captured": int(capture_result.get("captured") or 0),
                    "league_errors": dict(slate.errors),
                }
            )
        except (httpx.HTTPError, OSError, RuntimeError, TypeError, ValueError) as error:
            results.append(
                {
                    "sport": sport,
                    "game_date": game_date,
                    "status": "error",
                    "captured": 0,
                    "error": str(error)[:200],
                }
            )
    return results


AUTO_BUYER_CYCLE_HISTORY_PATH = DATA / "logs" / "auto_buyer_cycle_history.jsonl"

# D-01 (operator, 2026-11-25): a live IOC remainder rests GOOD_TILL_DATE and expires this long before
# the event start, so nothing the buyer placed can rest into a live game.
LIVE_FALLBACK_EXPIRY_LEAD_SECONDS = 90
# B5: a live order is refused when the live ask has moved more than this from the ticket price.
LIVE_MAX_PRICE_DEVIATION = 0.02
# D-03 (operator, 2026-11-25): a live cohort buys only a pick whose model timestamp is at most this
# old. The global max_model_age_minutes (720) still governs paper and legacy passes.
LIVE_COHORT_MAX_MODEL_AGE_MINUTES = 180.0
# D-03 overnight extension (operator, 2026-11-25): a live row up to this old is still bought when no
# daily run that could have re-priced it has started since it was written. The last daily run is
# 21:00 ET and tips run past midnight, so late tips otherwise never have a pick under 180 minutes.
LIVE_COHORT_OVERNIGHT_MAX_MODEL_AGE_MINUTES = 360.0
# Daily runs read per cycle: about two days of the 11-a-day schedule. The extension also requires a
# run at or before the pick, so a truncated history fails closed.
_DAILY_RUN_HISTORY_LIMIT = 40
# game_exposure key suffix for a pick's still-pending (unfilled) exposure; see _build_bought_index.
_PENDING_EXPOSURE_KEY_SUFFIX = "#pending"
# fallback_status values that need the operator's eye but leave the placed IOC itself intact.
_FALLBACK_ALARM_STATUSES = frozenset(
    {"rejected", "submit_unknown", "cancelled_unverified_gtd", "unverified_gtd_cancel_failed"}
)


def _read_daily_run_history(paths: RuntimePaths | None = None) -> list[dict[str, Any]] | None:
    """Recent daily runs from runs.db, newest first; None when runs.db cannot be read."""
    from model_prediction.run_supervisor import RunSupervisor

    paths = paths or _paths
    # RunSupervisor.conn creates a missing runs.db, and an empty history would read as "no run
    # started after the pick".
    if not paths.runs_db.is_file():
        return None
    supervisor = RunSupervisor(paths=paths)
    try:
        return supervisor.latest_runs(worker="daily", limit=_DAILY_RUN_HISTORY_LIMIT)
    except (sqlite3.Error, OSError):
        logger.warning("daily run history unreadable; live picks keep the 180-minute limit", exc_info=True)
        return None
    finally:
        supervisor.close()


def _daily_clear_retires(run_date: str, created_at_utc: str, event_start_utc: str) -> bool:
    """Whether a daily run for ``run_date`` (ET) supersedes, and so re-prices, this open row.

    Mirrors ``cli.commands._clear_today_open(by_event_date=True)``: the row matches when the ET
    calendar date of its created or event timestamp equals the run date; a missing or naive timestamp
    never matches. Since 2026-11-25 that clear compares parsed ET dates rather than raw UTC prefixes,
    so every later run on the date a row was written re-prices it, including the 21:00 ET run for a
    19:44 ET pick. ``test_overnight_rule_mirrors_the_daily_clear`` pins the two together.
    """

    def _et_date(value: str) -> str | None:
        try:
            return parse_utc(value).astimezone(EASTERN).date().isoformat()
        except ValueError:
            return None

    return run_date in (_et_date(created_at_utc), _et_date(event_start_utc))


class _OvernightAgeExtension:
    """D-03 overnight extension for live rows aged 180-360 minutes, evaluated once per pass.

    A row qualifies only when it is the freshest pick the scheduler could have produced: it is the
    newest open row of its (model, event, market) and no daily run that would re-price it has
    started after it was written. Every later run on the row's ET creation date re-prices it, so in
    practice only a row written by the last run of its date (21:00 ET) qualifies. Such a run counts
    whether it finished, failed or is still running; a lock skip never forecast and does not count.
    Neither does a run on a later ET date than the row's slate: the 00:30 run forecasts the next
    date, and when its clear matches a post-midnight tip by event date without re-forecasting it,
    restore reopens the same row. runs.db and the slot index are read lazily on the first row that
    needs them, after the pass captured ``now``.
    """

    def __init__(self, picks: list[dict[str, Any]]) -> None:
        self._picks = picks
        self._loaded = False
        self._runs: list[tuple[datetime, str]] | None = None
        self._newest_by_slot: dict[tuple[str, str, str], datetime] = {}

    @staticmethod
    def _slot(row: dict[str, Any]) -> tuple[str, str, str]:
        model_id = str(row.get("model_id") or row.get("model_version") or "")
        return (model_id, str(row.get("event_id") or ""), str(row.get("market_type") or "").lower())

    def _load(self) -> None:
        self._loaded = True
        history = _read_daily_run_history()
        if history is not None:
            try:
                self._runs = [
                    (parse_utc(str(run["started_at_utc"])), str(run.get("status") or "")) for run in history
                ]
            except (KeyError, TypeError, ValueError):
                self._runs = None
        for pick in self._picks:
            if pick.get("status") != "open":
                continue
            try:
                created = parse_utc(str(pick.get("created_at_utc") or ""))
            except (TypeError, ValueError):
                continue
            slot = self._slot(pick)
            if slot not in self._newest_by_slot or created > self._newest_by_slot[slot]:
                self._newest_by_slot[slot] = created

    def allows(self, row: dict[str, Any]) -> bool:
        if not self._loaded:
            self._load()
        created_raw = str(row.get("created_at_utc") or "")
        try:
            created = parse_utc(created_raw)
        except (TypeError, ValueError):
            return False
        # An empty or truncated history cannot show what ran after the pick.
        if not self._runs or not any(started <= created for started, _status in self._runs):
            return False
        if self._newest_by_slot.get(self._slot(row), created) > created:
            return False
        slate_date = created.astimezone(EASTERN).date()
        event_raw = str(row.get("event_start_utc") or "")
        for started, status in self._runs:
            if started <= created or status == "skipped":
                continue
            run_date = started.astimezone(EASTERN).date()
            if run_date > slate_date:
                continue
            if _daily_clear_retires(run_date.isoformat(), created_raw, event_raw):
                return False
        return True


class LiveAutoBuyerAlarm(RuntimeError):
    """A live pass raised an alarm (unknown order, unrecorded order, ...). Raised by
    ``run_auto_buyer_cycle`` after state and history are saved, so the scheduled run is marked
    failed; ``run_summary`` is the cycle's full summary."""

    def __init__(self, message: str, *, run_summary: dict[str, Any], alarms: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.run_summary = run_summary
        self.alarms = alarms


def _append_auto_buyer_cycle_history(
    run_summary: dict[str, Any],
    history_path: Path | str = AUTO_BUYER_CYCLE_HISTORY_PATH,
) -> None:
    """Append one trimmed row per cycle so funnel/rejection trends are queryable directly.

    Previously the only way to see how a cycle's rejection breakdown compared to prior
    cycles was to parse the raw operational log and ledger by hand. ``orders`` (the full
    per-order payload) is dropped here -- the ledger jsonl is already the durable record
    of what was bought -- so this file stays a lightweight, append-only funnel history.
    """
    row = {k: v for k, v in run_summary.items() if k not in ("orders", "snapshot_captures", "reconciliation")}
    path = Path(history_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
    except OSError as exc:
        logger.warning("Failed to append auto_buyer_cycle_history.jsonl: %s", exc)


def _pending_exposure_cost(row: dict[str, Any]) -> float:
    """Dollars an open order of this row may still add on the exchange, beyond its recorded fill.

    One helper for the daily cap, the per-game cap and the pending-orders check (C6), so none of
    them double-counts: reconcile folds a resting fallback's partial fill into ``cost_usd`` (and
    ``fallback_filled_shares``), and an unknown-fill primary's into ``shares``, so only the unfilled
    remainder is still pending. An open unknown submission counts its full requested cost and an
    unknown fallback leg its remainder: either may exist on the exchange.
    """
    try:
        price = float(row.get("entry_price") or 0.0)
        resting = float(row.get("fallback_resting_shares") or 0.0)
        if row.get("submit_unknown") is True:
            if str(row.get("status") or "open").lower() != "open":
                return 0.0
            cost = float(row.get("requested_cost_usd") or 0.0)
        elif is_fallback_submit_unknown(row):
            cost = resting * price
        elif not row.get("fallback_order_id") or row.get("fallback_reconciled"):
            return 0.0
        elif str(row.get("fallback_order_id")) == str(row.get("order_id") or ""):
            # An unknown-fill primary: reconcile restates its fills into ``shares``.
            cost = max(0.0, resting - float(row.get("shares") or 0.0)) * price
        else:
            cost = max(0.0, resting - float(row.get("fallback_filled_shares") or 0.0)) * price
    except (TypeError, ValueError):
        return 0.0
    return cost if math.isfinite(cost) and cost > 0 else 0.0


def _live_buying_still_enabled() -> bool:
    """C9: the kill switch as saved right now; an unreadable state fails toward stopping."""
    try:
        return load_auto_buyer_state().get("enabled") is True
    except Exception:
        logger.exception("Auto-buyer state unreadable before a live submission; stopping live buying")
        return False


def _alarm(
    kind: str, *, pick_id: str, market_slug: str, detail: str, order_ids: Any = None
) -> dict[str, Any]:
    return {
        "alarm": kind,
        "pick_id": pick_id,
        "market_slug": market_slug,
        "order_ids": [str(o) for o in (order_ids or []) if o],
        "detail": str(detail)[:500],
        "at_utc": iso_utc(utc_now()),
    }


# Every timing gate reads the row's ESPN tip time; the exchange's own event start is the second
# opinion. Five minutes absorbs the two feeds' rounding, not a rescheduled game.
TIP_TIME_TOLERANCE_SECONDS = 300.0


def _tip_time_refusal(row_start: datetime, exchange_start_raw: Any, now: datetime) -> str | None:
    """Why a live buy must not go out on these two start times, or None when they agree and neither
    has passed. A snapshot with no parseable start refuses (every NCAAB snapshot row carries one)."""
    try:
        exchange_start = parse_utc(str(exchange_start_raw or ""))
    except (TypeError, ValueError):
        return "exchange snapshot has no event start; tip time unverified"
    gap = abs((exchange_start - row_start).total_seconds())
    if gap > TIP_TIME_TOLERANCE_SECONDS:
        return (
            f"tip time mismatch: row {iso_utc(row_start)} vs exchange {iso_utc(exchange_start)} "
            f"({gap / 60:.1f} min)"
        )
    if now >= min(row_start, exchange_start):
        return f"event started at {iso_utc(min(row_start, exchange_start))} (earlier of row and exchange)"
    return None


# _request does not retry a 5xx, and the gateway answered 503 five times in the 2026-11-25 logs: one
# blip must not block a cycle's live buying and fail the run.
LIVE_CASH_CHECK_RETRY_DELAYS = (0.0, 1.0, 3.0)


def _live_cash_check() -> dict[str, Any]:
    """Read the account's buying power once for this cycle (read-only GET, no order).

    ``status``: ``ok`` with ``available_cash_usd``; ``auth_error`` (401/403 or missing credentials);
    ``unavailable`` (network, 5xx, or no parseable USD buying power -- unknown, never zero). Anything
    but ``ok`` stops live buying for the cycle: a live pass never trades on an unknown balance.
    """
    outcome: dict[str, Any] = {"status": "unavailable", "detail": "balance not read"}
    for attempt, delay in enumerate(LIVE_CASH_CHECK_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            executor = PolymarketExecutor(audit=AuditLog(DATA / "audit.jsonl"), confirm=lambda _prompt: "N")
            balance = executor.account_balance()
        except ExecutionGateError as exc:
            if classify_gate_refusal(exc) == "auth":
                # Credentials do not heal between retries; fail at once.
                return {"status": "auth_error", "detail": str(exc)[:300], "attempts": attempt + 1}
            outcome = {"status": "unavailable", "detail": str(exc)[:300], "attempts": attempt + 1}
            continue
        except (
            httpx.HTTPError,
            OSError,
            ValueError,
            KeyError,
            TypeError,
            RuntimeError,
            AttributeError,
        ) as exc:
            detail = f"{type(exc).__name__}: {exc}"[:300]
            outcome = {"status": "unavailable", "detail": detail, "attempts": attempt + 1}
            continue
        if balance.get("available_cash_usd") is None:
            # A well-formed answer without buying power will not change on a retry.
            return {
                "status": "unavailable",
                "detail": "balance response carried no USD buying power",
                **balance,
            }
        return {"status": "ok", **balance}
    return outcome


def _auth_or_balance_refusal_alarm(orders: list[dict[str, Any]]) -> dict[str, Any] | None:
    """An alarm when a live pass sent orders and every one the exchange answered was refused for
    auth, credentials or balance (and none was placed). Refusals by our own gates before any request
    (stale quote, side check, started game) are not exchange answers and are left out of the count."""
    exchange_answers = [
        o
        for o in orders
        if o.get("status") != "refused" or o.get("refusal_kind") or o.get("http_status") is not None
    ]
    if not exchange_answers or any(
        o.get("refusal_kind") not in ("auth", "balance") for o in exchange_answers
    ):
        return None
    kinds = sorted({str(o["refusal_kind"]) for o in exchange_answers})
    return _alarm(
        "live_orders_refused_" + "_and_".join(kinds),
        pick_id=",".join(str(o.get("pick_id") or "") for o in exchange_answers)[:200],
        market_slug=",".join(str(o.get("market_slug") or "") for o in exchange_answers)[:200],
        detail=str(exchange_answers[0].get("error") or ""),
    )


def _live_book_has_pending_orders(ledger_path: Path) -> bool:
    """An unreconciled resting fallback, an open row whose fill is unknown (the selection
    ``reconcile_pending_auto_buyer_fallbacks`` works on), or an open fallback leg whose outcome is
    unknown. An unreadable ledger counts as pending (manage the book)."""
    from model_prediction.portfolio.auto_buyer_ledger import read_auto_buyer_ledger

    try:
        rows = read_auto_buyer_ledger(jsonl_path=ledger_path)
    except (OSError, ValueError):
        return True
    return any(
        (row.get("fallback_order_id") and not row.get("fallback_reconciled"))
        or (str(row.get("status", "")).lower() == "open" and row.get("fill_known") is False)
        or is_fallback_submit_unknown(row)
        or _pending_exposure_cost(row) > 0
        for row in rows
    )


def _today_auto_buyer_totals(data_root: Path | str = DATA) -> tuple[float, float]:
    """Sum today's (ET calendar day) already-executed spend and realized P&L.

    Computed fresh from the ledger rather than reset per call, so the daily-spend
    cap and the drawdown circuit breaker both apply across a full calendar day's
    worth of cycles -- not just whichever cycle happens to be running right now.
    """
    from model_prediction.portfolio.auto_buyer_ledger import read_auto_buyer_ledger

    today = utc_now().astimezone(EASTERN).date()
    spend = 0.0
    realized_pnl = 0.0
    j_path = Path(data_root) / "auto_buyer_ledger.jsonl"
    for row in read_auto_buyer_ledger(jsonl_path=j_path):
        executed_raw = row.get("executed_at_utc")
        # Hand-placed positions are imported for visibility, not buyer spend.
        if executed_raw and not row.get("manual"):
            try:
                if parse_utc(str(executed_raw)).astimezone(EASTERN).date() == today:
                    spend += float(row.get("cost_usd") or 0.0) + _pending_exposure_cost(row)
            except (TypeError, ValueError):
                pass

        settled_raw = row.get("settled_at_utc")
        result = str(row.get("result") or "").lower()
        if settled_raw and result in ("win", "loss", "push"):
            try:
                if parse_utc(str(settled_raw)).astimezone(EASTERN).date() == today:
                    realized_pnl += float(row.get("pnl_usd") or 0.0)
            except (TypeError, ValueError):
                pass

    return round(spend, 2), round(realized_pnl, 2)


def _parse_disabled_sport_markets(state: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """Parse the persisted ``disabled_sport_markets`` state entry into sport/market pairs.

    Single source of truth for both production config-builders (``run_auto_buyer_cycle``
    and ``AutoPolymarketBuyer.__init__``'s fallback) so a categorical block like the
    soccer/MLB disable can't silently apply on one path and not the other.
    """
    disabled_parsed: list[tuple[str, str]] = []
    for entry in state.get("disabled_sport_markets") or []:
        if isinstance(entry, str) and ":" in entry:
            sp, mk = entry.split(":", 1)
            disabled_parsed.append((sp.strip().lower(), mk.strip().lower()))
        elif isinstance(entry, (list, tuple)) and len(entry) == 2:
            disabled_parsed.append((str(entry[0]).strip().lower(), str(entry[1]).strip().lower()))
    return tuple(disabled_parsed)


STATE_VERSION_KEY = "_state_version"  # monotonic save counter for read-modify-write conflict detection


class StateVersionConflictError(RuntimeError):
    """Raised when a state file's on-disk ``_state_version`` advanced past the
    version this process read, meaning another writer (dashboard, scheduled
    worker, operator hand-edit) saved in between this process's load and save.
    The caller must reload fresh state and retry rather than blindly overwrite it.
    """


def _coerce_state_version(value: Any) -> int:
    """Legacy files have no ``_state_version`` key; a hand-edited or corrupt
    value (string, bool, negative, float) is not a trustworthy version either --
    both cases fall back to 0, the backward-compatible default."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int) and value >= 0:
        return value
    return 0


def read_state_file_version(path: Path) -> int:
    """Read the live on-disk ``_state_version`` without disturbing the caller's
    own (possibly stale) in-memory copy. Any failure to read it -- missing file,
    unreadable bytes, invalid JSON, or a JSON value that isn't an object -- is
    treated as version 0 so a missing/older live file never blocks a save."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    if not isinstance(data, dict):
        return 0
    return _coerce_state_version(data.get(STATE_VERSION_KEY))


# Leaf lock around the state file's compare-and-replace: one small write, so a long wait means a stuck
# writer, and the save fails loudly instead of hanging the caller.
STATE_FILE_LOCK_WAIT_SECONDS = 30.0


def write_versioned_state_file(path: Path, state: dict[str, Any], *, temp_path: Path) -> int:
    """Atomically save ``state`` to ``path``, bumping its ``_state_version``.

    Raises ``StateVersionConflictError`` when the live file's version is
    strictly greater than the version this ``state`` dict was read at -- not
    ``!=``, because ``load_auto_buyer_state`` can seed from the repo copy when
    the runtime file is missing, so a missing or older live file must never
    raise. The version check closes the read-modify-write window that spans a
    whole multi-minute cycle; the re-read/compare/replace itself runs under
    ``<name>.lock``, so two near-simultaneous writers (a cycle ending while the
    dashboard kill switch saves) can never both pass the compare at one version
    or interleave on the shared temp file. That lock is a leaf (see
    auto_buyer_lock.py): nothing else is acquired while it is held, and it is
    held for one small file write, so the kill switch never waits on a cycle.
    """
    expected = _coerce_state_version(state.get(STATE_VERSION_KEY))
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = acquire_lock(path.with_name(path.name + ".lock"), wait_seconds=STATE_FILE_LOCK_WAIT_SECONDS)
    if lock is None:
        raise TimeoutError(
            f"{path} is held by another writer past {STATE_FILE_LOCK_WAIT_SECONDS:.0f}s; not saved"
        )
    try:
        live = read_state_file_version(path)
        if live > expected:
            raise StateVersionConflictError(
                f"{path} advanced to version {live} (read at {expected}); reload and retry"
            )
        new_version = expected + 1
        temp_path.write_text(
            json.dumps({**state, STATE_VERSION_KEY: new_version}, indent=2) + "\n", encoding="utf-8"
        )
        temp_path.replace(path)
    finally:
        release_lock(lock)
    # Reflect the bumped version back onto the caller's dict so a second save
    # of the same object (e.g. two sequential set_* calls) does not self-conflict.
    state[STATE_VERSION_KEY] = new_version
    return new_version


def load_auto_buyer_state() -> dict[str, Any]:
    """Load persistent auto-buyer configuration and runtime toggle state."""
    default_state = {
        "enabled": False,
        "mode": DEFAULT_AUTO_BUYER_MODE,
        "unit_value_usd": DEFAULT_AUTO_BUYER_UNIT_VALUE_USD,
        "min_model_probability": 0.50,
        "min_edge": 0.035,
        "max_edge": DEFAULT_AUTO_BUYER_MAX_EDGE,
        "max_daily_spend_units": DEFAULT_MAX_DAILY_SPEND_UNITS,
        "max_game_stake_units": DEFAULT_MAX_GAME_STAKE_UNITS,
        "max_event_lookahead_hours": DEFAULT_MAX_EVENT_LOOKAHEAD_HOURS,
        "max_model_age_minutes": DEFAULT_MAX_MODEL_AGE_MINUTES,
        "whitelist_models": list(DEFAULT_WHITELIST_MODELS),
        "blacklist_models": list(EXPLICIT_BLACKLIST_MODELS),
        "disabled_sport_markets": [
            f"{sport}:{market}" for sport, market in sorted(DISABLED_AUTO_BUYER_SPORT_MARKETS)
        ],
        "last_run": None,
        "last_daily_date": None,
        STATE_VERSION_KEY: 0,
    }
    data: dict[str, Any] = {}
    state_file = AUTO_BUYER_STATE_FILE
    canonical_runtime_file = _paths.runtime_root / "auto_buyer_state.json"
    repo_seed_file = _paths.repo_root / "data" / "auto_buyer_state.json"
    if (
        not state_file.exists()
        and state_file == canonical_runtime_file
        and _paths.runtime_root == DATA
        and repo_seed_file.exists()
    ):
        state_file = repo_seed_file
    if state_file.exists():
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    state = {**default_state, **data}
    normalized_mode = str(state.get("mode") or DEFAULT_AUTO_BUYER_MODE).lower()
    state["mode"] = normalized_mode if normalized_mode in AUTO_BUYER_MODES else DEFAULT_AUTO_BUYER_MODE
    state[STATE_VERSION_KEY] = _coerce_state_version(state.get(STATE_VERSION_KEY))
    try:
        unit_value = float(state.get("unit_value_usd") or DEFAULT_AUTO_BUYER_UNIT_VALUE_USD)
    except (TypeError, ValueError):
        unit_value = DEFAULT_AUTO_BUYER_UNIT_VALUE_USD
    if not math.isfinite(unit_value) or unit_value <= 0:
        unit_value = DEFAULT_AUTO_BUYER_UNIT_VALUE_USD

    try:
        max_edge = float(state.get("max_edge") or DEFAULT_AUTO_BUYER_MAX_EDGE)
    except (TypeError, ValueError):
        max_edge = DEFAULT_AUTO_BUYER_MAX_EDGE
    if not math.isfinite(max_edge) or max_edge <= 0:
        max_edge = DEFAULT_AUTO_BUYER_MAX_EDGE
    state["max_edge"] = max_edge

    def _unit_limit(unit_key: str, legacy_usd_key: str, default_units: float) -> float:
        raw_units = data.get(unit_key)
        if raw_units is None and data.get(legacy_usd_key) is not None:
            try:
                raw_units = float(data[legacy_usd_key]) / unit_value
            except (TypeError, ValueError, ZeroDivisionError):
                raw_units = None
        try:
            parsed = float(raw_units if raw_units is not None else default_units)
        except (TypeError, ValueError):
            parsed = default_units
        return parsed if math.isfinite(parsed) and parsed > 0 else default_units

    daily_units = _unit_limit(
        "max_daily_spend_units",
        "max_daily_spend_usd",
        DEFAULT_MAX_DAILY_SPEND_UNITS,
    )
    game_units = _unit_limit(
        "max_game_stake_units",
        "max_game_stake_usd",
        DEFAULT_MAX_GAME_STAKE_UNITS,
    )
    state["unit_value_usd"] = unit_value
    state["max_daily_spend_units"] = daily_units
    state["max_game_stake_units"] = game_units
    state["max_daily_spend_usd"] = round(daily_units * unit_value, 2)
    state["max_game_stake_usd"] = round(game_units * unit_value, 2)
    try:
        lookahead = float(state.get("max_event_lookahead_hours") or DEFAULT_MAX_EVENT_LOOKAHEAD_HOURS)
    except (TypeError, ValueError):
        lookahead = DEFAULT_MAX_EVENT_LOOKAHEAD_HOURS
    state["max_event_lookahead_hours"] = (
        lookahead if math.isfinite(lookahead) and lookahead > 0 else DEFAULT_MAX_EVENT_LOOKAHEAD_HOURS
    )
    try:
        model_age = float(state.get("max_model_age_minutes") or DEFAULT_MAX_MODEL_AGE_MINUTES)
    except (TypeError, ValueError):
        model_age = DEFAULT_MAX_MODEL_AGE_MINUTES
    state["max_model_age_minutes"] = (
        model_age if math.isfinite(model_age) and model_age > 0 else DEFAULT_MAX_MODEL_AGE_MINUTES
    )
    try:
        raw_tennis_edge = state.get("min_tennis_edge")
        min_tennis_edge = float(raw_tennis_edge) if raw_tennis_edge is not None else None
    except (TypeError, ValueError):
        min_tennis_edge = None
    state["min_tennis_edge"] = (
        min_tennis_edge
        if (min_tennis_edge is not None and math.isfinite(min_tennis_edge) and min_tennis_edge > 0)
        else None
    )

    try:
        raw_tennis_price = state.get("max_tennis_price")
        max_tennis_price = float(raw_tennis_price) if raw_tennis_price is not None else None
    except (TypeError, ValueError):
        max_tennis_price = None
    state["max_tennis_price"] = (
        max_tennis_price
        if (max_tennis_price is not None and math.isfinite(max_tennis_price) and 0 < max_tennis_price < 1)
        else None
    )

    return state


def save_auto_buyer_state(state: dict[str, Any]) -> None:
    """Save auto-buyer toggle state and settings atomically.

    Raises ``StateVersionConflictError`` if another writer advanced the file
    on disk since ``state`` was loaded -- see ``write_versioned_state_file``.
    """
    write_versioned_state_file(
        AUTO_BUYER_STATE_FILE, state, temp_path=AUTO_BUYER_STATE_FILE.with_suffix(".json.tmp")
    )


def toggle_auto_buyer(enabled: bool | None = None) -> dict[str, Any]:
    """Toggle Auto-Buyer ON or OFF from the dashboard or API."""
    # Only the booleans true/false are accepted: bool("false") is True, and this switch gates
    # scheduled real-money orders. A bare toggle of a non-boolean saved value fails safe to off.
    if enabled is not None and not isinstance(enabled, bool):
        raise ValueError(f"Auto-Buyer enabled must be true or false, got {enabled!r}")
    state = load_auto_buyer_state()
    if enabled is None:
        state["enabled"] = state.get("enabled") is False
    else:
        state["enabled"] = enabled
    state["updated_at_utc"] = iso_utc(utc_now())
    save_auto_buyer_state(state)
    if state["enabled"] is False:
        # D-02: turning the buyer off also cancels the live book's resting fallbacks by order id,
        # right now and best effort, after the save so a failed cancel never blocks or undoes the
        # kill switch. Cancel-only (no ledger write): this runs in the dashboard process without
        # auto_buyer.lock; every disabled cycle, daily reconcile and the backstop cancel again.
        try:
            from .auto_buyer_ledger import cancel_resting_live_fallbacks

            state["kill_switch_cancel"] = cancel_resting_live_fallbacks(
                data_root=DATA, cancel_all=True, reason="kill_switch"
            )
        except Exception as exc:  # reported to the operator, never raised
            logger.exception("Kill switch saved, but cancelling resting live fallbacks failed")
            state["kill_switch_cancel"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:300]}
    return state


def _live_drawdown(state: dict[str, Any]) -> dict[str, Any]:
    """The advisory live-cohort drawdown report (D-06). Never raises."""
    try:
        from .auto_buyer_drawdown import live_cohort_drawdown_report

        # DATA is read at call time: tests and the dashboard's own process point it elsewhere.
        return live_cohort_drawdown_report(state, data_root=DATA)
    except Exception as exc:  # advisory only; must never break the status page or a buy cycle
        logger.warning("Live cohort drawdown report failed", exc_info=True)
        return {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}"[:300],
            "cohorts": [],
            "warnings": [],
        }


def auto_buyer_status() -> dict[str, Any]:
    """Saved state plus what the dashboard needs to show real-money trading truthfully (C1, C2, DD).

    ``live_trading_active`` holds only when ``enabled`` is the boolean true and a live book trades:
    an active live cohort, or the legacy ungoverned ``mode: live`` (no ``cohorts`` key at all).
    """
    state = load_auto_buyer_state()
    try:
        live_cohorts = [c.label for c in _parse_cohorts(state).active if c.mode == "live"]
    except Exception:  # a malformed state must still render the kill switch
        logger.warning("Could not parse auto-buyer cohorts for the status page", exc_info=True)
        live_cohorts = []
    legacy_live = COHORTS_STATE_KEY not in state and str(state.get("mode") or "").lower() == "live"
    return {
        **state,
        "live_cohorts": live_cohorts,
        "live_trading_active": state.get("enabled") is True and (bool(live_cohorts) or legacy_live),
        "live_drawdown": _live_drawdown(state),
    }


def set_auto_buyer_mode(raw_mode: Any) -> dict[str, Any]:
    """Switch the Auto-Buyer between "paper" (simulated, recorded fills) and
    "live" (real Polymarket orders). Never places or cancels a real order by
    itself -- it only changes which path future cycles take."""
    mode = str(raw_mode or "").strip().lower()
    if mode not in AUTO_BUYER_MODES:
        raise ValueError(f"Auto-Buyer mode must be one of {sorted(AUTO_BUYER_MODES)}")

    state = load_auto_buyer_state()
    previous = state.get("mode", DEFAULT_AUTO_BUYER_MODE)
    state["mode"] = mode
    state["updated_at_utc"] = iso_utc(utc_now())
    save_auto_buyer_state(state)
    try:
        AuditLog(DATA / "audit.jsonl").append(
            "auto_buyer_mode_updated",
            "auto_buyer.mode",
            {"previous_mode": previous, "mode": mode, "source": "dashboard"},
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        pass
    return state


def set_auto_buyer_unit_value(raw_value: Any) -> dict[str, Any]:
    """Persist the dollar value used for future Auto-Buyer order sizing."""
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as error:
        raise ValueError("Auto-Buyer 1U must be a dollar amount") from error
    if not math.isfinite(value) or not 0.01 <= value <= 100_000:
        raise ValueError("Auto-Buyer 1U must be between $0.01 and $100,000.00")

    state = load_auto_buyer_state()
    previous = float(state.get("unit_value_usd") or DEFAULT_AUTO_BUYER_UNIT_VALUE_USD)
    state["unit_value_usd"] = round(value, 2)
    daily_units = float(state.get("max_daily_spend_units") or DEFAULT_MAX_DAILY_SPEND_UNITS)
    game_units = float(state.get("max_game_stake_units") or DEFAULT_MAX_GAME_STAKE_UNITS)
    state["max_daily_spend_usd"] = round(daily_units * state["unit_value_usd"], 2)
    state["max_game_stake_usd"] = round(game_units * state["unit_value_usd"], 2)
    state["updated_at_utc"] = iso_utc(utc_now())
    save_auto_buyer_state(state)
    try:
        AuditLog(DATA / "audit.jsonl").append(
            "auto_buyer_unit_value_updated",
            "auto_buyer.unit_value_usd",
            {
                "previous_usd": previous,
                "unit_value_usd": state["unit_value_usd"],
                "max_game_stake_units": game_units,
                "max_game_stake_usd": state["max_game_stake_usd"],
                "max_daily_spend_units": daily_units,
                "max_daily_spend_usd": state["max_daily_spend_usd"],
                "source": "dashboard",
            },
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        pass
    return {
        "status": "ok",
        "previous_unit_value_usd": previous,
        "unit_value_usd": state["unit_value_usd"],
        "max_game_stake_units": game_units,
        "max_game_stake_usd": state["max_game_stake_usd"],
        "max_daily_spend_units": daily_units,
        "max_daily_spend_usd": state["max_daily_spend_usd"],
        "note": "Applies only to future Auto-Buyer orders; unit-based game/day caps scale with it and historical rows retain their recorded unit value.",
    }


def _base_auto_execution_kwargs_from_state(state: dict[str, Any]) -> dict[str, Any]:
    """Build the `AutoExecutionConfig` fields shared by every call site that
    derives config from saved state (`run_auto_buyer_cycle`'s cycle config and
    `AutoPolymarketBuyer.__init__`'s no-config fallback). Keeping this in one
    place is what the 2026-11-10 settlement-immutability bug in
    `auto_buyer_ledger.py` was a reminder of: two independently-maintained
    copies of load-bearing logic drift apart silently. Callers still set their
    own execute_live/mode/require_model_timestamp/max_quote_age_seconds/
    refresh_stale_quotes -- those differ by call site on purpose.
    """
    whitelisted = state.get("whitelist_models")
    blacklisted = state.get("blacklist_models")
    return {
        # `.get(key, default)`, not `.get(key) or default` -- a user-set 0.0
        # (e.g. disabling the min-edge filter) must survive, not get silently
        # replaced by the default.
        "unit_value_usd": float(state.get("unit_value_usd", DEFAULT_AUTO_BUYER_UNIT_VALUE_USD)),
        "min_edge": float(state.get("min_edge", 0.035)),
        "max_edge": float(state.get("max_edge", DEFAULT_AUTO_BUYER_MAX_EDGE)),
        "max_daily_spend_usd": float(state.get("max_daily_spend_usd", 250.0)),
        "max_game_stake_usd": float(state.get("max_game_stake_usd", 25.0)),
        "max_event_lookahead_hours": float(
            state.get("max_event_lookahead_hours", DEFAULT_MAX_EVENT_LOOKAHEAD_HOURS)
        ),
        "max_model_age_minutes": float(state.get("max_model_age_minutes", DEFAULT_MAX_MODEL_AGE_MINUTES)),
        "whitelisted_models": tuple(whitelisted) if whitelisted is not None else DEFAULT_WHITELIST_MODELS,
        "blacklisted_models": tuple(blacklisted) if blacklisted is not None else EXPLICIT_BLACKLIST_MODELS,
        "disabled_sport_markets": _parse_disabled_sport_markets(state),
        "ncaab_spread_v6_live_bucket": _ncaab_spread_v6_live_bucket_from_state(state),
        "max_daily_loss_usd": (
            float(state["max_daily_loss_usd"])
            if state.get("max_daily_loss_usd") is not None
            else round(float(state.get("max_daily_spend_usd") or 250.0) * 0.5, 2)
        ),
        "edge_scaled_sizing_enabled": bool(state.get("edge_scaled_sizing_enabled", False)),
        "edge_scaling_min_fraction": float(state.get("edge_scaling_min_fraction", 0.5)),
        "min_tennis_edge": state.get("min_tennis_edge"),
        "max_tennis_price": state.get("max_tennis_price"),
    }


def _cycle_gate_kwargs(state: dict[str, Any]) -> dict[str, Any]:
    """Gate values every cycle pass (global and cohort) takes from saved state."""
    return {
        "min_model_probability": float(state.get("min_model_probability", 0.50)),
        "min_ncaaf_edge": (
            float(state["min_ncaaf_edge"]) if state.get("min_ncaaf_edge") is not None else 0.02
        ),
        "max_ncaaf_edge": (
            float(state["max_ncaaf_edge"]) if state.get("max_ncaaf_edge") is not None else 0.08
        ),
        "max_moneyline_price": (
            float(state["max_moneyline_price"]) if state.get("max_moneyline_price") is not None else 0.72
        ),
    }


def _read_cohort_candidate_picks() -> list[dict[str, Any]]:
    """Flat-ledger rows only. The cohorts were measured on ``ledger_tier='flat'`` rows, and a Main
    copy would otherwise win the merge with its own units and reason code; gated research rows are
    never cohort candidates.

    Read straight from the canonical SQLite ledger, never the dashboard cache or an xlsx projection:
    the cache falls back to parsing workbooks on any error (and defaults to xlsx authority when the
    env var is unset), and the workbooks lag the ledger until the daily job's deferred export. Raises
    on any read failure; the live pass then buys nothing (``run_auto_buyer_cycle``)."""
    from model_prediction.dashboard_cache import read_sqlite_authority_picks

    return _merge_auto_buyer_pick_sources(read_sqlite_authority_picks("flat"))


def _read_paper_cohort_fallback_picks() -> list[dict[str, Any]]:
    """The paper book's fallback when the SQLite read fails: the dashboard reader, xlsx included."""
    from model_prediction.dashboard.picks import read_flat_picks

    return _merge_auto_buyer_pick_sources(read_flat_picks())


def _cohort_pass_config(
    state: dict[str, Any],
    *,
    cohorts: tuple[BuyCohort, ...],
    mode: str,
    execute_live: bool,
    **overrides: Any,
) -> AutoExecutionConfig:
    """One book's cohort pass: whitelists nothing, so only its own cohorts can buy. A fresh ask
    (300 s, live refresh) measures the live-ask edge window in either book."""
    return AutoExecutionConfig(
        **{
            **_base_auto_execution_kwargs_from_state(state),
            "whitelisted_models": (),
            "ncaab_spread_v6_live_bucket": None,
            # D-06 (operator decision 2026-11-25): no cohort book auto-quarantines. The paper book
            # exists to measure its cohort; the live book gets an advisory drawdown warning instead
            # of a latch that silently stops an operator-approved cohort.
            "auto_quarantine_underperforming": False,
            **overrides,
        },
        **_cycle_gate_kwargs(state),
        cohorts=cohorts,
        mode=mode,
        execute_live=execute_live,
        require_model_timestamp=True,
        max_quote_age_seconds=MAX_AUTO_BUYER_QUOTE_AGE_SECONDS,
        refresh_stale_quotes=True,
        dedup_own_ledger_only=True,
        recheck_enabled_before_submit=mode == "live",
    )


def _skipped_cohort_pass(label: str, cohorts: tuple[BuyCohort, ...]) -> dict[str, Any]:
    return {"mode": label, "cohorts": [c.label for c in cohorts], "skipped": "enabled is not true"}


def _cycle_manages_live_book(*, governed: bool, buying_live: bool, preview: bool) -> bool:
    """Whether a cycle reconciles and settles the live book before evaluating picks."""
    if preview:
        return False
    if buying_live:
        return True
    return governed and _live_book_has_pending_orders(DATA / "auto_buyer_ledger.jsonl")


def auto_buyer_cycle_manages_live_book(state: dict[str, Any]) -> bool:
    """Whether ``run_auto_buyer_cycle()`` (no override, no preview) on ``state`` reconciles the live
    book itself. The daily job defers its own reconcile only when this is true."""
    mode = str(state.get("mode") or DEFAULT_AUTO_BUYER_MODE).lower()
    cycle_enabled = state.get("enabled") is True
    governed = COHORTS_STATE_KEY in state
    live_cohorts = [c for c in _parse_cohorts(state).active if c.mode == "live"]
    buying_live = cycle_enabled and ((not governed and mode == "live") or bool(live_cohorts))
    return _cycle_manages_live_book(governed=governed, buying_live=buying_live, preview=False)


def _cohort_pass_summary(
    label: str,
    cohorts: tuple[BuyCohort, ...],
    res: AutoExecutionResult,
    *,
    executing: bool,
) -> dict[str, Any]:
    orders = res.submitted_orders if executing else res.dry_run_orders
    return {
        "mode": label,
        "cohorts": [c.label for c in cohorts],
        "total_evaluated": res.total_evaluated,
        "cohort_matched": res.cohort_matched,
        **{name: value for name, value in vars(res).items() if name.startswith("rejected_")},
        "live_overnight_age_extended": res.live_overnight_age_extended,
        "quarantined_models": res.quarantined_models,
        "total_spend_usd": res.total_spend_usd,
        "orders_count": len(orders),
        "orders": orders,
        "alarms": res.alarms,
        "live_halt_reason": res.live_halt_reason,
    }


def _record_paper_fills(orders: list[dict[str, Any]]) -> None:
    """Record dry-run orders as paper fills in the shared paper ledger.

    Paper fills are recorded as immediately, fully filled at the requested
    limit price -- there's no real order book to rest on, so "what would
    have happened" is the simulated fact here. Persisted at the cycle layer
    (not inside AutoPolymarketBuyer) so direct/unit-test construction of
    the buyer never has a filesystem side effect.
    """
    from .auto_buyer_ledger import record_auto_buy_execution

    PAPER_AUTO_BUYER_DATA_ROOT.mkdir(parents=True, exist_ok=True)
    for preview in orders:
        pick_id = str(preview.get("pick_id") or "")
        paper_order_id = f"PAPER-{pick_id}" if pick_id else f"PAPER-{preview.get('market_slug', '')}"
        paper_payload = {
            **preview,
            "order_id": paper_order_id,
            "order_ids": [paper_order_id],
            "fill_known": True,
        }
        try:
            record_auto_buy_execution(
                paper_payload,
                order_id=paper_order_id,
                order_state="ORDER_STATE_FILLED",
                pick_row=paper_payload,
                jsonl_path=PAPER_AUTO_BUYER_DATA_ROOT / "auto_buyer_ledger.jsonl",
                xlsx_path=PAPER_AUTO_BUYER_DATA_ROOT / "auto_buyer_picks.xlsx",
            )
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            logger.warning("Failed to record paper trade for %s: %s", pick_id, exc)


def _preview_pass_overrides(preview: bool, preview_lookahead_hours: float | None) -> dict[str, Any]:
    """Config overrides a preview applies to every pass for that call only.

    A widened lookahead also turns slate capture off: a 12-hour capture burst takes minutes and
    meets the gateway 429 limit, so rows whose contracts are not already in the snapshot files
    show as unmapped. A default-lookahead preview still captures missing snapshot slates (market
    data only) and, never executing, does not read exchange positions into the dedup index.
    """
    if not preview or preview_lookahead_hours is None:
        return {}
    try:
        hours = float(preview_lookahead_hours)
    except (TypeError, ValueError):
        return {}
    if not math.isfinite(hours) or hours <= 0:
        return {}
    return {"max_event_lookahead_hours": hours, "capture_missing_slates": False}


def run_auto_buyer_cycle(
    execute_override: bool | None = None,
    forecast_date: str | None = None,
    force: bool = False,
    *,
    preview: bool = False,
    preview_lookahead_hours: float | None = None,
) -> dict[str, Any]:
    """Run one cycle of the Auto-Buyer based on saved configuration.

    Evaluates all open forecast picks and executes qualifying whitelisted orders,
    with fail-closed deduplication ensuring already-bought events are never re-purchased
    across multiple daily runs (e.g. 12:30 AM and 12:00 PM schedules).

    With cohorts in the state file one cycle runs up to three passes, each its own book with its
    own caps: the live cohort pass, the legacy whitelist pass (paper whenever a ``cohorts`` key
    exists), and the paper cohort pass. ``preview=True`` places no order, records no paper fill,
    runs no reconcile or settle, saves no state and appends no history.
    """
    state = load_auto_buyer_state()
    mode = str(state.get("mode") or DEFAULT_AUTO_BUYER_MODE).lower()
    if mode not in AUTO_BUYER_MODES:
        mode = DEFAULT_AUTO_BUYER_MODE
    state["mode"] = mode
    # Strict: only the boolean true enables (bool("false") is True).
    cycle_enabled = state.get("enabled") is True
    # Cohort governance (quick-261125-31d): any `cohorts` key, even an empty or malformed one, puts
    # the legacy whitelist pass on paper, so only an operator-set live cohort can trade real money
    # from the scheduler. A separate local keeps the saved `mode` untouched in the state written back.
    governed = COHORTS_STATE_KEY in state
    parsed_cohorts = _parse_cohorts(state)
    live_cohorts = tuple(c for c in parsed_cohorts.active if c.mode == "live")
    paper_cohorts = tuple(c for c in parsed_cohorts.active if c.mode == "paper")
    global_mode = "paper" if governed else mode
    wants_execution = cycle_enabled if execute_override is None else bool(execute_override)
    # Explicit execution can request a one-off live cycle, but can never
    # override PAPER mode. Changing the saved mode is a separate action.
    should_execute = global_mode == "live" and wants_execution and not preview
    # `enabled: false` in the state file is the cohorts' kill switch: execute_override=True never
    # bypasses it (the legacy ungoverned pass keeps its override semantics above).
    cohort_live_execute = (
        bool(live_cohorts) and cycle_enabled and execute_override is not False and not preview
    )
    record_paper = cycle_enabled and not preview
    # A disabled cycle runs no cohort buy pass at all (no candidate read, no quote refresh, no slate
    # capture); the live book below is still managed.
    run_cohort_passes = cycle_enabled or preview
    pass_overrides = _preview_pass_overrides(preview, preview_lookahead_hours)
    buying_live = should_execute or cohort_live_execute
    manage_live_book = _cycle_manages_live_book(governed=governed, buying_live=buying_live, preview=preview)

    # Reconcile resting orders, update fills, and cancel stale/moved orders before evaluating new picks.
    # Under cohorts this also runs while nothing buys live (kill switch on, no live cohort parsed):
    # cancelling a resting fallback at game start or on an adverse move only ever reduces risk.
    reconciliation_summary: dict[str, Any] = {}
    if manage_live_book:
        try:
            from .auto_buyer_ledger import (
                reconcile_pending_auto_buyer_fallbacks,
                settle_auto_buyer_ledger,
            )

            # D-02: while disabled, every cycle cancels the live book's resting fallbacks by id.
            reconciliation_summary = reconcile_pending_auto_buyer_fallbacks(
                cancel_all_resting=not cycle_enabled
            )
            settle_auto_buyer_ledger()
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, ExecutionGateError) as exc:
            logger.warning("Pre-cycle resting order reconciliation or settlement failed: %s", exc)
            if buying_live:
                raise RuntimeError("Live auto-buyer stopped: pre-cycle reconciliation failed") from exc
            reconciliation_summary = {"status": "error", "error": str(exc)[:200]}
    # Not an elif of the live branch: a live cohort and paper books can run in one cycle.
    if record_paper and (global_mode == "paper" or paper_cohorts):
        try:
            from .auto_buyer_ledger import settle_auto_buyer_ledger

            settle_auto_buyer_ledger(data_root=PAPER_AUTO_BUYER_DATA_ROOT)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, ExecutionGateError) as exc:
            logger.warning("Pre-cycle paper-ledger settlement failed: %s", exc)

    cohort_passes: dict[str, Any] = {}
    # Live-buying alarms raised outside a pass (unreadable picks, balance); raised with the pass alarms.
    cycle_alarms: list[dict[str, Any]] = []
    cohort_picks: list[dict[str, Any]] = []
    live_blocked: str | None = None
    if run_cohort_passes and (live_cohorts or paper_cohorts):
        try:
            cohort_picks = _read_cohort_candidate_picks()
        except Exception as exc:  # noqa: BLE001 -- any read failure fails the live pass closed
            detail = f"{type(exc).__name__}: {exc}"[:300]
            logger.error("Cohort picks unreadable from the SQLite ledger authority: %s", detail)
            live_blocked = f"cohort picks unreadable from the SQLite ledger authority ({detail})"
            if cohort_live_execute:
                cycle_alarms.append(
                    _alarm("live_picks_unreadable", pick_id="", market_slug="", detail=detail)
                )
            cohort_picks = _read_paper_cohort_fallback_picks() if paper_cohorts else []
    cash_check: dict[str, Any] | None = None
    if should_execute or (cohort_live_execute and live_blocked is None):
        cash_check = _live_cash_check()
        if cash_check["status"] != "ok":
            logger.error("Live buying skipped this cycle: account balance %s", cash_check)
            cycle_alarms.append(
                _alarm(
                    f"live_balance_{cash_check['status']}",
                    pick_id="",
                    market_slug="",
                    detail=str(cash_check.get("detail") or ""),
                )
            )
            live_blocked = live_blocked or f"account balance {cash_check['status']}"
            should_execute = False
    available_cash = cash_check.get("available_cash_usd") if cash_check else None
    if live_cohorts and not run_cohort_passes:
        cohort_passes["live"] = _skipped_cohort_pass("LIVE_DISABLED", live_cohorts)
    elif live_cohorts and live_blocked is not None:
        cohort_passes["live"] = {
            **_skipped_cohort_pass("LIVE_BLOCKED", live_cohorts),
            "skipped": live_blocked,
        }
    elif live_cohorts:
        # First, so the live book is evaluated before any paper fill of this cycle exists.
        live_config = _cohort_pass_config(
            state,
            cohorts=live_cohorts,
            mode="live",
            execute_live=cohort_live_execute,
            **{**pass_overrides, "available_cash_usd": available_cash if cohort_live_execute else None},
        )
        live_res = AutoPolymarketBuyer(config=live_config).evaluate_and_execute(
            [dict(r) for r in cohort_picks]
        )
        refusal_alarm = (
            _auth_or_balance_refusal_alarm(live_res.submitted_orders) if cohort_live_execute else None
        )
        if refusal_alarm is not None:
            live_res.alarms.append(refusal_alarm)
        if preview:
            live_label = "LIVE_PREVIEW"
        else:
            live_label = "LIVE_EXECUTION" if cohort_live_execute else "LIVE_DRY_RUN"
        cohort_passes["live"] = _cohort_pass_summary(
            live_label, live_cohorts, live_res, executing=cohort_live_execute
        )

    config = AutoExecutionConfig(
        **{**_base_auto_execution_kwargs_from_state(state), **pass_overrides},
        **_cycle_gate_kwargs(state),
        require_model_timestamp=True,
        execute_live=should_execute,
        mode=global_mode,
        max_quote_age_seconds=(
            MAX_AUTO_BUYER_QUOTE_AGE_SECONDS
            if should_execute
            else float(
                state.get(
                    "max_quote_age_seconds",
                    state.get("max_model_age_minutes", DEFAULT_MAX_MODEL_AGE_MINUTES) * 60,
                )
            )
        ),
        refresh_stale_quotes=should_execute,
        recheck_enabled_before_submit=should_execute,
        available_cash_usd=available_cash if should_execute else None,
    )

    buyer = AutoPolymarketBuyer(config=config)
    res = buyer.evaluate_and_execute()
    global_refusal_alarm = _auth_or_balance_refusal_alarm(res.submitted_orders) if should_execute else None
    if global_refusal_alarm is not None:
        res.alarms.append(global_refusal_alarm)

    if not should_execute and record_paper and global_mode == "paper":
        _record_paper_fills(res.dry_run_orders)

    if paper_cohorts and not run_cohort_passes:
        cohort_passes["paper"] = _skipped_cohort_pass("PAPER_DISABLED", paper_cohorts)
    elif paper_cohorts:
        # After the global pass recorded its paper fills, so this book's dedup sees them. The paper
        # book never quarantines: it exists to measure the cohort, and the rolling-ROI breaker would
        # silently stop that measurement after the first losing stretch.
        paper_config = _cohort_pass_config(
            state,
            cohorts=paper_cohorts,
            mode="paper",
            execute_live=False,
            **{**pass_overrides, "auto_quarantine_underperforming": False},
        )
        paper_res = AutoPolymarketBuyer(config=paper_config).evaluate_and_execute(
            [dict(r) for r in cohort_picks]
        )
        if record_paper:
            _record_paper_fills(paper_res.dry_run_orders)
        if preview:
            paper_label = "PAPER_PREVIEW"
        else:
            paper_label = "PAPER_TRADING" if record_paper else "PAPER_DRY_RUN"
        cohort_passes["paper"] = _cohort_pass_summary(paper_label, paper_cohorts, paper_res, executing=False)

    if preview:
        run_mode_label = "PREVIEW"
    elif should_execute:
        run_mode_label = "LIVE_EXECUTION"
    elif cycle_enabled and global_mode == "paper":
        run_mode_label = "PAPER_TRADING"
    else:
        run_mode_label = "DISABLED_OR_DRY_RUN"

    run_summary = {
        "executed_at_utc": iso_utc(utc_now()),
        "forecast_date": forecast_date,
        "mode": run_mode_label,
        "auto_buyer_mode": mode,
        "total_evaluated": res.total_evaluated,
        "rejected_not_open": res.rejected_not_open,
        "whitelisted_count": res.whitelisted_count,
        "rejected_not_whitelisted": res.rejected_not_whitelisted,
        "rejected_not_qualified_decision": res.rejected_not_qualified_decision,
        "rejected_blacklist": res.rejected_blacklist,
        "rejected_future_slate": res.rejected_future_slate,
        "rejected_started": res.rejected_started,
        "rejected_stale_open": res.rejected_stale_open,
        "rejected_stale_model": res.rejected_stale_model,
        "rejected_stale_quote": res.rejected_stale_quote,
        "rejected_unmapped_market": res.rejected_unmapped_market,
        "rejected_unsupported_market": res.rejected_unsupported_market,
        "rejected_disabled_sport_market": res.rejected_disabled_sport_market,
        "rejected_input_unqualified": res.rejected_input_unqualified,
        "rejected_closed_market": res.rejected_closed_market,
        "rejected_below_win_probability": res.rejected_below_win_probability,
        "rejected_low_edge": res.rejected_low_edge,
        "rejected_unrealistic_edge": res.rejected_unrealistic_edge,
        "rejected_budget": res.rejected_budget,
        "rejected_dedup": res.rejected_dedup,
        "rejected_min_topup": res.rejected_min_topup,
        "rejected_daily_loss_breaker": res.rejected_daily_loss_breaker,
        "rejected_quarantined_model": res.rejected_quarantined_model,
        "rejected_live_calibration": res.rejected_live_calibration,
        "quarantined_models": res.quarantined_models,
        "total_spend_usd": res.total_spend_usd,
        "orders_count": len(res.submitted_orders) if should_execute else len(res.dry_run_orders),
        "orders": res.submitted_orders if should_execute else res.dry_run_orders,
        "snapshot_captures": res.snapshot_captures,
        "reconciliation": reconciliation_summary,
        "global_pass_mode": global_mode,
        "preview": preview,
        "cohorts_governed": governed,
        "cohort_config_errors": list(parsed_cohorts.errors),
        "cohorts_active": [c.label for c in parsed_cohorts.active],
        "cohort_passes": cohort_passes,
        "alarms": res.alarms,
        "live_halt_reason": res.live_halt_reason,
        "live_cash_check": cash_check,
        "cycle_alarms": cycle_alarms,
    }

    if preview:
        return run_summary

    if governed:
        # Advisory only (D-06): the warnings get their own key and never join `alarms`, which would
        # raise LiveAutoBuyerAlarm and fail the worker run.
        live_drawdown = _live_drawdown(state)
        run_summary["live_drawdown"] = live_drawdown
        if "live" in cohort_passes:
            cohort_passes["live"]["drawdown_warnings"] = list(live_drawdown.get("warnings") or [])

    paper_history_path = PAPER_AUTO_BUYER_DATA_ROOT / "logs" / "auto_buyer_cycle_history.jsonl"
    _append_auto_buyer_cycle_history(
        {**{k: v for k, v in run_summary.items() if k != "cohort_passes"}, "pass": "global"},
        history_path=paper_history_path if global_mode == "paper" else AUTO_BUYER_CYCLE_HISTORY_PATH,
    )
    for book, pass_summary in cohort_passes.items():
        _append_auto_buyer_cycle_history(
            {
                "executed_at_utc": run_summary["executed_at_utc"],
                "forecast_date": forecast_date,
                "pass": f"cohort_{book}",
                **pass_summary,
            },
            history_path=AUTO_BUYER_CYCLE_HISTORY_PATH if book == "live" else paper_history_path,
        )

    state["last_run"] = run_summary
    if forecast_date:
        state["last_daily_date"] = forecast_date
    try:
        save_auto_buyer_state(state)
    except StateVersionConflictError as exc:
        # The cycle holds its state snapshot for the whole run (minutes for a
        # live cycle), so an operator toggle in that window is expected, not a
        # rare race. Re-apply only the keys THIS cycle owns onto a fresh load --
        # the operator's concurrent change (e.g. disabling the live buyer)
        # wins, and the cycle's own telemetry is still recorded rather than
        # silently dropped. A second consecutive conflict propagates.
        logger.warning("Auto-buyer state conflict at end of cycle, re-applying onto fresh state: %s", exc)
        fresh = load_auto_buyer_state()
        fresh["last_run"] = run_summary
        if forecast_date:
            fresh["last_daily_date"] = forecast_date
        save_auto_buyer_state(fresh)
    # Raised only after state and history are saved, so the alarm is on record whichever caller
    # catches it (the worker marks the run failed; daily step 12 logs it and still reconciles).
    live_alarms = [
        *cycle_alarms,
        *(res.alarms if should_execute else []),
        *cohort_passes.get("live", {}).get("alarms", []),
    ]
    if live_alarms:
        kinds = ", ".join(sorted({str(a.get("alarm")) for a in live_alarms}))
        logger.error("Live auto-buyer alarm(s): %s -- %s", kinds, live_alarms)
        raise LiveAutoBuyerAlarm(
            f"live auto-buyer alarm: {kinds}", run_summary=run_summary, alarms=live_alarms
        )
    return run_summary


def _is_explicit_zero(value: Any) -> bool:
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def _get_quarantined_underperforming_models(
    data_root: Path | str = DATA,
    min_picks: int = 15,
    max_loss_roi: float = -0.10,
) -> dict[str, dict[str, Any]]:
    """Identify whitelisted models currently in steep rolling drawdown (>=15 picks and ROI < -10%).

    Provides an autonomous self-pruning circuit breaker so underperforming models
    are dynamically quarantined without requiring manual operator intervention.
    """
    from model_prediction.portfolio.auto_buyer_ledger import (
        _is_settled_auto_buyer_record,
        is_manual_auto_buyer_record,
        read_auto_buyer_ledger,
    )

    quarantined: dict[str, dict[str, Any]] = {}
    ledger_path = Path(data_root) / "auto_buyer_ledger.jsonl"
    try:
        all_rows = read_auto_buyer_ledger(jsonl_path=ledger_path)
    except OSError:
        all_rows = []

    by_model: dict[str, list[dict[str, Any]]] = {}
    for r in all_rows:
        # MANUAL rows are hand-placed and carry no model edge; grouping them made a
        # fake "manual" model that could show up in quarantined_models.
        if is_manual_auto_buyer_record(r):
            continue
        # C4: a void zero-fill row (an order that never filled) or an unknown submission is not a
        # bet; counting it diluted the ROI sample. Only an explicit zero excludes: a row with no
        # shares key is a recorded bet.
        if (
            str(r.get("result") or "").lower() == "void"
            or r.get("submit_unknown") is True
            or _is_explicit_zero(r.get("shares"))
        ):
            continue
        if _is_settled_auto_buyer_record(r):
            m = str(r.get("model_id") or r.get("model_version") or "")
            if m:
                by_model.setdefault(m, []).append(r)

    for model_id, rows in by_model.items():

        def settled_at_timestamp(row: dict[str, Any]) -> float:
            try:
                return parse_utc(str(row.get("settled_at_utc") or "")).timestamp()
            except (TypeError, ValueError):
                return float("-inf")

        rows.sort(key=settled_at_timestamp)
        recent = rows[-30:] if len(rows) > 30 else rows
        if len(recent) >= min_picks:
            cost = sum(float(x.get("cost_usd") or 0.0) for x in recent)
            pnl = sum(float(x.get("pnl_usd") or 0.0) for x in recent)
            roi = (pnl / cost) if cost > 0 else 0.0
            if roi < max_loss_roi:
                quarantined[model_id] = {
                    "sample": len(recent),
                    "roi": round(roi, 3),
                    "cost_usd": round(cost, 2),
                    "pnl_usd": round(pnl, 2),
                }

    return quarantined


def _merge_auto_buyer_pick_sources(*sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate tier copies without collapsing distinct event contracts."""
    seen: set[Any] = set()
    merged: list[dict[str, Any]] = []
    for source in sources:
        for pick in source:
            pick_id = str(pick.get("pick_id") or "")
            event_id = str(pick.get("event_id") or f"{pick.get('away_team')}@{pick.get('home_team')}")
            selection = str(pick.get("selection") or "").lower()
            model = str(pick.get("model_id") or pick.get("model_version") or "")
            market = str(pick.get("market_type") or "").lower()
            line = "" if pick.get("line") is None else str(pick["line"])
            key = (event_id, market, line, selection, model) if event_id and selection else pick_id
            if key in seen:
                continue
            seen.add(key)
            merged.append(pick)
    return merged


def _auto_buyer_start_order(row: dict[str, Any]) -> tuple[float, str]:
    """Spend the cycle's budget on earlier games before later ones."""
    try:
        start = parse_utc(str(row.get("event_start_utc") or ""))
        timestamp = start.timestamp()
    except (TypeError, ValueError, OverflowError):
        timestamp = float("inf")
    return timestamp, str(row.get("pick_id") or "")


class BoughtIndexUnavailableError(RuntimeError):
    """Raised when one or more of ``_build_bought_index``'s dedup sources
    could not be read this cycle. A partial index would make an already-
    bought position look unbought to ``_is_already_bought``, so the caller
    must treat the whole cycle as blocked (fail-closed, across every sport)
    rather than trust an incomplete result.
    """


class AutoPolymarketBuyer:
    """Automated buyer executing qualified model picks with risk gates."""

    def __init__(
        self,
        config: AutoExecutionConfig | None = None,
        audit: AuditLog | None = None,
        live_quote_fn: Callable[[str], dict[str, Any]] | None = None,
        data_root: Path | str | None = None,
    ) -> None:
        if config is not None:
            self.config = config
        else:
            state = load_auto_buyer_state()
            state_mode = str(state.get("mode") or DEFAULT_AUTO_BUYER_MODE).lower()
            if state_mode not in AUTO_BUYER_MODES:
                state_mode = DEFAULT_AUTO_BUYER_MODE
            if COHORTS_STATE_KEY in state:
                # The fallback never runs cohort passes, and the legacy whitelist never trades
                # live while cohorts exist.
                state_mode = "paper"
            state_should_execute = state.get("enabled") is True and state_mode == "live"
            self.config = AutoExecutionConfig(
                **_base_auto_execution_kwargs_from_state(state),
                execute_live=state_should_execute,
                mode=state_mode,
            )
        self.data_root = Path(data_root) if data_root else DATA
        self.paper_data_root = self.data_root / "paper_auto_buyer"
        self.audit = audit or AuditLog(self.data_root / "audit.jsonl")
        self._live_quote_fn = live_quote_fn

    def _build_bought_index(self) -> dict[str, Any]:
        """Index all executed, submitted, and held positions to prevent double-buying."""
        bought_pick_ids: set[str] = set()
        bought_market_sides: set[tuple[str, str]] = set()
        bought_event_selections: set[tuple[str, str]] = set()
        bought_event_markets: set[tuple[str, str]] = set()
        bought_matchup_markets: set[tuple[str, str, str, str]] = set()
        bought_matchup_selections: set[tuple[str, str, str, str, str]] = set()
        held_slugs: set[str] = set()
        held_events: set[str] = set()

        partially_filled: dict[str, dict[str, Any]] = {}
        partially_filled_pick_ids: set[str] = set()
        partially_filled_market_sides: set[tuple[str, str]] = set()
        partially_filled_event_selections: set[tuple[str, str]] = set()
        partially_filled_event_markets: set[tuple[str, str]] = set()
        partially_filled_matchup_markets: set[tuple[str, str, str, str]] = set()
        partially_filled_slugs: set[str] = set()
        # A partial row is left out of the hard lockouts so its own contract can be topped up, which
        # also left its game market open to a moved line or the other side. This keeps only the
        # partial's own (slug, side) buyable in that game market (B4c).
        partial_contracts: dict[str, set[tuple[str, str]]] = {}

        failures: list[tuple[str, str]] = []
        # Orders that may exist on the exchange with no known fill (an unknown submission, or an
        # intent with no recorded outcome). Their keys are re-added after the partial-fill
        # exclusion below, so a top-up whose POST went unanswered can never be retried.
        unknown_pick_ids: set[str] = set()
        unknown_market_sides: set[tuple[str, str]] = set()
        unknown_event_selections: set[tuple[str, str]] = set()
        unknown_event_markets: set[tuple[str, str]] = set()
        unknown_matchup_markets: set[tuple[str, str, str, str]] = set()
        # Audit payloads now carry event_id/selection/market_type (B4b). Those event-level keys
        # block a whole game, so they apply only to the live book (or the legacy shared index): a
        # live fill must never block a paper cohort's row (the 31d per-book dedup design).
        audit_event_keys = self.config.mode == "live" or not self.config.dedup_own_ledger_only

        # 1. Audit logs (self.audit, data_root / audit.jsonl, data_root / audit_log.jsonl)
        audit_paths = [self.audit.path, self.data_root / "audit.jsonl", self.data_root / "audit_log.jsonl"]
        seen_paths: set[Path] = set()
        for log_path in audit_paths:
            resolved = log_path.resolve()
            if resolved in seen_paths or not log_path.exists():
                continue
            seen_paths.add(resolved)
            buy_events: list[tuple[str, dict[str, Any]]] = []
            settled_intents: set[str] = set()
            try:
                with log_path.open(encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        ev_type = str(event.get("event_type") or "")
                        payload = event.get("payload") or {}
                        if ev_type in {"order_executed", "order_rejected"} and payload.get("intent_id"):
                            # The intent has an outcome; only an executed one indexes (below).
                            settled_intents.add(str(payload["intent_id"]))
                        if ev_type in {
                            "order_executed",
                            "order_submitted",
                            "order_submit_unknown",
                            "order_intent",
                        }:
                            buy_events.append((ev_type, event))
            except OSError as exc:
                logger.warning("Failed to read audit log %s for bought-index build: %s", log_path, exc)
                failures.append((f"audit_log:{log_path}", str(exc)))
                continue
            # A forward read sees an intent before its own rejection, so outcomes are collected
            # first and the events indexed second.
            for ev_type, event in buy_events:
                payload = event.get("payload") or {}
                if str(payload.get("action") or "buy").lower() != "buy":
                    continue
                unknown = ev_type == "order_submit_unknown"
                if ev_type == "order_intent":
                    if str(payload.get("intent_id") or "") in settled_intents:
                        continue
                    unknown = True
                subj = str(event.get("subject_id") or "")
                slug = str(payload.get("market_slug") or "")
                side = str(payload.get("token_side") or payload.get("side") or "").lower()
                pick_keys = {k for k in (subj, str(payload.get("pick_id") or "")) if k}
                bought_pick_ids.update(pick_keys)
                if slug and side:
                    bought_market_sides.add((slug, side))
                if unknown:
                    unknown_pick_ids.update(pick_keys)
                    if slug and side:
                        unknown_market_sides.add((slug, side))
                if audit_event_keys:
                    ev_id = str(payload.get("event_id") or "")
                    sel = str(payload.get("selection") or "").lower()
                    if ev_id and sel:
                        bought_event_selections.add((ev_id, sel))
                        if unknown:
                            unknown_event_selections.add((ev_id, sel))
                    mtype = str(payload.get("market_type") or "").lower()
                    if ev_id and mtype:
                        bought_event_markets.add((ev_id, mtype))
                        if unknown:
                            unknown_event_markets.add((ev_id, mtype))

        # 2. Dashboard orders file (dashboard/orders.json)
        dashboard_orders_file = _paths.repo_root / "dashboard" / "orders.json"
        if dashboard_orders_file.exists():
            try:
                orders_data = json.loads(dashboard_orders_file.read_text(encoding="utf-8"))
                for ord_entry in orders_data.get("orders") or []:
                    if not isinstance(ord_entry, dict):
                        continue
                    status = str(ord_entry.get("status") or "").lower()
                    action = str(ord_entry.get("action") or "buy").lower()
                    if (
                        status in {"filled", "submitted", "open", "resting_limit", "marketable_limit"}
                        and action == "buy"
                    ):
                        if ord_entry.get("pick_id"):
                            bought_pick_ids.add(str(ord_entry["pick_id"]))
                        slug = str(ord_entry.get("market_slug") or "")
                        side = str(ord_entry.get("side") or ord_entry.get("token_side") or "").lower()
                        if slug and side:
                            bought_market_sides.add((slug, side))
                        ev_id = str(ord_entry.get("event_id") or "")
                        sel = str(ord_entry.get("selection") or "").lower()
                        if ev_id and sel:
                            bought_event_selections.add((ev_id, sel))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(
                    "Failed to read dashboard orders file %s for bought-index build: %s",
                    dashboard_orders_file,
                    exc,
                )
                failures.append(("dashboard_orders_file", str(exc)))

        # 3. Polymarket edge ledger (data/polymarket_picks.xlsx)
        poly_ledger_file = self.data_root / "polymarket_picks.xlsx"
        if poly_ledger_file.exists():
            try:
                from ..xlsx_ledger import read_xlsx_rows

                _, rows = read_xlsx_rows(poly_ledger_file)
                for r in rows:
                    p_id = str(r.get("pick_id") or "")
                    if p_id:
                        bought_pick_ids.add(p_id)
                    ev_id = str(r.get("event_id") or "")
                    sel = str(r.get("selection") or "").lower()
                    if ev_id and sel:
                        bought_event_selections.add((ev_id, sel))
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                logger.warning(
                    "Failed to read polymarket_picks.xlsx %s for bought-index build: %s",
                    poly_ledger_file,
                    exc,
                )
                failures.append(("polymarket_picks_xlsx", str(exc)))

        # 3b. Dedicated Auto-Buyer Ledger, live and paper (both must be indexed
        # so paper mode never re-buys a pick it already recorded a paper fill
        # for, and switching modes mid-day can't double-buy the same pick).
        auto_ledger_files = [
            (self.data_root / "auto_buyer_ledger.jsonl", "live_auto_buyer_ledger"),
            (self.paper_data_root / "auto_buyer_ledger.jsonl", "paper_auto_buyer_ledger"),
        ]
        if self.config.dedup_own_ledger_only:
            # Cohort books are separate (a model sits in one window per market; overlaps are
            # rejected at parse), while `event_selections` keys home/away across markets, so one
            # book's fills would block the other book's cohort on the same game and side.
            auto_ledger_files = [auto_ledger_files[0] if self.config.mode == "live" else auto_ledger_files[1]]
        game_exposure: dict[str, dict[str, float]] = {}
        for auto_ledger_file, source_label in auto_ledger_files:
            if not auto_ledger_file.exists():
                continue
            try:
                from .auto_buyer_ledger import read_auto_buyer_ledger

                for r in read_auto_buyer_ledger(auto_ledger_file):
                    # An unknown primary submission, or an unknown fallback leg (the remainder may
                    # rest or fill with no id to track): never a top-up candidate, always blocked.
                    fallback_unknown = is_fallback_submit_unknown(r)
                    if fallback_unknown or (
                        r.get("submit_unknown") is True and str(r.get("status") or "").lower() == "open"
                    ):
                        unknown_pid = str(r.get("pick_id") or "")
                        if unknown_pid:
                            unknown_pick_ids.add(unknown_pid)
                        if r.get("market_slug") and r.get("token_side"):
                            unknown_market_sides.add((str(r["market_slug"]), str(r["token_side"]).lower()))
                        unknown_ev = str(r.get("event_id") or "")
                        unknown_sel = str(r.get("selection") or "").lower()
                        unknown_mtype = str(r.get("market_type") or "").lower()
                        if unknown_ev and unknown_sel:
                            unknown_event_selections.add((unknown_ev, unknown_sel))
                        if unknown_ev and unknown_mtype:
                            unknown_event_markets.add((unknown_ev, unknown_mtype))
                        unknown_away = str(r.get("away_team") or "").strip().lower()
                        unknown_home = str(r.get("home_team") or "").strip().lower()
                        unknown_start = str(r.get("event_start_utc") or "")[:10]
                        if unknown_away and unknown_home and unknown_start and unknown_mtype:
                            unknown_matchup_markets.add(
                                (unknown_away, unknown_home, unknown_start, unknown_mtype.strip())
                            )
                    # Per-game exposure before the dead-row filter below: a zero-fill primary whose
                    # IOC was cancelled can still have its fallback resting on the book.
                    game = _game_key(r)
                    pending_cost = _pending_exposure_cost(r)
                    filled_cost_r = float(r.get("cost_usd") or 0.0)
                    if game and filled_cost_r + pending_cost > 0:
                        by_pick = game_exposure.setdefault(game, {})
                        pick_key = str(r.get("pick_id") or r.get("order_id") or "")
                        by_pick[pick_key] = by_pick.get(pick_key, 0.0) + filled_cost_r
                        if pending_cost > 0:
                            # C7: kept under its own key so the game-room check excludes only the
                            # pick's own FILLED cost (the top-up math subtracts that); its unfilled
                            # resting or unknown exposure still counts against the game cap.
                            pending_key = f"{pick_key}{_PENDING_EXPOSURE_KEY_SUFFIX}"
                            by_pick[pending_key] = by_pick.get(pending_key, 0.0) + pending_cost
                    status = str(r.get("status") or "").lower()
                    order_st = str(r.get("order_state") or "").upper()
                    result_st = str(r.get("result") or "").lower()
                    shares_cnt = float(r.get("shares") or 0.0)
                    if pending_cost <= 0 and (
                        status in {"expired", "cancelled", "rejected"}
                        or (
                            order_st
                            in {"ORDER_STATE_CANCELED", "ORDER_STATE_EXPIRED", "ORDER_STATE_REJECTED"}
                            and shares_cnt == 0
                        )
                        or (result_st == "void" and shares_cnt == 0)
                    ):
                        # A dead row; one that may still fill (C7) falls through to the lockout.
                        continue

                    pid = str(r.get("pick_id") or "")
                    slug = str(r.get("market_slug") or "")
                    side = str(r.get("token_side") or "").lower()
                    ev_id = str(r.get("event_id") or "")
                    sel = str(r.get("selection") or "").lower()
                    away = str(r.get("away_team") or "")
                    home = str(r.get("home_team") or "")
                    mtype = str(r.get("market_type") or "")
                    start_utc = str(r.get("event_start_utc") or "")
                    is_reconciled = bool(r.get("fallback_reconciled", True))
                    # The operator selling out of a position overrides the model: the
                    # row must fall through to the hard lockout below and never count
                    # as a partial fill, or the next cycle "tops up" the shares just sold.
                    operator_sold = (
                        str(r.get("settlement_source") or "") == "manual_sell"
                        or float(r.get("manual_exit_shares") or 0.0) > 0
                    )

                    # Detect partially filled orders whose resting fallbacks are not active
                    if (
                        status == "open"
                        and not operator_sold
                        and order_st == "ORDER_STATE_PARTIALLY_FILLED"
                        and is_reconciled
                        and not fallback_unknown
                        and shares_cnt > 0
                    ):
                        filled_cost = float(r.get("cost_usd") or 0.0)
                        p_info = {
                            "pick_id": pid,
                            "market_slug": slug,
                            "token_side": side,
                            "event_id": ev_id,
                            "selection": sel,
                            "filled_cost_usd": filled_cost,
                            "filled_shares": shares_cnt,
                            "model_units": float(r.get("model_units") or r.get("units") or 1.0),
                            "unit_value_usd": float(r.get("unit_value_usd") or self.config.unit_value_usd),
                            "existing_order_ids": list(r.get("order_ids") or [r.get("order_id")]),
                            "entry_price": float(r.get("entry_price") or 0.0),
                            "ledger_row": r,
                        }
                        if pid:
                            partially_filled[pid] = p_info
                            partially_filled_pick_ids.add(pid)
                        if slug and side:
                            partially_filled_market_sides.add((slug, side))
                        if ev_id and sel:
                            partially_filled_event_selections.add((ev_id, sel))
                        if ev_id and mtype:
                            partially_filled_event_markets.add((ev_id, mtype.lower()))
                        if away and home and start_utc and mtype:
                            partially_filled_matchup_markets.add(
                                (
                                    away.strip().lower(),
                                    home.strip().lower(),
                                    start_utc[:10],
                                    mtype.strip().lower(),
                                )
                            )
                        if slug:
                            partially_filled_slugs.add(slug)
                            if side:
                                partial_contracts.setdefault(event_key_from_slug(slug), set()).add(
                                    (slug, side)
                                )
                        continue

                    if slug:
                        held_events.add(event_key_from_slug(slug))
                    if pid:
                        bought_pick_ids.add(pid)
                    if r.get("order_id"):
                        bought_pick_ids.add(str(r["order_id"]))
                    if slug and side:
                        bought_market_sides.add((slug, side))
                    if ev_id and sel:
                        bought_event_selections.add((ev_id, sel))
                    if ev_id and mtype:
                        bought_event_markets.add((ev_id, mtype.lower()))
                    if away and home and start_utc and mtype:
                        m_key = (
                            away.strip().lower(),
                            home.strip().lower(),
                            start_utc[:10],
                            mtype.strip().lower(),
                        )
                        bought_matchup_markets.add(m_key)
                        if sel:
                            bought_matchup_selections.add((*m_key, sel.strip().lower()))
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                logger.warning(
                    "Failed to read auto-buyer ledger %s (%s) for bought-index build: %s",
                    auto_ledger_file,
                    source_label,
                    exc,
                )
                failures.append((source_label, str(exc)))

        # 4. Live Polymarket US account positions
        if self.config.execute_live and os.environ.get("POLYMARKET_KEY_ID") and self._live_quote_fn is None:
            try:
                executor = PolymarketExecutor(audit=self.audit, live_quote=self._live_quote_fn)
                snap = executor.portfolio_snapshot()
                for pos_slug, pos_data in (snap.get("positions") or {}).items():
                    if isinstance(pos_data, dict):
                        qty = float(pos_data.get("netPositionDecimal") or pos_data.get("netPosition") or 0.0)
                        if abs(qty) > 0:
                            held_events.add(event_key_from_slug(str(pos_slug)))
                        if qty > 0:
                            held_slugs.add(str(pos_slug))
            except (OSError, ValueError, KeyError, TypeError, ExecutionGateError, RuntimeError) as exc:
                logger.warning(
                    "Failed to read live Polymarket account positions for bought-index build: %s", exc
                )
                failures.append(("live_polymarket_positions", str(exc)))

        # Exclude partially filled positions from hard lockouts so they remain eligible for remainder top-up
        bought_pick_ids.difference_update(partially_filled_pick_ids)
        bought_market_sides.difference_update(partially_filled_market_sides)
        bought_event_selections.difference_update(partially_filled_event_selections)
        bought_event_markets.difference_update(partially_filled_event_markets)
        bought_matchup_markets.difference_update(partially_filled_matchup_markets)
        held_slugs.difference_update(partially_filled_slugs)
        bought_pick_ids.update(unknown_pick_ids)
        bought_market_sides.update(unknown_market_sides)
        bought_event_selections.update(unknown_event_selections)
        bought_event_markets.update(unknown_event_markets)
        bought_matchup_markets.update(unknown_matchup_markets)
        # No top-up on anything an unknown order may already cover: same pick, market side or game side.
        for pf_pid, info in list(partially_filled.items()):
            if (
                pf_pid in unknown_pick_ids
                or (str(info.get("market_slug") or ""), str(info.get("token_side") or "").lower())
                in unknown_market_sides
                or (str(info.get("event_id") or ""), str(info.get("selection") or "").lower())
                in unknown_event_selections
            ):
                del partially_filled[pf_pid]

        if failures:
            detail = "; ".join(f"{name}: {msg}" for name, msg in failures)
            raise BoughtIndexUnavailableError(
                f"Bought-index build failed-closed for {len(failures)} dedup source(s): {detail} -- "
                "blocking every buy for this cycle, across every sport, because a partial index risks "
                "re-buying an already-held position."
            )

        return {
            "pick_ids": bought_pick_ids,
            "market_sides": bought_market_sides,
            "event_selections": bought_event_selections,
            "event_markets": bought_event_markets,
            "matchup_markets": bought_matchup_markets,
            "matchup_selections": bought_matchup_selections,
            "held_slugs": held_slugs,
            "held_events": held_events,
            "partially_filled": partially_filled,
            "partially_filled_market_sides": partially_filled_market_sides,
            "partially_filled_event_selections": partially_filled_event_selections,
            "partially_filled_slugs": partially_filled_slugs,
            "partial_contracts": partial_contracts,
            "game_exposure": game_exposure,
        }

    def _get_partial_fill_info(
        self,
        pick_id: str,
        market_slug: str | None = None,
        token_side: str | None = None,
        event_id: str | None = None,
        selection: str | None = None,
        bought_index: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Lookup existing partial fill metadata for an open pick to size remainder."""
        index = bought_index if bought_index is not None else self._build_bought_index()
        pf_map: dict[str, dict[str, Any]] = index.get("partially_filled") or {}
        if pick_id and pick_id in pf_map:
            return pf_map[pick_id]
        if market_slug and token_side:
            target_slug = str(market_slug)
            target_side = str(token_side).lower()
            for info in pf_map.values():
                if (
                    info.get("market_slug") == target_slug
                    and str(info.get("token_side") or "").lower() == target_side
                ):
                    return info
        if event_id and selection:
            target_ev = str(event_id)
            target_sel = str(selection).lower()
            for info in pf_map.values():
                if (
                    str(info.get("event_id") or "") == target_ev
                    and str(info.get("selection") or "").lower() == target_sel
                ):
                    return info
        return None

    def _is_already_bought(
        self,
        pick_id: str,
        market_slug: str | None = None,
        token_side: str | None = None,
        event_id: str | None = None,
        selection: str | None = None,
        market_type: str | None = None,
        away_team: str | None = None,
        home_team: str | None = None,
        event_start_utc: str | None = None,
        bought_index: dict[str, Any] | None = None,
    ) -> bool:
        """Check if an order was already executed for this pick, event, market, or position."""
        index = bought_index if bought_index is not None else self._build_bought_index()
        if pick_id and pick_id in index["pick_ids"]:
            return True
        if market_slug and market_slug in index["held_slugs"]:
            return True
        if market_slug and token_side and (market_slug, token_side.lower()) in index["market_sides"]:
            return True
        # Either line of a spread counts: holding or having sold one side must also
        # block the other, or a refreshed pick on the opposite line re-enters the game.
        if market_slug and event_key_from_slug(market_slug) in index.get("held_events", set()):
            return True
        if market_slug and token_side:
            partial_held = index.get("partial_contracts", {}).get(event_key_from_slug(market_slug))
            if partial_held and (market_slug, token_side.lower()) not in partial_held:
                return True
        if event_id and selection and (str(event_id), str(selection).lower()) in index["event_selections"]:
            return True
        if (
            event_id
            and market_type
            and (str(event_id), str(market_type).lower()) in index.get("event_markets", set())
        ):
            return True
        if away_team and home_team and event_start_utc and market_type:
            m_key = (
                str(away_team).strip().lower(),
                str(home_team).strip().lower(),
                str(event_start_utc)[:10],
                str(market_type).strip().lower(),
            )
            if m_key in index.get("matchup_markets", set()):
                return True
            if selection and (*m_key, str(selection).strip().lower()) in index.get(
                "matchup_selections", set()
            ):
                return True
        return False

    def _effective_min_edge(
        self,
        sport: str,
        market_type: str,
        line: Any = None,
    ) -> float:
        """Resolve sport- and market-specific edge hurdles matching Main Ledger gating."""
        s = sport.strip().lower()
        m = market_type.strip().lower()

        # Moneyline market hurdle: moneylines have higher vig and asymmetric risk; require >= 7.5%
        if m == "moneyline" and s in ("nba", "nfl", "wnba", "mlb", "ncaab"):
            return 0.075

        # 1. NFL: 5.0% base hurdle; heavy dogs (spread >= 8.0 pts) require 7.0% (Main Ledger gating)
        if s == "nfl":
            base = 0.05
            try:
                if m == "spread" and line is not None and abs(float(line)) >= 8.0:
                    return base + 0.02
            except (ValueError, TypeError):
                pass
            return base

        # 2. Major pro sports (NBA, WNBA, MLB): 5.0% for spreads/totals
        if s in ("nba", "wnba", "mlb"):
            return 0.05

        # 3. College basketball / international basketball: 3.5%
        if s in ("ncaab", "euroleague", "nbl"):
            return 0.035

        # 4. Low-variance / calibrated structural sports: 2.0% for CFB & Soccer Totals; 5.0% for Tennis
        if s == "tennis":
            return float(self.config.min_tennis_edge if self.config.min_tennis_edge is not None else 0.05)
        if s in ("ncaaf", "cfb"):
            return float(self.config.min_ncaaf_edge if self.config.min_ncaaf_edge is not None else 0.02)
        if s in ("soccer", "hockey", "darts"):
            return 0.02

        # 5. Tactical / high-variance esports (blacklisted by default; 5.0% safeguard)
        if s in ("r6", "rainbow_six", "lol", "cs2", "dota2", "valorant"):
            return 0.05

        return float(self.config.min_edge)

    def evaluate_and_execute(
        self,
        picks: list[dict[str, Any]] | None = None,
    ) -> AutoExecutionResult:
        """Scan open picks, apply filters and sizing, and execute live or paper-trade."""
        from model_prediction.dashboard.orders import _decorate_pick

        # Circuit breaker for this cycle's live quote refresh (step 7 below):
        # once the gateway 429s once, every subsequent stale-quote pick would
        # otherwise retry (with its own 10s backoff) against a still-throttled
        # endpoint -- 30 stale picks under live throttling cost ~1623s of the
        # daily run this way (2026-10-26). Mirrors the same breaker already
        # used in polymarket_us.py's capture_pool_slates.
        gateway_rate_limited = False

        if picks is None:
            from model_prediction.dashboard.picks import _parse_research_picks, read_flat_picks, read_picks

            main_picks = read_picks()
            flat_picks = read_flat_picks()
            gated_picks = (
                []
                if (self.config.execute_live or self.config.mode == "live")
                else _parse_research_picks(gated=True)
            )

            picks = _merge_auto_buyer_pick_sources(main_picks, gated_picks, flat_picks)

        now = utc_now()
        result = AutoExecutionResult()

        totals_root = (
            self.paper_data_root
            if self.config.mode == "paper" and not self.config.execute_live
            else self.data_root
        )
        today_spend_usd, today_realized_pnl_usd = _today_auto_buyer_totals(totals_root)

        # Drawdown circuit breaker: once today's realized loss crosses the configured
        # threshold, stop considering new buys for the rest of the calendar day.
        # Reconciliation/settlement of already-open positions happens separately in
        # run_auto_buyer_cycle, before this method is called, so it is unaffected.
        if self.config.max_daily_loss_usd is not None and today_realized_pnl_usd <= -abs(
            self.config.max_daily_loss_usd
        ):
            result.total_evaluated = len(picks)
            result.rejected_daily_loss_breaker = len(picks)
            return result

        if (
            self._live_quote_fn is None
            and (self.config.mode in AUTO_BUYER_MODES or self.config.execute_live)
            and self.config.capture_missing_slates
        ):
            result.snapshot_captures = _capture_missing_active_snapshot_slates(
                picks,
                config=self.config,
                now=now,
                data_root=self.data_root,
            )
        quarantined = (
            _get_quarantined_underperforming_models(
                totals_root,
                min_picks=self.config.auto_quarantine_min_picks,
                max_loss_roi=self.config.auto_quarantine_max_loss_roi,
            )
            if self.config.auto_quarantine_underperforming
            else {}
        )
        if quarantined:
            result.quarantined_models = sorted(quarantined.keys())
            for q_model, q_info in quarantined.items():
                logger.info(
                    "Dynamic circuit breaker quarantined underperforming model %s (rolling ROI: %.1f%% over %d picks)",
                    q_model,
                    q_info["roi"] * 100,
                    q_info["sample"],
                )

        # Seeded from today's already-executed spend so the "daily" cap enforces a
        # real per-calendar-day ceiling across every cycle, not just this one. The cap reads
        # committed_spend (fills plus resting fallbacks); total_spend_usd keeps reporting fills.
        current_spend = today_spend_usd
        committed_spend = today_spend_usd
        bought_index = self._build_bought_index()
        live_calibration_alarmed: set[str] = set()
        overnight_age = _OvernightAgeExtension(picks)
        open_pick_ids = [str(row.get("pick_id") or "") for row in picks if row.get("status") == "open"]
        input_flags = _load_pick_input_flags(open_pick_ids)
        # Any live pass may hold a live cohort (state cohorts or the legacy NCAAB v6 bucket).
        live_calibration_fields = (
            _load_pick_calibration_fields(open_pick_ids) if self.config.mode == "live" else {}
        )

        for row in sorted(picks, key=_auto_buyer_start_order):
            result.total_evaluated += 1
            pick_id = str(row.get("pick_id") or "")
            model_id = str(row.get("model_id") or row.get("model_version") or "")
            event_id = str(row.get("event_id") or "")
            selection = str(row.get("selection") or "").lower()
            cohort = _match_cohort(row, self.config)

            # 1. Status filter (must be open). Checked first, ahead of every other
            # gate: the pick-source files (main/flat/gated/research) are append-only
            # and accumulate settled/closed history alongside live picks, so most
            # rows on any given cycle are already-resolved noise rather than genuine
            # candidates. Filtering that out first keeps every other counter below
            # (blacklist, whitelist, disabled-sport-market, etc.) reflecting the real
            # live-candidate funnel instead of being diluted by historical rows.
            if row.get("status") != "open":
                result.rejected_not_open += 1
                continue
            if cohort is not None:
                result.cohort_matched += 1

            # Tags name destinations (operator directive 2026-11-11), so a row's legacy
            # record_type no longer blocks it; the whitelist/blacklist/quarantine/caps
            # below remain the operator's controls. This is a decision-qualification
            # rejection, not a whitelist rejection -- it fires on picks the model
            # itself never called, regardless of whether model_id is whitelisted, so
            # folding it into rejected_not_whitelisted would misattribute the reason
            # in the operator's rejection funnel.
            decision = str(row.get("decision") or "").upper()
            reason_code = str(row.get("reason_code") or "").upper()
            if (decision and decision != "CALL") or (
                cohort is None and reason_code.startswith("PAPER_CALL_")
            ):
                result.rejected_not_qualified_decision += 1
                continue

            if model_id in quarantined:
                result.rejected_quarantined_model += 1
                continue

            # Append-only forecast files retain open rows after an event starts.
            # Label those rows as stale before quote/mapping work so old rows do
            # not consume the same evaluation path as actionable candidates.
            try:
                row_event_start = parse_utc(str(row.get("event_start_utc") or ""))
            except (TypeError, ValueError):
                row_event_start = None
            if row_event_start is not None and now >= row_event_start:
                result.rejected_stale_open += 1
                result.rejected_started += 1
                continue

            # 2. Blacklist filter
            if model_id in self.config.blacklisted_models and cohort is None:
                result.rejected_blacklist += 1
                continue

            # 3. Whitelist filter
            if model_id not in self.config.whitelisted_models and cohort is None:
                result.rejected_not_whitelisted += 1
                continue

            # Operator-level categorical block. This deliberately takes precedence
            # over persisted model whitelists so renamed or replacement MLB models
            # cannot restore moneyline execution without a reviewed code change.
            paper_ncaab_trial = _is_paper_ncaab_spread_v6_trial(row, self.config)
            if (
                not paper_ncaab_trial
                and cohort is None
                and _is_disabled_auto_buyer_sport_market(row, self.config.disabled_sport_markets)
            ):
                result.rejected_disabled_sport_market += 1
                continue

            # The forecast's own input verdict (operator directive 2026-11-15): a
            # pick Main declined for unqualified inputs or unverified weather is
            # not bought from its Flat copy either.
            if _has_unqualified_inputs(row, input_flags):
                result.rejected_input_unqualified += 1
                continue

            # whitelisted_count counts only picks that cleared every categorical
            # gate (blacklist, whitelist, disabled sport/market) -- incrementing it
            # right after the whitelist check would also credit picks later blocked
            # by the disabled-sport-market gate (e.g. a whitelisted MLB moneyline
            # model), inflating the "how many picks had a real shot" telemetry.
            result.whitelisted_count += 1

            # Only markets with a proven contract representation may reach mapping.
            # In particular, an NRFI/YRFI model must never be attached to a full-game
            # total merely to increase volume when the exchange exposes no first-inning
            # contract for the event.
            market_type = str(row.get("market_type") or "").lower()
            away_team = str(row.get("away_team") or "")
            home_team = str(row.get("home_team") or "")
            if market_type and market_type not in EXECUTABLE_AUTO_BUYER_MARKET_TYPES:
                result.rejected_unsupported_market += 1
                continue

            # 4. Timing & Slate Date Check:
            # Prevent buying tomorrow's games or games outside today's active slate
            event_start_str = str(row.get("event_start_utc") or "")
            try:
                event_start = parse_utc(event_start_str)
            except (ValueError, TypeError):
                result.rejected_started += 1
                continue

            # Block games that already started
            if now >= event_start:
                result.rejected_started += 1
                continue

            # Block games scheduled beyond the active slate window. Bounded to the
            # scheduler's own run cadence (daily cycle runs every 6h) by default, so
            # an event isn't bought hours before it can be re-evaluated against a
            # fresher quote/model run, and isn't left unbought because it was out of
            # range on every cycle before it started.
            if (event_start - now).total_seconds() > self.config.max_event_lookahead_hours * 3600:
                result.rejected_future_slate += 1
                continue

            # A wider candidate horizon is safe only when the model output is
            # still recent at purchase time. Missing timestamps fail closed.
            model_timestamp = row.get("created_at_utc") or row.get("observed_at_utc")
            try:
                model_age_seconds = (now - parse_utc(str(model_timestamp))).total_seconds()
            except (TypeError, ValueError):
                model_age_seconds = float("inf")
            live_cohort = cohort is not None and cohort.mode == "live"
            max_model_age_minutes = self.config.max_model_age_minutes
            if live_cohort:
                # D-03: real money buys only a recent model call, whatever the global (paper) limit.
                overnight_limit = min(max_model_age_minutes, LIVE_COHORT_OVERNIGHT_MAX_MODEL_AGE_MINUTES)
                max_model_age_minutes = min(max_model_age_minutes, LIVE_COHORT_MAX_MODEL_AGE_MINUTES)
                if max_model_age_minutes * 60 < model_age_seconds <= overnight_limit * 60 and (
                    overnight_age.allows(row)
                ):
                    max_model_age_minutes = overnight_limit
                    result.live_overnight_age_extended += 1
            if (self.config.require_model_timestamp or live_cohort) and (
                model_age_seconds < 0 or model_age_seconds > max_model_age_minutes * 60
            ):
                result.rejected_stale_model += 1
                continue

            # A fresh executable ask cannot repair a pick priced from an old
            # source quote: the model may have selected the wrong main line.
            source_quote_at = row.get("market_quote_observed_at_utc")
            source_quote_age = 0.0
            if source_quote_at:
                try:
                    source_quote_age = (
                        parse_utc(str(model_timestamp)) - parse_utc(str(source_quote_at))
                    ).total_seconds()
                except (TypeError, ValueError):
                    source_quote_age = float("inf")
                if not 0 <= source_quote_age <= MAX_SOURCE_QUOTE_AGE_SECONDS:
                    result.rejected_stale_quote += 1
                    continue

            # 5. Fast Deduplication check (pick_id, event_id, or matchup + market)
            if self._is_already_bought(
                pick_id=pick_id,
                event_id=event_id,
                selection=selection,
                market_type=market_type,
                away_team=away_team,
                home_team=home_team,
                event_start_utc=event_start_str,
                bought_index=bought_index,
            ):
                result.rejected_dedup += 1
                continue

            # 6. Resolve Verified Polymarket Market & Quote
            # Decorates with verified Polymarket mapping, team resolution, and snapshot
            # If a simulated live_quote_fn is provided (e.g. in test suite), use it
            if self._live_quote_fn is not None:
                try:
                    mock_quote = self._live_quote_fn(str(row.get("market_slug") or f"slug-{pick_id}"))
                    if not mock_quote:
                        result.rejected_stale_quote += 1
                        continue
                    quote = {
                        "market_slug": mock_quote.get("market_slug") or f"slug-{pick_id}",
                        "side": mock_quote.get("side") or str(row.get("token_side") or "long"),
                        "ask": mock_quote.get("ask") or float(row.get("market_implied_probability") or 0.50),
                        "fresh": True,
                        "market_state": "MARKET_STATE_OPEN",
                        "event_start_utc": mock_quote.get("event_start_utc"),
                    }
                except (RuntimeError, ValueError, KeyError, TypeError, OSError):
                    quote = None
            else:
                decorated = _decorate_pick(row)
                quote = decorated.get("quote")

            if quote is None:
                result.rejected_unmapped_market += 1
                continue

            # 7. Quote Staleness & Market State Check (with live refresh fallback)
            age_sec = float(quote.get("age_seconds") or 999999)
            max_quote_age = self.config.max_quote_age_seconds
            fallback_requires_live_refresh = source_quote_age > max_quote_age
            if (
                self.config.refresh_stale_quotes
                and (not quote.get("fresh", False) or fallback_requires_live_refresh)
                and (age_sec > max_quote_age or fallback_requires_live_refresh)
                and self._live_quote_fn is None
                and not gateway_rate_limited
            ):
                try:
                    from model_prediction.data_sources.polymarket_us import PolymarketUSClient

                    # The gateway throttles bursts (the client already backs off
                    # inside one call); a stale pick that exhausts those retries
                    # was silently rejected. Give it one more attempt after a
                    # longer pause before treating the quote as unrefreshable.
                    try:
                        live_snap = PolymarketUSClient().snapshot(str(quote["market_slug"]))
                    except httpx.HTTPStatusError as throttled:
                        if throttled.response.status_code != 429:
                            raise
                        time.sleep(10)
                        live_snap = PolymarketUSClient().snapshot(str(quote["market_slug"]))
                    side = str(quote.get("side") or "long").lower()
                    side_data = live_snap.get(side) or {}
                    fresh_ask = side_data.get("ask") or side_data.get("price")
                    live_quote_age = (
                        utc_now() - parse_utc(str(live_snap["observed_at_utc"]))
                    ).total_seconds()
                    if (
                        fresh_ask is not None
                        and float(fresh_ask) > 0
                        and 0 <= live_quote_age <= max_quote_age
                    ):
                        quote = {
                            **quote,
                            "ask": float(fresh_ask),
                            "fresh": True,
                            "age_seconds": 0,
                            "market_state": live_snap.get("market_state", "MARKET_STATE_OPEN"),
                            "event_start_utc": live_snap.get("event_start_utc")
                            or quote.get("event_start_utc"),
                        }
                        age_sec = 0.0
                        fallback_requires_live_refresh = False
                except httpx.HTTPStatusError as throttled_again:
                    if throttled_again.response.status_code == 429:
                        gateway_rate_limited = True
                    logger.warning(
                        "live quote refresh failed for %s (%s): %s",
                        quote.get("market_slug"),
                        pick_id,
                        throttled_again,
                    )
                except (
                    httpx.HTTPError,
                    OSError,
                    ValueError,
                    KeyError,
                    TypeError,
                    RuntimeError,
                    ExecutionGateError,
                ) as refresh_error:
                    # Was a bare `pass`: a failed live refresh just showed up
                    # as an unexplained "stale quote" rejection.
                    logger.warning(
                        "live quote refresh failed for %s (%s): %s",
                        quote.get("market_slug"),
                        pick_id,
                        refresh_error,
                    )

            if fallback_requires_live_refresh or (not quote.get("fresh", False) and age_sec > max_quote_age):
                result.rejected_stale_quote += 1
                continue

            if quote.get("market_state") != "MARKET_STATE_OPEN":
                result.rejected_closed_market += 1
                continue

            if self.config.execute_live:
                tip_refusal = _tip_time_refusal(event_start, quote.get("event_start_utc"), utc_now())
                if tip_refusal is not None:
                    logger.warning("Live buy refused for pick %s: %s", pick_id, tip_refusal)
                    result.rejected_tip_time_mismatch += 1
                    continue

            market_slug = str(quote["market_slug"])
            token_side = str(quote["side"]).lower()
            clob_ask = float(quote.get("ask") or 0.0)

            # 8. Precise Deduplication check on resolved market_slug and token_side
            if self._is_already_bought(
                pick_id=pick_id,
                market_slug=market_slug,
                token_side=token_side,
                event_id=event_id,
                selection=selection,
                market_type=market_type,
                away_team=away_team,
                home_team=home_team,
                event_start_utc=event_start_str,
                bought_index=bought_index,
            ):
                result.rejected_dedup += 1
                continue

            sport_norm = str(row.get("league") or row.get("sport") or "").strip().lower()
            if not sport_norm and model_id.startswith("tennis-"):
                sport_norm = "tennis"
            if (
                sport_norm == "tennis"
                and self.config.max_tennis_price is not None
                and clob_ask > self.config.max_tennis_price
            ):
                result.rejected_unrealistic_edge += 1
                continue

            # Heavy favorite moneyline gate: cap moneyline buying price to prevent negative vig traps (odds <= -260)
            if (
                cohort is None
                and market_type == "moneyline"
                and self.config.max_moneyline_price is not None
                and clob_ask > self.config.max_moneyline_price
            ):
                result.rejected_unrealistic_edge += 1
                continue

            # 9. Win probability gate: Outcome must be favored by the model (>= min_model_probability)
            # Map the raw probability through the model's fitted calibrator when it
            # passes the serving guard. The pick ledgers stay raw on purpose: the
            # calibration pipeline trains on them, and calibrated values there
            # would be calibrated again on the next fit. Everything below (gates,
            # edge, sizing, the recorded order) sees the calibrated copy.
            raw_model_prob = float(row.get("model_probability") or 0.0)
            calibration = calibrate_for_serving(
                model_id, market_type, raw_model_prob, data_root=self.data_root
            )
            model_prob = calibration.probability
            calibration_refusal: str | None = None
            if live_cohort:
                # B7: a live cohort's edge window was approved on raw probabilities. A calibrator the
                # calibrate job writes (4x a day) must never silently move it: fail closed, loudly.
                # The forecast may already have calibrated the saved value, so check the pick's
                # own record first; the buyer-side calibrator is checked as well.
                calibration_refusal = _live_calibration_refusal(
                    row,
                    None if live_calibration_fields is None else live_calibration_fields.get(pick_id, []),
                )
                if calibration_refusal is None and (
                    calibration.applied
                    or calibration.method != "identity"
                    or calibration.probability != calibration.raw_probability
                ):
                    calibration_refusal = f"served calibration {calibration.method} ({calibration.reason})"
            if calibration_refusal is not None:
                result.rejected_live_calibration += 1
                logger.error(
                    "Live cohort pick %s (%s %s) refused, not identity calibration: %s",
                    pick_id,
                    model_id,
                    market_type,
                    calibration_refusal,
                )
                if model_id not in live_calibration_alarmed:
                    live_calibration_alarmed.add(model_id)
                    result.alarms.append(
                        _alarm(
                            "live_calibration_not_identity",
                            pick_id=pick_id,
                            market_slug=market_slug,
                            detail=f"{model_id} {market_type}: {calibration_refusal}",
                        )
                    )
                continue
            row = {
                **row,
                "model_probability": model_prob,
                "model_probability_raw": raw_model_prob,
                "calibration_method": calibration.method,
            }
            if cohort is None and model_prob < self.config.min_model_probability:
                result.rejected_below_win_probability += 1
                continue

            # 10. Sizing & Edge check against real Polymarket CLOB ask (mirrors Main Ledger sport hurdles)
            edge = model_prob - clob_ask
            if cohort is not None:
                # A cohort is bounded by its own window on the live-ask edge, not by the sport
                # floors or the global max edge (both would drop the measured cohort rows).
                if not 0 < clob_ask < 1:
                    result.rejected_unrealistic_edge += 1
                    continue
                if not math.isfinite(edge) or edge < cohort.min_saved_edge:
                    result.rejected_low_edge += 1
                    continue
                if not cohort.contains(edge) or edge > COHORT_EXECUTION_EDGE_CEILING:
                    result.rejected_unrealistic_edge += 1
                    continue
            else:
                effective_min_edge = self._effective_min_edge(
                    sport_norm,
                    market_type,
                    row.get("line"),
                )
                if edge < effective_min_edge:
                    result.rejected_low_edge += 1
                    continue
                effective_max_edge = (
                    self.config.max_ncaaf_edge
                    if sport_norm in ("ncaaf", "cfb") and self.config.max_ncaaf_edge is not None
                    else self.config.max_edge
                )
                if effective_max_edge is not None and edge > effective_max_edge:
                    result.rejected_unrealistic_edge += 1
                    continue

            if sport_norm in ("ncaaf", "cfb") and market_type == "spread" and row.get("line") is not None:
                try:
                    if abs(float(row["line"])) > 21.5:
                        result.rejected_unrealistic_edge += 1
                        continue
                except (ValueError, TypeError):
                    pass

            # Sizing: units * unit_value_usd
            raw_units = float(row.get("units") or row.get("display_units") or 1.0)
            target_spend = raw_units * self.config.unit_value_usd
            target_spend = min(target_spend, self.config.max_game_stake_usd)
            # Cohort books cap a game, not an order (operator directive 2026-11-25): the live spread
            # and total cohorts on one game share max_game_stake_usd with this book's earlier fills.
            game = _game_key(row) if self.config.cohorts else ""
            game_other_spend = 0.0
            if game:
                # Everything on the game except this pick's own filled cost, which the top-up math
                # subtracts below; its own pending (resting or unknown) exposure still counts (C7).
                game_other_spend = sum(
                    cost
                    for key, cost in bought_index.get("game_exposure", {}).get(game, {}).items()
                    if key != pick_id
                )
                game_room = round(self.config.max_game_stake_usd - game_other_spend, 2)
                if game_room < 0.50:
                    result.rejected_budget += 1
                    continue
                target_spend = min(target_spend, game_room)

            # Optional edge-scaled sizing (on by default since f9d077e0; see
            # edge_scaled_sizing_enabled).
            # Scales the already-capped stake linearly between edge_scaling_min_fraction
            # (at min_edge) and 1.0 (at max_edge), so a bare-minimum-edge pick risks less
            # than a near-max-edge one. Never increases stake above the per-game cap above.
            if self.config.edge_scaled_sizing_enabled:
                edge_ceiling = (
                    self.config.max_edge if self.config.max_edge is not None else self.config.min_edge + 0.20
                )
                edge_span = max(1e-9, edge_ceiling - self.config.min_edge)
                edge_fraction = max(0.0, min(1.0, (edge - self.config.min_edge) / edge_span))
                min_fraction = min(1.0, max(0.0, self.config.edge_scaling_min_fraction))
                scale = min_fraction + (1.0 - min_fraction) * edge_fraction
                target_spend = round(target_spend * scale, 2)

            partial_info = self._get_partial_fill_info(
                pick_id=pick_id,
                market_slug=market_slug,
                token_side=token_side,
                event_id=event_id,
                selection=selection,
                bought_index=bought_index,
            )

            if partial_info is not None and str(partial_info.get("market_slug") or "") != market_slug:
                # B4c: a top-up only ever adds to the contract already held. A partial found through
                # event + selection on another line (a moved spread or total) is a held game market.
                result.rejected_dedup += 1
                continue

            is_topup = False
            filled_cost = 0.0
            if partial_info is not None:
                filled_cost = float(partial_info.get("filled_cost_usd") or 0.0)
                remaining_spend = round(target_spend - filled_cost, 2)
                if remaining_spend < 0.50:
                    result.rejected_min_topup += 1
                    continue
                spend_for_order = remaining_spend
                is_topup = True
            else:
                spend_for_order = target_spend

            limit_price = max(0.01, min(0.99, round(clob_ask, 2)))

            # Compute shares: spend_for_order / limit_price, minimum min_shares
            shares = max(self.config.min_shares, round(spend_for_order / limit_price, 2))
            actual_cost = round(shares * limit_price, 2)

            if is_topup and (filled_cost + actual_cost) > target_spend + 0.10:
                shares = max(
                    self.config.min_shares, math.floor((target_spend - filled_cost) / limit_price * 100) / 100
                )
                actual_cost = round(shares * limit_price, 2)
                if actual_cost < 0.50:
                    result.rejected_min_topup += 1
                    continue

            # Daily budget check
            if committed_spend + actual_cost > self.config.max_daily_spend_usd:
                result.rejected_budget += 1
                continue
            if game and game_other_spend + actual_cost > self.config.max_game_stake_usd + 0.10:
                result.rejected_budget += 1
                continue

            ticket_pick_id = pick_id
            if is_topup and partial_info is not None:
                existing_oids = partial_info.get("existing_order_ids") or []
                topup_suffix = f"topup_{len(existing_oids)}" if len(existing_oids) > 1 else "topup"
                ticket_pick_id = f"{pick_id}_{topup_suffix}"

            ticket = OrderTicket(
                market_slug=market_slug,
                token_side=token_side,
                action="buy",
                order_type="limit_ioc",
                price=limit_price,
                size_shares=shares,
                pick_id=ticket_pick_id,
                estimated_cost_usd=actual_cost,
                maximum_cost_usd=actual_cost + 0.10,
                authorization_type="auto_whitelisted_model",
                ioc_fallback_resting=True,
                # D-01: the remainder expires before tip-off; B5: re-checked against the live ask.
                resting_expires_at_utc=iso_utc(
                    event_start - timedelta(seconds=LIVE_FALLBACK_EXPIRY_LEAD_SECONDS)
                ),
                max_live_price_deviation=LIVE_MAX_PRICE_DEVIATION,
            )

            order_payload = {
                "pick_id": pick_id,
                "ticket_pick_id": ticket_pick_id,
                "is_topup": is_topup,
                "target_spend": target_spend,
                "model_id": model_id,
                "market_slug": market_slug,
                "token_side": token_side,
                "sport": row.get("sport") or row.get("league"),
                "selection": row.get("selection"),
                "limit_price": limit_price,
                "shares": shares,
                "cost_usd": actual_cost,
                "unit_value_usd": self.config.unit_value_usd,
                "edge": round(edge, 4),
                "event_start_utc": event_start_str,
                "timestamp_utc": iso_utc(now),
                # Carried through so a preview/paper fill recorded one layer up
                # (run_auto_buyer_cycle) has everything settlement needs without
                # re-touching the original pick row.
                "home_team": row.get("home_team"),
                "away_team": row.get("away_team"),
                "market_type": row.get("market_type"),
                "model_probability": row.get("model_probability"),
                "model_probability_raw": row.get("model_probability_raw"),
                "calibration_method": row.get("calibration_method"),
                "market_probability": row.get("market_probability") or row.get("market_implied_probability"),
                "event_id": row.get("event_id"),
                "line": row.get("line"),
                "cohort": cohort.label if cohort is not None else None,
            }

            # Update bought_index to prevent intra-batch double buys
            bought_index["pick_ids"].add(pick_id)
            bought_index["pick_ids"].add(ticket_pick_id)
            bought_index["market_sides"].add((market_slug, token_side))
            if event_id and selection:
                bought_index["event_selections"].add((event_id, selection))
            if event_id and market_type:
                bought_index.setdefault("event_markets", set()).add((event_id, market_type.lower()))
            if away_team and home_team and event_start_str and market_type:
                m_key = (
                    away_team.strip().lower(),
                    home_team.strip().lower(),
                    event_start_str[:10],
                    market_type.lower(),
                )
                bought_index.setdefault("matchup_markets", set()).add(m_key)
                if selection:
                    bought_index.setdefault("matchup_selections", set()).add((*m_key, selection.lower()))
            bought_index["held_slugs"].add(market_slug)
            bought_index.setdefault("held_events", set()).add(event_key_from_slug(market_slug))
            if game:
                # Requested cost, not filled: an IOC remainder can still rest and fill.
                game_picks = bought_index.setdefault("game_exposure", {}).setdefault(game, {})
                game_picks[pick_id] = game_picks.get(pick_id, 0.0) + actual_cost
            if "partially_filled" in bought_index and pick_id in bought_index["partially_filled"]:
                del bought_index["partially_filled"][pick_id]

            if not self.config.execute_live:
                # NOTE: this is a pure in-memory preview -- it never touches disk.
                # Paper-mode persistence happens one layer up, in
                # run_auto_buyer_cycle(), so constructing a buyer directly (as
                # every unit test does) can never have a filesystem side effect.
                result.dry_run_orders.append(
                    {
                        "status": "paper_filled" if self.config.mode == "paper" else "dry_run_preview",
                        **order_payload,
                    }
                )
                current_spend += actual_cost
                committed_spend += actual_cost
                result.total_spend_usd = round(current_spend, 2)
                continue

            # Live Execution
            assert self.config.mode == "live" and self.config.execute_live is True, (
                f"FATAL: Live order execution reached under invalid configuration! "
                f"mode={self.config.mode!r}, execute_live={self.config.execute_live!r}"
            )
            # Explicit guard that survives `python -O`, outside the submission try/except so it
            # can never be downgraded to a "refused" row. In a cohort-carrying config only a row
            # matched to a live cohort may reach the exchange.
            if not (self.config.mode == "live" and self.config.execute_live is True):
                raise RuntimeError(
                    f"Refusing live submission for {model_id} pick {pick_id}: config is not live-executing"
                )
            if (self.config.cohorts or cohort is not None) and not (
                cohort is not None and cohort.mode == "live"
            ):
                raise RuntimeError(
                    f"Refusing live submission for {model_id} pick {pick_id}: "
                    "not matched to a live cohort in a cohort-governed config"
                )
            if self.config.recheck_enabled_before_submit and not _live_buying_still_enabled():
                # C9: the operator turned the buyer off while this pass was running.
                logger.warning("Auto-buyer disabled mid-pass; no further live submissions (pick %s)", pick_id)
                result.live_halt_reason = "kill_switch"
                break
            available_cash = self.config.available_cash_usd
            pass_committed = committed_spend - today_spend_usd
            if available_cash is not None and pass_committed + actual_cost > available_cash + 1e-9:
                logger.error(
                    "Live buying stopped: pick %s needs $%.2f with $%.2f already committed this pass, "
                    "but the account's buying power was $%.2f",
                    pick_id,
                    actual_cost,
                    pass_committed,
                    available_cash,
                )
                result.alarms.append(
                    _alarm(
                        "insufficient_cash",
                        pick_id=pick_id,
                        market_slug=market_slug,
                        detail=(
                            f"order ${actual_cost:.2f} + committed ${pass_committed:.2f} "
                            f"> buying power ${available_cash:.2f}"
                        ),
                    )
                )
                result.live_halt_reason = "insufficient_cash"
                break
            executor = PolymarketExecutor(
                audit=self.audit,
                confirm=lambda _prompt: "Y",
                live_quote=self._live_quote_fn,
            )

            exec_row = dict(row)
            exec_row["pick_id"] = ticket_pick_id
            exec_row["market_slug"] = market_slug
            exec_row["rationale"] = f"auto_buy market_slug:{market_slug}"

            # Only a gate refusal raised before anything was sent is "refused". An unknown outcome
            # and anything after the order exists are alarms that stop live buying for this pass.
            try:
                sub = executor.execute(
                    ticket=ticket,
                    pick_row=exec_row,
                    execute_flag=True,
                    user_command=True,
                    manual_research_order=False,
                    artifact_qualified=True,
                )
            except OrderSubmitUnknownError as exc:
                unknown_payload: dict[str, Any] = {
                    **order_payload,
                    # Its own row, never merged into a partial row as a top-up.
                    "is_topup": False,
                    "submit_unknown": True,
                    "requested_shares": shares,
                    "requested_cost_usd": actual_cost,
                    "shares": 0.0,
                    "cost_usd": 0.0,
                    "order_ids": [],
                    "fill_known": False,
                    "intent_id": exc.intent_id,
                    "idempotency_key": exc.idempotency_key,
                }
                result.submitted_orders.append(
                    {"status": "submit_unknown", "error": str(exc), **unknown_payload}
                )
                # The order may exist: its requested cost counts against this pass's caps.
                committed_spend += actual_cost
                logger.error(
                    "Live order outcome UNKNOWN for pick %s on %s (key %s): %s -- check the exchange, "
                    "never resubmit",
                    pick_id,
                    market_slug,
                    exc.idempotency_key,
                    exc,
                )
                result.alarms.append(
                    _alarm("submit_unknown", pick_id=pick_id, market_slug=market_slug, detail=str(exc))
                )
                result.live_halt_reason = "submit_unknown"
                try:
                    from model_prediction.portfolio.auto_buyer_ledger import record_auto_buy_execution

                    record_auto_buy_execution(
                        order_payload=unknown_payload,
                        order_id="",
                        order_state="ORDER_STATE_SUBMIT_UNKNOWN",
                        pick_row=row,
                        jsonl_path=self.data_root / "auto_buyer_ledger.jsonl",
                        xlsx_path=self.data_root / "auto_buyer_picks.xlsx",
                        raise_on_write_failure=True,
                    )
                except Exception as record_exc:  # noqa: BLE001 -- recorded as an alarm, never swallowed
                    logger.error(
                        "Failed to record the UNKNOWN live order for pick %s on %s: %s",
                        pick_id,
                        market_slug,
                        record_exc,
                    )
                    result.alarms.append(
                        _alarm(
                            "ledger_write_failed",
                            pick_id=pick_id,
                            market_slug=market_slug,
                            detail=str(record_exc),
                        )
                    )
                break
            except ExecutionGateError as exc:
                result.submitted_orders.append(
                    {
                        "status": "refused",
                        "error": str(exc),
                        # Lets the cycle tell "every order refused for auth or balance" (an alarm and a
                        # failed run) from ordinary gate refusals.
                        "http_status": exc.status_code,
                        "refusal_kind": classify_gate_refusal(exc),
                        **order_payload,
                    }
                )
                continue
            except (RuntimeError, OSError) as exc:
                # Not a gate refusal: the intent may already be audited and the POST may have gone
                # out. The pick stays blocked by its dangling intent; stop and alarm.
                logger.error(
                    "Live submission for pick %s on %s failed unexpectedly: %s", pick_id, market_slug, exc
                )
                result.submitted_orders.append({"status": "error", "error": str(exc), **order_payload})
                result.alarms.append(
                    _alarm("execution_error", pick_id=pick_id, market_slug=market_slug, detail=str(exc))
                )
                result.live_halt_reason = "execution_error"
                break

            oid = sub.get("order_id")
            order_ids = sub.get("order_ids") or [oid]
            filled_shares = float(sub.get("filled_size_shares") or 0.0)
            filled_cost = float(sub.get("estimated_filled_cost_usd") or 0.0)
            fill_known = bool(sub.get("fill_known", True))
            fallback_status = sub.get("fallback_status")
            executed_payload = {
                **order_payload,
                "requested_shares": shares,
                "requested_cost_usd": actual_cost,
                "shares": filled_shares,
                "cost_usd": filled_cost,
                "order_ids": order_ids,
                "fallback_order_id": sub.get("fallback_order_id"),
                "fallback_status": fallback_status,
                "fallback_resting_shares": sub.get("fallback_resting_shares"),
                "fallback_error": sub.get("fallback_error"),
                "fallback_verified_via": sub.get("fallback_verified_via"),
                "good_till_time": sub.get("good_till_time"),
                "fill_known": fill_known,
                "intent_id": sub.get("intent_id"),
                "idempotency_key": sub.get("idempotency_key"),
                "fallback_idempotency_key": sub.get("fallback_idempotency_key"),
            }
            result.submitted_orders.append(
                {
                    "status": sub.get("status", "submitted"),
                    "order_id": oid,
                    "order_state": sub.get("order_state"),
                    **executed_payload,
                }
            )
            # The daily cap counts committed money: a fallback resting on the book can still
            # fill after this cycle, so 30 zero-fill IOCs resting $25 each must not read as $0.
            resting_cost = float(sub.get("fallback_resting_shares") or 0.0) * limit_price
            committed = round(filled_cost + resting_cost, 2)
            if not fill_known:
                committed = max(committed, actual_cost)
            committed_spend += committed
            current_spend += filled_cost
            result.total_spend_usd = round(current_spend, 2)

            halt_reason: str | None = None
            if sub.get("audit_error"):
                result.alarms.append(
                    _alarm(
                        "audit_failed_after_submit",
                        pick_id=pick_id,
                        market_slug=market_slug,
                        detail=str(sub["audit_error"]),
                        order_ids=order_ids,
                    )
                )
                halt_reason = "audit_failed_after_submit"
            if fallback_status in _FALLBACK_ALARM_STATUSES:
                # The IOC result stands; the remainder did not rest as intended. Alarm, keep buying.
                result.alarms.append(
                    _alarm(
                        f"fallback_{fallback_status}",
                        pick_id=pick_id,
                        market_slug=market_slug,
                        detail=str(sub.get("fallback_error") or fallback_status),
                        order_ids=order_ids,
                    )
                )

            # Record directly to dedicated Auto-Buyer Ledger and Logger
            # -- the confirmed *filled* quantity/cost, not the requested
            # ones: an IOC can partially or never fill, and any
            # unfilled remainder is now resting separately (see
            # ioc_fallback_resting) rather than assumed complete here.
            # Record a zero-fill primary when a fallback order is resting,
            # or when the primary fill is unknown, so a later
            # reconcile_pending_auto_buyer_fallbacks() pass can restate it
            # from the exchange instead of leaving a fully untracked live
            # position. A failure here is an alarm (B3): the order exists.
            needs_row = (
                filled_shares > 0
                or executed_payload.get("fallback_order_id")
                or not fill_known
                or fallback_status == "submit_unknown"
            )
            if needs_row:
                try:
                    from model_prediction.portfolio.auto_buyer_ledger import record_auto_buy_execution

                    record_auto_buy_execution(
                        order_payload=executed_payload,
                        order_id=oid,
                        order_state=str(sub.get("order_state") or "FILLED").upper(),
                        pick_row=row,
                        jsonl_path=self.data_root / "auto_buyer_ledger.jsonl",
                        xlsx_path=self.data_root / "auto_buyer_picks.xlsx",
                        raise_on_write_failure=True,
                    )
                except Exception as record_exc:  # noqa: BLE001 -- recorded as an alarm, never swallowed
                    logger.error(
                        "Live order(s) %s for pick %s on %s were placed but NOT recorded in the ledger: %s",
                        order_ids,
                        pick_id,
                        market_slug,
                        record_exc,
                    )
                    result.alarms.append(
                        _alarm(
                            "ledger_write_failed",
                            pick_id=pick_id,
                            market_slug=market_slug,
                            detail=str(record_exc),
                            order_ids=order_ids,
                        )
                    )
                    halt_reason = halt_reason or "ledger_write_failed"

            if halt_reason is not None:
                result.live_halt_reason = halt_reason
                break

            # Polite pacing to respect exchange rate limits
            time.sleep(0.25)

        return result
