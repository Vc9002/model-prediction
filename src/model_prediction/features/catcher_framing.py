"""Point-in-time Statcast Catcher Framing feature engine.

Tracks catcher pitch-framing ability in the Statcast Shadow Zone (the 2-inch border
around the strike zone) to model called-strike probability leverage:
- Shadow zone called-strike rate above expected (CSAE)
- Empirical Bayes shrinkage toward neutral baseline (0.0 CSAE)
- Framing runs saved per 1,000 shadow takes (~+1.5 to +2.0 runs per 100 takes for elite catchers)
- Impact on NRFI (First Inning No Run) and Game Totals (strike call inflation suppresses offense)

Strict Point-In-Time (PIT) Invariant:
    A catcher framing state is computed strictly from games completed prior to
    event_start_utc / game_date T.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

# MLB League Average Framing Priors
LEAGUE_FRAMING_PRIOR_CSAE = 0.0  # 0.0% called strike above expected
LEAGUE_FRAMING_PRIOR_TAKES = 200.0  # Takes stabilization weight (~15-20 games)


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


@dataclass(slots=True)
class CatcherGameRecord:
    """Point-in-time record of an individual catcher game performance."""

    catcher_id: str
    game_date: str  # YYYY-MM-DD or ISO timestamp
    team_id: str = ""
    player_name: str = ""
    shadow_zone_takes: int = 0  # Total takes by batters in the Statcast shadow zone
    called_strikes_obtained: int = 0  # Actual called strikes awarded
    expected_called_strikes: float = 0.0  # Expected strikes based on pitch trajectory/coordinates
    innings_caught: float = 9.0


@dataclass(slots=True)
class CatcherFramingMetrics:
    """Shrunk point-in-time catcher framing metrics."""

    catcher_id: str
    games_caught: int
    innings_caught: float
    shadow_zone_takes: int
    raw_csae: float  # Raw called-strike above expected
    shrunk_csae: float  # Empirical Bayes shrunk CSAE (-0.05 to +0.05)
    framing_strike_rate_delta: float  # Shrunk strike probability delta on border pitches
    estimated_runs_per_game: float  # Runs saved/cost per 9 innings (~ -0.15 to +0.15)


@dataclass(slots=True)
class CatcherFramingMatchupAdvantage:
    """Comparative catcher framing advantage between home and away teams."""

    home_catcher_id: str
    away_catcher_id: str
    as_of_date: str
    home_csae: float
    away_csae: float
    csae_differential: float  # home_csae - away_csae (positive = home catcher gets more calls)
    total_strike_rate_leverage: float  # (home_csae + away_csae) / 2 -> positive compresses totals
    home_metrics: CatcherFramingMetrics
    away_metrics: CatcherFramingMetrics


class CatcherFramingAccumulator:
    """Accumulates catcher performances and computes Empirical Bayes shrunk framing metrics."""

    def __init__(self, stabilization_takes: float = LEAGUE_FRAMING_PRIOR_TAKES) -> None:
        self.stabilization_takes = stabilization_takes

    def compute_metrics(
        self,
        catcher_id: str,
        records: Sequence[CatcherGameRecord],
    ) -> CatcherFramingMetrics:
        """Compute shrunk framing metrics for a given sequence of catcher outings."""
        if not records:
            return CatcherFramingMetrics(
                catcher_id=catcher_id,
                games_caught=0,
                innings_caught=0.0,
                shadow_zone_takes=0,
                raw_csae=0.0,
                shrunk_csae=0.0,
                framing_strike_rate_delta=0.0,
                estimated_runs_per_game=0.0,
            )

        total_takes = sum(r.shadow_zone_takes for r in records)
        total_strikes = sum(r.called_strikes_obtained for r in records)
        total_expected_strikes = sum(r.expected_called_strikes for r in records)
        total_innings = sum(r.innings_caught for r in records)

        if total_takes > 0:
            raw_csae = (total_strikes - total_expected_strikes) / total_takes
        else:
            raw_csae = 0.0

        # Empirical Bayes shrinkage: w = n / (n + M)
        w = total_takes / (total_takes + self.stabilization_takes)
        shrunk_csae = w * raw_csae + (1.0 - w) * LEAGUE_FRAMING_PRIOR_CSAE

        # A +1% CSAE represents roughly ~0.12 runs saved per 100 shadow takes (~10 takes per game -> 0.012 R/game)
        # For full 9 innings (~12 shadow takes per game on average):
        runs_per_game = shrunk_csae * 12.0 * 0.12

        return CatcherFramingMetrics(
            catcher_id=catcher_id,
            games_caught=len(records),
            innings_caught=round(total_innings, 1),
            shadow_zone_takes=total_takes,
            raw_csae=round(raw_csae, 4),
            shrunk_csae=round(shrunk_csae, 4),
            framing_strike_rate_delta=round(shrunk_csae, 4),
            estimated_runs_per_game=round(runs_per_game, 4),
        )


class PointInTimeCatcherFramingEngine:
    """Point-in-time manager for catcher framing logs and game matchup evaluations."""

    def __init__(self, accumulator: CatcherFramingAccumulator | None = None) -> None:
        self.accumulator = accumulator or CatcherFramingAccumulator()
        self._records: dict[str, list[CatcherGameRecord]] = {}
        self._team_catchers: dict[str, str] = {}  # team_id -> most recent primary catcher

    def record_catcher_game(self, record: CatcherGameRecord) -> None:
        """Add a catcher game outing sequentially."""
        self._records.setdefault(record.catcher_id, []).append(record)
        if record.team_id:
            self._team_catchers[record.team_id] = record.catcher_id

    def get_catcher_metrics(
        self,
        catcher_id: str,
        as_of_date: str,
    ) -> CatcherFramingMetrics:
        """Get point-in-time framing metrics for a catcher strictly before as_of_date."""
        all_recs = self._records.get(catcher_id, [])
        valid_recs = [r for r in all_recs if _is_strictly_before(r.game_date, as_of_date)]
        return self.accumulator.compute_metrics(catcher_id, valid_recs)

    def evaluate_matchup(
        self,
        home_catcher_id: str,
        away_catcher_id: str,
        as_of_date: str,
    ) -> CatcherFramingMatchupAdvantage:
        """Evaluate head-to-head framing differential between home and away starting catchers."""
        home_m = self.get_catcher_metrics(home_catcher_id, as_of_date)
        away_m = self.get_catcher_metrics(away_catcher_id, as_of_date)

        csae_diff = home_m.shrunk_csae - away_m.shrunk_csae
        total_strike_leverage = (home_m.shrunk_csae + away_m.shrunk_csae) / 2.0

        return CatcherFramingMatchupAdvantage(
            home_catcher_id=home_catcher_id,
            away_catcher_id=away_catcher_id,
            as_of_date=as_of_date,
            home_csae=home_m.shrunk_csae,
            away_csae=away_m.shrunk_csae,
            csae_differential=round(csae_diff, 4),
            total_strike_rate_leverage=round(total_strike_leverage, 4),
            home_metrics=home_m,
            away_metrics=away_m,
        )
