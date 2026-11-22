"""Season-keyed NCAAF conference membership and the lagged scoring proxy for cfb-structural-v5.1.

Both are inputs of the v5.1 feature extractor (``CFBFeatureExtractorV51``) and are read by the same code in the
training harness (``scripts/cfb_v51_research.py``) and in serving (``models/cfb_structural_v51.py``).

Conference membership (models.md finding 3): v5's ``is_conference_matchup`` reads the static team table, one
alignment for every season, so 2026 realignment games (Pac-12 rebuild, NIU and UTEP to the MWC, La Tech to the
Sun Belt) and every game of FIU, Sacramento State and Missouri State were labelled non-conference, and 2016-2023
training rows carried the 2025 alignment. ``config/ncaaf/cfb_conference_membership.json`` (built by
``scripts/cfb_v51_build_conference_map.py``) maps (season, ESPN team id) to that season's conference. Membership
is fixed before a season starts, so the table holds no outcome information. A season the table lacks, or a team
whose ESPN id cannot be resolved, is an explicit unresolved result; v5.1 serving declines it (never falls back to
the static table).

Lagged scoring proxy (models.md finding 4): the "EPA" channel is ``(mean of the team's last 5 points scored - 28)
/ 70``, written per row only by the one-off backfill, so daily-ingested rows have none since mid-October 2026.
``lagged_scoring_proxies`` computes it from the scores of history rows before the decision, so training and
serving run the same function and the stored ``*_epa_per_play`` fields are never read.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT

CONFERENCE_TABLE_PATH = PROJECT_ROOT / "config" / "ncaaf" / "cfb_conference_membership.json"
CONFERENCE_TABLE_SCHEMA = "cfb-conference-membership-v1"
FBS_CLASSIFICATION = "fbs"

# The backfill's constants (scripts/build_cfb_historical_dataset.py): baseline 28 points, scale 70, last 5 games.
PROXY_BASELINE_POINTS = 28.0
PROXY_SCALE_POINTS = 70.0
PROXY_WINDOW_GAMES = 5


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


@dataclass(frozen=True)
class ConferenceResolution:
    """Conference flag of one game, with what it rests on."""

    flag: bool
    resolved: bool  # season in the table and both ESPN ids known
    home_team_id: str | None
    away_team_id: str | None
    home_conference: str | None  # None = not listed for the season (not an FBS/FCS team the source saw)
    away_conference: str | None
    reason: str  # "ok", "season_missing", "home_id_unresolved", "away_id_unresolved"

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_conference_matchup": self.flag,
            "resolved": self.resolved,
            "home_team_id": self.home_team_id,
            "away_team_id": self.away_team_id,
            "home_conference": self.home_conference,
            "away_conference": self.away_conference,
            "reason": self.reason,
        }


class ConferenceMembership:
    """(season, ESPN team id) -> conference, from the hash-recorded membership table."""

    def __init__(self, table: Mapping[str, Any]) -> None:
        if table.get("schema") != CONFERENCE_TABLE_SCHEMA:
            raise ValueError(f"conference table schema mismatch: {table.get('schema')!r}")
        self._seasons: dict[int, dict[str, Mapping[str, str]]] = {
            int(season): dict(teams) for season, teams in dict(table["seasons"]).items()
        }
        self._aliases: dict[str, str] = {str(k): str(v) for k, v in dict(table.get("aliases") or {}).items()}
        self._independent = frozenset(str(c) for c in table.get("independent_conferences") or ())

    @classmethod
    def load(
        cls, path: str | Path | None = None, *, expected_sha256: str | None = None
    ) -> ConferenceMembership:
        """``expected_sha256`` is the table's CONTENT hash (``table_content_sha256``), not a file-bytes hash: git
        checks the JSON out with CRLF on Windows and LF elsewhere, and a bytes pin would refuse one of them."""
        p = Path(path) if path is not None else CONFERENCE_TABLE_PATH
        table = json.loads(p.read_text(encoding="utf-8"))
        if expected_sha256 is not None and table_content_sha256(table) != expected_sha256:
            raise ValueError(f"conference table sha256 mismatch for {p}")
        return cls(table)

    @property
    def seasons(self) -> tuple[int, ...]:
        return tuple(sorted(self._seasons))

    def has_season(self, season: int) -> bool:
        return int(season) in self._seasons

    def team_id(self, name: str, team_id: str | None = None) -> str | None:
        """The ESPN id the event gives, else the id the history name maps to (None when unknown)."""
        if team_id not in (None, ""):
            return str(team_id)
        return self._aliases.get(str(name))

    def conference(self, season: int, team_id: str | None) -> str | None:
        if team_id is None:
            return None
        entry = self._seasons.get(int(season), {}).get(str(team_id))
        return None if entry is None else str(entry.get("conference") or "") or None

    def is_fbs(self, season: int, team_id: str | None) -> bool:
        if team_id is None:
            return False
        entry = self._seasons.get(int(season), {}).get(str(team_id))
        return entry is not None and entry.get("classification") == FBS_CLASSIFICATION

    def is_conference_matchup(
        self,
        season: int,
        *,
        home_name: str,
        away_name: str,
        home_team_id: str | None = None,
        away_team_id: str | None = None,
    ) -> ConferenceResolution:
        """Both teams FBS that season, in the same conference, and not an independent "conference".

        Same scope as v5's flag (FBS conferences only): an FCS same-conference game stays False.
        """
        hid = self.team_id(home_name, home_team_id)
        aid = self.team_id(away_name, away_team_id)
        if not self.has_season(season):
            return ConferenceResolution(False, False, hid, aid, None, None, "season_missing")
        hconf, aconf = self.conference(season, hid), self.conference(season, aid)
        if hid is None or aid is None:
            reason = "home_id_unresolved" if hid is None else "away_id_unresolved"
            return ConferenceResolution(False, False, hid, aid, hconf, aconf, reason)
        flag = bool(
            self.is_fbs(season, hid)
            and self.is_fbs(season, aid)
            and hconf is not None
            and hconf == aconf
            and hconf not in self._independent
        )
        return ConferenceResolution(flag, True, hid, aid, hconf, aconf, "ok")


def table_content_sha256(table: Mapping[str, Any]) -> str:
    """sha256 of the canonical JSON of the parsed table (sorted keys, no whitespace): line endings cannot move it."""
    canonical = json.dumps(table, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def proxy_value(recent_points: Sequence[float]) -> float:
    """``(mean of recent points scored - 28) / 70``: the backfill's formula, unrounded."""
    return (sum(recent_points) / len(recent_points) - PROXY_BASELINE_POINTS) / PROXY_SCALE_POINTS


def lagged_scoring_proxies(
    keyed_rows: Sequence[tuple[Any, str, str]],
) -> dict[str, tuple[float | None, float | None]]:
    """event_id -> (home proxy, away proxy), each from that team's last 5 games strictly before the row.

    ``keyed_rows`` is (row, home key, away key) in history (kickoff) order, rows before the decision only, so
    every value is a pure function of scores the decision could see. A team with no earlier row has None (the
    backfill wrote 0.0 there and the feature path skipped a row whose two values were both 0.0).
    """
    recent: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=PROXY_WINDOW_GAMES))
    out: dict[str, tuple[float | None, float | None]] = {}
    for row, home_key, away_key in keyed_rows:
        h_hist, a_hist = recent[home_key], recent[away_key]
        out[str(_get(row, "event_id"))] = (
            proxy_value(h_hist) if h_hist else None,
            proxy_value(a_hist) if a_hist else None,
        )
        home_score, away_score = _get(row, "home_score"), _get(row, "away_score")
        if home_score is not None and away_score is not None:
            h_hist.append(float(home_score))
            a_hist.append(float(away_score))
    return out
