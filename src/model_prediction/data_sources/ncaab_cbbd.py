"""CollegeBasketballData.com (CBBD) client: historical NCAAB games, betting lines and team box scores.

API: ``https://api.collegebasketballdata.com`` (the CFBD family; endpoint shapes from the
``CFBD/cbbd-python`` generated docs). Auth is ``Authorization: Bearer <key>`` with the key in
``CBBD_API_KEY`` (``os.environ``; callers may first run ``runtime_paths.load_dotenv_into_environ``).
No key -> ``CBBDAuthError`` before any request. The key never reaches the cache, its file names,
error text or logs (it is a header, not a URL parameter, so httpx's request log never sees it).

Quota: every request counts against a monthly call budget shared with CFBD. The server reports the
budget left in ``X-CallLimit-Remaining`` (3,000/month on this account as of 2026-11-20). The client
records it after every call (``_quota.json`` in the cache root, so a fresh process honours it too),
logs it, and refuses to make a request once the last seen value is below ``quota_floor`` (default
200). Responses are cached as raw JSON with ``fetched_at_utc`` and the (key-free) parameters, and a
cached response is never refetched unless asked: rerunning a backfill costs no calls.

List endpoints return at most 3,000 games per call, so season pulls go through ``fetch_windowed``:
a few date windows per season, each split in half and refetched if a response hits the cap.

Line semantics (what the API documents, which is little): each game carries one entry per
provider with ``spread`` / ``overUnder`` (the provider's latest stored number; for finished games
read as the closing line, but the API gives NO timestamp for it), ``spreadOpen`` /
``overUnderOpen`` (opening numbers, often null), and moneylines (served here as implied
probabilities). ``spread`` is from the HOME team's
perspective (negative = home favoured), as in CFBD. Nothing marks a line as pre-game, so a
provider row could in principle be a stale or in-game capture; ``parse_lines`` keeps every
provider's row and the provider name so research can choose and audit.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

import httpx

from .ncaab_cbbdata import KNOWN_ESPN_RENAMES
from .ncaab_team_crosswalk import (
    DEFAULT_OVERRIDES,
    MATCHED_OVERRIDE,
    NO_CALL_COLLISION,
    NO_CALL_UNMATCHED,
    CrosswalkResult,
    TeamCrosswalk,
    normalize_team_name,
)

CBBD_BASE_URL = "https://api.collegebasketballdata.com"
CBBD_API_KEY_ENV = "CBBD_API_KEY"
DEFAULT_CACHE_ROOT = Path(__file__).resolve().parents[3] / "data" / "research" / "ncaab_external" / "cbbd"
USER_AGENT = "Mozilla/5.0 (compatible; research-data-client/1.0)"  # generic, no personal details
QUOTA_HEADER = "x-calllimit-remaining"
DEFAULT_QUOTA_FLOOR = 200  # leave room for CFBD work sharing the same monthly budget
RESPONSE_GAME_CAP = 3000  # documented per-call maximum for /games, /lines, /games/teams

_logger = logging.getLogger(__name__)


class CBBDAuthError(RuntimeError):
    """No key, or the API rejected it."""


class CBBDQuotaError(RuntimeError):
    """The monthly call budget is at or below the floor; no request was made."""


def _slug(endpoint: str, params: Mapping[str, Any]) -> str:
    clean = {k: params[k] for k in sorted(params) if params[k] is not None}
    readable = "_".join(f"{k}-{v}" for k, v in clean.items())
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in readable)[:90]
    digest = hashlib.sha256(json.dumps([endpoint, clean], sort_keys=True, default=str).encode()).hexdigest()
    return f"{safe or 'all'}_{digest[:12]}"


class CBBDClient:
    """Cached, quota-aware, single-threaded CBBD client."""

    def __init__(
        self,
        api_key: str | None = None,
        cache_root: str | Path | None = None,
        client: httpx.Client | None = None,
        environ: Mapping[str, str] | None = None,
        quota_floor: int = DEFAULT_QUOTA_FLOOR,
    ) -> None:
        env = os.environ if environ is None else environ
        key = (api_key or env.get(CBBD_API_KEY_ENV) or "").strip()
        if not key:
            raise CBBDAuthError(f"{CBBD_API_KEY_ENV} not set")
        self._api_key = key
        self.client = client or httpx.Client(timeout=120)
        self.cache_root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
        self.quota_floor = quota_floor
        self.calls_made = 0
        self.remaining: int | None = self._load_quota()

    # -- quota -------------------------------------------------------------------------------------
    @property
    def _quota_path(self) -> Path:
        return self.cache_root / "_quota.json"

    def _load_quota(self) -> int | None:
        try:
            return int(json.loads(self._quota_path.read_text(encoding="utf-8"))["remaining"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _record_quota(self, header: str | None) -> None:
        if header is None or not header.strip().lstrip("-").isdigit():
            _logger.warning("CBBD response carried no %s header", QUOTA_HEADER)
            return
        self.remaining = int(header)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self._quota_path.write_text(
            json.dumps({"remaining": self.remaining, "at_utc": datetime.now(UTC).isoformat()}) + "\n",
            encoding="utf-8",
        )

    # -- transport ---------------------------------------------------------------------------------
    def cache_paths(self, endpoint: str, params: Mapping[str, Any]) -> tuple[Path, Path]:
        folder = self.cache_root / endpoint.strip("/").replace("/", "__")
        stem = _slug(endpoint, params)
        return folder / f"{stem}.json", folder / f"{stem}.meta.json"

    def fetch(self, endpoint: str, params: Mapping[str, Any] | None = None, refresh: bool = False) -> Any:
        params = dict(params or {})
        body_path, meta_path = self.cache_paths(endpoint, params)
        if body_path.exists() and meta_path.exists() and not refresh:
            return json.loads(body_path.read_text(encoding="utf-8"))
        if self.remaining is not None and self.remaining < self.quota_floor:
            raise CBBDQuotaError(f"CBBD calls remaining {self.remaining} < floor {self.quota_floor}")
        try:
            response = self.client.get(
                f"{CBBD_BASE_URL}/{endpoint.strip('/')}",
                params={k: v for k, v in params.items() if v is not None},
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Accept": "application/json",
                    "User-Agent": USER_AGENT,
                },
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"CBBD {endpoint} request failed: {type(exc).__name__}") from None
        self.calls_made += 1
        self._record_quota(response.headers.get(QUOTA_HEADER))
        _logger.info(
            "CBBD %s %s -> HTTP %s, calls remaining %s",
            endpoint,
            params,
            response.status_code,
            self.remaining,
        )
        if response.status_code in (401, 403):
            raise CBBDAuthError(f"CBBD rejected the key for {endpoint} (HTTP {response.status_code})")
        if response.status_code == 429:
            raise CBBDQuotaError(f"CBBD rate/quota limit hit on {endpoint} (HTTP 429)")
        if response.status_code != 200:
            raise RuntimeError(f"CBBD {endpoint} returned HTTP {response.status_code}")
        payload = response.json()
        body_path.parent.mkdir(parents=True, exist_ok=True)
        body_path.write_bytes(response.content)
        meta = {
            "source": f"{CBBD_BASE_URL}/{endpoint.strip('/')}",
            "params": params,
            "fetched_at_utc": datetime.now(UTC).isoformat(),
            "http_status": response.status_code,
            "calls_remaining_after": self.remaining,
            "rows": len(payload) if isinstance(payload, list) else None,
            "sha256": hashlib.sha256(response.content).hexdigest(),
        }
        meta_path.write_text(json.dumps(meta, sort_keys=True, indent=1) + "\n", encoding="utf-8")
        return payload

    def fetch_windowed(
        self, endpoint: str, season: int, windows: Iterable[tuple[datetime, datetime]] | None = None
    ) -> list[dict[str, Any]]:
        """All rows for a season, pulled in date windows; a window that hits the cap is halved."""
        pending = list(windows if windows is not None else season_windows(season))
        rows: list[dict[str, Any]] = []
        while pending:
            start, end = pending.pop(0)
            params = {"season": season, "startDateRange": _iso(start), "endDateRange": _iso(end)}
            chunk = self.fetch(endpoint, params)
            if not isinstance(chunk, list):
                raise TypeError(f"CBBD {endpoint} returned {type(chunk).__name__}, expected a list")
            games = {_get(row, "gameId", "id") for row in chunk}
            if len(chunk) >= RESPONSE_GAME_CAP or len(games) >= RESPONSE_GAME_CAP:
                if end - start <= timedelta(days=1):
                    raise ValueError(f"CBBD {endpoint} window {start}..{end} still at the response cap")
                mid = start + (end - start) / 2
                mid = mid.replace(microsecond=0)
                pending[:0] = [(start, mid), (mid + timedelta(seconds=1), end)]
                continue
            rows.extend(chunk)
        return rows

    # -- endpoints ---------------------------------------------------------------------------------
    def teams(self, season: int | None = None) -> list[dict[str, Any]]:
        return self.fetch("teams", {"season": season})

    def games(self, season: int) -> list[dict[str, Any]]:
        return self.fetch_windowed("games", season)

    def lines(self, season: int) -> list[dict[str, Any]]:
        return self.fetch_windowed("lines", season)

    def game_teams(self, season: int) -> list[dict[str, Any]]:
        return self.fetch_windowed("games/teams", season)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def season_windows(season: int) -> list[tuple[datetime, datetime]]:
    """Season = spring year (2026 = 2025-26). Contiguous UTC windows from Oct 15 to May 15."""
    cuts = [
        datetime(season - 1, 10, 15, tzinfo=UTC),
        datetime(season - 1, 12, 1, tzinfo=UTC),
        datetime(season, 1, 1, tzinfo=UTC),
        datetime(season, 2, 1, tzinfo=UTC),
        datetime(season, 3, 1, tzinfo=UTC),
        datetime(season, 5, 15, tzinfo=UTC),
    ]
    return [(a, b - timedelta(seconds=1)) for a, b in pairwise(cuts)]


def _get(row: Mapping[str, Any], *names: str) -> Any:
    """CBBD's raw JSON is camelCase; the generated client is snake_case. Accept either."""
    for name in names:
        if name in row:
            return row[name]
        snake = "".join("_" + ch.lower() if ch.isupper() else ch for ch in name)
        if snake in row:
            return row[snake]
    return None


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _moneyline_prob(value: Any) -> float | None:
    """Provider moneyline -> implied probability; None for missing or malformed (|ml| < 100)."""
    if value is None:
        return None
    ml = float(value)
    if ml <= -100:
        return -ml / (-ml + 100.0)
    if ml >= 100:
        return 100.0 / (ml + 100.0)
    return None


@dataclass(frozen=True)
class CBBDGame:
    game_id: int
    source_id: str | None  # ESPN event id
    season: int
    start_utc: str
    status: str | None
    neutral_site: bool | None
    conference_game: bool | None
    season_type: str | None
    tournament: str | None
    home_team_id: int
    home_team: str
    home_points: int | None
    away_team_id: int
    away_team: str
    away_points: int | None


def parse_games(rows: Iterable[Mapping[str, Any]]) -> list[CBBDGame]:
    out = []
    for row in rows:
        out.append(
            CBBDGame(
                game_id=int(_get(row, "id")),
                source_id=None if _get(row, "sourceId") is None else str(_get(row, "sourceId")),
                season=int(_get(row, "season")),
                start_utc=str(_get(row, "startDate")),
                status=_get(row, "status"),
                neutral_site=_get(row, "neutralSite"),
                conference_game=_get(row, "conferenceGame"),
                season_type=_get(row, "seasonType"),
                tournament=_get(row, "tournament"),
                home_team_id=int(_get(row, "homeTeamId")),
                home_team=str(_get(row, "homeTeam")),
                home_points=_get(row, "homePoints"),
                away_team_id=int(_get(row, "awayTeamId")),
                away_team=str(_get(row, "awayTeam")),
                away_points=_get(row, "awayPoints"),
            )
        )
    return out


@dataclass(frozen=True)
class CBBDLine:
    game_id: int
    season: int
    start_utc: str
    home_team_id: int
    away_team_id: int
    provider: str
    spread: float | None  # home perspective, latest stored number (no timestamp)
    over_under: float | None
    spread_open: float | None
    over_under_open: float | None
    # Moneylines converted to raw implied probability (vig included); the American-odds originals
    # stay only in the raw cache (project rule: prices are probabilities, never American odds).
    home_moneyline_prob: float | None
    away_moneyline_prob: float | None


def parse_lines(rows: Iterable[Mapping[str, Any]]) -> list[CBBDLine]:
    out = []
    for row in rows:
        for line in _get(row, "lines") or []:
            out.append(
                CBBDLine(
                    game_id=int(_get(row, "gameId")),
                    season=int(_get(row, "season")),
                    start_utc=str(_get(row, "startDate")),
                    home_team_id=int(_get(row, "homeTeamId")),
                    away_team_id=int(_get(row, "awayTeamId")),
                    provider=str(_get(line, "provider")),
                    spread=_opt_float(_get(line, "spread")),
                    over_under=_opt_float(_get(line, "overUnder")),
                    spread_open=_opt_float(_get(line, "spreadOpen")),
                    over_under_open=_opt_float(_get(line, "overUnderOpen")),
                    home_moneyline_prob=_moneyline_prob(_get(line, "homeMoneyline")),
                    away_moneyline_prob=_moneyline_prob(_get(line, "awayMoneyline")),
                )
            )
    return out


@dataclass(frozen=True)
class CBBDTeamBox:
    game_id: int
    team_id: int
    opponent_id: int
    is_home: bool | None
    points: float | None
    fga: float | None
    fta: float | None
    oreb: float | None
    tov: float | None
    possessions: float | None  # CBBD's own estimate, kept for cross-checking

    @property
    def complete(self) -> bool:
        return None not in (self.fga, self.fta, self.oreb, self.tov)


def parse_team_box(rows: Iterable[Mapping[str, Any]]) -> list[CBBDTeamBox]:
    out = []
    for row in rows:
        stats = _get(row, "teamStats") or {}
        turnovers = _get(stats, "turnovers")
        tov = _get(turnovers, "total") if isinstance(turnovers, Mapping) else turnovers
        points = _get(stats, "points")
        out.append(
            CBBDTeamBox(
                game_id=int(_get(row, "gameId")),
                team_id=int(_get(row, "teamId")),
                opponent_id=int(_get(row, "opponentId")),
                is_home=_get(row, "isHome"),
                points=_opt_float(_get(points, "total") if isinstance(points, Mapping) else points),
                fga=_opt_float(_get(_get(stats, "fieldGoals") or {}, "attempted")),
                fta=_opt_float(_get(_get(stats, "freeThrows") or {}, "attempted")),
                oreb=_opt_float(_get(_get(stats, "rebounds") or {}, "offensive")),
                tov=_opt_float(tov),
                possessions=_opt_float(_get(stats, "possessions")),
            )
        )
    return out


def season_label(season: int) -> str:
    return f"{season - 1}-{str(season)[-2:]}"


def game_date_utc(start_utc: str) -> date:
    return datetime.fromisoformat(start_utc).date()


def build_cbbd_crosswalk(
    cbbd_teams: Iterable[Mapping[str, Any]], espn_teams: Iterable[Any]
) -> dict[int, CrosswalkResult]:
    """CBBD team id -> ESPN team id via CBBD's ``sourceId`` (an ESPN team id), cross-checked by name.

    The id is accepted when the ESPN list knows it and the names agree (display name, or the school
    resolving to the same id through the strict name crosswalk, or a known ESPN rename). A known id
    whose names disagree is declined; an id absent from the current ESPN list (former D1, non-D1)
    is accepted with a note. Two CBBD ids on one ESPN id decline both."""
    espn_list = list(espn_teams)
    espn = {team.team_id: team for team in espn_list}
    # Overrides only back up the name check here; keep those whose ESPN id is in the list given.
    names = TeamCrosswalk(espn_list, {k: v for k, v in DEFAULT_OVERRIDES.items() if v in espn})
    results: dict[int, CrosswalkResult] = {}
    claims: dict[str, list[int]] = {}
    for row in cbbd_teams:
        cbbd_id = int(_get(row, "id"))
        source = str(_get(row, "sourceId") or "").strip()
        display = str(_get(row, "displayName") or "")
        school = str(_get(row, "school") or "")
        if not source:
            by_name = names.resolve([school, display])
            results[cbbd_id] = by_name
            if by_name.espn_team_id:
                claims.setdefault(by_name.espn_team_id, []).append(cbbd_id)
            continue
        known = espn.get(source)
        if known is not None:
            agrees = (
                normalize_team_name(known.display_name) == normalize_team_name(display)
                or names.resolve([school]).espn_team_id == source
                or names.resolve([display]).espn_team_id == source
                or KNOWN_ESPN_RENAMES.get(source) is not None
            )
            if not agrees:
                results[cbbd_id] = CrosswalkResult(
                    None,
                    NO_CALL_UNMATCHED,
                    f"sourceId {source} is {known.display_name!r}, CBBD says {display!r}",
                )
                continue
            results[cbbd_id] = CrosswalkResult(source, MATCHED_OVERRIDE)
        else:
            results[cbbd_id] = CrosswalkResult(
                source, MATCHED_OVERRIDE, "sourceId not in current ESPN team list"
            )
        claims.setdefault(source, []).append(cbbd_id)
    for espn_id, ids in claims.items():
        if len(ids) > 1:
            for cbbd_id in ids:
                results[cbbd_id] = CrosswalkResult(
                    None, NO_CALL_COLLISION, f"espn {espn_id} claimed by {sorted(ids)}"
                )
    return results
