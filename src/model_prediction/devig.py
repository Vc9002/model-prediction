"""Remove the bookmaker margin from a two-way (or n-way) set of implied probabilities.

Why this exists (quick-261120-npc): the NBA v10 plan gates on closing-line value against the
devigged sportsbook consensus, and track T-B compares that consensus with the Polymarket ask.
Both need a fair probability per side, and the method has to be stated, because the two common
methods disagree on lopsided markets.

Inputs are implied probabilities, never sportsbook prices: callers convert a quoted price to a
probability the moment they read it (see ``data_sources/nba_pretip_capture.py``).

Methods
- ``multiplicative`` (the default; also called proportional or basic normalisation): each side's
  implied probability is divided by the book's total, ``p_i = q_i / sum(q)``. It spreads the margin
  in proportion to each side's price. It is the house convention (``pricing.normalize_no_vig``,
  ``espn_nfl_odds.devig_two_sided``), so consensus numbers stay comparable with existing NFL rows.
- ``power``: find ``k >= 1`` with ``sum(q_i ** k) = 1`` and return ``q_i ** k``. It moves more of
  the margin onto the longshot, which matches the favourite-longshot bias better on heavy
  moneyline favourites. Kept so a later analysis can re-derive either view from the stored raw pair.

On a near-even spread or total the two methods agree within about 0.1 percentage point (identical
when both sides carry the same price). On a heavy moneyline favourite they diverge by 2 points or
more: DraftKings' 2026-11-21 MIL @ PHI moneyline (86.7% / 17.4% implied) devigs to 83.3% for
Philadelphia multiplicatively and 85.4% with the power method.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from .pricing import normalize_no_vig

DEVIG_METHODS = ("multiplicative", "power")
DEFAULT_DEVIG_METHOD = "multiplicative"

_POWER_ITERATIONS = 200
_POWER_TOLERANCE = 1e-12


def overround(implied: Sequence[float]) -> float:
    """The book's margin: ``sum(q) - 1`` (0.045 means the two sides add up to 104.5%)."""
    return float(sum(implied)) - 1.0


def _valid(implied: Sequence[float]) -> bool:
    return len(implied) >= 2 and all(
        isinstance(q, int | float) and math.isfinite(q) and 0.0 < q < 1.0 for q in implied
    )


def devig_power(implied: Sequence[float]) -> tuple[float, ...]:
    """Power-method fair probabilities; raises ``ValueError`` on an invalid input set.

    ``sum(q ** k)`` falls monotonically in ``k`` for 0 < q < 1, so bisection on ``k`` converges. A
    book priced under 100% (negative margin, e.g. a stale side) needs ``k < 1``; the same bisection
    covers it.
    """
    if not _valid(implied):
        raise ValueError("power devig requires at least two probabilities in (0, 1)")
    low, high = 1e-6, 1.0
    while sum(q**high for q in implied) > 1.0:
        high *= 2.0
        if high > 1e6:
            raise ValueError("power devig did not bracket a solution")
    for _ in range(_POWER_ITERATIONS):
        mid = (low + high) / 2.0
        total = sum(q**mid for q in implied)
        if abs(total - 1.0) < _POWER_TOLERANCE:
            break
        if total > 1.0:
            low = mid
        else:
            high = mid
    k = (low + high) / 2.0
    fair = [q**k for q in implied]
    scale = sum(fair)
    return tuple(p / scale for p in fair)


def devig(implied: Sequence[float | None], method: str = DEFAULT_DEVIG_METHOD) -> tuple[float, ...] | None:
    """Fair probabilities for every side, or None when any side is missing or out of range (fail closed)."""
    if method not in DEVIG_METHODS:
        raise ValueError(f"unknown devig method {method!r}; expected one of {DEVIG_METHODS}")
    if any(q is None for q in implied):
        return None
    values = [float(q) for q in implied if q is not None]
    if not _valid(values):
        return None
    try:
        return tuple(normalize_no_vig(values)) if method == "multiplicative" else devig_power(values)
    except ValueError:
        return None


__all__ = ["DEFAULT_DEVIG_METHOD", "DEVIG_METHODS", "devig", "devig_power", "overround"]
