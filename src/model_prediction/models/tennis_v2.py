"""Tennis v2: Dynamic Empirical Bayes Surface & Inactivity Shrinkage Model.

Addresses the structural flaws in Tennis v1 (fixed 60/40 surface weighting):
1. Dynamic Surface Shrinkage:
   w_surface = w_max * (n_surface / (n_surface + C_stabilization))
   where w_max = 0.75 and C_stabilization = 15 matches.
2. Inactivity Rust Decay:
   Exponential shrinkage toward neutral 1500 rating for long layoffs (>45 days).
3. Best-of-3 vs Best-of-5 Format Transformation:
   Derives match win probability from underlying set win probability p:
   - Best-of-3: P_Bo3(p) = p^2 * (3 - 2p)
   - Best-of-5: P_Bo5(p) = p^3 * (1 + 3*(1-p) + 6*(1-p)^2)
4. Strict Point-in-Time (PIT) chronological state.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

TENNIS_V2_MODEL_VERSION = "tennis-surface-elo-v2"
DEFAULT_ELO = 1500.0
BASE_K_FACTOR = 32.0
W_MAX_SURFACE = 0.75
C_SURFACE_STABILIZATION = 15.0
RUST_DAYS_THRESHOLD = 45.0
RUST_DECAY_RATE = 0.003  # Daily decay rate toward 1500 after 45 days


def _parse_date(val: Any) -> datetime:
    """Parse date or ISO string into timezone-aware UTC datetime."""
    s = str(val).replace("Z", "+00:00").split("T")[0]
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC)


def _is_strictly_before(record_date: str, as_of_date: str) -> bool:
    """Check if record_date is strictly before as_of_date."""
    if record_date == as_of_date:
        return False
    if ("T" in record_date or " " in record_date) and ("T" in as_of_date or " " in as_of_date):
        return record_date < as_of_date
    return _parse_date(record_date).date() < _parse_date(as_of_date).date()


def expected_win_probability(rating_a: float, rating_b: float) -> float:
    """Standard Elo logistic formula."""
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def set_to_match_prob_bo3(p_set: float) -> float:
    """Probability of winning Best-of-3 match given set win prob p."""
    # P = p^2 (2-0) + 2 * p^2 * (1-p) (2-1) = p^2 * (3 - 2p)
    p = max(1e-6, min(1.0 - 1e-6, p_set))
    return p * p * (3.0 - 2.0 * p)


def set_to_match_prob_bo5(p_set: float) -> float:
    """Probability of winning Best-of-5 match (Grand Slam) given set win prob p."""
    # P = p^3 (3-0) + 3 * p^3 * (1-p) (3-1) + 6 * p^3 * (1-p)^2 (3-2)
    p = max(1e-6, min(1.0 - 1e-6, p_set))
    q = 1.0 - p
    return p**3 * (1.0 + 3.0 * q + 6.0 * q * q)


@dataclass(slots=True)
class TennisPlayerProfile:
    """Player rating state across surfaces with match history."""

    player_id: str
    overall_elo: float = DEFAULT_ELO
    surface_elo: dict[str, float] = field(default_factory=dict)
    surface_match_counts: dict[str, int] = field(default_factory=dict)
    total_matches: int = 0
    last_match_date: str = ""


@dataclass(slots=True)
class TennisMatchForecast:
    """Forecast output for a tennis match."""

    player_one: str
    player_two: str
    surface: str
    format: str  # "Bo3" or "Bo5"
    p_player_one_win: float
    p_player_two_win: float
    p_set_win_one: float
    p1_blended_elo: float
    p2_blended_elo: float
    p1_surface_weight: float
    p2_surface_weight: float
    p1_inactivity_days: int
    p2_inactivity_days: int


class TennisV2Model:
    """Tennis v2 dynamic shrinkage and format-aware model."""

    version: str = TENNIS_V2_MODEL_VERSION
    sport: str = "tennis"

    def __init__(
        self,
        k_factor: float = BASE_K_FACTOR,
        w_max_surface: float = W_MAX_SURFACE,
        c_surface: float = C_SURFACE_STABILIZATION,
    ) -> None:
        self.k_factor = k_factor
        self.w_max_surface = w_max_surface
        self.c_surface = c_surface
        self._matches: list[dict[str, Any]] = []

    def record_match(self, match: dict[str, Any]) -> None:
        """Record completed match chronologically."""
        self._matches.append(match)

    def record_matches(self, matches: Sequence[dict[str, Any]]) -> None:
        """Record a sequence of matches."""
        for m in matches:
            self.record_match(m)

    def compute_player_profiles(self, as_of_date: str) -> dict[str, TennisPlayerProfile]:
        """Compute point-in-time player profiles strictly before as_of_date."""
        profiles: dict[str, TennisPlayerProfile] = {}
        valid_matches = [
            m for m in self._matches if _is_strictly_before(str(m.get("match_date", "")), as_of_date)
        ]
        sorted_matches = sorted(valid_matches, key=lambda item: str(item.get("match_date", "")))

        for match in sorted_matches:
            winner = str(match.get("winner", ""))
            loser = str(match.get("loser", ""))
            surface = str(match.get("surface", "Hard"))
            m_date = str(match.get("match_date", ""))
            if not winner or not loser:
                continue

            prof_w = profiles.setdefault(winner, TennisPlayerProfile(player_id=winner))
            prof_l = profiles.setdefault(loser, TennisPlayerProfile(player_id=loser))

            # Overall Elo update
            exp_w_overall = expected_win_probability(prof_w.overall_elo, prof_l.overall_elo)
            prof_w.overall_elo += self.k_factor * (1.0 - exp_w_overall)
            prof_l.overall_elo -= self.k_factor * (1.0 - exp_w_overall)

            # Surface Elo update
            s_elo_w = prof_w.surface_elo.get(surface, DEFAULT_ELO)
            s_elo_l = prof_l.surface_elo.get(surface, DEFAULT_ELO)
            exp_w_surf = expected_win_probability(s_elo_w, s_elo_l)
            prof_w.surface_elo[surface] = s_elo_w + self.k_factor * (1.0 - exp_w_surf)
            prof_l.surface_elo[surface] = s_elo_l - self.k_factor * (1.0 - exp_w_surf)

            # Match counts
            prof_w.surface_match_counts[surface] = prof_w.surface_match_counts.get(surface, 0) + 1
            prof_l.surface_match_counts[surface] = prof_l.surface_match_counts.get(surface, 0) + 1
            prof_w.total_matches += 1
            prof_l.total_matches += 1
            prof_w.last_match_date = m_date
            prof_l.last_match_date = m_date

        return profiles

    def evaluate_effective_elo(
        self,
        profile: TennisPlayerProfile | None,
        surface: str,
        as_of_date: str,
    ) -> tuple[float, float, int]:
        """Evaluate blended surface-overall Elo with inactivity decay."""
        if profile is None or profile.total_matches == 0:
            return DEFAULT_ELO, 0.0, 0

        n_surf = profile.surface_match_counts.get(surface, 0)
        w_surf = self.w_max_surface * (n_surf / (n_surf + self.c_surface))

        s_elo = profile.surface_elo.get(surface, profile.overall_elo)
        blended_elo = w_surf * s_elo + (1.0 - w_surf) * profile.overall_elo

        # Inactivity decay
        inactivity_days = 0
        if profile.last_match_date:
            d_last = _parse_date(profile.last_match_date).date()
            d_now = _parse_date(as_of_date).date()
            inactivity_days = max(0, (d_now - d_last).days)

        if inactivity_days > RUST_DAYS_THRESHOLD:
            excess_days = inactivity_days - RUST_DAYS_THRESHOLD
            decay_factor = 2.718281828459045 ** (-RUST_DECAY_RATE * excess_days)
            blended_elo = DEFAULT_ELO + (blended_elo - DEFAULT_ELO) * decay_factor

        return round(blended_elo, 2), round(w_surf, 4), inactivity_days

    def forecast_match(
        self,
        player_one: str,
        player_two: str,
        surface: str,
        as_of_date: str,
        match_format: str = "Bo3",
    ) -> TennisMatchForecast:
        """Generate match forecast using dynamic surface shrinkage & format translation."""
        profiles = self.compute_player_profiles(as_of_date)
        prof_1 = profiles.get(player_one)
        prof_2 = profiles.get(player_two)

        elo_1, w_1, days_1 = self.evaluate_effective_elo(prof_1, surface, as_of_date)
        elo_2, w_2, days_2 = self.evaluate_effective_elo(prof_2, surface, as_of_date)

        # Baseline single set probability from Elo
        p_set_1 = expected_win_probability(elo_1, elo_2)

        # Transform to match probability based on format
        if match_format.upper() in ["BO5", "BEST_OF_5", "GRAND_SLAM"]:
            p_match_1 = set_to_match_prob_bo5(p_set_1)
        else:
            p_match_1 = set_to_match_prob_bo3(p_set_1)

        p_match_2 = 1.0 - p_match_1

        return TennisMatchForecast(
            player_one=player_one,
            player_two=player_two,
            surface=surface,
            format=match_format,
            p_player_one_win=round(p_match_1, 4),
            p_player_two_win=round(p_match_2, 4),
            p_set_win_one=round(p_set_1, 4),
            p1_blended_elo=elo_1,
            p2_blended_elo=elo_2,
            p1_surface_weight=w_1,
            p2_surface_weight=w_2,
            p1_inactivity_days=days_1,
            p2_inactivity_days=days_2,
        )
