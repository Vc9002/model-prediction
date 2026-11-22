"""cfb-structural-v5.1 feature extractor: v5's extractor with the two input fixes, nothing else.

1. ``is_conference_matchup`` from the season-keyed membership table (``cfb_conference.ConferenceMembership``)
   instead of the static team table.
2. The "EPA" channel from ``cfb_conference.lagged_scoring_proxies`` over the history rows before kickoff
   (``epa_mode="live_proxy"``), or switched off so the team state takes v5's PPP fallback (``"dropped"``). The
   stored ``*_epa_per_play`` fields are never read in either mode.

Everything else (identity, FBS/FCS classes, tier offsets from the static table, Elo, PPP, pace, travel,
weather, QB) is v5's code path unchanged. Tier offsets stay on the static table on purpose: the 2026 "Pac-12"
is a rebuilt Group-of-5 league, so keying the Power-conference tier offset by conference name per season would
give Boise State a +2.0 Power tier instead of its -2.5; season-specific tiers would need their own fit.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..data_sources.cfb_data import CFBTeam
from .cfb_conference import ConferenceMembership, lagged_scoring_proxies
from .cfb_features import CFBFeatureExtractor, EpaReader, KeyedRow

EPA_MODES = ("live_proxy", "dropped")


class CFBFeatureExtractorV51(CFBFeatureExtractor):
    def __init__(
        self,
        membership: ConferenceMembership,
        *,
        epa_mode: str = "live_proxy",
        home_advantage_points: float = 2.5,
    ) -> None:
        if epa_mode not in EPA_MODES:
            raise ValueError(f"unknown cfb v5.1 epa_mode {epa_mode!r}")
        super().__init__(home_advantage_points=home_advantage_points)
        self.membership = membership
        self.epa_mode = epa_mode

    def _is_conference_matchup(
        self,
        season: int,
        *,
        away_team: str,
        home_team: str,
        away_obj: CFBTeam | None,
        home_obj: CFBTeam | None,
        away_team_id: str | None,
        home_team_id: str | None,
    ) -> bool:
        return self.membership.is_conference_matchup(
            season,
            home_name=home_team,
            away_name=away_team,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
        ).flag

    def _epa_reader(self, keyed_rows: Sequence[KeyedRow]) -> EpaReader | None:
        if self.epa_mode == "dropped":
            return _no_epa
        proxies = lagged_scoring_proxies(keyed_rows)

        def read(row: Any) -> tuple[float | None, float | None]:
            eid = row.get("event_id") if isinstance(row, dict) else getattr(row, "event_id", None)
            return proxies.get(str(eid), (None, None))

        return read


def _no_epa(row: Any) -> tuple[float | None, float | None]:
    return None, None
