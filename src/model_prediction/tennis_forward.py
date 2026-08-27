"""Point-in-time tennis moneyline research against executable Polymarket BBOs.

Singles only -- Polymarket US never lists doubles markets, and ESPN's doubles
draws (``roster``-shaped competitors) are excluded at the ingestion layer
(``data_sources/espn.py::completed_tennis_singles_matches``) before this
module ever sees them.

WTA and ATP both price here (ATP added 2026-08-03 -- Polymarket US's "ATP has
no market" was true as of 2026-07-16 but is stale; live-verified via
``POLYMARKET_SPORT_LEAGUES["tennis"]`` that a real, operational ATP league
now exists on the gateway, and ESPN already has a matching ATP scoreboard).
ITF (men's/women's) still cannot ever be priced -- ESPN has no ITF scoreboard
path at all, so those legs remain BBO-capture-only, same as before.

Combined ATP+WTA tournaments (majors, most 500/1000s) return the SAME event
from BOTH the ``tennis/atp`` and ``tennis/wta`` site-API paths -- exactly
like ``completed_tennis_singles_matches``'s historical case, the tour is
derived per match from the competition's own ``type.slug``, never from which
endpoint served it, and matches are deduped by ``event_id`` across both
endpoint fetches.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from .data_sources.espn import _infer_tennis_surface
from .domain import EASTERN, parse_utc
from .models.tennis import UpcomingMatch, tennis_model

_TENNIS_SUBPERIOD_MARKERS = ("fh", "h1", "h2", "1h", "set", "ss", "st")

TENNIS_TOURS = ("WTA", "ATP")


def _tennis_history_before(data_root: str | Path, as_of_date: str) -> list[dict[str, Any]]:
    """Point-in-time WTA match history from ``data/processed/tennis/games.jsonl``.

    Tennis rows are player-vs-player (``winner``/``loser``/``surface``, no
    scores) -- structurally incompatible with ``features.base.FeatureStore``'s
    ``GameRecord``, which requires ``away_team``/``home_team``/``away_score``/
    ``home_score`` via direct dict subscript. Every tennis row raised
    ``KeyError`` there and was silently caught and skipped
    (``FeatureStore.load_games``'s broad ``except (KeyError, TypeError,
    ValueError): continue``), so ``games_before("tennis", ...)`` always
    returned an empty list -- meaning every match probability computed by
    this module defaulted to exactly 0.5 for every player, including
    well-known ones with hundreds of real matches on file, regardless of the
    combined-tournament tagging fix above. This reads the raw rows directly
    instead, using the same point-in-time cutoff ``games_before`` uses
    (midnight US-Eastern at the start of ``as_of_date``).
    """
    path = Path(data_root) / "processed" / "tennis" / "games.jsonl"
    if not path.exists():
        return []
    cutoff = datetime.combine(date.fromisoformat(as_of_date), time.min, tzinfo=EASTERN)
    history: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if parse_utc(str(row["event_start_utc"])) < cutoff:
                    history.append(row)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
    return history


def _words(value: str) -> list[str]:
    import unicodedata

    norm = unicodedata.normalize("NFKD", str(value)).encode("ASCII", "ignore").decode("utf-8")
    return re.findall(r"[a-z0-9]+", norm.casefold())


def _name_matches(player: str, text: str) -> bool:
    player_words = [word for word in _words(player) if len(word) >= 2]
    text_words = set(_words(text))
    if not player_words or not text_words:
        return False
    if " ".join(player_words) in " ".join(_words(text)):
        return True
    if all(word in text_words for word in player_words):
        return True
    # Surname matching with length guard
    return len(player_words) >= 2 and player_words[-1] in text_words and len(player_words[-1]) >= 4


def _is_tennis_subperiod_slug(slug: str) -> bool:
    """Return whether a market slug identifies a set/partial-match contract."""
    normalized = f"-{slug.casefold().strip('-')}-"
    return any(f"-{marker}-" in normalized for marker in _TENNIS_SUBPERIOD_MARKERS)


def _unsupported_tennis_market_reason(market_type: str, slug: str) -> str | None:
    if _is_tennis_subperiod_slug(slug):
        return (
            "UNSUPPORTED_TENNIS_SUBPERIOD_MARKET: exact set/period identity "
            "and result dimensions are unavailable"
        )
    if market_type in {"spread", "total"}:
        return "UNSUPPORTED_TENNIS_DERIVATIVE_PRICING: no validated game-score distribution is available"
    return None


def _select_one_tennis_line_per_market(
    contracts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Cap each match at one spread and one total, selected prospectively.

    Alternate lines are distinct contracts but strongly correlated exposure.
    Ranking uses only information known at decision time: expected return per
    unit risk for a binary contract (model_probability / executable_ask - 1),
    then raw edge and quote time as deterministic tie-breakers. Outcomes are
    deliberately absent, so historical winners can never influence selection.
    """
    grouped: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = {}
    passthrough: list[tuple[int, dict[str, Any]]] = []
    for index, contract in enumerate(contracts):
        market_type = str(contract.get("market_type") or "").casefold()
        if market_type not in {"spread", "total"}:
            passthrough.append((index, contract))
            continue
        key = (str(contract.get("event_id") or ""), market_type)
        grouped.setdefault(key, []).append((index, contract))

    chosen: list[tuple[int, dict[str, Any]]] = list(passthrough)
    suppressed: list[dict[str, str]] = []
    for (event_id, market_type), members in grouped.items():

        def rank(item: tuple[int, dict[str, Any]]) -> tuple[float, float, str, str]:
            _, row = item
            probability = float(row.get("model_probability") or 0.0)
            ask = float(row.get("executable_ask") or 0.0)
            expected_return = probability / ask - 1.0 if 0.0 < ask < 1.0 else float("-inf")
            edge = float(row.get("edge_vs_executable_ask") or probability - ask)
            return (
                expected_return,
                edge,
                str(row.get("observed_at_utc") or ""),
                str(row.get("market_slug") or ""),
            )

        survivor = max(members, key=rank)
        chosen.append(survivor)
        survivor_slug = str(survivor[1].get("market_slug") or "")
        for _, row in members:
            if row is survivor[1]:
                continue
            suppressed.append(
                {
                    "event_id": event_id,
                    "market_slug": str(row.get("market_slug") or ""),
                    "reason": "TENNIS_CORRELATED_LINE_SUPERSEDED",
                    "selected_market_slug": survivor_slug,
                    "market_type": market_type,
                }
            )

    chosen.sort(key=lambda item: item[0])
    return [row for _, row in chosen], suppressed


def _upcoming_singles_matches(scoreboard: dict[str, Any]) -> list[dict[str, Any]]:
    """Same singles/doubles split as the historical parser, for live events
    that have not necessarily finished (or even started) yet.

    Tour is derived per match from the competition's own ``type.slug``
    ("womens-singles" -> WTA, "mens-singles" -> ATP), exactly like
    ``completed_tennis_singles_matches`` -- never from which endpoint served
    it, since combined ATP+WTA tournaments return the SAME event with BOTH
    gender groupings from BOTH the ATP and WTA site-API paths. Tagging by
    endpoint would misattribute one tour's matches to the other.
    """
    matches: list[dict[str, Any]] = []
    for event in scoreboard.get("events", []):
        tournament = str(event.get("name", "unknown"))
        surface = _infer_tennis_surface(tournament)
        for grouping in event.get("groupings", []):
            for competition in grouping.get("competitions", []):
                slug = str(competition.get("type", {}).get("slug", ""))
                if "singles" not in slug:
                    continue
                tour = "WTA" if "womens" in slug else "ATP" if "mens" in slug else None
                if tour is None:
                    continue
                competitors = competition.get("competitors", [])
                if len(competitors) != 2:
                    continue
                by_side = {item.get("homeAway"): item for item in competitors}
                away, home = by_side.get("away"), by_side.get("home")
                if not away or not home:
                    continue
                away_name = (away.get("athlete") or {}).get("displayName")
                home_name = (home.get("athlete") or {}).get("displayName")
                if not away_name or not home_name:
                    continue
                start = competition.get("date") or event.get("date")
                matches.append(
                    {
                        "event_id": f"{event.get('id')}:{competition.get('id')}",
                        "event_start_utc": start,
                        "away_player": away_name,
                        "home_player": home_name,
                        "surface": surface,
                        "tour": tour,
                    }
                )
    return matches


def _latest_tennis_snapshots(
    data_root: str | Path,
    game_date: str,
    league: str,
) -> list[dict[str, Any]]:
    import json

    path = Path(data_root) / "odds" / "tennis" / game_date / "polymarket_snapshots.jsonl"
    if not path.exists():
        return []
    latest: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            slug = str(row.get("market_slug") or "")
            mtype = str(row.get("market_type") or "moneyline")
            if (
                mtype not in {"moneyline", "spread", "total"}
                or str(row.get("league") or "").upper() != league
                or not bool(row.get("timestamp_valid", False))
            ):
                continue
            canonical = json.dumps(row, sort_keys=True, separators=(",", ":"))
            snapshot_hash = hashlib.sha256(canonical.encode()).hexdigest()
            enriched = dict(row)
            enriched["_market_snapshot_hash"] = snapshot_hash
            enriched["_market_snapshot_archive_path"] = str(path.resolve())
            existing_record_id = row.get("market_snapshot_record_id") or row.get("snapshot_record_id")
            market_id = row.get("market_id")
            observed_at_utc = row.get("observed_at_utc")
            if existing_record_id:
                enriched["_market_snapshot_record_id"] = str(existing_record_id)
            elif market_id and observed_at_utc:
                enriched["_market_snapshot_record_id"] = f"{market_id}:{observed_at_utc}"
            else:
                enriched["_market_snapshot_record_id"] = snapshot_hash
            if slug not in latest or str(row.get("observed_at_utc") or "") > str(
                latest[slug].get("observed_at_utc") or ""
            ):
                latest[slug] = enriched
    return list(latest.values())


def build_tennis_slate(
    *,
    data_root: str | Path,
    game_date: str,
    client: Any,
    observed_at: datetime,
) -> dict[str, Any]:
    all_history = _tennis_history_before(data_root, game_date)

    # Combined ATP+WTA tournaments return the SAME event from both the ATP
    # and WTA site-API paths -- dedupe by event_id across both fetches
    # (mirrors ingest.py's historical-side handling of the same overlap).
    events_by_id: dict[str, dict[str, Any]] = {}
    for tour in TENNIS_TOURS:
        scoreboard = client.scoreboard(tour, game_date)
        for item in _upcoming_singles_matches(scoreboard):
            events_by_id[item["event_id"]] = item

    upcoming_by_tour: dict[str, list[UpcomingMatch]] = {tour: [] for tour in TENNIS_TOURS}
    skipped: list[dict[str, str]] = []
    for item in events_by_id.values():
        event_id = item["event_id"]
        try:
            start = parse_utc(str(item["event_start_utc"]))
            if start <= observed_at:
                raise ValueError("event_started")
            tour = str(item["tour"])
            upcoming_by_tour[tour].append(
                UpcomingMatch(
                    event_id=event_id,
                    event_start_utc=str(item["event_start_utc"]),
                    player_one=item["away_player"],
                    player_two=item["home_player"],
                    surface=str(item.get("surface") or "Hard"),
                    tour=tour,
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            skipped.append({"event_id": event_id, "reason": str(error)})

    model = tennis_model()
    predictions = []
    for tour in TENNIS_TOURS:
        tour_history = [game for game in all_history if str(game.get("league", "")).upper() == tour]
        predictions.extend(model.predict_games(tour_history, upcoming_by_tour[tour]))

    snapshots_by_tour = {tour: _latest_tennis_snapshots(data_root, game_date, tour) for tour in TENNIS_TOURS}
    priced: list[dict[str, Any]] = []
    unmatched: list[dict[str, str]] = []
    seen_contract_keys: set[str] = set()

    for prediction in predictions:
        start = parse_utc(prediction.event_start_utc)
        p_away = float(prediction.probabilities.get("away", 0.5))
        p_home = float(prediction.probabilities.get("home", 0.5))
        priced_before = len(priced)
        saw_unsupported_market = False

        for snapshot in snapshots_by_tour.get(prediction.league, []):
            try:
                snapshot_start = parse_utc(str(snapshot["event_start_utc"]))
                snapshot_at = parse_utc(str(snapshot["observed_at_utc"]))
            except (KeyError, TypeError, ValueError):
                continue

            long_desc = str((snapshot.get("long") or {}).get("description", ""))
            short_desc = str((snapshot.get("short") or {}).get("description", ""))
            event_title = str(snapshot.get("event_title", ""))

            title_matches = _name_matches(prediction.away_team, event_title) and _name_matches(
                prediction.home_team, event_title
            )
            away_is_long = _name_matches(prediction.away_team, long_desc) and _name_matches(
                prediction.home_team, short_desc
            )
            away_is_short = _name_matches(prediction.away_team, short_desc) and _name_matches(
                prediction.home_team, long_desc
            )

            if not (title_matches or away_is_long or away_is_short):
                continue

            # Same tournament / calendar day matching (within 24 hours)
            if abs((snapshot_start - start).total_seconds()) > 24 * 3600:
                continue
            if snapshot_at >= start:
                continue

            mtype = snapshot.get("market_type", "moneyline")
            slug = str(snapshot.get("market_slug", ""))
            contract_key = f"{prediction.event_id}:{slug}"
            if contract_key in seen_contract_keys:
                continue

            unsupported_reason = _unsupported_tennis_market_reason(str(mtype), slug)
            if unsupported_reason is not None:
                seen_contract_keys.add(contract_key)
                saw_unsupported_market = True
                skipped.append(
                    {
                        "event_id": prediction.event_id,
                        "market_slug": slug,
                        "reason": unsupported_reason,
                    }
                )
                continue

            if mtype == "moneyline":
                away_side_key = "long" if away_is_long else "short"
                home_side_key = "short" if away_side_key == "long" else "long"
                selection = "away" if p_away >= p_home else "home"
                side_key = away_side_key if selection == "away" else home_side_key
                side = snapshot.get(side_key) or {}
                ask = side.get("ask")
                if ask is None or not 0.01 <= float(ask) <= 0.99:
                    continue
                prob = p_away if selection == "away" else p_home
                seen_contract_keys.add(contract_key)
                priced.append(
                    {
                        "event_id": prediction.event_id,
                        "event_start_utc": prediction.event_start_utc,
                        "away_team": prediction.away_team,
                        "home_team": prediction.home_team,
                        "market_type": "moneyline",
                        "selection": selection,
                        "line": None,
                        "model_probability": round(prob, 4),
                        "model_uncertainty": prediction.uncertainty,
                        "model_version": prediction.model_version,
                        "feature_basis": prediction.feature_basis,
                        "rationale": prediction.rationale,
                        "market_slug": slug,
                        "executable_ask": float(ask),
                        "observed_at_utc": snapshot["observed_at_utc"],
                        "timestamp_valid": True,
                        "edge_vs_executable_ask": round(prob - float(ask), 6),
                        "market_snapshot_hash": snapshot["_market_snapshot_hash"],
                        "market_snapshot_archive_path": snapshot["_market_snapshot_archive_path"],
                        "market_snapshot_record_id": snapshot["_market_snapshot_record_id"],
                    }
                )

        if len(priced) == priced_before and not saw_unsupported_market:
            unmatched.append(
                {
                    "event_id": prediction.event_id,
                    "reason": "no snapshot matched",
                }
            )

    priced, correlated_line_skips = _select_one_tennis_line_per_market(priced)
    skipped.extend(correlated_line_skips)

    model_source = Path(__file__).with_name("models") / "tennis.py"
    code_hash = hashlib.sha256(model_source.read_bytes()).hexdigest()
    return {
        "sport": "tennis",
        "status": "research",
        "model_version": model.version,
        "model_code_hash": code_hash,
        "scheduled_games": sum(len(matches) for matches in upcoming_by_tour.values()),
        "priced_contracts": priced,
        "priced_count": len(priced),
        "unmatched": unmatched,
        "skipped": skipped,
        "note": (
            "Surface-Elo prices WTA & ATP full-match moneylines only. Tennis spread, "
            "total, set, and partial-match contracts fail closed until validated score-distribution "
            "pricing and exact result dimensions are available."
        ),
    }
