"""API-FOOTBALL (api-football.com / api-sports.io v3) soccer results client + capture.

Primary soccer results source for the daily pipeline's step1b_soccer_scores
since 2026-08-26, replacing The Odds API ``/scores`` endpoint after that
provider failed with 401s for 31+ days. The dormant Odds API fallback was
removed on 2026-09-02; ESPN event-ID settlement remains the keyless fallback.

Contract verified against public docs 2026-08-26 (api-football.com
documentation-v3 / news guides, dltHub source docs, openpublicapis listing —
no key required to read any of them):

- Base URL ``https://v3.football.api-sports.io``; auth via the
  ``x-apisports-key`` header (the same key also works through RapidAPI with
  ``X-RapidAPI-Key``/``X-RapidAPI-Host`` headers and the RapidAPI base URL).
- ``GET /fixtures?date=YYYY-MM-DD&league=<id>&season=<YYYY>&timezone=UTC``
  (every param optional individually; ``from``/``to``/``live``/``team``/
  ``round``/``status`` also exist). Response envelope: ``get``,
  ``parameters``, ``errors``, ``results``, ``paging``, ``response[]``.
- Fixture object shape: ``fixture{id, date, timestamp, status{short, long}}``,
  ``league{id, name, season}``, ``teams{home, away}{id, name, winner}``,
  ``goals{home, away}``, ``score{halftime, fulltime, extratime, penalty}``.
  ``status.short`` values include NS, 1H, HT, 2H, ET, P, BT, INT, LIVE, FT,
  AET, PEN, ABD, AWD, WO, CANC, PST, SUSP, TBD. Only ``FT``/``AET``/``PEN``
  are treated as final-with-score (see ``FINAL_STATUSES``).
- Free tier: 100 requests/day across all endpoints (season availability is
  what free plans lose, not leagues), daily counter resets roughly 00:00 UTC,
  remaining quota visible via ``x-ratelimit-requests-remaining``. Per-minute
  pacing on the free tier is unverified here — the 1s default
  ``request_delay`` is the conservative starting point; if 429s appear,
  raise it (per-league failures are fail-soft either way).

Request budget: ``days_from=3`` x ``len(API_FOOTBALL_LEAGUE_IDS)`` (20
leagues -> 60) requests per daily run, plus one ``/leagues`` call per manual
ID verification. Re-fetching the same dates is deliberate, not waste: a
fixture captured as not-started becomes final on a later fetch, and only
final games are written to the historical file — a re-fetch is how we learn
the score. Every successful per-league window also lands as a hash-stamped,
day-bucketed raw snapshot via ``provider_capture.write_provider_snapshot``
(``data/providers/api_football/soccer/raw/<day>/...``), so "provider had
nothing / provider said not-started" is provable separately from "we never
asked".

Requires ``API_FOOTBALL_KEY``. Missing key is fail-closed in the client
(``ValueError``) and fail-soft at the daily step (returns a ``no_api_key``
report dict; the daily job logs and continues).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import date as Date
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from ..config import PROJECT_ROOT
from ..domain import utc_now
from .provider_capture import ProviderEntry, write_provider_snapshot

API_FOOTBALL_KEY_ENV = "API_FOOTBALL_KEY"
BASE_URL = "https://v3.football.api-sports.io"
DEFAULT_KEY_HEADER = "x-apisports-key"

# status.short values that carry a real, played result with numeric scores.
# AET (after extra time) and PEN (after penalties) are final with the score
# in ``goals`` (for PEN, ``score.penalty`` holds the shootout). Everything
# else -- NS/1H/HT/2H/ET/LIVE, and the terminal-but-not-played ABD/AWD/WO/
# CANC/PST -- is deliberately NOT captured as a result: the terminal codes
# often carry no or a forced score, and we never fabricate one.
FINAL_STATUSES: frozenset[str] = frozenset({"FT", "AET", "PEN"})

# League key -> api-football v3 league id. Keys reuse the repo's existing
# soccer league labels (the legacy Odds API keys plus the leagues ESPN does
# not cover from the ledger's SOCCER list)
# so historical records keep stable ``league`` field values.
#
# IDs marked "doc-confirmed" were verified 2026-08-26 against api-football
# official docs examples (/teams?league=<id> guides); the rest are the API's
# stable, widely published ids but could not be re-verified from public docs
# without a key -- verify each with one /leagues?id=<id> call after the key
# is registered (see the module docstring), and fix the constant if any
# disagrees.
API_FOOTBALL_LEAGUE_IDS: dict[str, int] = {
    "PREMIER_LEAGUE": 39,  # doc-confirmed 2026-08-26 (api-football.com guide)
    "LA_LIGA": 140,  # doc-confirmed 2026-08-26 (api-football.com guide)
    "BUNDESLIGA": 78,  # doc-confirmed 2026-08-26 (api-football.com guide)
    "LIGUE_1": 61,  # doc-confirmed 2026-08-26 (api-football.com guide)
    "SERIE_A": 135,
    "EREDIVISIE": 88,
    "LIGA_PORTUGAL": 94,
    "CHAMPIONSHIP": 40,
    "K_LEAGUE_1": 292,
    "ELITESERIEN": 103,
    "CSL": 169,
    "SUPERLIGA": 119,
    "LIGA_MX": 262,
    "BRASILEIRAO": 71,
    "BRAZIL_SERIE_B": 72,
    "ARGENTINA": 128,
    "MLS": 253,
    "COPA_LIBERTADORES": 13,
    "SUDAMERICANA": 11,
    "UCL": 2,
}

# api-football numbers a season by its STARTING year, so a European season
# that runs Aug-May straddles two calendar years: a date in Jan-Jul belongs
# to the previous year's season. Calendar-year leagues (Brazil, Argentina,
# MLS, K League 1, Eliteserien, CSL, Liga MX, Libertadores, Sudamericana)
# always use the current year.
_CROSS_YEAR_SEASON_LEAGUES: frozenset[str] = frozenset(
    {
        "PREMIER_LEAGUE",
        "LA_LIGA",
        "SERIE_A",
        "BUNDESLIGA",
        "LIGUE_1",
        "EREDIVISIE",
        "LIGA_PORTUGAL",
        "CHAMPIONSHIP",
        "SUPERLIGA",  # Denmark, Jul-May
        "UCL",
    }
)


def _season_for(league_key: str, on_date: Date) -> int:
    """Season parameter for a league as of ``on_date`` (the capture date)."""
    if league_key in _CROSS_YEAR_SEASON_LEAGUES and on_date.month < 7:
        return on_date.year - 1
    return on_date.year


def _fixture_date_window(days_from: int, *, today: Date) -> list[Date]:
    """The ``days_from`` most recent calendar dates ending yesterday (UTC).

    Today's games are picked up by tomorrow's run; every run re-fetches the
    whole window so fixtures that were in progress when first seen get their
    final score when one exists.
    """
    return [today - timedelta(days=offset) for offset in range(1, days_from + 1)]


class APIFootballClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = BASE_URL,
        key_header: str = DEFAULT_KEY_HEADER,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("API_FOOTBALL_KEY is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.key_header = key_header
        self.client = client or httpx.Client(timeout=30)

    def _safe_get(self, path: str, params: dict[str, Any]) -> httpx.Response:
        """GET + raise_for_status, with the API key redacted from any error message."""
        try:
            response = self.client.get(
                self.base_url + path,
                params=params,
                headers={self.key_header: self.api_key},
            )
            response.raise_for_status()
            return response
        except Exception as exc:  # noqa: BLE001 -- transport and HTTP errors may embed the URL or header repr; redact and re-raise as one catchable base class
            msg = str(exc).replace(self.api_key, "[REDACTED]")
            raise httpx.HTTPError(msg) from None

    def fixtures(
        self,
        *,
        date: str,
        league_id: int,
        season: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fixtures for one league on one calendar date (UTC-bucketed)."""
        params: dict[str, Any] = {"date": date, "league": league_id, "timezone": "UTC"}
        if season is not None:
            params["season"] = season
        response = self._safe_get("/fixtures", params)
        return response.json().get("response", [])

    def leagues(self, *, league_id: int | None = None) -> list[dict[str, Any]]:
        """League discovery/metadata -- for verifying API_FOOTBALL_LEAGUE_IDS."""
        params: dict[str, Any] = {}
        if league_id is not None:
            params["id"] = league_id
        response = self._safe_get("/leagues", params)
        return response.json().get("response", [])


def _redact_api_key(text: str, api_key: str) -> str:
    """Strip a leaked API key out of exception text before it is stored or logged."""
    if not api_key:
        return text
    return text.replace(api_key, "***REDACTED***")


def _load_existing_ids(historical_path: Path) -> set[str]:
    """Event ids already captured, so a re-fetched game is never appended twice."""
    existing_ids: set[str] = set()
    if not historical_path.exists():
        return existing_ids
    with historical_path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                try:
                    existing_ids.add(str(json.loads(line).get("event_id", "")))
                except json.JSONDecodeError:
                    continue
    return existing_ids


def _game_record(
    league_key: str,
    fixture: dict[str, Any],
    observed_at_utc: datetime,
    season: int,
) -> dict[str, Any] | None:
    """Normalize one final fixture into a historical-JSONL line, or None if it
    is not a final game with numeric scores (never fabricate a result).

    ``goals`` is the API's canonical final score: for AET it includes extra
    time, for PEN it is the post-ET (level) score with the shootout carried
    in ``penalty_home``/``penalty_away``.
    """
    fixture_meta = fixture.get("fixture") or {}
    status_short = (fixture_meta.get("status") or {}).get("short")
    if status_short not in FINAL_STATUSES:
        return None
    goals = fixture.get("goals") or {}
    home_goal, away_goal = goals.get("home"), goals.get("away")
    if home_goal is None or away_goal is None or not str(home_goal).isdigit() or not str(away_goal).isdigit():
        return None
    teams = fixture.get("teams") or {}
    home_team = (teams.get("home") or {}).get("name", "")
    away_team = (teams.get("away") or {}).get("name", "")
    if not home_team or not away_team:
        return None

    start = fixture_meta.get("date", "")
    # Deterministic digest (the odds path's bug class: hash() is randomized
    # per process and minted a new id every run, defeating dedup).
    digest = hashlib.sha1(f"{home_team}|{away_team}".encode()).hexdigest()[:8]
    record: dict[str, Any] = {
        "event_id": f"apifootball:{league_key}:{start[:10]}:{digest}",
        "event_start_utc": start,
        "league": league_key,
        "home_team": home_team,
        "away_team": away_team,
        "home_score": int(home_goal),
        "away_score": int(away_goal),
        "status": status_short,
        "season_year": season,
        "source": "api_football",
        "observed_at_utc": observed_at_utc.isoformat(),
    }
    score = fixture.get("score") or {}
    penalty = score.get("penalty") or {}
    if status_short == "PEN" and penalty.get("home") is not None and penalty.get("away") is not None:
        record["penalty_home"] = int(penalty["home"])
        record["penalty_away"] = int(penalty["away"])
    return record


def collect_soccer_scores(
    api_key: str | None = None,
    data_root: str | Path | None = None,
    days_from: int = 3,
    *,
    leagues: dict[str, int] | None = None,
    client: APIFootballClient | None = None,
    request_delay: float = 1.0,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Fetch recent soccer results from API-FOOTBALL and capture them.

    Primary entry point for the daily pipeline's step1b_soccer_scores.
    Report shape mirrors the dormant ``odds_soccer_scores.collect_soccer_scores``:
    per-league ``{"status": "ok", "matches_returned", "new_games",
    "raw_path", "snapshot_path"}`` (or ``{"status": "error", "error"}`` /
    ``{"status": "no_api_key", ...}``) plus ``total_new_games`` and
    ``historical_path``.

    Missing key returns ``{"status": "no_api_key", ...}`` instead of raising
    so the daily job logs and continues (same fail-soft contract the The
    Odds API 401 era had); the client itself fails closed.
    """
    if data_root is None:
        data_root = PROJECT_ROOT / "data"
    observed = observed_at or utc_now()
    if api_key is None:
        api_key = os.environ.get(API_FOOTBALL_KEY_ENV, "")
    if not api_key:
        return {"status": "no_api_key", "error": f"{API_FOOTBALL_KEY_ENV} not set"}
    if client is None:
        client = APIFootballClient(api_key)

    league_map = leagues if leagues is not None else dict(API_FOOTBALL_LEAGUE_IDS)
    dates = _fixture_date_window(days_from, today=observed.date())

    historical_path = Path(data_root) / "historical" / "soccer_games_all.jsonl"
    historical_path.parent.mkdir(parents=True, exist_ok=True)
    existing_ids = _load_existing_ids(historical_path)

    results: dict[str, Any] = {}
    total_new = 0
    # Sequential on purpose: the free tier's per-minute cap (unverified, may
    # be as low as ~10/min) makes the old 12-way ThreadPoolExecutor unsafe.
    for league_key, league_id in sorted(league_map.items()):
        season = _season_for(league_key, observed.date())
        fixtures: list[dict[str, Any]] = []
        league_error: str | None = None
        for day in dates:
            try:
                fixtures.extend(client.fixtures(date=day.isoformat(), league_id=league_id, season=season))
            except Exception as exc:  # noqa: BLE001 -- per-league fail-soft: the caller needs the redacted message as data, not a raised error (same contract as the dormant odds path)
                league_error = _redact_api_key(str(exc), api_key)[:200]
                break
            if request_delay:
                time.sleep(request_delay)
        if league_error is not None:
            results[league_key] = {"status": "error", "error": league_error}
            continue

        entries: list[ProviderEntry] = []
        new_games = 0
        with historical_path.open("a", encoding="utf-8") as handle:
            for fixture in fixtures:
                fixture_meta = fixture.get("fixture") or {}
                entries.append(
                    ProviderEntry(
                        source="api_football",
                        source_entity_id=str(fixture_meta.get("id", "")),
                        effective_at_utc=str(fixture_meta.get("date", "")),
                        observed_at_utc=observed.isoformat(),
                        source_version=str(season),
                        payload=fixture,
                    )
                )
                record = _game_record(league_key, fixture, observed, season)
                if record is None or record["event_id"] in existing_ids:
                    continue
                handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
                existing_ids.add(record["event_id"])
                new_games += 1

        _, raw_path, snapshot_path = write_provider_snapshot(
            data_root,
            source="api_football",
            sport="soccer",
            entries=entries,
            observed_at=observed,
            source_url=(
                f"{BASE_URL}/fixtures?league={league_id}&season={season}&date={dates[0]}..{dates[-1]}"
            ),
        )
        results[league_key] = {
            "status": "ok",
            "matches_returned": len(fixtures),
            "new_games": new_games,
            "raw_path": str(raw_path),
            "snapshot_path": str(snapshot_path),
        }
        total_new += new_games

    results["total_new_games"] = total_new
    results["historical_path"] = str(historical_path)
    results["api"] = "api_football"
    results["captured_at_utc"] = observed.isoformat()
    return results
