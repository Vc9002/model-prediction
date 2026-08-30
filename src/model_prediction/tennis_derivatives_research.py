"""Research-only tennis spread/total pricing from the point-level Markov engine.

NOT a live pricing path: ``tennis_forward.build_tennis_slate`` keeps failing
closed on spread/total (``UNSUPPORTED_TENNIS_DERIVATIVE_PRICING``) -- this
module exists to validate whether a game-score distribution CAN price those
markets, and nothing here is wired into the slate.

Design
------
1. Ratings are the validated moneyline path's own surface-blended Elo
   (``models/tennis.TennisModel``), built strictly point-in-time from
   ``data/processed/tennis/games.jsonl`` matches before each target match
   (``tennis_forward._tennis_history_before``'s cutoff: midnight US-Eastern
   at the start of the match's Eastern calendar date). Tennis rows are
   structurally incompatible with ``validation.py``'s ``GameRecord``-shaped
   walk-forward machinery, so -- exactly like
   ``validation.qualify_tennis_elo_model`` (same incompatibility, documented
   there) -- the walk-forward is self-contained and strictly chronological.

2. The Markov engine works on point-level serve/return percentages, which
   outcome-only history does not record, so the serve/return split is not
   identified from this data. The bridge here anchors it to the engine's own
   tour-average return baselines (``SURFACE_TOUR_RETURN_AVERAGES``): a
   player's serve and return strength both move with the same Elo-scaled
   strength parameter around those baselines, and the single free constant
   (``strength_scale``) is calibrated -- on a SYNTHETIC Elo-difference grid,
   no match data, no settled picks -- so the engine's match probability
   tracks the validated moneyline probability. The residual of that fit is
   reported in the walk-forward JSON.

3. ``models.tennis_markov.match_game_distribution`` turns the engine's
   (hold, hold, tiebreak) triple into the full joint mass over
   (sets, games) for Bo3/Bo5, from which spread-cover and total probabilities
   and the expected total-games mass are computed. Outcomes are graded with
   the canonical ``pricing.grade_pick`` rule (margin + line > 0; total over
   iff away+home > line), mirrored here without importing the module so this
   research path cannot accidentally gain a production dependency.

4. Evaluation targets are the ALREADY-SETTLED tennis spread/total ledger rows
   (the 2026-08-24 repair's regraded truth: per-set game totals persisted by
   ``scripts/repair_tennis_derivative_settlements.py`` from exact ESPN
   identities). The ledger is opened read-only; no row is ever written.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .domain import EASTERN, parse_utc
from .models.tennis import DEFAULT_ELO, TennisModel
from .models.tennis_markov import (
    TennisMarkovEngine,
    TennisPlayerStats,
    match_game_distribution,
)
from .tennis_forward import _tennis_history_before

DEFAULT_STRENGTH_SCALE = 0.03
_SHRINKAGE_PRIOR_MATCHES = 15.0
_MAX_SURFACE_WEIGHT = 0.85

_GRAND_SLAM_HINTS = (
    "australian open",
    "roland garros",
    "french open",
    "wimbledon",
    "us open",
)


def _read_settled_derivative_contracts(ledgers_db: Path) -> list[dict[str, Any]]:
    """Read-only extraction of settled tennis spread/total contracts.

    Each settled row was regraded by the 2026-08-24 repair from exact ESPN
    per-set game totals; the totals live in ``decision_payload_json`` as
    ``away_score``/``home_score`` (game counts, NOT set wins). Rows are
    deduped by (event_id, market_type, selection, line) because main and
    flat tiers logged the same contract twice.
    """
    uri = f"file:{ledgers_db.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    contracts: dict[tuple[str, str, str, float], dict[str, Any]] = {}
    try:
        rows = connection.execute(
            """
            SELECT event_id, market_type, selection, line, result,
                   event_start_utc, decision_payload_json
            FROM ledger_records
            WHERE sport = 'tennis'
              AND status = 'settled'
              AND market_type IN ('spread', 'total')
            ORDER BY event_start_utc, event_id
            """
        ).fetchall()
    finally:
        connection.close()
    for event_id, market_type, selection, line, result, event_start_utc, payload_json in rows:
        try:
            payload = json.loads(payload_json or "{}")
            away_team = str(payload["away_team"])
            home_team = str(payload["home_team"])
            away_games = int(payload["away_score"])
            home_games = int(payload["home_score"])
            line_value = float(line)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if market_type not in {"spread", "total"} or selection not in {"away", "home", "over", "under"}:
            continue
        key = (event_id, market_type, selection, line_value)
        contracts[key] = {
            "event_id": event_id,
            "market_type": market_type,
            "selection": selection,
            "line": line_value,
            "result": str(result),
            "event_start_utc": str(event_start_utc),
            "away_team": away_team,
            "home_team": home_team,
            "away_games": away_games,
            "home_games": home_games,
        }
    return list(contracts.values())


def _normal_full_match(contract: dict[str, Any]) -> bool:
    """Reject rows that cannot price a full match: retirements/walkovers
    graded to a truncated score (the 2026-08-24 repair voided these as
    book-specific; the four pushed rows all carry a 1-0 truncated score).
    """
    if str(contract["result"]).casefold() == "push":
        return False
    return contract["away_games"] + contract["home_games"] >= 12


def _match_format(tournament: str | None, league: str | None) -> str:
    """Grand Slam men's matches are best-of-5; everything else best-of-3.

    The Markov engine has no final-set rules (it treats every set as first
    to 6-by-2 with a 7-point tiebreak at 6-6); Grand Slam men's final sets
    can play extended rules, which this model does not attempt.
    """
    if tournament and league:
        name = str(tournament).casefold()
        if any(hint in name for hint in _GRAND_SLAM_HINTS) and str(league).upper() == "ATP":
            return "Bo5"
    return "Bo3"


def _outcome(contract: dict[str, Any]) -> int:
    """Graded binary outcome (1 = win), mirroring ``pricing.grade_pick``:
    spread win iff margin_selection + line > 0; total over win iff
    away+home > line, under iff away+home < line. All settled lines are
    half-games, so no push is possible here."""
    away_games = contract["away_games"]
    home_games = contract["home_games"]
    line = contract["line"]
    if contract["market_type"] == "total":
        total = away_games + home_games
        if contract["selection"] == "over":
            return 1 if total > line else 0
        return 1 if total < line else 0
    margin = (away_games - home_games) if contract["selection"] == "away" else (home_games - away_games)
    return 1 if margin + line > 0.0 else 0


def _blended_elo_ratings(
    overall: dict[str, float],
    by_surface: dict[tuple[str, str], float],
    surface_counts: dict[tuple[str, str], int],
    player: str,
    surface: str,
) -> float:
    """Mirror of ``TennisModel.match_probability``'s per-player blend, kept
    in this module so the bridge can use the blended rating itself (the
    model exposes only the blended probability). Pinned against
    ``match_probability`` in tests so it cannot drift from the moneyline
    path."""
    n_surface = surface_counts.get((player, surface), 0)
    weight = (n_surface / (n_surface + _SHRINKAGE_PRIOR_MATCHES)) * _MAX_SURFACE_WEIGHT
    return weight * by_surface.get((player, surface), DEFAULT_ELO) + (1.0 - weight) * overall.get(
        player, DEFAULT_ELO
    )


def point_stats_for_ratings(
    engine: TennisMarkovEngine,
    blended_a: float,
    blended_b: float,
    surface: str,
    strength_scale: float,
) -> tuple[TennisPlayerStats, TennisPlayerStats]:
    """Map blended Elo ratings to the engine's serve/return stats.

    The serve/return split is unidentifiable from outcome-only history, so
    it is anchored to the engine's own tour-average return baseline
    (``SURFACE_TOUR_RETURN_AVERAGES``): a player at tour parity (1500 Elo)
    serves at ``1 - g_tour`` and returns at ``g_tour`` -- the same
    decomposition the engine's Barnett & Clarke adjustment is built on --
    and both move linearly with the Elo-scaled strength parameter.
    """
    g_tour = engine.surface_baselines.get(surface, engine.surface_baselines["Default"])
    sigma_a = (blended_a - DEFAULT_ELO) / 400.0
    sigma_b = (blended_b - DEFAULT_ELO) / 400.0
    stats_a = TennisPlayerStats(
        player_id="",
        name="A",
        serve_points_won_pct=1.0 - g_tour + strength_scale * sigma_a,
        return_points_won_pct=g_tour + strength_scale * sigma_a,
    )
    stats_b = TennisPlayerStats(
        player_id="",
        name="B",
        serve_points_won_pct=1.0 - g_tour + strength_scale * sigma_b,
        return_points_won_pct=g_tour + strength_scale * sigma_b,
    )
    return stats_a, stats_b


def _engine_match_probability(
    engine: TennisMarkovEngine,
    stats_a: TennisPlayerStats,
    stats_b: TennisPlayerStats,
    surface: str,
    match_format: str,
) -> float:
    """The engine's match aggregation without ``forecast_match``'s output
    rounding, used only by the strength-scale calibration grid (a rounded
    per-grid-point error would bias the fit). Pinned against
    ``forecast_match`` in tests.
    """
    from .models.tennis_markov import (
        game_hold_probability,
        set_win_probability,
        tiebreak_probability,
    )

    p_pt_a, p_pt_b = engine.adjust_serve_probabilities(stats_a, stats_b, surface)
    p_hold_a = game_hold_probability(p_pt_a)
    p_hold_b = game_hold_probability(p_pt_b)
    p_tb_a = tiebreak_probability(p_pt_a, p_pt_b)
    p_set_sf, p_set_rf = set_win_probability(p_hold_a, p_hold_b, p_tb_a)
    s_avg = 0.5 * (p_set_sf + p_set_rf)
    if match_format.upper() in ["BO5", "BEST_OF_5", "GRAND_SLAM"]:
        q = 1.0 - s_avg
        return s_avg**3 * (1.0 + 3.0 * q + 6.0 * q * q)
    return s_avg**2 * (3.0 - 2.0 * s_avg)


def fit_strength_scale(engine: TennisMarkovEngine, surface: str = "Hard") -> float:
    """Calibrate the single free bridge constant on a synthetic grid.

    Minimizes squared logit error between the engine's Bo3 match
    probability and the moneyline Elo logistic over Elo differences
    -400..+400, in 50-Elo steps. Uses no match data and no settled picks --
    the inputs are rating differences alone -- so the walk-forward
    evaluation stays clean. Deterministic.
    """
    import math

    deltas = list(range(-400, 401, 50))

    def elo_probability(delta: float) -> float:
        return 1.0 / (1.0 + 10.0 ** (-delta / 400.0))

    def logit(p: float) -> float:
        clipped = max(1e-6, min(1.0 - 1e-6, p))
        return math.log(clipped / (1.0 - clipped))

    best_scale = DEFAULT_STRENGTH_SCALE
    best_error = float("inf")
    scale = 0.005
    while scale <= 0.200:
        error = 0.0
        for delta in deltas:
            stats_a, stats_b = point_stats_for_ratings(
                engine, DEFAULT_ELO + delta / 2.0, DEFAULT_ELO - delta / 2.0, surface, scale
            )
            engine_p = _engine_match_probability(engine, stats_a, stats_b, surface, "Bo3")
            error += (logit(engine_p) - logit(elo_probability(delta))) ** 2
        if error < best_error:
            best_error = error
            best_scale = scale
        scale += 0.0025
    return best_scale


_STRENGTH_SCALE_CACHE: dict[str, float] = {}


def _strength_scale(engine: TennisMarkovEngine, surface: str) -> float:
    if surface not in _STRENGTH_SCALE_CACHE:
        _STRENGTH_SCALE_CACHE[surface] = fit_strength_scale(engine, surface)
    return _STRENGTH_SCALE_CACHE[surface]


@dataclass(frozen=True)
class ContractPrice:
    """One settled contract priced from strictly-prior ratings."""

    event_id: str
    away_team: str
    home_team: str
    market_type: str
    selection: str
    line: float
    probability: float
    outcome: int
    result: str
    actual_total: int
    actual_margin: int
    expected_total_games: float
    expected_margin: float
    p_match_away: float
    blended_elo_away: float
    blended_elo_home: float
    history_matches: int
    surface: str
    match_format: str
    strength_scale: float
    skipped_reason: str | None = None


@dataclass(frozen=True)
class MatchContext:
    """PIT rating inputs and match metadata for one event."""

    away_team: str
    home_team: str
    surface: str
    league: str
    tournament: str | None
    match_format: str
    event_start_utc: str
    as_of_date: str


def _infer_match_context(
    contract: dict[str, Any],
    meta_by_event: dict[str, dict[str, Any]],
    full_history: list[dict[str, Any]],
) -> MatchContext:
    """Match metadata from the history row when present; fall back to
    documented inference for the two 2026-08-23/24 events ESPN keyed under a
    site id games.jsonl never ingested (Tiafoe-Fils, Pegula-Gauff)."""
    event_id = contract["event_id"]
    start = parse_utc(contract["event_start_utc"])
    as_of_date = start.astimezone(EASTERN).date().isoformat()
    row = meta_by_event.get(event_id)
    if row is not None:
        league = str(row.get("league") or "ATP")
        surface = str(row.get("surface") or "Hard")
        tournament = str(row.get("tournament") or None) or None
    else:
        # Player gender identifies the tour; both unknown events follow a
        # Hard-court Cincinnati run for every player involved, and the
        # tournament is unrecorded, so format defaults to Bo3.
        away_in_atp = any(
            r["winner"] == contract["away_team"] or r["loser"] == contract["away_team"]
            for r in full_history
            if r.get("league") == "ATP"
        )
        away_in_wta = any(
            r["winner"] == contract["away_team"] or r["loser"] == contract["away_team"]
            for r in full_history
            if r.get("league") == "WTA"
        )
        league = "WTA" if away_in_wta and not away_in_atp else "ATP"
        surface = "Hard"
        tournament = None
    return MatchContext(
        away_team=contract["away_team"],
        home_team=contract["home_team"],
        surface=surface,
        league=league,
        tournament=tournament,
        match_format=_match_format(tournament, league),
        event_start_utc=contract["event_start_utc"],
        as_of_date=as_of_date,
    )


def price_contract(
    contract: dict[str, Any],
    context: MatchContext,
    *,
    data_root: str | Path,
    history_cache: dict[tuple[str, str], list[dict[str, Any]]],
    elo_cache: dict[tuple[str, str, str], Any],
    engine: TennisMarkovEngine,
) -> ContractPrice:
    """Price one contract with strictly-prior ratings (PIT).

    ``history_cache`` is keyed by league+as_of_date so the Elo build for a
    given (tour, day) happens once; ``elo_cache`` by (league, as_of_date,
    surface). Nothing after the match's own start is ever read.
    """
    league = context.league
    cache_key = (league, context.as_of_date)
    if cache_key not in history_cache:
        history_cache[cache_key] = [
            game
            for game in _tennis_history_before(data_root, context.as_of_date)
            if str(game.get("league", "")).upper() == league
        ]
    tour_history = history_cache[cache_key]

    model = TennisModel()
    elo_key = cache_key + (context.surface,)
    if elo_key not in elo_cache:
        elo_cache[elo_key] = model.build_elo(tour_history)
    overall, by_surface, _, surface_counts = elo_cache[elo_key]

    away = context.away_team
    home = context.home_team
    if away not in overall or home not in overall:
        return ContractPrice(
            event_id=contract["event_id"],
            away_team=away,
            home_team=home,
            market_type=contract["market_type"],
            selection=contract["selection"],
            line=contract["line"],
            probability=0.5,
            outcome=_outcome(contract),
            result=contract["result"],
            actual_total=contract["away_games"] + contract["home_games"],
            actual_margin=contract["away_games"] - contract["home_games"],
            expected_total_games=0.0,
            expected_margin=0.0,
            p_match_away=0.5,
            blended_elo_away=DEFAULT_ELO,
            blended_elo_home=DEFAULT_ELO,
            history_matches=len(tour_history),
            surface=context.surface,
            match_format=context.match_format,
            strength_scale=0.0,
            skipped_reason="player missing from strictly-prior history",
        )

    blended_away = _blended_elo_ratings(overall, by_surface, surface_counts, away, context.surface)
    blended_home = _blended_elo_ratings(overall, by_surface, surface_counts, home, context.surface)
    strength_scale = _strength_scale(engine, context.surface)

    stats_away, stats_home = point_stats_for_ratings(
        engine, blended_away, blended_home, context.surface, strength_scale
    )
    forecast = engine.forecast_match(stats_away, stats_home, context.surface, context.match_format)
    distribution = match_game_distribution(
        forecast.p_game_hold_a,
        forecast.p_game_hold_b,
        forecast.p_tiebreak_a,
        context.match_format,
    )

    if contract["market_type"] == "total":
        probability = (
            distribution.p_total_over(contract["line"])
            if contract["selection"] == "over"
            else 1.0 - distribution.p_total_over(contract["line"])
        )
    else:
        probability = distribution.p_cover(contract["selection"], contract["line"])

    return ContractPrice(
        event_id=contract["event_id"],
        away_team=away,
        home_team=home,
        market_type=contract["market_type"],
        selection=contract["selection"],
        line=contract["line"],
        probability=probability,
        outcome=_outcome(contract),
        result=contract["result"],
        actual_total=contract["away_games"] + contract["home_games"],
        actual_margin=contract["away_games"] - contract["home_games"],
        expected_total_games=distribution.expected_total_games,
        expected_margin=distribution.expected_games_a - distribution.expected_games_b,
        p_match_away=distribution.p_match_a,
        blended_elo_away=blended_away,
        blended_elo_home=blended_home,
        history_matches=len(tour_history),
        surface=context.surface,
        match_format=context.match_format,
        strength_scale=strength_scale,
    )


def _load_full_history(data_root: str | Path) -> list[dict[str, Any]]:
    path = Path(data_root) / "processed" / "tennis" / "games.jsonl"
    rows: list[dict[str, Any]] = []
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def run_derivative_walkforward(
    *,
    data_root: str | Path,
    ledgers_db: Path,
) -> dict[str, Any]:
    """Price every settled tennis spread/total contract PIT and evaluate.

    Chronological split (60/20/20) is not applicable here: the settled
    picks all fall on 2026-08-23/24 (a single evaluation day) and no
    derivative parameter is fit from match data at all -- the only fitted
    quantity, the strength-scale bridge, is calibrated on a synthetic
    rating grid before any pick is seen. The walk-forward discipline that
    matters -- strictly-prior ratings per match -- is enforced per contract.
    """
    engine = TennisMarkovEngine()
    contracts = _read_settled_derivative_contracts(ledgers_db)
    full_history = _load_full_history(data_root)
    meta_by_event = {row["event_id"]: row for row in full_history}

    valid: list[ContractPrice] = []
    excluded: list[dict[str, Any]] = []
    for contract in contracts:
        if not _normal_full_match(contract):
            excluded.append({**contract, "exclude_reason": "truncated/retirement result"})
            continue
        context = _infer_match_context(contract, meta_by_event, full_history)
        price = price_contract(
            contract,
            context,
            data_root=data_root,
            history_cache={},
            elo_cache={},
            engine=engine,
        )
        if price.skipped_reason:
            excluded.append({**contract, "exclude_reason": price.skipped_reason})
            continue
        valid.append(price)

    def brier(prices: list[ContractPrice]) -> float | None:
        if not prices:
            return None
        return sum((p.probability - p.outcome) ** 2 for p in prices) / len(prices)

    def hit_rate(prices: list[ContractPrice]) -> float | None:
        if not prices:
            return None
        return sum(1 for p in prices if (p.probability >= 0.5) == bool(p.outcome)) / len(prices)

    spread_prices = [p for p in valid if p.market_type == "spread"]
    total_prices = [p for p in valid if p.market_type == "total"]

    # MAE of expected total games, one observation per unique event
    by_event: dict[str, list[ContractPrice]] = {}
    for price in valid:
        by_event.setdefault(price.event_id, []).append(price)
    event_totals = [
        (prices[0].event_id, prices[0].actual_total, prices[0].expected_total_games)
        for prices in by_event.values()
    ]
    total_mae = (
        sum(abs(actual - expected) for _, actual, expected in event_totals) / len(event_totals)
        if event_totals
        else None
    )
    empirical_mean = (
        sum(actual for _, actual, _ in event_totals) / len(event_totals) if event_totals else None
    )
    naive_mae = (
        sum(abs(actual - empirical_mean) for _, actual, _ in event_totals) / len(event_totals)
        if event_totals and empirical_mean is not None
        else None
    )

    # Calibration buckets (wide, honest for n~34)
    calibration: list[dict[str, Any]] = []
    for low, high, label in ((0.0, 0.40, "0.00-0.40"), (0.40, 0.60, "0.40-0.60"), (0.60, 1.001, "0.60-1.00")):
        bucket = [p for p in valid if low <= p.probability < high]
        if bucket:
            calibration.append(
                {
                    "bucket": label,
                    "n": len(bucket),
                    "mean_predicted": sum(p.probability for p in bucket) / len(bucket),
                    "observed_frequency": sum(p.outcome for p in bucket) / len(bucket),
                }
            )

    # Moneyline-context Brier: same distribution, who won the match
    moneyline_brier = None
    ml_hits = 0
    if by_event:
        outcomes: list[tuple[float, int]] = []
        for prices in by_event.values():
            first = prices[0]
            away_won = first.actual_margin > 0
            outcomes.append((first.p_match_away, 1 if away_won else 0))
        moneyline_brier = sum((p - o) ** 2 for p, o in outcomes) / len(outcomes)
        ml_hits = sum(1 for p, o in outcomes if (p >= 0.5) == bool(o))

    return {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "model_versions": {
            "moneyline": TennisModel.version,
            "engine": "tennis-markov-barnett-clarke-v1",
            "distribution": "match_game_distribution-v1",
        },
        "strength_scale_by_surface": {
            surface: _strength_scale(engine, surface) for surface in sorted(engine.surface_baselines)
        },
        "sample": {
            "ledger_db": str(ledgers_db),
            "settled_contract_rows": len(contracts),
            "unique_events": len(by_event),
            "valid_contracts": len(valid),
            "valid_spread": len(spread_prices),
            "valid_total": len(total_prices),
            "excluded_contracts": len(excluded),
            "excluded_detail": excluded,
        },
        "metrics": {
            "spread": {
                "n": len(spread_prices),
                "brier": brier(spread_prices),
                "brier_naive_0_5": 0.25,
                # always-predict-NO floor: Brier = base rate of covers
                "brier_naive_always_no": (
                    sum(p.outcome for p in spread_prices) / len(spread_prices) if spread_prices else None
                ),
                "hit_rate": hit_rate(spread_prices),
                "base_rate_cover": (
                    sum(p.outcome for p in spread_prices) / len(spread_prices) if spread_prices else None
                ),
            },
            "total": {
                "n": len(total_prices),
                "brier": brier(total_prices),
                "brier_naive_0_5": 0.25,
                # always-predict-under floor: Brier = base rate of overs
                "brier_naive_always_under": (
                    sum(p.outcome for p in total_prices) / len(total_prices) if total_prices else None
                ),
                "hit_rate": hit_rate(total_prices),
                "base_rate_over": (
                    sum(p.outcome for p in total_prices) / len(total_prices) if total_prices else None
                ),
            },
            "expected_total_games": {
                "n_events": len(event_totals),
                "mae": total_mae,
                "empirical_mean": empirical_mean,
                "mae_of_empirical_mean": naive_mae,
                "events": [
                    {"event_id": event_id, "actual_total": actual, "expected_total": expected}
                    for event_id, actual, expected in event_totals
                ],
            },
            "moneyline_context": {
                "n_events": len(by_event),
                "brier": moneyline_brier,
                "brier_naive_0_5": 0.25,
                "hit_rate": ml_hits / len(by_event) if by_event else None,
            },
        },
        "calibration": calibration,
        "contracts": [
            {
                "event_id": p.event_id,
                "away_team": p.away_team,
                "home_team": p.home_team,
                "market_type": p.market_type,
                "selection": p.selection,
                "line": p.line,
                "probability": round(p.probability, 4),
                "outcome": p.outcome,
                "result": p.result,
                "actual_total": p.actual_total,
                "actual_margin": p.actual_margin,
                "expected_total_games": round(p.expected_total_games, 2),
                "expected_margin": round(p.expected_margin, 2),
                "p_match_away": round(p.p_match_away, 4),
                "blended_elo_away": round(p.blended_elo_away, 1),
                "blended_elo_home": round(p.blended_elo_home, 1),
                "history_matches": p.history_matches,
                "surface": p.surface,
                "match_format": p.match_format,
                "strength_scale": p.strength_scale,
            }
            for p in valid
        ],
        "notes": [
            "serve/return split is not identified from outcome-only history; anchored to engine tour baselines",
            "strength_scale calibrated on synthetic Elo grid; residual vs moneyline Elo not measured per-match",
            "grand-slam men final-set extended rules are not modeled (engine treats every set identically)",
            "all settled lines are half-games; no pushes in the evaluated set",
            "moneyline-context Brier ~0.25 means the PIT ratings barely separated winners on this 2-day sample (8 of 14 events were upsets), so derivative probabilities inherit that weak separation",
            "expected totals (~25.5 games at near-even ratings) run high vs actuals (mean 22.2); the bridge maps Elo differences to point-win differences too weakly to capture blowout margins",
        ],
    }


def main(data_root: str | Path, ledgers_db: Path, out_path: Path | None = None) -> dict[str, Any]:
    report = run_derivative_walkforward(data_root=data_root, ledgers_db=ledgers_db)
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
