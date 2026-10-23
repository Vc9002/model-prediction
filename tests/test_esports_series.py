"""Independent checks of the research series engine's probability contract."""

import math
from itertools import product

import pytest

from model_prediction.models.esports_series import (
    MapScenario,
    SeriesDistribution,
    mixture_series_distribution,
    series_distribution,
)


@pytest.mark.parametrize("best_of", [1, 3, 5])
@pytest.mark.parametrize("p", [0.0, 0.13, 0.5, 0.79, 1.0])
def test_identical_maps_match_negative_binomial_terminal_closed_form(best_of, p):
    result = series_distribution(best_of, [p] * best_of)
    target = best_of // 2 + 1
    expected = {}
    for losses in range(target):
        paths = math.comb(target + losses - 1, losses)
        expected[target, losses] = paths * p**target * (1 - p) ** losses
        expected[losses, target] = paths * (1 - p) ** target * p**losses
    assert dict(result.terminal_scores) == pytest.approx(expected)
    assert sum(result.terminal_scores.values()) == pytest.approx(1.0)
    assert result.moneyline_a + result.moneyline_b == pytest.approx(1.0)
    closed_form = {1: p, 3: p**2 * (3 - 2 * p), 5: p**3 * (10 - 15 * p + 6 * p**2)}
    assert result.moneyline_a == pytest.approx(closed_form[best_of])


def test_heterogeneous_ordered_maps_have_hand_calculated_terminal_scores():
    result = series_distribution(3, [0.8, 0.3, 0.6])
    assert dict(result.terminal_scores) == pytest.approx(
        {(2, 0): 0.24, (0, 2): 0.14, (2, 1): 0.372, (1, 2): 0.248}
    )
    assert result.moneyline_a == pytest.approx(0.612)


def test_bo5_matches_exhaustive_full_length_paths_with_early_stopping():
    probabilities = [0.13, 0.74, 0.28, 0.91, 0.61]
    expected = {(3, i): 0.0 for i in range(3)} | {(i, 3): 0.0 for i in range(3)}
    for path in product([False, True], repeat=5):
        probability = math.prod(p if won else 1 - p for won, p in zip(path, probabilities))
        a = b = 0
        for won in path:
            a += won
            b += not won
            if a == 3 or b == 3:
                expected[a, b] += probability
                break
    assert dict(series_distribution(5, probabilities).terminal_scores) == pytest.approx(expected)


@pytest.mark.parametrize("best_of,ps", [(1, [0.7]), (3, [0.8, 0.3, 0.6]), (5, [0.1, 0.7, 0.8, 0.3, 0.4])])
def test_swap_and_selection_complements(best_of, ps):
    original = series_distribution(best_of, ps)
    swapped = series_distribution(best_of, [1 - p for p in ps])
    assert original.moneyline_a == pytest.approx(swapped.moneyline_b)
    for (a, b), mass in original.terminal_scores.items():
        assert mass == pytest.approx(swapped.terminal_scores[b, a])
    for line in [-3, -1.5, -1, 0, 1, 1.5, 3]:
        a = original.map_handicap(line, team="a")
        b = original.map_handicap(-line, team="b")
        mirrored = swapped.map_handicap(line, team="b")
        assert (a.win, a.loss, a.push) == pytest.approx((b.loss, b.win, b.push))
        assert (a.win, a.loss, a.push) == pytest.approx((mirrored.win, mirrored.loss, mirrored.push))
        assert a.win + a.loss + a.push == pytest.approx(1)
    for line in [1, 2, 2.5, 3, 4.5, 5]:
        over = original.total_maps(line, side="over")
        under = original.total_maps(line, side="under")
        assert (over.win, over.loss, over.push) == pytest.approx((under.loss, under.win, under.push))
        assert over.win + over.loss + over.push == pytest.approx(1)


def test_integer_thresholds_preserve_push_mass_without_renormalizing():
    result = series_distribution(3, [0.8, 0.3, 0.6])
    spread = result.map_handicap(-1, team="a")
    assert (spread.win, spread.loss, spread.push) == pytest.approx((0.24, 0.388, 0.372))
    total = result.total_maps(2, side="over")
    assert (total.win, total.loss, total.push) == pytest.approx((0.62, 0, 0.38))


def test_explicit_scenario_mixture_agrees_with_weighted_terminal_and_market_probabilities():
    scenarios = [MapScenario(0.25, [0.8, 0.3, 0.6]), MapScenario(0.75, [0.2, 0.7, 0.4])]
    mixed = mixture_series_distribution(3, scenarios)
    components = [series_distribution(3, scenario.map_win_probabilities) for scenario in scenarios]
    for score, mass in mixed.terminal_scores.items():
        assert mass == pytest.approx(
            sum(s.weight * c.terminal_scores[score] for s, c in zip(scenarios, components))
        )
    assert mixed.moneyline_a == pytest.approx(
        0.25 * components[0].moneyline_a + 0.75 * components[1].moneyline_a
    )
    spread = mixed.map_handicap(-1, team="a")
    total = mixed.total_maps(2, side="over")
    for field in ["win", "loss", "push"]:
        assert getattr(spread, field) == pytest.approx(
            sum(
                s.weight * getattr(c.map_handicap(-1, team="a"), field) for s, c in zip(scenarios, components)
            )
        )
        assert getattr(total, field) == pytest.approx(
            sum(
                s.weight * getattr(c.total_maps(2, side="over"), field) for s, c in zip(scenarios, components)
            )
        )


@pytest.mark.parametrize("best_of", [None, 0, 2, 4, 7, "3", 3.0, True])
def test_unknown_or_unaudited_formats_reject(best_of):
    with pytest.raises(ValueError):
        series_distribution(best_of, [0.5] * 3)


@pytest.mark.parametrize(
    "ps",
    [
        [],
        [0.5],
        [0.5] * 2,
        [0.5] * 4,
        [0.5, float("nan"), 0.5],
        [0.5, float("inf"), 0.5],
        [0.5, -0.1, 0.5],
        [0.5, 1.1, 0.5],
        [0.5, True, 0.5],
        [0.5, "0.2", 0.5],
    ],
)
def test_invalid_or_missing_map_probabilities_reject(ps):
    with pytest.raises(ValueError):
        series_distribution(3, ps)


@pytest.mark.parametrize(
    "weights", [[], [0.0], [0.2, 0.2], [2.0], [-0.2, 1.2], [float("nan")], [float("inf")], [True], ["1"]]
)
def test_missing_invalid_or_unnormalized_scenario_weights_reject(weights):
    with pytest.raises(ValueError):
        mixture_series_distribution(3, [MapScenario(weight, [0.5] * 3) for weight in weights])


def test_zero_weight_does_not_hide_invalid_scenario():
    with pytest.raises(ValueError):
        mixture_series_distribution(3, [MapScenario(1, [0.5] * 3), MapScenario(0, [float("nan")] * 3)])


def test_scenario_weight_tolerance_only_removes_roundoff():
    result = mixture_series_distribution(1, [MapScenario(1 + 5e-13, [1.0])])
    assert result.moneyline_a == 1.0
    assert result.moneyline_b == 0.0


@pytest.mark.parametrize("line", [float("nan"), float("inf"), -float("inf"), True, "2.5"])
def test_invalid_lines_reject(line):
    result = series_distribution(3, [0.5] * 3)
    with pytest.raises(ValueError):
        result.map_handicap(line, team="a")
    with pytest.raises(ValueError):
        result.total_maps(line, side="over")


def test_unknown_selection_rejects_and_scores_are_immutable():
    result = series_distribution(1, [0.7])
    with pytest.raises(ValueError):
        result.map_handicap(1, team="unknown")
    with pytest.raises(ValueError):
        result.total_maps(1, side="unknown")
    with pytest.raises(TypeError):
        result.terminal_scores[1, 0] = 0.2


@pytest.mark.parametrize(
    "scores",
    [
        {(1, 0): 0.7},
        {(1, 0): 0.7, (0, 1): 0.2},
        {(1, 0): -0.2, (0, 1): 1.2},
        {(1, 0): float("nan"), (0, 1): 0.3},
        {(1, 0): 0.7, (0, 1): 0.3, (0, 0): 0.0},
    ],
)
def test_public_distribution_rejects_invalid_terminal_measure(scores):
    with pytest.raises(ValueError):
        SeriesDistribution(1, scores)


def test_inputs_are_copied_and_terminal_paths_ignore_unplayed_map_probability():
    maps = [1.0, 1.0, 0.1]
    scenario = MapScenario(1.0, maps)
    maps[0] = 0.0
    result = mixture_series_distribution(3, [scenario])
    assert result.terminal_scores[2, 0] == 1.0
    assert dict(result.terminal_scores) == dict(series_distribution(3, [1.0, 1.0, 0.9]).terminal_scores)
    scores = {(1, 0): 0.7, (0, 1): 0.3}
    copied = SeriesDistribution(1, scores)
    scores[1, 0] = 0
    assert copied.moneyline_a == 0.7
