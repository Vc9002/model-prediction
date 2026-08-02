"""Research-only KBO/NPB backfill and tie-aware Elo baselines.

Polymarket US settles a KBO/NPB moneyline to 0.50 when the game ends in a
tie.  The model therefore forecasts a decisive-result probability and an
independently estimated tie probability, then values a side as::

    P(side wins) + 0.5 * P(tie)

The module never authorizes execution.  Forecasts are zero-unit research
observations and require exact team identity plus a current executable ask.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from .domain import eastern_today
from .features.elo_ratings import expected_win_probability
from .research_io import atomic_write as _atomic_write
from .research_io import backup_before_overwrite as _backup_before_overwrite
from .research_io import canonical_json as _canonical_json
from .research_io import identity_key as _identity_key
from .research_io import sha256_file as _sha256
from .research_io import utc_now as _utc_now

KBO_SCHEDULE_PAGE = "https://www.koreabaseball.com/Schedule/Schedule.aspx"
KBO_RESULTS_ENDPOINT = "https://www.koreabaseball.com/ws/Schedule.asmx/GetScheduleList"
NPB_CALENDAR_TEMPLATE = "https://npb.jp/bis/eng/{year}/calendar/index_{month:02d}.html"

KBO_TEAMS = {
    "KIA": {"name": "Kia Tigers", "source_names": ("KIA",)},
    "DOOSAN": {"name": "Doosan Bears", "source_names": ("두산",)},
    "HANWHA": {"name": "Hanwha Eagles", "source_names": ("한화",)},
    "KIWOOM": {"name": "Kiwoom Heroes", "source_names": ("키움",)},
    "KT": {"name": "KT Wiz", "source_names": ("KT",)},
    "LG": {"name": "LG Twins", "source_names": ("LG",)},
    "LOTTE": {"name": "Lotte Giants", "source_names": ("롯데",)},
    "NC": {"name": "NC Dinos", "source_names": ("NC",)},
    "SAMSUNG": {"name": "Samsung Lions", "source_names": ("삼성",)},
    "SSG": {"name": "SSG Landers", "source_names": ("SSG",)},
}

NPB_TEAMS = {
    "B": {"name": "Orix Buffaloes", "aliases": ("ORIX Buffaloes",)},
    "C": {"name": "Hiroshima Carp", "aliases": ("Hiroshima Toyo Carp",)},
    "D": {"name": "Chunichi Dragons", "aliases": ()},
    "DB": {"name": "Yokohama BayStars", "aliases": ("Yokohama DeNA BayStars",)},
    "E": {"name": "Tohoku Rakuten Golden Eagles", "aliases": ()},
    "F": {"name": "Nippon Ham Fighters", "aliases": ("Hokkaido Nippon-Ham Fighters",)},
    "G": {"name": "Yomiuri Giants", "aliases": ()},
    "H": {"name": "Fukuoka SoftBank Hawks", "aliases": ()},
    "L": {"name": "Saitama Seibu Lions", "aliases": ()},
    "M": {"name": "Chiba Lotte Marines", "aliases": ()},
    "S": {"name": "Tokyo Yakult Swallows", "aliases": ()},
    "T": {"name": "Hanshin Tigers", "aliases": ()},
}

LEAGUE_SPECS: dict[str, dict[str, Any]] = {
    "kbo": {
        "name": "Korea Baseball Organization",
        "polymarket_league": "KBO",
        "timezone": "Asia/Seoul",
        "teams": KBO_TEAMS,
        "minimum_year": 2015,
    },
    "npb": {
        "name": "Nippon Professional Baseball",
        "polymarket_league": "NPB",
        "timezone": "Asia/Tokyo",
        "teams": NPB_TEAMS,
        "minimum_year": 2015,
    },
}

K_CANDIDATES = (8.0, 12.0, 16.0, 20.0, 24.0, 32.0, 40.0)
HOME_ADVANTAGE_CANDIDATES = (0.0, 20.0, 35.0, 50.0, 65.0, 80.0)
MARGIN_WEIGHT_CANDIDATES = (0.0, 0.5, 1.0)
DECAY_CANDIDATES = (0.0, 0.001, 0.002)
TIE_MODEL_CANDIDATES = ("flat", "elo_gap")
_TAG_RE = re.compile(r"<[^>]+>")
_KBO_DATE_RE = re.compile(r"(?P<month>\d{2})\.(?P<day>\d{2})")
_KBO_GAME_ID_RE = re.compile(r"gameId=(?P<id>[A-Za-z0-9]+)")
_NPB_GAME_RE = re.compile(
    r'href="(?P<href>/bis/eng/(?P<year>\d{4})/games/s(?P<game_id>\d+)\.html)"[^>]*>'
    r"\s*(?P<away>[A-Z]+)\s+(?P<away_score>\d+|\*)\s+-\s+"
    r"(?P<home_score>\d+|\*)\s+(?P<home>[A-Z]+)\s*</a>",
    re.IGNORECASE,
)


def _plain_text(value: str) -> str:
    return " ".join(html.unescape(_TAG_RE.sub(" ", value)).split())


def _team_lookup(league: str) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for team_id, team in LEAGUE_SPECS[league]["teams"].items():
        lookup[team_id] = team_id
        lookup[str(team["name"])] = team_id
        for alias in team.get("aliases", ()):
            lookup[str(alias)] = team_id
        for source_name in team.get("source_names", ()):
            lookup[str(source_name)] = team_id
    return lookup


def parse_kbo_rows(payload: dict[str, Any], year: int) -> list[dict[str, Any]]:
    """Normalize one official KBO regular-season monthly response."""
    lookup = _team_lookup("kbo")
    current_date: date | None = None
    output: list[dict[str, Any]] = []
    for entry in payload.get("rows", []):
        cells = entry.get("row") or []
        if not cells:
            continue
        date_cell = next((cell for cell in cells if cell.get("Class") == "day"), None)
        if date_cell:
            match = _KBO_DATE_RE.search(_plain_text(str(date_cell.get("Text") or "")))
            if match:
                current_date = date(year, int(match.group("month")), int(match.group("day")))
        play_cell = next((cell for cell in cells if cell.get("Class") == "play"), None)
        if current_date is None or not play_cell:
            continue
        spans = re.findall(r"<span(?:\s+class=\"[^\"]*\")?>(.*?)</span>", str(play_cell["Text"]))
        values = [_plain_text(value) for value in spans]
        if "vs" not in values:
            continue
        vs_index = values.index("vs")
        if vs_index < 2 or vs_index + 2 >= len(values):
            continue
        away_name, home_name = values[0], values[-1]
        try:
            away_score, home_score = int(values[vs_index - 1]), int(values[vs_index + 1])
            away_id, home_id = lookup[away_name], lookup[home_name]
        except (KeyError, ValueError):
            continue
        relay = next((str(cell.get("Text") or "") for cell in cells if cell.get("Class") == "relay"), "")
        game_match = _KBO_GAME_ID_RE.search(relay)
        game_id = game_match.group("id") if game_match else f"{current_date:%Y%m%d}:{away_id}:{home_id}"
        time_cell = next((cell for cell in cells if cell.get("Class") == "time"), None)
        local_time = _plain_text(str(time_cell.get("Text") or "")) if time_cell else None
        output.append(
            {
                "game_id": f"kbo:{game_id}",
                "league": "kbo",
                "season": year,
                "game_date": current_date.isoformat(),
                "scheduled_local_time": local_time,
                "away_team_id": away_id,
                "home_team_id": home_id,
                "away_team_name": KBO_TEAMS[away_id]["name"],
                "home_team_name": KBO_TEAMS[home_id]["name"],
                "away_score": away_score,
                "home_score": home_score,
                "tie": away_score == home_score,
                "source_url": f"{KBO_SCHEDULE_PAGE}?gameDate={current_date:%Y%m%d}&gameId={game_id}",
            }
        )
    return output


def parse_npb_calendar(page: str) -> list[dict[str, Any]]:
    """Normalize final regular-season rows from one official NPB calendar."""
    output: list[dict[str, Any]] = []
    for match in _NPB_GAME_RE.finditer(page):
        if "*" in {match.group("away_score"), match.group("home_score")}:
            continue
        # NPB calendar shorthand is HOME score - score VISITOR.  Individual
        # game line scores confirm the second calendar team bats first.
        home_id, away_id = match.group("away").upper(), match.group("home").upper()
        if away_id not in NPB_TEAMS or home_id not in NPB_TEAMS:
            continue
        game_id = match.group("game_id")
        game_date = date.fromisoformat(f"{game_id[:4]}-{game_id[4:6]}-{game_id[6:8]}")
        home_score, away_score = int(match.group("away_score")), int(match.group("home_score"))
        output.append(
            {
                "game_id": f"npb:{game_id}",
                "league": "npb",
                "season": int(match.group("year")),
                "game_date": game_date.isoformat(),
                "scheduled_local_time": None,
                "away_team_id": away_id,
                "home_team_id": home_id,
                "away_team_name": NPB_TEAMS[away_id]["name"],
                "home_team_name": NPB_TEAMS[home_id]["name"],
                "away_score": away_score,
                "home_score": home_score,
                "tie": away_score == home_score,
                "source_url": f"https://npb.jp{match.group('href')}",
            }
        )
    return output


class OfficialInternationalBaseballClient:
    """Read-only client for official KBO and NPB public schedule pages."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(
            timeout=45,
            follow_redirects=True,
            headers={"User-Agent": "model-prediction-research/1.0"},
        )

    def kbo_year(self, year: int) -> tuple[list[dict[str, Any]], int]:
        self.client.get(KBO_SCHEDULE_PAGE).raise_for_status()
        rows: list[dict[str, Any]] = []
        requests = 1
        for month in range(3, 11):
            response = self.client.post(
                KBO_RESULTS_ENDPOINT,
                data={
                    "leId": "1",
                    "srIdList": "0,9,6",
                    "seasonId": str(year),
                    "gameMonth": f"{month:02d}",
                    "teamId": "",
                },
                headers={"Referer": KBO_SCHEDULE_PAGE, "X-Requested-With": "XMLHttpRequest"},
            )
            response.raise_for_status()
            rows.extend(parse_kbo_rows(response.json(), year))
            requests += 1
        return rows, requests

    def npb_year(self, year: int) -> tuple[list[dict[str, Any]], int]:
        rows: list[dict[str, Any]] = []
        # The official October calendar mixes late regular-season games with
        # Climax/Japan Series games without a machine-readable competition
        # field.  Exclude it rather than contaminate the regular-season model.
        for month in range(4, 10):
            response = self.client.get(NPB_CALENDAR_TEMPLATE.format(year=year, month=month))
            response.raise_for_status()
            rows.extend(parse_npb_calendar(response.text))
        return rows, 6


def backfill_international_baseball(
    data_root: str | Path,
    league: str,
    from_date: str,
    to_date: str | None = None,
    client: OfficialInternationalBaseballClient | None = None,
) -> dict[str, Any]:
    """Backfill official regular-season results with hashes and provenance."""
    league = league.lower()
    if league not in LEAGUE_SPECS:
        raise ValueError(f"unsupported international baseball league: {league}")
    start = date.fromisoformat(from_date)
    end = date.fromisoformat(to_date) if to_date else eastern_today()
    minimum = date(int(LEAGUE_SPECS[league]["minimum_year"]), 1, 1)
    start = max(start, minimum)
    if start > end:
        raise ValueError("from_date must not be after to_date")
    source = client or OfficialInternationalBaseballClient()
    games: list[dict[str, Any]] = []
    request_count = 0
    for year in range(start.year, end.year + 1):
        yearly, requests = source.kbo_year(year) if league == "kbo" else source.npb_year(year)
        games.extend(row for row in yearly if start <= date.fromisoformat(row["game_date"]) <= end)
        request_count += requests
    games = sorted(
        {row["game_id"]: row for row in games}.values(),
        key=lambda row: (row["game_date"], row["game_id"]),
    )
    directory = Path(data_root) / "international_baseball" / league
    games_path = directory / "games.jsonl"
    teams_path = directory / "teams.json"
    manifest_path = directory / "manifest.json"
    teams = {
        team_id: {
            "team_id": team_id,
            "name": team["name"],
            "aliases": sorted(
                set(team.get("aliases", ())) | set(team.get("source_names", ())) | {team_id}
            ),
        }
        for team_id, team in LEAGUE_SPECS[league]["teams"].items()
    }
    _atomic_write(games_path, "".join(_canonical_json(row) + "\n" for row in games))
    _atomic_write(teams_path, json.dumps(teams, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    manifest = {
        "schema_version": "international-baseball-games-v1",
        "league": league,
        "league_name": LEAGUE_SPECS[league]["name"],
        "source": "official league regular-season schedule/results",
        "source_urls": [KBO_SCHEDULE_PAGE] if league == "kbo" else ["https://npb.jp/bis/eng/"],
        "requires_api_key": False,
        "requires_signup": False,
        "extracted_at_utc": _utc_now(),
        "requested_window": {"from": from_date, "to": to_date or end.isoformat()},
        "effective_window": {"from": start.isoformat(), "to": end.isoformat()},
        "game_count": len(games),
        "tie_count": sum(bool(row["tie"]) for row in games),
        "request_count": request_count,
        "games_sha256": _sha256(games_path),
        "teams_sha256": _sha256(teams_path),
        "limitations": [
            "Official pages do not provide a versioned bulk-data contract; cache and hashes preserve each extraction.",
            "The baseline excludes postseason and exhibition games.",
            *(
                [
                    "NPB v1 ends on September 30 because the official October calendar mixes competitions.",
                    "NPB calendar rows omit scheduled first-pitch time; same-day games are frozen as one rating batch.",
                ]
                if league == "npb"
                else []
            ),
        ],
    }
    _atomic_write(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return {
        **manifest,
        "paths": {"games": str(games_path), "teams": str(teams_path), "manifest": str(manifest_path)},
    }


def refresh_recent_international_baseball_matches(
    data_root: str | Path,
    league: str,
    client: OfficialInternationalBaseballClient | None = None,
) -> dict[str, Any]:
    """Merge the current season's newly-finished games into the existing
    history, instead of `backfill_international_baseball`'s full-window
    overwrite.

    Found 2026-07-31: unlike esports (`refresh_recent_matches`, wired into
    `daily`), nothing kept KBO/NPB Elo ratings current -- ratings only
    updated when someone manually ran `backfill_international_baseball`
    then re-validated. Confirmed live: kbo-tie-aware-elo-v1.json was 6 days
    stale, npb-tie-aware-elo-v1.json 14 days stale, and nothing in the
    dashboard's status/alert logic surfaces artifact staleness at all (only
    a much looser 30-day raw-manifest-age check). `backfill_...` replaces
    games.jsonl entirely with whatever `from_date..to_date` returns --
    calling it with a short recent window on a schedule would silently
    delete every prior season's history, and calling it with the league's
    full multi-year minimum_year to avoid that would re-fetch the entire
    catalog every cycle. This instead re-fetches only the current calendar
    year (a single, cheap `kbo_year`/`npb_year` call -- a season is a few
    hundred games, not the multi-year full history) and merges it into the
    existing file, keyed by the source's stable `game_id`, so ratings can be
    refreshed daily without either problem.

    Self-healing fallback: if `games.jsonl` doesn't exist yet -- no backfill
    has ever succeeded for this league (first run, or every prior daily
    attempt failed before ever writing the file) -- a current-year-only
    refresh would permanently strand every earlier season, since there's no
    existing history to merge into. Falls back to a full
    `backfill_international_baseball` (the league's real `minimum_year`
    through today) in that case instead; every day this doesn't happen it's
    a no-op fast path (an existing file means some earlier run already
    completed the real backfill).
    """
    league = league.lower()
    if league not in LEAGUE_SPECS:
        raise ValueError(f"unsupported international baseball league: {league}")
    games_path = Path(data_root) / "international_baseball" / league / "games.jsonl"

    if not games_path.exists():
        return backfill_international_baseball(
            data_root,
            league,
            from_date=date(int(LEAGUE_SPECS[league]["minimum_year"]), 1, 1).isoformat(),
            client=client,
        )

    return _merge_year_into_international_baseball_history(
        data_root, league, eastern_today().year, client=client
    )


def _merge_year_into_international_baseball_history(
    data_root: str | Path,
    league: str,
    year: int,
    client: OfficialInternationalBaseballClient | None = None,
) -> dict[str, Any]:
    """Re-fetch a single season and merge it into existing history by
    `game_id`, leaving every other season's rows untouched.

    Shared by `refresh_recent_international_baseball_matches` (always
    `year=today.year`) and `find_international_baseball_result`'s cache-miss
    fallback (`year=` the settled game's own year). Never used for a league
    with no existing `games.jsonl` -- callers fall back to a full
    `backfill_international_baseball` in that case, since there's no prior
    history a partial-year overwrite could destroy.
    """
    directory = Path(data_root) / "international_baseball" / league
    games_path = directory / "games.jsonl"
    teams_path = directory / "teams.json"
    manifest_path = directory / "manifest.json"

    existing_games: dict[str, dict[str, Any]] = {}
    with games_path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                existing_games[str(row["game_id"])] = row

    source = client or OfficialInternationalBaseballClient()
    today = eastern_today()
    fresh, request_count = source.kbo_year(year) if league == "kbo" else source.npb_year(year)
    minimum = date(int(LEAGUE_SPECS[league]["minimum_year"]), 1, 1)
    for row in fresh:
        if minimum <= date.fromisoformat(row["game_date"]) <= today:
            existing_games[str(row["game_id"])] = row

    games = sorted(existing_games.values(), key=lambda row: (row["game_date"], row["game_id"]))
    teams = {
        team_id: {
            "team_id": team_id,
            "name": team["name"],
            "aliases": sorted(
                set(team.get("aliases", ())) | set(team.get("source_names", ())) | {team_id}
            ),
        }
        for team_id, team in LEAGUE_SPECS[league]["teams"].items()
    }
    _atomic_write(games_path, "".join(_canonical_json(row) + "\n" for row in games))
    _atomic_write(teams_path, json.dumps(teams, indent=2, sort_keys=True, ensure_ascii=False) + "\n")

    earliest = min((row["game_date"] for row in games), default=today.isoformat())
    manifest = {
        "schema_version": "international-baseball-games-v1",
        "league": league,
        "league_name": LEAGUE_SPECS[league]["name"],
        "source": "official league regular-season schedule/results",
        "source_urls": [KBO_SCHEDULE_PAGE] if league == "kbo" else ["https://npb.jp/bis/eng/"],
        "requires_api_key": False,
        "requires_signup": False,
        "extracted_at_utc": _utc_now(),
        "requested_window": {"from": earliest, "to": today.isoformat()},
        "effective_window": {"from": earliest, "to": today.isoformat()},
        "game_count": len(games),
        "tie_count": sum(bool(row["tie"]) for row in games),
        "request_count": request_count,
        "games_sha256": _sha256(games_path),
        "teams_sha256": _sha256(teams_path),
        "limitations": [
            "Official pages do not provide a versioned bulk-data contract; cache and hashes preserve each extraction.",
            "The baseline excludes postseason and exhibition games.",
            f"Incremental refresh: only {year} was re-fetched this run; other years are carried forward unchanged from the last full backfill.",
        ],
    }
    _atomic_write(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return {
        **manifest,
        "paths": {"games": str(games_path), "teams": str(teams_path), "manifest": str(manifest_path)},
    }


def find_international_baseball_result(
    data_root: str | Path,
    league: str,
    game_date: str,
    home_team: str,
    away_team: str,
    client: "OfficialInternationalBaseballClient | None" = None,
) -> tuple[int, int] | None:
    """Find (away_score, home_score) for a completed KBO/NPB game.

    Ledger rows for these leagues carry Polymarket team-name strings and
    Polymarket event ids (see forecast_international_baseball_slate), not
    the official schedule's own game_id scheme -- so matching has to be by
    game_date + team alias, the same way ``_team_alias_index`` already
    matches teams when building the forecast slate. Checks the cached
    games.jsonl first; if this date isn't covered yet (e.g. today's game
    just finished and the cache predates it), refreshes this league's
    current season from the official source once before giving up.
    Returns None if the game can't be found (not yet played, or a genuine
    lookup miss) -- callers should leave the pick open rather than guess.

    Real bug fixed 2026-08-02: this fallback used to call
    `backfill_international_baseball(..., f"{year}-01-01")` directly, which
    *overwrites* games.jsonl with only that from_date..today window --
    silently deleting every earlier season on the very first settlement
    cache-miss. This destroyed NPB's real history (3,936 games back to
    2022-03-25, collapsed to 566 games from 2026-01-01 on) the first time a
    just-finished NPB game wasn't yet in the cache. Now merges via the same
    by-`game_id` logic `refresh_recent_international_baseball_matches` uses,
    which can only add/update rows for the target year, never drop others.
    """
    league = league.lower()
    directory = Path(data_root) / "international_baseball" / league
    games_path = directory / "games.jsonl"
    teams_path = directory / "teams.json"

    def _match() -> tuple[int, int] | None:
        if not games_path.exists() or not teams_path.exists():
            return None
        aliases = _team_alias_index(json.loads(teams_path.read_text(encoding="utf-8")))
        home_ids = aliases.get(_identity_key(home_team), set())
        away_ids = aliases.get(_identity_key(away_team), set())
        if not home_ids or not away_ids:
            return None
        for row in _load_games(games_path):
            if row["game_date"] != game_date:
                continue
            if row["home_team_id"] in home_ids and row["away_team_id"] in away_ids:
                return int(row["away_score"]), int(row["home_score"])
        return None

    result = _match()
    if result is not None:
        return result

    year = date.fromisoformat(game_date).year
    source = client or OfficialInternationalBaseballClient()
    with suppress(Exception):
        if games_path.exists():
            _merge_year_into_international_baseball_history(data_root, league, year, client=source)
        else:
            backfill_international_baseball(
                data_root,
                league,
                from_date=date(int(LEAGUE_SPECS[league]["minimum_year"]), 1, 1).isoformat(),
                client=source,
            )
    return _match()


@dataclass
class HomeElo:
    k: float
    home_advantage: float
    ratings: dict[str, float]
    margin_weight: float = 0.0
    decay_rate: float = 0.0
    _last_active: dict[str, str] | None = None

    def decisive_home_probability(self, away_id: str, home_id: str) -> float:
        away = self.ratings.get(away_id, 1500.0)
        home = self.ratings.get(home_id, 1500.0)
        return expected_win_probability(home, away, self.home_advantage)

    def _apply_decay(self, game_date: str) -> None:
        if self.decay_rate <= 0 or self._last_active is None:
            return
        from datetime import date
        today = date.fromisoformat(game_date)
        for team_id in list(self.ratings):
            last = self._last_active.get(team_id)
            if last is None:
                continue
            days = (today - date.fromisoformat(last)).days
            if days > 0:
                pull = (self.ratings[team_id] - 1500.0) * (1.0 - (1.0 - self.decay_rate) ** days)
                self.ratings[team_id] -= pull

    def _mark_active(self, team_ids: list[str], game_date: str) -> None:
        if self._last_active is None:
            self._last_active = {}
        for tid in team_ids:
            self._last_active[tid] = game_date

    def update(self, row: dict[str, Any], probability: float | None = None) -> None:
        game_date = str(row["game_date"])
        self._apply_decay(game_date)
        probability = (
            probability
            if probability is not None
            else self.decisive_home_probability(row["away_team_id"], row["home_team_id"])
        )
        if row["home_score"] == row["away_score"]:
            outcome = 0.5
        else:
            outcome = 1.0 if row["home_score"] > row["away_score"] else 0.0
        # Margin-weighted K: blowouts teach the model more
        margin = abs(int(row["home_score"]) - int(row["away_score"]))
        k_eff = self.k
        if self.margin_weight > 0 and margin > 1:
            import math
            k_eff = self.k * (1.0 + self.margin_weight * math.log(margin))
        delta = k_eff * (outcome - probability)
        self.ratings[row["home_team_id"]] = self.ratings.get(row["home_team_id"], 1500.0) + delta
        self.ratings[row["away_team_id"]] = self.ratings.get(row["away_team_id"], 1500.0) - delta
        self._mark_active([row["home_team_id"], row["away_team_id"]], game_date)


def tie_aware_fair_values(decisive_home_probability: float, tie_probability: float) -> tuple[float, float]:
    """Return away/home expected $1 contract settlements; values sum to one."""
    home = (1.0 - tie_probability) * decisive_home_probability + 0.5 * tie_probability
    return 1.0 - home, home


def _game_tie_probability(
    away_id: str,
    home_id: str,
    book: HomeElo,
    tie_method: str,
    base_tie_rate: float,
) -> float:
    """Game-specific tie probability.

    flat:      league-wide historical average (v1 default).
    elo_gap:   evenly-matched teams tie more often; model fits a logistic
               curve through the observed tie rate such that identical-rating
               games get base_tie_rate * 1.5 and 200-point gaps get half.
    """
    if tie_method == "flat" or base_tie_rate <= 0:
        return base_tie_rate
    if tie_method == "elo_gap":
        away_elo = book.ratings.get(away_id, 1500.0)
        home_elo = book.ratings.get(home_id, 1500.0) + book.home_advantage
        gap = abs(home_elo - away_elo)
        # Peak at even matchups, decay to near-zero at large gaps
        scale = max(0.0, 1.0 - gap / 200.0)
        return min(base_tie_rate * (0.5 + 1.0 * scale), 0.10)
    return base_tie_rate


def _load_games(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"missing international baseball backfill: {path}")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return sorted(rows, key=lambda row: (row["game_date"], row["game_id"]))


def _training_prefix_sha256(rows: list[dict[str, Any]], through_date: str) -> str:
    """Hash exactly the rows a rating artifact trained on, keyed by date.

    Unlike hashing the whole games.jsonl file, this stays valid forever as
    the file grows with new completed games -- `refresh_recent_
    international_baseball_matches` and the settlement fallback can only
    ever add/update rows for the *current* year (see
    `_merge_year_into_international_baseball_history`), so a training
    artifact's own prefix (every row up to `trained_through_date`) never
    changes on a healthy history. If it ever does mismatch, the training
    window itself was altered after the fact -- exactly what the 2026-08-02
    NPB destructive-overwrite bug did.
    """
    prefix = sorted(
        (row for row in rows if str(row["game_date"]) <= through_date),
        key=lambda row: (row["game_date"], row["game_id"]),
    )
    return hashlib.sha256(
        "".join(_canonical_json(row) + "\n" for row in prefix).encode("utf-8")
    ).hexdigest()


def _batched_predictions(
    book: HomeElo,
    rows: Iterable[dict[str, Any]],
    base_tie_probability: float,
    *,
    tie_method: str = "flat",
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row["game_date"]), []).append(row)
    output: list[dict[str, Any]] = []
    for game_date in sorted(groups):
        pending: list[tuple[dict[str, Any], float, float]] = []
        for row in groups[game_date]:
            decisive_home = book.decisive_home_probability(row["away_team_id"], row["home_team_id"])
            game_tie_p = _game_tie_probability(
                row["away_team_id"], row["home_team_id"], book, tie_method, base_tie_probability,
            )
            _, fair_home = tie_aware_fair_values(decisive_home, game_tie_p)
            outcome = 0.5 if row["tie"] else float(row["home_score"] > row["away_score"])
            output.append({"probability": fair_home, "outcome": outcome, "tie": bool(row["tie"])})
            pending.append((row, decisive_home, game_tie_p))
        for row, probability, _tie_p in pending:
            book.update(row, probability)
    return output


def _tie_rate(rows: Sequence[dict[str, Any]]) -> float:
    if not rows:
        return 0.0
    return sum(bool(row["tie"]) for row in rows) / len(rows)


def _metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "observations": 0, "brier_settlement": None, "mae_settlement": None, "accuracy_decisive": None,
            "ties": 0, "calls": 0, "hits": 0, "units_at_minus_110": 0.0,
        }
    brier = sum((float(row["probability"]) - float(row["outcome"])) ** 2 for row in rows) / len(rows)
    mae = sum(abs(float(row["probability"]) - float(row["outcome"])) for row in rows) / len(rows)
    decisive = [row for row in rows if not row["tie"]]
    hits = sum((float(row["probability"]) >= 0.5) == bool(row["outcome"]) for row in decisive)
    accuracy = hits / len(decisive) if decisive else None
    # Diagnostic flat one-unit -110 P&L, same convention as esports.py/
    # validation.py. A tie settles the contract at $0.50 regardless of side
    # picked -- treated as a push (0 P&L), matching how accuracy_decisive
    # already excludes ties from the hit-rate rather than scoring them as a
    # loss. Not real or Polymarket-executable profitability; see
    # model_improvements.md section 2's economic gate.
    misses = len(decisive) - hits
    units_at_minus_110 = round(hits * (10 / 11) - misses, 6)
    return {
        "observations": len(rows),
        "brier_settlement": round(brier, 6),
        "mae_settlement": round(mae, 6),
        "accuracy_decisive": round(accuracy, 6) if accuracy is not None else None,
        "ties": len(rows) - len(decisive),
        "calls": len(decisive),
        "hits": hits,
        "units_at_minus_110": units_at_minus_110,
    }


def _fit_and_score(
    training: Sequence[dict[str, Any]],
    evaluation: Sequence[dict[str, Any]],
    k: float,
    home_advantage: float,
    *,
    margin_weight: float = 0.0,
    decay_rate: float = 0.0,
    tie_method: str = "flat",
) -> tuple[HomeElo, list[dict[str, Any]]]:
    book = HomeElo(
        k=k, home_advantage=home_advantage, ratings={},
        margin_weight=margin_weight, decay_rate=decay_rate,
    )
    tie_probability = _tie_rate(training)
    _batched_predictions(book, training, tie_probability, tie_method=tie_method)
    return book, _batched_predictions(book, evaluation, tie_probability, tie_method=tie_method)


def _chronological_split(rows: Sequence[dict[str, Any]]) -> tuple[list[dict], list[dict], list[dict]]:
    dates = sorted({str(row["game_date"]) for row in rows})
    train_cut = dates[max(0, int(len(dates) * 0.60) - 1)]
    validation_cut = dates[min(len(dates) - 1, max(1, int(len(dates) * 0.80) - 1))]
    train = [row for row in rows if row["game_date"] <= train_cut]
    validation = [row for row in rows if train_cut < row["game_date"] <= validation_cut]
    test = [row for row in rows if row["game_date"] > validation_cut]
    return train, validation, test


def validate_international_baseball_baseline(
    data_root: str | Path,
    league: str,
    artifact_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Select Elo parameters on validation, then grade one locked test."""
    league = league.lower()
    directory = Path(data_root) / "international_baseball" / league
    games_path, manifest_path = directory / "games.jsonl", directory / "manifest.json"
    rows = _load_games(games_path)
    if len(rows) < 500:
        return {
            "status": "insufficient_history",
            "league": league,
            "observations": len(rows),
            "minimum_required": 500,
            "model_state": "research",
            "promotion_eligible": False,
        }
    train, validation, test = _chronological_split(rows)
    # Grid search: K × home_advantage × margin_weight × decay × tie_method
    candidates = []
    for k in K_CANDIDATES:
        for home_advantage in HOME_ADVANTAGE_CANDIDATES:
            for margin_weight in MARGIN_WEIGHT_CANDIDATES:
                for decay_rate in DECAY_CANDIDATES:
                    for tie_method in TIE_MODEL_CANDIDATES:
                        _, predictions = _fit_and_score(
                            train, validation, k, home_advantage,
                            margin_weight=margin_weight, decay_rate=decay_rate,
                            tie_method=tie_method,
                        )
                        candidates.append({
                            "k": k, "home_advantage": home_advantage,
                            "margin_weight": margin_weight, "decay_rate": decay_rate,
                            "tie_method": tie_method, **_metrics(predictions),
                        })
    chosen = min(
        candidates,
        key=lambda row: (float(row["brier_settlement"]), float(row["mae_settlement"])),
    )
    chosen_k = float(chosen["k"])
    chosen_home = float(chosen["home_advantage"])
    chosen_margin = float(chosen.get("margin_weight", 0))
    chosen_decay = float(chosen.get("decay_rate", 0))
    chosen_tie = str(chosen.get("tie_method", "flat"))
    _, test_predictions = _fit_and_score(
        [*train, *validation], test, chosen_k, chosen_home,
        margin_weight=chosen_margin, decay_rate=chosen_decay, tie_method=chosen_tie,
    )
    all_book = HomeElo(
        k=chosen_k, home_advantage=chosen_home, ratings={},
        margin_weight=chosen_margin, decay_rate=chosen_decay,
    )
    _batched_predictions(all_book, rows, _tie_rate(rows), tie_method=chosen_tie)
    source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = {
        "schema_version": "international-baseball-tie-aware-elo-v2",
        "model_version": f"{league}-tie-aware-elo-v2",
        "model_state": "research",
        "league": league,
        "target": "expected moneyline settlement where a tie pays 0.50",
        "initial_rating": 1500.0,
        "k": chosen_k,
        "home_advantage": chosen_home,
        "margin_weight": chosen_margin,
        "decay_rate": chosen_decay,
        "tie_method": chosen_tie,
        "tie_probability": round(_tie_rate(rows), 8),
        "training_observations": len(rows),
        "trained_through_date": rows[-1]["game_date"],
        "ratings": {team: round(rating, 6) for team, rating in sorted(all_book.ratings.items())},
        "source_manifest_sha256": _sha256(manifest_path),
        "games_sha256": source_manifest["games_sha256"],
        "training_prefix_sha256": _training_prefix_sha256(rows, rows[-1]["game_date"]),
        "qualified_for_betting": False,
        "units": 0,
    }
    artifact["artifact_hash"] = hashlib.sha256(_canonical_json(artifact).encode()).hexdigest()
    artifact_path = None
    if artifact_dir is not None:
        artifact_path = Path(artifact_dir) / f"{league}-tie-aware-elo-v1.json"
        _backup_before_overwrite(artifact_path)
        _atomic_write(artifact_path, json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    return {
        "status": "ok",
        "league": league,
        "model_version": artifact["model_version"],
        "model_state": "research",
        "promotion_eligible": False,
        "observations": len(rows),
        "tie_rate": round(_tie_rate(rows), 6),
        "chronological_split": {
            "train": {"n": len(train), "through_date": train[-1]["game_date"]},
            "validation": {"n": len(validation), "from_date": validation[0]["game_date"], "through_date": validation[-1]["game_date"]},
            "locked_test": {"n": len(test), "from_date": test[0]["game_date"], "through_date": test[-1]["game_date"]},
        },
        "parameter_selection_on_validation": candidates,
        "chosen": {"k": chosen_k, "home_advantage": chosen_home},
        "locked_test": _metrics(test_predictions),
        "artifact": str(artifact_path) if artifact_path else None,
        "artifact_hash": artifact["artifact_hash"],
        "point_in_time": "all games on one local date are predicted before that date updates ratings",
        "profitability": "not_established_no_point_in_time_market_prices",
        "units": 0,
        "limitations": [
            "No point-in-time starting pitcher, lineup, bullpen, park, weather, travel, or roster features yet.",
            "Tie probability is a league-wide historical baseline, not a game-specific model.",
            "No historical executable BBO archive exists for this sample.",
            "Locked-test metrics are diagnostic; v1 remains unqualified and zero-unit.",
        ],
    }


def validate_all_international_baseball_baselines(
    data_root: str | Path,
    leagues: Sequence[str],
    artifact_dir: str | Path | None = None,
) -> dict[str, Any]:
    reports = {
        league: validate_international_baseball_baseline(data_root, league, artifact_dir)
        for league in leagues
    }
    return {
        "generated_at_utc": _utc_now(),
        "scope": "research-only separate-league tie-aware moneyline baselines",
        "leagues": reports,
        "promotion_eligible": False,
        "units": 0,
    }


def _team_alias_index(teams: dict[str, dict[str, Any]]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for team_id, team in teams.items():
        for alias in (team.get("name"), *(team.get("aliases") or [])):
            if alias:
                index.setdefault(_identity_key(str(alias)), set()).add(team_id)
    return index


def forecast_international_baseball_slate(
    data_root: str | Path,
    artifact_dir: str | Path,
    league: str,
    game_date: str,
    timezone_name: str | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    """Price exact KBO/NPB moneylines as zero-unit research observations."""
    from .data_sources.polymarket_us import PolymarketUSClient
    from .domain import parse_utc, utc_now

    league = league.lower()
    if league not in LEAGUE_SPECS:
        raise ValueError(f"unsupported international baseball league: {league}")
    timezone_name = timezone_name or str(LEAGUE_SPECS[league]["timezone"])
    directory = Path(data_root) / "international_baseball" / league
    artifact_path = Path(artifact_dir) / f"{league}-tie-aware-elo-v1.json"
    if not artifact_path.exists():
        raise FileNotFoundError(f"missing research artifact: {artifact_path}")
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    expected_prefix_hash = artifact.get("training_prefix_sha256")
    if expected_prefix_hash is not None:
        # Real bug fixed 2026-08-02: NPB's settlement fallback silently
        # collapsed games.jsonl to a fraction of its real history while this
        # artifact kept using ratings trained on the destroyed data, with
        # nothing ever checking that the two still agreed. games_sha256
        # alone can't catch this prospectively (it hashes the *whole* file,
        # which legitimately grows every day) -- training_prefix_sha256
        # hashes only the rows through trained_through_date, which a
        # healthy history can never change. Older artifacts predating this
        # field (no training_prefix_sha256 key) are not retroactively
        # checked; they get the protection on their next real validation run.
        games_path = directory / "games.jsonl"
        current_prefix_hash = _training_prefix_sha256(
            _load_games(games_path), str(artifact["trained_through_date"])
        )
        if current_prefix_hash != expected_prefix_hash:
            raise ValueError(
                f"REFUSED: {league} training history has changed since "
                f"{artifact_path.name} was validated (training_prefix_sha256 "
                f"mismatch through {artifact['trained_through_date']}) -- "
                "re-run validate-international-baseball before forecasting "
                "with this artifact again."
            )
    teams = json.loads((directory / "teams.json").read_text(encoding="utf-8"))
    aliases = _team_alias_index(teams)
    market_client = client or PolymarketUSClient()
    events = market_client.slate(
        str(LEAGUE_SPECS[league]["polymarket_league"]),
        date.fromisoformat(game_date),
        timezone_name,
    )
    ratings = {key: float(value) for key, value in artifact["ratings"].items()}
    margin_weight = float(artifact.get("margin_weight", 0))
    decay_rate = float(artifact.get("decay_rate", 0))
    tie_method = str(artifact.get("tie_method", "flat"))
    book = HomeElo(
        float(artifact["k"]), float(artifact["home_advantage"]), ratings,
        margin_weight=margin_weight, decay_rate=decay_rate,
    )
    tie_probability = float(artifact["tie_probability"])
    trained_through = date.fromisoformat(str(artifact["trained_through_date"]))
    observed_now = utc_now()
    rows: list[dict[str, Any]] = []
    no_calls: list[dict[str, Any]] = []
    for event in events:
        event_start = parse_utc(str(event["event_start_utc"]))
        for market in event.get("markets", []):
            if market.get("market_type") != "moneyline":
                continue  # not a moneyline market for this event; not this function's concern
            descriptions = [str(side.get("description") or "") for side in market.get("sides", [])]
            base = {
                "event_id": event["event_id"],
                "event_start_utc": event["event_start_utc"],
                "market_slug": market.get("market_slug"),
                "teams": descriptions,
            }
            if len(market.get("sides", [])) != 2:
                # A moneyline market exists for this event, but the gateway
                # dropped a side (e.g. a transient boundary quote -- see
                # polymarket_us._normalize_event) -- this must be counted as
                # a no-call, not silently vanish from both priced_contracts
                # and no_calls.
                no_calls.append({**base, "reason": "NO_CALL_MARKET_SIDES_INVALID"})
                continue
            matches = [aliases.get(_identity_key(description), set()) for description in descriptions]
            if event_start <= observed_now:
                no_calls.append({**base, "reason": "NO_CALL_EVENT_STARTED"})
                continue
            if event_start <= datetime.combine(trained_through, datetime.min.time(), tzinfo=UTC):
                no_calls.append({**base, "reason": "NO_CALL_POINT_IN_TIME_MODEL_ARTIFACT"})
                continue
            if any(len(candidates) != 1 for candidates in matches):
                no_calls.append({**base, "reason": "NO_CALL_ENTITY_UNRESOLVED", "candidate_counts": [len(value) for value in matches]})
                continue
            team_ids = [next(iter(candidates)) for candidates in matches]
            if team_ids[0] == team_ids[1]:
                no_calls.append({**base, "reason": "NO_CALL_MODEL_UNVALIDATED_NEW_TEAM"})
                continue
            order_to_id = {
                str(side.get("selection")): team_id
                for side, team_id in zip(market["sides"], team_ids, strict=True)
            }
            away_id, home_id = order_to_id.get("away"), order_to_id.get("home")
            if not away_id or not home_id:
                no_calls.append({**base, "reason": "NO_CALL_HOME_AWAY_UNRESOLVED"})
                continue
            # Resolved by team_id (tag-safe, via order_to_id above), not by
            # position in `descriptions` -- market["sides"] has no ordering
            # guarantee (see data_sources.polymarket_us._normalize_event), so
            # a caller assuming descriptions[0]=away/descriptions[1]=home
            # would silently swap home/away whenever the raw side order
            # differs from that assumption.
            id_to_description = dict(zip(team_ids, descriptions, strict=True))
            base["away_team"] = id_to_description[away_id]
            base["home_team"] = id_to_description[home_id]
            if any(team_id not in ratings for team_id in team_ids):
                no_calls.append({**base, "reason": "NO_CALL_MODEL_UNVALIDATED_NEW_TEAM"})
                continue
            game_tie_p = _game_tie_probability(away_id, home_id, book, tie_method, tie_probability)
            away_fair, home_fair = tie_aware_fair_values(
                book.decisive_home_probability(away_id, home_id), game_tie_p
            )
            probabilities = {away_id: away_fair, home_id: home_fair}
            by_name = {
                _identity_key(description): probabilities[team_id]
                for description, team_id in zip(descriptions, team_ids, strict=True)
            }
            try:
                snapshot = market_client.snapshot(str(market["market_slug"]))
            except (httpx.HTTPError, KeyError, StopIteration, TypeError, ValueError) as error:
                no_calls.append({**base, "reason": "NO_CALL_MARKET_UNAVAILABLE", "detail": str(error)[:200]})
                continue
            sides = []
            for side_name in ("long", "short"):
                side = snapshot[side_name]
                fair = by_name.get(_identity_key(str(side["description"])))
                ask = side.get("ask")
                if fair is None or ask is None:
                    sides = []
                    break
                sides.append(
                    {
                        "side": side_name,
                        "team": side["description"],
                        "model_fair_settlement_value": round(fair, 6),
                        "win_probability_component": round(max(0.0, fair - 0.5 * game_tie_p), 6),
                        "tie_probability": round(game_tie_p, 6),
                        "executable_ask": float(ask),
                        "edge_vs_executable_ask": round(fair - float(ask), 6),
                    }
                )
            if len(sides) != 2:
                no_calls.append({**base, "reason": "NO_CALL_MARKET_OR_SIDE_UNRESOLVED"})
                continue
            rows.append(
                {
                    **base,
                    "league": league,
                    "source_team_ids": team_ids,
                    "model_version": artifact["model_version"],
                    "model_state": "research",
                    "artifact_hash": artifact["artifact_hash"],
                    "observed_at_utc": snapshot["observed_at_utc"],
                    "sides": sides,
                    "record_type": "RESEARCH_OBSERVATION",
                    "qualification": "NO_CALL_MODEL_UNVALIDATED",
                    "units": 0,
                }
            )
    return {
        "league": league,
        "game_date": game_date,
        "model_version": artifact["model_version"],
        "events": len(events),
        "priced_contracts": rows,
        "priced_count": len(rows),
        "no_calls": no_calls,
        "no_call_count": len(no_calls),
        "model_state": "research",
        "profitability": "not_established",
        "units": 0,
    }
