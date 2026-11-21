"""CollegeFootballData.com (CFBD) client: NCAAF preseason priors and betting lines (v7 research).

API: ``https://api.collegefootballdata.com``, ``Authorization: Bearer <key>`` with the key in
``CFBD_API_KEY`` (``os.environ``; callers may first run ``runtime_paths.load_dotenv_into_environ``).
No key -> ``CFBDAuthError`` before any request. The key is a header, never a URL parameter, so it
never reaches the cache, its file names, error text or logs.

Quota: the monthly call budget is shared with CollegeBasketballData (same account). The server
reports what is left in ``X-CallLimit-Remaining``; the client records it after every call and
refuses to request below ``quota_floor``. Because other sessions spend the same budget, the client
also keeps its own ledger (``_calls.jsonl`` in the cache root, one line per HTTP attempt, retries
included) and refuses once ``max_calls`` attempts have been made across processes. Responses are
cached as raw JSON with ``fetched_at_utc`` and the key-free parameters; a cached response is never
refetched unless asked, so rerunning a backfill costs nothing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

import httpx

CFBD_BASE_URL = "https://api.collegefootballdata.com"
CFBD_API_KEY_ENV = "CFBD_API_KEY"
DEFAULT_CACHE_ROOT = Path(__file__).resolve().parents[3] / "data" / "research" / "ncaaf_v7" / "cfbd" / "raw"
USER_AGENT = "Mozilla/5.0 (compatible; research-data-client/1.0)"  # generic, no personal details
QUOTA_HEADER = "x-calllimit-remaining"
DEFAULT_QUOTA_FLOOR = 200  # leave room for the CBBD work sharing the same monthly budget
DEFAULT_MAX_CALLS = 600  # this task's own ceiling, across processes, retries included
MAX_ATTEMPTS = 3
BACKOFF_S = (2.0, 6.0)

_logger = logging.getLogger(__name__)


class CFBDAuthError(RuntimeError):
    """No key, or the API rejected it."""


class CFBDQuotaError(RuntimeError):
    """Budget floor / call ceiling reached, or the server answered 429; no further request is made."""


def _slug(endpoint: str, params: Mapping[str, Any]) -> str:
    clean = {k: params[k] for k in sorted(params) if params[k] is not None}
    readable = "_".join(f"{k}-{v}" for k, v in clean.items())
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in readable)[:90]
    digest = hashlib.sha256(json.dumps([endpoint, clean], sort_keys=True, default=str).encode()).hexdigest()
    return f"{safe or 'all'}_{digest[:12]}"


class CFBDClient:
    """Cached, quota-aware, single-threaded CFBD client."""

    def __init__(
        self,
        api_key: str | None = None,
        cache_root: str | Path | None = None,
        client: httpx.Client | None = None,
        environ: Mapping[str, str] | None = None,
        quota_floor: int = DEFAULT_QUOTA_FLOOR,
        max_calls: int = DEFAULT_MAX_CALLS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        env = os.environ if environ is None else environ
        key = (api_key or env.get(CFBD_API_KEY_ENV) or "").strip()
        if not key:
            raise CFBDAuthError(f"{CFBD_API_KEY_ENV} not set")
        self._api_key = key
        self.client = client or httpx.Client(timeout=120)
        self.cache_root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
        self.quota_floor = quota_floor
        self.max_calls = max_calls
        self._sleep = sleep
        self.calls_made = 0  # this process
        self.remaining: int | None = self._load_quota()

    # -- quota and call ledger ---------------------------------------------------------------------
    @property
    def _quota_path(self) -> Path:
        return self.cache_root / "_quota.json"

    @property
    def _ledger_path(self) -> Path:
        return self.cache_root / "_calls.jsonl"

    def _load_quota(self) -> int | None:
        try:
            return int(json.loads(self._quota_path.read_text(encoding="utf-8"))["remaining"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def ledger_calls(self) -> int:
        """HTTP attempts recorded in the ledger by every process using this cache root."""
        try:
            with self._ledger_path.open(encoding="utf-8") as handle:
                return sum(1 for line in handle if line.strip())
        except OSError:
            return 0

    def _log_attempt(self, endpoint: str, params: Mapping[str, Any], status: int | str) -> None:
        self.cache_root.mkdir(parents=True, exist_ok=True)
        record = {
            "at_utc": datetime.now(UTC).isoformat(),
            "endpoint": endpoint,
            "params": dict(params),
            "status": status,
            "remaining_after": self.remaining,
        }
        with self._ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")

    def _record_quota(self, header: str | None) -> None:
        if header is None or not header.strip().lstrip("-").isdigit():
            _logger.warning("CFBD response carried no %s header", QUOTA_HEADER)
            return
        self.remaining = int(header)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self._quota_path.write_text(
            json.dumps({"remaining": self.remaining, "at_utc": datetime.now(UTC).isoformat()}) + "\n",
            encoding="utf-8",
        )

    def _guard(self) -> None:
        if self.remaining is not None and self.remaining < self.quota_floor:
            raise CFBDQuotaError(f"CFBD calls remaining {self.remaining} < floor {self.quota_floor}")
        used = self.ledger_calls()
        if used >= self.max_calls:
            raise CFBDQuotaError(f"CFBD call ledger at {used} >= ceiling {self.max_calls}")

    # -- transport ---------------------------------------------------------------------------------
    def cache_paths(self, endpoint: str, params: Mapping[str, Any]) -> tuple[Path, Path]:
        folder = self.cache_root / endpoint.strip("/").replace("/", "__")
        stem = _slug(endpoint, params)
        return folder / f"{stem}.json", folder / f"{stem}.meta.json"

    def is_cached(self, endpoint: str, params: Mapping[str, Any] | None = None) -> bool:
        body, meta = self.cache_paths(endpoint, dict(params or {}))
        return body.exists() and meta.exists()

    def cached_meta(self, endpoint: str, params: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        _, meta = self.cache_paths(endpoint, dict(params or {}))
        try:
            return json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _get(self, endpoint: str, params: Mapping[str, Any]) -> httpx.Response:
        url = f"{CFBD_BASE_URL}/{endpoint.strip('/')}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        for attempt in range(MAX_ATTEMPTS):
            self._guard()
            try:
                response = self.client.get(url, params=dict(params), headers=headers)
            except httpx.HTTPError as exc:
                self.calls_made += 1
                self._log_attempt(endpoint, params, type(exc).__name__)
                if attempt + 1 >= MAX_ATTEMPTS:
                    raise RuntimeError(f"CFBD {endpoint} request failed: {type(exc).__name__}") from None
                self._sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
                continue
            self.calls_made += 1
            self._record_quota(response.headers.get(QUOTA_HEADER))
            self._log_attempt(endpoint, params, response.status_code)
            _logger.info(
                "CFBD %s %s -> HTTP %s, calls remaining %s",
                endpoint,
                dict(params),
                response.status_code,
                self.remaining,
            )
            if response.status_code >= 500 and attempt + 1 < MAX_ATTEMPTS:
                self._sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
                continue
            return response
        raise AssertionError("unreachable")  # pragma: no cover

    def fetch(self, endpoint: str, params: Mapping[str, Any] | None = None, refresh: bool = False) -> Any:
        params = {k: v for k, v in dict(params or {}).items() if v is not None}
        body_path, meta_path = self.cache_paths(endpoint, params)
        if body_path.exists() and meta_path.exists() and not refresh:
            return json.loads(body_path.read_text(encoding="utf-8"))
        response = self._get(endpoint, params)
        if response.status_code in (401, 403):
            raise CFBDAuthError(f"CFBD refused {endpoint} (HTTP {response.status_code})")
        if response.status_code == 429:
            raise CFBDQuotaError(f"CFBD rate/quota limit hit on {endpoint} (HTTP 429)")
        if response.status_code != 200:
            raise RuntimeError(f"CFBD {endpoint} returned HTTP {response.status_code}")
        payload = response.json()
        body_path.parent.mkdir(parents=True, exist_ok=True)
        body_path.write_bytes(response.content)
        meta = {
            "source": f"{CFBD_BASE_URL}/{endpoint.strip('/')}",
            "params": params,
            "fetched_at_utc": datetime.now(UTC).isoformat(),
            "http_status": response.status_code,
            "calls_remaining_after": self.remaining,
            "rows": len(payload) if isinstance(payload, list) else None,
            "sha256": hashlib.sha256(response.content).hexdigest(),
        }
        meta_path.write_text(json.dumps(meta, sort_keys=True, indent=1) + "\n", encoding="utf-8")
        return payload

    def info(self) -> dict[str, Any]:
        """Account info (``remainingCalls``, tier features); never cached."""
        response = self._get("info", {})
        if response.status_code != 200:
            raise RuntimeError(f"CFBD info returned HTTP {response.status_code}")
        payload = response.json()
        return payload if isinstance(payload, dict) else {"raw": payload}


# ======================================================================================== normalizers
# Identity. CFBD team ids are ESPN team ids and CFBD game ids are ESPN event ids: on 2023 all 1,416
# CFBD /lines games were ESPN-history events, 1,409 with identical home/away team ids and 7 (neutral
# site) with the two sides swapped. An id is still only passed on as an ESPN id when the ESPN history
# has seen it; anything else keeps its CFBD id, no ESPN id, and a reason code.
MATCHED_ESPN_ID = "MATCHED_ESPN_ID"  # CFBD team id seen as an ESPN team id in the ESPN history
NO_CALL_ESPN_ID_UNSEEN = "NO_CALL_ESPN_ID_UNSEEN"  # CFBD team id never seen in the ESPN history
NO_CALL_UNMATCHED_NAME = "NO_CALL_UNMATCHED_NAME"  # school name not in CFBD /teams
NO_CALL_NAME_COLLISION = "NO_CALL_NAME_COLLISION"  # school name on several CFBD ids, none preferred

EVENT_SAME_ORIENTATION = "MATCHED_SAME_ORIENTATION"
EVENT_SWAPPED = "MATCHED_HOME_AWAY_SWAPPED"  # neutral site: ESPN and CFBD disagree on the home side
NO_CALL_EVENT_UNSEEN = "NO_CALL_EVENT_UNSEEN"  # game id not in the ESPN history (2014-15, FCS-only)
NO_CALL_EVENT_TEAM_MISMATCH = "NO_CALL_EVENT_TEAM_MISMATCH"  # same id, different teams

_DIVISION_CLASSES = ("fbs", "fcs")

# Former names that CFBD's season tables still use but /teams no longer lists for the real team
# (only unclassified stubs carry them). Each id was checked against the ESPN history's names:
# 399 "Albany Great Danes", 2545 "Southeastern Louisiana Lions", 2598 "St. Francis (PA) Red Flash",
# 292 "UT Rio Grande Valley Vaqueros".
NAME_OVERRIDES = {"Albany": 399, "Southeastern Louisiana": 2545, "Saint Francis (PA)": 2598, "UTRGV": 292}


@dataclass(frozen=True)
class TeamMatch:
    cfbd_team_id: int | None
    espn_team_id: str | None
    status: str
    note: str | None = None


def fold_name(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().casefold()
    return " ".join(folded.replace(".", "").replace("'", "").split())


class CFBDTeamDirectory:
    """CFBD school name -> CFBD team id -> ESPN team id (only when the ESPN history knows that id).

    A school name shared by several CFBD ids ("Charlotte" is both the FBS 49ers and a lower-division
    school) resolves to the single FBS/FCS candidate, else the single one the ESPN history has seen,
    else declines with ``NO_CALL_NAME_COLLISION``."""

    def __init__(self, teams: Iterable[Mapping[str, Any]], verified_espn_ids: Iterable[str]) -> None:
        self.teams = {int(t["id"]): dict(t) for t in teams}
        self.verified = {str(x) for x in verified_espn_ids}
        self._by_school: dict[str, list[int]] = {}
        self._by_alt: dict[str, list[int]] = {}
        for tid, team in self.teams.items():
            self._by_school.setdefault(fold_name(str(team.get("school") or "")), []).append(tid)
            for alt in team.get("alternateNames") or []:
                self._by_alt.setdefault(fold_name(str(alt)), []).append(tid)
        self._cache: dict[str, TeamMatch] = {}

    def _pick(self, ids: list[int]) -> int | None:
        # /teams carries unclassified stub entries (ids >= 1,000,000, no mascot) that reuse real
        # schools' names; a classified team always wins over them.
        classified = [i for i in ids if self.teams[i].get("classification")]
        ids = classified or ids
        if len(ids) == 1:
            return ids[0]
        division = [i for i in ids if self.teams[i].get("classification") in _DIVISION_CLASSES]
        if len(division) == 1:
            return division[0]
        seen = [i for i in ids if str(i) in self.verified]
        if len(seen) == 1:
            return seen[0]
        return None

    def by_id(self, cfbd_id: int) -> TeamMatch:
        if str(cfbd_id) in self.verified:
            return TeamMatch(cfbd_id, str(cfbd_id), MATCHED_ESPN_ID)
        return TeamMatch(cfbd_id, None, NO_CALL_ESPN_ID_UNSEEN)

    def resolve(self, school: str) -> TeamMatch:
        hit = self._cache.get(school)
        if hit is not None:
            return hit
        key = fold_name(school)
        ids = sorted(set(self._by_school.get(key) or []))
        if not any(self.teams[i].get("classification") for i in ids):
            ids = sorted(set(self._by_alt.get(key) or [])) or ids
        override = NAME_OVERRIDES.get(school)
        if override is not None and override in self.teams:
            ids = [override]
        if not ids:
            match = TeamMatch(None, None, NO_CALL_UNMATCHED_NAME, school)
        else:
            picked = self._pick(ids)
            if picked is None:
                match = TeamMatch(None, None, NO_CALL_NAME_COLLISION, f"{school!r} -> {ids}")
            else:
                match = self.by_id(picked)
        self._cache[school] = match
        return match


def _opt_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def moneyline_implied_prob(value: Any) -> float | None:
    """Provider moneyline -> raw implied probability (vig included); None if missing or malformed.

    Project rule: prices are probabilities, never American odds. This is the only place the
    provider's moneyline is read; the originals stay in the raw cache."""
    ml = _opt_float(value)
    if ml is None:
        return None
    if ml <= -100:
        return -ml / (-ml + 100.0)
    if ml >= 100:
        return 100.0 / (ml + 100.0)
    return None


def parse_utc(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:  # never let a naive stamp be read as local time
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def iso_utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------------- season-level tables
def parse_returning(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[int, str], dict[str, float | None]]:
    """/player/returning -> {(season, school): fields}. CFBD returning production is OFFENSE ONLY
    (the share of last season's offensive PPA and usage that is back); it has no defensive figure."""
    out: dict[tuple[int, str], dict[str, float | None]] = {}
    for row in rows:
        out[(int(row["season"]), str(row["team"]))] = {
            "returning_off_ppa_pct": _opt_float(row.get("percentPPA")),
            "returning_off_passing_ppa_pct": _opt_float(row.get("percentPassingPPA")),
            "returning_off_rushing_ppa_pct": _opt_float(row.get("percentRushingPPA")),
            "returning_off_receiving_ppa_pct": _opt_float(row.get("percentReceivingPPA")),
            "returning_off_usage_pct": _opt_float(row.get("usage")),
            "returning_off_total_ppa": _opt_float(row.get("totalPPA")),
        }
    return out


def parse_talent(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[int, str], float | None]:
    """/talent -> {(year, school): 247 team talent composite}."""
    return {
        (int(r["year"]), str(r.get("team") or r.get("school"))): _opt_float(r.get("talent")) for r in rows
    }


def parse_recruiting(
    rows: Iterable[Mapping[str, Any]],
) -> dict[tuple[int, str], tuple[int | None, float | None]]:
    """/recruiting/teams -> {(class year, school): (rank, points)}. Class year Y signs by February Y."""
    out: dict[tuple[int, str], tuple[int | None, float | None]] = {}
    for r in rows:
        rank = r.get("rank")
        out[(int(r["year"]), str(r["team"]))] = (
            None if rank is None else int(rank),
            _opt_float(r.get("points")),
        )
    return out


def parse_sp(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[int, str], dict[str, float | None]]:
    """/ratings/sp -> {(year, school): ratings}. END-OF-SEASON numbers: only year Y-1 may feed season Y
    (the same season's table is hindsight)."""
    out: dict[tuple[int, str], dict[str, float | None]] = {}
    for r in rows:
        team = str(r.get("team") or "")
        if not team or team.casefold().replace(" ", "") == "nationalaverages":
            continue
        out[(int(r["year"]), team)] = {
            "rating": _opt_float(r.get("rating")),
            "offense": _opt_float((r.get("offense") or {}).get("rating")),
            "defense": _opt_float((r.get("defense") or {}).get("rating")),
        }
    return out


PRESEASON_POLLS = {"AP Top 25": "ap", "Coaches Poll": "coaches"}


def parse_preseason_polls(weeks: Iterable[Mapping[str, Any]]) -> dict[int, dict[str, int]]:
    """/rankings for one season -> {CFBD team id: {"ap": rank, "coaches": rank}} from regular week 1.

    CFBD's regular-season week 1 is the preseason poll (checked on 2023: LSU #5 and Florida State #8
    in week 1; Florida State #4 and LSU out of the top 8 in week 2, after their opener)."""
    out: dict[int, dict[str, int]] = {}
    for week in weeks:
        if week.get("seasonType") != "regular" or int(week.get("week") or 0) != 1:
            continue
        for poll in week.get("polls") or []:
            label = PRESEASON_POLLS.get(str(poll.get("poll")))
            if label is None:
                continue
            for rank in poll.get("ranks") or []:
                if rank.get("teamId") is None or rank.get("rank") is None:
                    continue
                out.setdefault(int(rank["teamId"]), {})[label] = int(rank["rank"])
    return out


@dataclass
class PortalAgg:
    out_count: int = 0
    in_count: int = 0
    out_stars_sum: int = 0
    in_stars_sum: int = 0
    excluded_after_cutoff: int = 0  # entries dated after the observation time, left out


@dataclass
class PortalStats:
    entries: int = 0
    after_cutoff: int = 0
    undated: int = 0
    counted: int = 0
    counted_no_destination: int = 0


def aggregate_portal(
    rows: Iterable[Mapping[str, Any]], cutoff_utc: datetime
) -> tuple[dict[str, PortalAgg], PortalStats]:
    """/player/portal for one season -> per-school counts over entries dated at or before ``cutoff_utc``.

    ``transferDate`` is when the player ENTERED the portal, so ``out`` is point-in-time on it. The
    ``destination`` field carries no date: it is today's value, filled in whenever the player
    committed, so ``in`` counts are as-of-fetch (a commitment after the cutoff leaks into them).
    Undated entries are excluded (fail closed)."""
    teams: dict[str, PortalAgg] = {}
    stats = PortalStats()
    for row in rows:
        stats.entries += 1
        stamp = row.get("transferDate")
        origin, dest = row.get("origin"), row.get("destination")
        if not stamp:
            stats.undated += 1
            continue
        stars = int(row.get("stars") or 0)
        if parse_utc(str(stamp)) > cutoff_utc:
            stats.after_cutoff += 1
            for name in (origin, dest):
                if name:
                    teams.setdefault(str(name), PortalAgg()).excluded_after_cutoff += 1
            continue
        stats.counted += 1
        if origin:
            agg = teams.setdefault(str(origin), PortalAgg())
            agg.out_count += 1
            agg.out_stars_sum += stars
        if dest:
            agg = teams.setdefault(str(dest), PortalAgg())
            agg.in_count += 1
            agg.in_stars_sum += stars
        else:
            stats.counted_no_destination += 1
    return teams, stats


# Fixed a priori, never fitted: upperclass classes carry more of the on-field roster.
RECRUITING_WEIGHTS = {0: 0.15, 1: 0.25, 2: 0.30, 3: 0.30}  # key = season - class year


def recruiting_window(
    recruiting: Mapping[tuple[int, Any], tuple[int | None, float | None]], season: int, school: Any
) -> dict[str, Any]:
    """Classes Y..Y-3 for season Y. One missing class is renormalised over; two or more -> None."""
    points = {k: (recruiting.get((season - k, school)) or (None, None))[1] for k in RECRUITING_WEIGHTS}
    present = {k: v for k, v in points.items() if v is not None}
    out: dict[str, Any] = {
        (f"recruiting_points_class_y_minus{k}" if k else "recruiting_points_class_y"): points[k]
        for k in RECRUITING_WEIGHTS
    }
    out["recruiting_rank_class_y"] = (recruiting.get((season, school)) or (None, None))[0]
    out["recruiting_classes_present"] = len(present)
    if len(present) >= 3:
        weight = sum(RECRUITING_WEIGHTS[k] for k in present)
        out["recruiting_points_4yr_weighted"] = (
            sum(RECRUITING_WEIGHTS[k] * v for k, v in present.items()) / weight
        )
        out["recruiting_points_4yr_mean"] = sum(present.values()) / len(present)
    else:
        out["recruiting_points_4yr_weighted"] = None
        out["recruiting_points_4yr_mean"] = None
    return out


TeamKey = int | str  # resolved CFBD team id, else "name:<school>" for a name CFBD /teams cannot place
_V = TypeVar("_V")


def _team_key(directory: CFBDTeamDirectory, name: str) -> TeamKey:
    match = directory.resolve(name)
    return match.cfbd_team_id if match.cfbd_team_id is not None else f"name:{name}"


def _rekey(
    table: Mapping[tuple[int, str], _V], directory: CFBDTeamDirectory
) -> tuple[dict[tuple[int, TeamKey], _V], dict[tuple[int, TeamKey], str]]:
    """(year, school name) -> (year, team key). Two names on one key in one year: the first (sorted)
    wins; the backfill checked that this never happens in the fetched tables."""
    out: dict[tuple[int, TeamKey], _V] = {}
    names: dict[tuple[int, TeamKey], str] = {}
    for (year, name), value in sorted(table.items(), key=lambda item: item[0]):
        key = (year, _team_key(directory, name))
        if key not in out:
            out[key] = value
            names[key] = name
    return out, names


@dataclass(frozen=True)
class SeasonInputs:
    """Everything ``build_team_season_priors`` reads for one season (tables may span several years)."""

    returning: Mapping[tuple[int, str], Mapping[str, float | None]]
    talent: Mapping[tuple[int, str], float | None]
    recruiting: Mapping[tuple[int, str], tuple[int | None, float | None]]
    sp: Mapping[tuple[int, str], Mapping[str, float | None]]
    polls: Mapping[int, Mapping[str, int]]  # this season's preseason polls, by CFBD team id
    portal: Mapping[str, PortalAgg] | None  # None = no portal data for this season


# Revision status per field group, stated plainly in every row. Nothing CFBD serves carries a
# publication timestamp, so no group is verified as-published; see the coverage report.
PROVENANCE_NOTES = {
    "returning": "cfbd current value; computed from rosters, no publication timestamp",
    "talent": "cfbd current value of the 247 team talent composite for that year; publication date unknown",
    "talent_prev": "previous year's composite, published before this season",
    "recruiting": "247 class points; class Y final by February Y; later rating revisions possible",
    "sp_prev": "previous season's final SP+ (published January); historical tables may be restated",
    "polls": "preseason AP / Coaches poll (CFBD week 1); historical record",
    "portal_out": "point-in-time on transferDate (portal entry date)",
    "portal_in": "destination has no date: as-of-fetch, can include post-cutoff commitments",
}


def build_team_season_priors(
    season: int,
    observed_at_utc: datetime,
    directory: CFBDTeamDirectory,
    inputs: SeasonInputs,
    *,
    fetched_at_utc: str | None = None,
    in_season_fetch: bool = False,
) -> list[dict[str, Any]]:
    """One row per school with any of: this season's returning production, talent or recruiting class,
    last season's SP+, or a preseason poll rank. Portal-only schools (mostly non-D1 destinations) get no
    row; their portal counts are reported separately."""
    # Join on the resolved CFBD team id, not the name: the tables spell some schools differently from
    # year to year and from the portal ("Southeastern Louisiana" / "SE Louisiana", "Saint Francis (PA)"
    # / "Saint Francis"), and a name join turns those into silent zeros or nulls.
    returning, names = _rekey(inputs.returning, directory)
    talent, talent_names = _rekey(inputs.talent, directory)
    recruiting, rec_names = _rekey(inputs.recruiting, directory)
    sp, sp_names = _rekey(inputs.sp, directory)
    for table in (talent_names, rec_names, sp_names):
        for k, v in table.items():
            names.setdefault(k, v)
    keys = {k for (y, k) in returning if y == season}
    keys |= {k for (y, k) in talent if y == season}
    keys |= {k for (y, k) in recruiting if y == season}
    keys |= {k for (y, k) in sp if y == season - 1}
    keys |= set(inputs.polls)  # ranked but absent from every table: still emit, keyed by id
    portal: dict[TeamKey, PortalAgg] = {}
    for name, agg in (inputs.portal or {}).items():
        total = portal.setdefault(_team_key(directory, name), PortalAgg())
        total.out_count += agg.out_count
        total.in_count += agg.in_count
        total.out_stars_sum += agg.out_stars_sum
        total.in_stars_sum += agg.in_stars_sum
        total.excluded_after_cutoff += agg.excluded_after_cutoff

    def label(key: TeamKey) -> str:
        for year in (season, season - 1, season - 2, season - 3):
            if (year, key) in names:
                return names[(year, key)]
        if isinstance(key, int) and key in directory.teams:
            return str(directory.teams[key]["school"])
        return str(key)

    rows: list[dict[str, Any]] = []
    for key in sorted(keys, key=lambda k: (label(k), str(k))):
        school = label(key)
        match = directory.by_id(key) if isinstance(key, int) else directory.resolve(school)
        team = directory.teams.get(key) if isinstance(key, int) else None
        poll = inputs.polls.get(key, {}) if isinstance(key, int) else {}
        ret = returning.get((season, key)) or {}
        sp_prev = sp.get((season - 1, key)) or {}
        row: dict[str, Any] = {
            "season": season,
            "cfbd_team": school,
            "cfbd_team_id": match.cfbd_team_id,
            "espn_team_id": match.espn_team_id,
            "espn_match_status": match.status,
            "espn_match_note": match.note,
            "classification_current": team.get("classification") if team else None,
            "conference_current": team.get("conference") if team else None,
            "returning_off_ppa_pct": ret.get("returning_off_ppa_pct"),
            "returning_off_passing_ppa_pct": ret.get("returning_off_passing_ppa_pct"),
            "returning_off_rushing_ppa_pct": ret.get("returning_off_rushing_ppa_pct"),
            "returning_off_receiving_ppa_pct": ret.get("returning_off_receiving_ppa_pct"),
            "returning_off_usage_pct": ret.get("returning_off_usage_pct"),
            "returning_def_pct": None,  # CFBD publishes no defensive returning production
            "talent": talent.get((season, key)),
            "talent_prev_season": talent.get((season - 1, key)),
            **recruiting_window(recruiting, season, key),
            "sp_prev_season_rating": sp_prev.get("rating"),
            "sp_prev_season_offense": sp_prev.get("offense"),
            "sp_prev_season_defense": sp_prev.get("defense"),
            "preseason_ap_rank": poll.get("ap"),
            "preseason_coaches_rank": poll.get("coaches"),
        }
        if inputs.portal is None:
            row.update(
                portal_available=False,
                portal_out_count=None,
                portal_in_count=None,
                portal_net=None,
                portal_out_stars_sum=None,
                portal_in_stars_sum=None,
                portal_excluded_after_cutoff=None,
            )
        else:
            agg = portal.get(key, PortalAgg())
            row.update(
                portal_available=True,
                portal_out_count=agg.out_count,
                portal_in_count=agg.in_count,
                portal_net=agg.in_count - agg.out_count,
                portal_out_stars_sum=agg.out_stars_sum,
                portal_in_stars_sum=agg.in_stars_sum,
                portal_excluded_after_cutoff=agg.excluded_after_cutoff,
            )
        row.update(
            observed_at_utc=iso_utc(observed_at_utc),
            observed_at_rule="season_first_kickoff",
            as_published_verified=False,
            in_season_fetch=in_season_fetch,
            fetched_at_utc=fetched_at_utc,
            provenance=PROVENANCE_NOTES,
        )
        rows.append(row)
    return rows


# ----------------------------------------------------------------------------------------------- lines
def espn_event_index(snapshot_rows: Iterable[Mapping[str, Any]]) -> dict[str, tuple[str, str]]:
    """ESPN history rows (``event_id``, ``home_id``, ``away_id``) -> {event id: (home id, away id)}."""
    out: dict[str, tuple[str, str]] = {}
    for r in snapshot_rows:
        if r.get("event_id") and r.get("home_id") and r.get("away_id"):
            out[str(r["event_id"])] = (str(r["home_id"]), str(r["away_id"]))
    return out


def event_match(game: Mapping[str, Any], events: Mapping[str, tuple[str, str]]) -> str:
    known = events.get(str(game.get("id")))
    if known is None:
        return NO_CALL_EVENT_UNSEEN
    pair = (str(game.get("homeTeamId")), str(game.get("awayTeamId")))
    if pair == known:
        return EVENT_SAME_ORIENTATION
    if pair == (known[1], known[0]):
        return EVENT_SWAPPED
    return NO_CALL_EVENT_TEAM_MISMATCH


# /lines has no completion flag. A game counts as finished only if both scores are present AND it
# kicked off at least this long before the response was fetched, so a live partial score is never
# read as final (none was seen: games in progress at fetch time had null scores).
COMPLETION_MARGIN = timedelta(hours=5)


def _neg(value: float | None) -> float | None:
    return None if value is None else -value


def parse_lines(
    games: Iterable[Mapping[str, Any]],
    events: Mapping[str, tuple[str, str]],
    as_of_utc: datetime,
) -> list[dict[str, Any]]:
    """/lines for one season -> one row per (game, provider).

    CFBD semantics: ``spread`` / ``overUnder`` are the provider's last stored numbers, with no
    timestamp; ``spreadOpen`` / ``overUnderOpen`` are its opening numbers (often null). ``spread`` is
    from the CFBD HOME side (negative = home favoured). The last stored number is read as the close
    only for a COMPLETED game (both scores present and kickoff at least ``COMPLETION_MARGIN`` before
    ``as_of_utc``, which should be the response's fetch time); for any other game
    the close fields are null, because the stored number is just today's line. Moneylines become raw
    implied probabilities plus a two-way no-vig probability; American odds are never written.

    ``spread_*_espn_home`` restate the spread from ESPN's home side (sign flipped on the neutral-site
    games where CFBD and ESPN disagree on home), only for games matched to an ESPN event. Every other
    home/away field, moneyline probabilities included, is from the CFBD home side."""
    rows: list[dict[str, Any]] = []
    for game in games:
        start = parse_utc(str(game["startDate"]))
        completed = (
            game.get("homeScore") is not None
            and game.get("awayScore") is not None
            and start + COMPLETION_MARGIN <= as_of_utc
        )
        match = event_match(game, events)
        matched = match in (EVENT_SAME_ORIENTATION, EVENT_SWAPPED)
        flip = match == EVENT_SWAPPED
        for line in game.get("lines") or []:
            spread_close = _opt_float(line.get("spread")) if completed else None
            spread_open = _opt_float(line.get("spreadOpen"))
            home_p = moneyline_implied_prob(line.get("homeMoneyline")) if completed else None
            away_p = moneyline_implied_prob(line.get("awayMoneyline")) if completed else None
            fair = home_p / (home_p + away_p) if home_p is not None and away_p is not None else None
            # About 2.6% of rows with both prices disagree on the favourite. Most are near pick'em,
            # but some are provider capture errors (a +17 home spread with a 84% home moneyline).
            # Flag them and leave the choice to research; nothing is dropped.
            conflict = (
                None
                if fair is None or spread_close is None or spread_close == 0
                else (fair > 0.5) != (spread_close < 0)
            )
            # 46 rows open and close a field goal or more on opposite sides (one 2026 game opens at
            # -19.5 and closes at +19.5): almost surely a sign error in one of the two numbers.
            open_close_conflict = (
                None
                if spread_open is None or spread_close is None
                else abs(spread_open) >= 3 and abs(spread_close) >= 3 and spread_open * spread_close < 0
            )
            rows.append(
                {
                    "season": int(game["season"]),
                    "season_type": game.get("seasonType"),
                    "week": game.get("week"),
                    "cfbd_game_id": int(game["id"]),
                    "espn_event_id": str(game["id"]) if matched else None,
                    "espn_match_status": match,
                    "start_utc": iso_utc(start),
                    "home_team": game.get("homeTeam"),
                    "home_cfbd_team_id": game.get("homeTeamId"),
                    "home_classification": game.get("homeClassification"),
                    "away_team": game.get("awayTeam"),
                    "away_cfbd_team_id": game.get("awayTeamId"),
                    "away_classification": game.get("awayClassification"),
                    "completed": completed,
                    "home_score": game.get("homeScore") if completed else None,
                    "away_score": game.get("awayScore") if completed else None,
                    "provider": str(line.get("provider")),
                    "spread_home_open": spread_open,
                    "spread_home_close": spread_close,
                    "total_open": _opt_float(line.get("overUnderOpen")),
                    "total_close": _opt_float(line.get("overUnder")) if completed else None,
                    "spread_espn_home_open": (_neg(spread_open) if flip else spread_open)
                    if matched
                    else None,
                    "spread_espn_home_close": (_neg(spread_close) if flip else spread_close)
                    if matched
                    else None,
                    "home_ml_implied_prob_close": home_p,
                    "away_ml_implied_prob_close": away_p,
                    "home_ml_novig_prob_close": fair,
                    "ml_spread_sign_conflict": conflict,
                    "open_close_sign_conflict": open_close_conflict,
                    "observed_at_utc": iso_utc(start),
                    "observed_at_rule": "kickoff (close and open carry no timestamp; open is earlier)",
                    "as_published_verified": False,
                }
            )
    return rows
