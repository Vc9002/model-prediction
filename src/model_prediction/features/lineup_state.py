"""Point-in-time confirmed and projected lineup aggregation engine.

Combines prospective lineup feeds with Empirical Bayes batter priors to produce
batting-order-weighted and platoon-adjusted lineup talent vectors.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from model_prediction.features.batter_priors import (
    LineupPriorVector,
    PointInTimeBatterPriorEngine,
)


@dataclass(slots=True)
class LineupBatter:
    player_id: str
    batting_order: int  # 1 through 9
    position: str = "DH"
    bats: str = "R"  # "R", "L", "S"


@dataclass(slots=True)
class ConfirmedLineup:
    team_id: str
    game_date: str  # YYYY-MM-DD
    is_confirmed: bool = True
    batters: list[LineupBatter] = field(default_factory=list)


@dataclass(slots=True)
class LineupAdvantageVector:
    home_xwoba: float
    away_xwoba: float
    xwoba_gap: float
    k_pct_gap: float
    bb_pct_gap: float
    iso_gap: float
    barrel_gap: float
    hard_hit_gap: float
    is_confirmed: bool


class LineupStateEngine:
    """Evaluates matchup lineup talent and differential advantages."""

    def __init__(self, batter_prior_engine: PointInTimeBatterPriorEngine) -> None:
        self.priors = batter_prior_engine
        self._lineups: dict[tuple[str, str], ConfirmedLineup] = {}

    def register_confirmed_lineup(self, lineup: ConfirmedLineup) -> None:
        key = (lineup.team_id, lineup.game_date)
        self._lineups[key] = lineup

    def evaluate_team_lineup(
        self,
        team_id: str,
        game_date: str,
        opposing_pitcher_hand: str | None = None,
    ) -> tuple[LineupPriorVector, bool]:
        """Return the LineupPriorVector and whether the lineup was confirmed."""
        key = (team_id, game_date)
        if key in self._lineups and len(self._lineups[key].batters) == 9:
            lineup = self._lineups[key]
            # Sort by batting order 1..9
            sorted_batters = sorted(lineup.batters, key=lambda b: b.batting_order)
            player_ids = [b.player_id for b in sorted_batters]
            prior_vec = self.priors.evaluate_confirmed_lineup(
                player_ids,
                as_of_date=game_date,
                opposing_pitcher_hand=opposing_pitcher_hand,
            )
            return prior_vec, True

        # Fallback to projected offense from preceding games
        prior_vec = self.priors.evaluate_projected_team_offense(
            team_id=team_id,
            as_of_date=game_date,
            opposing_pitcher_hand=opposing_pitcher_hand,
        )
        return prior_vec, False

    def evaluate_matchup(
        self,
        home_team: str,
        away_team: str,
        game_date: str,
        home_pitcher_hand: str | None = None,
        away_pitcher_hand: str | None = None,
    ) -> LineupAdvantageVector:
        """Evaluate head-to-head lineup differential advantages."""
        # Home batters face away pitcher
        home_vec, home_conf = self.evaluate_team_lineup(
            home_team, game_date, opposing_pitcher_hand=away_pitcher_hand
        )
        # Away batters face home pitcher
        away_vec, away_conf = self.evaluate_team_lineup(
            away_team, game_date, opposing_pitcher_hand=home_pitcher_hand
        )

        return LineupAdvantageVector(
            home_xwoba=home_vec.xwoba,
            away_xwoba=away_vec.xwoba,
            xwoba_gap=round(home_vec.xwoba - away_vec.xwoba, 4),
            # Positive k_pct_gap means home batters strike out less than away batters
            k_pct_gap=round(away_vec.k_pct - home_vec.k_pct, 4),
            bb_pct_gap=round(home_vec.bb_pct - away_vec.bb_pct, 4),
            iso_gap=round(home_vec.iso - away_vec.iso, 4),
            barrel_gap=round(home_vec.barrel_pct - away_vec.barrel_pct, 4),
            hard_hit_gap=round(home_vec.hard_hit_pct - away_vec.hard_hit_pct, 4),
            is_confirmed=(home_conf and away_conf),
        )
