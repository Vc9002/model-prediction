"""Cross-Market Internal Consistency Engine.

Validates probabilistic coherence across interrelated betting markets:
1. Monotonicity: P(Cover -1.5) <= P(Moneyline Win).
2. Complementarity: P(Over) + P(Under) == 1.0 (after no-vig normalization).
3. Arbitrage & Dutching Bounds: Detects synthetic mispricings or unphysical inversions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ConsistencyReport:
    is_consistent: bool
    violations: list[str]
    implied_no_vig_margin: float | None = None
    synthetic_edge: float | None = None


def check_cross_market_consistency(
    *,
    moneyline_home_prob: float | None = None,
    moneyline_away_prob: float | None = None,
    spread_home_minus_1_5_prob: float | None = None,
    spread_away_plus_1_5_prob: float | None = None,
    spread_away_minus_1_5_prob: float | None = None,
    spread_home_plus_1_5_prob: float | None = None,
    total_over_prob: float | None = None,
    total_under_prob: float | None = None,
    tolerance: float = 0.005,
) -> ConsistencyReport:
    """Validate that joint market probabilities satisfy physical and mathematical bounds."""
    violations: list[str] = []

    # 1. Moneyline coherence
    if moneyline_home_prob is not None and moneyline_away_prob is not None:
        ml_sum = moneyline_home_prob + moneyline_away_prob
        if abs(ml_sum - 1.0) > tolerance:
            violations.append(f"Moneyline probabilities sum to {ml_sum:.4f} != 1.0")

    # 2. Total coherence
    if total_over_prob is not None and total_under_prob is not None:
        total_sum = total_over_prob + total_under_prob
        if abs(total_sum - 1.0) > tolerance:
            violations.append(f"Total over/under probabilities sum to {total_sum:.4f} != 1.0")

    # 3. Spread vs Moneyline Monotonicity (Home -1.5 / Away +1.5)
    # A team cannot cover -1.5 without winning the game
    if (
        moneyline_home_prob is not None
        and spread_home_minus_1_5_prob is not None
        and spread_home_minus_1_5_prob > moneyline_home_prob + tolerance
    ):
        violations.append(
            f"Monotonicity inversion: P(Home -1.5)={spread_home_minus_1_5_prob:.4f} > P(Home Win)={moneyline_home_prob:.4f}"
        )

    # A team that wins the game always covers +1.5
    if (
        moneyline_away_prob is not None
        and spread_away_plus_1_5_prob is not None
        and moneyline_away_prob > spread_away_plus_1_5_prob + tolerance
    ):
        violations.append(
            f"Monotonicity inversion: P(Away Win)={moneyline_away_prob:.4f} > P(Away +1.5)={spread_away_plus_1_5_prob:.4f}"
        )

    # 4. Spread vs Moneyline Monotonicity (Away -1.5 / Home +1.5)
    # Away team cannot cover -1.5 without winning
    if (
        moneyline_away_prob is not None
        and spread_away_minus_1_5_prob is not None
        and spread_away_minus_1_5_prob > moneyline_away_prob + tolerance
    ):
        violations.append(
            f"Monotonicity inversion: P(Away -1.5)={spread_away_minus_1_5_prob:.4f} > P(Away Win)={moneyline_away_prob:.4f}"
        )

    # Home team that wins always covers +1.5
    if (
        moneyline_home_prob is not None
        and spread_home_plus_1_5_prob is not None
        and moneyline_home_prob > spread_home_plus_1_5_prob + tolerance
    ):
        violations.append(
            f"Monotonicity inversion: P(Home Win)={moneyline_home_prob:.4f} > P(Home +1.5)={spread_home_plus_1_5_prob:.4f}"
        )

    return ConsistencyReport(
        is_consistent=len(violations) == 0,
        violations=violations,
    )


def calculate_dutching_arbitrage(decimal_odds: list[float]) -> dict[str, Any]:
    """Calculate dutching arbitrage margins and guaranteed ROI across independent books.

    Parameters
    ----------
    decimal_odds : list[float]
        Best available decimal odds for each exhaustive mutually exclusive outcome.

    Returns
    -------
    dict with 'implied_margin', 'is_arbitrage', 'arbitrage_roi_pct', 'optimal_stakes'.
    """
    if not decimal_odds or any(o <= 1.0 for o in decimal_odds):
        return {"implied_margin": 1.0, "is_arbitrage": False, "arbitrage_roi_pct": 0.0}

    inv_odds = [1.0 / o for o in decimal_odds]
    total_implied = sum(inv_odds)
    is_arb = total_implied < 1.0
    roi = ((1.0 / total_implied) - 1.0) * 100.0 if total_implied > 0 else 0.0
    optimal_stakes = [inv / total_implied for inv in inv_odds]

    return {
        "implied_margin": round(total_implied, 4),
        "is_arbitrage": is_arb,
        "arbitrage_roi_pct": round(roi, 3) if is_arb else 0.0,
        "optimal_stakes": [round(s, 4) for s in optimal_stakes],
    }


def validate_cross_market_pick_coherence(
    candidate_market_type: str,
    candidate_selection: str,
    candidate_line: float | None,
    existing_picks_for_event: list[Any],
    small_spread_threshold: float = 3.0,
) -> tuple[bool, str | None]:
    """Validate that a candidate pick does not conflict with existing picks on the same event.

    Coherence Rules:
    1. Direct Monotonicity Contradiction:
       - Betting Team A -X (where X >= 0.5) while already having a pick on Team B Moneyline
         is a physical impossibility (Team A cannot cover a negative spread if Team B wins).
       - Conversely, candidate Team B Moneyline contradicts an existing Team A -X spread pick.
    2. Small Spread Anti-Cannibalization (|line| <= small_spread_threshold):
       - On near-pick'em spreads (e.g. |line| <= 3.0), picking Team A on Moneyline and Team B on Spread
         creates an unphysical self-cannibalizing position where only an improbable exact 1 or 2 point margin
         could hit both sides.
    """
    cand_mkt = str(getattr(candidate_market_type, "value", candidate_market_type) or "").lower()
    cand_sel = str(candidate_selection).lower()

    for pick in existing_picks_for_event:
        decision = pick.get("decision") if isinstance(pick, dict) else getattr(pick, "decision", None)
        if decision != "CALL":
            continue
        status = pick.get("status") if isinstance(pick, dict) else getattr(pick, "status", None)
        if status is not None and str(status).lower() not in ("open", ""):
            continue

        raw_mkt = pick.get("market_type") if isinstance(pick, dict) else getattr(pick, "market_type", "")
        ex_mkt = str(getattr(raw_mkt, "value", raw_mkt) or "").lower()
        ex_sel = str(
            (pick.get("selection") if isinstance(pick, dict) else getattr(pick, "selection", "")) or ""
        ).lower()
        ex_line = pick.get("line") if isinstance(pick, dict) else getattr(pick, "line", None)
        try:
            ex_line_val = float(ex_line) if ex_line is not None else None
        except (ValueError, TypeError):
            ex_line_val = None

        cand_line_val = None
        try:
            cand_line_val = float(candidate_line) if candidate_line is not None else None
        except (ValueError, TypeError):
            pass

        # Case A: Candidate is SPREAD, existing is MONEYLINE
        if cand_mkt == "spread" and ex_mkt == "moneyline" and cand_sel != ex_sel:
            if cand_line_val is not None and cand_line_val < 0:
                msg = (
                    f"CROSS_MARKET_CONTRADICTION: Candidate {cand_sel} spread ({cand_line_val:+g}) "
                    f"is impossible if existing {ex_sel} Moneyline pick wins"
                )
                return False, msg
            if cand_line_val is not None and abs(cand_line_val) <= small_spread_threshold:
                msg = (
                    f"CROSS_MARKET_CONFLICT: Candidate {cand_sel} spread ({cand_line_val:+g}) "
                    f"conflicts with existing {ex_sel} Moneyline pick on small line (|line| <= {small_spread_threshold})"
                )
                return False, msg

        # Case B: Candidate is MONEYLINE, existing is SPREAD
        if cand_mkt == "moneyline" and ex_mkt == "spread" and cand_sel != ex_sel:
            if ex_line_val is not None and ex_line_val < 0:
                msg = (
                    f"CROSS_MARKET_CONTRADICTION: Candidate {cand_sel} Moneyline contradicts "
                    f"existing {ex_sel} spread ({ex_line_val:+g})"
                )
                return False, msg
            if ex_line_val is not None and abs(ex_line_val) <= small_spread_threshold:
                msg = (
                    f"CROSS_MARKET_CONFLICT: Candidate {cand_sel} Moneyline conflicts "
                    f"with existing {ex_sel} spread ({ex_line_val:+g}) on small line (|line| <= {small_spread_threshold})"
                )
                return False, msg

    return True, None
