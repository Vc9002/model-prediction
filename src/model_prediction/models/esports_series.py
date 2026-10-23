"""Pure research distribution for ordinary first-to-N esports series.

Only explicit Bo1/Bo3/Bo5 without draws, map advantages, forfeits or voids are
supported. Ordered map outcomes are independent conditional on each supplied
scenario. A mixture can represent shared scenario uncertainty; neither the map
probabilities nor scenario weights are estimated here. Exact means enumerating
this mathematical model, not calibrated or validated real-world predictions.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import fsum, isclose, isfinite
from numbers import Real
from types import MappingProxyType
from typing import Literal


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real) or not isfinite(value):
        raise ValueError(f"{name} must be a finite real number")
    return float(value)


def _format(best_of: int) -> int:
    if type(best_of) is not int or best_of not in (1, 3, 5):
        raise ValueError("best_of must explicitly be integer 1, 3 or 5")
    return best_of // 2 + 1


def _map_probabilities(best_of: int, values: Iterable[float]) -> tuple[float, ...]:
    probabilities = tuple(_finite_number(value, "map probability") for value in values)
    if len(probabilities) != best_of:
        raise ValueError(
            "supply exactly best_of ordered map probabilities, including potential unplayed maps"
        )
    if any(not 0 <= p <= 1 for p in probabilities):
        raise ValueError("map probabilities must lie in [0, 1]")
    return probabilities


@dataclass(frozen=True)
class MapScenario:
    """An explicitly weighted ordered map sequence; no default veto or weights.

    Validation happens in ``mixture_series_distribution`` against its format.
    The input sequence is copied to prevent later caller mutation.
    """

    weight: float
    map_win_probabilities: tuple[float, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "map_win_probabilities", tuple(self.map_win_probabilities))


@dataclass(frozen=True)
class OutcomeProbabilities:
    """Unconditional win/loss/push mass; push is never redistributed.

    Push means equality with the supplied mathematical threshold. Whether a
    real contract refunds, voids, or settles otherwise requires external rules.
    """

    win: float
    loss: float
    push: float


@dataclass(frozen=True)
class SeriesDistribution:
    """Immutable terminal (A maps won, B maps won) probability measure."""

    best_of: int
    terminal_scores: Mapping[tuple[int, int], float]

    def __post_init__(self) -> None:
        target = _format(self.best_of)
        expected = {(target, i) for i in range(target)} | {(i, target) for i in range(target)}
        scores = dict(self.terminal_scores)
        if set(scores) != expected:
            raise ValueError("terminal scores must contain every and only legal terminal score")
        scores = {score: _finite_number(mass, "terminal probability") for score, mass in scores.items()}
        if any(not 0 <= mass <= 1 for mass in scores.values()):
            raise ValueError("terminal probabilities must lie in [0, 1]")
        total = fsum(scores.values())
        if not isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("terminal probabilities must sum to one")
        # Remove only accumulated floating-point summation error.
        object.__setattr__(
            self, "terminal_scores", MappingProxyType({s: p / total for s, p in scores.items()})
        )

    @property
    def moneyline_a(self) -> float:
        return fsum(p for (a, b), p in self.terminal_scores.items() if a > b)

    @property
    def moneyline_b(self) -> float:
        return fsum(p for (a, b), p in self.terminal_scores.items() if b > a)

    def _threshold(self, margins: Mapping[tuple[int, int], float]) -> OutcomeProbabilities:
        return OutcomeProbabilities(
            win=fsum(p for score, p in self.terminal_scores.items() if margins[score] > 0),
            loss=fsum(p for score, p in self.terminal_scores.items() if margins[score] < 0),
            push=fsum(p for score, p in self.terminal_scores.items() if margins[score] == 0),
        )

    def map_handicap(self, line: float, *, team: Literal["a", "b"]) -> OutcomeProbabilities:
        """Selection wins iff selected team's map margin plus its signed line > 0.

        E.g. A -1.5 requires A to win by at least two maps. This describes map
        counts, never round counts. Caller must independently bind real markets.
        """
        line = _finite_number(line, "handicap line")
        if team not in ("a", "b"):
            raise ValueError("team must be 'a' or 'b'")
        sign = 1 if team == "a" else -1
        return self._threshold({(a, b): sign * (a - b) + line for a, b in self.terminal_scores})

    def total_maps(self, line: float, *, side: Literal["over", "under"]) -> OutcomeProbabilities:
        """Over wins iff total maps > line; under wins iff total maps < line."""
        line = _finite_number(line, "total line")
        if side not in ("over", "under"):
            raise ValueError("side must be 'over' or 'under'")
        sign = 1 if side == "over" else -1
        return self._threshold({(a, b): sign * (a + b - line) for a, b in self.terminal_scores})


def series_distribution(best_of: int, map_win_probabilities: Iterable[float]) -> SeriesDistribution:
    """Propagate ordered conditional A-map win probabilities until a team wins.

    Supply every potentially played map in order, even if earlier maps can end
    the series. Later probabilities never affect paths that already terminated.
    No series-to-map probability inversion, default format, map or veto occurs.
    """
    target = _format(best_of)
    probabilities = _map_probabilities(best_of, map_win_probabilities)
    terminal = {(target, i): 0.0 for i in range(target)} | {(i, target): 0.0 for i in range(target)}
    active = {(0, 0): 1.0}
    for p in probabilities:
        next_active: dict[tuple[int, int], float] = {}
        for (a, b), mass in active.items():
            for score, transition in (((a + 1, b), p), ((a, b + 1), 1 - p)):
                destination = terminal if target in score else next_active
                destination[score] = destination.get(score, 0.0) + mass * transition
        active = next_active
    return SeriesDistribution(best_of, terminal)


def mixture_series_distribution(best_of: int, scenarios: Iterable[MapScenario]) -> SeriesDistribution:
    """Integrate explicit map/veto scenarios with nonnegative weights summing to one.

    Weights must already be normalized (absolute tolerance 1e-12). Unnormalized
    weights are rejected rather than silently treated as learned probabilities.
    All scenarios, including zero-weight ones, must have valid inputs. Correlation
    conditional on a scenario is not modeled; unconditional dependence can arise
    from shared scenario uncertainty.
    """
    _format(best_of)
    scenarios = tuple(scenarios)
    if not scenarios:
        raise ValueError("at least one explicitly weighted scenario is required")
    weights = tuple(_finite_number(s.weight, "scenario weight") for s in scenarios)
    total_weight = fsum(weights)
    if any(w < 0 for w in weights) or not isclose(total_weight, 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("scenario weights must be nonnegative and sum to one")
    weights = tuple(weight / total_weight for weight in weights)
    components = [series_distribution(best_of, s.map_win_probabilities) for s in scenarios]
    terminal = {
        score: fsum(
            weight * component.terminal_scores[score] for weight, component in zip(weights, components)
        )
        for score in components[0].terminal_scores
    }
    return SeriesDistribution(best_of, terminal)
