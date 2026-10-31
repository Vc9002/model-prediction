"""Real NFL point-in-time efficiency feature engine.

Extracts genuine team-level EPA/play (offensive passing + rushing, defensive allowed)
and starting quarterback value over replacement from official point-in-time game records.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np
import polars as pl

from model_prediction.data_sources.nfl_efficiency_snapshot import load_efficiency_snapshot
from model_prediction.runtime_paths import RuntimePaths

logger = logging.getLogger(__name__)

NFL_LEAGUE_AVG_EPA_PLAY = 0.00
DEFAULT_PRIOR_PLAYS = 120.0
DEFAULT_PRIOR_DROPBACKS = 120.0

# Canonical 3-letter team codes used in nflverse
NFL_TEAM_NAME_TO_CODE: dict[str, str] = {
    "arizona cardinals": "ARI",
    "cardinals": "ARI",
    "ari": "ARI",
    "atlanta falcons": "ATL",
    "falcons": "ATL",
    "atl": "ATL",
    "baltimore ravens": "BAL",
    "ravens": "BAL",
    "bal": "BAL",
    "buffalo bills": "BUF",
    "bills": "BUF",
    "buf": "BUF",
    "carolina panthers": "CAR",
    "panthers": "CAR",
    "car": "CAR",
    "chicago bears": "CHI",
    "bears": "CHI",
    "chi": "CHI",
    "cincinnati bengals": "CIN",
    "bengals": "CIN",
    "cin": "CIN",
    "cleveland browns": "CLE",
    "browns": "CLE",
    "cle": "CLE",
    "dallas cowboys": "DAL",
    "cowboys": "DAL",
    "dal": "DAL",
    "denver broncos": "DEN",
    "broncos": "DEN",
    "den": "DEN",
    "detroit lions": "DET",
    "lions": "DET",
    "det": "DET",
    "green bay packers": "GB",
    "packers": "GB",
    "gb": "GB",
    "houston texans": "HOU",
    "texans": "HOU",
    "hou": "HOU",
    "indianapolis colts": "IND",
    "colts": "IND",
    "ind": "IND",
    "jacksonville jaguars": "JAX",
    "jaguars": "JAX",
    "jax": "JAX",
    "kansas city chiefs": "KC",
    "chiefs": "KC",
    "kc": "KC",
    "los angeles rams": "LA",
    "rams": "LA",
    "la": "LA",
    "lar": "LA",
    "los angeles chargers": "LAC",
    "chargers": "LAC",
    "lac": "LAC",
    "las vegas raiders": "LV",
    "raiders": "LV",
    "lv": "LV",
    "oak": "LV",
    "miami dolphins": "MIA",
    "dolphins": "MIA",
    "mia": "MIA",
    "minnesota vikings": "MIN",
    "vikings": "MIN",
    "min": "MIN",
    "new england patriots": "NE",
    "patriots": "NE",
    "ne": "NE",
    "new orleans saints": "NO",
    "saints": "NO",
    "no": "NO",
    "new york giants": "NYG",
    "giants": "NYG",
    "nyg": "NYG",
    "new york jets": "NYJ",
    "jets": "NYJ",
    "nyj": "NYJ",
    "philadelphia eagles": "PHI",
    "eagles": "PHI",
    "phi": "PHI",
    "pittsburgh steelers": "PIT",
    "steelers": "PIT",
    "pit": "PIT",
    "seattle seahawks": "SEA",
    "seahawks": "SEA",
    "sea": "SEA",
    "san francisco 49ers": "SF",
    "49ers": "SF",
    "sf": "SF",
    "tampa bay buccaneers": "TB",
    "buccaneers": "TB",
    "tb": "TB",
    "tennessee titans": "TEN",
    "titans": "TEN",
    "ten": "TEN",
    "washington commanders": "WAS",
    "commanders": "WAS",
    "washington": "WAS",
    "was": "WAS",
    "wsh": "WAS",
}


def normalize_nfl_team_code(team_name: str) -> str:
    """Normalize any team name, city, or variant to canonical 3-letter code."""
    key = str(team_name or "").strip().lower()
    return NFL_TEAM_NAME_TO_CODE.get(key, key.upper())


@dataclass(frozen=True)
class NFLEfficiencyMatchup:
    home_team: str
    away_team: str
    home_off_epa: float
    home_def_epa: float
    away_off_epa: float
    away_def_epa: float
    home_qb_tier_adj: float
    away_qb_tier_adj: float
    home_prior_games: int
    away_prior_games: int
    home_pass_epa: float = 0.0
    home_rush_epa: float = 0.0
    away_pass_epa: float = 0.0
    away_rush_epa: float = 0.0
    home_def_pass_epa: float = 0.0
    home_def_rush_epa: float = 0.0
    away_def_pass_epa: float = 0.0
    away_def_rush_epa: float = 0.0
    home_pressure_disparity: float = 0.0
    away_pressure_disparity: float = 0.0
    home_pace: float = 62.5
    away_pace: float = 62.5
    home_explosive_rate: float = 0.08
    away_explosive_rate: float = 0.08
    home_def_explosive_rate: float = 0.08
    away_def_explosive_rate: float = 0.08
    home_rz_td_rate: float = 0.60
    away_rz_td_rate: float = 0.60
    home_def_rz_td_rate: float = 0.60
    away_def_rz_td_rate: float = 0.60


class NFLEfficiencyStore:
    """Thread-safe point-in-time store for real NFL team and QB efficiency metrics."""

    _instance: NFLEfficiencyStore | None = None

    def __init__(self, data_root: Path | None = None, *, observed_at: datetime | None = None) -> None:
        if data_root is None:
            data_root = RuntimePaths.resolve().repo_root / "data"
        self.data_root = Path(data_root)
        self.team_parquet = self.data_root / "processed/nfl/team_weekly_efficiency.parquet"
        self.qb_parquet = self.data_root / "processed/nfl/qb_weekly_efficiency.parquet"

        self._team_df: pl.DataFrame | None = None
        self._qb_df: pl.DataFrame | None = None
        self.source_manifest: dict[str, object] | None = None
        self._load(observed_at)

    def _load(self, observed_at: datetime | None) -> None:
        if observed_at is not None:
            snapshot = load_efficiency_snapshot(self.data_root, observed_at)
            if snapshot is not None:
                self._team_df, self._qb_df, self.source_manifest = snapshot
                return
        try:
            if self.team_parquet.exists():
                self._team_df = pl.read_parquet(self.team_parquet)
            if self.qb_parquet.exists():
                self._qb_df = pl.read_parquet(self.qb_parquet)
        except (OSError, pl.exceptions.PolarsError) as exc:
            logger.warning("Could not read local NFL efficiency parquets: %s", exc)

    @classmethod
    def get_default(
        cls, data_root: Path | None = None, *, observed_at: datetime | None = None
    ) -> NFLEfficiencyStore:
        # A singleton can retain another root or an older release across slates.
        return cls(data_root=data_root, observed_at=observed_at)

    def covers_last_game(self, team: str, last_game_date: date | None) -> bool:
        """Qualification requires a verified release covering the team's latest result."""
        if self.source_manifest is None or self._team_df is None or last_game_date is None:
            return False
        rows = self._team_df.filter(pl.col("team") == normalize_nfl_team_code(team))
        if rows.is_empty():
            return False
        return str(rows.select(pl.col("gameday").max()).item()) >= last_game_date.isoformat()

    def get_matchup_efficiency(
        self,
        home_team: str,
        away_team: str,
        as_of_date: str | date | datetime,
        home_qb: str | None = None,
        away_qb: str | None = None,
        lookback_games: int = 8,
        opponent_adjust: bool = True,
        recency_decay: bool = True,
    ) -> NFLEfficiencyMatchup:
        """Calculate strictly point-in-time EPA and QB metrics before as_of_date."""
        if isinstance(as_of_date, datetime):
            date_str = as_of_date.strftime("%Y-%m-%d")
        elif isinstance(as_of_date, date):
            date_str = as_of_date.isoformat()
        else:
            date_str = str(as_of_date)[:10]

        h_code = normalize_nfl_team_code(home_team)
        a_code = normalize_nfl_team_code(away_team)

        h_eff = self._team_efficiency(
            h_code, date_str, lookback_games, opponent_adjust=opponent_adjust, recency_decay=recency_decay
        )
        a_eff = self._team_efficiency(
            a_code, date_str, lookback_games, opponent_adjust=opponent_adjust, recency_decay=recency_decay
        )

        (
            h_off,
            h_def,
            h_count,
            h_pass,
            h_rush,
            h_def_pass,
            h_def_rush,
            h_sack,
            h_def_sack,
            h_pace,
            h_exp,
            h_def_exp,
            h_rz,
            h_def_rz,
        ) = h_eff
        (
            a_off,
            a_def,
            a_count,
            a_pass,
            a_rush,
            a_def_pass,
            a_def_rush,
            a_sack,
            a_def_sack,
            a_pace,
            a_exp,
            a_def_exp,
            a_rz,
            a_def_rz,
        ) = a_eff

        h_pressure_disparity = a_def_sack - h_sack
        a_pressure_disparity = h_def_sack - a_sack

        h_qb_adj = self._qb_adjustment(home_qb, date_str) if home_qb else 0.0
        a_qb_adj = self._qb_adjustment(away_qb, date_str) if away_qb else 0.0

        return NFLEfficiencyMatchup(
            home_team=home_team,
            away_team=away_team,
            home_off_epa=round(h_off, 4),
            home_def_epa=round(h_def, 4),
            away_off_epa=round(a_off, 4),
            away_def_epa=round(a_def, 4),
            home_qb_tier_adj=round(h_qb_adj, 2),
            away_qb_tier_adj=round(a_qb_adj, 2),
            home_prior_games=h_count,
            away_prior_games=a_count,
            home_pass_epa=round(h_pass, 4),
            home_rush_epa=round(h_rush, 4),
            away_pass_epa=round(a_pass, 4),
            away_rush_epa=round(a_rush, 4),
            home_def_pass_epa=round(h_def_pass, 4),
            home_def_rush_epa=round(h_def_rush, 4),
            away_def_pass_epa=round(a_def_pass, 4),
            away_def_rush_epa=round(a_def_rush, 4),
            home_pressure_disparity=round(h_pressure_disparity, 4),
            away_pressure_disparity=round(a_pressure_disparity, 4),
            home_pace=round(h_pace, 1),
            away_pace=round(a_pace, 1),
            home_explosive_rate=round(h_exp, 4),
            away_explosive_rate=round(a_exp, 4),
            home_def_explosive_rate=round(h_def_exp, 4),
            away_def_explosive_rate=round(a_def_exp, 4),
            home_rz_td_rate=round(h_rz, 4),
            away_rz_td_rate=round(a_rz, 4),
            home_def_rz_td_rate=round(h_def_rz, 4),
            away_def_rz_td_rate=round(a_def_rz, 4),
        )

    def _team_efficiency(
        self,
        team_code: str,
        date_str: str,
        lookback: int,
        opponent_adjust: bool = True,
        recency_decay: bool = True,
    ) -> tuple[
        float,
        float,
        int,
        float,
        float,
        float,
        float,
        float,
        float,
        float,
        float,
        float,
        float,
        float,
    ]:
        """Compute shrunk, opponent-adjusted, and recency-decayed offensive/defensive metrics strictly before date_str."""
        if self._team_df is None or len(self._team_df) == 0:
            return (
                NFL_LEAGUE_AVG_EPA_PLAY,
                NFL_LEAGUE_AVG_EPA_PLAY,
                0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.06,
                0.06,
                62.5,
                0.08,
                0.08,
                0.60,
                0.60,
            )

        try:
            # 1. Offensive EPA (this team on offense)
            off_prior = (
                self._team_df.filter((pl.col("team") == team_code) & (pl.col("gameday") < date_str))
                .sort("gameday")
                .tail(lookback)
            )
            count = len(off_prior)
            if count == 0:
                off_epa = NFL_LEAGUE_AVG_EPA_PLAY
                pass_epa, rush_epa, sack_rate, pace = 0.0, 0.0, 0.06, 62.5
                exp_rate, rz_td_rate = 0.08, 0.60
            else:
                attempts = float(off_prior["attempts"].sum() or 0.0)
                carries = float(off_prior["carries"].sum() or 0.0)
                sacks = float(off_prior["sacks_suffered"].sum() or 0.0)
                pass_epa_sum = float(off_prior["passing_epa"].sum() or 0.0)
                rush_epa_sum = float(off_prior["rushing_epa"].sum() or 0.0)
                plays = attempts + carries

                p20 = (
                    float(off_prior["passing_20"].sum() or 0.0) if "passing_20" in off_prior.columns else 0.0
                )
                r12 = (
                    float(off_prior["rushing_12"].sum() or 0.0) if "rushing_12" in off_prior.columns else 0.0
                )
                exp_rate = (p20 + r12) / max(1.0, plays)

                tds = (
                    float(off_prior["passing_tds"].sum() or 0.0)
                    + float(off_prior["rushing_tds"].sum() or 0.0)
                    if "passing_tds" in off_prior.columns
                    else 2.0 * count
                )
                fgs = (
                    float(off_prior["fg_made"].sum() or 0.0)
                    if "fg_made" in off_prior.columns
                    else 1.5 * count
                )
                rz_td_rate = tds / max(1.0, tds + fgs)

                off_epa = (DEFAULT_PRIOR_PLAYS * NFL_LEAGUE_AVG_EPA_PLAY + pass_epa_sum + rush_epa_sum) / (
                    DEFAULT_PRIOR_PLAYS + max(1.0, plays)
                )

                # Exponential recency decay weights (half-life ~ 6 games)
                if recency_decay and count > 1:
                    w = np.exp(-0.10 * np.arange(count)[::-1])
                    weights = w / np.sum(w)
                else:
                    weights = np.ones(count) / float(count)

                # Opponent schedule adjustment: lookup opponent defense baseline before date_str
                opp_dp_map: dict[str, float] = {}
                opp_dr_map: dict[str, float] = {}
                if opponent_adjust:
                    opps = off_prior["opponent_team"].to_list()
                    opp_def_stats = (
                        self._team_df.filter(
                            (pl.col("opponent_team").is_in(opps)) & (pl.col("gameday") < date_str)
                        )
                        .group_by("opponent_team")
                        .agg(
                            [
                                (pl.col("passing_epa").sum() / pl.col("attempts").sum()).alias("opp_dp"),
                                (pl.col("rushing_epa").sum() / pl.col("carries").sum()).alias("opp_dr"),
                            ]
                        )
                    )
                    for r in opp_def_stats.iter_rows(named=True):
                        opp_dp_map[r["opponent_team"]] = float(r["opp_dp"] or 0.0)
                        opp_dr_map[r["opponent_team"]] = float(r["opp_dr"] or 0.0)

                pass_adj_list: list[float] = []
                rush_adj_list: list[float] = []
                for r in off_prior.iter_rows(named=True):
                    opp = r["opponent_team"]
                    att_g = max(1.0, float(r["attempts"] or 0.0))
                    car_g = max(1.0, float(r["carries"] or 0.0))
                    raw_p = float(r["passing_epa"] or 0.0) / att_g
                    raw_r = float(r["rushing_epa"] or 0.0) / car_g
                    pass_adj_list.append(raw_p - opp_dp_map.get(opp, 0.0))
                    rush_adj_list.append(raw_r - opp_dr_map.get(opp, 0.0))

                pass_epa = float(np.sum(weights * np.array(pass_adj_list)))
                rush_epa = float(np.sum(weights * np.array(rush_adj_list)))
                sack_rate = sacks / max(1.0, attempts + sacks)
                pace = plays / max(1.0, count)

            # 2. Defensive EPA (opponents against this team)
            def_prior = (
                self._team_df.filter((pl.col("opponent_team") == team_code) & (pl.col("gameday") < date_str))
                .sort("gameday")
                .tail(lookback)
            )
            if len(def_prior) == 0:
                def_epa = NFL_LEAGUE_AVG_EPA_PLAY
                def_pass_epa, def_rush_epa, def_sack_rate = 0.0, 0.0, 0.06
                def_exp_rate, def_rz_td_rate = 0.08, 0.60
            else:
                opp_attempts = float(def_prior["attempts"].sum() or 0.0)
                opp_carries = float(def_prior["carries"].sum() or 0.0)
                def_sacks = (
                    float(def_prior["def_sacks"].sum() or 0.0)
                    if "def_sacks" in def_prior.columns
                    else 2.4 * count
                )
                opp_plays = opp_attempts + opp_carries
                opp_pass_sum = float(def_prior["passing_epa"].sum() or 0.0)
                opp_rush_sum = float(def_prior["rushing_epa"].sum() or 0.0)

                d_p20 = (
                    float(def_prior["passing_20"].sum() or 0.0) if "passing_20" in def_prior.columns else 0.0
                )
                d_r12 = (
                    float(def_prior["rushing_12"].sum() or 0.0) if "rushing_12" in def_prior.columns else 0.0
                )
                def_exp_rate = (d_p20 + d_r12) / max(1.0, opp_plays)

                d_tds = (
                    float(def_prior["passing_tds"].sum() or 0.0)
                    + float(def_prior["rushing_tds"].sum() or 0.0)
                    if "passing_tds" in def_prior.columns
                    else 2.0 * len(def_prior)
                )
                d_fgs = (
                    float(def_prior["fg_made"].sum() or 0.0)
                    if "fg_made" in def_prior.columns
                    else 1.5 * len(def_prior)
                )
                def_rz_td_rate = d_tds / max(1.0, d_tds + d_fgs)

                def_epa = (DEFAULT_PRIOR_PLAYS * NFL_LEAGUE_AVG_EPA_PLAY + opp_pass_sum + opp_rush_sum) / (
                    DEFAULT_PRIOR_PLAYS + max(1.0, opp_plays)
                )

                if recency_decay and len(def_prior) > 1:
                    w_d = np.exp(-0.10 * np.arange(len(def_prior))[::-1])
                    def_weights = w_d / np.sum(w_d)
                else:
                    def_weights = np.ones(len(def_prior)) / float(len(def_prior))

                opp_op_map: dict[str, float] = {}
                opp_or_map: dict[str, float] = {}
                if opponent_adjust:
                    opp_off_teams = def_prior["team"].to_list()
                    opp_off_stats = (
                        self._team_df.filter(
                            (pl.col("team").is_in(opp_off_teams)) & (pl.col("gameday") < date_str)
                        )
                        .group_by("team")
                        .agg(
                            [
                                (pl.col("passing_epa").sum() / pl.col("attempts").sum()).alias("opp_op"),
                                (pl.col("rushing_epa").sum() / pl.col("carries").sum()).alias("opp_or"),
                            ]
                        )
                    )
                    for r in opp_off_stats.iter_rows(named=True):
                        opp_op_map[r["team"]] = float(r["opp_op"] or 0.0)
                        opp_or_map[r["team"]] = float(r["opp_or"] or 0.0)

                def_pass_adj: list[float] = []
                def_rush_adj: list[float] = []
                for r in def_prior.iter_rows(named=True):
                    opp_team = r["team"]
                    att_g = max(1.0, float(r["attempts"] or 0.0))
                    car_g = max(1.0, float(r["carries"] or 0.0))
                    raw_p = float(r["passing_epa"] or 0.0) / att_g
                    raw_r = float(r["rushing_epa"] or 0.0) / car_g
                    def_pass_adj.append(raw_p - opp_op_map.get(opp_team, 0.0))
                    def_rush_adj.append(raw_r - opp_or_map.get(opp_team, 0.0))

                def_pass_epa = float(np.sum(def_weights * np.array(def_pass_adj)))
                def_rush_epa = float(np.sum(def_weights * np.array(def_rush_adj)))
                def_sack_rate = def_sacks / max(1.0, opp_attempts + def_sacks)

            return (
                off_epa,
                def_epa,
                count,
                pass_epa,
                rush_epa,
                def_pass_epa,
                def_rush_epa,
                sack_rate,
                def_sack_rate,
                pace,
                exp_rate,
                def_exp_rate,
                rz_td_rate,
                def_rz_td_rate,
            )
        except (KeyError, ValueError, TypeError, pl.exceptions.PolarsError) as exc:
            logger.debug("Failed computing team efficiency for %s: %s", team_code, exc)
            return (
                NFL_LEAGUE_AVG_EPA_PLAY,
                NFL_LEAGUE_AVG_EPA_PLAY,
                0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.06,
                0.06,
                62.5,
                0.08,
                0.08,
                0.60,
                0.60,
            )

    def _qb_adjustment(self, qb_name: str, date_str: str) -> float:
        """Compute QB spread point value adjustment over average starter."""
        if self._qb_df is None or len(self._qb_df) == 0 or not qb_name or qb_name == "Unknown":
            return 0.0

        try:
            name_clean = qb_name.strip()
            prior = (
                self._qb_df.filter(
                    (pl.col("player_display_name").str.to_lowercase() == name_clean.lower())
                    & (pl.col("gameday") < date_str)
                )
                .sort("gameday")
                .tail(16)
            )

            dropbacks = float(prior["attempts"].sum() or 0.0) + float(prior["sacks_suffered"].sum() or 0.0)
            if dropbacks < 20.0:
                # Untested or rookie backup penalty (-1.5 to -2.5 pts)
                return -1.5

            pepa_sum = float(prior["passing_epa"].sum() or 0.0)
            cpoe_mean = float(str(prior["passing_cpoe"].mean() or 0.0))

            # Bayesian shrinkage toward league average
            shrunk_epa = (DEFAULT_PRIOR_DROPBACKS * 0.04 + pepa_sum) / (DEFAULT_PRIOR_DROPBACKS + dropbacks)
            shrunk_cpoe = (DEFAULT_PRIOR_DROPBACKS * 0.0 + dropbacks * cpoe_mean) / (
                DEFAULT_PRIOR_DROPBACKS + dropbacks
            )

            # Map to spread points over replacement (0.5 to 6.5 pts; average starter = 3.0 pts)
            spread_val = max(
                0.5,
                min(6.5, 3.0 + (shrunk_epa - 0.04) * 15.0 + (shrunk_cpoe / 3.0)),
            )
            # Tier adjustment delta against average starter (3.0 pts)
            return spread_val - 3.0
        except (KeyError, ValueError, TypeError, pl.exceptions.PolarsError) as exc:
            logger.debug("Failed computing QB adjustment for %s: %s", qb_name, exc)
            return 0.0
