"""Forward gate G2 for the NBA v10 overhaul: read-only scoring of ``nba-anchor-v10`` rows.

Pre-registered in ``docs/NBA_REVIEW_AND_V10_PLAN_2026-11-20.md`` (sections 6, 9, 10); the criteria
are fixed and must not be re-tuned after results are seen. Over forward picks only (rows written by
``nba-anchor-v10`` since its wiring on 2026-11-21), games clustered across markets:

1. at least 150 picks. A pick, per market: with the artifact's ``main_min_edge`` null (as shipped,
   lambda = 0), a consensus-anchored v10 row whose devigged consensus probability of a side minus
   the Polymarket ask for that side is >= 3.0 points, gross of fees, graded on that side (track
   T-B, plan section 9); with ``main_min_edge`` set, a row whose ``net_edge`` clears it;
2. mean CLV against the devigged sportsbook consensus at T-10 min of at least +1.0c, with a
   95% cluster-bootstrap CI (by game) excluding 0;
3. CLV above v9's on the same games and markets (paired, same benchmark capture);
4. Brier on settled picks no worse than the Polymarket mid at decision time on the same picks.

Kill rule: a G2 failure at 300 or more picks is KILL, never "extend". Below 150 picks: PENDING.
Polymarket-to-Polymarket CLV is reported but is NOT a G2 criterion (it passes no-skill models).

Benchmark, point in time: the T-10 benchmark for a pick is the LATEST ESPN consensus capture
(``<runtime root>/odds/nba/<ET date>/espn_odds.jsonl``) of that game with
``decision_time < observed_at_utc <= tip - 10 min``, where tip is the earlier of the ledger row's
and the capture's own ``event_start_utc``. A game is scored only once ``as_of >= tip``, so a
report run before tip never freezes an early capture as the "T-10" line. The latest qualifying
capture must also be no older than ``BENCHMARK_MAX_LEAD`` before tip, otherwise the pick is
excluded as ``stale_benchmark`` (an operator-visible choice: the capture worker runs every 10 min,
so a T-10 capture older than 40 min means the worker missed runs). Nothing falls back silently:
every excluded pick carries a reason.

The consensus is quoted at ESPN's line; it is moved to the contract's line with the same normal
translation ``nba-anchor-v10`` uses for its anchor and track T-B (``anchor_mean`` then
``Phi((contract_line - mean) / sigma)``), with the v10 row's own sigma for both v10 and its v9 pair.

This module never writes: the ledger is opened read-only and capture files are only read.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np

from .domain import EASTERN, parse_utc

V10_MODEL = "nba-anchor-v10"
V9_MODELS = {"spread": "nba-spread-margin-v9", "total": "nba-total-margin-v9"}
# nba-anchor-v10 was wired into the daily NBA slates on 2026-11-21 (quick-261121-nw1); every row
# it has written is forward relative to the v10 freeze.
V10_FREEZE_UTC = "2026-11-21T00:00:00+00:00"

G2_MIN_PICKS = 150
G2_KILL_PICKS = 300
G2_MIN_MEAN_CLV = 0.010  # +1.0c, in probability units
BENCHMARK_LEAD = timedelta(minutes=10)  # "T-10": the benchmark is observed at or before tip - 10 min
BENCHMARK_MAX_LEAD = timedelta(minutes=40)  # older than tip - 40 min is not a T-10 line (stale)
# A v10 row and its v9 pair come from the same daily run (v10 is logged right after v9); runs are
# hours apart, so a wider decision gap means the pair mixes two runs.
PAIR_MAX_DECISION_GAP = timedelta(minutes=15)
BOOTSTRAP_DRAWS = 10_000
BOOTSTRAP_SEED = 20261121

LIVE_STATUSES = ("open", "settled")
_LOW_SIDE = {"spread": "away", "total": "under"}
_HIGH_SIDE = {"spread": "home", "total": "over"}
_OPPOSITE = {"away": "home", "home": "away", "over": "under", "under": "over"}
POPULATIONS = ("g2_picks", "all_flat", "tb")


# --------------------------------------------------------------------------- data shapes
@dataclass(frozen=True)
class ConsensusCapture:
    """One devigged ESPN consensus for one market of one game, in the anchor convention.

    ``line`` is the home-margin line for spreads (positive when home is favoured; ESPN's
    ``home_line`` negated) and the points line for totals. ``p_low`` is the fair probability of the
    low side at that line: away covers (spread) or under (total).
    """

    event_id: str
    market: str
    observed_at_utc: datetime
    event_start_utc: datetime
    line: float
    p_low: float
    n_books: int | None


@dataclass(frozen=True)
class Pick:
    """A priced side to score. Prices and probabilities are in probability units (0.51 = 51c)."""

    pick_id: str
    model: str
    event_id: str
    market: str
    side: str
    contract_line: float  # home-margin convention for spreads, points line for totals
    ask: float
    probability: float
    decision_time: datetime
    tip: datetime
    sigma: float
    outcome: int | None = None  # 1 win, 0 loss, None unsettled / push / void
    mid: float | None = None  # Polymarket mid of this side at decision time (two-sided, <= 10 wide)
    pm_close: float | None = None  # Polymarket closing ask of this side (NOT a G2 benchmark)
    anchor_source: str | None = None


@dataclass
class ScoredPick:
    pick: Pick
    reason: str | None  # None when scored; else the exclusion reason
    benchmark_observed_at_utc: str | None = None
    benchmark_lead_minutes: float | None = None
    benchmark_line: float | None = None
    benchmark_n_books: int | None = None
    consensus_prob: float | None = None
    clv: float | None = None


# --------------------------------------------------------------------------- ESPN captures
def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_espn_record(record: Mapping[str, Any]) -> list[ConsensusCapture]:
    """The spread and total consensus of one ``espn_odds.jsonl`` record; fail closed per market.

    Same conventions as ``cli.forecast_nba_anchor_v10._consensus_quotes``: a market counts only
    with a line and a devigged low-side probability strictly inside (0, 1).
    """
    try:
        event_id = str(record["event_id"])
        observed = parse_utc(str(record["observed_at_utc"]))
        start = parse_utc(str(record["event_start_utc"]))
    except (KeyError, TypeError, ValueError):
        return []
    consensus = record.get("consensus") or {}
    spread = consensus.get("spread") or {}
    total = consensus.get("total") or {}
    home_line = _finite(spread.get("home_line"))
    candidates = {
        "spread": (None if home_line is None else -home_line, _finite(spread.get("away")), spread),
        "total": (_finite(total.get("line")), _finite(total.get("under")), total),
    }
    out = []
    for market, (line, p_low, block) in candidates.items():
        if line is None or p_low is None or not 0.0 < p_low < 1.0:
            continue
        books = block.get("n_books")
        out.append(
            ConsensusCapture(
                event_id=event_id,
                market=market,
                observed_at_utc=observed,
                event_start_utc=start,
                line=line,
                p_low=p_low,
                n_books=books if isinstance(books, int) else None,
            )
        )
    return out


def load_consensus_captures(
    files: Iterable[Path],
) -> tuple[dict[tuple[str, str], list[ConsensusCapture]], dict[str, Any]]:
    """Every consensus capture in ``files`` keyed by (event_id, market), plus data-quality counts.

    The files are ingest-ordered, so nothing here relies on line order; selection is a max-scan.
    """
    captures: dict[tuple[str, str], list[ConsensusCapture]] = {}
    diag: Counter[str] = Counter()
    for path in files:
        if not path.is_file():
            continue
        diag["files"] += 1
        with path.open(encoding="utf-8") as handle:
            for raw in handle:
                try:
                    record = json.loads(raw)
                except ValueError:
                    diag["unparseable_lines"] += 1
                    continue
                diag["records"] += 1
                _record_quality(record, diag)
                for capture in parse_espn_record(record):
                    captures.setdefault((capture.event_id, capture.market), []).append(capture)
                    diag[f"{capture.market}_consensus"] += 1
    return captures, dict(sorted(diag.items()))


def _record_quality(record: Mapping[str, Any], diag: Counter[str]) -> None:
    """Counts of things in a capture record that would make it a doubtful benchmark."""
    try:
        observed = parse_utc(str(record["observed_at_utc"]))
        start = parse_utc(str(record["event_start_utc"]))
    except (KeyError, TypeError, ValueError):
        diag["bad_timestamps"] += 1
        return
    if observed >= start:
        diag["observed_at_or_after_tip"] += 1
    stated = _finite(record.get("minutes_to_tip"))
    if stated is not None and abs(stated - (start - observed).total_seconds() / 60) > 0.2:
        diag["minutes_to_tip_mismatch"] += 1
    consensus = record.get("consensus") or {}
    for market, (a, b) in (("spread", ("home", "away")), ("total", ("over", "under"))):
        block = consensus.get(market) or {}
        pa, pb = _finite(block.get(a)), _finite(block.get(b))
        if pa is None or pb is None:
            diag[f"{market}_no_devigged_pair"] += 1
        elif abs(pa + pb - 1.0) > 1e-4:
            diag[f"{market}_pair_not_summing_to_1"] += 1
        books = block.get("n_books")
        diag[f"{market}_n_books={books}"] += 1
    for provider in record.get("providers") or []:
        for market in ("spread", "total"):
            if (provider.get(market) or {}).get("lines_consistent") is False:
                diag[f"{market}_provider_lines_inconsistent"] += 1
        overround = _finite((provider.get("spread") or {}).get("overround"))
        if overround is not None and not 0.0 <= overround <= 0.10:
            diag["spread_overround_outside_0_10pct"] += 1
        overround = _finite((provider.get("total") or {}).get("overround"))
        if overround is not None and not 0.0 <= overround <= 0.10:
            diag["total_overround_outside_0_10pct"] += 1


# --------------------------------------------------------------------------- benchmark
def select_t10_benchmark(
    captures: Sequence[ConsensusCapture],
    *,
    decision_time: datetime,
    tip: datetime,
    as_of: datetime,
    max_lead: timedelta = BENCHMARK_MAX_LEAD,
) -> tuple[ConsensusCapture | None, str | None]:
    """The T-10 consensus capture for one pick, or (None, exclusion reason).

    Qualifying: ``decision_time < observed_at_utc <= min(tip, capture tip) - 10 min``. The latest
    qualifying capture wins (max-scan). Reasons: ``pre_tip_not_final`` (the game has not tipped
    at ``as_of``), ``missing_benchmark_no_capture``, ``missing_benchmark_all_before_decision``,
    ``missing_benchmark_all_after_t10``, ``missing_benchmark_outside_window`` (a mix of both),
    ``stale_benchmark`` (the latest qualifying capture is older than ``tip - max_lead``).
    """
    if as_of < tip:
        return None, "pre_tip_not_final"
    if not captures:
        return None, "missing_benchmark_no_capture"
    qualifying = []
    before = after = 0
    for capture in captures:
        cutoff = min(tip, capture.event_start_utc) - BENCHMARK_LEAD
        if capture.observed_at_utc <= decision_time:
            before += 1
        elif capture.observed_at_utc > cutoff:
            after += 1
        else:
            qualifying.append(capture)
    if not qualifying:
        if after == 0:
            return None, "missing_benchmark_all_before_decision"
        if before == 0:
            return None, "missing_benchmark_all_after_t10"
        return None, "missing_benchmark_outside_window"
    best = max(qualifying, key=lambda c: c.observed_at_utc)
    if best.observed_at_utc < min(tip, best.event_start_utc) - max_lead:
        return None, "stale_benchmark"
    return best, None


def consensus_side_probability(
    capture: ConsensusCapture, side: str, contract_line: float, sigma: float
) -> float:
    """Consensus probability of ``side`` at the contract's line (normal translation, as v10's T-B)."""
    from .cli.forecast_nba_anchor_v10 import anchor_mean

    mean = anchor_mean(capture.line, capture.p_low, sigma)
    p_low = NormalDist().cdf((contract_line - mean) / sigma)
    return p_low if side == _LOW_SIDE[capture.market] else 1.0 - p_low


def score_pick(
    pick: Pick,
    captures: Mapping[tuple[str, str], Sequence[ConsensusCapture]],
    *,
    as_of: datetime,
) -> ScoredPick:
    capture, reason = select_t10_benchmark(
        captures.get((pick.event_id, pick.market), ()),
        decision_time=pick.decision_time,
        tip=pick.tip,
        as_of=as_of,
    )
    if capture is None:
        return ScoredPick(pick, reason)
    prob = consensus_side_probability(capture, pick.side, pick.contract_line, pick.sigma)
    return ScoredPick(
        pick,
        None,
        benchmark_observed_at_utc=capture.observed_at_utc.isoformat(),
        benchmark_lead_minutes=round((pick.tip - capture.observed_at_utc).total_seconds() / 60, 1),
        benchmark_line=capture.line,
        benchmark_n_books=capture.n_books,
        consensus_prob=round(prob, 6),
        clv=round(prob - pick.ask, 6),
    )


# --------------------------------------------------------------------------- statistics
@dataclass(frozen=True)
class MeanCI:
    n: int
    n_games: int
    mean: float | None
    low: float | None
    high: float | None


def cluster_mean_ci(
    values: Sequence[float],
    clusters: Sequence[str],
    *,
    draws: int = BOOTSTRAP_DRAWS,
    seed: int = BOOTSTRAP_SEED,
) -> MeanCI:
    """Pick-weighted mean with a 95% percentile CI from resampling whole games (``clusters``).

    Reuses ``nba_replay_harness._cluster_bootstrap`` (ratio of resampled sums). With fewer than two
    games there is no CI (None), never a degenerate zero-width one.
    """
    from .nba_replay_harness import _cluster_bootstrap

    if len(values) != len(clusters):
        raise ValueError("values and clusters differ in length")
    n_games = len(set(clusters))
    if not values:
        return MeanCI(0, 0, None, None, None)
    mean = float(np.mean(values))
    if n_games < 2:
        return MeanCI(len(values), n_games, mean, None, None)
    stats = {"n": np.ones(len(values)), "x": np.asarray(values, dtype=float)}
    interval = _cluster_bootstrap(stats, list(clusters), lambda s: s["x"] / s["n"], draws=draws, seed=seed)
    return MeanCI(len(values), n_games, interval.estimate, interval.low, interval.high)


def brier(probabilities: Sequence[float], outcomes: Sequence[int]) -> float | None:
    if not probabilities:
        return None
    return float(np.mean([(p - o) ** 2 for p, o in zip(probabilities, outcomes, strict=True)]))


# --------------------------------------------------------------------------- verdict
PASS, FAIL, NOT_EVALUABLE = "pass", "fail", "not_evaluable"


def g2_verdict(n_picks: int, criteria: Mapping[str, str]) -> tuple[str, str]:
    """(verdict, why). PENDING below 150 picks; KILL for any failure at 300+, never "extend".

    A criterion that cannot be evaluated is never a pass: with no failure it keeps the gate
    PENDING (waiting for data) at any n.
    """
    failed = sorted(name for name, status in criteria.items() if status == FAIL)
    waiting = sorted(name for name, status in criteria.items() if status == NOT_EVALUABLE)
    if n_picks < G2_MIN_PICKS:
        return "PENDING", f"{n_picks} scored picks < {G2_MIN_PICKS}"
    if failed:
        if n_picks >= G2_KILL_PICKS:
            return "KILL", f"failed at {n_picks} >= {G2_KILL_PICKS} picks: {', '.join(failed)}"
        return "FAIL", f"failing at {n_picks} picks (kill check at {G2_KILL_PICKS}): {', '.join(failed)}"
    if waiting:
        return "PENDING", f"not yet evaluable: {', '.join(waiting)}"
    return "PASS", f"all four criteria pass at {n_picks} picks"


def evaluate_population(
    scored: Sequence[ScoredPick],
    v9_by_key: Mapping[tuple[str, str], ScoredPick],
    *,
    draws: int = BOOTSTRAP_DRAWS,
) -> dict[str, Any]:
    """The full G2 scorecard of one population of picks (criteria, verdict and supporting numbers)."""
    ok = [s for s in scored if s.reason is None and s.clv is not None]
    exclusions = Counter(s.reason for s in scored if s.reason is not None)
    clv = cluster_mean_ci([s.clv for s in ok], [s.pick.event_id for s in ok], draws=draws)  # type: ignore[misc]

    # Paired v10 - v9 on the same game and market, against the same benchmark capture.
    diffs, diff_games = [], []
    pair_reasons: Counter[str] = Counter()
    for s in ok:
        v9 = v9_by_key.get((s.pick.event_id, s.pick.market))
        if v9 is None:
            pair_reasons["no_v9_row"] += 1
        elif v9.reason is not None or v9.clv is None:
            pair_reasons[f"v9_{v9.reason}"] += 1
        elif v9.benchmark_observed_at_utc != s.benchmark_observed_at_utc:
            pair_reasons["v9_different_benchmark"] += 1
        elif abs(v9.pick.decision_time - s.pick.decision_time) > PAIR_MAX_DECISION_GAP:
            # Both rows are replaced together on every daily re-run; a wider gap means they come
            # from different runs (one model failed or was skipped in a run), so not a fair pair.
            pair_reasons["v9_decision_not_same_run"] += 1
        else:
            diffs.append(s.clv - v9.clv)  # type: ignore[operator]
            diff_games.append(s.pick.event_id)
    paired = cluster_mean_ci(diffs, diff_games, draws=draws)

    settled = [s for s in ok if s.pick.outcome is not None and s.pick.mid is not None]
    brier_reasons = Counter(
        "unsettled_or_push" if s.pick.outcome is None else "no_decision_mid"
        for s in ok
        if s.pick.outcome is None or s.pick.mid is None
    )
    brier_model = brier([s.pick.probability for s in settled], [s.pick.outcome for s in settled])  # type: ignore[misc]
    brier_mid = brier([s.pick.mid for s in settled], [s.pick.outcome for s in settled])  # type: ignore[misc]
    by_anchor: dict[str, dict[str, Any]] = {}
    for source in sorted({str(s.pick.anchor_source) for s in settled}):
        rows = [s for s in settled if str(s.pick.anchor_source) == source]
        by_anchor[source] = {
            "n": len(rows),
            "brier_model": brier([s.pick.probability for s in rows], [s.pick.outcome for s in rows]),  # type: ignore[misc]
            "brier_mid": brier([s.pick.mid for s in rows], [s.pick.outcome for s in rows]),  # type: ignore[misc]
        }

    # Reference only and benchmark-free: every row with a Polymarket close counts, not just the
    # consensus-scored ones, so it stays visible while T-10 captures are missing or stale.
    pm = [s for s in scored if s.pick.pm_close is not None]
    pm_clv = cluster_mean_ci(
        [s.pick.pm_close - s.pick.ask for s in pm],  # type: ignore[operator]
        [s.pick.event_id for s in pm],
        draws=draws,
    )

    criteria = {
        "c1_n_picks": PASS if clv.n >= G2_MIN_PICKS else FAIL,
        "c2_consensus_clv": (
            NOT_EVALUABLE
            if clv.mean is None or clv.low is None
            else PASS
            if clv.mean >= G2_MIN_MEAN_CLV and clv.low > 0.0
            else FAIL
        ),
        "c3_clv_above_v9": (NOT_EVALUABLE if paired.mean is None else PASS if paired.mean > 0.0 else FAIL),
        "c4_brier_vs_mid": (
            NOT_EVALUABLE
            if brier_model is None or brier_mid is None
            else PASS
            if brier_model <= brier_mid
            else FAIL
        ),
    }
    verdict, why = g2_verdict(clv.n, {k: v for k, v in criteria.items() if k != "c1_n_picks"})
    leads = [s.benchmark_lead_minutes for s in ok if s.benchmark_lead_minutes is not None]
    return {
        "rows": len(scored),
        "scored": clv.n,
        "games": clv.n_games,
        "excluded": dict(sorted(exclusions.items())),
        "criteria": criteria,
        "verdict": verdict,
        "verdict_reason": why,
        "consensus_clv": asdict(clv),
        "v9_paired_clv_diff": {**asdict(paired), "unpaired": dict(sorted(pair_reasons.items()))},
        "brier": {
            "n": len(settled),
            "model": brier_model,
            "polymarket_mid": brier_mid,
            "excluded": dict(sorted(brier_reasons.items())),
            "by_anchor_source": by_anchor,
        },
        "polymarket_clv_not_g2": asdict(pm_clv),
        "benchmark_lead_minutes": (
            None if not leads else {"min": min(leads), "median": float(np.median(leads)), "max": max(leads)}
        ),
    }


# --------------------------------------------------------------------------- loader
@dataclass
class G2Inputs:
    populations: dict[str, list[Pick]] = field(default_factory=dict)
    v9: dict[tuple[str, str], Pick] = field(default_factory=dict)
    capture_files: list[Path] = field(default_factory=list)
    row_exclusions: dict[str, dict[str, int]] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    main_min_edge: dict[str, float | None] = field(default_factory=dict)


_ROWS_SQL = """
SELECT pick_id, ledger_tier, model_id, event_id, event_start_utc, market_type, selection, line,
       model_probability, status, result, created_at_utc, decision_payload_json
FROM ledger_records
WHERE lower(sport) = 'nba' AND ledger_tier = 'flat' AND model_id IN ({placeholders})
  AND created_at_utc >= ?
ORDER BY created_at_utc
"""


def _read_rows(ledgers_db: Path, freeze_utc: str) -> list[dict[str, Any]]:
    models = [V10_MODEL, *V9_MODELS.values()]
    sql = _ROWS_SQL.format(placeholders=",".join("?" * len(models)))
    # The ledger is the live authority: read-only URI, never a write handle.
    conn = sqlite3.connect(f"{Path(ledgers_db).resolve().as_uri()}?mode=ro", uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        # created_at_utc is stored with a "Z" suffix; compare on the same text form.
        cutoff = parse_utc(freeze_utc).strftime("%Y-%m-%dT%H:%M:%S")
        return [dict(row) for row in conn.execute(sql, (*models, cutoff))]
    finally:
        conn.close()


def _outcome(status: str, result: Any) -> int | None:
    if status != "settled":
        return None
    return {"win": 1, "loss": 0}.get(str(result or "").lower())


def _contract_line_from_row(market: str, selection: str, line: float) -> float:
    """Home-margin contract line from a ledger selection line (inverse of v10's ``_selection_line``)."""
    if market == "spread":
        return line if selection == "away" else -line
    return line


class _DecisionQuotes:
    """Polymarket decision quotes, looked up by exact (market_slug, observed_at_utc)."""

    def __init__(self, data_root: Path) -> None:
        self.data_root = Path(data_root)
        self._by_date: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}

    def _load(self, game_date: str) -> dict[tuple[str, str], dict[str, Any]]:
        if game_date not in self._by_date:
            index: dict[tuple[str, str], dict[str, Any]] = {}
            path = self.data_root / "odds" / "nba" / game_date / "polymarket_snapshots.jsonl"
            if path.is_file():
                with path.open(encoding="utf-8") as handle:
                    for raw in handle:
                        try:
                            item = json.loads(raw)
                        except ValueError:
                            continue
                        slug, observed = item.get("market_slug"), item.get("observed_at_utc")
                        if slug and observed:
                            index[(str(slug), str(observed))] = item
            self._by_date[game_date] = index
        return self._by_date[game_date]

    def get(self, slug: str | None, observed: str | None, tip: datetime) -> dict[str, Any] | None:
        if not slug or not observed:
            return None
        return self._load(tip.astimezone(EASTERN).date().isoformat()).get((slug, observed))


def _quote_side(
    quote: Mapping[str, Any] | None, market: str, side: str, away_team: str
) -> tuple[float | None, float | None, str | None]:
    """(ask, two-sided mid probability, problem) of ``side`` in one decision quote.

    Side mapping is v10's own (``_ROW_SIDE``: spread long = away, total long = over); NBA spread
    rows are selected by the away team, so a spread quote whose ``team`` is not the away team is
    not trusted. The mid follows v10's Polymarket-mid anchor: the mean of this side's mid and one
    minus the other side's mid, each from a two-sided quote at most 10 points wide.
    """
    from .cli.forecast import _nba_team_matches
    from .cli.forecast_nba_anchor_v10 import _ROW_SIDE, _row_ask, _row_mid

    if quote is None:
        return None, None, "decision_quote_not_found"
    if market == "spread" and not _nba_team_matches(away_team, str(quote.get("team") or "")):
        return None, None, "decision_quote_side_unverified"
    this = quote.get(_ROW_SIDE[market][side])
    this_mid = _row_mid(this)
    other_mid = _row_mid(quote.get(_ROW_SIDE[market][_OPPOSITE[side]]))
    mids = [m for m in (this_mid, None if other_mid is None else 1.0 - other_mid) if m is not None]
    return _row_ask(this), (round(sum(mids) / len(mids), 6) if mids else None), None


def _pm_close(
    data_root: Path,
    slug: str | None,
    row: Mapping[str, Any],
    selection: str,
    line: float,
    decision: datetime,
    as_of: datetime,
    tip: datetime,
) -> float | None:
    """Settlement's Polymarket close for ``selection`` (after the decision, before tip); None pre-tip."""
    if not slug or as_of < tip:
        return None
    from .cli.settle import _closing_probability_for_moneyline_pick

    close, _ = _closing_probability_for_moneyline_pick(
        data_root,
        "nba",
        slug,
        str(row["event_start_utc"]),
        str(row["home_team"]),
        str(row["away_team"]),
        selection,
        line=line,
        not_before_utc=decision.isoformat(),
    )
    return close


def load_g2_inputs(
    ledgers_db: Path,
    data_root: Path,
    artifact: Mapping[str, Any],
    *,
    as_of: datetime,
    freeze_utc: str = V10_FREEZE_UTC,
) -> G2Inputs:
    """v10 picks (three populations), their v9 Flat pairs, and the capture files to read."""
    from .cli.settle import _extract_market_slug

    main_min_edge = {
        market: (None if block.get("main_min_edge") is None else float(block["main_min_edge"]))
        for market, block in (artifact.get("markets") or {}).items()
    }
    out = G2Inputs(populations={name: [] for name in POPULATIONS}, main_min_edge=main_min_edge)
    quotes = _DecisionQuotes(data_root)
    diag: Counter[str] = Counter()
    v10_excl: Counter[str] = Counter()
    v9_excl: Counter[str] = Counter()
    tb_counts: Counter[str] = Counter()
    rows = _read_rows(ledgers_db, freeze_utc)
    dates: set[str] = set()

    # One live row per (model, event, market): superseded rows are earlier runs of the same call.
    # v9 rows matter only on games and markets v10 has priced, so only those are counted.
    v10_any = {(str(r["event_id"]), str(r["market_type"])) for r in rows if r["model_id"] == V10_MODEL}
    rows = [
        r
        for r in rows
        if r["model_id"] == V10_MODEL or (str(r["event_id"]), str(r["market_type"])) in v10_any
    ]
    latest: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        excl = v10_excl if row["model_id"] == V10_MODEL else v9_excl
        if row["status"] not in LIVE_STATUSES:
            excl[f"status_{row['status']}"] += 1
            continue
        key = (row["model_id"], str(row["event_id"]), str(row["market_type"]))
        if key in latest:
            excl["duplicate_live_row_older_dropped"] += 1
        latest[key] = row
    v10_events = {(e, m) for (model, e, m) in latest if model == V10_MODEL}

    for (model, event_id, market), row in sorted(latest.items(), key=lambda kv: str(kv[1]["created_at_utc"])):
        payload = json.loads(row["decision_payload_json"] or "{}")
        row = {**row, "home_team": payload.get("home_team"), "away_team": payload.get("away_team")}
        try:
            tip = parse_utc(str(row["event_start_utc"]))
        except (TypeError, ValueError):
            (v10_excl if model == V10_MODEL else v9_excl)["bad_event_start"] += 1
            continue
        dates.add(tip.astimezone(EASTERN).date().isoformat())
        outcome = _outcome(row["status"], row["result"])
        if model == V10_MODEL:
            _add_v10(
                row,
                payload,
                tip,
                outcome,
                quotes,
                data_root,
                as_of,
                main_min_edge,
                out,
                v10_excl,
                tb_counts,
                diag,
            )
        elif (event_id, market) in v10_events:
            slug = _extract_market_slug(str(payload.get("rationale") or ""), payload)
            selection = str(row["selection"])
            line = float(row["line"])
            ask, mid, problem = _quote_side(
                quotes.get(slug, payload.get("market_quote_observed_at_utc"), tip),
                market,
                selection,
                str(row["away_team"]),
            )
            if problem:
                diag[f"v9_{problem}"] += 1
            if ask is None:
                # Last resort, and counted: the ledger's implied probability is a lossy round trip.
                ask = _finite(payload.get("decision_raw_implied_probability"))
                diag["v9_ask_from_ledger_implied_probability"] += 1
            if ask is None:
                v9_excl["no_decision_ask"] += 1
                continue
            decision = parse_utc(str(row["created_at_utc"]))
            out.v9[(event_id, market)] = Pick(
                pick_id=str(row["pick_id"]),
                model=model,
                event_id=event_id,
                market=market,
                side=selection,
                contract_line=_contract_line_from_row(market, selection, line),
                ask=ask,
                probability=float(row["model_probability"]),
                decision_time=decision,
                tip=tip,
                sigma=float("nan"),  # set from the paired v10 row (same translation for both)
                outcome=outcome,
                mid=mid,
                pm_close=_pm_close(data_root, slug, row, selection, line, decision, as_of, tip),
            )
        else:
            v9_excl["no_live_v10_row_for_game_market"] += 1

    # v9 is translated with its v10 pair's sigma, so the benchmark is literally the same.
    sigma_by_key = {(p.event_id, p.market): p.sigma for p in out.populations["all_flat"]}
    for key, pick in list(out.v9.items()):
        out.v9[key] = Pick(**{**asdict(pick), "sigma": sigma_by_key.get(key, float("nan"))})

    from .data_sources.nba_pretip_capture import espn_odds_path

    for day in sorted(dates):
        base = datetime.fromisoformat(day)
        for offset in (-1, 0, 1):  # a rescheduled tip can file the game under a neighbouring date
            out.capture_files.append(
                espn_odds_path(data_root, (base + timedelta(days=offset)).date().isoformat())
            )
    out.capture_files = sorted(set(out.capture_files))
    out.row_exclusions = {"v10": dict(sorted(v10_excl.items())), "v9": dict(sorted(v9_excl.items()))}
    out.diagnostics = {"rows": dict(sorted(diag.items())), "tb": dict(sorted(tb_counts.items()))}
    return out


def _add_v10(
    row: dict[str, Any],
    payload: Mapping[str, Any],
    tip: datetime,
    outcome: int | None,
    quotes: _DecisionQuotes,
    data_root: Path,
    as_of: datetime,
    main_min_edge: Mapping[str, float | None],
    out: G2Inputs,
    excl: Counter[str],
    tb_counts: Counter[str],
    diag: Counter[str],
) -> None:
    try:
        snap = json.loads(payload.get("model_input_snapshot_json") or "")
        decision = parse_utc(str(snap["decision_time_utc"]))
        market = str(snap["market"])
        side = str(snap["side"])
        contract_line = float(snap["contract_line"])
        sigma = float(snap["sigma"])
        ask = float(snap["ask"])
        probability = float(snap["probability"])
    except (KeyError, TypeError, ValueError):
        excl["snapshot_missing_or_incomplete"] += 1
        return
    if side != str(row["selection"]) or market != str(row["market_type"]):
        excl["snapshot_row_identity_mismatch"] += 1
        return
    if decision >= tip:
        excl["decision_at_or_after_tip"] += 1
        return
    slug = snap.get("market_slug")
    quote = quotes.get(slug, snap.get("quote_observed_at_utc"), tip)
    q_ask, mid, problem = _quote_side(quote, market, side, str(row["away_team"]))
    if problem:
        diag[f"v10_{problem}"] += 1
    elif q_ask is None or abs(q_ask - ask) > 1e-9:
        diag["v10_stored_ask_differs_from_decision_quote"] += 1
    if abs(float(row["model_probability"]) - probability) > 2e-6:
        diag["v10_ledger_probability_differs_from_snapshot"] += 1
    row_line = float(row["line"])
    if abs(_contract_line_from_row(market, side, row_line) - contract_line) > 1e-9:
        diag["v10_ledger_line_differs_from_contract_line"] += 1
    base = Pick(
        pick_id=str(row["pick_id"]),
        model=V10_MODEL,
        event_id=str(row["event_id"]),
        market=market,
        side=side,
        contract_line=contract_line,
        ask=ask,
        probability=probability,
        decision_time=decision,
        tip=tip,
        sigma=sigma,
        outcome=outcome,
        mid=mid,
        pm_close=_pm_close(data_root, slug, row, side, row_line, decision, as_of, tip),
        anchor_source=snap.get("anchor_source"),
    )
    out.populations["all_flat"].append(base)

    # G2 pick rule (operator, 2026-11-21): with an artifact ``main_min_edge`` set for the market, a
    # pick is a row whose stored net edge clears it with a servable anchor (the logger hard-codes
    # ``main_eligible`` False, so that flag is not consulted). With it null (lambda = 0 ships no
    # model edge), a pick is the pinned T-B rule of plan section 9, graded on the T-B side.
    threshold = main_min_edge.get(market)
    if threshold is not None:
        edge = _finite(snap.get("net_edge"))
        if edge is not None and edge >= threshold and snap.get("anchor_source"):
            out.populations["g2_picks"].append(base)

    tb_pick = _tb_pick(base, snap, row, quote, outcome, data_root, as_of, tb_counts)
    if tb_pick is not None:
        out.populations["tb"].append(tb_pick)
        if threshold is None and _is_tb_g2_pick(snap, tb_pick.side):
            out.populations["g2_picks"].append(tb_pick)


def g2_rule(main_min_edge: Mapping[str, float | None], market: str) -> str:
    """Which G2 pick definition applies to ``market``: ``net_edge`` or ``tb``."""
    return "tb" if main_min_edge.get(market) is None else "net_edge"


def _is_tb_g2_pick(snap: Mapping[str, Any], side: str) -> bool:
    """Plan section 9, pinned: consensus-anchored, consensus prob minus Polymarket ask >= 3.0 points.

    Gross of fees. Rows anchored on the Polymarket mid are never G2 picks.
    """
    from .cli.forecast_nba_anchor_v10 import SOURCE_CONSENSUS, TB_THRESHOLD

    if snap.get("anchor_source") != SOURCE_CONSENSUS:
        return False
    gap = _finite(((snap.get("tb") or {}).get("gap_by_side") or {}).get(side))
    # The stored gap is rounded to 6 decimals; the epsilon keeps an exact 3.0-point gap inside.
    return gap is not None and gap >= TB_THRESHOLD - 1e-9


def _tb_pick(
    base: Pick,
    snap: Mapping[str, Any],
    row: Mapping[str, Any],
    quote: Mapping[str, Any] | None,
    outcome: int | None,
    data_root: Path,
    as_of: datetime,
    tb_counts: Counter[str],
) -> Pick | None:
    """The row's T-B side as a pick (side, ask and outcome of the T-B side), or None if not flagged."""
    tb = snap.get("tb") or {}
    if not tb.get("evaluated"):
        tb_counts["not_evaluated_no_consensus_anchor"] += 1
        return None
    tb_counts["evaluated"] += 1
    if not tb.get("flag"):
        return None
    tb_counts["flagged"] += 1
    tb_side = str(tb.get("side"))
    try:
        tb_prob = float(tb["consensus_prob_at_contract"][tb_side])
        tb_ask = tb_prob - float(tb["gap_by_side"][tb_side])
    except (KeyError, TypeError, ValueError):
        tb_counts["flagged_missing_side_prices"] += 1
        return None
    if tb_side != base.side:
        tb_counts["flagged_off_row_side"] += 1
    tb_ask_q, tb_mid, _ = _quote_side(quote, base.market, tb_side, str(row["away_team"]))
    row_line = float(row["line"])
    tb_line = row_line if tb_side == base.side or base.market == "total" else -row_line
    return Pick(
        **{
            **asdict(base),
            "pick_id": f"{base.pick_id}:tb",
            "side": tb_side,
            "ask": round(tb_ask, 6) if tb_ask_q is None else tb_ask_q,
            "probability": tb_prob,
            "outcome": None if outcome is None or tb_side == base.side else 1 - outcome,
            "mid": tb_mid,
            "pm_close": _pm_close(
                data_root, snap.get("market_slug"), row, tb_side, tb_line, base.decision_time, as_of, base.tip
            ),
        }
    )


def run_g2(
    ledgers_db: Path,
    data_root: Path,
    artifact: Mapping[str, Any],
    *,
    as_of: datetime,
    draws: int = BOOTSTRAP_DRAWS,
) -> dict[str, Any]:
    """Load, score and evaluate every population; the dict the report and its JSON are written from."""
    inputs = load_g2_inputs(ledgers_db, data_root, artifact, as_of=as_of)
    captures, capture_diag = load_consensus_captures(inputs.capture_files)
    v9_scored = {key: score_pick(pick, captures, as_of=as_of) for key, pick in inputs.v9.items()}
    populations = {}
    scored_rows = {}
    for name in POPULATIONS:
        scored = [score_pick(p, captures, as_of=as_of) for p in inputs.populations[name]]
        populations[name] = evaluate_population(scored, v9_scored, draws=draws)
        scored_rows[name] = [_row_json(s) for s in scored]
    return {
        "as_of_utc": as_of.isoformat(),
        "freeze_utc": V10_FREEZE_UTC,
        "main_min_edge": inputs.main_min_edge,
        "g2_rule": {market: g2_rule(inputs.main_min_edge, market) for market in ("spread", "total")},
        "populations": populations,
        "row_exclusions": inputs.row_exclusions,
        "load_diagnostics": inputs.diagnostics,
        "capture_files": [str(p) for p in inputs.capture_files if p.is_file()],
        "capture_diagnostics": capture_diag,
        "rows": scored_rows,
        "v9_rows": [_row_json(s) for s in v9_scored.values()],
    }


def _row_json(scored: ScoredPick) -> dict[str, Any]:
    pick = asdict(scored.pick)
    for key in ("decision_time", "tip"):
        pick[key] = pick[key].isoformat()
    return {**pick, **{k: v for k, v in asdict(scored).items() if k != "pick"}}


__all__ = [
    "BENCHMARK_LEAD",
    "BENCHMARK_MAX_LEAD",
    "G2_KILL_PICKS",
    "G2_MIN_MEAN_CLV",
    "G2_MIN_PICKS",
    "ConsensusCapture",
    "Pick",
    "ScoredPick",
    "cluster_mean_ci",
    "consensus_side_probability",
    "evaluate_population",
    "g2_rule",
    "g2_verdict",
    "load_consensus_captures",
    "load_g2_inputs",
    "parse_espn_record",
    "run_g2",
    "score_pick",
    "select_t10_benchmark",
]
