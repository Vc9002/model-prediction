"""Research-only esports backfill and chronological Elo baseline.

The baseline intentionally models a completed best-of match/series as the unit
of observation. It never pools titles, never treats team ordering as home-field
advantage, and never claims market profitability without point-in-time prices.
"""

from __future__ import annotations

import hashlib
import json
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import httpx

from .domain import eastern_today
from .research_io import atomic_write as _atomic_write
from .research_io import canonical_json as _canonical_json
from .research_io import identity_key as _identity_key
from .research_io import sha256_file as _sha256
from .research_io import utc_now as _utc_now


BO3_BASE_URL = "https://api.bo3.gg/api/v1"
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
}
K_CANDIDATES = (8.0, 12.0, 16.0, 20.0, 24.0, 32.0, 40.0, 48.0)
CONFIDENCE_CANDIDATES = (0.0, 0.03, 0.05, 0.08, 0.10, 0.12, 0.15)


def _parse_date(value: str) -> date:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date()


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
    if start < minimum:
        start = minimum
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


@dataclass
class NeutralElo:
    k: float
    ratings: dict[str, float]

    def probability(self, team1_id: str, team2_id: str) -> float:
        rating1 = self.ratings.get(team1_id, 1500.0)
        rating2 = self.ratings.get(team2_id, 1500.0)
        return 1.0 / (1.0 + 10.0 ** ((rating2 - rating1) / 400.0))

    def update(self, row: dict[str, Any]) -> None:
        probability = self.probability(row["team1_id"], row["team2_id"])
        outcome = 1.0 if row["winner_id"] == row["team1_id"] else 0.0
        delta = self.k * (outcome - probability)
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
        return {"observations": 0, "brier": None, "log_loss": None, "accuracy": None}
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
    return {
        "observations": len(selected),
        "brier": round(brier, 6),
        "log_loss": round(log_loss, 6),
        "accuracy": round(correct / len(selected), 6),
        "ece_10_bin": round(expected_calibration_error, 6),
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
    """Select K on validation data and grade one chronological locked test."""
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
    candidate_scores: list[dict[str, Any]] = []
    validation_predictions: dict[float, list[dict[str, Any]]] = {}
    for k in K_CANDIDATES:
        _, predictions = _fit_and_score(train, validation, k)
        validation_predictions[k] = predictions
        candidate_scores.append({"k": k, **_metrics(predictions)})
    chosen = min(candidate_scores, key=lambda row: (float(row["brier"]), float(row["log_loss"])))
    chosen_k = float(chosen["k"])
    threshold_scores = [
        {"threshold": threshold, **_metrics(validation_predictions[chosen_k], threshold)}
        for threshold in CONFIDENCE_CANDIDATES
    ]
    viable = [
        row for row in threshold_scores
        if int(row["observations"]) >= 50 and float(row["accuracy"] or 0) >= 0.60
    ]
    chosen_threshold = float(max(viable, key=lambda row: row["observations"])["threshold"]) if viable else 0.0
    _, test_predictions = _fit_and_score([*train, *validation], test, chosen_k)
    all_book, _ = _fit_and_score(rows, (), chosen_k)
    source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = {
        "schema_version": "esports-neutral-elo-v1",
        "model_version": f"{title}-neutral-series-elo-v1",
        "model_state": "research",
        "title": title,
        "target": "best-of match/series winner",
        "initial_rating": 1500.0,
        "home_or_order_advantage": 0.0,
        "k": chosen_k,
        "confidence_threshold": chosen_threshold,
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
        artifact_path = Path(artifact_dir) / f"{title}-neutral-series-elo-v1.json"
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
        "chosen": {"k": chosen_k, "confidence_threshold": chosen_threshold},
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
            "Locked-test metrics are diagnostic; this v1 baseline remains unqualified and zero-unit.",
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
    artifact_path = Path(artifact_dir) / f"{title}-neutral-series-elo-v1.json"
    if not artifact_path.exists():
        raise FileNotFoundError(f"missing research artifact: {artifact_path}")
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    teams = json.loads((directory / "teams.json").read_text(encoding="utf-8"))
    aliases = _team_alias_index(teams)
    market_client = client or PolymarketUSClient()
    league = str(TITLE_SPECS[title]["polymarket_league"])
    events = market_client.slate(league, date.fromisoformat(game_date), timezone_name)
    ratings = {key: float(value) for key, value in artifact["ratings"].items()}
    book = NeutralElo(k=float(artifact["k"]), ratings=ratings)
    trained_through = parse_utc(str(artifact["trained_through_utc"]))
    observed_now = utc_now()
    rows: list[dict[str, Any]] = []
    no_calls: list[dict[str, Any]] = []
    for event in events:
        event_start = parse_utc(str(event["event_start_utc"]))
        for market in event.get("markets", []):
            if market.get("market_type") != "moneyline" or len(market.get("sides", [])) != 2:
                continue
            descriptions = [str(side.get("description") or "") for side in market["sides"]]
            matches = [aliases.get(_identity_key(description), set()) for description in descriptions]
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
            if team_ids[0] == team_ids[1] or any(team_id not in ratings for team_id in team_ids):
                no_calls.append({**base, "reason": "NO_CALL_MODEL_UNVALIDATED_NEW_TEAM"})
                continue
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
            rows.append(
                {
                    **base,
                    "title": title,
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
