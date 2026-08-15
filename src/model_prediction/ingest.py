"""Local data ingestion: raw cache, normalized games, historical bootstrap.

Principles:
- Raw payloads under ``data/raw/{sport}/{date}/`` are immutable — an existing
  file is never overwritten.
- ``data/processed/{sport}/games.jsonl`` is append-only, deduplicated by
  event id.
- Everything is idempotent: re-running an ingest for a date is a no-op beyond
  filling gaps.
- Each operation is audit-logged.
"""

from __future__ import annotations

import hashlib
import json
import time as time_module
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx

from .audit import AuditLog
from .data_sources.espn import SPORT_LEAGUES, ESPNClient, parse_pregame_and_closing_markets
from .domain import eastern_today

SPORTS = tuple(SPORT_LEAGUES)

# Bumped when the normalized-row shape of completed_games /
# completed_tennis_singles_matches changes meaningfully — consumers can
# tell which parser version produced a normalized row.
PARSER_VERSION = "espn-scoreboard-1"

_NONTERMINAL_STATES = {"in", "pre"}


def _has_unfinished_events(scoreboard: dict[str, Any]) -> bool:
    """True if any event is still pre-game or in progress.

    A cache containing some finished AND some unfinished events is a partial
    snapshot captured mid-slate -- see the staleness comment in
    ingest_scores for the live case that motivated this (5 MLB games from
    2026-08-07 frozen at STATUS_IN_PROGRESS while 10 others were already
    final). Postponed/canceled events are terminal (state "post") and
    deliberately do not count.
    """
    for event in scoreboard.get("events", []):
        competition = (event.get("competitions") or [{}])[0]
        state = (competition.get("status") or {}).get("type", {}).get("state")
        if state in _NONTERMINAL_STATES:
            return True
    return False


class Ingestor:
    def __init__(
        self,
        data_root: str | Path,
        client: ESPNClient | None = None,
        audit: AuditLog | None = None,
        rate_limit_seconds: float = 0.6,
    ) -> None:
        self.data_root = Path(data_root)
        self.client = client or ESPNClient()
        self.audit = audit
        self.rate_limit_seconds = rate_limit_seconds

    # ----------------------------------------------------------------- paths

    def raw_dir(self, sport: str, game_date: str) -> Path:
        return self.data_root / "raw" / sport.lower() / game_date

    def processed_path(self, sport: str) -> Path:
        return self.data_root / "processed" / sport.lower() / "games.jsonl"

    def historical_path(self, sport: str) -> Path:
        return self.data_root / "historical" / f"{sport.lower()}_games_all.jsonl"

    # ---------------------------------------------------------------- ingest

    def ingest_scores(self, sport: str, game_date: str) -> dict[str, Any]:
        """Fetch, cache raw, and normalize one date for one sport."""
        sport_key = sport.lower()
        leagues = SPORT_LEAGUES.get(sport_key)
        if not leagues:
            raise ValueError(f"unknown sport: {sport}; expected one of {sorted(SPORT_LEAGUES)}")
        raw_dir = self.raw_dir(sport_key, game_date)
        new_games: list[dict[str, Any]] = []
        fetched = skipped = errors = 0
        is_past_date = date.fromisoformat(game_date) < eastern_today()
        for league in leagues:
            raw_path = raw_dir / f"scores_{league.lower()}.json"
            cached_payload = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.exists() else None
            # "Immutable" (module docstring) means a genuinely FINAL raw
            # payload is never overwritten -- it does not mean a snapshot
            # captured mid-day, while games were still STATUS_SCHEDULED/
            # IN_PROGRESS, gets treated as the permanent record forever.
            # Two confirmed live cases:
            #   1. 2026-07-26: cache written at 14:29 (before that
            #      evening's games) froze every event at STATUS_SCHEDULED,
            #      and every ingest since silently skipped re-fetching it.
            #   2. 2026-08-07: cache written mid-slate froze 5 games at
            #      STATUS_IN_PROGRESS while 10 others were already final.
            #      The old check only flagged a cache with ZERO completed
            #      events, so a partially-completed snapshot was trusted
            #      forever and those 5 games never reached processed/
            #      historical (and the Elo/trend features that read it).
            # A past-date cache is therefore re-fetched when it holds any
            # unfinished event (state "pre"/"in"); postponed/canceled
            # events are terminal and never trigger a refetch.
            #
            # Must use the SAME completion parser as the real ingest below
            # (completed_tennis_singles_matches for tennis, completed_games
            # otherwise) -- tennis events nest matches under `groupings`, a
            # completely different shape from `completed_games`'s flat
            # `competitions` list, so that parser always returns zero
            # matches for tennis regardless of true completion state. Using
            # it here first flagged ~1748 tennis dates as "stale" that were
            # actually fine -- a false positive from the wrong parser, not
            # real staleness like the MLB/WNBA/soccer case above. The
            # unfinished-event scan is likewise sport-aware: tennis payloads
            # are tournaments, not per-game events, so it is skipped there.
            completed_events = (
                ESPNClient.completed_tennis_singles_matches(cached_payload)
                if sport_key == "tennis" and cached_payload is not None
                else ESPNClient.completed_games(cached_payload) if cached_payload is not None else []
            )
            cache_is_stale = (
                cached_payload is not None
                and is_past_date
                and bool(cached_payload.get("events"))
                and (
                    not completed_events
                    or (sport_key != "tennis" and _has_unfinished_events(cached_payload))
                )
            )
            if cached_payload is not None and not cache_is_stale:
                skipped += 1
                payload = cached_payload
            else:
                try:
                    payload = self.client.scoreboard(league, game_date)
                except (httpx.HTTPError, ValueError) as error:
                    errors += 1
                    if self.audit:
                        self.audit.append(
                            "ingest_error",
                            f"{sport_key}:{league}:{game_date}",
                            {"error": type(error).__name__, "detail": str(error)[:200]},
                        )
                    continue
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                raw_path.write_text(
                    json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
                    encoding="utf-8",
                )
                fetched += 1
                time_module.sleep(self.rate_limit_seconds)
            # Every normalized row is traceable to the exact raw snapshot
            # it came from (content-addressed provenance, consolidation
            # item 7): source, raw payload hash, and parser version.
            raw_hash = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if sport_key == "tennis":
                # Combined ATP+WTA tournaments return the same event (with
                # both gender groupings) from both the ATP and WTA site-API
                # paths -- completed_tennis_singles_matches derives the real
                # per-match tour itself; tagging by which endpoint served it
                # would misattribute WTA matches to ATP (see that function's
                # docstring), so `league` is deliberately not overwritten here.
                fresh = ESPNClient.completed_tennis_singles_matches(payload)
            else:
                fresh = ESPNClient.completed_games(payload)
                for game in fresh:
                    game["league"] = league
            for game in fresh:
                game["raw_source"] = f"espn:{league}:{game_date}"
                game["raw_hash"] = raw_hash
                game["parser_version"] = PARSER_VERSION
            new_games.extend(fresh)
        appended = self._append_games(sport_key, new_games)
        result = {
            "sport": sport_key,
            "date": game_date,
            "leagues": len(leagues),
            "fetched": fetched,
            "cached": skipped,
            "errors": errors,
            "completed_games_seen": len(new_games),
            "new_games_appended": appended,
        }
        if self.audit:
            self.audit.append("ingest_scores", f"{sport_key}:{game_date}", result)
        return result

    def _existing_event_ids(self, path: Path) -> set[str]:
        if not path.exists():
            return set()
        ids = set()
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    try:
                        ids.add(str(json.loads(line).get("event_id")))
                    except json.JSONDecodeError:
                        continue
        return ids

    def _append_games(self, sport: str, games: Iterable[dict[str, Any]]) -> int:
        processed = self.processed_path(sport)
        historical = self.historical_path(sport)
        processed.parent.mkdir(parents=True, exist_ok=True)
        historical.parent.mkdir(parents=True, exist_ok=True)
        existing = self._existing_event_ids(processed)
        appended = 0
        with (
            processed.open("a", encoding="utf-8") as processed_handle,
            historical.open("a", encoding="utf-8") as historical_handle,
        ):
            for game in games:
                key = str(game.get("event_id"))
                if key in existing:
                    continue
                line = json.dumps(game, sort_keys=True, separators=(",", ":")) + "\n"
                processed_handle.write(line)
                historical_handle.write(line)
                existing.add(key)
                appended += 1
        return appended

    # ------------------------------------------------------------- bootstrap

    def bootstrap(self, sport: str, from_date: str, to_date: str | None = None) -> dict[str, Any]:
        """Idempotent, rate-limited historical backfill from ESPN."""
        start = date.fromisoformat(from_date)
        end = date.fromisoformat(to_date) if to_date else eastern_today()
        if start > end:
            raise ValueError("from date must not be after to date")
        days = 0
        totals = {"fetched": 0, "cached": 0, "errors": 0, "new_games_appended": 0}
        cursor = start
        while cursor <= end:
            result = self.ingest_scores(sport, cursor.isoformat())
            for key in totals:
                totals[key] += int(result.get(key, 0))
            days += 1
            cursor += timedelta(days=1)
        summary = {"sport": sport.lower(), "from": from_date, "to": end.isoformat(), "days": days, **totals}
        if self.audit:
            self.audit.append("bootstrap_completed", f"{sport.lower()}:{from_date}", summary)
        return summary

    def reconstruct_mlb_markets(
        self,
        from_date: str,
        to_date: str,
        output_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Cache historical ESPN summaries and extract diagnostic opening lines.

        ESPN does not guarantee that a postgame-retrieved summary is an
        immutable point-in-time archive. Every extracted row is therefore
        hard-labeled ``timestamp_valid=false`` and can never qualify a model
        for promotion. The reconstruction is useful for debugging selection
        and grading—not for claiming executable historical edge.
        """
        start, end = date.fromisoformat(from_date), date.fromisoformat(to_date)
        if start > end:
            raise ValueError("from date must not be after to date")
        destination = (
            Path(output_path)
            if output_path
            else (self.data_root / "historical" / "mlb_market_lines_reconstructed.jsonl")
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        existing = self._existing_event_ids(destination)
        rows: list[dict[str, Any]] = []
        fetched = cached = missing = errors = 0
        cursor = start
        while cursor <= end:
            game_date = cursor.isoformat()
            scoreboard_path = self.raw_dir("mlb", game_date) / "scores_mlb.json"
            if scoreboard_path.exists():
                scoreboard = json.loads(scoreboard_path.read_text(encoding="utf-8"))
            else:
                try:
                    scoreboard = self.client.scoreboard("MLB", game_date)
                except (httpx.HTTPError, ValueError):
                    errors += 1
                    cursor += timedelta(days=1)
                    continue
            for event in scoreboard.get("events", []):
                event_id = str(event.get("id", ""))
                if not event_id or event_id in existing:
                    continue
                competition = (event.get("competitions") or [{}])[0]
                if not (
                    competition.get("status", {}).get("type", {}) or event.get("status", {}).get("type", {})
                ).get("completed"):
                    continue
                summary_path = self.raw_dir("mlb", game_date) / "summaries" / f"{event_id}.json"
                if summary_path.exists():
                    summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
                    cached += 1
                else:
                    try:
                        summary_payload = self.client.summary("MLB", event_id)
                    except (httpx.HTTPError, ValueError):
                        errors += 1
                        continue
                    summary_path.parent.mkdir(parents=True, exist_ok=True)
                    summary_path.write_text(
                        json.dumps(summary_payload, sort_keys=True, separators=(",", ":")) + "\n",
                        encoding="utf-8",
                    )
                    fetched += 1
                    time_module.sleep(self.rate_limit_seconds)
                parsed = parse_pregame_and_closing_markets(summary_payload)
                normalized = _historical_market_row(event, parsed)
                if normalized is None:
                    missing += 1
                    continue
                rows.append(normalized)
                existing.add(event_id)
            cursor += timedelta(days=1)
        with destination.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        result = {
            "from": from_date,
            "to": to_date,
            "fetched_summaries": fetched,
            "cached_summaries": cached,
            "market_rows_appended": len(rows),
            "market_rows_missing": missing,
            "errors": errors,
            "output": str(destination),
            "timestamp_valid": False,
            "usage": "diagnostic_only",
        }
        if self.audit:
            self.audit.append("mlb_markets_reconstructed", f"{from_date}:{to_date}", result)
        return result

    # -------------------------------------------------------------- entities

    def bootstrap_entities(self, league: str, registry_path: str | Path) -> dict[str, Any]:
        """Merge ESPN's team list for a league into the entity registry JSON."""
        payload = self.client.teams(league)
        teams = []
        for sport_entry in payload.get("sports", []):
            for league_entry in sport_entry.get("leagues", []):
                for item in league_entry.get("teams", []):
                    team = item.get("team", {})
                    if team.get("displayName"):
                        teams.append(team)
        path = Path(registry_path)
        registry = (
            json.loads(path.read_text(encoding="utf-8"))
            if path.exists()
            else {"schema_version": "1", "teams": []}
        )
        entries = cast(list[dict[str, Any]], registry.setdefault("teams", []))
        known = {
            (str(entry.get("league", "")).upper(), str(entry.get("canonical_name", "")).casefold())
            for entry in entries
        }
        known_ids = {entry.get("canonical_team_id") for entry in entries}
        added = 0
        for team in sorted(teams, key=lambda item: item["displayName"]):
            display = str(team["displayName"])
            if (league.upper(), display.casefold()) in known:
                continue
            abbreviation = str(team.get("abbreviation", "")).upper()
            canonical_id = f"{league.lower()}-{(abbreviation or str(team.get('id', ''))).lower()}"
            if canonical_id in known_ids:
                canonical_id = f"{league.lower()}-{team.get('id')}"
            aliases = [
                {"source_name": value}
                for value in sorted(
                    {
                        item
                        for item in (
                            team.get("shortDisplayName"),
                            team.get("name"),
                            team.get("location"),
                        )
                        if item and item != display
                    }
                )
            ]
            entries.append(
                {
                    "canonical_team_id": canonical_id,
                    "league": league.upper(),
                    "canonical_name": display,
                    "abbreviation": abbreviation,
                    "aliases": aliases,
                }
            )
            known.add((league.upper(), display.casefold()))
            known_ids.add(canonical_id)
            added += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        result = {"league": league.upper(), "teams_seen": len(teams), "teams_added": added}
        if self.audit:
            self.audit.append("entities_bootstrapped", league.upper(), result)
        return result


def _historical_market_row(event: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any] | None:
    if not parsed:
        return None
    markets: dict[str, dict[str, dict[str, Any]]] = {}
    definitions = {
        "moneyline": ("away", "home"),
        "spread": ("away", "home"),
        "total": ("over", "under"),
    }
    for market, sides in definitions.items():
        normalized: dict[str, dict[str, Any]] = {}
        for side in sides:
            values = parsed.get(market, {}).get(side, {})
            odds = values.get("decision_odds")
            line = None if market == "moneyline" else values.get("decision_line")
            if odds is None or (market != "moneyline" and line is None):
                normalized = {}
                break
            normalized[side] = {
                "line": line,
                "american_odds": odds,
                "closing_line": values.get("closing_line"),
                "closing_american_odds": values.get("closing_odds"),
            }
        if normalized:
            markets[market] = normalized
    if not markets:
        return None
    return {
        "event_id": str(event.get("id")),
        "event_start_utc": event.get("date"),
        "observed_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "timestamp_valid": False,
        "source": "espn_postgame_reconstructed_open",
        "provider": parsed.get("provider", "unknown"),
        "markets": markets,
    }
