"""MLB Discrete-Event Plate-Appearance (PA) Monte Carlo Simulation Engine.

Architecture inspired by discrete-event baseball modeling and r/algobetting 12k backtest design:
1. 8-Class Plate Appearance (PA) Discrete Event Engine:
   - Single, Double, Triple, HomeRun, Walk/HBP, Strikeout, FieldOut, GIDP.
2. 24-State Base-Out Markov State Tracker:
   - (0, 1, 2 outs) x 8 base occupancy states (Empty, 1B, 2B, 3B, 1B+2B, 1B+3B, 2B+3B, Loaded).
3. Dynamic Base Runner Advancement:
   - Probabilistic extra bases (1st to 3rd on single, 1st scores on double) and sac fly / tag up logic.
4. Pitch Count and Fatigue Tracking:
   - Pitch count per PA sampling.
   - Times Through the Order (TTO) fatigue penalty.
   - Starter to Bullpen transition when pitch count threshold / fatigue limit is reached.
5. Monte Carlo Simulation Derivatives across N iterations:
   - Moneyline win probability (Home / Away).
   - Runline -1.5 / +1.5 distributions.
   - Full-game total and Team total distributions (O/U 6.5 .. 11.5).
   - First 5 Innings (F5) win, loss, push, and total distributions.
   - NRFI / YRFI (No Run / Yes Run First Inning) exact joint probabilities.
   - Starting Pitcher strikeout prop distributions (P(K >= 3.5 .. 8.5), expected Ks).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any

import numpy as np

from ..features.pitch_arsenal import PitchArsenalTensor
from .base import ScoreSimulation


class PAOutcome(str, Enum):
    """8-Class Plate Appearance Outcomes."""

    SINGLE = "single"
    DOUBLE = "double"
    TRIPLE = "triple"
    HOME_RUN = "home_run"
    WALK_HBP = "walk_hbp"
    STRIKEOUT = "strikeout"
    FIELD_OUT = "field_out"
    GIDP = "gidp"


class BaseOccupancy(IntEnum):
    """8 Base Occupancy States (Binary-encoded: 1B=1, 2B=2, 3B=4)."""

    EMPTY = 0  # 000: ---
    FIRST = 1  # 001: 1--
    SECOND = 2  # 010: -2-
    THIRD = 4  # 100: --3
    FIRST_SECOND = 3  # 011: 12-
    FIRST_THIRD = 5  # 101: 1-3
    SECOND_THIRD = 6  # 110: -23
    LOADED = 7  # 111: 123


# Canonical 8-class PA probabilities for MLB league baseline (~2023-2024 MLB aggregate)
LEAGUE_PA_RATES: dict[PAOutcome, float] = {
    PAOutcome.SINGLE: 0.145,
    PAOutcome.DOUBLE: 0.045,
    PAOutcome.TRIPLE: 0.005,
    PAOutcome.HOME_RUN: 0.030,
    PAOutcome.WALK_HBP: 0.088,
    PAOutcome.STRIKEOUT: 0.227,
    PAOutcome.FIELD_OUT: 0.435,
    PAOutcome.GIDP: 0.025,
}


@dataclass(slots=True)
class BaseRunnerState:
    """Explicit base runner occupancy."""

    first: bool = False
    second: bool = False
    third: bool = False

    @property
    def occupancy(self) -> BaseOccupancy:
        val = int(self.first) | (int(self.second) << 1) | (int(self.third) << 2)
        return BaseOccupancy(val)

    @classmethod
    def from_occupancy(cls, occ: BaseOccupancy | int) -> BaseRunnerState:
        val = int(occ)
        return cls(
            first=bool(val & 1),
            second=bool(val & 2),
            third=bool(val & 4),
        )

    def runners_count(self) -> int:
        return int(self.first) + int(self.second) + int(self.third)

    def clear(self) -> None:
        self.first = False
        self.second = False
        self.third = False


@dataclass(slots=True)
class BatterProfile:
    """Individual batter offensive profile."""

    player_id: str | int
    name: str = "Batter"
    handedness: str = "R"  # "R", "L", "S"
    single_rate: float = LEAGUE_PA_RATES[PAOutcome.SINGLE]
    double_rate: float = LEAGUE_PA_RATES[PAOutcome.DOUBLE]
    triple_rate: float = LEAGUE_PA_RATES[PAOutcome.TRIPLE]
    hr_rate: float = LEAGUE_PA_RATES[PAOutcome.HOME_RUN]
    bb_rate: float = LEAGUE_PA_RATES[PAOutcome.WALK_HBP]
    k_rate: float = LEAGUE_PA_RATES[PAOutcome.STRIKEOUT]
    field_out_rate: float = LEAGUE_PA_RATES[PAOutcome.FIELD_OUT]
    gidp_rate: float = LEAGUE_PA_RATES[PAOutcome.GIDP]

    def get_outcome_rates(self) -> dict[PAOutcome, float]:
        raw = {
            PAOutcome.SINGLE: max(0.01, self.single_rate),
            PAOutcome.DOUBLE: max(0.005, self.double_rate),
            PAOutcome.TRIPLE: max(0.001, self.triple_rate),
            PAOutcome.HOME_RUN: max(0.005, self.hr_rate),
            PAOutcome.WALK_HBP: max(0.01, self.bb_rate),
            PAOutcome.STRIKEOUT: max(0.05, self.k_rate),
            PAOutcome.FIELD_OUT: max(0.15, self.field_out_rate),
            PAOutcome.GIDP: max(0.005, self.gidp_rate),
        }
        total = sum(raw.values())
        return {k: v / total for k, v in raw.items()}


@dataclass
class PitcherProfile:
    """Pitcher profile with repertoire tensor, fatigue curve, and transition thresholds."""

    player_id: str | int
    name: str = "Pitcher"
    is_starter: bool = True
    handedness: str = "R"
    pitch_limit: int = 95
    max_batters_faced: int = 25
    single_rate: float = LEAGUE_PA_RATES[PAOutcome.SINGLE]
    double_rate: float = LEAGUE_PA_RATES[PAOutcome.DOUBLE]
    triple_rate: float = LEAGUE_PA_RATES[PAOutcome.TRIPLE]
    hr_rate: float = LEAGUE_PA_RATES[PAOutcome.HOME_RUN]
    bb_rate: float = LEAGUE_PA_RATES[PAOutcome.WALK_HBP]
    k_rate: float = LEAGUE_PA_RATES[PAOutcome.STRIKEOUT]
    field_out_rate: float = LEAGUE_PA_RATES[PAOutcome.FIELD_OUT]
    gidp_rate: float = LEAGUE_PA_RATES[PAOutcome.GIDP]
    pitch_arsenal_tensor: PitchArsenalTensor | None = None

    def get_outcome_rates(self) -> dict[PAOutcome, float]:
        raw = {
            PAOutcome.SINGLE: max(0.01, self.single_rate),
            PAOutcome.DOUBLE: max(0.005, self.double_rate),
            PAOutcome.TRIPLE: max(0.001, self.triple_rate),
            PAOutcome.HOME_RUN: max(0.005, self.hr_rate),
            PAOutcome.WALK_HBP: max(0.01, self.bb_rate),
            PAOutcome.STRIKEOUT: max(0.05, self.k_rate),
            PAOutcome.FIELD_OUT: max(0.15, self.field_out_rate),
            PAOutcome.GIDP: max(0.005, self.gidp_rate),
        }

        # Apply pitch arsenal tensor modifications if present
        if self.pitch_arsenal_tensor is not None:
            mods = self.pitch_arsenal_tensor.to_simulation_modifiers()
            raw[PAOutcome.STRIKEOUT] *= mods["k_rate_mult"]
            raw[PAOutcome.WALK_HBP] *= mods["bb_rate_mult"]
            raw[PAOutcome.HOME_RUN] *= mods["hr_suppression"]
            raw[PAOutcome.SINGLE] *= mods["contact_mult"]
            raw[PAOutcome.DOUBLE] *= mods["contact_mult"]

        total = sum(raw.values())
        return {k: v / total for k, v in raw.items()}


@dataclass
class TeamLineup:
    """9-Hitter Batting Order for a team."""

    team_name: str
    batters: list[BatterProfile]

    def __post_init__(self) -> None:
        if len(self.batters) < 9:
            # Pad with league average hitters if fewer than 9 provided
            while len(self.batters) < 9:
                idx = len(self.batters) + 1
                self.batters.append(BatterProfile(player_id=f"hitter_{idx}", name=f"Hitter {idx}"))

    def get_batter(self, order_idx: int) -> BatterProfile:
        return self.batters[order_idx % 9]


@dataclass(slots=True)
class HalfInningResult:
    """Detailed summary of half-inning outcome."""

    runs: int = 0
    hits: int = 0
    strikeouts: int = 0
    walks: int = 0
    pitches: int = 0
    pas: int = 0


@dataclass(slots=True)
class SingleGameSimulation:
    """Result of a single discrete-event simulated game."""

    home_runs: int
    away_runs: int
    home_inning_runs: list[int]
    away_inning_runs: list[int]
    home_sp_strikeouts: int
    away_sp_strikeouts: int
    home_sp_innings: float
    away_sp_innings: float
    home_sp_pitches: int
    away_sp_pitches: int
    total_innings: int

    @property
    def total_runs(self) -> int:
        return self.home_runs + self.away_runs

    @property
    def f5_home_runs(self) -> int:
        return sum(self.home_inning_runs[:5])

    @property
    def f5_away_runs(self) -> int:
        return sum(self.away_inning_runs[:5])

    @property
    def f5_total_runs(self) -> int:
        return self.f5_home_runs + self.f5_away_runs

    @property
    def is_nrfi(self) -> bool:
        top_1st = self.away_inning_runs[0] if self.away_inning_runs else 0
        bot_1st = self.home_inning_runs[0] if self.home_inning_runs else 0
        return (top_1st == 0) and (bot_1st == 0)


@dataclass
class MLBMonteCarloResult:
    """Comprehensive Monte Carlo Simulation Analytics and Betting Market Distributions."""

    n_sims: int
    home_win_prob: float
    away_win_prob: float
    home_minus_1_5_prob: float
    home_plus_1_5_prob: float
    away_minus_1_5_prob: float
    away_plus_1_5_prob: float
    mean_home_runs: float
    mean_away_runs: float
    mean_total_runs: float
    total_distributions: dict[float, float]
    team_totals: dict[str, dict[float, float]]
    f5_home_win_prob: float
    f5_away_win_prob: float
    f5_push_prob: float
    f5_totals: dict[float, float]
    nrfi_prob: float
    yrfi_prob: float
    home_sp_k_props: dict[str, float]
    away_sp_k_props: dict[str, float]
    simulations: list[SingleGameSimulation] = field(default_factory=list, repr=False)

    def to_score_simulation(self, model_version: str = "mlb-monte-carlo-v1") -> ScoreSimulation:
        """Convert to standard ScoreSimulation object for downstream ledger & pricing integration."""
        away_scores = [s.away_runs for s in self.simulations]
        home_scores = [s.home_runs for s in self.simulations]
        return ScoreSimulation(
            away_scores=away_scores,
            home_scores=home_scores,
            uncertainty=0.08,
            model_version=model_version,
        )

    def summary(self) -> dict[str, Any]:
        """Return formatted summary dictionary."""
        return {
            "n_sims": self.n_sims,
            "moneyline": {
                "home_win_prob": round(self.home_win_prob, 4),
                "away_win_prob": round(self.away_win_prob, 4),
            },
            "runline": {
                "home_minus_1_5": round(self.home_minus_1_5_prob, 4),
                "home_plus_1_5": round(self.home_plus_1_5_prob, 4),
                "away_minus_1_5": round(self.away_minus_1_5_prob, 4),
                "away_plus_1_5": round(self.away_plus_1_5_prob, 4),
            },
            "projected_scores": {
                "home": round(self.mean_home_runs, 2),
                "away": round(self.mean_away_runs, 2),
                "total": round(self.mean_total_runs, 2),
            },
            "nrfi_yrfi": {
                "p_nrfi": round(self.nrfi_prob, 4),
                "p_yrfi": round(self.yrfi_prob, 4),
            },
            "first_5_innings": {
                "f5_home_win": round(self.f5_home_win_prob, 4),
                "f5_away_win": round(self.f5_away_win_prob, 4),
                "f5_push": round(self.f5_push_prob, 4),
                "f5_totals": {k: round(v, 4) for k, v in self.f5_totals.items()},
            },
            "full_game_totals": {k: round(v, 4) for k, v in self.total_distributions.items()},
            "home_sp_k_props": {k: round(v, 3) for k, v in self.home_sp_k_props.items()},
            "away_sp_k_props": {k: round(v, 3) for k, v in self.away_sp_k_props.items()},
        }


class MLBMonteCarloEngine:
    """High-Performance 8-Class PA Discrete-Event Simulation Engine."""

    def __init__(
        self,
        *,
        p_1b_to_3b_on_single: float = 0.28,
        p_2b_score_on_single: float = 0.58,
        p_1b_score_on_double: float = 0.42,
        p_sac_fly_tag_3b: float = 0.52,
        p_tag_2b_to_3b: float = 0.22,
        p_gidp_score_3b: float = 0.65,
    ) -> None:
        self.p_1b_to_3b_on_single = p_1b_to_3b_on_single
        self.p_2b_score_on_single = p_2b_score_on_single
        self.p_1b_score_on_double = p_1b_score_on_double
        self.p_sac_fly_tag_3b = p_sac_fly_tag_3b
        self.p_tag_2b_to_3b = p_tag_2b_to_3b
        self.p_gidp_score_3b = p_gidp_score_3b

    @staticmethod
    def compute_matchup_probabilities(
        batter: BatterProfile,
        pitcher: PitcherProfile,
        *,
        times_through_order: int = 1,
        park_factor: float = 1.0,
        weather_factor: float = 1.0,
    ) -> np.ndarray:
        """Compute normalized 8-class PA probabilities via Log-5 / Multiplicative Odds Ratio."""
        b_rates = batter.get_outcome_rates()
        p_rates = pitcher.get_outcome_rates()

        # Times Through the Order (TTO) Fatigue Modifier
        if times_through_order == 1:
            tto_k = 1.0
            tto_hr = 1.0
            tto_hit = 1.0
        elif times_through_order == 2:
            tto_k = 0.96
            tto_hr = 1.06
            tto_hit = 1.03
        else:  # 3rd+ TTO
            tto_k = 0.88
            tto_hr = 1.18
            tto_hit = 1.08

        # Odds-Ratio Matchup blend relative to league baseline
        probs = np.zeros(8, dtype=np.float64)
        for i, outcome in enumerate(PAOutcome):
            b_p = b_rates[outcome]
            p_p = p_rates[outcome]
            l_p = LEAGUE_PA_RATES[outcome]

            # Multiplicative Log-5 odds ratio formula
            odds_ratio = (b_p / (1.0 - b_p + 1e-6)) * (p_p / (1.0 - p_p + 1e-6)) / (l_p / (1.0 - l_p + 1e-6))
            p_val = odds_ratio / (1.0 + odds_ratio)

            # Environmental & fatigue adjustments
            if outcome == PAOutcome.HOME_RUN:
                p_val *= park_factor * weather_factor * tto_hr
            elif outcome in (PAOutcome.SINGLE, PAOutcome.DOUBLE, PAOutcome.TRIPLE):
                p_val *= math.sqrt(park_factor) * tto_hit
            elif outcome == PAOutcome.STRIKEOUT:
                p_val *= tto_k

            probs[i] = max(p_val, 1e-4)

        # Normalize to sum to 1.0
        total = np.sum(probs)
        return probs / total

    @staticmethod
    def sample_pitches_per_pa(outcome: PAOutcome, rng: np.random.Generator) -> int:
        """Simulate realistic pitch count consumed in a plate appearance."""
        if outcome in (PAOutcome.STRIKEOUT, PAOutcome.WALK_HBP):
            # Ks and Walks take more pitches (mean ~4.8)
            return int(rng.choice([3, 4, 5, 6, 7, 8], p=[0.08, 0.22, 0.35, 0.22, 0.10, 0.03]))
        if outcome == PAOutcome.HOME_RUN:
            return int(rng.choice([1, 2, 3, 4, 5, 6], p=[0.14, 0.26, 0.30, 0.18, 0.09, 0.03]))
        # Standard in-play contact or field out (mean ~3.6)
        return int(rng.choice([1, 2, 3, 4, 5, 6], p=[0.16, 0.28, 0.28, 0.16, 0.08, 0.04]))

    def simulate_pa(
        self,
        base_state: BaseRunnerState,
        outs: int,
        outcome: PAOutcome,
        rng: np.random.Generator,
    ) -> tuple[int, int]:
        """Simulate dynamic base runner advancement and return (runs_scored, new_outs)."""
        runs = 0

        if outcome == PAOutcome.HOME_RUN:
            runs = 1 + base_state.runners_count()
            base_state.clear()
            return runs, outs

        if outcome == PAOutcome.TRIPLE:
            runs = base_state.runners_count()
            base_state.clear()
            base_state.third = True
            return runs, outs

        if outcome == PAOutcome.DOUBLE:
            runs = int(base_state.second) + int(base_state.third)
            base_state.third = False
            base_state.second = True
            if base_state.first:
                # 1st to home or 3rd
                if rng.random() < self.p_1b_score_on_double:
                    runs += 1
                else:
                    base_state.third = True
            base_state.first = False
            return runs, outs

        if outcome == PAOutcome.SINGLE:
            if base_state.third:
                runs += 1
                base_state.third = False
            if base_state.second:
                if rng.random() < self.p_2b_score_on_single:
                    runs += 1
                    base_state.second = False
                else:
                    base_state.third = True
                    base_state.second = False
            if base_state.first:
                if not base_state.third and rng.random() < self.p_1b_to_3b_on_single:
                    base_state.third = True
                else:
                    base_state.second = True
            base_state.first = True
            return runs, outs

        if outcome == PAOutcome.WALK_HBP:
            # Force advance
            if base_state.first:
                if base_state.second:
                    if base_state.third:
                        runs += 1
                    base_state.third = True
                base_state.second = True
            base_state.first = True
            return runs, outs

        if outcome == PAOutcome.STRIKEOUT:
            return 0, outs + 1

        if outcome == PAOutcome.FIELD_OUT:
            new_outs = outs + 1
            if new_outs < 3:
                # Sac fly / tag up from 3B
                if base_state.third and rng.random() < self.p_sac_fly_tag_3b:
                    runs += 1
                    base_state.third = False
                # Tag up 2B -> 3B if 3B is empty
                if base_state.second and not base_state.third and rng.random() < self.p_tag_2b_to_3b:
                    base_state.third = True
                    base_state.second = False
            return runs, new_outs

        if outcome == PAOutcome.GIDP:
            # Double play requires runner on 1B and outs < 2
            if base_state.first and outs < 2:
                if outs == 0:
                    # 2 outs recorded
                    new_outs = 2
                    base_state.first = False
                    if base_state.third and rng.random() < self.p_gidp_score_3b:
                        runs += 1
                        base_state.third = False
                    if base_state.second and not base_state.third:
                        base_state.third = True
                        base_state.second = False
                    return runs, new_outs
                # outs == 1 -> inning ends on double play
                base_state.first = False
                return 0, 3
            # No DP possible -> standard field out
            new_outs = outs + 1
            if new_outs < 3 and base_state.third and rng.random() < self.p_sac_fly_tag_3b:
                runs += 1
                base_state.third = False
            return runs, new_outs

        return 0, outs

    def simulate_half_inning(
        self,
        batting_lineup: TeamLineup,
        active_pitcher: PitcherProfile,
        order_idx: int,
        pitcher_pitch_count: int,
        pitcher_batters_faced: int,
        is_extra_inning: bool,
        park_factor: float,
        weather_factor: float,
        rng: np.random.Generator,
    ) -> tuple[HalfInningResult, int, int, int]:
        """Simulate one half-inning until 3 outs are reached."""
        outs = 0
        base_state = BaseRunnerState()
        if is_extra_inning:
            # MLB Ghost runner rule on 2B in extra innings
            base_state.second = True

        res = HalfInningResult()
        current_order_idx = order_idx
        current_pitch_count = pitcher_pitch_count
        current_batters_faced = pitcher_batters_faced

        pa_outcome_list = list(PAOutcome)

        while outs < 3:
            batter = batting_lineup.get_batter(current_order_idx)
            tto = (current_batters_faced // 9) + 1

            probs = self.compute_matchup_probabilities(
                batter,
                active_pitcher,
                times_through_order=tto,
                park_factor=park_factor,
                weather_factor=weather_factor,
            )

            # Sample discrete PA outcome
            outcome_idx = rng.choice(8, p=probs)
            outcome = pa_outcome_list[outcome_idx]

            # Track pitches
            pitches = self.sample_pitches_per_pa(outcome, rng)
            current_pitch_count += pitches
            current_batters_faced += 1
            res.pitches += pitches
            res.pas += 1

            if outcome == PAOutcome.STRIKEOUT:
                res.strikeouts += 1
            elif outcome == PAOutcome.WALK_HBP:
                res.walks += 1
            elif outcome in (PAOutcome.SINGLE, PAOutcome.DOUBLE, PAOutcome.TRIPLE, PAOutcome.HOME_RUN):
                res.hits += 1

            runs_on_play, outs = self.simulate_pa(base_state, outs, outcome, rng)
            res.runs += runs_on_play
            current_order_idx += 1

        return res, current_order_idx, current_pitch_count, current_batters_faced

    def simulate_game(
        self,
        home_lineup: TeamLineup,
        away_lineup: TeamLineup,
        home_starter: PitcherProfile,
        away_starter: PitcherProfile,
        home_bullpen: PitcherProfile,
        away_bullpen: PitcherProfile,
        *,
        park_factor: float = 1.0,
        weather_factor: float = 1.0,
        rng: np.random.Generator | None = None,
    ) -> SingleGameSimulation:
        """Simulate a single full 9+ inning discrete-event MLB game."""
        if rng is None:
            rng = np.random.default_rng()

        home_inning_runs: list[int] = []
        away_inning_runs: list[int] = []

        home_order_idx = 0
        away_order_idx = 0

        # Pitcher states
        home_pitcher = home_starter
        away_pitcher = away_starter
        home_is_starter_active = True
        away_is_starter_active = True

        home_sp_ks = 0
        away_sp_ks = 0
        home_sp_outs = 0
        away_sp_outs = 0
        home_sp_pitches = 0
        away_sp_pitches = 0

        home_total_runs = 0
        away_total_runs = 0

        inning = 1

        while True:
            # -------------------------------------------------------------
            # Top of Inning: Away team batting vs Home team pitching
            # -------------------------------------------------------------
            # Check Home starter fatigue / hook
            if home_is_starter_active and (
                home_sp_pitches >= home_starter.pitch_limit
                or home_pitcher.max_batters_faced <= (home_sp_outs + 10) // 3 * 4
                or inning >= 7
            ):
                home_pitcher = home_bullpen
                home_is_starter_active = False

            top_res, away_order_idx, _p_count, _b_faced = self.simulate_half_inning(
                batting_lineup=away_lineup,
                active_pitcher=home_pitcher,
                order_idx=away_order_idx,
                pitcher_pitch_count=home_sp_pitches if home_is_starter_active else 0,
                pitcher_batters_faced=(home_sp_outs + 5) if home_is_starter_active else 0,
                is_extra_inning=(inning > 9),
                park_factor=park_factor,
                weather_factor=weather_factor,
                rng=rng,
            )

            away_inning_runs.append(top_res.runs)
            away_total_runs += top_res.runs

            if home_is_starter_active:
                home_sp_ks += top_res.strikeouts
                home_sp_outs += 3
                home_sp_pitches += top_res.pitches

            # -------------------------------------------------------------
            # Bottom of Inning: Home team batting vs Away team pitching
            # -------------------------------------------------------------
            # Walk-off / 9th inning bottom skip check
            if inning >= 9 and home_total_runs > away_total_runs:
                # Home team is already leading in 9th+ inning, bottom skipped
                home_inning_runs.append(0)
                break

            # Check Away starter fatigue / hook
            if away_is_starter_active and (
                away_sp_pitches >= away_starter.pitch_limit
                or away_pitcher.max_batters_faced <= (away_sp_outs + 10) // 3 * 4
                or inning >= 7
            ):
                away_pitcher = away_bullpen
                away_is_starter_active = False

            bot_res, home_order_idx, _p_count, _b_faced = self.simulate_half_inning(
                batting_lineup=home_lineup,
                active_pitcher=away_pitcher,
                order_idx=home_order_idx,
                pitcher_pitch_count=away_sp_pitches if away_is_starter_active else 0,
                pitcher_batters_faced=(away_sp_outs + 5) if away_is_starter_active else 0,
                is_extra_inning=(inning > 9),
                park_factor=park_factor,
                weather_factor=weather_factor,
                rng=rng,
            )

            home_inning_runs.append(bot_res.runs)
            home_total_runs += bot_res.runs

            if away_is_starter_active:
                away_sp_ks += bot_res.strikeouts
                away_sp_outs += 3
                away_sp_pitches += bot_res.pitches

            # Check if game is concluded after 9 innings
            if inning >= 9 and home_total_runs != away_total_runs:
                break

            inning += 1
            if inning > 15:  # Safety boundary against infinite extra innings
                if home_total_runs == away_total_runs:
                    home_total_runs += 1  # Break tie
                break

        return SingleGameSimulation(
            home_runs=home_total_runs,
            away_runs=away_total_runs,
            home_inning_runs=home_inning_runs,
            away_inning_runs=away_inning_runs,
            home_sp_strikeouts=home_sp_ks,
            away_sp_strikeouts=away_sp_ks,
            home_sp_innings=home_sp_outs / 3.0,
            away_sp_innings=away_sp_outs / 3.0,
            home_sp_pitches=home_sp_pitches,
            away_sp_pitches=away_sp_pitches,
            total_innings=inning,
        )

    def run_monte_carlo(
        self,
        home_lineup: TeamLineup,
        away_lineup: TeamLineup,
        home_starter: PitcherProfile,
        away_starter: PitcherProfile,
        home_bullpen: PitcherProfile | None = None,
        away_bullpen: PitcherProfile | None = None,
        *,
        n_sims: int = 2500,
        park_factor: float = 1.0,
        weather_factor: float = 1.0,
        seed: int | None = None,
    ) -> MLBMonteCarloResult:
        """Run N Monte Carlo game simulations to derive full betting market distributions."""
        rng = np.random.default_rng(seed)

        if home_bullpen is None:
            home_bullpen = PitcherProfile(
                player_id="home_bullpen",
                name="Home Bullpen",
                is_starter=False,
                k_rate=LEAGUE_PA_RATES[PAOutcome.STRIKEOUT] + 0.02,
                bb_rate=LEAGUE_PA_RATES[PAOutcome.WALK_HBP] + 0.005,
                hr_rate=LEAGUE_PA_RATES[PAOutcome.HOME_RUN] - 0.004,
            )
        if away_bullpen is None:
            away_bullpen = PitcherProfile(
                player_id="away_bullpen",
                name="Away Bullpen",
                is_starter=False,
                k_rate=LEAGUE_PA_RATES[PAOutcome.STRIKEOUT] + 0.02,
                bb_rate=LEAGUE_PA_RATES[PAOutcome.WALK_HBP] + 0.005,
                hr_rate=LEAGUE_PA_RATES[PAOutcome.HOME_RUN] - 0.004,
            )

        sims: list[SingleGameSimulation] = []
        for _ in range(n_sims):
            sim = self.simulate_game(
                home_lineup=home_lineup,
                away_lineup=away_lineup,
                home_starter=home_starter,
                away_starter=away_starter,
                home_bullpen=home_bullpen,
                away_bullpen=away_bullpen,
                park_factor=park_factor,
                weather_factor=weather_factor,
                rng=rng,
            )
            sims.append(sim)

        # -------------------------------------------------------------
        # 1. Moneyline Win Probabilities
        # -------------------------------------------------------------
        home_wins = sum(1 for s in sims if s.home_runs > s.away_runs)
        home_win_prob = home_wins / n_sims
        away_win_prob = 1.0 - home_win_prob

        # -------------------------------------------------------------
        # 2. Runline -1.5 / +1.5 Distributions
        # -------------------------------------------------------------
        home_m15 = sum(1 for s in sims if (s.home_runs - s.away_runs) >= 2) / n_sims
        home_p15 = sum(1 for s in sims if (s.home_runs - s.away_runs) >= -1) / n_sims
        away_m15 = sum(1 for s in sims if (s.away_runs - s.home_runs) >= 2) / n_sims
        away_p15 = sum(1 for s in sims if (s.away_runs - s.home_runs) >= -1) / n_sims

        # -------------------------------------------------------------
        # 3. Projected Run Means
        # -------------------------------------------------------------
        mean_home = float(np.mean([s.home_runs for s in sims]))
        mean_away = float(np.mean([s.away_runs for s in sims]))
        mean_total = float(np.mean([s.total_runs for s in sims]))

        # -------------------------------------------------------------
        # 4. Full Game Over / Under Lines (6.5 to 11.5)
        # -------------------------------------------------------------
        total_lines = [6.5, 7.0, 7.5, 8.0, 8.5, 9.0, 9.5, 10.0, 10.5, 11.0, 11.5]
        totals_dist: dict[float, float] = {}
        for line in total_lines:
            if line.is_integer():
                # For integer totals, push gets 0.5 or strict over
                over_count = sum(1 for s in sims if s.total_runs > line)
                push_count = sum(1 for s in sims if s.total_runs == line)
                totals_dist[line] = (over_count + 0.5 * push_count) / n_sims
            else:
                totals_dist[line] = sum(1 for s in sims if s.total_runs > line) / n_sims

        # Team totals
        tt_lines = [3.5, 4.0, 4.5, 5.0, 5.5]
        team_totals: dict[str, dict[float, float]] = {"home": {}, "away": {}}
        for line in tt_lines:
            team_totals["home"][line] = sum(1 for s in sims if s.home_runs > line) / n_sims
            team_totals["away"][line] = sum(1 for s in sims if s.away_runs > line) / n_sims

        # -------------------------------------------------------------
        # 5. First 5 Innings (F5) Derivatives
        # -------------------------------------------------------------
        f5_home_wins = sum(1 for s in sims if s.f5_home_runs > s.f5_away_runs)
        f5_away_wins = sum(1 for s in sims if s.f5_away_runs > s.f5_home_runs)
        f5_pushes = sum(1 for s in sims if s.f5_home_runs == s.f5_away_runs)

        f5_totals: dict[float, float] = {}
        for f5_line in [3.5, 4.0, 4.5, 5.0, 5.5]:
            f5_totals[f5_line] = sum(1 for s in sims if s.f5_total_runs > f5_line) / n_sims

        # -------------------------------------------------------------
        # 6. NRFI / YRFI (1st Inning Joint Probabilities)
        # -------------------------------------------------------------
        nrfi_count = sum(1 for s in sims if s.is_nrfi)
        p_nrfi = nrfi_count / n_sims
        p_yrfi = 1.0 - p_nrfi

        # -------------------------------------------------------------
        # 7. Starter Strikeout Props Distribution
        # -------------------------------------------------------------
        def compute_k_props(ks: Sequence[int]) -> dict[str, float]:
            arr = np.asarray(ks)
            return {
                "expected_k": float(np.mean(arr)),
                "median_k": float(np.median(arr)),
                "p_k_ge_3_5": float(np.mean(arr >= 4)),
                "p_k_ge_4_5": float(np.mean(arr >= 5)),
                "p_k_ge_5_5": float(np.mean(arr >= 6)),
                "p_k_ge_6_5": float(np.mean(arr >= 7)),
                "p_k_ge_7_5": float(np.mean(arr >= 8)),
                "p_k_ge_8_5": float(np.mean(arr >= 9)),
            }

        home_k_props = compute_k_props([s.home_sp_strikeouts for s in sims])
        away_k_props = compute_k_props([s.away_sp_strikeouts for s in sims])

        return MLBMonteCarloResult(
            n_sims=n_sims,
            home_win_prob=home_win_prob,
            away_win_prob=away_win_prob,
            home_minus_1_5_prob=home_m15,
            home_plus_1_5_prob=home_p15,
            away_minus_1_5_prob=away_m15,
            away_plus_1_5_prob=away_p15,
            mean_home_runs=mean_home,
            mean_away_runs=mean_away,
            mean_total_runs=mean_total,
            total_distributions=totals_dist,
            team_totals=team_totals,
            f5_home_win_prob=f5_home_wins / n_sims,
            f5_away_win_prob=f5_away_wins / n_sims,
            f5_push_prob=f5_pushes / n_sims,
            f5_totals=f5_totals,
            nrfi_prob=p_nrfi,
            yrfi_prob=p_yrfi,
            home_sp_k_props=home_k_props,
            away_sp_k_props=away_k_props,
            simulations=sims,
        )


def create_sample_lineup(team_name: str, power_boost: float = 0.0) -> TeamLineup:
    """Create a realistic 9-man MLB batting order with talent gradient."""
    batters: list[BatterProfile] = []
    # Realistic batting order quality gradient
    order_weights = [
        {"name": "Leadoff", "bb": 0.11, "k": 0.18, "single": 0.17, "hr": 0.025},
        {"name": "No. 2", "bb": 0.10, "k": 0.20, "single": 0.16, "hr": 0.045 + power_boost},
        {"name": "No. 3 Slugger", "bb": 0.12, "k": 0.21, "single": 0.15, "hr": 0.055 + power_boost},
        {"name": "Cleanup", "bb": 0.09, "k": 0.24, "single": 0.14, "hr": 0.060 + power_boost},
        {"name": "No. 5", "bb": 0.08, "k": 0.22, "single": 0.14, "hr": 0.038},
        {"name": "No. 6", "bb": 0.08, "k": 0.23, "single": 0.14, "hr": 0.030},
        {"name": "No. 7", "bb": 0.07, "k": 0.25, "single": 0.13, "hr": 0.025},
        {"name": "No. 8", "bb": 0.06, "k": 0.26, "single": 0.13, "hr": 0.020},
        {"name": "No. 9", "bb": 0.07, "k": 0.24, "single": 0.14, "hr": 0.015},
    ]

    for idx, w in enumerate(order_weights, start=1):
        batters.append(
            BatterProfile(
                player_id=f"{team_name.lower()}_{idx}",
                name=f"{team_name} {w['name']}",
                single_rate=w["single"],
                hr_rate=w["hr"],
                bb_rate=w["bb"],
                k_rate=w["k"],
            )
        )
    return TeamLineup(team_name=team_name, batters=batters)
