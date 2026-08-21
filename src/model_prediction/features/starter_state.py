"""Point-in-time multidimensional starter pitching state engine.

MLB v9 Rich Starter State module inspired by Volodymyr4K/market-efficiency-lab.
Tracks point-in-time starter metrics across rolling windows (21-day, last-3 starts, season baseline):
- xwOBA allowed
- Strikeout rate (K%)
- Walk rate (BB%)
- K-BB%
- CSW% (Called Strikes + Whiffs %)
- First-pitch strike %
- Average start depth (Innings pitched per start)
- Pitch count efficiency (Pitches per IP)
- Fastball velocity and velocity drift delta
- Last-3 start form deltas (recent form vs season baseline)
- Handedness ('R' vs 'L') tracking

Strict Point-In-Time (PIT) Invariant:
    A starter's state is computed strictly from games completed prior to
    event_start_utc / game_date T. No game's own boxscore may enter its own prior state.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

# MLB league average prior constants for Empirical Bayes shrinkage
LEAGUE_STARTER_PRIORS: dict[str, tuple[float, float]] = {
    # metric: (prior_mean, shrinkage_sample_weight)
    "xwoba": (0.315, 150.0),  # PA weight
    "k_pct": (0.220, 70.0),  # PA weight
    "bb_pct": (0.080, 100.0),  # PA weight
    "csw_pct": (0.280, 250.0),  # Pitches weight
    "first_pitch_strike_pct": (0.600, 100.0),  # PA weight
    "avg_innings_per_start": (5.30, 5.0),  # Starts weight
    "pitches_per_ip": (16.50, 25.0),  # IP weight
    "fastball_velocity": (93.80, 150.0),  # Fastball pitches weight
}


def _parse_date(date_str: str) -> datetime:
    """Parse date or ISO timestamp string into a datetime object."""
    clean = date_str.replace("Z", "+00:00").split("T")[0]
    return datetime.strptime(clean, "%Y-%m-%d").replace(tzinfo=UTC)


def _is_strictly_before(record_date: str, as_of_date: str) -> bool:
    """Check if record_date is strictly before as_of_date."""
    if record_date == as_of_date:
        return False
    # If both have time components, compare directly
    if ("T" in record_date or " " in record_date) and ("T" in as_of_date or " " in as_of_date):
        return record_date < as_of_date
    return _parse_date(record_date).date() < _parse_date(as_of_date).date()


@dataclass(slots=True)
class StarterGameRecord:
    """Historical record of an individual starting pitching performance."""

    pitcher_id: str
    game_date: str  # YYYY-MM-DD or ISO timestamp
    team_id: str = ""
    player_name: str = ""
    throws: str = "R"  # "R" or "L"
    innings_pitched: float = 0.0
    pitches_thrown: int = 0
    batters_faced: int = 0
    strikeouts: int = 0
    walks: int = 0
    hits: int = 0
    doubles: int = 0
    triples: int = 0
    home_runs: int = 0
    hit_by_pitch: int = 0
    earned_runs: int = 0
    called_strikes: int = 0
    whiffs: int = 0
    first_pitch_strikes: int = 0
    fastball_velocity_avg: float | None = None
    fastball_pitches: int = 0
    xwoba_sum: float = 0.0


@dataclass(slots=True)
class StarterRollingMetrics:
    """Point-in-time aggregated pitching metrics with Empirical Bayes shrinkage."""

    starts_count: int
    innings_pitched: float
    pitches_thrown: int
    batters_faced: int
    xwoba: float
    k_pct: float
    bb_pct: float
    k_bb_pct: float
    csw_pct: float
    first_pitch_strike_pct: float
    avg_innings_per_start: float
    pitches_per_ip: float
    fastball_velocity: float
    is_shrunk: bool = True


@dataclass(slots=True)
class StarterPitcherState:
    """Complete multidimensional starter state including rolling windows and form deltas."""

    pitcher_id: str
    player_name: str
    throws: str  # "R" or "L"
    as_of_date: str
    total_career_starts: int

    # Rolling windows
    rolling_21d: StarterRollingMetrics
    season_metrics: StarterRollingMetrics
    last3_metrics: StarterRollingMetrics

    # Last-3 start form deltas (recent trend vs season baseline)
    delta_xwoba: float  # last3.xwoba - season.xwoba (negative = improvement)
    delta_k_pct: float  # last3.k_pct - season.k_pct (positive = improvement)
    delta_bb_pct: float  # last3.bb_pct - season.bb_pct (negative = improvement)
    delta_k_bb: float  # last3.k_bb_pct - season.k_bb_pct (positive = improvement)
    delta_csw_pct: float  # last3.csw_pct - season.csw_pct (positive = improvement)
    delta_first_pitch_strike_pct: float  # last3 - season (positive = improvement)
    delta_depth: float  # last3.avg_innings - season.avg_innings (positive = improvement)
    delta_pitch_efficiency: float  # last3.pitches_per_ip - season.pitches_per_ip (negative = more efficient)

    # Fastball velocity drift delta (21d rolling vs season baseline)
    velocity_drift_delta: float  # rolling_21d.fastball_velocity - season.fastball_velocity


@dataclass(slots=True)
class StarterMatchupAdvantage:
    """Comparative starter advantages for head-to-head matchup evaluation."""

    home_starter_id: str
    away_starter_id: str
    home_throws: str
    away_throws: str
    as_of_date: str

    # Direct metric differentials (positive indicates home starter advantage)
    xwoba_gap: float  # away_xwoba - home_xwoba (positive = home starter allows lower xwOBA)
    k_bb_gap: float  # home_k_bb - away_k_bb (positive = home starter strikes out more net of walks)
    csw_gap: float  # home_csw - away_csw (positive = home starter misses more bats)
    first_pitch_strike_gap: float  # home - away (positive = home starter gets ahead more)
    depth_gap: float  # home_depth - away_depth (positive = home starter goes deeper)
    pitch_efficiency_gap: float  # away_eff - home_eff (positive = home starter uses fewer pitches/IP)
    velocity_gap: float  # home_velo - away_velo
    velocity_drift_gap: float  # home_drift - away_drift

    # Form trend differentials
    form_k_bb_gap: float  # home_delta_k_bb - away_delta_k_bb
    form_xwoba_gap: float  # away_delta_xwoba - home_delta_xwoba (positive = home form improving faster)

    home_state: StarterPitcherState
    away_state: StarterPitcherState


class StarterStateAccumulator:
    """Accumulates starter performances and computes shrunk multidimensional metrics."""

    def __init__(self, priors: dict[str, tuple[float, float]] | None = None) -> None:
        self.priors = priors or LEAGUE_STARTER_PRIORS

    def compute_metrics(self, records: Sequence[StarterGameRecord]) -> StarterRollingMetrics:
        """Compute shrunk rolling metrics from a sequence of starter game records."""
        starts_count = len(records)
        total_ip = sum(r.innings_pitched for r in records)
        total_pitches = sum(r.pitches_thrown for r in records)
        total_bf = sum(r.batters_faced for r in records)
        total_k = sum(r.strikeouts for r in records)
        total_bb = sum(r.walks for r in records)
        total_csw = sum(r.called_strikes + r.whiffs for r in records)
        total_fps = sum(r.first_pitch_strikes for r in records)

        # Fastball velocity
        fb_pitches = sum(r.fastball_pitches for r in records if r.fastball_velocity_avg is not None)
        fb_velo_sum = sum(
            (r.fastball_velocity_avg or 0.0) * r.fastball_pitches
            for r in records
            if r.fastball_velocity_avg is not None
        )
        if fb_pitches == 0 and any(r.fastball_velocity_avg is not None for r in records):
            velo_records = [r for r in records if r.fastball_velocity_avg is not None]
            fb_velo_sum = sum(r.fastball_velocity_avg or 0.0 for r in velo_records) * 50.0
            fb_pitches = len(velo_records) * 50

        # xwOBA calculation / component estimation
        statcast_xwoba_sum = sum(r.xwoba_sum for r in records)
        woba_est_sum = sum(
            0.690 * r.walks
            + 0.720 * r.hit_by_pitch
            + 0.880 * max(0, r.hits - r.doubles - r.triples - r.home_runs)
            + 1.240 * r.doubles
            + 1.560 * r.triples
            + 2.070 * r.home_runs
            for r in records
        )
        if statcast_xwoba_sum > 0:
            combined_xwoba_sum = 0.5 * woba_est_sum + 0.5 * statcast_xwoba_sum
        else:
            combined_xwoba_sum = woba_est_sum

        # Empirical Bayes shrinkage towards league priors
        # xwOBA
        mu_xwoba, m_xwoba = self.priors["xwoba"]
        shrunk_xwoba = (combined_xwoba_sum + mu_xwoba * m_xwoba) / (total_bf + m_xwoba)

        # K%
        mu_k, m_k = self.priors["k_pct"]
        shrunk_k = (total_k + mu_k * m_k) / (total_bf + m_k)

        # BB%
        mu_bb, m_bb = self.priors["bb_pct"]
        shrunk_bb = (total_bb + mu_bb * m_bb) / (total_bf + m_bb)

        # K-BB%
        shrunk_k_bb = shrunk_k - shrunk_bb

        # CSW%
        mu_csw, m_csw = self.priors["csw_pct"]
        shrunk_csw = (total_csw + mu_csw * m_csw) / (total_pitches + m_csw)

        # First pitch strike %
        mu_fps, m_fps = self.priors["first_pitch_strike_pct"]
        shrunk_fps = (total_fps + mu_fps * m_fps) / (total_bf + m_fps)

        # Average innings per start
        mu_depth, m_depth = self.priors["avg_innings_per_start"]
        shrunk_depth = (total_ip + mu_depth * m_depth) / (starts_count + m_depth)

        # Pitches per IP
        mu_eff, m_eff = self.priors["pitches_per_ip"]
        shrunk_eff = (total_pitches + mu_eff * m_eff) / (total_ip + m_eff)

        # Fastball velocity
        mu_velo, m_velo = self.priors["fastball_velocity"]
        shrunk_velo = (fb_velo_sum + mu_velo * m_velo) / (fb_pitches + m_velo)

        return StarterRollingMetrics(
            starts_count=starts_count,
            innings_pitched=round(total_ip, 2),
            pitches_thrown=total_pitches,
            batters_faced=total_bf,
            xwoba=round(shrunk_xwoba, 4),
            k_pct=round(shrunk_k, 4),
            bb_pct=round(shrunk_bb, 4),
            k_bb_pct=round(shrunk_k_bb, 4),
            csw_pct=round(shrunk_csw, 4),
            first_pitch_strike_pct=round(shrunk_fps, 4),
            avg_innings_per_start=round(shrunk_depth, 2),
            pitches_per_ip=round(shrunk_eff, 2),
            fastball_velocity=round(shrunk_velo, 2),
            is_shrunk=True,
        )

    def evaluate_starter_state(
        self,
        pitcher_id: str,
        records: Sequence[StarterGameRecord],
        as_of_date: str,
        player_name: str = "",
        throws: str = "R",
    ) -> StarterPitcherState:
        """Compute the full multidimensional starter state strictly as-of as_of_date."""
        pit_records = [r for r in records if _is_strictly_before(r.game_date, as_of_date)]
        # Sort chronologically
        sorted_records = sorted(pit_records, key=lambda r: r.game_date)

        as_of_dt = _parse_date(as_of_date)
        season_year = str(as_of_dt.year)
        cutoff_21d = as_of_dt - timedelta(days=21)

        records_21d = [r for r in sorted_records if _parse_date(r.game_date) >= cutoff_21d]
        records_season = [r for r in sorted_records if r.game_date[:4] == season_year]
        if not records_season and sorted_records:
            # Fallback to career records if season is fresh
            records_season = sorted_records
        records_last3 = sorted_records[-3:] if len(sorted_records) >= 3 else sorted_records

        rolling_21d = self.compute_metrics(records_21d)
        season_metrics = self.compute_metrics(records_season)
        last3_metrics = self.compute_metrics(records_last3)

        # Form deltas (recent trend vs season baseline)
        delta_xwoba = round(last3_metrics.xwoba - season_metrics.xwoba, 4)
        delta_k_pct = round(last3_metrics.k_pct - season_metrics.k_pct, 4)
        delta_bb_pct = round(last3_metrics.bb_pct - season_metrics.bb_pct, 4)
        delta_k_bb = round(last3_metrics.k_bb_pct - season_metrics.k_bb_pct, 4)
        delta_csw_pct = round(last3_metrics.csw_pct - season_metrics.csw_pct, 4)
        delta_first_pitch_strike_pct = round(
            last3_metrics.first_pitch_strike_pct - season_metrics.first_pitch_strike_pct, 4
        )
        delta_depth = round(last3_metrics.avg_innings_per_start - season_metrics.avg_innings_per_start, 2)
        delta_pitch_efficiency = round(last3_metrics.pitches_per_ip - season_metrics.pitches_per_ip, 2)
        velocity_drift_delta = round(rolling_21d.fastball_velocity - season_metrics.fastball_velocity, 2)

        return StarterPitcherState(
            pitcher_id=pitcher_id,
            player_name=player_name or pitcher_id,
            throws=throws,
            as_of_date=as_of_date,
            total_career_starts=len(sorted_records),
            rolling_21d=rolling_21d,
            season_metrics=season_metrics,
            last3_metrics=last3_metrics,
            delta_xwoba=delta_xwoba,
            delta_k_pct=delta_k_pct,
            delta_bb_pct=delta_bb_pct,
            delta_k_bb=delta_k_bb,
            delta_csw_pct=delta_csw_pct,
            delta_first_pitch_strike_pct=delta_first_pitch_strike_pct,
            delta_depth=delta_depth,
            delta_pitch_efficiency=delta_pitch_efficiency,
            velocity_drift_delta=velocity_drift_delta,
        )


class PointInTimeStarterEngine:
    """Point-in-time starter pitching state engine managing sequential pitcher histories and matchups."""

    def __init__(self, accumulator: StarterStateAccumulator | None = None) -> None:
        self.accumulator = accumulator or StarterStateAccumulator()
        self._history: dict[str, list[StarterGameRecord]] = {}
        self._info: dict[str, dict[str, str]] = {}

    def update_starter_game(self, record: StarterGameRecord) -> None:
        """Record a starter's game performance sequentially."""
        self._history.setdefault(record.pitcher_id, []).append(record)
        existing_info = self._info.get(record.pitcher_id, {})
        self._info[record.pitcher_id] = {
            "player_name": record.player_name or existing_info.get("player_name", record.pitcher_id),
            "throws": record.throws or existing_info.get("throws", "R"),
            "team_id": record.team_id or existing_info.get("team_id", ""),
        }

    def get_starter_state(
        self,
        pitcher_id: str,
        as_of_date: str,
        throws: str | None = None,
        player_name: str | None = None,
    ) -> StarterPitcherState:
        """Retrieve the multidimensional starter state strictly as-of as_of_date."""
        records = self._history.get(pitcher_id, [])
        info = self._info.get(pitcher_id, {})
        resolved_throws = throws or info.get("throws", "R")
        resolved_name = player_name or info.get("player_name", pitcher_id)

        return self.accumulator.evaluate_starter_state(
            pitcher_id=pitcher_id,
            records=records,
            as_of_date=as_of_date,
            player_name=resolved_name,
            throws=resolved_throws,
        )

    def evaluate_matchup(
        self,
        home_starter_id: str,
        away_starter_id: str,
        as_of_date: str,
        home_throws: str | None = None,
        away_throws: str | None = None,
        home_name: str | None = None,
        away_name: str | None = None,
    ) -> StarterMatchupAdvantage:
        """Evaluate starter matchup differential advantages for home vs away starting pitchers."""
        home_state = self.get_starter_state(
            home_starter_id, as_of_date=as_of_date, throws=home_throws, player_name=home_name
        )
        away_state = self.get_starter_state(
            away_starter_id, as_of_date=as_of_date, throws=away_throws, player_name=away_name
        )

        # Calculate differential advantages
        # xwOBA gap: positive means away starter allows higher xwOBA than home starter (home advantage)
        xwoba_gap = round(away_state.season_metrics.xwoba - home_state.season_metrics.xwoba, 4)
        # K-BB% gap: positive means home starter has higher K-BB% (home advantage)
        k_bb_gap = round(home_state.season_metrics.k_bb_pct - away_state.season_metrics.k_bb_pct, 4)
        # CSW% gap: positive means home starter gets more called strikes + whiffs
        csw_gap = round(home_state.season_metrics.csw_pct - away_state.season_metrics.csw_pct, 4)
        # First pitch strike % gap
        fps_gap = round(
            home_state.season_metrics.first_pitch_strike_pct
            - away_state.season_metrics.first_pitch_strike_pct,
            4,
        )
        # Depth gap: positive means home starter averages more innings per start
        depth_gap = round(
            home_state.season_metrics.avg_innings_per_start - away_state.season_metrics.avg_innings_per_start,
            2,
        )
        # Pitch efficiency gap: positive means home starter uses fewer pitches per IP
        pitch_eff_gap = round(
            away_state.season_metrics.pitches_per_ip - home_state.season_metrics.pitches_per_ip, 2
        )
        # Velocity gap: home fastball velo - away fastball velo
        velo_gap = round(
            home_state.season_metrics.fastball_velocity - away_state.season_metrics.fastball_velocity, 2
        )
        # Velocity drift gap: home drift - away drift
        drift_gap = round(home_state.velocity_drift_delta - away_state.velocity_drift_delta, 2)

        # Form trend deltas
        form_k_bb_gap = round(home_state.delta_k_bb - away_state.delta_k_bb, 4)
        form_xwoba_gap = round(away_state.delta_xwoba - home_state.delta_xwoba, 4)

        return StarterMatchupAdvantage(
            home_starter_id=home_starter_id,
            away_starter_id=away_starter_id,
            home_throws=home_state.throws,
            away_throws=away_state.throws,
            as_of_date=as_of_date,
            xwoba_gap=xwoba_gap,
            k_bb_gap=k_bb_gap,
            csw_gap=csw_gap,
            first_pitch_strike_gap=fps_gap,
            depth_gap=depth_gap,
            pitch_efficiency_gap=pitch_eff_gap,
            velocity_gap=velo_gap,
            velocity_drift_gap=drift_gap,
            form_k_bb_gap=form_k_bb_gap,
            form_xwoba_gap=form_xwoba_gap,
            home_state=home_state,
            away_state=away_state,
        )
