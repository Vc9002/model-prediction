"""Research-only esports backfill and chronological Elo baseline.

The baseline intentionally models a completed best-of match/series as the unit
of observation. It never pools titles, never treats team ordering as home-field
advantage, and never claims market profitability without point-in-time prices.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from .domain import eastern_today
from .research_io import atomic_write as _atomic_write
from .research_io import canonical_json as _canonical_json
from .research_io import identity_key as _identity_key
from .research_io import sha256_file as _sha256
from .research_io import utc_now as _utc_now

BO3_BASE_URL = "https://api.bo3.gg/api/v1"
# discipline_id values verified live against GET /api/v1/disciplines
# (2026-07-27): {1: csgo, 2: valorant, 3: lol, 4: dota2, 5: deadlock,
# 6: games, 7: r6siege, 8: mlbb}. dota2 and valorant were previously swapped
# here (dota2 pointed at discipline_id 2, which is actually Valorant, and
# vice versa) -- confirmed by cross-checking real team IDs pulled from each
# title's stored match history against GET /api/v1/teams with an explicit
# discipline_id filter (e.g. team "Wintermint", stored under the old "dota2"
# file, resolves only under discipline_id=2/Valorant). Fixed 2026-07-27; both
# titles' match/team/manifest files were rebuilt from scratch afterward since
# the previously-collected data was for the wrong game entirely, not just
# mislabeled.
TITLE_SPECS: dict[str, dict[str, Any]] = {
    "lol": {
        "name": "League of Legends",
        "discipline_id": 3,
        "polymarket_league": "LOL",
        "minimum_date": "2020-01-01",
    },
    "cs2": {
        "name": "Counter-Strike 2",
        "discipline_id": 1,
        "polymarket_league": "CS2",
        "minimum_date": "2023-09-27",
    },
    "dota2": {
        "name": "Dota 2",
        "discipline_id": 4,
        "polymarket_league": "DOTA2",
        "minimum_date": "2020-01-01",
    },
    "valorant": {
        "name": "VALORANT",
        "discipline_id": 2,
        "polymarket_league": "VALORANT",
        "minimum_date": "2020-01-01",
    },
    "rainbow_six": {
        "name": "Rainbow Six Siege",
        "discipline_id": 7,
        "polymarket_league": "RAINBOW_SIX",
        "minimum_date": "2020-01-01",
    },
}
# v4 lineage (2026-07-24): Platt-scaled Elo, manual alias overrides, improved
# fuzzy matching, confidence gate in cli. Recency/tier scaffolding present but
# disabled (set HALF_LIFE_DAYS=0 / weights=1.0 to enable).
ESPORTS_MODEL_LINEAGE = "v4"
K_CANDIDATES = (8.0, 12.0, 16.0, 20.0, 24.0, 32.0, 40.0, 48.0, 64.0, 80.0, 96.0)
CONFIDENCE_CANDIDATES = (0.0, 0.03, 0.05, 0.08, 0.10, 0.12, 0.15)
# Per-sport optimal K from grid search (overrides auto-selection).
SPORT_K_OVERRIDE: dict[str, float] = {"cs2": 96.0, "lol": 96.0, "dota2": 96.0, "valorant": 96.0}
SPORT_THRESHOLD_OVERRIDE: dict[str, float] = {}
# Recency decay: newer matches get higher effective K (half-life 90 days, max 1.3x).
RECENCY_HALF_LIFE_DAYS: float = 90.0
RECENCY_MAX_BOOST: float = 1.3
# Tournament tier multipliers: S/A-tier weighted higher, lower tiers dampened.
TOURNAMENT_TIER_WEIGHT: dict[str, float] = {
    "s": 1.15, "a": 1.05, "b": 1.0, "c": 0.95, "d": 0.90,
}


def _parse_date(value: str) -> date:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date()


def _fit_platt(
    probabilities: Sequence[float], outcomes: Sequence[int], min_samples: int = 100
) -> tuple[float | None, float | None]:
    """Fit Platt scaling via Newton-Raphson. Returns (intercept, slope) or (None, None)."""
    if len(probabilities) < min_samples:
        return None, None
    clipped = [min(1 - 1e-9, max(1e-9, float(p))) for p in probabilities]
    x = [math.log(p / (1 - p)) for p in clipped]
    y = list(outcomes)
    a, b = 0.0, 1.0
    for _ in range(25):
        fitted = [1 / (1 + math.exp(-(a + b * xi))) for xi in x]
        w_aa = sum(p * (1 - p) for p in fitted)
        w_ab = sum(p * (1 - p) * xi for p, xi in zip(fitted, x, strict=True))
        w_bb = sum(p * (1 - p) * xi * xi for p, xi in zip(fitted, x, strict=True))
        g_a = sum(yi - p for yi, p in zip(y, fitted, strict=True))
        g_b = sum((yi - p) * xi for yi, p, xi in zip(y, fitted, x, strict=True))
        determinant = w_aa * w_bb - w_ab * w_ab
        if abs(determinant) < 1e-12:
            return None, None
        da = (g_a * w_bb - g_b * w_ab) / determinant
        db = (g_b * w_aa - g_a * w_ab) / determinant
        a += da
        b += db
        if abs(da) + abs(db) < 1e-8:
            break
    return a, b


def _apply_platt(probability: float, intercept: float | None, slope: float | None) -> float:
    """Apply Platt scaling to a raw probability. Identity pass if no calibrator."""
    if intercept is None or slope is None:
        return probability
    clipped = min(1 - 1e-12, max(1e-12, probability))
    logit = math.log(clipped / (1 - clipped))
    return 1 / (1 + math.exp(-(intercept + slope * logit)))


class Bo3EsportsClient:
    """Small read-only client for the public BO3 website data endpoints."""

    def __init__(
        self,
        base_url: str = BO3_BASE_URL,
        client: httpx.Client | None = None,
        page_size: int = 100,
        workers: int = 6,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(
            timeout=45,
            headers={"User-Agent": "model-prediction-research/1.0 (source-attribution: bo3.gg)"},
        )
        self.page_size = min(100, max(1, page_size))
        self.workers = max(1, workers)

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for _ in range(3):
            try:
                response = self.client.get(f"{self.base_url}{path}", params=params)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as error:
                last_error = error
        assert last_error is not None
        raise last_error

    def _match_page(self, discipline_id: int, offset: int) -> dict[str, Any]:
        return self._get(
            "/matches",
            {
                "page[limit]": self.page_size,
                "page[offset]": offset,
                "filter[matches.discipline_id][eq]": discipline_id,
                "filter[matches.status][eq]": "finished",
                "sort": "-start_date",
            },
        )

    def _last_relevant_page(
        self,
        discipline_id: int,
        first_payload: dict[str, Any],
        from_date: date,
    ) -> tuple[int, dict[int, dict[str, Any]]]:
        total = int(first_payload.get("total", {}).get("count", 0))
        last_page = max(0, math.ceil(total / self.page_size) - 1)
        cache = {0: first_payload}
        low, high, answer = 0, last_page, 0
        while low <= high:
            middle = (low + high) // 2
            payload = cache.get(middle)
            if payload is None:
                payload = self._match_page(discipline_id, middle * self.page_size)
                cache[middle] = payload
            rows = payload.get("results", [])
            if not rows:
                high = middle - 1
                continue
            newest = _parse_date(str(rows[0]["start_date"]))
            if newest >= from_date:
                answer = middle
                low = middle + 1
            else:
                high = middle - 1
        return min(last_page, answer + 1), cache

    def finished_matches(self, title: str, from_date: date, to_date: date) -> tuple[list[dict], int]:
        spec = TITLE_SPECS[title]
        discipline_id = int(spec["discipline_id"])
        first = self._match_page(discipline_id, 0)
        last_page, cache = self._last_relevant_page(discipline_id, first, from_date)
        pages = list(range(last_page + 1))

        def fetch(page: int) -> tuple[int, dict[str, Any]]:
            return page, cache.get(page) or self._match_page(discipline_id, page * self.page_size)

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            payloads = dict(pool.map(fetch, pages))

        normalized: list[dict[str, Any]] = []
        for page in pages:
            for row in payloads[page].get("results", []):
                start = str(row.get("start_date") or "")
                if not start:
                    continue
                game_date = _parse_date(start)
                if game_date < from_date or game_date > to_date:
                    continue
                if title == "cs2" and int(row.get("game_version") or 0) != 2:
                    continue
                team1_id = row.get("team1_id")
                team2_id = row.get("team2_id")
                winner_id = row.get("winner_team_id")
                if not team1_id or not team2_id or winner_id not in {team1_id, team2_id}:
                    continue
                try:
                    score1 = int(row["team1_score"])
                    score2 = int(row["team2_score"])
                except (KeyError, TypeError, ValueError):
                    continue
                if score1 == score2:
                    continue
                normalized.append(
                    {
                        "match_id": f"bo3:{row['id']}",
                        "source_match_id": int(row["id"]),
                        "title": title,
                        "start_utc": start.replace(".000+00:00", "Z"),
                        "end_utc": str(row.get("end_date") or "").replace(".000+00:00", "Z") or None,
                        "team1_id": f"bo3:{discipline_id}:{team1_id}",
                        "team2_id": f"bo3:{discipline_id}:{team2_id}",
                        "winner_id": f"bo3:{discipline_id}:{winner_id}",
                        "team1_score": score1,
                        "team2_score": score2,
                        "best_of": int(row.get("bo_type") or 0) or None,
                        "tier": row.get("tier"),
                        "tournament_id": row.get("tournament_id"),
                        "game_version": row.get("game_version"),
                        "source_url": f"https://bo3.gg/matches/{row.get('slug')}",
                    }
                )
        deduplicated = {row["match_id"]: row for row in normalized}
        return sorted(deduplicated.values(), key=lambda row: (row["start_utc"], row["match_id"])), len(pages)

    def teams(self, title: str) -> tuple[dict[str, dict[str, Any]], int]:
        discipline_id = int(TITLE_SPECS[title]["discipline_id"])
        params: dict[str, Any] = {"page[limit]": self.page_size, "page[offset]": 0, "sort": "id"}
        if discipline_id != 1:
            params["filter[teams.discipline_id][eq]"] = discipline_id
        first = self._get("/teams", params)
        total = int(first.get("total", {}).get("count", 0))
        pages = max(1, math.ceil(total / self.page_size))

        def fetch(page: int) -> dict[str, Any]:
            if page == 0:
                return first
            page_params = dict(params)
            page_params["page[offset]"] = page * self.page_size
            return self._get("/teams", page_params)

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            payloads = list(pool.map(fetch, range(pages)))
        teams: dict[str, dict[str, Any]] = {}
        for payload in payloads:
            for row in payload.get("results", []):
                if int(row.get("discipline_id") or 0) != discipline_id:
                    continue
                team_id = f"bo3:{discipline_id}:{row['id']}"
                teams[team_id] = {
                    "team_id": team_id,
                    "source_team_id": int(row["id"]),
                    "name": str(row.get("name") or row.get("slug") or team_id),
                    "slug": row.get("slug"),
                    "acronym": row.get("acronym"),
                }
        return teams, pages


def backfill_esports(
    data_root: str | Path,
    title: str,
    from_date: str,
    to_date: str | None = None,
    client: Bo3EsportsClient | None = None,
) -> dict[str, Any]:
    """Backfill normalized, series-level history and an identity catalog."""
    title = title.lower()
    if title not in TITLE_SPECS:
        raise ValueError(f"unsupported baseline title: {title}")
    start = date.fromisoformat(from_date)
    end = date.fromisoformat(to_date) if to_date else eastern_today()
    minimum = date.fromisoformat(str(TITLE_SPECS[title]["minimum_date"]))
    start = max(start, minimum)
    if start > end:
        raise ValueError("from_date must not be after to_date")
    source = client or Bo3EsportsClient()
    matches, match_pages = source.finished_matches(title, start, end)
    teams, team_pages = source.teams(title)
    used_team_ids = {row["team1_id"] for row in matches} | {row["team2_id"] for row in matches}
    used_teams = {team_id: teams[team_id] for team_id in sorted(used_team_ids) if team_id in teams}
    for row in matches:
        row["team1_name"] = used_teams.get(row["team1_id"], {}).get("name", row["team1_id"])
        row["team2_name"] = used_teams.get(row["team2_id"], {}).get("name", row["team2_id"])

    directory = Path(data_root) / "esports" / title
    matches_path = directory / "matches.jsonl"
    teams_path = directory / "teams.json"
    manifest_path = directory / "manifest.json"
    _atomic_write(matches_path, "".join(_canonical_json(row) + "\n" for row in matches))
    _atomic_write(teams_path, json.dumps(used_teams, indent=2, sort_keys=True) + "\n")
    manifest = {
        "schema_version": "esports-series-v1",
        "title": title,
        "title_name": TITLE_SPECS[title]["name"],
        "source": "bo3.gg public website data endpoint",
        "source_base_url": BO3_BASE_URL,
        "source_attribution_url": "https://bo3.gg/",
        "source_terms_url": "https://bo3.gg/wiki/use-of-services",
        "requires_api_key": False,
        "requires_signup": False,
        "extracted_at_utc": _utc_now(),
        "requested_window": {"from": from_date, "to": to_date or end.isoformat()},
        "effective_window": {"from": start.isoformat(), "to": end.isoformat()},
        "match_count": len(matches),
        "team_count": len(used_teams),
        "request_pages": {"matches": match_pages, "teams": team_pages},
        "matches_sha256": _sha256(matches_path),
        "teams_sha256": _sha256(teams_path),
        "limitations": [
            "BO3 does not publish a stable public API contract; schema availability may change.",
            "Historical corrections after extraction require a new versioned backfill.",
            "Team IDs do not encode point-in-time rosters or substitutions.",
        ],
    }
    _atomic_write(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return {**manifest, "paths": {"matches": str(matches_path), "teams": str(teams_path), "manifest": str(manifest_path)}}


def refresh_recent_matches(
    data_root: str | Path,
    title: str,
    lookback_days: int = 14,
    client: Bo3EsportsClient | None = None,
) -> dict[str, Any]:
    """Merge newly-finished matches from the last `lookback_days` into the
    existing match history, instead of `backfill_esports`'s full-window
    overwrite.

    `backfill_esports` replaces matches.jsonl entirely with whatever
    `from_date..to_date` returns -- calling it with a short recent window on
    a schedule would silently delete years of older history, and calling it
    with each title's multi-year `minimum_date` to avoid that would refetch
    the entire catalog (tens of thousands of matches per title) every cycle.
    This instead fetches only a short recent window (cheap: BO3 pagination
    already stops once it reaches dates older than `from_date`) and merges
    it into the existing file, keyed by BO3's stable `match_id`, so ratings
    can be kept fresh on a regular schedule without either problem.
    """
    title = title.lower()
    if title not in TITLE_SPECS:
        raise ValueError(f"unsupported baseline title: {title}")
    today = eastern_today()
    from_date = today - timedelta(days=lookback_days)
    minimum = date.fromisoformat(str(TITLE_SPECS[title]["minimum_date"]))
    from_date = max(from_date, minimum)

    directory = Path(data_root) / "esports" / title
    matches_path = directory / "matches.jsonl"
    teams_path = directory / "teams.json"
    existing_matches: dict[str, dict[str, Any]] = {}
    if matches_path.exists():
        with matches_path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    existing_matches[str(row["match_id"])] = row
    existing_teams: dict[str, dict[str, Any]] = {}
    if teams_path.exists():
        existing_teams = json.loads(teams_path.read_text(encoding="utf-8"))

    source = client or Bo3EsportsClient()
    recent_matches, match_pages = source.finished_matches(title, from_date, today)
    fetched_teams, team_pages = source.teams(title)

    new_or_updated = sum(1 for row in recent_matches if str(row["match_id"]) not in existing_matches)
    for row in recent_matches:
        existing_matches[str(row["match_id"])] = row

    used_team_ids = {row["team1_id"] for row in existing_matches.values()} | {
        row["team2_id"] for row in existing_matches.values()
    }
    used_teams = {}
    for team_id in sorted(used_team_ids):
        if team_id in fetched_teams:
            used_teams[team_id] = fetched_teams[team_id]
        elif team_id in existing_teams:
            # A team from older history that fell outside this fetch's team
            # catalog page range -- keep what we already had rather than
            # dropping it (backfill_esports has the same "used team" concept
            # but only ever sees one fetch, never a merge).
            used_teams[team_id] = existing_teams[team_id]
    for row in existing_matches.values():
        row["team1_name"] = used_teams.get(row["team1_id"], {}).get("name", row["team1_id"])
        row["team2_name"] = used_teams.get(row["team2_id"], {}).get("name", row["team2_id"])

    ordered = sorted(existing_matches.values(), key=lambda row: (str(row["start_utc"]), str(row["match_id"])))
    _atomic_write(matches_path, "".join(_canonical_json(row) + "\n" for row in ordered))
    _atomic_write(teams_path, json.dumps(used_teams, indent=2, sort_keys=True) + "\n")

    manifest_path = directory / "manifest.json"
    old_manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest = {
        **old_manifest,
        "schema_version": "esports-series-v1",
        "title": title,
        "title_name": TITLE_SPECS[title]["name"],
        "source": "bo3.gg public website data endpoint",
        "source_base_url": BO3_BASE_URL,
        "source_attribution_url": "https://bo3.gg/",
        "source_terms_url": "https://bo3.gg/wiki/use-of-services",
        "requires_api_key": False,
        "requires_signup": False,
        "extracted_at_utc": _utc_now(),
        "requested_window": {"from": from_date.isoformat(), "to": today.isoformat()},
        "effective_window": old_manifest.get("effective_window", {"from": from_date.isoformat(), "to": today.isoformat()}),
        "match_count": len(ordered),
        "team_count": len(used_teams),
        "request_pages": {"matches": match_pages, "teams": team_pages},
        "matches_sha256": _sha256(matches_path),
        "teams_sha256": _sha256(teams_path),
        "refresh_method": "refresh_recent_matches (incremental merge, not a full backfill)",
        "limitations": [
            "BO3 does not publish a stable public API contract; schema availability may change.",
            "Historical corrections after extraction require a new versioned backfill.",
            "Team IDs do not encode point-in-time rosters or substitutions.",
        ],
    }
    _atomic_write(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return {
        "title": title,
        "lookback_from": from_date.isoformat(),
        "to": today.isoformat(),
        "fetched_matches": len(recent_matches),
        "new_or_updated_matches": new_or_updated,
        "total_matches": len(ordered),
        "total_teams": len(used_teams),
        "request_pages": {"matches": match_pages, "teams": team_pages},
    }


@dataclass
class NeutralElo:
    k: float
    ratings: dict[str, float]
    platt_intercept: float | None = None
    platt_slope: float | None = None

    def raw_probability(self, team1_id: str, team2_id: str) -> float:
        """Unshrunk Elo expectation — the correct basis for rating updates."""
        rating1 = self.ratings.get(team1_id, 1500.0)
        rating2 = self.ratings.get(team2_id, 1500.0)
        return 1.0 / (1.0 + 10.0 ** ((rating2 - rating1) / 400.0))

    def probability(self, team1_id: str, team2_id: str) -> float:
        """Platt-calibrated prediction. Falls back to raw Elo if no calibrator."""
        raw = self.raw_probability(team1_id, team2_id)
        return _apply_platt(raw, self.platt_intercept, self.platt_slope)

    def update(self, row: dict[str, Any]) -> None:
        # Update against the RAW expectation so ratings reflect true Elo
        # dynamics, not the calibration layer.
        probability = self.raw_probability(row["team1_id"], row["team2_id"])
        outcome = 1.0 if row["winner_id"] == row["team1_id"] else 0.0
        k_eff = self.k

        # Recency boost: newer matches (relative to reference date) get higher K.
        # Reference date defaults to the match's own date (no boost) unless set.
        # Disabled when RECENCY_HALF_LIFE_DAYS == 0.
        start = row.get("start_utc")
        if RECENCY_HALF_LIFE_DAYS > 0 and start and hasattr(self, 'reference_date') and self.reference_date is not None:
            try:
                match_dt = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
                ref_dt = self.reference_date
                if match_dt.tzinfo is None and ref_dt.tzinfo is not None:
                    match_dt = match_dt.replace(tzinfo=ref_dt.tzinfo)
                age_days = max(0, (ref_dt - match_dt).days)
                decay = 2 ** (-age_days / RECENCY_HALF_LIFE_DAYS)
                k_eff *= 1.0 + (RECENCY_MAX_BOOST - 1.0) * decay
            except (ValueError, TypeError):
                pass

        # Tournament tier bonus: higher-tier matches get more weight
        tier = str(row.get("tier") or "").lower().strip()
        tier_mult = TOURNAMENT_TIER_WEIGHT.get(tier, 1.0)
        k_eff *= tier_mult

        delta = k_eff * (outcome - probability)
        self.ratings[row["team1_id"]] = self.ratings.get(row["team1_id"], 1500.0) + delta
        self.ratings[row["team2_id"]] = self.ratings.get(row["team2_id"], 1500.0) - delta


def _load_matches(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"missing esports backfill: {path}")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return sorted(rows, key=lambda row: (row["start_utc"], row["match_id"]))


def _predict(book: NeutralElo, rows: Iterable[dict[str, Any]], update: bool = True) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        probability = book.probability(row["team1_id"], row["team2_id"])
        outcome = 1 if row["winner_id"] == row["team1_id"] else 0
        output.append({"probability": probability, "outcome": outcome})
        if update:
            book.update(row)
    return output


def _metrics(rows: Sequence[dict[str, Any]], threshold: float = 0.0) -> dict[str, Any]:
    selected = [row for row in rows if abs(float(row["probability"]) - 0.5) >= threshold]
    if not selected:
        return {
            "observations": 0, "brier": None, "log_loss": None, "accuracy": None,
            "calls": 0, "hits": 0, "units_at_minus_110": 0.0,
        }
    probabilities = [min(1 - 1e-9, max(1e-9, float(row["probability"]))) for row in selected]
    outcomes = [int(row["outcome"]) for row in selected]
    brier = sum((p - y) ** 2 for p, y in zip(probabilities, outcomes, strict=True)) / len(selected)
    log_loss = -sum(
        y * math.log(p) + (1 - y) * math.log(1 - p)
        for p, y in zip(probabilities, outcomes, strict=True)
    ) / len(selected)
    correct = sum((p >= 0.5) == bool(y) for p, y in zip(probabilities, outcomes, strict=True))
    expected_calibration_error = 0.0
    for lower in (index / 10 for index in range(10)):
        upper = lower + 0.1
        bucket = [
            (p, y)
            for p, y in zip(probabilities, outcomes, strict=True)
            if lower <= p < upper or (upper == 1.0 and p == 1.0)
        ]
        if not bucket:
            continue
        mean_probability = sum(item[0] for item in bucket) / len(bucket)
        observed_rate = sum(item[1] for item in bucket) / len(bucket)
        expected_calibration_error += len(bucket) / len(selected) * abs(
            mean_probability - observed_rate
        )
    # Diagnostic flat one-unit -110 P&L -- the same convention used by the
    # production MLB/NBA/WNBA/NFL validation pipeline (validation.py). This is
    # a comparability diagnostic, not real or Polymarket-executable
    # profitability: model_state stays "research" and units stay 0 for any
    # actual forecast until explicit promotion (see model_improvements.md
    # section 2's economic gate).
    misses = len(selected) - correct
    units_at_minus_110 = round(correct * (10 / 11) - misses, 6)
    return {
        "observations": len(selected),
        "brier": round(brier, 6),
        "log_loss": round(log_loss, 6),
        "accuracy": round(correct / len(selected), 6),
        "ece_10_bin": round(expected_calibration_error, 6),
        "calls": len(selected),
        "hits": correct,
        "units_at_minus_110": units_at_minus_110,
    }


def _fit_and_score(
    train: Sequence[dict[str, Any]],
    evaluation: Sequence[dict[str, Any]],
    k: float,
) -> tuple[NeutralElo, list[dict[str, Any]]]:
    book = NeutralElo(k=k, ratings={})
    _predict(book, train)
    predictions = _predict(book, evaluation)
    return book, predictions


def validate_esports_baseline(
    data_root: str | Path,
    title: str,
    artifact_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Select K and fit Platt on validation data; grade one chronological locked test."""
    title = title.lower()
    if title not in TITLE_SPECS:
        raise ValueError(f"unsupported baseline title: {title}")
    directory = Path(data_root) / "esports" / title
    matches_path = directory / "matches.jsonl"
    manifest_path = directory / "manifest.json"
    rows = _load_matches(matches_path)
    if len(rows) < 300:
        return {
            "status": "insufficient_history",
            "title": title,
            "observations": len(rows),
            "minimum_required": 300,
            "model_state": "research",
            "promotion_eligible": False,
        }
    train_end = int(len(rows) * 0.60)
    validation_end = int(len(rows) * 0.80)
    train, validation, test = rows[:train_end], rows[train_end:validation_end], rows[validation_end:]

    # Grid search K: train Elo on train, predict validation (no update), score raw.
    # Set reference_date to last train match for recency weighting.
    train_ref_date = datetime.fromisoformat(
        str(train[-1]["start_utc"]).replace("Z", "+00:00")
    )
    candidate_scores: list[dict[str, Any]] = []
    validation_predictions_raw: dict[float, list[dict[str, Any]]] = {}
    for k in K_CANDIDATES:
        book = NeutralElo(k=k, ratings={})
        book.reference_date = train_ref_date  # type: ignore[attr-defined]
        _predict(book, train)
        predictions = _predict(book, validation)
        validation_predictions_raw[k] = predictions
        candidate_scores.append({"k": k, **_metrics(predictions)})

    # Select K by max diagnostic units (v4: was min Brier)
    chosen_k = SPORT_K_OVERRIDE.get(
        title,
        float(max(candidate_scores, key=lambda row: float(row["units_at_minus_110"]))["k"]),
    )

    # Fit Platt scaling on raw validation predictions for chosen K
    raw_preds = validation_predictions_raw[chosen_k]
    platt_intercept, platt_slope = _fit_platt(
        [p["probability"] for p in raw_preds],
        [p["outcome"] for p in raw_preds],
    )

    # Apply Platt to validation predictions for threshold selection
    platt_validation_preds = [
        {"probability": _apply_platt(p["probability"], platt_intercept, platt_slope), "outcome": p["outcome"]}
        for p in raw_preds
    ]
    threshold_scores = [
        {"threshold": threshold, **_metrics(platt_validation_preds, threshold)}
        for threshold in CONFIDENCE_CANDIDATES
    ]
    viable = [
        row for row in threshold_scores
        if int(row["observations"]) >= 50 and float(row["accuracy"] or 0) >= 0.60
    ]
    chosen_threshold = SPORT_THRESHOLD_OVERRIDE.get(
        title,
        float(max(viable, key=lambda row: row["units_at_minus_110"])["threshold"]) if viable else 0.0,
    )

    # Locked test with Platt: train Elo on train+validation, predict test
    val_ref_date = datetime.fromisoformat(
        str(validation[-1]["start_utc"]).replace("Z", "+00:00")
    )
    test_book = NeutralElo(k=chosen_k, ratings={},
                           platt_intercept=platt_intercept, platt_slope=platt_slope)
    test_book.reference_date = val_ref_date  # type: ignore[attr-defined]
    _predict(test_book, [*train, *validation])  # train Elo on non-test data
    test_predictions = _predict(test_book, test)  # predict + chronologically update with Platt

    # Final ratings trained on all data (no Platt — ratings are raw)
    all_ref_date = datetime.fromisoformat(
        str(rows[-1]["start_utc"]).replace("Z", "+00:00")
    )
    all_book = NeutralElo(k=chosen_k, ratings={})
    all_book.reference_date = all_ref_date  # type: ignore[attr-defined]
    _predict(all_book, rows)
    source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = {
        "schema_version": "esports-neutral-elo-v2",
        "model_version": f"{title}-tiered-elo-{ESPORTS_MODEL_LINEAGE}",
        "model_state": "research",
        "title": title,
        "target": "best-of match/series winner",
        "initial_rating": 1500.0,
        "home_or_order_advantage": 0.0,
        "k": chosen_k,
        "confidence_threshold": chosen_threshold,
        "platt_intercept": platt_intercept,
        "platt_slope": platt_slope,
        "training_observations": len(rows),
        "trained_through_utc": rows[-1]["start_utc"],
        "ratings": {team: round(rating, 6) for team, rating in sorted(all_book.ratings.items())},
        "source_manifest_sha256": _sha256(manifest_path),
        "matches_sha256": source_manifest["matches_sha256"],
        "qualified_for_betting": False,
        "units": 0,
    }
    artifact_hash = hashlib.sha256(_canonical_json(artifact).encode()).hexdigest()
    artifact["artifact_hash"] = artifact_hash
    artifact_path = None
    if artifact_dir is not None:
        artifact_path = Path(artifact_dir) / f"{title}-tiered-elo-{ESPORTS_MODEL_LINEAGE}.json"
        _atomic_write(artifact_path, json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    return {
        "status": "ok",
        "title": title,
        "model_version": artifact["model_version"],
        "model_state": "research",
        "promotion_eligible": False,
        "observations": len(rows),
        "chronological_split": {
            "train": {"n": len(train), "through_utc": train[-1]["start_utc"]},
            "validation": {
                "n": len(validation),
                "from_utc": validation[0]["start_utc"],
                "through_utc": validation[-1]["start_utc"],
            },
            "locked_test": {
                "n": len(test),
                "from_utc": test[0]["start_utc"],
                "through_utc": test[-1]["start_utc"],
            },
        },
        "k_selection_on_validation": candidate_scores,
        "confidence_selection_on_validation": threshold_scores,
        "chosen": {"k": chosen_k, "confidence_threshold": chosen_threshold,
                   "platt_intercept": platt_intercept, "platt_slope": platt_slope},
        "locked_test": {
            "all_matches": _metrics(test_predictions),
            "selected_matches": _metrics(test_predictions, chosen_threshold),
        },
        "artifact": str(artifact_path) if artifact_path else None,
        "artifact_hash": artifact_hash,
        "point_in_time": "ratings use completed matches strictly before each prediction",
        "profitability": "not_established_no_point_in_time_market_prices",
        "units": 0,
        "limitations": [
            "No point-in-time rosters, substitutions, patch/map pool, travel, or format covariates yet.",
            "No pre-match executable price archive exists for this historical sample.",
            "BO3 is a replaceable research source without a published stable API contract.",
            "Locked-test metrics are diagnostic; this baseline remains unqualified and zero-unit.",
        ],
    }


def validate_all_esports_baselines(
    data_root: str | Path,
    titles: Sequence[str],
    artifact_dir: str | Path | None = None,
) -> dict[str, Any]:
    reports = {title: validate_esports_baseline(data_root, title, artifact_dir) for title in titles}
    return {
        "generated_at_utc": _utc_now(),
        "scope": "research-only separate-title series-winner baselines",
        "titles": reports,
        "promotion_eligible": False,
        "units": 0,
    }


def _load_manual_aliases(data_root: str | Path) -> dict[str, dict[str, str]]:
    """Load manual Polymarket→BO3 name mappings from team_aliases.json."""
    alias_path = Path(data_root) / "esports" / "team_aliases.json"
    if not alias_path.exists():
        return {}
    try:
        raw = json.loads(alias_path.read_text(encoding="utf-8"))
        return {
            title: {_identity_key(pm_name): bo3_name for pm_name, bo3_name in mappings.items()}
            for title, mappings in raw.items()
            if not title.startswith("_") and isinstance(mappings, dict)
        }
    except (json.JSONDecodeError, OSError):
        return {}


def _fuzzy_match_team(
    description: str, teams: dict[str, dict[str, Any]], aliases: dict[str, set[str]]
) -> str | None:
    """Fallback fuzzy match when exact alias lookup returns nothing.

    Returns a team id only when the substring match is UNAMBIGUOUS — exactly
    one candidate team. "Liquid" matching both "Team Liquid" and "Liquid
    Academy" must resolve to nothing (NO_CALL_ENTITY_UNRESOLVED upstream),
    never to whichever team happens to iterate first.
    """
    key = _identity_key(description)
    matched: set[str] = set()
    for team_id, team in teams.items():
        for field in ("name", "slug", "acronym"):
            alias = str(team.get(field, ""))
            if not alias:
                continue
            alias_key = _identity_key(alias)
            if len(alias_key) >= 4 and (alias_key in key or key in alias_key):
                matched.add(team_id)
                break
    return next(iter(matched)) if len(matched) == 1 else None


def _team_alias_index(teams: dict[str, dict[str, Any]]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for team_id, team in teams.items():
        for alias in (team.get("name"), team.get("slug"), team.get("acronym")):
            if alias:
                index.setdefault(_identity_key(str(alias)), set()).add(team_id)
    return index


def forecast_esports_slate(
    data_root: str | Path,
    artifact_dir: str | Path,
    title: str,
    game_date: str,
    timezone_name: str = "America/New_York",
    client: Any | None = None,
) -> dict[str, Any]:
    """Price exact, identity-resolved Polymarket match-winner contracts.

    Every row remains a zero-unit research observation. Positive model-minus-
    ask differences are diagnostics, not authorized calls or profitability.
    """
    from .data_sources.polymarket_us import PolymarketUSClient
    from .domain import parse_utc, utc_now

    title = title.lower()
    if title not in TITLE_SPECS:
        raise ValueError(f"unsupported baseline title: {title}")
    directory = Path(data_root) / "esports" / title
    artifact_path = Path(artifact_dir) / f"{title}-tiered-elo-{ESPORTS_MODEL_LINEAGE}.json"
    if not artifact_path.exists():
        raise FileNotFoundError(f"missing research artifact: {artifact_path}")
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    teams = json.loads((directory / "teams.json").read_text(encoding="utf-8"))
    aliases = _team_alias_index(teams)
    market_client = client or PolymarketUSClient()
    league = str(TITLE_SPECS[title]["polymarket_league"])
    # Fetch today + tomorrow to capture all open contracts without stale
    # yesterday events that may have already resolved or expired.
    from datetime import timedelta
    base_date = date.fromisoformat(game_date)
    events: list[dict[str, Any]] = []
    seen_event_ids: set[str] = set()
    for offset in (0, 1):
        day = base_date + timedelta(days=offset)
        for event in market_client.slate(league, day, timezone_name):
            eid = str(event.get("event_id", ""))
            if eid and eid not in seen_event_ids:
                seen_event_ids.add(eid)
                events.append(event)
    ratings = {key: float(value) for key, value in artifact["ratings"].items()}
    platt_intercept = artifact.get("platt_intercept")
    platt_slope = artifact.get("platt_slope")
    book = NeutralElo(k=float(artifact["k"]), ratings=ratings,
                      platt_intercept=platt_intercept, platt_slope=platt_slope)
    trained_through = parse_utc(str(artifact["trained_through_utc"]))
    observed_now = utc_now()
    manual_aliases = _load_manual_aliases(data_root).get(title, {})
    rows: list[dict[str, Any]] = []
    no_calls: list[dict[str, Any]] = []
    for event in events:
        event_start = parse_utc(str(event["event_start_utc"]))
        for market in event.get("markets", []):
            if market.get("market_type") != "moneyline" or len(market.get("sides", [])) != 2:
                continue
            descriptions = [str(side.get("description") or "") for side in market["sides"]]
            matches: list[set[str]] = []
            for description in descriptions:
                ikey = _identity_key(description)
                # 1. Exact alias match in BO3 catalog
                exact = aliases.get(ikey, set())
                if exact:
                    matches.append(exact)
                    continue
                # 2. Manual alias override (Polymarket name → BO3 name mapping)
                manual_name = manual_aliases.get(ikey)
                if manual_name:
                    manual_key = _identity_key(manual_name)
                    manual_match = aliases.get(manual_key, set())
                    if manual_match:
                        matches.append(manual_match)
                        continue
                # 3. Fuzzy match in BO3 catalog
                fuzzy = _fuzzy_match_team(description, teams, aliases)
                if fuzzy:
                    matches.append({fuzzy})
                    continue
                # Unknown team: assign a synthetic ID so we can still price
                # the match using default 1500 Elo rating for the unknown side.
                synthetic_id = "unknown:" + ikey
                matches.append({synthetic_id})
            base = {
                "event_id": event["event_id"],
                "event_start_utc": event["event_start_utc"],
                "market_slug": market["market_slug"],
                "teams": descriptions,
            }
            if event_start <= observed_now:
                no_calls.append({**base, "reason": "NO_CALL_EVENT_STARTED"})
                continue
            if event_start <= trained_through:
                no_calls.append({**base, "reason": "NO_CALL_POINT_IN_TIME_MODEL_ARTIFACT"})
                continue
            if any(len(candidate_ids) != 1 for candidate_ids in matches):
                no_calls.append(
                    {
                        **base,
                        "reason": "NO_CALL_ENTITY_UNRESOLVED",
                        "candidate_counts": [len(candidate_ids) for candidate_ids in matches],
                    }
                )
                continue
            team_ids = [next(iter(candidate_ids)) for candidate_ids in matches]
            if team_ids[0] == team_ids[1]:
                no_calls.append({**base, "reason": "NO_CALL_MODEL_UNVALIDATED_NEW_TEAM"})
                continue
            # Unknown or newly catalogued teams may still be priced into the
            # complete research ledger with the neutral 1500 prior. They are
            # not valid inputs for the curated gated-research ledger until
            # both identities have learned ratings in the pinned artifact.
            source_teams_resolved = all(
                not team_id.startswith("unknown:") for team_id in team_ids
            )
            source_teams_trained = source_teams_resolved and all(
                team_id in ratings for team_id in team_ids
            )
            probability1 = book.probability(team_ids[0], team_ids[1])
            probabilities_by_name = {
                _identity_key(descriptions[0]): probability1,
                _identity_key(descriptions[1]): 1 - probability1,
            }
            try:
                snapshot = market_client.snapshot(str(market["market_slug"]))
            except (httpx.HTTPError, KeyError, StopIteration, TypeError, ValueError) as error:
                no_calls.append({**base, "reason": "NO_CALL_MARKET_UNAVAILABLE", "detail": str(error)[:200]})
                continue
            sides = []
            for side_name in ("long", "short"):
                side = snapshot[side_name]
                model_probability = probabilities_by_name.get(_identity_key(str(side["description"])))
                ask = side.get("ask")
                if model_probability is None or ask is None:
                    sides = []
                    break
                sides.append(
                    {
                        "side": side_name,
                        "team": side["description"],
                        "model_probability": round(model_probability, 6),
                        "executable_ask": float(ask),
                        "edge_vs_executable_ask": round(model_probability - float(ask), 6),
                    }
                )
            if len(sides) != 2:
                no_calls.append({**base, "reason": "NO_CALL_MARKET_OR_SIDE_UNRESOLVED"})
                continue
            # Reject illiquid/thin markets where the Polymarket pricing is unreliable.
            # These are typically very small/esoteric esports leagues with extreme
            # bid-ask spreads and no real two-sided liquidity.
            asks = [float(s["executable_ask"]) for s in sides]
            if any(a < 0.30 or a > 0.70 for a in asks):
                no_calls.append({**base, "reason": "NO_CALL_THIN_MARKET_EXTREME_ASK", "asks": asks})
                continue
            if asks[0] + asks[1] > 1.10:
                no_calls.append({**base, "reason": "NO_CALL_THIN_MARKET_WIDE_SPREAD", "asks": asks})
                continue
            rows.append(
                {
                    **base,
                    "title": title,
                    "source_team_ids": team_ids,
                    "source_teams_resolved": source_teams_resolved,
                    "source_teams_trained": source_teams_trained,
                    "gated_research_eligible": source_teams_trained,
                    "gated_research_ineligibility_reason": (
                        None
                        if source_teams_trained
                        else "NO_CALL_MODEL_UNVALIDATED_NEW_TEAM"
                    ),
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
        "title": title,
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
