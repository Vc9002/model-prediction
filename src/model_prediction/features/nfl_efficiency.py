"""Real NFL point-in-time efficiency feature engine.

Extracts genuine team-level EPA/play (offensive passing + rushing, defensive allowed)
and starting quarterback value over replacement from official point-in-time game records.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import polars as pl

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


class NFLEfficiencyStore:
    """Thread-safe point-in-time store for real NFL team and QB efficiency metrics."""

    _instance: NFLEfficiencyStore | None = None

    def __init__(self, data_root: Path | None = None) -> None:
        if data_root is None:
            data_root = RuntimePaths.resolve().repo_root / "data"
        self.data_root = Path(data_root)
        self.team_parquet = self.data_root / "processed/nfl/team_weekly_efficiency.parquet"
        self.qb_parquet = self.data_root / "processed/nfl/qb_weekly_efficiency.parquet"

        self._team_df: pl.DataFrame | None = None
        self._qb_df: pl.DataFrame | None = None
        self._load()

    def _load(self) -> None:
        try:
            if self.team_parquet.exists():
                self._team_df = pl.read_parquet(self.team_parquet)
            if self.qb_parquet.exists():
                self._qb_df = pl.read_parquet(self.qb_parquet)
        except (OSError, pl.exceptions.PolarsError) as exc:
            logger.warning("Could not read local NFL efficiency parquets: %s", exc)

    @classmethod
    def get_default(cls, data_root: Path | None = None) -> NFLEfficiencyStore:
        if cls._instance is None:
            cls._instance = cls(data_root=data_root)
        return cls._instance

    def get_matchup_efficiency(
        self,
        home_team: str,
        away_team: str,
        as_of_date: str | date | datetime,
        home_qb: str | None = None,
        away_qb: str | None = None,
        lookback_games: int = 6,
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

        h_off, h_def, h_count = self._team_efficiency(h_code, date_str, lookback_games)
        a_off, a_def, a_count = self._team_efficiency(a_code, date_str, lookback_games)

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
        )

    def _team_efficiency(self, team_code: str, date_str: str, lookback: int) -> tuple[float, float, int]:
        """Compute shrunk offensive and defensive EPA/play strictly before date_str."""
        if self._team_df is None or len(self._team_df) == 0:
            return NFL_LEAGUE_AVG_EPA_PLAY, NFL_LEAGUE_AVG_EPA_PLAY, 0

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
            else:
                plays = float(off_prior["attempts"].sum() or 0.0) + float(off_prior["carries"].sum() or 0.0)
                epa_sum = float(off_prior["passing_epa"].sum() or 0.0) + float(
                    off_prior["rushing_epa"].sum() or 0.0
                )
                off_epa = (DEFAULT_PRIOR_PLAYS * NFL_LEAGUE_AVG_EPA_PLAY + epa_sum) / (
                    DEFAULT_PRIOR_PLAYS + max(1.0, plays)
                )

            # 2. Defensive EPA (opponents against this team)
            def_prior = (
                self._team_df.filter((pl.col("opponent_team") == team_code) & (pl.col("gameday") < date_str))
                .sort("gameday")
                .tail(lookback)
            )
            if len(def_prior) == 0:
                def_epa = NFL_LEAGUE_AVG_EPA_PLAY
            else:
                opp_plays = float(def_prior["attempts"].sum() or 0.0) + float(
                    def_prior["carries"].sum() or 0.0
                )
                opp_epa_sum = float(def_prior["passing_epa"].sum() or 0.0) + float(
                    def_prior["rushing_epa"].sum() or 0.0
                )
                def_epa = (DEFAULT_PRIOR_PLAYS * NFL_LEAGUE_AVG_EPA_PLAY + opp_epa_sum) / (
                    DEFAULT_PRIOR_PLAYS + max(1.0, opp_plays)
                )

            return off_epa, def_epa, count
        except (KeyError, ValueError, TypeError, pl.exceptions.PolarsError) as exc:
            logger.debug("Failed computing team efficiency for %s: %s", team_code, exc)
            return NFL_LEAGUE_AVG_EPA_PLAY, NFL_LEAGUE_AVG_EPA_PLAY, 0

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
