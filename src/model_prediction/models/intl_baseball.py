"""International Baseball (KBO & NPB) Point-in-Time Starter & 12-Inning Tie Engine for Polymarket US.

Mathematical Specifications:
1. League Environments & Baselines:
   - KBO: High-scoring environment (mean runs ~9.6/game, home advantage ~53.0%, tie rate ~3.5% after 12 inn).
   - NPB: Pitcher-dominated environment (mean runs ~6.8/game, home advantage ~53.5%, tie rate ~7.5% after 12 inn).
2. Starting Pitcher Sabermetrics (FIP / K-BB%):
   Empirical Bayes shrinkage on starter K% and BB% over BF (stabilization ~150 BF):
   FIP = ((13*HR + 3*(BB+HBP) - 2*K) / IP) + c_FIP
3. 12-Inning Tie Settlement Invariant for Polymarket US:
   Polymarket US rules settle international baseball ties to 0.50 (half payout).
   Expected Contract Value:
   E[Payout_Home] = P(Home_Win) + 0.5 * P(Tie)
   E[Payout_Away] = P(Away_Win) + 0.5 * P(Tie)
4. Strict Point-in-Time (PIT) chronological separation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

KBO_LEAGUE_RUNS_PER_GAME = 9.6
KBO_TIE_RATE = 0.035
NPB_LEAGUE_RUNS_PER_GAME = 6.8
NPB_TIE_RATE = 0.075


def _parse_date(date_str: str) -> datetime:
    """Parse date or ISO timestamp string into UTC datetime."""
    clean = date_str.replace("Z", "+00:00").split("T")[0]
    return datetime.strptime(clean, "%Y-%m-%d").replace(tzinfo=UTC)


def _is_strictly_before(record_date: str, as_of_date: str) -> bool:
    """Check if record_date is strictly before as_of_date."""
    if record_date == as_of_date:
        return False
    if ("T" in record_date or " " in record_date) and ("T" in as_of_date or " " in as_of_date):
        return record_date < as_of_date
    return _parse_date(record_date).date() < _parse_date(as_of_date).date()


@dataclass(slots=True)
class IntlStarterRecord:
    """Single game outing record for a KBO/NPB starting pitcher."""

    pitcher_id: str
    game_date: str
    batters_faced: int
    innings_pitched: float
    strikeouts: int
    walks: int
    home_runs: int
    earned_runs: int


@dataclass(slots=True)
class IntlStarterState:
    """Shrunk sabermetric state for an international starting pitcher."""

    pitcher_id: str
    as_of_date: str
    games: int
    total_bf: int
    total_ip: float
    shrunk_k_pct: float
    shrunk_bb_pct: float
    shrunk_fip: float
    k_minus_bb_pct: float


@dataclass(slots=True)
class IntlBaseballForecast:
    """Point-in-time forecast for a KBO/NPB game with Polymarket US tie pricing."""

    league: str  # "KBO" or "NPB"
    home_team: str
    away_team: str
    p_home_win: float
    p_away_win: float
    p_tie: float
    expected_payout_home: float  # P(Home_Win) + 0.5 * P(Tie)
    expected_payout_away: float  # P(Away_Win) + 0.5 * P(Tie)
    expected_total_runs: float
    home_starter_fip: float
    away_starter_fip: float


class IntlBaseballEngine:
    """International baseball forecasting engine supporting KBO and NPB."""

    def __init__(self, league: str = "KBO", stabilization_bf: float = 150.0) -> None:
        self.league = league.upper()
        self.stabilization_bf = stabilization_bf
        self.league_runs = KBO_LEAGUE_RUNS_PER_GAME if self.league == "KBO" else NPB_LEAGUE_RUNS_PER_GAME
        self.baseline_tie_rate = KBO_TIE_RATE if self.league == "KBO" else NPB_TIE_RATE
        self.league_k_pct = 0.190 if self.league == "KBO" else 0.205
        self.league_bb_pct = 0.085 if self.league == "KBO" else 0.070
        self.league_fip = 4.20 if self.league == "KBO" else 3.40
        self._starter_logs: dict[str, list[IntlStarterRecord]] = {}

    def record_starter_outing(self, outing: IntlStarterRecord) -> None:
        """Record a starting pitcher outing chronologically."""
        self._starter_logs.setdefault(outing.pitcher_id, []).append(outing)

    def evaluate_starter(self, pitcher_id: str, as_of_date: str) -> IntlStarterState:
        """Evaluate point-in-time starter metrics strictly before as_of_date."""
        logs = [
            r for r in self._starter_logs.get(pitcher_id, []) if _is_strictly_before(r.game_date, as_of_date)
        ]

        if not logs:
            return IntlStarterState(
                pitcher_id=pitcher_id,
                as_of_date=as_of_date,
                games=0,
                total_bf=0,
                total_ip=0.0,
                shrunk_k_pct=self.league_k_pct,
                shrunk_bb_pct=self.league_bb_pct,
                shrunk_fip=self.league_fip,
                k_minus_bb_pct=round(self.league_k_pct - self.league_bb_pct, 4),
            )

        n_games = len(logs)
        total_bf = sum(r.batters_faced for r in logs)
        total_ip = sum(r.innings_pitched for r in logs)
        total_k = sum(r.strikeouts for r in logs)
        total_bb = sum(r.walks for r in logs)
        total_hr = sum(r.home_runs for r in logs)

        raw_k_pct = total_k / total_bf if total_bf > 0 else self.league_k_pct
        raw_bb_pct = total_bb / total_bf if total_bf > 0 else self.league_bb_pct

        # Empirical Bayes shrinkage
        w = total_bf / (total_bf + self.stabilization_bf)
        shrunk_k = w * raw_k_pct + (1.0 - w) * self.league_k_pct
        shrunk_bb = w * raw_bb_pct + (1.0 - w) * self.league_bb_pct

        # Estimate FIP
        if total_ip >= 5.0:
            raw_fip = ((13.0 * total_hr + 3.0 * total_bb - 2.0 * total_k) / total_ip) + 3.15
            shrunk_fip = w * raw_fip + (1.0 - w) * self.league_fip
        else:
            shrunk_fip = self.league_fip

        return IntlStarterState(
            pitcher_id=pitcher_id,
            as_of_date=as_of_date,
            games=n_games,
            total_bf=total_bf,
            total_ip=round(total_ip, 1),
            shrunk_k_pct=round(shrunk_k, 4),
            shrunk_bb_pct=round(shrunk_bb, 4),
            shrunk_fip=round(shrunk_fip, 2),
            k_minus_bb_pct=round(shrunk_k - shrunk_bb, 4),
        )

    def forecast_matchup(
        self,
        home_team: str,
        away_team: str,
        home_sp_id: str,
        away_sp_id: str,
        as_of_date: str,
    ) -> IntlBaseballForecast:
        """Forecast KBO/NPB matchup including 0.50 Polymarket US tie payoff contract pricing."""
        sp_home = self.evaluate_starter(home_sp_id, as_of_date)
        sp_away = self.evaluate_starter(away_sp_id, as_of_date)

        # Baseline home win probability (home advantage ~53.2%)
        p_home_base = 0.532

        # Starter FIP differential effect (~2.8% win prob shift per 1.0 FIP gap)
        fip_diff = sp_away.shrunk_fip - sp_home.shrunk_fip
        kbb_diff = sp_home.k_minus_bb_pct - sp_away.k_minus_bb_pct

        p_home_decided = p_home_base + (fip_diff * 0.028) + (kbb_diff * 0.15)
        p_home_decided = max(0.20, min(0.80, p_home_decided))

        # Tie probability (higher in low-scoring NPB than high-scoring KBO)
        p_tie = self.baseline_tie_rate

        # Decided outcomes sum to 1.0 - p_tie
        p_home_win = p_home_decided * (1.0 - p_tie)
        p_away_win = (1.0 - p_home_decided) * (1.0 - p_tie)

        # Polymarket US 0.50 Tie Settlement Expected Value:
        # Contract pays 1.00 on win, 0.50 on tie, 0.00 on loss
        ev_payout_home = p_home_win + 0.5 * p_tie
        ev_payout_away = p_away_win + 0.5 * p_tie

        # Expected game total runs
        sp_fip_avg = 0.5 * (sp_home.shrunk_fip + sp_away.shrunk_fip)
        exp_runs = self.league_runs * (sp_fip_avg / self.league_fip)

        return IntlBaseballForecast(
            league=self.league,
            home_team=home_team,
            away_team=away_team,
            p_home_win=round(p_home_win, 4),
            p_away_win=round(p_away_win, 4),
            p_tie=round(p_tie, 4),
            expected_payout_home=round(ev_payout_home, 4),
            expected_payout_away=round(ev_payout_away, 4),
            expected_total_runs=round(exp_runs, 2),
            home_starter_fip=sp_home.shrunk_fip,
            away_starter_fip=sp_away.shrunk_fip,
        )
