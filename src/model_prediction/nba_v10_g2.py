"""Forward gate G2 for the NBA v10 overhaul: read-only scoring of ``nba-anchor-v10`` rows.

Pre-registered in ``docs/NBA_REVIEW_AND_V10_PLAN_2026-11-20.md`` (sections 6, 9, 10, and the G2
amendment pinned 2026-11-21 before any pick was scored); the criteria are fixed and must not be
re-tuned after results are seen. Over forward picks only (rows written by ``nba-anchor-v10`` since
its wiring on 2026-11-21), games clustered across markets. A G2 pick, per market: with the
artifact's ``main_min_edge`` null (as shipped, lambda = 0), a consensus-anchored v10 row whose
devigged consensus probability of a side minus the Polymarket ask for that side is >= 3.0 points,
gross of fees, graded on that side (track T-B, plan section 9); with ``main_min_edge`` set, a row
whose ``net_edge`` clears it. PASS needs every criterion:

- C1: at least 150 consensus-benchmark-scored picks.
- C2a (necessary, selection-correlated): mean CLV against the devigged consensus at T-10 of at
  least +1.0c with a 95% cluster-bootstrap CI (by game) above 0. T-B picks are selected on
  consensus minus ask, so this passes by construction unless the book drifts to Polymarket.
- C2b (selection-independent): Polymarket CLV net of fee, the Polymarket T-10 mid of the picked
  side minus (ask + 0.06 * ask * (1 - ask)), mean above 0 with the CI above 0.
- C3: paired v10 minus v9 on the C2b CLV (same game, market and T-10 quote), mean above 0.
- C4: Brier on settled picks no worse than the decision-time Polymarket mid on the same picks.
- C5: realized net-of-fee return per 1-unit settled pick above 0 (point estimate; 150 picks
  cannot establish it, and the report says so).

Diagnostics, not gating: D1 "who moved" (consensus change vs Polymarket mid change between the
decision and T-10) and D2 (picks whose Polymarket rung equals the decision ESPN line vs picks whose
consensus was translated to the rung).

Kill rule: a G2 failure at 300 or more picks is KILL, never "extend". Below 150 picks: PENDING. A
criterion that cannot be computed yet is never a pass.

Benchmarks, point in time. Both T-10 benchmarks take the LATEST observation with
``decision_time < observed_at_utc <= tip - 10 min``, tip being the earlier of the ledger row's and
the observation's own ``event_start_utc``; nothing is scored before tip; an observation older than
``tip - 40 min`` is stale (excluded, counted). The consensus benchmark reads
``<runtime root>/odds/nba/<ET date>/espn_odds.jsonl``; the Polymarket T-10 mid reads the pick's own
contract (``market_slug``) in ``polymarket_snapshots.jsonl`` and needs an open, two-sided book
(the side's mid and one minus the other side's mid, each from a quote at most 10 points wide, as
v10's Polymarket-mid anchor). Nothing falls back silently: every excluded pick carries a reason.

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
from dataclasses import asdict, dataclass, field, replace
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
# Polymarket US taker fee c * p * (1 - p) on the price paid (plan G2 amendment C2b/C5; the same
# coefficient as the nba-anchor-v10 artifact's ``fee_coefficient``).
FEE_COEFFICIENT = 0.06
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
    # Polymarket mid of this side in the exact decision quote (two-sided, <= 10 points wide).
    pm_mid_decision: float | None = None
    pm_close: float | None = None  # settlement's closing ask of this side (reference only)
    anchor_source: str | None = None
    anchor_observed_at_utc: str | None = None  # the decision-time consensus capture, if consensus-anchored
    # Every Polymarket quote of this contract, as this side's mid: the C2b T-10 candidates.
    pm_quotes: tuple[PolymarketQuote, ...] = ()


@dataclass(frozen=True)
class PolymarketQuote:
    """One stored Polymarket quote of a pick's contract, reduced to the pick side's mid.

    ``mid`` is None when the book was not open or not two-sided within 10 points; such a quote is
    never a T-10 mid.
    """

    observed_at_utc: datetime
    event_start_utc: datetime | None
    mid: float | None


@dataclass
class ScoredPick:
    pick: Pick
    reason: str | None  # None when scored; else the exclusion reason
    benchmark_observed_at_utc: str | None = None
    benchmark_lead_minutes: float | None = None
    benchmark_line: float | None = None
    benchmark_n_books: int | None = None
    consensus_prob: float | None = None
    clv: float | None = None  # C2a: T-10 consensus minus ask (selection-correlated for T-B picks)
    pm_reason: str | None = None  # None when a Polymarket T-10 mid was found; else the exclusion reason
    pm_mid_t10: float | None = None
    pm_mid_t10_observed_at_utc: str | None = None
    pm_clv_net: float | None = None  # C2b: pm_mid_t10 - (ask + fee)
    consensus_prob_decision: float | None = None  # D1: consensus at the decision capture, same side
    decision_consensus_line: float | None = None
    line_split: str = "unknown"  # D2: same_line / translated / unknown (no decision consensus)


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
def _select_t10(
    items: Sequence[Any],
    *,
    observed: Any,
    own_start: Any,
    usable: Any,
    decision_time: datetime,
    tip: datetime,
    as_of: datetime,
    max_lead: timedelta,
    missing: str,
    stale: str,
) -> tuple[Any | None, str | None]:
    """Shared T-10 window rule for the consensus (C2a) and Polymarket (C2b) benchmarks.

    Qualifying: ``decision_time < observed <= min(tip, item tip) - 10 min`` and ``usable(item)``.
    The latest qualifying item wins (a max-scan: the stores are ingest-ordered). Nothing is final
    before tip, and a latest item older than ``tip - max_lead`` is stale, never a T-10 value.
    """
    if as_of < tip:
        return None, "pre_tip_not_final"
    if not items:
        return None, f"{missing}_no_capture"
    in_window = []
    before = after = 0
    for item in items:
        start = own_start(item)
        cutoff = (tip if start is None else min(tip, start)) - BENCHMARK_LEAD
        if observed(item) <= decision_time:
            before += 1
        elif observed(item) > cutoff:
            after += 1
        else:
            in_window.append(item)
    if not in_window:
        if after == 0:
            return None, f"{missing}_all_before_decision"
        if before == 0:
            return None, f"{missing}_all_after_t10"
        return None, f"{missing}_outside_window"
    qualifying = [item for item in in_window if usable(item)]
    if not qualifying:
        return None, f"{missing}_no_two_sided_quote"
    best = max(qualifying, key=observed)
    start = own_start(best)
    if observed(best) < (tip if start is None else min(tip, start)) - max_lead:
        return None, stale
    return best, None


def select_t10_benchmark(
    captures: Sequence[ConsensusCapture],
    *,
    decision_time: datetime,
    tip: datetime,
    as_of: datetime,
    max_lead: timedelta = BENCHMARK_MAX_LEAD,
) -> tuple[ConsensusCapture | None, str | None]:
    """The T-10 consensus capture for one pick (C2a), or (None, exclusion reason).

    Qualifying: ``decision_time < observed_at_utc <= min(tip, capture tip) - 10 min``; the latest
    wins. Reasons: ``pre_tip_not_final``, ``missing_benchmark_no_capture``,
    ``missing_benchmark_all_before_decision``, ``missing_benchmark_all_after_t10``,
    ``missing_benchmark_outside_window`` (a mix of both) and ``stale_benchmark``.
    """
    return _select_t10(
        captures,
        observed=lambda c: c.observed_at_utc,
        own_start=lambda c: c.event_start_utc,
        usable=lambda c: True,
        decision_time=decision_time,
        tip=tip,
        as_of=as_of,
        max_lead=max_lead,
        missing="missing_benchmark",
        stale="stale_benchmark",
    )


def select_pm_t10_mid(
    quotes: Sequence[PolymarketQuote],
    *,
    decision_time: datetime,
    tip: datetime,
    as_of: datetime,
    max_lead: timedelta = BENCHMARK_MAX_LEAD,
) -> tuple[PolymarketQuote | None, str | None]:
    """The Polymarket T-10 mid for one pick (C2b), or (None, exclusion reason).

    The latest quote of the pick's own contract (rung) with
    ``decision_time < observed_at_utc <= min(tip, quote tip) - 10 min`` and a two-sided open book
    for the side; the same stale rule as the consensus. Reasons are the consensus ones with the
    ``missing_pm_benchmark`` / ``stale_pm_benchmark`` prefix, plus
    ``missing_pm_benchmark_no_two_sided_quote``.
    """
    return _select_t10(
        quotes,
        observed=lambda q: q.observed_at_utc,
        own_start=lambda q: q.event_start_utc,
        usable=lambda q: q.mid is not None,
        decision_time=decision_time,
        tip=tip,
        as_of=as_of,
        max_lead=max_lead,
        missing="missing_pm_benchmark",
        stale="stale_pm_benchmark",
    )


def fee(price: float) -> float:
    """Polymarket US taker fee per contract at ``price``: ``0.06 * p * (1 - p)``."""
    return FEE_COEFFICIENT * price * (1.0 - price)


def net_return(pick: Pick) -> float | None:
    """C5: realized return per 1 unit staked, net of fee (None while unsettled or on a push)."""
    if pick.outcome is None:
        return None
    cost = pick.ask + fee(pick.ask)
    return pick.outcome / cost - 1.0


def consensus_side_probability(
    capture: ConsensusCapture, side: str, contract_line: float, sigma: float
) -> float:
    """Consensus probability of ``side`` at the contract's line (normal translation, as v10's T-B)."""
    from .cli.forecast_nba_anchor_v10 import anchor_mean

    mean = anchor_mean(capture.line, capture.p_low, sigma)
    p_low = NormalDist().cdf((contract_line - mean) / sigma)
    return p_low if side == _LOW_SIDE[capture.market] else 1.0 - p_low


def _decision_capture(pick: Pick, captures: Sequence[ConsensusCapture]) -> ConsensusCapture | None:
    """The consensus capture the row was anchored on (matched on its observed time), if any."""
    if not pick.anchor_observed_at_utc:
        return None
    try:
        anchored = parse_utc(pick.anchor_observed_at_utc)
    except (TypeError, ValueError):
        return None
    return next((c for c in captures if c.observed_at_utc == anchored), None)


def score_pick(
    pick: Pick,
    captures: Mapping[tuple[str, str], Sequence[ConsensusCapture]],
    *,
    as_of: datetime,
) -> ScoredPick:
    """Both T-10 benchmarks of one pick, each with its own exclusion reason, plus D1/D2 inputs."""
    own = captures.get((pick.event_id, pick.market), ())
    scored = ScoredPick(pick, None)

    decision_capture = _decision_capture(pick, own)
    if decision_capture is not None:
        scored.consensus_prob_decision = round(
            consensus_side_probability(decision_capture, pick.side, pick.contract_line, pick.sigma), 6
        )
        scored.decision_consensus_line = decision_capture.line
        same = abs(decision_capture.line - pick.contract_line) < 1e-9
        scored.line_split = "same_line" if same else "translated"

    quote, scored.pm_reason = select_pm_t10_mid(
        pick.pm_quotes, decision_time=pick.decision_time, tip=pick.tip, as_of=as_of
    )
    if quote is not None and quote.mid is not None:
        scored.pm_mid_t10 = quote.mid
        scored.pm_mid_t10_observed_at_utc = quote.observed_at_utc.isoformat()
        scored.pm_clv_net = round(quote.mid - (pick.ask + fee(pick.ask)), 6)

    capture, scored.reason = select_t10_benchmark(
        own, decision_time=pick.decision_time, tip=pick.tip, as_of=as_of
    )
    if capture is not None:
        prob = consensus_side_probability(capture, pick.side, pick.contract_line, pick.sigma)
        scored.benchmark_observed_at_utc = capture.observed_at_utc.isoformat()
        scored.benchmark_lead_minutes = round((pick.tip - capture.observed_at_utc).total_seconds() / 60, 1)
        scored.benchmark_line = capture.line
        scored.benchmark_n_books = capture.n_books
        scored.consensus_prob = round(prob, 6)
        scored.clv = round(prob - pick.ask, 6)
    return scored


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
    return "PASS", f"all criteria pass at {n_picks} picks"


def _ci_block(values: Sequence[float], clusters: Sequence[str], draws: int) -> dict[str, Any]:
    return asdict(cluster_mean_ci(values, clusters, draws=draws))


def who_moved(delta_consensus: float | None, delta_poly: float | None) -> str:
    """D1 reading of the mean moves between decision and T-10 on the picked side.

    For a T-B pick the book sat above Polymarket at the decision; the gap closes by
    ``delta_poly - delta_consensus``. The book's share is ``-delta_consensus``.
    """
    if delta_consensus is None or delta_poly is None:
        return "not computable (no pick has decision and T-10 values for both the book and Polymarket)"
    closure = delta_poly - delta_consensus
    if closure <= 0:
        return "the consensus-Polymarket gap did not close on average"
    if -delta_consensus > delta_poly:
        return (
            "the book moved toward Polymarket more than Polymarket moved toward the book: "
            "T-B is fading a stale single-book line"
        )
    return "Polymarket moved toward the book more than the book moved toward Polymarket"


def _ci_pass(ci: MeanCI, floor: float) -> str:
    """PASS when the mean clears ``floor`` and the 95% CI lies above 0."""
    if ci.mean is None or ci.low is None:
        return NOT_EVALUABLE
    return PASS if ci.mean >= floor and ci.low > 0.0 else FAIL


def evaluate_population(
    scored: Sequence[ScoredPick],
    v9_by_key: Mapping[tuple[str, str], ScoredPick],
    *,
    draws: int = BOOTSTRAP_DRAWS,
) -> dict[str, Any]:
    """The amended G2 scorecard of one population (criteria C1-C5, verdict, diagnostics D1/D2)."""
    games = [s.pick.event_id for s in scored]
    ok = [s for s in scored if s.reason is None and s.clv is not None]
    exclusions = Counter(s.reason for s in scored if s.reason is not None)
    c2a = cluster_mean_ci([s.clv for s in ok], [s.pick.event_id for s in ok], draws=draws)  # type: ignore[misc]

    # C2b: selection-independent Polymarket CLV net of fee, with its own benchmark and exclusions.
    pm_ok = [s for s in scored if s.pm_clv_net is not None]
    pm_excl = Counter(str(s.pm_reason) for s in scored if s.pm_clv_net is None)
    c2b = cluster_mean_ci([s.pm_clv_net for s in pm_ok], [s.pick.event_id for s in pm_ok], draws=draws)  # type: ignore[misc]

    # C3: paired v10 - v9 on the C2b CLV, same game and market, same Polymarket T-10 quote.
    diffs: list[float] = []
    diff_games: list[str] = []
    pair_reasons: Counter[str] = Counter()
    for s in pm_ok:
        v9 = v9_by_key.get((s.pick.event_id, s.pick.market))
        if v9 is None:
            pair_reasons["no_v9_row"] += 1
        elif v9.pm_clv_net is None:
            pair_reasons[f"v9_{v9.pm_reason}"] += 1
        elif v9.pm_mid_t10_observed_at_utc != s.pm_mid_t10_observed_at_utc:
            pair_reasons["v9_different_benchmark"] += 1
        elif abs(v9.pick.decision_time - s.pick.decision_time) > PAIR_MAX_DECISION_GAP:
            # Both rows are replaced together on every daily re-run; a wider gap means they come
            # from different runs (one model failed or was skipped in a run), so not a fair pair.
            pair_reasons["v9_decision_not_same_run"] += 1
        else:
            diffs.append(s.pm_clv_net - v9.pm_clv_net)  # type: ignore[operator]
            diff_games.append(s.pick.event_id)
    paired = cluster_mean_ci(diffs, diff_games, draws=draws)

    # C4 on the consensus-scored picks (the C1 sample).
    settled = [s for s in ok if s.pick.outcome is not None and s.pick.pm_mid_decision is not None]
    brier_reasons = Counter(
        "unsettled_or_push" if s.pick.outcome is None else "no_decision_mid"
        for s in ok
        if s.pick.outcome is None or s.pick.pm_mid_decision is None
    )
    outcomes = [int(s.pick.outcome) for s in settled]  # type: ignore[arg-type]
    brier_model = brier([s.pick.probability for s in settled], outcomes)
    brier_mid = brier([float(s.pick.pm_mid_decision) for s in settled], outcomes)  # type: ignore[arg-type]
    by_anchor: dict[str, dict[str, Any]] = {}
    for source in sorted({str(s.pick.anchor_source) for s in settled}):
        rows = [s for s in settled if str(s.pick.anchor_source) == source]
        row_outcomes = [int(s.pick.outcome) for s in rows]  # type: ignore[arg-type]
        by_anchor[source] = {
            "n": len(rows),
            "brier_model": brier([s.pick.probability for s in rows], row_outcomes),
            "brier_mid": brier([float(s.pick.pm_mid_decision) for s in rows], row_outcomes),  # type: ignore[arg-type]
        }

    # C5: realized net-of-fee return per 1-unit pick over every settled pick (needs no benchmark).
    returns = [(r, s.pick.event_id) for s in scored if (r := net_return(s.pick)) is not None]
    c5 = cluster_mean_ci([r for r, _ in returns], [g for _, g in returns], draws=draws)

    # D1: who moved between the decision and T-10, on the picked side.
    moved = [
        s
        for s in scored
        if None not in (s.consensus_prob_decision, s.consensus_prob, s.pick.pm_mid_decision, s.pm_mid_t10)
    ]
    moved_games = [s.pick.event_id for s in moved]
    d_cons = cluster_mean_ci(
        [s.consensus_prob - s.consensus_prob_decision for s in moved],  # type: ignore[operator]
        moved_games,
        draws=draws,
    )
    d_poly = cluster_mean_ci(
        [s.pm_mid_t10 - s.pick.pm_mid_decision for s in moved],  # type: ignore[operator]
        moved_games,
        draws=draws,
    )

    # D2: same-line vs translated-line picks (the decision consensus line vs the contract rung).
    splits: dict[str, dict[str, Any]] = {}
    for split in ("same_line", "translated", "unknown"):
        part = [s for s in scored if s.line_split == split]
        a = [s for s in part if s.reason is None and s.clv is not None]
        b = [s for s in part if s.pm_clv_net is not None]
        r = [(ret, s.pick.event_id) for s in part if (ret := net_return(s.pick)) is not None]
        splits[split] = {
            "rows": len(part),
            "c2a_consensus_clv": _ci_block([s.clv for s in a], [s.pick.event_id for s in a], draws),  # type: ignore[misc]
            "c2b_polymarket_clv_net": _ci_block(
                [s.pm_clv_net for s in b],  # type: ignore[misc]
                [s.pick.event_id for s in b],
                draws,
            ),
            "c5_net_return": _ci_block([x for x, _ in r], [g for _, g in r], draws),
        }

    closes = [s for s in scored if s.pick.pm_close is not None]
    close_clv = cluster_mean_ci(
        [s.pick.pm_close - s.pick.ask for s in closes],  # type: ignore[operator]
        [s.pick.event_id for s in closes],
        draws=draws,
    )

    criteria = {
        "c1_n_picks": PASS if c2a.n >= G2_MIN_PICKS else FAIL,
        "c2a_consensus_clv": _ci_pass(c2a, G2_MIN_MEAN_CLV),
        # "Mean > 0 with the CI excluding 0": the CI lying above 0 already implies the mean does.
        "c2b_polymarket_clv_net": _ci_pass(c2b, 0.0),
        "c3_clv_above_v9": NOT_EVALUABLE if paired.mean is None else PASS if paired.mean > 0.0 else FAIL,
        "c4_brier_vs_mid": (
            NOT_EVALUABLE
            if brier_model is None or brier_mid is None
            else PASS
            if brier_model <= brier_mid
            else FAIL
        ),
        "c5_net_return": NOT_EVALUABLE if c5.mean is None else PASS if c5.mean > 0.0 else FAIL,
    }
    verdict, why = g2_verdict(c2a.n, {k: v for k, v in criteria.items() if k != "c1_n_picks"})
    leads = [s.benchmark_lead_minutes for s in ok if s.benchmark_lead_minutes is not None]
    return {
        "rows": len(scored),
        "row_games": len(set(games)),
        "scored": c2a.n,
        "games": c2a.n_games,
        "excluded": dict(sorted(exclusions.items())),
        "criteria": criteria,
        "verdict": verdict,
        "verdict_reason": why,
        "consensus_clv": asdict(c2a),
        "polymarket_clv_net": {**asdict(c2b), "excluded": dict(sorted(pm_excl.items()))},
        "v9_paired_clv_diff": {
            **asdict(paired),
            "basis": "polymarket_clv_net",
            "unpaired": dict(sorted(pair_reasons.items())),
        },
        "brier": {
            "n": len(settled),
            "model": brier_model,
            "polymarket_mid": brier_mid,
            "excluded": dict(sorted(brier_reasons.items())),
            "by_anchor_source": by_anchor,
        },
        "net_return": asdict(c5),
        "d1_who_moved": {
            "delta_consensus": asdict(d_cons),
            "delta_poly": asdict(d_poly),
            "reading": who_moved(d_cons.mean, d_poly.mean),
        },
        "d2_line_split": splits,
        "polymarket_close_ask_clv_reference": asdict(close_clv),
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

    def contract(self, slug: str | None, tip: datetime) -> list[dict[str, Any]]:
        """Every stored quote of one contract (rung), in no particular order."""
        if not slug:
            return []
        index = self._load(tip.astimezone(EASTERN).date().isoformat())
        return [item for (item_slug, _), item in index.items() if item_slug == slug]


def _pm_quotes(
    records: Sequence[Mapping[str, Any]], market: str, side: str, away_team: str
) -> tuple[PolymarketQuote, ...]:
    """A contract's stored quotes reduced to ``side``'s two-sided mid (None when unusable).

    A quote whose book is not open (halted, closed, expired) is never a mid, as in settlement's
    ``closing_snapshot``.
    """
    out = []
    for record in records:
        try:
            observed = parse_utc(str(record["observed_at_utc"]))
        except (KeyError, TypeError, ValueError):
            continue
        try:
            own_start = parse_utc(str(record["event_start_utc"])) if record.get("event_start_utc") else None
        except (TypeError, ValueError):
            continue
        state = record.get("market_state")
        _, mid, problem = _quote_side(record, market, side, away_team)
        usable = problem is None and (state is None or state == "MARKET_STATE_OPEN")
        out.append(PolymarketQuote(observed, own_start, mid if usable else None))
    return tuple(out)


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
                pm_mid_decision=mid,
                pm_close=_pm_close(data_root, slug, row, selection, line, decision, as_of, tip),
                pm_quotes=_pm_quotes(quotes.contract(slug, tip), market, selection, str(row["away_team"])),
            )
        else:
            v9_excl["no_live_v10_row_for_game_market"] += 1

    # v9 is translated with its v10 pair's sigma, so the benchmark is literally the same.
    sigma_by_key = {(p.event_id, p.market): p.sigma for p in out.populations["all_flat"]}
    for key, pick in list(out.v9.items()):
        out.v9[key] = replace(pick, sigma=sigma_by_key.get(key, float("nan")))

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
    from .cli.forecast_nba_anchor_v10 import SOURCE_CONSENSUS

    contract_quotes = quotes.contract(slug, tip)
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
        pm_mid_decision=mid,
        pm_close=_pm_close(data_root, slug, row, side, row_line, decision, as_of, tip),
        anchor_source=snap.get("anchor_source"),
        anchor_observed_at_utc=(
            snap.get("anchor_observed_at_utc") if snap.get("anchor_source") == SOURCE_CONSENSUS else None
        ),
        pm_quotes=_pm_quotes(contract_quotes, market, side, str(row["away_team"])),
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

    tb_pick = _tb_pick(base, snap, row, quote, contract_quotes, outcome, data_root, as_of, tb_counts)
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
    contract_quotes: Sequence[Mapping[str, Any]],
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
    away = str(row["away_team"])
    return replace(
        base,
        pick_id=f"{base.pick_id}:tb",
        side=tb_side,
        ask=round(tb_ask, 6) if tb_ask_q is None else tb_ask_q,
        probability=tb_prob,
        outcome=None if outcome is None or tb_side == base.side else 1 - outcome,
        pm_mid_decision=tb_mid,
        pm_close=_pm_close(
            data_root, snap.get("market_slug"), row, tb_side, tb_line, base.decision_time, as_of, base.tip
        ),
        pm_quotes=_pm_quotes(contract_quotes, base.market, tb_side, away),
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
    pick.pop("pm_quotes")  # every stored quote of the contract: candidates, not results
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
    "PolymarketQuote",
    "ScoredPick",
    "cluster_mean_ci",
    "consensus_side_probability",
    "evaluate_population",
    "fee",
    "g2_rule",
    "g2_verdict",
    "load_consensus_captures",
    "load_g2_inputs",
    "net_return",
    "parse_espn_record",
    "run_g2",
    "score_pick",
    "select_pm_t10_mid",
    "select_t10_benchmark",
    "who_moved",
]
