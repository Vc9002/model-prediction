"""WNBA v5 Possession x Points-per-Possession (PPP) fundamental model.

Decomposes basketball game dynamics into fundamental possession and efficiency metrics:
1. Pace (Possessions per 40 minutes):
   Possessions = 0.5 * ((FGA + 0.44*FTA - OREB + TOV) + (OppFGA + 0.44*OppFTA - OppOREB + OppTOV))
2. Offensive Rating (ORtg = 100 * Points / Possessions)
3. Defensive Rating (DRtg = 100 * OppPoints / Possessions)
4. Empirical Bayes shrinkage toward WNBA baseline (Pace ~ 79.5, ORtg ~ 102.5)
5. Expected Points per team:
   mu_home = Expected_Pace * (ORtg_home * DRtg_away / LgAvg_ORtg) / 100 + HFA_points
   mu_away = Expected_Pace * (ORtg_away * DRtg_home / LgAvg_ORtg) / 100
6. Gaussian score distribution deriving:
   - Moneyline win probability
   - Point spread coverage P(Home Margin > Spread)
   - Over/Under total distribution P(Total Points > Line)

Strict Point-In-Time (PIT) Invariant:
    Team efficiency and pace ratings are computed strictly from games completed prior to
    event_start_utc / game_date T.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import erf, sqrt

# WNBA League Priors
WNBA_LEAGUE_PACE = 79.5  # Possessions per 40 min
WNBA_LEAGUE_ORTG = 102.5  # Points per 100 possessions
WNBA_LEAGUE_DRTG = 102.5
WNBA_HOME_ADVANTAGE_PTS = 2.4  # Points of home court advantage
WNBA_MARGIN_SD = 11.2  # Standard deviation of margin
WNBA_TOTAL_SD = 13.8  # Standard deviation of total points

PACE_STABILIZATION_GAMES = 10.0
RATING_STABILIZATION_POSS = 800.0  # ~10 games * 80 poss


def _parse_date(date_str: str) -> datetime:
    """Parse date or ISO timestamp string into a timezone-aware UTC datetime."""
    clean = date_str.replace("Z", "+00:00").split("T")[0]
    return datetime.strptime(clean, "%Y-%m-%d").replace(tzinfo=UTC)


def _is_strictly_before(record_date: str, as_of_date: str) -> bool:
    """Check if record_date is strictly before as_of_date."""
    if record_date == as_of_date:
        return False
    if ("T" in record_date or " " in record_date) and ("T" in as_of_date or " " in as_of_date):
        return record_date < as_of_date
    return _parse_date(record_date).date() < _parse_date(as_of_date).date()


def _normal_cdf(x: float, mean: float, sd: float) -> float:
    """Cumulative distribution function for N(mean, sd^2)."""
    if sd <= 0:
        return 1.0 if x >= mean else 0.0
    return 0.5 * (1.0 + erf((x - mean) / (sd * sqrt(2.0))))


@dataclass(slots=True)
class WNBAGameBoxscore:
    """Point-in-time boxscore record of a completed WNBA game for one team."""

    game_id: str
    game_date: str  # YYYY-MM-DD or ISO timestamp
    team_id: str
    opponent_id: str
    is_home: bool
    points_scored: int
    points_allowed: int
    fga: int
    fta: int
    oreb: int
    tov: int
    opp_fga: int = 0
    opp_fta: int = 0
    opp_oreb: int = 0
    opp_tov: int = 0
    minutes_played: float = 40.0

    @property
    def possessions(self) -> float:
        """Estimate possessions from two-way or single-team boxscore components."""
        team_poss = self.fga + 0.44 * self.fta - self.oreb + self.tov
        if self.opp_fga > 0:
            opp_poss = self.opp_fga + 0.44 * self.opp_fta - self.opp_oreb + self.opp_tov
            return max(10.0, 0.5 * (team_poss + opp_poss))
        return max(10.0, float(team_poss))


@dataclass(slots=True)
class WNBATeamState:
    """Shrunk point-in-time pace and efficiency ratings for a team."""

    team_id: str
    as_of_date: str
    games_played: int
    total_possessions: float
    pace_per_40: float  # Shrunk pace (possessions / 40 min)
    ortg: float  # Shrunk offensive rating (pts / 100 poss)
    drtg: float  # Shrunk defensive rating (pts allowed / 100 poss)
    net_rating: float  # ortg - drtg
    raw_pace: float
    raw_ortg: float
    raw_drtg: float


@dataclass(slots=True)
class WNBAGameForecast:
    """Game forecast derived from possession x PPP model."""

    home_team: str
    away_team: str
    as_of_date: str
    expected_pace: float
    expected_home_points: float
    expected_away_points: float
    expected_total: float
    expected_margin: float  # home - away
    p_home_win: float  # Moneyline probability
    p_away_win: float
    home_state: WNBATeamState
    away_state: WNBATeamState

    def p_cover_spread(self, spread_away: float) -> float:
        """Probability that Home covers spread_away (e.g. spread_away = +4.5 means Home -4.5)."""
        # Home covers if (home_points - away_points) > spread_away (or margin > -spread_away)
        target_margin = -spread_away
        return 1.0 - _normal_cdf(target_margin, self.expected_margin, WNBA_MARGIN_SD)

    def p_over_total(self, total_line: float) -> float:
        """Probability that game total exceeds total_line."""
        return 1.0 - _normal_cdf(total_line, self.expected_total, WNBA_TOTAL_SD)

    def p_under_total(self, total_line: float) -> float:
        """Probability that game total stays under total_line."""
        return _normal_cdf(total_line, self.expected_total, WNBA_TOTAL_SD)


class WNBAPossessionEngine:
    """Point-in-time WNBA possession and efficiency engine."""

    def __init__(
        self,
        league_pace: float = WNBA_LEAGUE_PACE,
        league_ortg: float = WNBA_LEAGUE_ORTG,
        home_advantage: float = WNBA_HOME_ADVANTAGE_PTS,
    ) -> None:
        self.league_pace = league_pace
        self.league_ortg = league_ortg
        self.league_drtg = league_ortg
        self.home_advantage = home_advantage
        self._boxscores: dict[str, list[WNBAGameBoxscore]] = {}

    def record_boxscore(self, box: WNBAGameBoxscore) -> None:
        """Record a game boxscore sequentially."""
        self._boxscores.setdefault(box.team_id, []).append(box)

    def evaluate_team_state(self, team_id: str, as_of_date: str) -> WNBATeamState:
        """Compute point-in-time team pace and efficiency ratings strictly before as_of_date."""
        recs = [b for b in self._boxscores.get(team_id, []) if _is_strictly_before(b.game_date, as_of_date)]

        if not recs:
            return WNBATeamState(
                team_id=team_id,
                as_of_date=as_of_date,
                games_played=0,
                total_possessions=0.0,
                pace_per_40=self.league_pace,
                ortg=self.league_ortg,
                drtg=self.league_drtg,
                net_rating=0.0,
                raw_pace=self.league_pace,
                raw_ortg=self.league_ortg,
                raw_drtg=self.league_drtg,
            )

        total_pts = sum(b.points_scored for b in recs)
        total_opp_pts = sum(b.points_allowed for b in recs)
        total_poss = sum(b.possessions for b in recs)
        total_min = sum(b.minutes_played for b in recs)

        raw_pace = 40.0 * (total_poss / total_min) if total_min > 0 else self.league_pace
        raw_ortg = 100.0 * (total_pts / total_poss) if total_poss > 0 else self.league_ortg
        raw_drtg = 100.0 * (total_opp_pts / total_poss) if total_poss > 0 else self.league_drtg

        # Empirical Bayes shrinkage
        n_games = len(recs)
        w_pace = n_games / (n_games + PACE_STABILIZATION_GAMES)
        shrunk_pace = w_pace * raw_pace + (1.0 - w_pace) * self.league_pace

        w_rtg = total_poss / (total_poss + RATING_STABILIZATION_POSS)
        shrunk_ortg = w_rtg * raw_ortg + (1.0 - w_rtg) * self.league_ortg
        shrunk_drtg = w_rtg * raw_drtg + (1.0 - w_rtg) * self.league_drtg

        return WNBATeamState(
            team_id=team_id,
            as_of_date=as_of_date,
            games_played=n_games,
            total_possessions=round(total_poss, 1),
            pace_per_40=round(shrunk_pace, 2),
            ortg=round(shrunk_ortg, 2),
            drtg=round(shrunk_drtg, 2),
            net_rating=round(shrunk_ortg - shrunk_drtg, 2),
            raw_pace=round(raw_pace, 2),
            raw_ortg=round(raw_ortg, 2),
            raw_drtg=round(raw_drtg, 2),
        )

    def forecast_game(
        self,
        home_team: str,
        away_team: str,
        as_of_date: str,
    ) -> WNBAGameForecast:
        """Generate full game forecast from team possession and efficiency states."""
        h_state = self.evaluate_team_state(home_team, as_of_date)
        a_state = self.evaluate_team_state(away_team, as_of_date)

        # Expected game pace: geometric / multiplicative pace interaction
        exp_pace = (h_state.pace_per_40 * a_state.pace_per_40) / self.league_pace

        # Expected Points Per 100 Possessions
        # Home team offensive efficiency adjusted by Away team defensive efficiency
        exp_home_ppp100 = (h_state.ortg * a_state.drtg) / self.league_ortg
        exp_away_ppp100 = (a_state.ortg * h_state.drtg) / self.league_ortg

        # Expected Game Points (Pace * PPP / 100) + Home Court Advantage
        exp_home_pts = exp_pace * (exp_home_ppp100 / 100.0) + (self.home_advantage / 2.0)
        exp_away_pts = exp_pace * (exp_away_ppp100 / 100.0) - (self.home_advantage / 2.0)

        exp_total = exp_home_pts + exp_away_pts
        exp_margin = exp_home_pts - exp_away_pts

        # Win probability from margin normal CDF
        p_home = 1.0 - _normal_cdf(0.0, exp_margin, WNBA_MARGIN_SD)
        p_away = 1.0 - p_home

        return WNBAGameForecast(
            home_team=home_team,
            away_team=away_team,
            as_of_date=as_of_date,
            expected_pace=round(exp_pace, 2),
            expected_home_points=round(exp_home_pts, 2),
            expected_away_points=round(exp_away_pts, 2),
            expected_total=round(exp_total, 2),
            expected_margin=round(exp_margin, 2),
            p_home_win=round(p_home, 4),
            p_away_win=round(p_away, 4),
            home_state=h_state,
            away_state=a_state,
        )
