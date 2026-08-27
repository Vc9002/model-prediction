"""WNBA Hierarchical Player Impact & Lineup Engine (Roadmap Step 26).

Implements point-in-time player-level impact modeling for WNBA:
  1. Empirical Bayes player Offensive / Defensive Rating shrinkage
  2. Projected minutes distribution per player
  3. Aggregate lineup strength differential (home vs away)
  4. Missing starter / key player VORP injury penalty
  5. Player-weighted possession pace estimation
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WNBAPlayerProfile:
    """Point-in-time shrunk player profile."""

    player_name: str
    team_name: str
    minutes_per_game: float
    off_rating_shrunk: float  # Points produced per 100 possessions
    def_rating_shrunk: float  # Points allowed per 100 possessions
    net_rating: float  # off_rating - def_rating
    usage_rate: float
    true_shooting_pct: float
    pace_factor: float  # Player pace tendency relative to league (1.00 = baseline)
    sample_games: int


@dataclass(frozen=True, slots=True)
class WNBALineupImpact:
    """Lineup impact feature vector for a matchup."""

    home_lineup_rating: float  # Net points per 100 poss
    away_lineup_rating: float  # Net points per 100 poss
    lineup_net_advantage: float  # home - away
    home_starter_available_pct: float
    away_starter_available_pct: float
    injury_impact_gap: float  # Positive = away more depleted
    projected_pace: float  # Possessions per 40 min
    home_projected_ppp: float  # Points per possession (e.g. 1.05)
    away_projected_ppp: float  # Points per possession (e.g. 0.98)


# League baseline parameters (WNBA 2024-2026 empirical averages)
LEAGUE_OFF_RATING_PRIOR = 101.5
LEAGUE_DEF_RATING_PRIOR = 101.5
LEAGUE_PACE_PRIOR = 79.5  # Possessions per 40 min
PRIOR_WEIGHT_GAMES = 8.0  # Empirical Bayes pseudo-count


def shrink_player_rating(
    observed_off_rating: float,
    observed_def_rating: float,
    games_played: int,
    prior_weight: float = PRIOR_WEIGHT_GAMES,
) -> tuple[float, float, float]:
    """Empirical Bayes shrinkage of player ratings toward league baseline."""
    if games_played <= 0:
        return LEAGUE_OFF_RATING_PRIOR, LEAGUE_DEF_RATING_PRIOR, 0.0

    shrunk_off = (prior_weight * LEAGUE_OFF_RATING_PRIOR + games_played * observed_off_rating) / (
        prior_weight + games_played
    )
    shrunk_def = (prior_weight * LEAGUE_DEF_RATING_PRIOR + games_played * observed_def_rating) / (
        prior_weight + games_played
    )
    return round(shrunk_off, 2), round(shrunk_def, 2), round(shrunk_off - shrunk_def, 2)


def compute_lineup_impact(
    home_roster: list[WNBAPlayerProfile],
    away_roster: list[WNBAPlayerProfile],
    home_inactive_players: list[str] | None = None,
    away_inactive_players: list[str] | None = None,
) -> WNBALineupImpact:
    """Compute point-in-time aggregate lineup impact and projected PPP."""
    home_inactives = set(home_inactive_players or [])
    away_inactives = set(away_inactive_players or [])

    # Filter active players and sort by minutes share
    home_active = [p for p in home_roster if p.player_name not in home_inactives]
    away_active = [p for p in away_roster if p.player_name not in away_inactives]

    home_active.sort(key=lambda p: p.minutes_per_game, reverse=True)
    away_active.sort(key=lambda p: p.minutes_per_game, reverse=True)

    # Top-8 rotation weighting (200 player-minutes per 40 min game)
    def aggregate_rotation(
        active_players: list[WNBAPlayerProfile], all_players: list[WNBAPlayerProfile]
    ) -> tuple[float, float, float, float]:
        if not active_players:
            return 0.0, 1.0, 1.0, 1.0

        top_rotation = active_players[:8]
        raw_mins = sum(p.minutes_per_game for p in top_rotation) or 200.0
        normalized_weights = [min(36.0, p.minutes_per_game) / (raw_mins or 1.0) for p in top_rotation]
        weight_sum = sum(normalized_weights) or 1.0
        weights = [w / weight_sum for w in normalized_weights]

        net_rtg = sum(w * p.net_rating for w, p in zip(weights, top_rotation))
        pace_mult = sum(w * p.pace_factor for w, p in zip(weights, top_rotation))

        # Measure lost rotation minutes due to inactives
        all_expected_top5 = sum(p.minutes_per_game for p in all_players[:5]) or 150.0
        active_expected_top5 = (
            sum(p.minutes_per_game for p in active_players if p in all_players[:5]) or 150.0
        )
        starter_avail = active_expected_top5 / all_expected_top5

        return net_rtg, pace_mult, starter_avail, max(0.0, 1.0 - starter_avail)

    home_net, home_pace, home_avail, home_loss = aggregate_rotation(home_active, home_roster)
    away_net, away_pace, away_avail, away_loss = aggregate_rotation(away_active, away_roster)

    # Combined pace
    projected_pace = round(LEAGUE_PACE_PRIOR * ((home_pace + away_pace) / 2.0), 1)

    # Home court advantage (+3.2 net rating points per 100 poss)
    HFA_NET = 3.2
    home_adj_net = home_net + HFA_NET
    lineup_net_advantage = round(home_adj_net - away_net, 2)

    # Projected points per possession
    home_ppp = round((LEAGUE_OFF_RATING_PRIOR + home_net / 2.0 + HFA_NET / 2.0) / 100.0, 3)
    away_ppp = round((LEAGUE_OFF_RATING_PRIOR + away_net / 2.0 - HFA_NET / 2.0) / 100.0, 3)

    return WNBALineupImpact(
        home_lineup_rating=round(home_adj_net, 2),
        away_lineup_rating=round(away_net, 2),
        lineup_net_advantage=lineup_net_advantage,
        home_starter_available_pct=round(home_avail, 3),
        away_starter_available_pct=round(away_avail, 3),
        injury_impact_gap=round(away_loss - home_loss, 3),
        projected_pace=projected_pace,
        home_projected_ppp=home_ppp,
        away_projected_ppp=away_ppp,
    )
