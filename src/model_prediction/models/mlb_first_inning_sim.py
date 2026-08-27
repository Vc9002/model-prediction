"""First-inning plate-appearance simulator (the plan's challenger 3).

A deliberately simple generative model: every plate appearance resolves
into K / BB / 1B / 2B / 3B / HR / BIP-out with probabilities modulated by
the game's PIT starter and top-of-order features from the first-inning
ledger, then the inning is simulated until three outs. From one coherent
distribution the same run of draws yields NRFI/YRFI, exact first-inning
runs, and (with more innings) the O/U-0.5 first-inning market — the
philosophy the full-game engine already uses, applied to inning one.

Rates are league priors modulated linearly by the starter's shrunk K% /
BB% and the lineup's shrunk top-of-order composite; the modulation
strengths are fixed constants, not tuned on any holdout. Seeded RNG makes
every prediction reproducible.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .mlb_first_inning import FirstInningGameRow

# League per-PA outcome rates from the snapshot history's league sums,
# then scaled once by LEAGUE_BASE_RATE_MULT so the simulated league-mean
# NRFI rate reproduces the measured league prior (0.5106,
# compute_first_inning_priors) — a one-time calibration to a league
# constant, fixed forever, never tuned on any holdout.
LEAGUE_K_RATE = 0.22
LEAGUE_BB_RATE = 0.08
LEAGUE_HR_RATE = 0.031
LEAGUE_1B_RATE = 0.135
LEAGUE_2B_RATE = 0.042
LEAGUE_3B_RATE = 0.006
LEAGUE_BASE_RATE_MULT = 1.25  # -> sim league p_nrfi 0.5148 vs measured 0.5106

# Modulation strengths: how far a one-unit change in the feature moves
# the log-odds of each outcome. Small, fixed, deliberately conservative.
K_MOD = 1.2  # starter K% (shrunk, league-mean-centered)
BB_MOD = 0.8  # starter BB%
POWER_MOD = 1.5  # top-3 composite (shrunk, league-mean-centered)

DEFAULT_SIMS_PER_GAME = 2000
DEFAULT_SEED = 20260827


@dataclass(frozen=True)
class SimulatedInning:
    """One simulated first inning."""

    runs_away: int
    runs_home: int


def _pa_outcome_probs(
    starter_k_pct: float,
    starter_bb_pct: float,
    top3_composite: float,
) -> list[tuple[str, float]]:
    """Per-PA outcome probabilities for one team-vs-starter matchup.

    K and BB rates are modulated by the starter's shrunk rates; the hit
    rates are modulated together by the lineup composite (a power proxy).
    Probabilities are renormalized so they always sum to one.
    """

    def modulate(base: float, feature: float, league: float, strength: float) -> float:
        return base * max(0.1, 1.0 + strength * (feature - league))

    m = LEAGUE_BASE_RATE_MULT
    k = modulate(LEAGUE_K_RATE, starter_k_pct, LEAGUE_K_RATE, K_MOD)
    bb = modulate(LEAGUE_BB_RATE * m, starter_bb_pct, LEAGUE_BB_RATE, BB_MOD)
    hr = modulate(LEAGUE_HR_RATE * m, top3_composite, 0.32, POWER_MOD)
    s1 = modulate(LEAGUE_1B_RATE * m, top3_composite, 0.32, POWER_MOD * 0.5)
    s2 = modulate(LEAGUE_2B_RATE * m, top3_composite, 0.32, POWER_MOD * 0.5)
    s3 = LEAGUE_3B_RATE * m
    outs = 1.0 - (k + bb + hr + s1 + s2 + s3)
    if outs <= 0.05:
        # Degenerate matchup: fall back to calibrated league rates rather
        # than fabricating negative out probability.
        base = [
            LEAGUE_K_RATE,
            LEAGUE_BB_RATE * m,
            LEAGUE_HR_RATE * m,
            LEAGUE_1B_RATE * m,
            LEAGUE_2B_RATE * m,
            LEAGUE_3B_RATE * m,
        ]
        total = sum(base)
        return [
            ("K", LEAGUE_K_RATE),
            ("BB", base[1]),
            ("HR", base[2]),
            ("1B", base[3]),
            ("2B", base[4]),
            ("3B", base[5]),
            ("OUT", 1.0 - total),
        ]
    return [("K", k), ("BB", bb), ("HR", hr), ("1B", s1), ("2B", s2), ("3B", s3), ("OUT", outs)]


def _simulate_half_inning(probs: list[tuple[str, float]], rng: random.Random) -> int:
    """One half-inning of plate appearances until three outs.

    Base state is tracked run-to-run: singles advance runners one base,
    doubles two, triples three, homers clear. Walks advance only when
    forced. This is a deliberately coarse runner model — enough for a
    first-inning run distribution, not a claim about base-running skill.
    """
    outs = 0
    bases = [False, False, False]
    runs = 0
    while outs < 3:
        r = rng.random()
        cumulative = 0.0
        outcome = "OUT"
        for name, p in probs:
            cumulative += p
            if r < cumulative:
                outcome = name
                break
        if outcome == "OUT" or outcome == "K":
            outs += 1
        elif outcome == "BB":
            if bases[0]:
                if bases[1]:
                    if bases[2]:
                        runs += 1
                    else:
                        bases[2] = True
                else:
                    bases[1] = True
            else:
                bases[0] = True
        else:
            advance = {"1B": 1, "2B": 2, "3B": 3, "HR": 4}[outcome]
            for base in range(2, -1, -1):
                if bases[base]:
                    bases[base] = False
                    if base + advance >= 3:
                        runs += 1
                    else:
                        bases[base + advance] = True
            if advance == 4:
                runs += 1
            else:
                bases[advance - 1] = True
    return runs


def simulate_first_inning(
    row: FirstInningGameRow,
    *,
    n_sims: int = DEFAULT_SIMS_PER_GAME,
    seed: int = DEFAULT_SEED,
) -> list[SimulatedInning]:
    """Simulate the game's first inning from its PIT feature vector.

    The away half faces the home starter (and vice versa); each half uses
    the starter's shrunk rates and the opposing lineup's top-3 composite
    from the ledger row. ``seed`` is fixed per call so predictions are
    reproducible; pass a per-game seed to vary draws across games.
    """
    f = row.features
    away_probs = _pa_outcome_probs(
        float(f.get("home_starter_k_pct", LEAGUE_K_RATE)),
        float(f.get("home_starter_bb_pct", LEAGUE_BB_RATE)),
        float(f.get("away_top3_composite", 0.32)),
    )
    home_probs = _pa_outcome_probs(
        float(f.get("away_starter_k_pct", LEAGUE_K_RATE)),
        float(f.get("away_starter_bb_pct", LEAGUE_BB_RATE)),
        float(f.get("home_top3_composite", 0.32)),
    )
    rng = random.Random(seed)
    sims: list[SimulatedInning] = []
    for _ in range(n_sims):
        sims.append(
            SimulatedInning(
                runs_away=_simulate_half_inning(away_probs, rng),
                runs_home=_simulate_half_inning(home_probs, rng),
            )
        )
    return sims


def nrfi_probability(sims: list[SimulatedInning]) -> float:
    """P(NRFI) = share of simulated innings with zero combined runs."""
    if not sims:
        return 0.5106
    return sum(1 for s in sims if s.runs_away + s.runs_home == 0) / len(sims)


def first_inning_run_distribution(sims: list[SimulatedInning]) -> dict[int, float]:
    """Empirical probability mass of exact first-inning combined runs."""
    dist: dict[int, float] = {}
    for s in sims:
        dist[s.runs_away + s.runs_home] = dist.get(s.runs_away + s.runs_home, 0.0) + 1.0
    return {runs: count / len(sims) for runs, count in dist.items()}
