"""Point-in-time dynamic bullpen capability and availability state engine.

MLB v9 Dynamic Bullpen State module combining:
- Reliever talent (xwOBA allowed, K-BB%, FIP proxy with Empirical Bayes shrinkage)
- Workload availability probability function P(avail | pitches_1d, pitches_2d, pitches_3d, consecutive_days)
- Role leverage / closer weighting vs middle and long relief
- Aggregate bullpen index: sum_{r in bullpen} talent_r * P(available_r) * leverage_r
- Strict Point-In-Time (PIT) sequential updates

Strict Point-In-Time (PIT) Invariant:
    Reliever workload and talent are computed strictly from games completed prior to
    game_date / event_start_utc.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path

# League average reliever priors for Empirical Bayes shrinkage
FIP_CONSTANT = 3.10
LEAGUE_RELIEVER_PRIORS: dict[str, tuple[float, float]] = {
    "xwoba": (0.315, 100.0),  # (mean, PA weight)
    "k_pct": (0.235, 60.0),  # (mean, PA weight)
    "bb_pct": (0.088, 80.0),  # (mean, PA weight)
    "fip": (3.90, 20.0),  # (mean, IP weight)
}


class RelieverRole(str, Enum):
    """Bullpen role hierarchy and default leverage weights."""

    CLOSER = "CLOSER"
    SETUP = "SETUP"
    HIGH_LEVERAGE = "HIGH_LEVERAGE"
    MIDDLE_RELIEF = "MIDDLE_RELIEF"
    LONG_RELIEF = "LONG_RELIEF"
    MOP_UP = "MOP_UP"


ROLE_LEVERAGE_WEIGHTS: dict[RelieverRole | str, float] = {
    RelieverRole.CLOSER: 2.0,
    RelieverRole.SETUP: 1.5,
    RelieverRole.HIGH_LEVERAGE: 1.5,
    RelieverRole.MIDDLE_RELIEF: 1.0,
    RelieverRole.LONG_RELIEF: 0.6,
    RelieverRole.MOP_UP: 0.5,
    "CLOSER": 2.0,
    "SETUP": 1.5,
    "HIGH_LEVERAGE": 1.5,
    "MIDDLE_RELIEF": 1.0,
    "LONG_RELIEF": 0.6,
    "MOP_UP": 0.5,
}


def _parse_date(date_str: str) -> datetime:
    """Parse date or ISO timestamp string into a datetime object."""
    clean = date_str.replace("Z", "+00:00").split("T")[0]
    return datetime.strptime(clean, "%Y-%m-%d").replace(tzinfo=UTC)


def _is_strictly_before(record_date: str, as_of_date: str) -> bool:
    """Check if record_date is strictly before as_of_date."""
    if record_date == as_of_date:
        return False
    if ("T" in record_date or " " in record_date) and ("T" in as_of_date or " " in as_of_date):
        return record_date < as_of_date
    return _parse_date(record_date).date() < _parse_date(as_of_date).date()


def calculate_reliever_availability(
    pitches_1d: int,
    pitches_2d: int = 0,
    pitches_3d: int = 0,
    consecutive_days: int = 0,
) -> float:
    """Compute availability probability P(avail | pitches_1d, pitches_2d, pitches_3d, consecutive_days).

    Constraints:
    - If pitches_1d >= 35 or consecutive_days >= 3 -> availability < 0.15
    - If pitches_1d >= 20 or consecutive_days == 2 -> availability ~ 0.50
    - If rested (pitches_1d == 0, pitches_2d <= 15) -> availability ~ 0.95
    """
    # 1. High fatigue / consecutive day ceiling: availability < 0.15
    if pitches_1d >= 35 or consecutive_days >= 3:
        if pitches_1d >= 45 or (consecutive_days >= 3 and pitches_1d >= 25):
            return 0.03
        if pitches_1d >= 35:
            return 0.08
        return 0.10

    # 2. Moderate fatigue / back-to-back appearances: availability ~ 0.50
    if pitches_1d >= 20 or consecutive_days == 2:
        if consecutive_days == 2:
            total_2d = pitches_1d + pitches_2d
            if total_2d >= 40:
                return 0.40
            if total_2d >= 25:
                return 0.48
            return 0.54
        # consecutive_days == 1, pitches_1d >= 20
        if pitches_1d >= 30:
            return 0.38
        if pitches_1d >= 25:
            return 0.46
        return 0.52

    # 3. Rested: pitches_1d == 0 and pitches_2d <= 15 -> availability ~ 0.95
    if pitches_1d == 0 and pitches_2d <= 15:
        if pitches_2d == 0 and pitches_3d == 0:
            return 0.98
        if pitches_2d == 0 and pitches_3d <= 20:
            return 0.96
        return 0.95

    # 4. Intermediate cases
    if pitches_1d == 0:
        # Off yesterday, but heavy workload 2 days ago
        if pitches_2d >= 35:
            return 0.82
        if pitches_2d >= 25:
            return 0.88
        return 0.92

    # 1 <= pitches_1d < 20 (and consecutive_days == 1)
    if pitches_1d >= 15:
        return 0.70
    if pitches_1d >= 10:
        return 0.80
    return 0.88


@dataclass(slots=True)
class RelieverAppearance:
    """Historical record of an individual reliever game outing."""

    player_id: str
    game_date: str  # YYYY-MM-DD or ISO timestamp
    team_id: str = ""
    player_name: str = ""
    throws: str = "R"
    pitches_thrown: int = 0
    innings_pitched: float = 0.0
    strikeouts: int = 0
    walks: int = 0
    hits: int = 0
    doubles: int = 0
    triples: int = 0
    home_runs: int = 0
    hit_by_pitch: int = 0
    earned_runs: int = 0
    batters_faced: int = 0
    xwoba_sum: float = 0.0


@dataclass(slots=True)
class RelieverProfile:
    """Active reliever metadata, role, and leverage weight."""

    player_id: str
    player_name: str = ""
    team_id: str = ""
    throws: str = "R"
    role: str | RelieverRole = RelieverRole.MIDDLE_RELIEF
    leverage_weight: float | None = None

    def get_leverage(self) -> float:
        """Resolve effective leverage weight from explicit value or role."""
        if self.leverage_weight is not None and self.leverage_weight > 0:
            return self.leverage_weight
        return ROLE_LEVERAGE_WEIGHTS.get(self.role, 1.0)


@dataclass(slots=True)
class RelieverTalent:
    """Shrunk point-in-time reliever talent vector."""

    player_id: str
    player_name: str
    throws: str
    xwoba: float
    k_pct: float
    bb_pct: float
    k_bb_pct: float
    fip: float
    composite_talent_score: float  # Baseline 1.0, > 1.0 elite, < 1.0 subpar
    sample_innings: float
    sample_batters_faced: int


@dataclass(slots=True)
class RelieverWorkload:
    """Reliever trailing workload and consecutive day sequence."""

    pitches_1d: int
    pitches_2d: int
    pitches_3d: int
    consecutive_days: int
    total_pitches_3d: int


@dataclass(slots=True)
class RelieverState:
    """Point-in-time state of an individual reliever."""

    profile: RelieverProfile
    talent: RelieverTalent
    workload: RelieverWorkload
    availability: float  # P(avail) in [0.0, 1.0]
    effective_leverage: float
    weighted_talent_contribution: float  # talent.composite_talent_score * availability * effective_leverage


@dataclass(slots=True)
class BullpenStateVector:
    """Aggregate bullpen state for a team as-of a specific date."""

    team_id: str
    as_of_date: str
    aggregate_index: float  # sum_{r} talent_r * P(avail_r) * leverage_r
    effective_availability: float  # leverage-weighted availability in [0.0, 1.0]
    high_leverage_availability: float  # availability of closer / setup tier
    available_xwoba: float  # leverage and availability weighted xwOBA allowed
    available_k_bb: float  # leverage and availability weighted K-BB%
    available_fip: float  # leverage and availability weighted FIP
    active_relievers_count: int
    available_relievers_count: int  # relievers with P(avail) >= 0.50
    relievers: list[RelieverState] = field(default_factory=list)


@dataclass(slots=True)
class BullpenAdvantageVector:
    """Comparative bullpen advantage metrics between home and away teams."""

    home_team: str
    away_team: str
    as_of_date: str

    # Direct differentials (positive indicates home bullpen advantage)
    bullpen_index_gap: float  # home_index - away_index
    availability_gap: float  # home_avail - away_avail
    high_leverage_avail_gap: float  # home_hl_avail - away_hl_avail
    xwoba_gap: float  # away_xwoba - home_xwoba (positive = home bullpen suppresses xwOBA better)
    k_bb_gap: float  # home_k_bb - away_k_bb (positive = home bullpen has higher K-BB%)
    fip_gap: float  # away_fip - home_fip (positive = home bullpen has lower FIP)

    home_state: BullpenStateVector
    away_state: BullpenStateVector


class BullpenStateAccumulator:
    """Computes point-in-time reliever talent, workload, and availability."""

    def __init__(self, priors: dict[str, tuple[float, float]] | None = None) -> None:
        self.priors = priors or LEAGUE_RELIEVER_PRIORS

    def compute_reliever_talent(
        self,
        player_id: str,
        records: Sequence[RelieverAppearance],
        as_of_date: str,
        player_name: str = "",
        throws: str = "R",
    ) -> RelieverTalent:
        """Compute shrunk reliever talent from appearances strictly before as_of_date."""
        pit_records = [r for r in records if _is_strictly_before(r.game_date, as_of_date)]
        total_ip = sum(r.innings_pitched for r in pit_records)
        total_bf = sum(r.batters_faced for r in pit_records)
        total_k = sum(r.strikeouts for r in pit_records)
        total_bb = sum(r.walks for r in pit_records)
        total_hbp = sum(r.hit_by_pitch for r in pit_records)
        total_hr = sum(r.home_runs for r in pit_records)
        statcast_xwoba_sum = sum(r.xwoba_sum for r in pit_records)

        # Component wOBA
        woba_est_sum = sum(
            0.690 * r.walks
            + 0.720 * r.hit_by_pitch
            + 0.880 * max(0, r.hits - r.doubles - r.triples - r.home_runs)
            + 1.240 * r.doubles
            + 1.560 * r.triples
            + 2.070 * r.home_runs
            for r in pit_records
        )
        if statcast_xwoba_sum > 0:
            combined_xwoba_sum = 0.5 * woba_est_sum + 0.5 * statcast_xwoba_sum
        else:
            combined_xwoba_sum = woba_est_sum

        # Empirical Bayes shrinkage
        mu_xwoba, m_xwoba = self.priors["xwoba"]
        shrunk_xwoba = (combined_xwoba_sum + mu_xwoba * m_xwoba) / (total_bf + m_xwoba)

        mu_k, m_k = self.priors["k_pct"]
        shrunk_k = (total_k + mu_k * m_k) / (total_bf + m_k)

        mu_bb, m_bb = self.priors["bb_pct"]
        shrunk_bb = (total_bb + mu_bb * m_bb) / (total_bf + m_bb)

        shrunk_k_bb = shrunk_k - shrunk_bb

        # FIP calculation with shrinkage
        mu_fip, m_fip = self.priors["fip"]
        raw_fip_num = 13.0 * total_hr + 3.0 * (total_bb + total_hbp) - 2.0 * total_k
        if total_ip > 0:
            raw_fip = (raw_fip_num / total_ip) + FIP_CONSTANT
            shrunk_fip = (raw_fip * total_ip + mu_fip * m_fip) / (total_ip + m_fip)
        else:
            shrunk_fip = mu_fip

        # Composite talent score: baseline 1.0 (higher = better quality)
        composite_score = (
            1.0 + (0.315 - shrunk_xwoba) * 5.0 + (shrunk_k_bb - 0.147) * 2.0 + (3.90 - shrunk_fip) * 0.15
        )

        return RelieverTalent(
            player_id=player_id,
            player_name=player_name or player_id,
            throws=throws,
            xwoba=round(shrunk_xwoba, 4),
            k_pct=round(shrunk_k, 4),
            bb_pct=round(shrunk_bb, 4),
            k_bb_pct=round(shrunk_k_bb, 4),
            fip=round(shrunk_fip, 3),
            composite_talent_score=round(max(0.1, composite_score), 4),
            sample_innings=round(total_ip, 2),
            sample_batters_faced=total_bf,
        )

    def compute_reliever_workload(
        self,
        records: Sequence[RelieverAppearance],
        as_of_date: str,
    ) -> RelieverWorkload:
        """Compute trailing 3-day pitch counts and consecutive days pitched strictly before as_of_date."""
        as_of_dt = _parse_date(as_of_date)
        d1 = (as_of_dt - timedelta(days=1)).strftime("%Y-%m-%d")
        d2 = (as_of_dt - timedelta(days=2)).strftime("%Y-%m-%d")
        d3 = (as_of_dt - timedelta(days=3)).strftime("%Y-%m-%d")

        pitches_1d = 0
        pitches_2d = 0
        pitches_3d = 0

        for r in records:
            if not _is_strictly_before(r.game_date, as_of_date):
                continue
            r_date = r.game_date[:10]
            if r_date == d1:
                pitches_1d += r.pitches_thrown
            elif r_date == d2:
                pitches_2d += r.pitches_thrown
            elif r_date == d3:
                pitches_3d += r.pitches_thrown

        # Consecutive days calculation leading into as_of_date
        consecutive_days = 0
        if pitches_1d > 0:
            consecutive_days = 1
            if pitches_2d > 0:
                consecutive_days = 2
                if pitches_3d > 0:
                    consecutive_days = 3

        return RelieverWorkload(
            pitches_1d=pitches_1d,
            pitches_2d=pitches_2d,
            pitches_3d=pitches_3d,
            consecutive_days=consecutive_days,
            total_pitches_3d=pitches_1d + pitches_2d + pitches_3d,
        )

    def evaluate_reliever_state(
        self,
        profile: RelieverProfile,
        records: Sequence[RelieverAppearance],
        as_of_date: str,
    ) -> RelieverState:
        """Evaluate an individual reliever's full point-in-time state."""
        talent = self.compute_reliever_talent(
            player_id=profile.player_id,
            records=records,
            as_of_date=as_of_date,
            player_name=profile.player_name,
            throws=profile.throws,
        )
        workload = self.compute_reliever_workload(records, as_of_date=as_of_date)
        availability = calculate_reliever_availability(
            pitches_1d=workload.pitches_1d,
            pitches_2d=workload.pitches_2d,
            pitches_3d=workload.pitches_3d,
            consecutive_days=workload.consecutive_days,
        )
        leverage = profile.get_leverage()
        weighted_contrib = round(talent.composite_talent_score * availability * leverage, 4)

        return RelieverState(
            profile=profile,
            talent=talent,
            workload=workload,
            availability=round(availability, 4),
            effective_leverage=leverage,
            weighted_talent_contribution=weighted_contrib,
        )


class PointInTimeBullpenEngine:
    """Point-in-time dynamic bullpen capability engine managing appearances, rosters, and matchups."""

    def __init__(
        self,
        accumulator: BullpenStateAccumulator | None = None,
        snapshot_path: str | Path | None = None,
    ) -> None:
        self.accumulator = accumulator or BullpenStateAccumulator()
        self._appearances: dict[str, list[RelieverAppearance]] = {}  # player_id -> appearances
        self._team_appearances: dict[str, list[tuple[str, RelieverAppearance]]] = {}
        self._rosters: dict[tuple[str, str], list[RelieverProfile]] = {}  # (team_id, game_date) -> roster
        self._reliever_profiles: dict[str, RelieverProfile] = {}  # player_id -> profile
        if snapshot_path:
            self._load_from_snapshots(Path(snapshot_path))

    def _load_from_snapshots(self, path: Path) -> None:
        import json

        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    snap = json.loads(line)
                except json.JSONDecodeError:
                    continue
                game_start = str(snap.get("game_start_utc") or "")
                game_date = game_start[:10] if len(game_start) >= 10 else ""
                if not game_date:
                    continue
                for side in ("home", "away"):
                    side_obj = snap.get(side, {})
                    if not isinstance(side_obj, dict):
                        continue
                    # The engine's public API keys teams by NAME
                    # (evaluate_matchup("New York Yankees", ...) -- every
                    # caller passes full team names). Keying appearances by
                    # the numeric MLB Stats API team_id here made every
                    # name-keyed lookup miss and every bullpen feature fall
                    # back to the neutral league-prior vector
                    # (DEBUG.md 2026-08-26: 5 dead v9 bullpen columns).
                    team_id = str(side_obj.get("team_name") or side_obj.get("team_id") or "")
                    players = side_obj.get("players", [])
                    if not isinstance(players, list):
                        continue
                    for p in players:
                        order = int(p.get("pitching_order") or 0)
                        if order > 1:  # Reliever appearance
                            pid = str(p.get("player_id") or "")
                            pname = str(p.get("name") or pid)
                            pitching = p.get("pitching", {})
                            ip_str = str(pitching.get("inningsPitched") or "1.0")
                            try:
                                ip = float(ip_str)
                            except ValueError:
                                ip = 1.0
                            pitches = int(pitching.get("numberOfPitches") or int(ip * 16))
                            k = int(pitching.get("strikeOuts") or 0)
                            bb = int(pitching.get("baseOnBalls") or 0)
                            er = int(pitching.get("earnedRuns") or 0)
                            bf = int(pitching.get("battersFaced") or int(ip * 4))
                            self.update_reliever_appearance(
                                RelieverAppearance(
                                    player_id=pid,
                                    player_name=pname,
                                    team_id=team_id,
                                    game_date=game_date,
                                    pitches_thrown=pitches,
                                    innings_pitched=ip,
                                    batters_faced=bf,
                                    strikeouts=k,
                                    walks=bb,
                                    earned_runs=er,
                                )
                            )

    def update_reliever_appearance(self, record: RelieverAppearance) -> None:
        """Record a reliever game outing sequentially."""
        self._appearances.setdefault(record.player_id, []).append(record)
        if record.team_id:
            self._team_appearances.setdefault(record.team_id, []).append((record.player_id, record))
        if record.player_id not in self._reliever_profiles:
            self._reliever_profiles[record.player_id] = RelieverProfile(
                player_id=record.player_id,
                player_name=record.player_name or record.player_id,
                team_id=record.team_id,
                throws=record.throws,
                role=RelieverRole.MIDDLE_RELIEF,
            )

    def register_bullpen_roster(
        self,
        team_id: str,
        game_date: str,
        relievers: list[RelieverProfile],
    ) -> None:
        """Register the active bullpen roster for a team as of game_date."""
        key = (team_id, game_date[:10])
        self._rosters[key] = relievers
        for r in relievers:
            self._reliever_profiles[r.player_id] = r

    def register_reliever_profile(self, profile: RelieverProfile) -> None:
        """Register or update a reliever profile (role, leverage weight)."""
        self._reliever_profiles[profile.player_id] = profile

    def _get_active_roster(self, team_id: str, as_of_date: str) -> list[RelieverProfile]:
        """Get active bullpen roster for team, falling back to recent active relievers."""
        as_of_key = (team_id, as_of_date[:10])
        if as_of_key in self._rosters:
            return self._rosters[as_of_key]

        # Check most recent registered roster for this team prior to as_of_date
        past_roster_dates = [
            dt for (t, dt) in self._rosters if t == team_id and _is_strictly_before(dt, as_of_date)
        ]
        if past_roster_dates:
            latest_dt = max(past_roster_dates)
            return self._rosters[(team_id, latest_dt)]

        # Fallback: assemble roster from relievers with appearances for team in preceding 30 days
        team_apps = self._team_appearances.get(team_id, [])
        as_of_dt = _parse_date(as_of_date)
        cutoff_30d = as_of_dt - timedelta(days=30)

        active_player_ids: set[str] = set()
        for p_id, app in team_apps:
            if _is_strictly_before(app.game_date, as_of_date) and _parse_date(app.game_date) >= cutoff_30d:
                active_player_ids.add(p_id)

        if active_player_ids:
            return [
                self._reliever_profiles.get(
                    p_id,
                    RelieverProfile(player_id=p_id, team_id=team_id, role=RelieverRole.MIDDLE_RELIEF),
                )
                for p_id in active_player_ids
            ]

        return []

    def evaluate_bullpen(self, team_id: str, as_of_date: str) -> BullpenStateVector:
        """Evaluate dynamic bullpen capability vector for a team strictly as-of as_of_date."""
        roster = self._get_active_roster(team_id, as_of_date)
        if not roster:
            # Fallback neutral bullpen
            return BullpenStateVector(
                team_id=team_id,
                as_of_date=as_of_date,
                aggregate_index=1.0,
                effective_availability=0.90,
                high_leverage_availability=0.90,
                available_xwoba=LEAGUE_RELIEVER_PRIORS["xwoba"][0],
                available_k_bb=0.147,
                available_fip=LEAGUE_RELIEVER_PRIORS["fip"][0],
                active_relievers_count=0,
                available_relievers_count=0,
                relievers=[],
            )

        reliever_states: list[RelieverState] = []
        for profile in roster:
            records = self._appearances.get(profile.player_id, [])
            state = self.accumulator.evaluate_reliever_state(
                profile=profile,
                records=records,
                as_of_date=as_of_date,
            )
            reliever_states.append(state)

        # Aggregate metrics
        agg_index = sum(r.weighted_talent_contribution for r in reliever_states)
        total_leverage = sum(r.effective_leverage for r in reliever_states)
        available_leverage = sum(r.availability * r.effective_leverage for r in reliever_states)

        effective_avail = available_leverage / total_leverage if total_leverage > 0 else 0.0

        # High leverage availability (Closer & Setup)
        hl_relievers = [
            r
            for r in reliever_states
            if r.effective_leverage >= 1.4 or r.profile.role in (RelieverRole.CLOSER, RelieverRole.SETUP)
        ]
        if hl_relievers:
            hl_avail = sum(r.availability * r.effective_leverage for r in hl_relievers) / sum(
                r.effective_leverage for r in hl_relievers
            )
        else:
            hl_avail = effective_avail

        # Weighted talent metrics
        if available_leverage > 0:
            w_xwoba = (
                sum(r.talent.xwoba * r.availability * r.effective_leverage for r in reliever_states)
                / available_leverage
            )
            w_k_bb = (
                sum(r.talent.k_bb_pct * r.availability * r.effective_leverage for r in reliever_states)
                / available_leverage
            )
            w_fip = (
                sum(r.talent.fip * r.availability * r.effective_leverage for r in reliever_states)
                / available_leverage
            )
        else:
            w_xwoba = LEAGUE_RELIEVER_PRIORS["xwoba"][0]
            w_k_bb = 0.147
            w_fip = LEAGUE_RELIEVER_PRIORS["fip"][0]

        avail_count = sum(1 for r in reliever_states if r.availability >= 0.50)

        return BullpenStateVector(
            team_id=team_id,
            as_of_date=as_of_date,
            aggregate_index=round(agg_index, 4),
            effective_availability=round(effective_avail, 4),
            high_leverage_availability=round(hl_avail, 4),
            available_xwoba=round(w_xwoba, 4),
            available_k_bb=round(w_k_bb, 4),
            available_fip=round(w_fip, 3),
            active_relievers_count=len(reliever_states),
            available_relievers_count=avail_count,
            relievers=reliever_states,
        )

    def evaluate_matchup(
        self,
        home_team: str,
        away_team: str,
        as_of_date: str,
    ) -> BullpenAdvantageVector:
        """Evaluate comparative bullpen advantage between home and away teams."""
        home_state = self.evaluate_bullpen(home_team, as_of_date)
        away_state = self.evaluate_bullpen(away_team, as_of_date)

        # Differentials
        index_gap = round(home_state.aggregate_index - away_state.aggregate_index, 4)
        avail_gap = round(home_state.effective_availability - away_state.effective_availability, 4)
        hl_avail_gap = round(home_state.high_leverage_availability - away_state.high_leverage_availability, 4)
        xwoba_gap = round(away_state.available_xwoba - home_state.available_xwoba, 4)
        k_bb_gap = round(home_state.available_k_bb - away_state.available_k_bb, 4)
        fip_gap = round(away_state.available_fip - home_state.available_fip, 3)

        return BullpenAdvantageVector(
            home_team=home_team,
            away_team=away_team,
            as_of_date=as_of_date,
            bullpen_index_gap=index_gap,
            availability_gap=avail_gap,
            high_leverage_avail_gap=hl_avail_gap,
            xwoba_gap=xwoba_gap,
            k_bb_gap=k_bb_gap,
            fip_gap=fip_gap,
            home_state=home_state,
            away_state=away_state,
        )
