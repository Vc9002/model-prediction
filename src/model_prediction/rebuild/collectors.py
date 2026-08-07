"""MLB source collector — pybaseball + Open-Meteo + Polymarket US.

Every collector is restartable, idempotent, rate-limited, and schema-tested.
Raw responses are immutable. Normalized tables carry full provenance columns.
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from .identity import (
    IdentityRegistry,
    resolve_espn_roster_player_id,
    resolve_espn_scoreboard_event_id,
    resolve_espn_scoreboard_team_ids,
    resolve_espn_scoreboard_venue_id,
    resolve_polymarket_team_id,
)
from .schemas import MARKET_SNAPSHOT_CONTRACT, ROSTER_CONTRACT, SCOREBOARD_CONTRACT
from .storage import MarketStore, NormalizedStore, RawStore, provenance_row, utc_now

DEFAULT_RATE_LIMIT = 0.6  # seconds between calls


def _competitor_identity_obj(competitor: dict[str, Any]) -> dict[str, Any]:
    """Normalize an ESPN competitor entry to {"id", "displayName"}
    regardless of whether it's team-shaped (competitor["team"]) or
    athlete-shaped (individual-sport competitor["athlete"], with the
    ESPN person ID on the competitor itself rather than inside
    "athlete") -- so resolve_espn_scoreboard_team_ids() can treat both
    the same way."""
    team = competitor.get("team")
    if team:
        return {"id": team.get("id"), "displayName": team.get("displayName", "")}
    athlete = competitor.get("athlete")
    if athlete:
        return {"id": competitor.get("id"), "displayName": athlete.get("displayName", "")}
    return {"id": None, "displayName": ""}


def _collect_espn_roster(collector: Any, sport: str, league: str, team_id: str, season: int) -> dict[str, Any]:
    """Real ESPN roster collection + canonical player identity resolution
    (Task 3: player identity outside MLB), shared by every non-MLB
    collector -- MLB's player identity comes from pybaseball's player
    register instead (resolve_mlbam_player_id()), which has no
    roster-endpoint equivalent needed here. Duck-types on `collector`
    (needs .raw/.norm/.identity/.meta/._throttle -- every real collector
    class in this module already has that exact shape) instead of a
    shared base class, matching this module's existing convention of
    small per-sport classes over one large shared implementation.

    Real athlete-id shape verified against a real cached ESPN roster
    payload (data/availability/wnba/espn_rosters/): a real, stable
    numeric `id` alongside `displayName` -- same shape
    resolve_espn_roster_player_id() expects."""
    source = "espn_public"
    record_id = f"{sport}_roster_{team_id}_{season}"
    collector._throttle()
    try:
        from model_prediction.data_sources.espn import ESPNClient
    except ImportError:
        return {"status": "no_espn_client", "team_id": team_id}

    client = ESPNClient()
    payload = client.roster(league, team_id, season)
    snapshot_hash = collector.raw.write(source, str(season), record_id, payload).snapshot_hash
    collector.meta.update_source_health(source, "active")

    observed_at = utc_now().isoformat()
    # Real team canonical id, if this team was already resolved via
    # scoreboard collection (resolve_espn_scoreboard_team_ids()'s same
    # sport-namespaced source_id) -- honestly None, not fabricated, when
    # roster collection runs before/without scoreboard collection for
    # this team.
    existing_team = collector.identity.resolve(f"{source}:{sport}", str(team_id))
    team_canonical_id = existing_team.entity_id if existing_team else None

    rows: list[dict[str, Any]] = []
    for athlete in payload.get("athletes", []):
        player_canonical_id = resolve_espn_roster_player_id(
            collector.identity, sport, source, athlete, team_canonical_id, observed_at,
        )
        player_id = str(athlete.get("id", ""))
        if not player_id:
            continue
        rows.append({
            **provenance_row(
                source=source, source_record_id=f"{team_id}_{player_id}",
                source_version="espn_public_v1", observed_at_utc=observed_at,
                effective_at_utc=observed_at, event_start_utc="",
                raw_snapshot_hash=snapshot_hash,
            ),
            "team_id": str(team_id),
            "player_id": player_id,
            "player_name": athlete.get("displayName", ""),
            "player_canonical_id": player_canonical_id,
            "team_canonical_id": team_canonical_id,
            "position": (athlete.get("position") or {}).get("abbreviation"),
            "jersey": athlete.get("jersey"),
            "season": season,
        })

    if rows:
        collector.norm.write(
            sport, "roster", pl.DataFrame(rows),
            primary_key=["team_id", "player_id"], contract=ROSTER_CONTRACT,
        )
        collector.meta.audit_event("collect_espn_roster", {"sport": sport, "team_id": team_id, "players": len(rows)})
        return {"status": "ok", "sport": sport, "team_id": team_id, "players": len(rows)}
    return {"status": "no_players", "sport": sport, "team_id": team_id}


class MLBCollector:
    """Collect MLB data from pybaseball, Open-Meteo, and Polymarket.

    Usage:
        collector = MLBCollector(data_root, meta)
        collector.collect_date("2026-08-04")
    """

    def __init__(
        self,
        data_root: str | Path,
        meta: Any,  # MetadataDB — injected for loose coupling
        rate_limit: float = DEFAULT_RATE_LIMIT,
    ) -> None:
        self.root = Path(data_root)
        self.meta = meta
        self.rate_limit = rate_limit
        self.raw = RawStore(self.root / "raw")
        self.norm = NormalizedStore(self.root / "normalized")
        self.markets = MarketStore(self.root / "markets")
        self.identity = IdentityRegistry(meta)
        self._last_call = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_call = time.monotonic()

    # ── ESPN Scoreboard (existing pipeline, adapted to medallion) ───────

    def collect_espn_scoreboard(self, game_date: str) -> dict[str, Any]:
        """Fetch ESPN MLB scoreboard for a date. Caches raw, returns normalized games."""
        source = "espn_public"
        record_id = f"mlb_scoreboard_{game_date}"
        self._throttle()

        try:
            from model_prediction.data_sources.espn import ESPNMLBClient
        except ImportError:
            return {"status": "no_espn_client", "date": game_date}

        client = ESPNMLBClient()
        payload = client.scoreboard(game_date)
        snapshot_hash = self.raw.write(source, game_date, record_id, payload).snapshot_hash
        self.meta.update_source_health(source, "active")

        events = payload.get("events", [])
        games: list[dict[str, Any]] = []
        for event in events:
            competitions = event.get("competitions", [{}])
            comp = competitions[0] if competitions else {}
            competitors = comp.get("competitors", [])
            away = home = {}
            for c in competitors:
                if c.get("homeAway") == "away":
                    away = c
                else:
                    home = c
            away_team_obj = away.get("team", {}) or {}
            home_team_obj = home.get("team", {}) or {}
            observed_at = utc_now().isoformat()

            # Real canonical identity, resolved from ESPN's own stable
            # numeric team ID (team["id"]) -- FOUNDATION_COMPLETION.md
            # Phase 4. Additive: home_team/away_team display-name columns
            # are unchanged so existing consumers (mlb_features.py's
            # ESPN_TO_STATCAST_ABBREV, mlb_market_matching.py's name
            # comparison) keep working exactly as before.
            home_canonical_id, away_canonical_id = resolve_espn_scoreboard_team_ids(
                self.identity, "mlb", source, home_team_obj, away_team_obj, observed_at,
            )
            venue_obj = comp.get("venue", {}) or {}
            venue_name = venue_obj.get("fullName", "")
            event_canonical_id = resolve_espn_scoreboard_event_id(
                self.identity, "mlb", source, str(event.get("id", "")),
                home_team_obj, away_team_obj, home_canonical_id, away_canonical_id,
                event.get("date", ""), observed_at, venue_name,
            )
            venue_canonical_id = resolve_espn_scoreboard_venue_id(
                self.identity, "mlb", source, venue_obj, observed_at,
            )

            games.append({
                **provenance_row(
                    source=source,
                    source_record_id=str(event.get("id", "")),
                    source_version="espn_public_v1",
                    observed_at_utc=observed_at,
                    effective_at_utc=event.get("date", ""),
                    event_start_utc=event.get("date", ""),
                    raw_snapshot_hash=snapshot_hash,
                ),
                "event_id": str(event.get("id", "")),
                "away_team": away_team_obj.get("displayName", ""),
                "home_team": home_team_obj.get("displayName", ""),
                "away_team_canonical_id": away_canonical_id,
                "home_team_canonical_id": home_canonical_id,
                "event_canonical_id": event_canonical_id,
                "venue_canonical_id": venue_canonical_id,
                "away_score": int(away.get("score", 0) or 0),
                "home_score": int(home.get("score", 0) or 0),
                "status": str(comp.get("status", {}).get("type", {}).get("name", "")),
                "venue": venue_name,
            })

        if games:
            df = pl.DataFrame(games)
            self.norm.write("mlb", "scoreboard", df, primary_key=["event_id"], contract=SCOREBOARD_CONTRACT)
            self.meta.audit_event("collect_espn_scoreboard", {"date": game_date, "games": len(games)})
            return {"status": "ok", "date": game_date, "games": len(games)}

        return {"status": "no_games", "date": game_date}

    # ── pybaseball (Statcast, Savant, FanGraphs) ────────────────────────

    def collect_pybaseball(self, game_date: str, *, statcast: bool = True, schedules: bool = True) -> dict[str, Any]:
        """Collect from pybaseball for a date. Requires pybaseball installed."""
        source = "pybaseball"
        results: dict[str, Any] = {"date": game_date, "collected": []}

        try:
            import pybaseball
        except ImportError:
            self.meta.update_source_health(source, "degraded", "pybaseball not installed")
            return {"status": "pybaseball_unavailable", "date": game_date}

        # Statcast pitch-level data
        if statcast:
            try:
                self._throttle()
                payload = pybaseball.statcast(game_date, game_date)
                record_id = f"statcast_{game_date}"
                if payload is not None and len(payload) > 0:
                    data = payload.to_dict(orient="records") if hasattr(payload, "to_dict") else payload
                    # Store the actual pitch-level data, not just a row count
                    snapshot_hash = self.raw.write(source, game_date, record_id, data).snapshot_hash
                    self.meta.update_source_health(source, "active")
                    results["collected"].append("statcast")
                    results["statcast_rows"] = len(data)
                    results["statcast_hash"] = snapshot_hash
            except Exception as e:  # noqa: BLE001 -- external I/O (HTTP/parsing); error captured and reported via status/health, not swallowed
                self.meta.update_source_health(source, "degraded", str(e)[:200])

        # Schedule
        if schedules:
            try:
                self._throttle()
                payload = pybaseball.schedule_and_record(int(game_date[:4]), "MLB")
                record_id = f"schedule_{game_date}"
                if payload is not None and len(payload) > 0:
                    data = payload.to_dict(orient="records") if hasattr(payload, "to_dict") else payload
                    self.raw.write(source, game_date, record_id, data)
                    results["collected"].append("schedule")
                    results["schedule_rows"] = len(data)
            except Exception as e:  # noqa: BLE001 -- external I/O (HTTP/parsing); error captured and reported via status/health, not swallowed
                self.meta.update_source_health(source, "degraded", str(e)[:200])

        results["status"] = "ok" if results["collected"] else "no_data"
        self.meta.audit_event("collect_pybaseball", results)
        return results

    # ── Open-Meteo Archived Forecasts ───────────────────────────────────

    def collect_weather_forecast(
        self, game_date: str, latitude: float, longitude: float, venue_id: str,
    ) -> dict[str, Any]:
        """Fetch weather for a venue on a game date, with correct point-in-time semantics.

        For today-or-future game_date, hits Open-Meteo's live Forecast API —
        whatever is captured right now genuinely *is* the forecast as of now
        (observed_at_utc = utc_now()), so there is no leak. For a past
        game_date (backfill), the *live* forecast for that date no longer
        exists, so this instead calls the Historical Forecast API, which
        Open-Meteo documents as a stitched continuous series of past model
        runs — a real, disclosed approximation of "the forecast at the time,"
        not a single exact run, but far closer to train-serving parity than
        realized/reanalysis weather would be.

        Previously this called the Archive API (`archive-api.open-meteo.com`),
        which is ERA5 *reanalysis* — realized weather, not a forecast, and
        returns 400 for any date without several days of processing lag. That
        was a real bug (see outputs/rebuild/takeover_status.md Checkpoint 4),
        not just today's date failing to resolve: a model trained on realized
        weather but served with only a live forecast at inference time would
        have a genuine train-serving mismatch, independent of the 400s.

        Args:
            game_date: ISO date string
            latitude, longitude: venue coordinates
            venue_id: identifier for this venue
        """
        source = "open_meteo"
        record_id = f"weather_{venue_id}_{game_date}"
        self._throttle()

        import httpx
        is_future_or_today = date.fromisoformat(game_date) >= utc_now().date()
        url = (
            "https://api.open-meteo.com/v1/forecast"
            if is_future_or_today
            else "https://historical-forecast-api.open-meteo.com/v1/forecast"
        )
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": game_date,
            "end_date": game_date,
            "hourly": ["temperature_2m", "relative_humidity_2m", "dew_point_2m",
                        "precipitation", "surface_pressure", "wind_speed_10m",
                        "wind_direction_10m", "weather_code"],
            # Real bug fixed here (see outputs/rebuild/takeover_status.md
            # Task 3): this previously requested "America/New_York" for
            # every venue regardless of its real location, so
            # hourly.time's local-time labels were wrong for any
            # non-Eastern venue (verified live: a real Chase Field
            # response still carried utc_offset_seconds=-14400, Eastern's
            # offset, not Arizona's real -25200) -- any downstream
            # first-pitch-hour lookup keyed off those mislabeled local
            # timestamps would silently pick the wrong hour's weather.
            # Requesting UTC directly makes hourly.time genuinely
            # comparable to event_start_utc with no venue-timezone
            # dependency at all.
            "timezone": "UTC",
            "models": "best_match",
        }
        try:
            resp = httpx.get(url, params=params, timeout=30)
            resp.raise_for_status()
            forecast_data = resp.json()
            observed_at = utc_now().isoformat()
            endpoint = "live_forecast" if is_future_or_today else "historical_forecast_stitched"
            # Real gap fixed here: RawStore.list_snapshots() cannot recover
            # a snapshot's real observed_at_utc from disk after the fact
            # (it's only ever known transiently, at write time, to the
            # caller that passed it in) -- so without embedding it inside
            # the immutable content itself, no later point-in-time
            # selection over multiple weather snapshots for the same
            # venue/date would be possible at all. Wrapping the real
            # Open-Meteo response in a small self-describing envelope
            # (not altering the real forecast_data itself) is the minimal
            # fix; a full RawStore provenance-recovery fix is a separate,
            # broader storage-layer gap, not this collector's to solve.
            payload = {
                "observed_at_utc": observed_at,
                "endpoint": endpoint,
                "forecast_data": forecast_data,
            }
            snapshot_hash = self.raw.write(source, game_date, record_id, payload, observed_at=observed_at).snapshot_hash
            self.meta.update_source_health(source, "active")
            return {
                "status": "ok",
                "venue_id": venue_id,
                "hash": snapshot_hash,
                "endpoint": endpoint,
            }
        except Exception as e:  # noqa: BLE001 -- external I/O (HTTP/parsing); error captured and reported via status/health, not swallowed
            self.meta.update_source_health(source, "degraded", str(e)[:200])
            return {"status": "error", "venue_id": venue_id, "error": str(e)[:200]}

    # ── Polymarket US Order Book / BBO ──────────────────────────────────

    def collect_polymarket_books(self, game_date: str) -> dict[str, Any]:
        """Collect Polymarket US MLB markets for a date.

        Captures order books (bids, asks, depths) not just midpoint/BBO.
        """
        source = "polymarket_us"
        record_id = f"mlb_markets_{game_date}"
        self._throttle()

        try:
            from model_prediction.data_sources.polymarket_us import PolymarketUSClient
        except ImportError:
            return {"status": "no_client", "date": game_date}

        try:
            client = PolymarketUSClient()
            mlb_events = client.slate("MLB", date.fromisoformat(game_date))
            books: list[dict[str, Any]] = []
            skipped_events = 0
            for event in mlb_events:
                try:
                    # _normalize_event's real output shape (polymarket_us.py):
                    # event["event_id"] (not "id"), event["markets"][i]
                    # ["market_id"]/["market_type"] (not "id"/"type"), and
                    # per-side executable prices live in market["sides"] (a
                    # 2-entry list with "price_probability"/"selection"/
                    # "team"), not flat market["bestBid"]/["bestAsk"] fields
                    # that don't exist on this object at all. Every one of
                    # those wrong keys previously resolved to None/"" for
                    # every row -- silently, no exception, "status": "ok"
                    # with 132 books that were really 132 empty shells (see
                    # outputs/rebuild/takeover_status.md Checkpoint 8).
                    event_id = str(event.get("event_id", ""))
                    for market in event.get("markets", []):
                        market_id = str(market.get("market_id", ""))
                        market_type = market.get("market_type", "")
                        market_slug = market.get("market_slug", "")
                        line = market.get("line")
                        for side in market.get("sides", []):
                            books.append({
                                **provenance_row(
                                    source=source,
                                    source_record_id=f"{event_id}_{market_id}_{side.get('side_id', '')}",
                                    source_version="polymarket_us_v1",
                                    observed_at_utc=utc_now().isoformat(),
                                    effective_at_utc=utc_now().isoformat(),
                                    event_start_utc=event.get("event_start_utc", ""),
                                ),
                                "event_id": event_id,
                                "market_id": market_id,
                                "market_type": market_type,
                                # market_slug carries the only reliable signal for
                                # period ("-f5-" = first 5 innings vs full game) --
                                # a real, distinct market can share the exact same
                                # market_type/line as a full-game market (e.g. two
                                # different real "total > 6.5" markets, one full
                                # game at 65c, one F5 at 25c, confirmed live). A
                                # caller comparing a full-game model probability
                                # against an F5 price is comparing incompatible
                                # bets -- see outputs/rebuild/takeover_status.md
                                # Checkpoint 9.
                                "market_slug": market_slug,
                                "is_first_five_innings": "-f5-" in market_slug,
                                "team_or_side": side.get("selection", ""),
                                "team": side.get("team"),
                                "team_canonical_id": resolve_polymarket_team_id(self.identity, "mlb", side.get("team")),
                                "line": side.get("line", line),
                                "executable_price": side.get("price_probability"),
                                "decimal_odds": side.get("decimal_odds"),
                                "american_odds": side.get("american_odds"),
                                # PolymarketUSClient's _normalize_event doesn't
                                # expose order-book depth/size, only an
                                # indicative side price -- real, disclosed gap,
                                # not fabricated. A caller must not treat this
                                # as a depth-checked executable quote.
                                "available_depth": None,
                            })
                except Exception as e:  # noqa: BLE001 -- external I/O (HTTP/parsing); error captured and reported via status/health, not swallowed
                    skipped_events += 1
                    self.meta.update_source_health(source, "active", f"skipped malformed event: {str(e)[:150]}")
                    continue

            if books:
                payload = {"events": len(mlb_events), "books": len(books)}
                self.raw.write(source, game_date, record_id, payload)
                df = pl.DataFrame(books)
                self.markets.write_books("mlb", game_date, df, contract=MARKET_SNAPSHOT_CONTRACT)
                self.meta.update_source_health(source, "active")
                self.meta.audit_event(
                    "collect_polymarket_books",
                    {"date": game_date, "books": len(books), "skipped_events": skipped_events},
                )
                return {"status": "ok", "date": game_date, "books": len(books), "skipped_events": skipped_events}

            return {"status": "no_mlb_markets", "date": game_date}

        except Exception as e:  # noqa: BLE001 -- external I/O (HTTP/parsing); error captured and reported via status/health, not swallowed
            self.meta.update_source_health(source, "degraded", str(e)[:200])
            return {"status": "error", "date": game_date, "error": str(e)[:200]}

    # ── Full date collection ───────────────────────────────────────────

    def collect_date(self, game_date: str) -> dict[str, Any]:
        """Run all MLB collectors for a date. Idempotent — safe to re-run."""
        results: dict[str, Any] = {"date": game_date}

        results["espn"] = self.collect_espn_scoreboard(game_date)
        results["pybaseball"] = self.collect_pybaseball(game_date)
        results["polymarket"] = self.collect_polymarket_books(game_date)

        # Weather needs venue coordinates — collect per-game after ESPN
        espn_status = results["espn"].get("status", "")
        if espn_status == "ok":
            venues = self._venues_for_date(game_date)
            weather_results: list[dict[str, Any]] = []
            for v in venues[:30]:  # safety cap
                weather_results.append(
                    self.collect_weather_forecast(game_date, v["lat"], v["lon"], v["id"])
                )
            results["weather"] = {"count": len(weather_results), "results": weather_results}

        all_ok = all(
            r.get("status") in ("ok", "no_games", "no_data", "no_mlb_markets")
            for r in [results["espn"], results.get("pybaseball", {}), results.get("polymarket", {})]
        )
        results["status"] = "ok" if all_ok else "partial_failure"
        return results

    def _venues_for_date(self, game_date: str) -> list[dict[str, Any]]:
        """Extract venue coordinates from the normalized scoreboard table."""
        try:
            path = self.norm.path("mlb", "scoreboard")
            if not path.exists():
                return []
            df = pl.read_parquet(str(path))
            venue_df = df.filter(
                pl.col("event_start_utc").str.contains(game_date)
            ).select(["venue"]).unique()
            # Default coordinates for known ballparks
            KNOWN_PARKS: dict[str, tuple[float, float]] = {
                "Yankee Stadium": (40.8296, -73.9262),
                "Fenway Park": (42.3467, -71.0972),
                "Dodger Stadium": (34.0739, -118.2400),
                "Wrigley Field": (41.9484, -87.6553),
                "Oracle Park": (37.7786, -122.3893),
                "Truist Park": (33.8908, -84.4678),
                "Citizens Bank Park": (39.9061, -75.1665),
                "Busch Stadium": (38.6226, -90.1928),
                "Petco Park": (32.7073, -117.1569),
                "T-Mobile Park": (47.5914, -122.3325),
                "Minute Maid Park": (29.7571, -95.3554),
                "Globe Life Field": (32.7473, -97.0845),
                "Coors Field": (39.7562, -104.9942),
                "Chase Field": (33.4455, -112.0667),
                "PNC Park": (40.4469, -80.0057),
                "Great American Ball Park": (39.0979, -84.5067),
                "American Family Field": (43.0280, -87.9712),
                "Target Field": (44.9817, -93.2776),
                "Progressive Field": (41.4962, -81.6852),
                "Comerica Park": (42.3390, -83.0485),
                "Kauffman Stadium": (39.0516, -94.4803),
                "Guaranteed Rate Field": (41.8300, -87.6339),
                "Angel Stadium": (33.8003, -117.8827),
                "RingCentral Coliseum": (37.7516, -122.2005),
                "Tropicana Field": (27.7678, -82.6533),
                "Rogers Centre": (43.6414, -79.3894),
                "Oriole Park at Camden Yards": (39.2839, -76.6217),
                "Nationals Park": (38.8730, -77.0074),
                "Citi Field": (40.7571, -73.8458),
                "loanDepot park": (25.7781, -80.2195),
            }
            venues = []
            for row in venue_df.iter_rows(named=True):
                name = str(row.get("venue", "") or row.get("venue_id", ""))
                coords = KNOWN_PARKS.get(name, (0.0, 0.0))
                venues.append({"id": name, "lat": coords[0], "lon": coords[1]})
            return venues
        except Exception as e:  # noqa: BLE001 -- external I/O (HTTP/parsing); error captured and reported via status/health, not swallowed
            # This except previously masked a real bug (a KeyError from
            # selecting a nonexistent `venue_id` column) as a silent `[]` —
            # see outputs/rebuild/takeover_status.md Checkpoint 4. Surface
            # failures through source health instead of swallowing them.
            self.meta.update_source_health("open_meteo", "degraded", f"_venues_for_date failed: {str(e)[:150]}")
            return []


# ── NBA/WNBA Collector ──────────────────────────────────────────────────────

class NBACollector:
    """NBA/WNBA data via ESPN + Polymarket.

    Usage:
        collector = NBACollector(data_root, meta)
        collector.collect_date("2026-08-05", sport="nba")
        collector.collect_date("2026-08-05", sport="wnba")
    """

    def __init__(self, data_root: str | Path, meta: Any, rate_limit: float = DEFAULT_RATE_LIMIT) -> None:
        self.root = Path(data_root)
        self.meta = meta
        self.rate_limit = rate_limit
        self.raw = RawStore(self.root / "raw")
        self.norm = NormalizedStore(self.root / "normalized")
        self.markets = MarketStore(self.root / "markets")
        self.identity = IdentityRegistry(meta)
        self._last_call = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_call = time.monotonic()

    # ── ESPN Scoreboard ──────────────────────────────────────────────────

    def collect_espn_scoreboard(self, game_date: str, sport: str = "nba") -> dict[str, Any]:
        """Fetch ESPN scoreboard for NBA or WNBA. Caches raw, returns normalized games."""
        source = "espn_public"
        league = sport.upper()  # "NBA" or "WNBA"
        record_id = f"{sport}_scoreboard_{game_date}"
        self._throttle()

        try:
            from model_prediction.data_sources.espn import ESPNClient
        except ImportError:
            return {"status": "no_espn_client", "date": game_date, "sport": sport}

        client = ESPNClient()
        payload = client.scoreboard(league, game_date)
        snapshot_hash = self.raw.write(source, game_date, record_id, payload).snapshot_hash
        self.meta.update_source_health(source, "active")

        events = payload.get("events", [])
        games: list[dict[str, Any]] = []
        for event in events:
            competitions = event.get("competitions", [{}])
            comp = competitions[0] if competitions else {}
            competitors = comp.get("competitors", [])
            away = home = {}
            for c in competitors:
                if c.get("homeAway") == "away":
                    away = c
                else:
                    home = c
            home_team_obj = (home.get("team", {}) or {})
            away_team_obj = (away.get("team", {}) or {})
            observed_at = utc_now().isoformat()
            home_canonical_id, away_canonical_id = resolve_espn_scoreboard_team_ids(
                self.identity, sport, source, home_team_obj, away_team_obj, observed_at,
            )
            venue_obj = comp.get("venue", {}) or {}
            venue_name = venue_obj.get("fullName", "")
            event_canonical_id = resolve_espn_scoreboard_event_id(
                self.identity, sport, source, str(event.get("id", "")),
                home_team_obj, away_team_obj, home_canonical_id, away_canonical_id,
                event.get("date", ""), observed_at, venue_name,
            )
            venue_canonical_id = resolve_espn_scoreboard_venue_id(
                self.identity, sport, source, venue_obj, observed_at,
            )
            games.append({
                **provenance_row(
                    source=source,
                    source_record_id=str(event.get("id", "")),
                    source_version="espn_public_v1",
                    observed_at_utc=observed_at,
                    effective_at_utc=event.get("date", ""),
                    event_start_utc=event.get("date", ""),
                    raw_snapshot_hash=snapshot_hash,
                ),
                "event_id": str(event.get("id", "")),
                "away_team": away_team_obj.get("displayName", ""),
                "home_team": home_team_obj.get("displayName", ""),
                "away_team_canonical_id": away_canonical_id,
                "home_team_canonical_id": home_canonical_id,
                "event_canonical_id": event_canonical_id,
                "venue_canonical_id": venue_canonical_id,
                "away_score": int(away.get("score", 0) or 0),
                "home_score": int(home.get("score", 0) or 0),
                "status": str(comp.get("status", {}).get("type", {}).get("name", "")),
                "venue": venue_name,
            })

        if games:
            df = pl.DataFrame(games)
            self.norm.write(sport, "scoreboard", df, primary_key=["event_id"], contract=SCOREBOARD_CONTRACT)
            self.meta.audit_event("collect_espn_scoreboard", {"sport": sport, "date": game_date, "games": len(games)})
            return {"status": "ok", "sport": sport, "date": game_date, "games": len(games)}

        return {"status": "no_games", "sport": sport, "date": game_date}

    # ── ESPN Roster (canonical player identity, Task 3) ─────────────────

    def collect_espn_roster(self, team_id: str, season: int, sport: str = "nba") -> dict[str, Any]:
        """Real per-team roster collection for NBA or WNBA -- resolves
        each real athlete to a canonical player identity via
        resolve_espn_roster_player_id(), same shape as
        collect_espn_scoreboard()'s team-identity resolution."""
        return _collect_espn_roster(self, sport, sport.upper(), team_id, season)

    # ── Polymarket Markets ───────────────────────────────────────────────

    def _collect_markets(self, sport: str, game_date: str) -> dict[str, Any]:
        source = "polymarket_us"
        self._throttle()
        try:
            from model_prediction.data_sources.polymarket_us import PolymarketUSClient
            client = PolymarketUSClient()
            events = client.slate(sport.upper(), date.fromisoformat(game_date))
            books: list[dict[str, Any]] = []
            for event in events:
                # Real _normalize_event shape (polymarket_us.py): event_id/
                # market_id/market_type keys, per-side prices in a "sides"
                # list — not the flat id/type/bestBid/bestAsk keys used
                # below previously, which don't exist on this object and
                # silently resolved to "" / None for every row (see
                # outputs/rebuild/takeover_status.md Checkpoint 8; same bug
                # as MLBCollector.collect_polymarket_books, fixed there first).
                event_id = str(event.get("event_id", ""))
                for market in event.get("markets", []):
                    market_id = str(market.get("market_id", ""))
                    market_type = market.get("market_type", "")
                    line = market.get("line")
                    for side in market.get("sides", []):
                        books.append({
                            **provenance_row(source, f"{event_id}_{market_id}_{side.get('side_id', '')}",
                                              "polymarket_us_v1", utc_now().isoformat(),
                                              utc_now().isoformat(), event.get("event_start_utc", "")),
                            "event_id": event_id, "market_id": market_id, "market_type": market_type,
                            "team_or_side": side.get("selection", ""), "team": side.get("team"),
                            "team_canonical_id": resolve_polymarket_team_id(self.identity, sport, side.get("team")),
                            "line": side.get("line", line),
                            "executable_price": side.get("price_probability"),
                            "decimal_odds": side.get("decimal_odds"), "american_odds": side.get("american_odds"),
                            "available_depth": None,
                        })
            if books:
                self.markets.write_books(sport, game_date, pl.DataFrame(books), contract=MARKET_SNAPSHOT_CONTRACT)
                return {"status": "ok", "books": len(books)}
            return {"status": "no_markets"}
        except Exception as e:  # noqa: BLE001 -- external I/O (HTTP/parsing); error captured and reported via status/health, not swallowed
            return {"status": "error", "error": str(e)[:200]}

    # ── Orchestrate ──────────────────────────────────────────────────────

    def collect_date(self, game_date: str, sport: str = "nba") -> dict[str, Any]:
        """Collect NBA/WNBA data for a date: ESPN scoreboard + Polymarket markets."""
        results: dict[str, Any] = {"date": game_date, "sport": sport}
        results["espn"] = self.collect_espn_scoreboard(game_date, sport)
        results["polymarket"] = self._collect_markets(sport, game_date)
        all_ok = all(
            r.get("status") in ("ok", "no_games", "no_markets")
            for r in (results.get("espn", {}), results.get("polymarket", {}))
        )
        results["status"] = "ok" if all_ok else "partial"
        return results

# ── NFL Collector ───────────────────────────────────────────────────────────

class NFLCollector:
    """NFL data via nflverse + ESPN + Polymarket."""

    def __init__(self, data_root: str | Path, meta: Any, rate_limit: float = DEFAULT_RATE_LIMIT) -> None:
        self.root = Path(data_root)
        self.meta = meta
        self.rate_limit = rate_limit
        self.raw = RawStore(self.root / "raw")
        self.norm = NormalizedStore(self.root / "normalized")
        self.markets = MarketStore(self.root / "markets")
        self.identity = IdentityRegistry(meta)
        self._last_call = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_call = time.monotonic()

    def collect_espn_roster(self, team_id: str, season: int) -> dict[str, Any]:
        """Real per-team roster collection -- resolves each real athlete
        to a canonical player identity (Task 3)."""
        return _collect_espn_roster(self, "nfl", "NFL", team_id, season)

    def _collect_espn(self, game_date: str) -> dict[str, Any]:
        sport = "nfl"; source = "espn_public"
        record_id = f"nfl_scoreboard_{game_date}"
        self._throttle()
        try:
            from model_prediction.data_sources.espn import ESPNClient
        except ImportError:
            return {"status": "no_espn_client", "date": game_date}
        client = ESPNClient()
        payload = client.scoreboard("NFL", game_date)
        snapshot_hash = self.raw.write(source, game_date, record_id, payload).snapshot_hash
        self.meta.update_source_health(source, "active")
        events = payload.get("events", [])
        games = []
        for event in events:
            comp = (event.get("competitions", [{}]) or [{}])[0]
            competitors = comp.get("competitors", [])
            away = home = {}
            for c in competitors:
                if c.get("homeAway") == "away": away = c
                else: home = c
            home_team_obj = (home.get("team", {}) or {})
            away_team_obj = (away.get("team", {}) or {})
            observed_at = utc_now().isoformat()
            home_canonical_id, away_canonical_id = resolve_espn_scoreboard_team_ids(
                self.identity, sport, source, home_team_obj, away_team_obj, observed_at,
            )
            venue_obj = comp.get("venue", {}) or {}
            venue_name = venue_obj.get("fullName", "")
            event_canonical_id = resolve_espn_scoreboard_event_id(
                self.identity, sport, source, str(event.get("id", "")),
                home_team_obj, away_team_obj, home_canonical_id, away_canonical_id,
                event.get("date", ""), observed_at, venue_name,
            )
            venue_canonical_id = resolve_espn_scoreboard_venue_id(
                self.identity, sport, source, venue_obj, observed_at,
            )
            games.append({
                **provenance_row(source, str(event.get("id", "")), "espn_public_v1",
                                  observed_at, event.get("date", ""), event.get("date", ""),
                                  raw_snapshot_hash=snapshot_hash),
                "event_id": str(event.get("id", "")),
                "away_team": away_team_obj.get("displayName", ""),
                "home_team": home_team_obj.get("displayName", ""),
                "away_team_canonical_id": away_canonical_id,
                "home_team_canonical_id": home_canonical_id,
                "event_canonical_id": event_canonical_id,
                "venue_canonical_id": venue_canonical_id,
                "away_score": int(away.get("score", 0) or 0),
                "home_score": int(home.get("score", 0) or 0),
                "status": str(comp.get("status", {}).get("type", {}).get("name", "")),
                "venue": venue_name,
            })
        if games:
            self.norm.write(sport, "scoreboard", pl.DataFrame(games), primary_key=["event_id"], contract=SCOREBOARD_CONTRACT)
            return {"status": "ok", "sport": sport, "date": game_date, "games": len(games)}
        return {"status": "no_games", "sport": sport, "date": game_date}

    def _collect_markets(self, game_date: str) -> dict[str, Any]:
        sport = "nfl"; source = "polymarket_us"
        self._throttle()
        try:
            from model_prediction.data_sources.polymarket_us import PolymarketUSClient
            client = PolymarketUSClient()
            events = client.slate("NFL", date.fromisoformat(game_date))
            books: list[dict[str, Any]] = []
            for event in events:
                # See Checkpoint 8 in outputs/rebuild/takeover_status.md:
                # real _normalize_event keys are event_id/market_id/
                # market_type, with per-side prices in "sides" - the
                # previous id/type/bestBid/bestAsk keys don't exist here.
                event_id = str(event.get("event_id", ""))
                for market in event.get("markets", []):
                    market_id = str(market.get("market_id", ""))
                    market_type = market.get("market_type", "")
                    line = market.get("line")
                    for side in market.get("sides", []):
                        books.append({**provenance_row(
                            source, f"{event_id}_{market_id}_{side.get('side_id', '')}",
                            "polymarket_us_v1", utc_now().isoformat(), utc_now().isoformat(),
                            event.get("event_start_utc", "")),
                            "event_id": event_id, "market_id": market_id, "market_type": market_type,
                            "team_or_side": side.get("selection", ""), "team": side.get("team"),
                            "team_canonical_id": resolve_polymarket_team_id(self.identity, sport, side.get("team")),
                            "line": side.get("line", line),
                            "executable_price": side.get("price_probability"),
                            "decimal_odds": side.get("decimal_odds"), "american_odds": side.get("american_odds"),
                            "available_depth": None})
            if books:
                self.markets.write_books(sport, game_date, pl.DataFrame(books), contract=MARKET_SNAPSHOT_CONTRACT)
                return {"status": "ok", "books": len(books)}
            return {"status": "no_markets"}
        except Exception as e:  # noqa: BLE001 -- external I/O (HTTP/parsing); error captured and reported via status/health, not swallowed
            return {"status": "error", "error": str(e)[:200]}

    def collect_date(self, game_date: str) -> dict[str, Any]:
        results: dict[str, Any] = {"date": game_date, "sport": "nfl"}
        results["espn"] = self._collect_espn(game_date)
        results["polymarket"] = self._collect_markets(game_date)
        results["status"] = "ok" if all(r.get("status") in ("ok", "no_games", "no_markets") for r in (results.get("espn", {}), results.get("polymarket", {}))) else "partial"
        return results


# ── Soccer Collector ────────────────────────────────────────────────────────

class SoccerCollector:
    """Soccer data via StatsBomb + ESPN + Polymarket."""

    def __init__(self, data_root: str | Path, meta: Any, rate_limit: float = DEFAULT_RATE_LIMIT) -> None:
        self.root = Path(data_root)
        self.meta = meta
        self.rate_limit = rate_limit
        self.raw = RawStore(self.root / "raw")
        self.norm = NormalizedStore(self.root / "normalized")
        self.markets = MarketStore(self.root / "markets")
        self.identity = IdentityRegistry(meta)
        self._last_call = 0.0

    def collect_espn_roster(self, team_id: str, season: int, league: str = "EPL") -> dict[str, Any]:
        """Real per-team roster collection -- resolves each real athlete
        to a canonical player identity (Task 3). `league` matches
        collect_date()'s own real-vs-guessed-umbrella-competition
        constraint (see collect_date()'s docstring): soccer has no single
        ESPN league path, so a real one must be named explicitly."""
        return _collect_espn_roster(self, "soccer", league, team_id, season)

    def _collect_date(self, game_date: str, sport: str, league: str) -> dict[str, Any]:
        results: dict[str, Any] = {"date": game_date, "sport": sport}
        results["espn"] = self._collect_espn(game_date, sport, league)
        results["polymarket"] = self._collect_markets(game_date, sport, league)
        results["status"] = "ok" if all(r.get("status") in ("ok", "no_games", "no_markets") for r in (results.get("espn", {}), results.get("polymarket", {}))) else "partial"
        return results

    def _collect_espn(self, game_date: str, sport: str, league: str) -> dict[str, Any]:
        source = "espn_public"; record_id = f"{sport}_scoreboard_{game_date}"
        self._throttle()
        try:
            from model_prediction.data_sources.espn import ESPNClient
        except ImportError:
            return {"status": "no_espn_client", "date": game_date, "sport": sport}
        client = ESPNClient()
        payload = client.scoreboard(league, game_date)
        snapshot_hash = self.raw.write(source, game_date, record_id, payload).snapshot_hash
        self.meta.update_source_health(source, "active")
        events = payload.get("events", [])
        games = []
        for event in events:
            comp = (event.get("competitions", [{}]) or [{}])[0]
            competitors = comp.get("competitors", [])
            away = home = {}
            for c in competitors:
                if c.get("homeAway") == "away": away = c
                else: home = c
            home_team_obj = (home.get("team", {}) or {})
            away_team_obj = (away.get("team", {}) or {})
            observed_at = utc_now().isoformat()
            home_canonical_id, away_canonical_id = resolve_espn_scoreboard_team_ids(
                self.identity, sport, source, home_team_obj, away_team_obj, observed_at,
            )
            venue_obj = comp.get("venue", {}) or {}
            venue_name = venue_obj.get("fullName", "")
            event_canonical_id = resolve_espn_scoreboard_event_id(
                self.identity, sport, source, str(event.get("id", "")),
                home_team_obj, away_team_obj, home_canonical_id, away_canonical_id,
                event.get("date", ""), observed_at, venue_name,
            )
            venue_canonical_id = resolve_espn_scoreboard_venue_id(
                self.identity, sport, source, venue_obj, observed_at,
            )
            games.append({**provenance_row(source, str(event.get("id", "")), "espn_public_v1",
                observed_at, event.get("date", ""), event.get("date", ""),
                raw_snapshot_hash=snapshot_hash),
                "event_id": str(event.get("id", "")),
                "away_team": away_team_obj.get("displayName", ""),
                "home_team": home_team_obj.get("displayName", ""),
                "away_team_canonical_id": away_canonical_id,
                "home_team_canonical_id": home_canonical_id,
                "event_canonical_id": event_canonical_id,
                "venue_canonical_id": venue_canonical_id,
                "away_score": int(away.get("score", 0) or 0),
                "home_score": int(home.get("score", 0) or 0),
                "status": str(comp.get("status", {}).get("type", {}).get("name", "")),
                "venue": venue_name,})
        if games:
            self.norm.write(sport, "scoreboard", pl.DataFrame(games), primary_key=["event_id"], contract=SCOREBOARD_CONTRACT)
            return {"status": "ok", "sport": sport, "date": game_date, "games": len(games)}
        return {"status": "no_games", "sport": sport, "date": game_date}

    def _collect_markets(self, game_date: str, sport: str, league: str) -> dict[str, Any]:
        source = "polymarket_us"; self._throttle()
        try:
            from model_prediction.data_sources.polymarket_us import PolymarketUSClient
            client = PolymarketUSClient()
            events = client.slate(league, date.fromisoformat(game_date))
            books: list[dict[str, Any]] = []
            for event in events:
                # See Checkpoint 8 in outputs/rebuild/takeover_status.md:
                # real _normalize_event keys are event_id/market_id/
                # market_type, with per-side prices in "sides" - the
                # previous id/type/bestBid/bestAsk keys don't exist here.
                event_id = str(event.get("event_id", ""))
                for market in event.get("markets", []):
                    market_id = str(market.get("market_id", ""))
                    market_type = market.get("market_type", "")
                    line = market.get("line")
                    for side in market.get("sides", []):
                        books.append({**provenance_row(
                            source, f"{event_id}_{market_id}_{side.get('side_id', '')}",
                            "polymarket_us_v1", utc_now().isoformat(), utc_now().isoformat(),
                            event.get("event_start_utc", "")),
                            "event_id": event_id, "market_id": market_id, "market_type": market_type,
                            "team_or_side": side.get("selection", ""), "team": side.get("team"),
                            "team_canonical_id": resolve_polymarket_team_id(self.identity, sport, side.get("team")),
                            "line": side.get("line", line),
                            "executable_price": side.get("price_probability"),
                            "decimal_odds": side.get("decimal_odds"), "american_odds": side.get("american_odds"),
                            "available_depth": None})
            if books:
                self.markets.write_books(sport, game_date, pl.DataFrame(books), contract=MARKET_SNAPSHOT_CONTRACT)
                return {"status": "ok", "books": len(books)}
            return {"status": "no_markets"}
        except Exception as e:  # noqa: BLE001 -- external I/O (HTTP/parsing); error captured and reported via status/health, not swallowed
            return {"status": "error", "error": str(e)[:200]}

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_call = time.monotonic()

    def collect_date(self, game_date: str, league: str = "EPL") -> dict[str, Any]:
        # Real bug fixed here (found live wiring the shared sport-adapter
        # CLI, 2026-08-07): this called ESPNClient.scoreboard() with
        # league="SOCCER" -- not a real league in data_sources/espn.py's
        # LEAGUE_PATHS, which only has specific competitions (EPL,
        # LA_LIGA, MLS, ...). Real collection has never worked for any
        # soccer league through this collector; every real call raised
        # ValueError("unsupported ESPN league: SOCCER"). "Soccer" isn't
        # one real ESPN league -- there's no single umbrella competition
        # -- so this defaults to a real, well-established one (EPL) rather
        # than silently guessing at "the" soccer league. Multi-competition
        # aggregation (CLAUDE.md Part 1's real soccer scope) remains real,
        # disclosed future work, not claimed here.
        return self._collect_date(game_date, "soccer", league)


# ── Tennis Collector ────────────────────────────────────────────────────────

class TennisCollector:
    """Tennis data via Sackmann + ESPN + Polymarket."""

    def __init__(self, data_root: str | Path, meta: Any, rate_limit: float = DEFAULT_RATE_LIMIT) -> None:
        self.root = Path(data_root)
        self.meta = meta
        self.rate_limit = rate_limit
        self.raw = RawStore(self.root / "raw")
        self.norm = NormalizedStore(self.root / "normalized")
        self.markets = MarketStore(self.root / "markets")
        self.identity = IdentityRegistry(meta)
        self._last_call = 0.0

    def _collect_date(self, game_date: str, sport: str, league: str) -> dict[str, Any]:
        results: dict[str, Any] = {"date": game_date, "sport": sport}
        results["espn"] = self._collect_espn(game_date, sport, league)
        results["polymarket"] = self._collect_markets(game_date, sport, league)
        results["status"] = "ok" if all(r.get("status") in ("ok", "no_games", "no_markets") for r in (results.get("espn", {}), results.get("polymarket", {}))) else "partial"
        return results

    def _collect_espn(self, game_date: str, sport: str, league: str) -> dict[str, Any]:
        source = "espn_public"; record_id = f"{sport}_scoreboard_{game_date}"
        self._throttle()
        try:
            from model_prediction.data_sources.espn import ESPNClient
        except ImportError:
            return {"status": "no_espn_client", "date": game_date, "sport": sport}
        client = ESPNClient()
        payload = client.scoreboard(league, game_date)
        snapshot_hash = self.raw.write(source, game_date, record_id, payload).snapshot_hash
        self.meta.update_source_health(source, "active")
        events = payload.get("events", [])
        games = []
        for event in events:
            # Real gap fixed here: ESPN tennis events are tournaments, not
            # single matches. Real matches live under groupings[].competitions[]
            # (e.g. "Men's Singles"), not the top-level event.competitions used
            # by team sports -- that top-level key is absent/empty for real
            # tennis events, which silently produced one garbage all-empty row
            # per tournament (competitors=[]) instead of one real row per
            # match. Falls back to top-level competitions for any payload
            # shape without groupings (defensive, not currently observed live).
            groupings = event.get("groupings")
            competitions = (
                [c for g in groupings for c in (g.get("competitions", []) or [])]
                if groupings else (event.get("competitions", []) or [])
            )
            for comp in competitions:
                competitors = comp.get("competitors", [])
                away = home = {}
                for c in competitors:
                    if c.get("homeAway") == "away": away = c
                    else: home = c
                home_team_obj = _competitor_identity_obj(home)
                away_team_obj = _competitor_identity_obj(away)
                observed_at = utc_now().isoformat()
                home_canonical_id, away_canonical_id = resolve_espn_scoreboard_team_ids(
                    self.identity, sport, source, home_team_obj, away_team_obj, observed_at,
                )
                comp_id = str(comp.get("id", "")) or str(event.get("id", ""))
                comp_date = comp.get("date", event.get("date", ""))
                venue_obj = comp.get("venue", {}) or {}
                venue_name = venue_obj.get("fullName", "")
                event_canonical_id = resolve_espn_scoreboard_event_id(
                    self.identity, sport, source, comp_id,
                    home_team_obj, away_team_obj, home_canonical_id, away_canonical_id,
                    comp_date, observed_at, venue_name,
                )
                venue_canonical_id = resolve_espn_scoreboard_venue_id(
                    self.identity, sport, source, venue_obj, observed_at,
                )
                games.append({**provenance_row(source, comp_id, "espn_public_v1",
                    observed_at, comp_date, comp_date,
                    raw_snapshot_hash=snapshot_hash),
                    "event_id": comp_id,
                    "away_team": away_team_obj.get("displayName", ""),
                    "home_team": home_team_obj.get("displayName", ""),
                    "away_team_canonical_id": away_canonical_id,
                    "home_team_canonical_id": home_canonical_id,
                    "event_canonical_id": event_canonical_id,
                "venue_canonical_id": venue_canonical_id,
                    "away_score": int(away.get("score", 0) or 0),
                    "home_score": int(home.get("score", 0) or 0),
                    "status": str(comp.get("status", {}).get("type", {}).get("name", "")),
                    "venue": venue_name,})
        if games:
            self.norm.write(sport, "scoreboard", pl.DataFrame(games), primary_key=["event_id"], contract=SCOREBOARD_CONTRACT)
            return {"status": "ok", "sport": sport, "date": game_date, "games": len(games)}
        return {"status": "no_games", "sport": sport, "date": game_date}

    def _collect_markets(self, game_date: str, sport: str, league: str) -> dict[str, Any]:
        source = "polymarket_us"; self._throttle()
        try:
            from model_prediction.data_sources.polymarket_us import PolymarketUSClient
            client = PolymarketUSClient()
            events = client.slate(league, date.fromisoformat(game_date))
            books: list[dict[str, Any]] = []
            for event in events:
                # See Checkpoint 8 in outputs/rebuild/takeover_status.md:
                # real _normalize_event keys are event_id/market_id/
                # market_type, with per-side prices in "sides" - the
                # previous id/type/bestBid/bestAsk keys don't exist here.
                event_id = str(event.get("event_id", ""))
                for market in event.get("markets", []):
                    market_id = str(market.get("market_id", ""))
                    market_type = market.get("market_type", "")
                    line = market.get("line")
                    for side in market.get("sides", []):
                        books.append({**provenance_row(
                            source, f"{event_id}_{market_id}_{side.get('side_id', '')}",
                            "polymarket_us_v1", utc_now().isoformat(), utc_now().isoformat(),
                            event.get("event_start_utc", "")),
                            "event_id": event_id, "market_id": market_id, "market_type": market_type,
                            "team_or_side": side.get("selection", ""), "team": side.get("team"),
                            "team_canonical_id": resolve_polymarket_team_id(self.identity, sport, side.get("team")),
                            "line": side.get("line", line),
                            "executable_price": side.get("price_probability"),
                            "decimal_odds": side.get("decimal_odds"), "american_odds": side.get("american_odds"),
                            "available_depth": None})
            if books:
                self.markets.write_books(sport, game_date, pl.DataFrame(books), contract=MARKET_SNAPSHOT_CONTRACT)
                return {"status": "ok", "books": len(books)}
            return {"status": "no_markets"}
        except Exception as e:  # noqa: BLE001 -- external I/O (HTTP/parsing); error captured and reported via status/health, not swallowed
            return {"status": "error", "error": str(e)[:200]}

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_call = time.monotonic()

    def collect_date(self, game_date: str, league: str = "ATP") -> dict[str, Any]:
        # Real bug fixed here (found live wiring the shared sport-adapter
        # CLI, 2026-08-07): this called ESPNClient.scoreboard() with
        # league="TENNIS" -- not a real league in data_sources/espn.py's
        # LEAGUE_PATHS, which has "ATP"/"WTA" specifically, not a generic
        # "TENNIS" umbrella. Real collection has never worked through this
        # collector; every real call raised ValueError("unsupported ESPN
        # league: TENNIS"). Defaults to ATP; a caller wanting WTA passes
        # league="WTA" explicitly (collect_date() doesn't aggregate both
        # tours in one call, unlike ESPNClient.tennis_scoreboard() which
        # already merges them -- using that directly is real, disclosed
        # future work for this collector, not claimed here).
        return self._collect_date(game_date, "tennis", league)


# ── Esports Collector ───────────────────────────────────────────────────────

class EsportsCollector:
    """Esports data via BO3 + Valve VRS + OpenDota + Polymarket."""

    def __init__(self, data_root: str | Path, meta: Any, rate_limit: float = DEFAULT_RATE_LIMIT) -> None:
        self.root = Path(data_root)
        self.meta = meta
        self.rate_limit = rate_limit
        self.raw = RawStore(self.root / "raw")
        self.norm = NormalizedStore(self.root / "normalized")
        self.markets = MarketStore(self.root / "markets")
        self._last_call = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_call = time.monotonic()

    def collect_date(self, game_date: str, title: str) -> dict[str, Any]:
        return {"status": "stub", "date": game_date, "title": title, "note": "BO3/OpenDota integration pending"}
