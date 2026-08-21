from __future__ import annotations

from .domain import MarketType, PickResult


def american_to_decimal(american_odds: int) -> float:
    if -99 <= american_odds <= 99:
        raise ValueError("American odds must be <= -100 or >= +100")
    return 1 + (american_odds / 100 if american_odds > 0 else 100 / abs(american_odds))


def implied_probability(american_odds: int) -> float:
    return 1 / american_to_decimal(american_odds)


def probability_to_american(p: float) -> int:
    if not (0.0 < p < 1.0):
        raise ValueError("Probability must be in (0, 1)")
    if p >= 0.5:
        return round(-100.0 * (p / (1.0 - p)))
    return round(100.0 * ((1.0 - p) / p))


def normalize_no_vig(raw_probabilities: list[float] | tuple[float, ...]) -> tuple[float, ...]:
    if len(raw_probabilities) < 2 or any(
        probability <= 0 or probability >= 1 for probability in raw_probabilities
    ):
        raise ValueError("no-vig normalization requires at least two probabilities in (0, 1)")
    total = sum(raw_probabilities)
    if total <= 0:
        raise ValueError("implied-probability sum must be positive")
    return tuple(probability / total for probability in raw_probabilities)


def grade_pick(
    market_type: MarketType,
    selection: str,
    line: float | None,
    away_score: int,
    home_score: int,
    league: str | None = None,
) -> PickResult:
    selection = selection.lower()
    if market_type is MarketType.MONEYLINE:
        if selection == "draw":
            # A draw pick is a binary yes/no on the scores tying, not a
            # margin bet — there is no push case: either it ties or it
            # doesn't. Without this branch a "draw" selection silently fell
            # into the "not home" -> away-margin case above and got graded
            # as a straight away-moneyline bet, which is a different market.
            return PickResult.WIN if home_score == away_score else PickResult.LOSS
        if league == "SOCCER" and home_score == away_score:
            # Soccer's Polymarket win market is not one 2-outcome market
            # with a tie -- it's three independent Yes/No contracts (home
            # wins / draw / away wins). A "home" or "away" pick is a bet on
            # that contract resolving YES; a draw resolves it NO, a full
            # loss of stake, not a refunded push (unlike KBO/NPB, which
            # really are 2-outcome markets settled 50/50 on a tie -- see
            # ledger.py's binary_contract_settlement_value handling).
            return PickResult.LOSS
        margin: float = home_score - away_score if selection == "home" else away_score - home_score
    elif market_type is MarketType.SPREAD:
        if line is None:
            raise ValueError("spread requires a line")
        raw_margin = home_score - away_score if selection == "home" else away_score - home_score
        margin = raw_margin + line
    elif market_type is MarketType.BTTS:
        # Binary yes/no on both teams scoring -- no push case, like
        # moneyline's "draw" sub-case above.
        both_scored = away_score > 0 and home_score > 0
        return PickResult.WIN if (both_scored == (selection == "yes")) else PickResult.LOSS
    else:
        if line is None:
            raise ValueError("total requires a line")
        total = away_score + home_score
        margin = total - line if selection == "over" else line - total
    if margin > 0:
        return PickResult.WIN
    if margin < 0:
        return PickResult.LOSS
    return PickResult.PUSH


def profit_units(result: PickResult, units: float, decimal_odds: float) -> float:
    if result is PickResult.WIN:
        return units * (decimal_odds - 1)
    if result is PickResult.LOSS:
        return -units
    return 0.0
