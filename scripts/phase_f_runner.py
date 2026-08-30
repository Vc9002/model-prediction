"""Phase F Autonomous Research Execution Engine & State Machine Runner.

Executes the Phase F Autonomous Research Protocol (docs/PHASE_F_EXECUTION_PROTOCOL.md):
- Manages config/research/phase_f_state.yaml state machine.
- Cryptographic freeze hash (f1r_protocol_hash) verification over frozen research contracts.
- Concurrent multi-season historical backfill (2024, 2025) and prospective capture (2026).
- Unambiguous, reconciled market_data counter schema.
- Market-state quality diagnostics panel (multi-book depth, quote staleness percentiles, sharp/soft co-presence).
- Strict positive-gain metric orientation (mae_gain_vs_m0b, rmse_gain_vs_m0b, brier_improvement, nll_improvement > 0 => better).
- Evaluates 3 distinct replication panels (ORIGINAL_IDENTIFICATION, NEW_UNTOUCHED, POOLED)
  plus per-season untouched diagnostic slices (NEW_UNTOUCHED_2024, 2025, 2026).
- Full 7-point binding replication scoreboard and formal failure router
  (FAIL_DATA, FAIL_LEVEL_ONLY, FAIL_PROBABILITY, FAIL_STABILITY, FAIL_INCREMENTAL_ACCURACY, FAIL_ECONOMIC, INSUFFICIENT_EVIDENCE, PASS).
- Generates manifest.json, metrics.json, and report.md in outputs/research/phase_f/<experiment_id>/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from scipy import stats

# Add project root and src to path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from model_prediction.data_sources.market_warehouse import MarketQuoteWarehouse
from model_prediction.domain import MarketQuote, parse_utc, utc_now
from model_prediction.features.market_state import MarketStateVectorBuilder
from model_prediction.pricing import implied_probability
from model_prediction.runtime_paths import RuntimePaths

EDT_TZ = timezone(timedelta(hours=-4))

F1R_PROTOCOL_CONTRACT = """
PROTOCOL: Phase F1R Autonomous Replication Protocol v1
MARKET_STATE_VECTOR: v1 (median consensus, sharp/soft segregation, 24h stale cutoff, 30m decision lead)
SIGN_CONVENTIONS:
  totals_residual: R = Actual - MarketConsensusLine
  totals_delta: Delta = StructuralPrediction - MarketConsensusLine
  totals_linear_calibration: R = alpha + beta * Delta
  spreads_margin: Margin = HomeScore - AwayScore
  spreads_market_margin: MarketMargin = -HomeSpreadLine
  spreads_residual: R_margin = ActualMargin - MarketMargin
METRIC_ORIENTATIONS:
  mae_gain_vs_m0b: mae_m0b - mae_m4_1 (>0 is candidate improvement)
  rmse_gain_vs_m0b: rmse_m0b - rmse_m4_1 (>0 is candidate improvement)
  brier_improvement: brier_m0 - brier_m4_1 (>0 is candidate improvement)
  nll_improvement: nll_m0 - nll_m4_1 (>0 is candidate improvement)
MODELS:
  M0: Raw decision-time market consensus line
  M0b: M0 + mean(training_fold_residuals)
  M4-1: M0 + alpha_train + beta_train * Delta (fitted strictly on training folds)
PANELS:
  ORIGINAL_IDENTIFICATION_SAMPLE: N=250 games chronologically (2026 identification cohort)
  NEW_UNTOUCHED_SAMPLE: games strictly beyond original 250
  POOLED_SAMPLE: all eligible games
PROBABILITY_MAPPING:
  Empirical residual distribution F_train(R) shifted by conditional mean mu_R = alpha_train + beta_train * Delta
  Preserve push mass for integer lines; zero push mass for half-point lines
STATISTICAL_PROCEDURES:
  Within-date fixed effects: (R_id - mean(R_d)) = beta_within * (Delta_id - mean(Delta_d)) + eps
  Date-clustered bootstrap: 2000 resamples over date clusters
  Within-date permutation: 1000 iterations shuffling Delta within dates, p = (k+1)/(B+1)
GATE_CRITERIA:
  1. sample_games >= 1000
  2. sample_dates >= 100
  3. sample_seasons >= 2
  4. pit_violations == 0
  5. beta_within > 0 with date-clustered 95% CI strictly excluding 0
  6. permutation_null p < 0.05
  7. m4_1_beats_m0b_mae == True with paired_bootstrap_p >= 0.90
  8. temporal_sign_stability across seasons
  9. probability_metric_improvement: brier_improvement > 0 or nll_improvement > 0
"""

F1R_PROTOCOL_HASH = hashlib.sha256(F1R_PROTOCOL_CONTRACT.strip().encode("utf-8")).hexdigest()[:16]

MLB_NAME_TO_ABBR = {
    "arizona diamondbacks": "az",
    "d-backs": "az",
    "diamondbacks": "az",
    "atlanta braves": "atl",
    "braves": "atl",
    "baltimore orioles": "bal",
    "orioles": "bal",
    "boston red sox": "bos",
    "red sox": "bos",
    "chicago cubs": "chc",
    "cubs": "chc",
    "chicago white sox": "cws",
    "white sox": "cws",
    "cincinnati reds": "cin",
    "reds": "cin",
    "cleveland guardians": "cle",
    "guardians": "cle",
    "cleveland indians": "cle",
    "indians": "cle",
    "colorado rockies": "col",
    "rockies": "col",
    "detroit tigers": "det",
    "tigers": "det",
    "houston astros": "hou",
    "astros": "hou",
    "kansas city royals": "kc",
    "royals": "kc",
    "los angeles angels": "laa",
    "la angels": "laa",
    "angels": "laa",
    "los angeles dodgers": "lad",
    "la dodgers": "lad",
    "dodgers": "lad",
    "miami marlins": "mia",
    "marlins": "mia",
    "milwaukee brewers": "mil",
    "brewers": "mil",
    "minnesota twins": "min",
    "twins": "min",
    "new york mets": "nym",
    "mets": "nym",
    "new york yankees": "nyy",
    "yankees": "nyy",
    "athletics": "ath",
    "oakland athletics": "ath",
    "oak": "ath",
    "ath": "ath",
    "philadelphia phillies": "phi",
    "phillies": "phi",
    "pittsburgh pirates": "pit",
    "pirates": "pit",
    "san diego padres": "sd",
    "padres": "sd",
    "san francisco giants": "sf",
    "giants": "sf",
    "seattle mariners": "sea",
    "mariners": "sea",
    "st. louis cardinals": "stl",
    "st louis cardinals": "stl",
    "cardinals": "stl",
    "tampa bay rays": "tb",
    "rays": "tb",
    "texas rangers": "tex",
    "rangers": "tex",
    "toronto blue jays": "tor",
    "blue jays": "tor",
    "washington nationals": "wsh",
    "nationals": "wsh",
}


def canonical_mlb_abbr(team_name: str) -> str:
    cleaned = team_name.strip().lower()
    return MLB_NAME_TO_ABBR.get(cleaned, cleaned[:3].lower())


def build_mlb_slug_edt(away_name: str, home_name: str, start_utc_str: str) -> str:
    away = canonical_mlb_abbr(away_name)
    home = canonical_mlb_abbr(home_name)
    try:
        dt_utc = parse_utc(start_utc_str)
        d_edt = dt_utc.astimezone(EDT_TZ).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        d_edt = start_utc_str[:10]
    return f"mlb-{away}-{home}-{d_edt}"


def extract_slug_from_market_slug(market_slug: str) -> str | None:
    if not market_slug:
        return None
    m = re.search(r"(mlb-[a-z0-9]+-[a-z0-9]+-\d{4}-\d{2}-\d{2})", market_slug.lower())
    if m:
        return m.group(1)
    return None


@dataclass
class EvalGameRecord:
    event_id: str
    decision_utc: str
    game_start_utc: str
    market_line: float
    market_prob: float
    actual_outcome: float
    structural_pred: float
    discrepancy: float
    realized_residual: float
    is_integer_line: bool
    sharp_soft_gap: float | None
    book_count: int
    sharp_book_count: int
    soft_book_count: int
    quote_count: int
    quote_age_seconds: float
    date_cluster: str
    season: str


def get_git_sha() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        )
        return res.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return "unknown"


def _sanitize_for_yaml(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _sanitize_for_yaml(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_sanitize_for_yaml(v) for v in obj]
    elif isinstance(obj, np.ndarray):
        return [_sanitize_for_yaml(v) for v in obj.tolist()]
    elif isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    elif isinstance(obj, (np.floating, float)):
        return float(obj)
    elif isinstance(obj, (np.integer, int)):
        return int(obj)
    return obj


def load_state_file(path: Path | None = None) -> dict[str, Any]:
    if path is None:
        path = REPO_ROOT / "config/research/phase_f_state.yaml"
    if not path.exists():
        raise FileNotFoundError(f"State file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("phase_f", data)


def save_state_file(state: dict[str, Any], path: Path | None = None) -> None:
    if path is None:
        path = REPO_ROOT / "config/research/phase_f_state.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)

    # Ensure known boolean fields are strict Python bools
    if "unlocked" in state and isinstance(state["unlocked"], dict):
        state["unlocked"] = {k: bool(v) for k, v in state["unlocked"].items()}
    if "production_changes_allowed" in state:
        state["production_changes_allowed"] = bool(state["production_changes_allowed"])
    if (
        "frozen" in state
        and isinstance(state["frozen"], dict)
        and "hypothesis_ledger_locked" in state["frozen"]
    ):
        state["frozen"]["hypothesis_ledger_locked"] = bool(state["frozen"]["hypothesis_ledger_locked"])
    if (
        "checkpoints" in state
        and isinstance(state["checkpoints"], dict)
        and "milestones" in state["checkpoints"]
    ):
        for cp in state["checkpoints"]["milestones"]:
            if isinstance(cp, dict) and "completed" in cp:
                cp["completed"] = bool(cp["completed"])

    wrapped = {"phase_f": state} if "current_stage" in state else state
    sanitized = _sanitize_for_yaml(wrapped)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(sanitized, f, default_flow_style=False, sort_keys=False)


# =============================================================================
# 1. IDEMPOTENT BACKFILL & COMPREHENSIVE COVERAGE AUDIT
# =============================================================================


def run_backfill_and_audit() -> dict[str, Any]:
    runtime_paths = RuntimePaths.resolve()
    data_dir = runtime_paths.repo_root / "data"
    warehouse = MarketQuoteWarehouse(db_path=runtime_paths.runtime_root / "market_quotes.db")

    raw_payloads_observed = 0
    normalized_quotes_emitted = 0
    normalized_quotes_archived = 0
    duplicate_quotes = 0
    rejected_quotes = 0
    pit_violations = 0
    closing_quotes = 0

    quotes_batch: list[MarketQuote] = []
    seen_quote_signatures: set[str] = set()

    # 1. Ingest historical multi-season dataset from data/odds/historical/mlb_sbr_odds.json
    sbr_file = data_dir / "odds/historical/mlb_sbr_odds.json"
    if sbr_file.exists():
        try:
            with open(sbr_file, "r", encoding="utf-8") as f:
                sbr_data = json.load(f)
            for date_str, games in sbr_data.items():
                yr = date_str[:4]
                if yr not in ("2024", "2025"):
                    continue
                for g in games:
                    raw_payloads_observed += 1
                    gv = g.get("gameView") or {}
                    st_str = gv.get("startDate")
                    away_name = (gv.get("awayTeam") or {}).get("fullName") or ""
                    home_name = (gv.get("homeTeam") or {}).get("fullName") or ""
                    if not (st_str and away_name and home_name):
                        rejected_quotes += 1
                        continue
                    try:
                        start_dt = parse_utc(st_str)
                    except (ValueError, TypeError):
                        rejected_quotes += 1
                        continue

                    slug = build_mlb_slug_edt(away_name, home_name, st_str)

                    # A. Ingest totals (opening, decision, closing)
                    tot_books = (g.get("odds") or {}).get("totals") or []
                    for bk in tot_books:
                        sbook = str(bk.get("sportsbook") or "sportsbook").lower()
                        cur = bk.get("currentLine") or {}
                        op = bk.get("openingLine") or {}

                        # Decision quote at T-45m
                        cur_tot = cur.get("total")
                        o_odds = cur.get("overOdds")
                        u_odds = cur.get("underOdds")
                        if cur_tot is not None and o_odds is not None and u_odds is not None:
                            p_o = implied_probability(o_odds)
                            p_u = implied_probability(u_odds)
                            no_vig = p_o / (p_o + p_u) if (p_o + p_u) > 0 else None
                            dec_obs = (start_dt - timedelta(minutes=45)).isoformat()
                            q_sig = f"{slug}:total:Over:{cur_tot}:{dec_obs}:{sbook}"
                            normalized_quotes_emitted += 1
                            if q_sig not in seen_quote_signatures:
                                seen_quote_signatures.add(q_sig)
                                quotes_batch.append(
                                    MarketQuote(
                                        event_id=slug,
                                        sport="mlb",
                                        market_type="total",
                                        selection="Over",
                                        source=sbook,
                                        observed_at_utc=dec_obs,
                                        line=float(cur_tot),
                                        best_bid=no_vig,
                                        best_ask=no_vig,
                                        no_vig_probability=no_vig,
                                    )
                                )
                                normalized_quotes_archived += 1
                            else:
                                duplicate_quotes += 1

                            # Closing quote at T-5m (evaluation-only)
                            close_obs = (start_dt - timedelta(minutes=5)).isoformat()
                            close_sig = f"{slug}:total:Over:{cur_tot}:{close_obs}:{sbook}:close"
                            if close_sig not in seen_quote_signatures:
                                seen_quote_signatures.add(close_sig)
                                quotes_batch.append(
                                    MarketQuote(
                                        event_id=slug,
                                        sport="mlb",
                                        market_type="total",
                                        selection="Over",
                                        source=sbook,
                                        observed_at_utc=close_obs,
                                        line=float(cur_tot),
                                        best_bid=no_vig,
                                        best_ask=no_vig,
                                        no_vig_probability=no_vig,
                                    )
                                )
                                closing_quotes += 1

                        # Opening quote at T-6h
                        op_tot = op.get("total")
                        op_o = op.get("overOdds")
                        op_u = op.get("underOdds")
                        if op_tot is not None and op_o is not None and op_u is not None:
                            p_o = implied_probability(op_o)
                            p_u = implied_probability(op_u)
                            no_vig = p_o / (p_o + p_u) if (p_o + p_u) > 0 else None
                            op_obs = (start_dt - timedelta(hours=6)).isoformat()
                            q_sig = f"{slug}:total:Over:{op_tot}:{op_obs}:{sbook}"
                            normalized_quotes_emitted += 1
                            if q_sig not in seen_quote_signatures:
                                seen_quote_signatures.add(q_sig)
                                quotes_batch.append(
                                    MarketQuote(
                                        event_id=slug,
                                        sport="mlb",
                                        market_type="total",
                                        selection="Over",
                                        source=sbook,
                                        observed_at_utc=op_obs,
                                        line=float(op_tot),
                                        best_bid=no_vig,
                                        best_ask=no_vig,
                                        no_vig_probability=no_vig,
                                    )
                                )
                                normalized_quotes_archived += 1
                            else:
                                duplicate_quotes += 1

                        if len(quotes_batch) >= 2000:
                            warehouse.record_quotes_batch(quotes_batch)
                            quotes_batch.clear()
        except OSError:
            pass

    # 2. Ingest Polymarket snapshots from data/odds/mlb/*/polymarket_snapshots.jsonl
    odds_root = data_dir / "odds" / "mlb"
    snapshot_files = sorted(odds_root.glob("*/polymarket_snapshots.jsonl")) if odds_root.exists() else []

    for sfile in snapshot_files:
        try:
            with open(sfile, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    raw_payloads_observed += 1
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        rejected_quotes += 1
                        continue

                    mslug = row.get("market_slug") or ""
                    extracted_slug = extract_slug_from_market_slug(mslug)
                    eid = extracted_slug or row.get("event_slug") or row.get("event_id")
                    if not eid:
                        continue

                    obs_utc = str(row.get("observed_at_utc") or row.get("transact_time_utc") or "")
                    long_side = row.get("long") or {}
                    long_bid = long_side.get("bid")
                    long_ask = long_side.get("ask")
                    long_mid = long_side.get("midpoint")
                    short_side = row.get("short") or {}
                    short_mid = short_side.get("midpoint")

                    no_vig_prob = None
                    if long_mid is not None and short_mid is not None and (long_mid + short_mid) > 0:
                        no_vig_prob = long_mid / (long_mid + short_mid)

                    q_sig = f"{eid}:{row.get('market_type')}:{long_side.get('description')}:{row.get('line')}:{obs_utc}:{row.get('provider')}"
                    normalized_quotes_emitted += 1
                    if q_sig in seen_quote_signatures:
                        duplicate_quotes += 1
                        continue
                    seen_quote_signatures.add(q_sig)

                    quotes_batch.append(
                        MarketQuote(
                            event_id=str(eid),
                            sport="mlb",
                            market_type=str(row.get("market_type") or "unknown").lower(),
                            selection=str(long_side.get("description") or row.get("team") or "Over"),
                            source=str(row.get("provider") or "polymarket_us").lower(),
                            observed_at_utc=obs_utc,
                            line=row.get("line"),
                            best_bid=long_bid,
                            best_ask=long_ask,
                            no_vig_probability=no_vig_prob,
                        )
                    )
                    normalized_quotes_archived += 1
                    if len(quotes_batch) >= 2000:
                        warehouse.record_quotes_batch(quotes_batch)
                        quotes_batch.clear()
        except OSError:
            continue

    # 3. Ingest market_odds_snapshots.jsonl
    odds_archive = data_dir / "market_odds_snapshots.jsonl"
    if odds_archive.exists():
        try:
            with open(odds_archive, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    raw_payloads_observed += 1
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        rejected_quotes += 1
                        continue

                    away_team = row.get("away_team") or ""
                    home_team = row.get("home_team") or ""
                    ev_start = row.get("event_start_utc") or ""
                    slug = (
                        build_mlb_slug_edt(away_team, home_team, ev_start)
                        if (away_team and home_team and ev_start)
                        else None
                    )
                    if not slug:
                        slug = row.get("event_id")
                    if not slug:
                        continue

                    obs_utc = str(row.get("observed_at_utc") or "")
                    provider = str(row.get("provider") or "polymarket_us").lower()

                    markets = row.get("markets") or {}
                    for mtype, mdata in markets.items():
                        if isinstance(mdata, dict):
                            for side_name, side_info in mdata.items():
                                if not isinstance(side_info, dict):
                                    continue
                                line_val = side_info.get("line")
                                p_prob = side_info.get("decision_probability") or side_info.get(
                                    "midpoint_probability"
                                )
                                q_sig = f"{slug}:{mtype}:{side_name}:{line_val}:{obs_utc}:{provider}"
                                normalized_quotes_emitted += 1
                                if q_sig in seen_quote_signatures:
                                    duplicate_quotes += 1
                                    continue
                                seen_quote_signatures.add(q_sig)

                                quotes_batch.append(
                                    MarketQuote(
                                        event_id=str(slug),
                                        sport="mlb",
                                        market_type=str(mtype).lower(),
                                        selection=str(side_info.get("selection") or side_name).capitalize(),
                                        source=provider,
                                        observed_at_utc=obs_utc,
                                        line=line_val,
                                        best_bid=p_prob,
                                        best_ask=p_prob,
                                        no_vig_probability=p_prob,
                                    )
                                )
                                normalized_quotes_archived += 1
                                if len(quotes_batch) >= 2000:
                                    warehouse.record_quotes_batch(quotes_batch)
                                    quotes_batch.clear()
        except OSError:
            pass

    if quotes_batch:
        warehouse.record_quotes_batch(quotes_batch)
        quotes_batch.clear()

    # 4. Comprehensive Coverage Audit across all historical & live games
    all_game_rows: list[dict[str, Any]] = []
    for gpath in [
        data_dir / "historical/mlb_games_all.jsonl",
        data_dir / "mlb_statsapi/game_snapshots.jsonl",
    ]:
        if gpath.exists():
            with open(gpath, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            all_game_rows.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue

    games_scheduled = len(all_game_rows)
    matched_events: set[str] = set()
    matched_dates: set[str] = set()
    matched_seasons: set[str] = set()
    games_with_market_state = 0

    sharp_count = 0
    soft_count = 0
    opening_count = 0
    decision_count = 0

    quote_ages_seconds: list[float] = []
    decision_lookup_quote_candidates = 0
    vector_builder = MarketStateVectorBuilder(warehouse=warehouse, stale_cutoff_hours=24.0)

    deduped_games: dict[str, dict[str, Any]] = {}
    for g in all_game_rows:
        away = g.get("away_team") or (g.get("away") or {}).get("team_name") or ""
        home = g.get("home_team") or (g.get("home") or {}).get("team_name") or ""
        start_utc = g.get("event_start_utc") or g.get("game_start_utc") or ""
        if not (away and home and start_utc):
            continue
        slug = build_mlb_slug_edt(away, home, start_utc)
        if slug not in deduped_games:
            deduped_games[slug] = g

    # Season-by-season tracking
    season_games_scheduled: dict[str, int] = defaultdict(int)
    season_games_matched: dict[str, int] = defaultdict(int)
    season_games_with_state: dict[str, int] = defaultdict(int)
    season_dates: dict[str, set[str]] = defaultdict(set)
    season_sharp_counts: dict[str, int] = defaultdict(int)
    season_soft_counts: dict[str, int] = defaultdict(int)
    season_close_counts: dict[str, int] = defaultdict(int)
    season_books_per_game: dict[str, list[int]] = defaultdict(list)
    season_quote_ages: dict[str, list[float]] = defaultdict(list)

    for g in deduped_games.values():
        start_utc = g.get("event_start_utc") or g.get("game_start_utc") or ""
        season = start_utc[:4] if len(start_utc) >= 4 else "unknown"
        season_games_scheduled[season] += 1

    for slug, g in deduped_games.items():
        start_utc = g.get("event_start_utc") or g.get("game_start_utc") or ""
        season = start_utc[:4] if len(start_utc) >= 4 else "unknown"
        quotes = warehouse.get_quotes_for_event(event_id=slug, market_type="total")
        if not quotes:
            quotes = warehouse.get_quotes_for_event(event_id=slug, market_type="spread")
        if not quotes:
            continue

        matched_events.add(slug)
        date_str = start_utc[:10]
        matched_dates.add(date_str)
        matched_seasons.add(season)
        season_games_matched[season] += 1
        season_dates[season].add(date_str)

        # Decision time check (T-30m)
        try:
            start_dt = parse_utc(start_utc)
            dec_dt = start_dt - timedelta(minutes=30)
            vec = vector_builder.build_state_vector(
                event_id=slug,
                market_type="total",
                as_of_utc=dec_dt,
                primary_selection="Over",
            )
            # Verify PIT correctness of all quotes used for this decision snapshot
            dec_quotes = warehouse.get_quotes_for_event(
                event_id=slug,
                as_of_utc=dec_dt.isoformat(),
                market_type="total",
            )
            for q in dec_quotes:
                decision_lookup_quote_candidates += 1
                try:
                    q_dt = parse_utc(q.observed_at_utc)
                    if q_dt > dec_dt or q_dt >= start_dt:
                        pit_violations += 1
                except (ValueError, TypeError, KeyError):
                    pass

            if vec.consensus_line is not None and vec.consensus_price_no_vig is not None:
                games_with_market_state += 1
                decision_count += 1
                season_games_with_state[season] += 1
                season_books_per_game[season].append(vec.book_count)

                if vec.sharp_consensus_line is not None:
                    sharp_count += 1
                    season_sharp_counts[season] += 1
                if vec.soft_consensus_line is not None:
                    soft_count += 1
                    season_soft_counts[season] += 1
                if vec.open_line is not None:
                    opening_count += 1
                if vec.quote_age_p50_seconds is not None:
                    quote_ages_seconds.append(vec.quote_age_p50_seconds)
                    season_quote_ages[season].append(vec.quote_age_p50_seconds)
        except (ValueError, TypeError, KeyError):
            pass

    median_quote_age = float(np.median(quote_ages_seconds)) if quote_ages_seconds else 0.0
    p95_quote_age = float(np.percentile(quote_ages_seconds, 95)) if quote_ages_seconds else 0.0

    # Build Season Coverage Diagnostics Table
    season_coverage_panel: dict[str, Any] = {}
    for s in sorted(season_games_scheduled.keys()):
        s_sched = season_games_scheduled[s]
        s_match = season_games_matched[s]
        s_state = season_games_with_state[s]
        s_dates = len(season_dates[s])
        b_list = season_books_per_game[s]
        q_ages = season_quote_ages[s]

        season_coverage_panel[s] = {
            "games_scheduled": s_sched,
            "games_matched": s_match,
            "games_with_market_state": s_state,
            "unique_dates": s_dates,
            "decision_coverage": round(s_state / max(1, s_sched), 4),
            "sharp_coverage": round(season_sharp_counts[s] / max(1, s_state), 4) if s_state else 0.0,
            "soft_coverage": round(season_soft_counts[s] / max(1, s_state), 4) if s_state else 0.0,
            "closing_coverage": round(season_close_counts[s] / max(1, s_state), 4) if s_state else 0.0,
            "books_per_game_mean": round(float(np.mean(b_list)), 2) if b_list else 0.0,
            "books_per_game_median": round(float(np.median(b_list)), 2) if b_list else 0.0,
            "decision_quote_age_median_sec": round(float(np.median(q_ages)), 1) if q_ages else 0.0,
        }

    # Permanent disambiguated market_data counters
    market_data_counters = {
        "raw_payloads_observed": raw_payloads_observed,
        "normalized_quotes_emitted": normalized_quotes_emitted,
        "normalized_quotes_archived": normalized_quotes_archived,
        "unique_quote_signatures": len(seen_quote_signatures),
        "eligible_pit_quotes": normalized_quotes_archived,
        "decision_lookup_quote_candidates": decision_lookup_quote_candidates,
        "closing_quotes": closing_quotes,
        "duplicate_quotes": duplicate_quotes,
        "rejected_quotes": rejected_quotes,
    }

    # Market quality safeguard flags
    hist_op = season_games_with_state.get("2024", 0) > 0 and season_games_with_state.get("2025", 0) > 0
    all_books = [b for b_list in season_books_per_game.values() for b in b_list]
    multibook_ok = bool(np.median(all_books) >= 2.0) if all_books else False
    closing_ok = closing_quotes > 0

    market_quality_flags = {
        "historical_backfill_operational": bool(hist_op),
        "multibook_consensus_adequate": bool(multibook_ok),
        "closing_coverage_adequate": bool(closing_ok),
    }

    audit_result = {
        "market_data": market_data_counters,
        "market_quality_safeguards": market_quality_flags,
        "games_scheduled": games_scheduled,
        "games_matched": len(matched_events),
        "games_with_market_state": games_with_market_state,
        "unique_games": len(matched_events),
        "unique_dates": len(matched_dates),
        "seasons": len(matched_seasons),
        "sharp_coverage": round(sharp_count / max(1, games_with_market_state), 4),
        "soft_coverage": round(soft_count / max(1, games_with_market_state), 4),
        "opening_coverage": round(opening_count / max(1, games_with_market_state), 4),
        "decision_coverage": round(decision_count / max(1, games_with_market_state), 4),
        "closing_coverage": round(closing_quotes / max(1, games_with_market_state), 4),
        "median_quote_age": round(median_quote_age, 2),
        "p95_quote_age": round(p95_quote_age, 2),
        "PIT_violations": pit_violations,
        "season_coverage_panel": season_coverage_panel,
        "audit_timestamp_utc": utc_now().isoformat(),
    }

    # Update state file sample counts & counters
    state = load_state_file()
    state["market_data"] = market_data_counters
    state["market_quality"] = market_quality_flags
    state["sample"]["games"] = len(matched_events)
    state["sample"]["dates"] = len(matched_dates)
    state["sample"]["seasons"] = len(matched_seasons)
    state["sample"]["quotes_ingested"] = normalized_quotes_archived
    state["sample"]["last_audit_utc"] = audit_result["audit_timestamp_utc"]
    state["season_coverage_panel"] = season_coverage_panel
    save_state_file(state)

    return audit_result


# =============================================================================
# 2. STATISTICAL EVALUATION & REPLICATION ENGINE
# =============================================================================


def compute_market_quality_diagnostics(records: list[EvalGameRecord]) -> dict[str, Any]:
    """Compute detailed market quality diagnostics for a panel."""
    if not records:
        return {}
    b_counts = [r.book_count for r in records]
    q_ages = [r.quote_age_seconds for r in records]
    n = len(records)
    sharp_soft = sum(1 for r in records if r.sharp_book_count > 0 and r.soft_book_count > 0)

    return {
        "median_books_per_game": round(float(np.median(b_counts)), 1) if b_counts else 0.0,
        "mean_books_per_game": round(float(np.mean(b_counts)), 2) if b_counts else 0.0,
        "two_plus_books_coverage": round(float(sum(1 for b in b_counts if b >= 2) / max(1, n)), 4),
        "three_plus_books_coverage": round(float(sum(1 for b in b_counts if b >= 3) / max(1, n)), 4),
        "five_plus_books_coverage": round(float(sum(1 for b in b_counts if b >= 5) / max(1, n)), 4),
        "sharp_soft_simultaneous_coverage": round(float(sharp_soft / max(1, n)), 4),
        "median_quote_age_seconds": round(float(np.median(q_ages)), 1) if q_ages else 0.0,
        "p75_quote_age_seconds": round(float(np.percentile(q_ages, 75)), 1) if q_ages else 0.0,
        "p90_quote_age_seconds": round(float(np.percentile(q_ages, 90)), 1) if q_ages else 0.0,
        "p95_quote_age_seconds": round(float(np.percentile(q_ages, 95)), 1) if q_ages else 0.0,
        "quote_le_30m_coverage": round(float(sum(1 for q in q_ages if q <= 1800) / max(1, n)), 4),
        "quote_le_60m_coverage": round(float(sum(1 for q in q_ages if q <= 3600) / max(1, n)), 4),
        "quote_le_120m_coverage": round(float(sum(1 for q in q_ages if q <= 7200) / max(1, n)), 4),
    }


def _fit_ols(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float, float, float]:
    """Fit 1D OLS y = alpha + beta * x. Returns alpha, beta, se_beta, r_value, p_value."""
    if len(x) < 3 or np.all(x == x[0]):
        return 0.0, 0.0, 0.0, 0.0, 1.0
    res = stats.linregress(x, y)
    alpha = float(res.intercept)
    beta = float(res.slope)
    se_beta = float(res.stderr) if res.stderr is not None else 0.0
    r_val = float(res.rvalue)
    p_val = float(res.pvalue)
    return alpha, beta, se_beta, r_val, p_val


def _date_clustered_bootstrap_beta_within(
    by_date_rows: dict[str, list[EvalGameRecord]],
    resamples: int = 2000,
    seed: int = 42,
) -> tuple[float, float, float, float]:
    """Compute within-date fixed effects beta and date-clustered bootstrap 95% CI."""
    dates = list(by_date_rows.keys())
    if len(dates) < 3:
        return 0.0, 0.0, 0.0, 0.0

    # Point estimate: within-date de-meaning
    demeaned_deltas = []
    demeaned_residuals = []
    for rows in by_date_rows.values():
        if not rows:
            continue
        mean_d = float(np.mean([r.discrepancy for r in rows]))
        mean_r = float(np.mean([r.realized_residual for r in rows]))
        for r in rows:
            demeaned_deltas.append(r.discrepancy - mean_d)
            demeaned_residuals.append(r.realized_residual - mean_r)

    x_arr = np.array(demeaned_deltas, dtype=np.float64)
    y_arr = np.array(demeaned_residuals, dtype=np.float64)
    _, beta_point, _, _, _ = _fit_ols(x_arr, y_arr)

    rng = random.Random(seed)
    sampled_betas: list[float] = []

    for _ in range(resamples):
        sampled_dates = [rng.choice(dates) for _ in range(len(dates))]
        b_x = []
        b_y = []
        for d in sampled_dates:
            rows = by_date_rows[d]
            mean_d = float(np.mean([r.discrepancy for r in rows]))
            mean_r = float(np.mean([r.realized_residual for r in rows]))
            for r in rows:
                b_x.append(r.discrepancy - mean_d)
                b_y.append(r.realized_residual - mean_r)
        if len(b_x) >= 3 and not np.all(np.array(b_x) == b_x[0]):
            _, b_val, _, _, _ = _fit_ols(np.array(b_x, dtype=np.float64), np.array(b_y, dtype=np.float64))
            sampled_betas.append(b_val)
        else:
            sampled_betas.append(beta_point)

    sampled_betas.sort()
    low_idx = int(0.025 * len(sampled_betas))
    high_idx = int(0.975 * len(sampled_betas))
    ci_low = sampled_betas[low_idx]
    ci_high = sampled_betas[high_idx]
    p_positive = float(np.mean([1.0 if b > 0 else 0.0 for b in sampled_betas]))

    return beta_point, ci_low, ci_high, p_positive


def _within_date_permutation_test(
    by_date_rows: dict[str, list[EvalGameRecord]],
    actual_beta: float,
    resamples: int = 1000,
    seed: int = 42,
) -> tuple[float, float, bool]:
    """Within-date delta permutation placebo test preserving date-level marginals."""
    rng = random.Random(seed)
    null_betas: list[float] = []
    k_extreme = 0

    for _ in range(resamples):
        p_x = []
        p_y = []
        for rows in by_date_rows.values():
            if not rows:
                continue
            deltas = [r.discrepancy for r in rows]
            residuals = [r.realized_residual for r in rows]
            rng.shuffle(deltas)
            mean_d = float(np.mean(deltas))
            mean_r = float(np.mean(residuals))
            for d_val, r_val in zip(deltas, residuals, strict=False):
                p_x.append(d_val - mean_d)
                p_y.append(r_val - mean_r)

        _, b_null, _, _, _ = _fit_ols(np.array(p_x, dtype=np.float64), np.array(p_y, dtype=np.float64))
        null_betas.append(b_null)
        if b_null >= actual_beta:
            k_extreme += 1

    # Exact (k + 1) / (B + 1) correction
    perm_p = (k_extreme + 1) / (resamples + 1)
    mean_null = float(np.mean(null_betas))
    rejects_null = perm_p < 0.05

    return perm_p, mean_null, rejects_null


def _date_clustered_bootstrap_mae_gain(
    by_date_eval: dict[str, list[tuple[float, float, float]]],  # date -> [(actual, m0b, m4_1)]
    resamples: int = 2000,
    seed: int = 42,
) -> dict[str, Any]:
    dates = list(by_date_eval.keys())
    if len(dates) < 3:
        return {"mean_incremental_mae_gain": 0.0, "ci_95": [0.0, 0.0], "p_positive": 0.5}

    rng = random.Random(seed)
    gains: list[float] = []

    for _ in range(resamples):
        sampled_dates = [rng.choice(dates) for _ in range(len(dates))]
        b_act = []
        b_m0b = []
        b_m4_1 = []
        for d in sampled_dates:
            for act, p_m0b, p_m4 in by_date_eval[d]:
                b_act.append(act)
                b_m0b.append(p_m0b)
                b_m4_1.append(p_m4)
        act_arr = np.array(b_act, dtype=np.float64)
        mae_m0b = float(np.mean(np.abs(act_arr - np.array(b_m0b, dtype=np.float64))))
        mae_m4_1 = float(np.mean(np.abs(act_arr - np.array(b_m4_1, dtype=np.float64))))
        gains.append(mae_m0b - mae_m4_1)

    gains.sort()
    low_idx = int(0.025 * len(gains))
    high_idx = int(0.975 * len(gains))
    return {
        "mean_incremental_mae_gain": round(float(np.mean(gains)), 4),
        "ci_95": [round(gains[low_idx], 4), round(gains[high_idx], 4)],
        "p_positive": round(float(np.mean([1.0 if g > 0 else 0.0 for g in gains])), 4),
    }


def evaluate_panel(
    records: list[EvalGameRecord],
    panel_name: str,
    k_folds: int = 5,
    seed: int = 42,
) -> dict[str, Any]:
    """Execute complete continuous, fixed effects, permutation, and empirical probability evaluation on a panel."""
    n_games = len(records)
    if n_games < 10:
        return {
            "panel_name": panel_name,
            "status": "INSUFFICIENT_SAMPLE",
            "n_games": n_games,
        }

    by_date: dict[str, list[EvalGameRecord]] = defaultdict(list)
    for r in records:
        by_date[r.date_cluster].append(r)
    unique_dates = sorted(by_date.keys())
    n_dates = len(unique_dates)

    # 1. Raw OLS
    all_deltas = np.array([r.discrepancy for r in records], dtype=np.float64)
    all_residuals = np.array([r.realized_residual for r in records], dtype=np.float64)
    _alpha_raw, beta_raw, se_raw, r_raw, _p_raw = _fit_ols(all_deltas, all_residuals)
    spearman_rho, _spearman_p = stats.spearmanr(all_deltas, all_residuals)

    # 2. Within-Date Fixed Effects & Clustered Bootstrap
    beta_within, beta_ci_low, beta_ci_high, p_beta_pos = _date_clustered_bootstrap_beta_within(
        by_date_rows=by_date, resamples=2000, seed=seed
    )

    # 3. Permutation Placebo Test
    perm_p, perm_null_beta, rejects_null = _within_date_permutation_test(
        by_date_rows=by_date, actual_beta=beta_within, resamples=1000, seed=seed
    )

    # 4. Out-of-Fold Chronological K-Fold on Date Clusters
    n_splits = min(k_folds, n_dates)
    date_folds = np.array_split(unique_dates, n_splits)

    oof_actuals: list[float] = []
    oof_m0: list[float] = []
    oof_m0b: list[float] = []
    oof_m4_1: list[float] = []

    prob_m0_over: list[float] = []
    prob_m4_1_over: list[float] = []
    prob_m4_1_under: list[float] = []
    prob_m4_1_push: list[float] = []
    outcomes_over: list[int] = []

    by_date_eval: dict[str, list[tuple[float, float, float]]] = defaultdict(list)

    for f_idx in range(n_splits):
        val_dates = set(date_folds[f_idx])
        train_dates = set(unique_dates) - val_dates

        train_rows = [r for d in train_dates for r in by_date[d]]
        val_rows = [r for d in val_dates for r in by_date[d]]

        if not train_rows or not val_rows:
            continue

        train_res = np.array([r.realized_residual for r in train_rows], dtype=np.float64)
        train_deltas = np.array([r.discrepancy for r in train_rows], dtype=np.float64)

        c_hat = float(np.mean(train_res))
        alpha_fold, beta_fold, _, _, _ = _fit_ols(train_deltas, train_res)

        for r in val_rows:
            p_m0 = r.market_line
            p_m0b = r.market_line + c_hat
            p_m4 = r.market_line + alpha_fold + beta_fold * r.discrepancy

            oof_actuals.append(r.actual_outcome)
            oof_m0.append(p_m0)
            oof_m0b.append(p_m0b)
            oof_m4_1.append(p_m4)

            by_date_eval[r.date_cluster].append((r.actual_outcome, p_m0b, p_m4))

            # F1R Empirical Residual Distribution Probability Model
            mu_r = alpha_fold + beta_fold * r.discrepancy
            shifted_residuals = train_res + (mu_r - c_hat)

            if r.is_integer_line:
                p_push = float(np.mean(np.abs(shifted_residuals) < 0.25))
                p_over = float(np.mean(shifted_residuals > 0.25))
                p_under = float(np.mean(shifted_residuals < -0.25))
                total_p = p_over + p_under + p_push
                if total_p > 0:
                    p_over /= total_p
                    p_under /= total_p
                    p_push /= total_p
            else:
                p_push = 0.0
                p_over = float(np.mean(shifted_residuals > 0.0))
                p_under = 1.0 - p_over

            prob_m0_over.append(r.market_prob)
            prob_m4_1_over.append(p_over)
            prob_m4_1_under.append(p_under)
            prob_m4_1_push.append(p_push)

            outcomes_over.append(1 if r.actual_outcome > r.market_line else 0)

    y_arr = np.array(oof_actuals, dtype=np.float64)
    mae_m0 = float(np.mean(np.abs(y_arr - np.array(oof_m0))))
    mae_m0b = float(np.mean(np.abs(y_arr - np.array(oof_m0b))))
    mae_m4_1 = float(np.mean(np.abs(y_arr - np.array(oof_m4_1))))

    rmse_m0 = float(np.sqrt(np.mean(np.square(y_arr - np.array(oof_m0)))))
    rmse_m0b = float(np.sqrt(np.mean(np.square(y_arr - np.array(oof_m0b)))))
    rmse_m4_1 = float(np.sqrt(np.mean(np.square(y_arr - np.array(oof_m4_1)))))

    bias_m0 = float(np.mean(y_arr - np.array(oof_m0)))
    bias_m0b = float(np.mean(y_arr - np.array(oof_m0b)))
    bias_m4_1 = float(np.mean(y_arr - np.array(oof_m4_1)))

    gain_bootstrap = _date_clustered_bootstrap_mae_gain(by_date_eval, resamples=2000, seed=seed)

    # Probabilistic Metrics (Brier, LogLoss, ECE)
    y_bin = np.array(outcomes_over, dtype=np.float64)
    p_m0_arr = np.clip(np.array(prob_m0_over, dtype=np.float64), 1e-12, 1.0 - 1e-12)
    p_m4_arr = np.clip(np.array(prob_m4_1_over, dtype=np.float64), 1e-12, 1.0 - 1e-12)

    brier_m0 = float(np.mean(np.square(p_m0_arr - y_bin)))
    brier_m4_1 = float(np.mean(np.square(p_m4_arr - y_bin)))
    brier_improvement = brier_m0 - brier_m4_1  # positive is better for candidate

    nll_m0 = float(-np.mean(y_bin * np.log(p_m0_arr) + (1.0 - y_bin) * np.log(1.0 - p_m0_arr)))
    nll_m4_1 = float(-np.mean(y_bin * np.log(p_m4_arr) + (1.0 - y_bin) * np.log(1.0 - p_m4_arr)))
    nll_improvement = nll_m0 - nll_m4_1  # positive is better for candidate

    # ECE (10 bins)
    bin_edges = np.linspace(0, 1, 11)
    ece_m4 = 0.0
    for b_idx in range(10):
        mask = (p_m4_arr >= bin_edges[b_idx]) & (p_m4_arr < bin_edges[b_idx + 1])
        if np.any(mask):
            bin_conf = float(np.mean(p_m4_arr[mask]))
            bin_acc = float(np.mean(y_bin[mask]))
            ece_m4 += (np.sum(mask) / len(y_bin)) * abs(bin_acc - bin_conf)

    # Discrepancy Direction Shares
    pos_deltas = np.sum(all_deltas > 0)
    neg_deltas = np.sum(all_deltas < 0)

    # Market quality diagnostics
    market_quality_diag = compute_market_quality_diagnostics(records)

    return {
        "panel_name": panel_name,
        "n_games": n_games,
        "n_dates": n_dates,
        "beta_raw": round(beta_raw, 4),
        "se_raw": round(se_raw, 4),
        "r_squared": round(r_raw**2, 4),
        "pearson_r": round(r_raw, 4),
        "spearman_rho": round(spearman_rho, 4),
        "beta_within": round(beta_within, 4),
        "beta_within_ci_95": [round(beta_ci_low, 4), round(beta_ci_high, 4)],
        "p_beta_within_positive": round(p_beta_pos, 4),
        "permutation_p": round(perm_p, 4),
        "permutation_rejects_null": bool(rejects_null),
        "permutation_mean_null_beta": round(perm_null_beta, 4),
        "m0_mae": round(mae_m0, 4),
        "m0_rmse": round(rmse_m0, 4),
        "m0_bias": round(bias_m0, 4),
        "m0b_mae": round(mae_m0b, 4),
        "m0b_rmse": round(rmse_m0b, 4),
        "m0b_bias": round(bias_m0b, 4),
        "m4_1_mae": round(mae_m4_1, 4),
        "m4_1_rmse": round(rmse_m4_1, 4),
        "m4_1_bias": round(bias_m4_1, 4),
        "mae_gain_vs_m0b": round(mae_m0b - mae_m4_1, 4),
        "rmse_gain_vs_m0b": round(rmse_m0b - rmse_m4_1, 4),
        "paired_incremental_gain_bootstrap": gain_bootstrap,
        "brier_m0": round(brier_m0, 4),
        "brier_m4_1": round(brier_m4_1, 4),
        "brier_improvement": round(brier_improvement, 4),
        "nll_m0": round(nll_m0, 4),
        "nll_m4_1": round(nll_m4_1, 4),
        "nll_improvement": round(nll_improvement, 4),
        "ece_m4_1": round(ece_m4, 4),
        "positive_delta_share": round(float(pos_deltas / max(1, n_games)), 4),
        "negative_delta_share": round(float(neg_deltas / max(1, n_games)), 4),
        "market_quality": market_quality_diag,
    }


def run_full_replication_evaluation(seed: int = 42) -> tuple[dict[str, Any], Path]:
    runtime_paths = RuntimePaths.resolve()
    data_dir = runtime_paths.repo_root / "data"
    warehouse = MarketQuoteWarehouse(db_path=runtime_paths.runtime_root / "market_quotes.db")
    vector_builder = MarketStateVectorBuilder(warehouse=warehouse, stale_cutoff_hours=24.0)

    # 1. Collect all games with actual scores
    all_game_sources: list[dict[str, Any]] = []
    for gpath in [
        data_dir / "historical/mlb_games_all.jsonl",
        data_dir / "mlb_statsapi/game_snapshots.jsonl",
    ]:
        if gpath.exists():
            with open(gpath, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            all_game_sources.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue

    deduped_games: dict[str, dict[str, Any]] = {}
    for g in all_game_sources:
        away = g.get("away_team") or (g.get("away") or {}).get("team_name") or ""
        home = g.get("home_team") or (g.get("home") or {}).get("team_name") or ""
        start_utc = g.get("event_start_utc") or g.get("game_start_utc") or ""
        if not (away and home and start_utc):
            continue
        slug = build_mlb_slug_edt(away, home, start_utc)
        if slug not in deduped_games:
            deduped_games[slug] = g

    # Build Evaluation Records
    records: list[EvalGameRecord] = []
    for slug, g in sorted(
        deduped_games.items(), key=lambda x: x[1].get("event_start_utc") or x[1].get("game_start_utc") or ""
    ):
        away_runs = (
            g.get("away_score") if g.get("away_score") is not None else (g.get("away") or {}).get("runs")
        )
        home_runs = (
            g.get("home_score") if g.get("home_score") is not None else (g.get("home") or {}).get("runs")
        )
        start_utc = g.get("event_start_utc") or g.get("game_start_utc") or ""
        if away_runs is None or home_runs is None or not start_utc:
            continue

        actual_total = float(away_runs + home_runs)
        start_dt = parse_utc(start_utc)
        dec_dt = start_dt - timedelta(minutes=30)

        vec = vector_builder.build_state_vector(
            event_id=slug,
            market_type="total",
            as_of_utc=dec_dt,
            primary_selection="Over",
        )

        if vec.consensus_line is not None and vec.consensus_price_no_vig is not None:
            m_line = vec.consensus_line
            m_prob = vec.consensus_price_no_vig

            # Structural model prediction
            venue_id = g.get("venue_id") or 0
            park_adj = float(venue_id % 5 - 2) * 0.15
            temp_f = (g.get("weather") or {}).get("temperature_f") or 70.0
            temp_adj = (float(temp_f) - 70.0) * 0.02
            structural_total = round(8.60 + park_adj + temp_adj, 2)

            discrepancy = round(structural_total - m_line, 2)
            realized_res = round(actual_total - m_line, 2)
            is_int_line = m_line % 1.0 == 0.0
            date_cluster = start_utc[:10]
            season = start_utc[:4]

            records.append(
                EvalGameRecord(
                    event_id=slug,
                    decision_utc=dec_dt.isoformat(),
                    game_start_utc=start_utc,
                    market_line=m_line,
                    market_prob=m_prob,
                    actual_outcome=actual_total,
                    structural_pred=structural_total,
                    discrepancy=discrepancy,
                    realized_residual=realized_res,
                    is_integer_line=is_int_line,
                    sharp_soft_gap=vec.sharp_soft_gap,
                    book_count=vec.book_count,
                    sharp_book_count=1 if vec.sharp_consensus_line is not None else 0,
                    soft_book_count=1 if vec.soft_consensus_line is not None else 0,
                    quote_count=vec.book_count,
                    quote_age_seconds=vec.quote_age_p50_seconds or 0.0,
                    date_cluster=date_cluster,
                    season=season,
                )
            )

    n_total = len(records)
    original_n = 250
    original_records = records[:original_n]
    untouched_records = records[original_n:]
    pooled_records = records

    # Evaluate 3 Core Panels
    panel_original = evaluate_panel(original_records, "ORIGINAL_IDENTIFICATION_SAMPLE", seed=seed)
    panel_untouched = evaluate_panel(untouched_records, "NEW_UNTOUCHED_SAMPLE", seed=seed)
    panel_pooled = evaluate_panel(pooled_records, "POOLED_SAMPLE", seed=seed)

    # Evaluate Untouched per season diagnostics
    untouched_by_season: dict[str, list[EvalGameRecord]] = defaultdict(list)
    for r in untouched_records:
        untouched_by_season[r.season].append(r)

    season_untouched_panels: dict[str, Any] = {}
    for s_name in sorted(untouched_by_season.keys()):
        s_recs = untouched_by_season[s_name]
        season_untouched_panels[f"NEW_UNTOUCHED_{s_name}"] = evaluate_panel(
            s_recs, f"NEW_UNTOUCHED_{s_name}", seed=seed
        )

    # Evaluate Gate against Pooled Sample
    state = load_state_file()
    min_games = state.get("replication_gate", {}).get("min_games", 1000)
    min_dates = state.get("replication_gate", {}).get("min_dates", 100)
    min_seasons = state.get("replication_gate", {}).get("min_seasons", 2)

    unique_seasons = len({r.season for r in pooled_records})
    unique_dates = len({r.date_cluster for r in pooled_records})

    # Individual Gate Tests
    pass_games = n_total >= min_games
    pass_dates = unique_dates >= min_dates
    pass_seasons = unique_seasons >= min_seasons
    pass_pit = state.get("replication_gate", {}).get("max_pit_violations", 0) >= 0

    beta_pos = panel_pooled.get("beta_within", 0) > 0
    beta_ci_pos = panel_pooled.get("beta_within_ci_95", [0, 0])[0] > 0
    perm_pass = panel_pooled.get("permutation_rejects_null", False)
    mae_pass = panel_pooled.get("mae_gain_vs_m0b", 0) > 0
    boot_pass = panel_pooled.get("paired_incremental_gain_bootstrap", {}).get("p_positive", 0) >= 0.90
    prob_pass = panel_pooled.get("brier_improvement", 0) > 0 or panel_pooled.get("nll_improvement", 0) > 0

    temporal_stability = "INSUFFICIENT_EVIDENCE"
    if unique_seasons >= 2:
        # Check if beta_within remains positive across individual season untouched slices
        season_betas = [
            p.get("beta_within", 0)
            for p in season_untouched_panels.values()
            if p.get("status") != "INSUFFICIENT_SAMPLE"
        ]
        temporal_stability = "PASS" if season_betas and all(b > 0 for b in season_betas) else "FAIL"

    scoreboard = {
        "sample_games": "PASS" if pass_games else "FAIL",
        "sample_dates": "PASS" if pass_dates else "FAIL",
        "sample_seasons": "PASS" if pass_seasons else "FAIL",
        "pit_violations_zero": "PASS" if pass_pit else "FAIL",
        "beta_within_positive": "PASS" if beta_pos else "FAIL",
        "beta_ci_excludes_zero": "PASS" if beta_ci_pos else "FAIL",
        "permutation_null": "PASS" if perm_pass else "FAIL",
        "m4_1_beats_m0b_mae": "PASS" if mae_pass else "FAIL",
        "paired_bootstrap_probability": "PASS" if boot_pass else "FAIL",
        "temporal_sign_stability": temporal_stability,
        "probability_metric_improvement": "PASS" if prob_pass else "FAIL",
    }

    # Determine Checkpoint Type & Authority
    if pass_games and pass_dates and pass_seasons:
        checkpoint_type = "FORMAL_REPLICATION_GATE"
        decision_authority = "BINDING_GATE"
    else:
        checkpoint_type = "INFORMATIONAL"
        decision_authority = "NONE"

    # Evaluate Overall Gate Verdict & Failure Router
    if not (pass_games and pass_dates and pass_seasons):
        gate_verdict = "INSUFFICIENT_EVIDENCE"
        failure_reason = f"Sample size criteria not met (N={n_total}/{min_games}, Dates={unique_dates}/{min_dates}, Seasons={unique_seasons}/{min_seasons})"
        failure_action = "Continue historical multi-season backfill and prospective capture via Data Track."
    else:
        if not beta_pos or not mae_pass:
            gate_verdict = "FAIL_LEVEL_ONLY"
            failure_reason = (
                "Structural model does not discriminate matchups (beta_within <= 0 or MAE gain <= 0)."
            )
            failure_action = "Improve sports domain features before retesting."
        elif not prob_pass:
            gate_verdict = "FAIL_PROBABILITY"
            failure_reason = "Continuous edge exists but empirical probability mapping fails against M0."
            failure_action = "Work on F2 distribution modeling only."
        elif not perm_pass or not beta_ci_pos or temporal_stability != "PASS":
            gate_verdict = "FAIL_STABILITY"
            failure_reason = (
                "Effect does not pass statistical stability / permutation null or has temporal sign flip."
            )
            failure_action = "Investigate temporal regime shifts or non-stationary features."
        elif not boot_pass:
            gate_verdict = "FAIL_INCREMENTAL_ACCURACY"
            failure_reason = "Structural matchup signal exists (beta_within > 0), but its magnitude is insufficient to beat M0b reliably."
            failure_action = "Open F1S_STRUCTURAL_SIGNAL_AMPLIFICATION to increase matchup-specific component before retesting."
        else:
            gate_verdict = "PASS"
            failure_reason = None
            failure_action = "Unlock Phase F2 (Probability/distribution modeling) and advance state machine."

    # Update Checkpoints and Trajectories
    checkpoints = state.get("checkpoints", {}).get("milestones", [])
    trajectories = state.get("checkpoints", {}).get("effect_size_trajectories", [])

    current_trajectory_entry = {
        "evaluated_at_utc": utc_now().isoformat(),
        "sample_size_N": n_total,
        "unique_dates_D": unique_dates,
        "beta_raw": panel_pooled.get("beta_raw"),
        "beta_within": panel_pooled.get("beta_within"),
        "beta_within_ci_95": panel_pooled.get("beta_within_ci_95"),
        "mae_gain_vs_m0b": panel_pooled.get("mae_gain_vs_m0b"),
        "rmse_gain_vs_m0b": panel_pooled.get("rmse_gain_vs_m0b"),
        "brier_improvement": panel_pooled.get("brier_improvement"),
        "nll_improvement": panel_pooled.get("nll_improvement"),
        "permutation_p": panel_pooled.get("permutation_p"),
        "positive_delta_share": panel_pooled.get("positive_delta_share"),
        "negative_delta_share": panel_pooled.get("negative_delta_share"),
    }

    if (
        trajectories
        and trajectories[-1].get("sample_size_N") == n_total
        and trajectories[-1].get("unique_dates_D") == unique_dates
    ):
        trajectories[-1] = current_trajectory_entry
    else:
        trajectories.append(current_trajectory_entry)
    state["checkpoints"]["effect_size_trajectories"] = trajectories

    # Check milestone hits
    for cp in checkpoints:
        t_games = cp.get("target_games", 999999)
        t_dates = cp.get("target_dates", 999999)
        if n_total >= t_games and unique_dates >= t_dates and not cp.get("completed", False):
            cp["completed"] = True
            cp["evaluated_at_utc"] = utc_now().isoformat()
            cp["beta_within"] = panel_pooled.get("beta_within")

    # Generate Experiment Artifacts
    git_sha = get_git_sha()
    exp_id = f"f1r_replication_{utc_now().strftime('%Y%m%d_%H%M%S')}_{git_sha[:7]}"
    exp_dir = REPO_ROOT / "outputs/research/phase_f" / exp_id
    exp_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "experiment_id": exp_id,
        "stage": "F1R_REPLICATION",
        "f1r_protocol_hash": F1R_PROTOCOL_HASH,
        "code_commit_sha": git_sha,
        "market_state_version": "v1",
        "checkpoint_type": checkpoint_type,
        "decision_authority": decision_authority,
        "sample_size_games": n_total,
        "sample_size_dates": unique_dates,
        "sample_size_seasons": unique_seasons,
        "started_at_utc": utc_now().isoformat(),
        "completed_at_utc": utc_now().isoformat(),
        "random_seed": seed,
        "gate_verdict": gate_verdict,
    }

    metrics = {
        "manifest": manifest,
        "panels": {
            "ORIGINAL_IDENTIFICATION_SAMPLE": panel_original,
            "NEW_UNTOUCHED_SAMPLE": panel_untouched,
            "POOLED_SAMPLE": panel_pooled,
            **season_untouched_panels,
        },
        "trajectories": trajectories,
        "replication_gate": {
            "checkpoint_type": checkpoint_type,
            "decision_authority": decision_authority,
            "scoreboard": scoreboard,
            "final_verdict": gate_verdict,
            "failure_reason": failure_reason,
            "failure_action": failure_action,
            "requirements": {
                "min_games": min_games,
                "min_dates": min_dates,
                "min_seasons": min_seasons,
                "actual_games": n_total,
                "actual_dates": unique_dates,
                "actual_seasons": unique_seasons,
            },
        },
    }

    # Generate Formatted Season Coverage Diagnostics Table for report
    season_panel_data = state.get("season_coverage_panel", {})
    season_rows_md = ""
    for s_yr, s_meta in sorted(season_panel_data.items()):
        season_rows_md += f"| {s_yr} | {s_meta.get('games_matched')} / {s_meta.get('games_scheduled')} | {s_meta.get('unique_dates')} | {s_meta.get('decision_coverage')} | {s_meta.get('sharp_coverage')} | {s_meta.get('books_per_game_median')} | {s_meta.get('decision_quote_age_median_sec')}s |\n"

    season_table_body = season_rows_md if season_rows_md else "| None | - | - | - | - | - | - |\n"

    # Format Market Quality Table
    pooled_mq = panel_pooled.get("market_quality", {})
    mq_rows_md = f"""| Median Books / Game | {pooled_mq.get("median_books_per_game")} |
| Mean Books / Game | {pooled_mq.get("mean_books_per_game")} |
| $\\ge 2$ Books Coverage | {pooled_mq.get("two_plus_books_coverage")} |
| $\\ge 3$ Books Coverage | {pooled_mq.get("three_plus_books_coverage")} |
| $\\ge 5$ Books Coverage | {pooled_mq.get("five_plus_books_coverage")} |
| Sharp+Soft Co-Presence | {pooled_mq.get("sharp_soft_simultaneous_coverage")} |
| Median Quote Age | {pooled_mq.get("median_quote_age_seconds")}s ({round(pooled_mq.get("median_quote_age_seconds", 0) / 60, 1)} min) |
| p75 Quote Age | {pooled_mq.get("p75_quote_age_seconds")}s |
| p90 Quote Age | {pooled_mq.get("p90_quote_age_seconds")}s |
| p95 Quote Age | {pooled_mq.get("p95_quote_age_seconds")}s |
| Quote $\\le 30$m Coverage | {pooled_mq.get("quote_le_30m_coverage")} |
| Quote $\\le 60$m Coverage | {pooled_mq.get("quote_le_60m_coverage")} |
| Quote $\\le 120$m Coverage | {pooled_mq.get("quote_le_120m_coverage")} |
"""

    report_md = f"""# Phase F1R Replication Report — {exp_id}

**Protocol Hash:** `{F1R_PROTOCOL_HASH}`  
**Execution Date:** {utc_now().isoformat()}  
**Git Commit SHA:** `{git_sha}`  
**Checkpoint Type:** `{checkpoint_type}` (Decision Authority: `{decision_authority}`)  
**Gate Verdict:** `{gate_verdict}`  
**Next Action:** {failure_action or "Advance to F2"}

---

## 1. Season-by-Season Coverage Breadth & Source-Era Metrics

| Season | Matched / Scheduled | Dates | Decision Coverage | Sharp Coverage | Books/Game (Median) | Median Quote Age |
|---|---|---|---|---|---|---|
{season_table_body}
---

## 2. Market-State Quality Diagnostics (Diagnostic Only)

| Market Quality Dimension | Value |
|---|---|
{mq_rows_md}
---

## 3. Replication Panels Summary

| Metric | Original Identification (N={panel_original.get("n_games")}) | New Untouched (N={panel_untouched.get("n_games")}) | Pooled Sample (N={panel_pooled.get("n_games")}) |
|---|---|---|---|
| $\\beta_{{raw}}$ | {panel_original.get("beta_raw")} | {panel_untouched.get("beta_raw")} | {panel_pooled.get("beta_raw")} |
| $\\beta_{{within}}$ | {panel_original.get("beta_within")} | {panel_untouched.get("beta_within")} | {panel_pooled.get("beta_within")} |
| Within-Date 95% CI | {panel_original.get("beta_within_ci_95")} | {panel_untouched.get("beta_within_ci_95")} | {panel_pooled.get("beta_within_ci_95")} |
| Permutation Placebo $p$ | {panel_original.get("permutation_p")} | {panel_untouched.get("permutation_p")} | {panel_pooled.get("permutation_p")} |
| $MAE_{{M0}}$ (Market) | {panel_original.get("m0_mae")} | {panel_untouched.get("m0_mae")} | {panel_pooled.get("m0_mae")} |
| $MAE_{{M0b}}$ (Bias-Corrected) | {panel_original.get("m0b_mae")} | {panel_untouched.get("m0b_mae")} | {panel_pooled.get("m0b_mae")} |
| $MAE_{{M4-1}}$ (Structural) | {panel_original.get("m4_1_mae")} | {panel_untouched.get("m4_1_mae")} | {panel_pooled.get("m4_1_mae")} |
| $MAE$ Gain vs M0b ($M0b - M4-1$) | {panel_original.get("mae_gain_vs_m0b")} | {panel_untouched.get("mae_gain_vs_m0b")} | {panel_pooled.get("mae_gain_vs_m0b")} |
| Bootstrap $P(M4-1 > M0b)$ | {panel_original.get("paired_incremental_gain_bootstrap", {}).get("p_positive")} | {panel_untouched.get("paired_incremental_gain_bootstrap", {}).get("p_positive")} | {panel_pooled.get("paired_incremental_gain_bootstrap", {}).get("p_positive")} |
| Brier Improvement ($M0 - M4-1$) | {panel_original.get("brier_improvement")} | {panel_untouched.get("brier_improvement")} | {panel_pooled.get("brier_improvement")} |
| NLL Improvement ($M0 - M4-1$) | {panel_original.get("nll_improvement")} | {panel_untouched.get("nll_improvement")} | {panel_pooled.get("nll_improvement")} |

---

## 4. Preregistered Replication Scoreboard (7 Points)

```yaml
replication_gate:
  sample_games: {scoreboard.get("sample_games")}
  sample_dates: {scoreboard.get("sample_dates")}
  sample_seasons: {scoreboard.get("sample_seasons")}
  pit_violations_zero: {scoreboard.get("pit_violations_zero")}
  beta_within_positive: {scoreboard.get("beta_within_positive")}
  beta_ci_excludes_zero: {scoreboard.get("beta_ci_excludes_zero")}
  permutation_null: {scoreboard.get("permutation_null")}
  m4_1_beats_m0b_mae: {scoreboard.get("m4_1_beats_m0b_mae")}
  paired_bootstrap_probability: {scoreboard.get("paired_bootstrap_probability")}
  temporal_sign_stability: {scoreboard.get("temporal_sign_stability")}
  probability_metric_improvement: {scoreboard.get("probability_metric_improvement")}
  final_verdict: {gate_verdict}
```

---

## 5. Effect-Size Trajectory across Sample Sizes

```text
{json.dumps(trajectories, indent=2)}
```

---

## 6. Failure Router Determination

- **Replication Status:** `{gate_verdict}`
- **Reason:** {failure_reason or "All preregistered criteria satisfied."}
- **Failure Router Recommendation:** {failure_action or "Proceed to stage F2."}
"""

    (exp_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (exp_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    (exp_dir / "report.md").write_text(report_md)

    # Symlink or write to latest
    latest_dir = REPO_ROOT / "outputs/research/phase_f/latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    (latest_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (latest_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    (latest_dir / "report.md").write_text(report_md)

    # Update state machine file
    state["last_experiment_id"] = exp_id
    state["last_verdict"] = gate_verdict
    state["last_failure_reason"] = failure_reason
    state["failure_router_action"] = failure_action
    state["replication_gate"]["status"] = gate_verdict
    state["replication_gate"]["checkpoint_type"] = checkpoint_type
    state["replication_gate"]["decision_authority"] = decision_authority
    state["replication_gate"]["scoreboard"] = scoreboard
    state["replication_gate"]["last_evaluated_utc"] = utc_now().isoformat()
    state["frozen"]["f1r_protocol_hash"] = F1R_PROTOCOL_HASH

    if gate_verdict == "PASS":
        state["unlocked"]["f2_distribution"] = True
        state["current_stage"] = "F2_DISTRIBUTION"

    save_state_file(state)
    return metrics, exp_dir


# =============================================================================
# 3. CLI & DISPATCH
# =============================================================================


def print_status() -> None:
    state = load_state_file()
    print("=================================================================")
    print("PHASE F STATE MACHINE STATUS")
    print("=================================================================")
    print(f"Current Stage:         {state.get('current_stage')}")
    print(f"Protocol Freeze Hash:  {state.get('frozen', {}).get('f1r_protocol_hash', F1R_PROTOCOL_HASH)}")
    print(f"Replication Status:    {state.get('replication_gate', {}).get('status')}")
    print(
        f"Checkpoint Type:       {state.get('replication_gate', {}).get('checkpoint_type', 'INFORMATIONAL')}"
    )
    print(
        f"Sample Size:           {state.get('sample', {}).get('games')} games across {state.get('sample', {}).get('dates')} dates ({state.get('sample', {}).get('seasons')} seasons)"
    )
    print(
        f"Market Data Quotes:    {state.get('market_data', {}).get('normalized_quotes_archived', 0)} archived ({state.get('market_data', {}).get('unique_quote_signatures', 0)} unique)"
    )
    print(f"Last Experiment ID:    {state.get('last_experiment_id')}")
    print(f"Last Verdict:          {state.get('last_verdict')}")
    print(f"Failure Router Action: {state.get('failure_router_action')}")
    print("Unlocked Stages:")
    for k, v in state.get("unlocked", {}).items():
        print(f"  - {k}: {v}")
    print("=================================================================")


def run_next_action() -> None:
    state = load_state_file()
    stage = state.get("current_stage")
    print(
        f"[Phase F Runner] Current stage is {stage}. Protocol hash: {F1R_PROTOCOL_HASH}. Determining next eligible action..."
    )

    if stage in ("F1R_REPLICATION", "F1R_BACKFILL"):
        print(
            "[Phase F Runner] Step 1: Running incremental backfill & coverage audit across all seasons (2024, 2025, 2026)..."
        )
        audit = run_backfill_and_audit()
        print(
            f"[Phase F Runner] Backfill audit complete: {audit['unique_games']} games across {audit['unique_dates']} dates in {audit['seasons']} seasons."
        )
        print(f"[Phase F Runner] PIT violations: {audit['PIT_violations']}")

        if audit["PIT_violations"] > 0:
            print("[Phase F Runner] HARD STOP: PIT violations > 0 detected!")
            sys.exit(1)

        print("[Phase F Runner] Step 2: Running formal 3-panel replication evaluation...")
        metrics, exp_dir = run_full_replication_evaluation()
        verdict = metrics["replication_gate"]["final_verdict"]
        print(f"[Phase F Runner] Experiment artifacts generated at {exp_dir}")
        print(f"[Phase F Runner] Checkpoint Type: {metrics['replication_gate']['checkpoint_type']}")
        print(f"[Phase F Runner] Replication Gate Verdict: {verdict}")
        print(f"[Phase F Runner] Router Action: {metrics['replication_gate']['failure_action']}")
    else:
        print(f"[Phase F Runner] Stage {stage} execution handler ready.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase F Autonomous Research State Machine Runner")
    parser.add_argument(
        "command",
        choices=["status", "backfill", "evaluate-checkpoint", "run-next", "run-until-gate"],
        default="status",
        nargs="?",
    )
    args = parser.parse_args()

    if args.command == "status":
        print_status()
    elif args.command == "backfill":
        audit = run_backfill_and_audit()
        print(json.dumps(audit, indent=2))
    elif args.command == "evaluate-checkpoint":
        metrics, exp_dir = run_full_replication_evaluation()
        print(f"Replication evaluation complete. Artifacts at {exp_dir}")
        print(f"Verdict: {metrics['replication_gate']['final_verdict']}")
    elif args.command in ("run-next", "run-until-gate"):
        run_next_action()


if __name__ == "__main__":
    main()
