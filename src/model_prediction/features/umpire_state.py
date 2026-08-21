"""MLB Point-in-Time Home Plate Umpire Strike Zone Feature Engine.

Calculates point-in-time umpire strike-zone tendencies and run-environment adjustments:
1. Called-Strike Rate Above Expected (CSAE):
   CSAE = Called_Strikes / Pitches_In_Shadow_Zone - Baseline_Expected_Rate
2. Umpire Run Multiplier (R_ump) affecting Totals & NRFI:
   Tight zone -> higher walks & runs (R_ump > 1.0)
   Wide / generous zone -> higher strikeouts & lower runs (R_ump < 1.0)
3. Empirical Bayes shrinkage toward neutral baseline (R_ump = 1.0, CSAE = 0.0)
   based on sample size (stabilization threshold ~30 games).
4. Strict Point-in-Time (PIT) Invariant:
   Umpire metrics computed strictly from games completed prior to event_start_utc.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

LEAGUE_NEUTRAL_CSAE = 0.0
LEAGUE_NEUTRAL_RUN_FACTOR = 1.0
LEAGUE_NEUTRAL_K_FACTOR = 1.0
LEAGUE_NEUTRAL_BB_FACTOR = 1.0
UMPIRE_STABILIZATION_GAMES = 30.0


def _parse_date(date_str: str) -> datetime:
    """Parse date or ISO timestamp into UTC datetime."""
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
class UmpireGameRecord:
    """Point-in-time single-game plate performance of a home-plate umpire."""

    game_id: str
    game_date: str  # YYYY-MM-DD or ISO timestamp
    umpire_id: str
    umpire_name: str
    total_pitches: int
    called_pitches: int
    called_strikes: int
    expected_called_strikes: float
    total_runs_scored: int
    total_strikeouts: int
    total_walks: int


@dataclass(slots=True)
class UmpireZoneState:
    """Shrunk point-in-time strike zone metrics for a home plate umpire."""

    umpire_id: str
    umpire_name: str
    as_of_date: str
    games_umpired: int
    total_called_pitches: int
    csae: float  # Called strike rate above expected (e.g. +0.02 = 2% more strikes)
    run_factor: float  # Multiplier on expected runs (e.g. 0.96 for pitcher-friendly)
    k_factor: float  # Multiplier on strikeout rate
    bb_factor: float  # Multiplier on walk rate
    raw_csae: float
    raw_run_factor: float


class PointInTimeUmpireEngine:
    """Accumulates point-in-time umpire logs and evaluates Empirical Bayes zone factors."""

    def __init__(self, stabilization_games: float = UMPIRE_STABILIZATION_GAMES) -> None:
        self.stabilization_games = stabilization_games
        self._records: dict[str, list[UmpireGameRecord]] = {}

    def record_game(self, rec: UmpireGameRecord) -> None:
        """Record an umpire game log chronologically."""
        self._records.setdefault(rec.umpire_id, []).append(rec)

    def record_games(self, records: Sequence[UmpireGameRecord]) -> None:
        """Batch record umpire game logs."""
        for r in records:
            self.record_game(r)

    def evaluate_umpire(
        self,
        umpire_id: str,
        as_of_date: str,
        umpire_name: str = "",
    ) -> UmpireZoneState:
        """Evaluate point-in-time umpire zone metrics strictly before as_of_date."""
        logs = [r for r in self._records.get(umpire_id, []) if _is_strictly_before(r.game_date, as_of_date)]

        if not logs:
            return UmpireZoneState(
                umpire_id=umpire_id,
                umpire_name=umpire_name,
                as_of_date=as_of_date,
                games_umpired=0,
                total_called_pitches=0,
                csae=LEAGUE_NEUTRAL_CSAE,
                run_factor=LEAGUE_NEUTRAL_RUN_FACTOR,
                k_factor=LEAGUE_NEUTRAL_K_FACTOR,
                bb_factor=LEAGUE_NEUTRAL_BB_FACTOR,
                raw_csae=LEAGUE_NEUTRAL_CSAE,
                raw_run_factor=LEAGUE_NEUTRAL_RUN_FACTOR,
            )

        n_games = len(logs)
        total_called = sum(r.called_pitches for r in logs)
        total_cs = sum(r.called_strikes for r in logs)
        total_exp_cs = sum(r.expected_called_strikes for r in logs)
        total_runs = sum(r.total_runs_scored for r in logs)

        raw_csae = (total_cs - total_exp_cs) / total_called if total_called > 0 else 0.0
        # Expected runs per game standard is ~8.8
        exp_runs_total = 8.8 * n_games
        raw_run_factor = total_runs / exp_runs_total if exp_runs_total > 0 else 1.0

        # Empirical Bayes shrinkage
        w = n_games / (n_games + self.stabilization_games)
        shrunk_csae = w * raw_csae + (1.0 - w) * LEAGUE_NEUTRAL_CSAE
        shrunk_run_factor = w * raw_run_factor + (1.0 - w) * LEAGUE_NEUTRAL_RUN_FACTOR

        # Zone effect on Ks and BBs
        # +1% CSAE -> +2.5% Ks, -3.5% BBs
        shrunk_k_factor = 1.0 + (shrunk_csae * 2.5)
        shrunk_bb_factor = 1.0 - (shrunk_csae * 3.5)

        name = umpire_name or logs[-1].umpire_name

        return UmpireZoneState(
            umpire_id=umpire_id,
            umpire_name=name,
            as_of_date=as_of_date,
            games_umpired=n_games,
            total_called_pitches=total_called,
            csae=round(shrunk_csae, 4),
            run_factor=round(shrunk_run_factor, 4),
            k_factor=round(shrunk_k_factor, 4),
            bb_factor=round(shrunk_bb_factor, 4),
            raw_csae=round(raw_csae, 4),
            raw_run_factor=round(raw_run_factor, 4),
        )
