"""cbbdata API client: Barttorvik (T-Rank) ratings as of a date, for NCAAB research.

Source: the official cbbdata API (``https://www.cbbdata.com/api``), the backend of the
``andreweatherman/cbbdata`` R package. Endpoints and auth were taken from that package's ``R/``
sources (2026-11-20): the API key travels as the ``key`` query parameter; table endpoints
(``torvik/ratings/archive``, ``data/teams``) return parquet; ``torvik/game/prediction`` returns
JSON. barttorvik.com itself is never fetched (its robots.txt blocks AI crawlers; operator decision
2026-11-20).

Auth, fail closed: the key comes from ``CBBDATA_API_KEY`` in ``os.environ`` (callers may first run
the project's ``runtime_paths.load_dotenv_into_environ``). If ``CBBDATA_USERNAME`` and
``CBBDATA_PASSWORD`` are set instead, the key is fetched from ``auth/login`` the way the R
package's ``cbd_login`` does. No key -> ``CBBDataAuthError`` before any request. The key is
redacted from every error message and never written to the cache (cache file names hash the
non-secret parameters only).

Point-in-time contract: ``TorvikRatingsArchive.as_of`` serves only snapshots whose date is
STRICTLY before the game date (date-only granularity: a same-day snapshot may already include
that day's games), and declines with a ``NO_CALL_*`` reason when the newest usable snapshot is
older than ``max_staleness_days`` (e.g. last season's final ratings for an opening-week game).
The archive column set below was inferred from the R package docs, not verified against a live
response (the supplied key was rejected on 2026-11-20); ``parse_ratings_archive`` fails closed on a
schema without the required columns. ``torvik_game_prediction`` is exposed for research only:
whether Torvik recomputes a past date's prediction from that date's ratings is undocumented.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from .ncaab_team_crosswalk import (
    MATCHED_OVERRIDE,
    NO_CALL_COLLISION,
    NO_CALL_UNMATCHED,
    CrosswalkResult,
    EspnTeam,
)

CBBDATA_BASE_URL = "https://www.cbbdata.com/api"
CBBDATA_API_KEY_ENV = "CBBDATA_API_KEY"
CBBDATA_USERNAME_ENV = "CBBDATA_USERNAME"
CBBDATA_PASSWORD_ENV = "CBBDATA_PASSWORD"
DEFAULT_CACHE_ROOT = Path(__file__).resolve().parents[3] / "data" / "research" / "ncaab_external" / "cbbdata"
USER_AGENT = "Mozilla/5.0 (compatible; research-data-client/1.0)"  # generic, no personal details
DEFAULT_MAX_STALENESS_DAYS = 10  # Torvik's archive is daily in-season; a wider gap means off-season

REQUIRED_ARCHIVE_COLUMNS = ("team", "date", "adj_o", "adj_d")
OPTIONAL_ARCHIVE_COLUMNS = ("year", "conf", "barthag", "adj_tempo", "rank")

# ESPN renamed these schools under the same id; cbbdata still carries the old display name.
# Checked by id and name on 2026-11-20 (data/research/ncaab_external/espn_teams/teams.json).
KNOWN_ESPN_RENAMES: dict[str, str] = {
    "2026": "Appalachian State Mountaineers",  # ESPN: App State Mountaineers
    "85": "IUPUI Jaguars",  # ESPN: IU Indianapolis Jaguars
    "2443": "New Orleans Privateers",  # ESPN: LSU New Orleans Privateers
    "2900": "St. Thomas-Minnesota Tommies",  # ESPN: St. Thomas Tommies
    "2837": "Texas A&M-Commerce Lions",  # ESPN: East Texas A&M Lions
}

NO_CALL_NO_SNAPSHOT = "NO_CALL_TORVIK_NO_SNAPSHOT_BEFORE_DATE"
NO_CALL_STALE = "NO_CALL_TORVIK_SNAPSHOT_STALE"
NO_CALL_UNKNOWN_TEAM = "NO_CALL_TORVIK_UNKNOWN_TEAM"


class CBBDataAuthError(RuntimeError):
    """No usable cbbdata credentials, or the API rejected them."""


class CBBDataSchemaError(ValueError):
    """A response did not have the shape this module relies on."""


def resolve_api_key(environ: Mapping[str, str] | None = None, client: httpx.Client | None = None) -> str:
    """``CBBDATA_API_KEY``, else a login with ``CBBDATA_USERNAME``/``CBBDATA_PASSWORD``; raise otherwise."""
    env = os.environ if environ is None else environ
    key = (env.get(CBBDATA_API_KEY_ENV) or "").strip()
    if key:
        return key
    user = (env.get(CBBDATA_USERNAME_ENV) or "").strip()
    password = env.get(CBBDATA_PASSWORD_ENV) or ""
    if not user or not password:
        raise CBBDataAuthError(
            f"{CBBDATA_API_KEY_ENV} not set (nor {CBBDATA_USERNAME_ENV}/{CBBDATA_PASSWORD_ENV})"
        )
    http = client or httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT})
    try:
        response = http.post(f"{CBBDATA_BASE_URL}/auth/login", json={"username": user, "password": password})
    except httpx.HTTPError as exc:
        raise CBBDataAuthError(f"cbbdata login failed: {type(exc).__name__}") from None
    if response.status_code != 200:
        raise CBBDataAuthError(f"cbbdata login rejected (HTTP {response.status_code})")
    payload = response.json()
    value = next(iter(payload.values()), None) if isinstance(payload, dict) else (payload or [None])[0]
    if not isinstance(value, str) or not value.strip():
        raise CBBDataAuthError("cbbdata login returned no api key")
    return value.strip()


class _RedactFilter(logging.Filter):
    def __init__(self, secret: str) -> None:
        super().__init__()
        self.secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if self.secret in message:
            record.msg = message.replace(self.secret, "<redacted>")
            record.args = None
        return True


def _params_slug(params: Mapping[str, Any]) -> str:
    clean = {k: params[k] for k in sorted(params) if k != "key" and params[k] is not None}
    readable = "_".join(f"{k}-{v}" for k, v in clean.items())
    digest = hashlib.sha256(json.dumps(clean, sort_keys=True, default=str).encode()).hexdigest()[:12]
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in readable)[:80]
    return f"{safe or 'all'}_{digest}"


class CBBDataClient:
    """Cached, single-threaded cbbdata client. Raw bytes are cached with fetched_at_utc provenance."""

    def __init__(
        self,
        api_key: str | None = None,
        cache_root: str | Path | None = None,
        client: httpx.Client | None = None,
        environ: Mapping[str, str] | None = None,
        require_key: bool = True,
    ) -> None:
        """``require_key=False`` allows a keyless client for the public ``data/teams`` table only."""
        self.client = client or httpx.Client(timeout=120, headers={"User-Agent": USER_AGENT})
        if api_key:
            self._api_key = api_key.strip()
        elif require_key:
            self._api_key = resolve_api_key(environ, self.client)
        else:
            try:
                self._api_key = resolve_api_key(environ, self.client)
            except CBBDataAuthError:
                self._api_key = ""
        self.cache_root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
        if self._api_key:
            # httpx logs every request URL at INFO, and the key is a query parameter.
            logging.getLogger("httpx").addFilter(_RedactFilter(self._api_key))

    def _redact(self, text: str) -> str:
        return text.replace(self._api_key, "<redacted>") if self._api_key else text

    def cache_paths(self, endpoint: str, params: Mapping[str, Any], ext: str) -> tuple[Path, Path]:
        folder = self.cache_root / endpoint.strip("/").replace("/", "__")
        stem = _params_slug(params)
        return folder / f"{stem}.{ext}", folder / f"{stem}.meta.json"

    def fetch(
        self, endpoint: str, params: Mapping[str, Any], ext: str, refresh: bool = False, public: bool = False
    ) -> bytes:
        """GET ``endpoint`` once and cache it; later calls read the cache unless ``refresh``."""
        body_path, meta_path = self.cache_paths(endpoint, params, ext)
        if body_path.exists() and meta_path.exists() and not refresh:
            return body_path.read_bytes()
        query = {k: v for k, v in params.items() if v is not None}
        if not public:
            if not self._api_key:
                raise CBBDataAuthError(f"{CBBDATA_API_KEY_ENV} not set; {endpoint} needs a key")
            query["key"] = self._api_key
        try:
            response = self.client.get(f"{CBBDATA_BASE_URL}/{endpoint.strip('/')}", params=query)
        except httpx.HTTPError as exc:
            raise RuntimeError(
                self._redact(f"cbbdata {endpoint} request failed: {type(exc).__name__}: {exc}")
            ) from None
        if response.status_code in (401, 403):
            raise CBBDataAuthError(
                f"cbbdata rejected the API key for {endpoint} (HTTP {response.status_code})"
            )
        if response.status_code != 200:
            raise RuntimeError(f"cbbdata {endpoint} returned HTTP {response.status_code}")
        body = response.content
        body_path.parent.mkdir(parents=True, exist_ok=True)
        body_path.write_bytes(body)
        meta = {
            "source": f"{CBBDATA_BASE_URL}/{endpoint.strip('/')}",
            "params": {k: v for k, v in sorted(params.items()) if k != "key"},
            "fetched_at_utc": datetime.now(UTC).isoformat(),
            "http_status": response.status_code,
            "content_type": response.headers.get("content-type"),
            "sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body),
        }
        meta_path.write_text(json.dumps(meta, sort_keys=True, indent=1) + "\n", encoding="utf-8")
        return body

    def torvik_ratings_archive(self, year: int, refresh: bool = False) -> list[TorvikSnapshot]:
        """Daily T-Rank snapshots for one season (``year`` = the season's spring year, 2026 = 2025-26)."""
        return parse_ratings_archive(
            self.fetch("torvik/ratings/archive", {"year": int(year)}, "parquet", refresh=refresh)
        )

    def teams(self, refresh: bool = False) -> list[dict[str, Any]]:
        """cbbdata's team dictionary (torvik_team, espn_id, espn_display, ...). Public endpoint."""
        return parse_parquet_rows(self.fetch("data/teams", {}, "parquet", refresh=refresh, public=True))

    def torvik_game_prediction(self, team: str, opp: str, game_date: date, location: str = "H") -> Any:
        """Torvik's projection for one matchup (JSON). Research only; see module docstring on PIT."""
        if location not in ("H", "A", "N"):
            raise ValueError("location must be H, A or N")
        params = {"team": team, "opp": opp, "date": game_date.strftime("%Y%m%d"), "location": location}
        return json.loads(self.fetch("torvik/game/prediction", params, "json"))


def parse_parquet_rows(raw: bytes) -> list[dict[str, Any]]:
    import polars as pl

    try:
        frame = pl.read_parquet(io.BytesIO(raw))
    except Exception as exc:  # noqa: BLE001 -- any decode failure means "not the parquet we expect"
        raise CBBDataSchemaError(f"cbbdata response is not parquet: {type(exc).__name__}") from None
    return frame.to_dicts()


@dataclass(frozen=True)
class TorvikSnapshot:
    team: str
    snapshot_date: date
    adj_o: float
    adj_d: float
    adj_tempo: float | None
    barthag: float | None
    rank: int | None
    year: int | None
    conf: str | None

    @property
    def adj_em(self) -> float:
        return self.adj_o - self.adj_d


def _to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        if len(text) >= 10 and text[4] == "-":
            return date.fromisoformat(text[:10])
        if len(text) == 8 and text.isdigit():
            return date(int(text[:4]), int(text[4:6]), int(text[6:]))
    except ValueError:
        pass
    raise CBBDataSchemaError(f"unparseable archive date: {text!r}")


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


def parse_ratings_archive(raw: bytes) -> list[TorvikSnapshot]:
    rows = parse_parquet_rows(raw)
    if rows:
        missing = [c for c in REQUIRED_ARCHIVE_COLUMNS if c not in rows[0]]
        if missing:
            raise CBBDataSchemaError(f"torvik archive missing columns {missing}; got {sorted(rows[0])}")
    out = []
    for row in rows:
        if row.get("adj_o") is None or row.get("adj_d") is None or not row.get("team"):
            continue
        out.append(
            TorvikSnapshot(
                team=str(row["team"]),
                snapshot_date=_to_date(row["date"]),
                adj_o=float(row["adj_o"]),
                adj_d=float(row["adj_d"]),
                adj_tempo=_opt_float(row.get("adj_tempo")),
                barthag=_opt_float(row.get("barthag")),
                rank=None if row.get("rank") is None else int(row["rank"]),
                year=None if row.get("year") is None else int(row["year"]),
                conf=None if row.get("conf") is None else str(row["conf"]),
            )
        )
    return out


@dataclass(frozen=True)
class TorvikLookup:
    snapshot: TorvikSnapshot | None
    reason: str


class TorvikRatingsArchive:
    """Serves "team T's T-Rank ratings as known before date D"."""

    def __init__(self, snapshots: Iterable[TorvikSnapshot]) -> None:
        by_team: dict[str, dict[date, TorvikSnapshot]] = {}
        for snap in snapshots:
            # One row per team per date; a duplicate date keeps the first seen (cache order).
            by_team.setdefault(snap.team, {}).setdefault(snap.snapshot_date, snap)
        self._by_team = {team: sorted(days.items()) for team, days in by_team.items()}

    def teams(self) -> set[str]:
        return set(self._by_team)

    def as_of(
        self, torvik_team: str, game_date: date, max_staleness_days: int = DEFAULT_MAX_STALENESS_DAYS
    ) -> TorvikLookup:
        series = self._by_team.get(torvik_team)
        if not series:
            return TorvikLookup(None, NO_CALL_UNKNOWN_TEAM)
        best: TorvikSnapshot | None = None
        for day, snap in series:
            if day >= game_date:  # strictly before: a same-day snapshot may include the game itself
                break
            best = snap
        if best is None:
            return TorvikLookup(None, NO_CALL_NO_SNAPSHOT)
        if (game_date - best.snapshot_date).days > max_staleness_days:
            return TorvikLookup(None, NO_CALL_STALE)
        return TorvikLookup(best, "OK")


def build_torvik_crosswalk(
    cbd_teams: Iterable[Mapping[str, Any]], espn_teams: Iterable[EspnTeam]
) -> dict[str, CrosswalkResult]:
    """torvik_team -> ESPN team id from cbbdata's own ``espn_id`` column, cross-checked.

    Declines a team whose espn_id is blank, unknown to the ESPN list *and* not a historical id we
    were told about, or shared by two Torvik names. A known ESPN id whose display name disagrees with
    cbbdata's ``espn_display`` is declined too (a silently re-pointed id is exactly the UConn/CCSU
    failure mode)."""
    espn = {team.team_id: team for team in espn_teams}
    results: dict[str, CrosswalkResult] = {}
    claims: dict[str, list[str]] = {}
    for row in cbd_teams:
        name = str(row.get("torvik_team") or "").strip()
        if not name:
            continue
        espn_id = str(row.get("espn_id") or "").strip()
        if not espn_id:
            results[name] = CrosswalkResult(None, NO_CALL_UNMATCHED, "no espn_id in cbbdata")
            continue
        known = espn.get(espn_id)
        display = str(row.get("espn_display") or "").strip()
        renamed = KNOWN_ESPN_RENAMES.get(espn_id) == display
        if (
            known is not None
            and display
            and not renamed
            and known.display_name.replace("-", " ") != display.replace("-", " ")
        ):
            results[name] = CrosswalkResult(
                None, NO_CALL_UNMATCHED, f"espn {espn_id} is {known.display_name!r}, cbbdata says {display!r}"
            )
            continue
        detail = "" if known is not None else "espn id not in current ESPN team list (former D1)"
        results[name] = CrosswalkResult(espn_id, MATCHED_OVERRIDE, detail)
        claims.setdefault(espn_id, []).append(name)
    for espn_id, names in claims.items():
        if len(names) > 1:
            for name in names:
                results[name] = CrosswalkResult(
                    None, NO_CALL_COLLISION, f"espn {espn_id} claimed by {sorted(names)}"
                )
    return results
