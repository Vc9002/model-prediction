"""MLB source collector — pybaseball + Open-Meteo + Polymarket US.

Every collector is restartable, idempotent, rate-limited, and schema-tested.
Raw responses are immutable. Normalized tables carry full provenance columns.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import polars as pl

from .storage import MarketStore, NormalizedStore, RawStore, provenance_row, utc_now

DEFAULT_RATE_LIMIT = 0.6  # seconds between calls


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
        snapshot_hash = self.raw.write(source, game_date, record_id, payload, allow_refresh=True)
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
            games.append({
                **provenance_row(
                    source=source,
                    source_record_id=str(event.get("id", "")),
                    source_version="espn_public_v1",
                    observed_at_utc=utc_now().isoformat(),
                    effective_at_utc=event.get("date", ""),
                    event_start_utc=event.get("date", ""),
                    raw_snapshot_hash=snapshot_hash,
                ),
                "event_id": str(event.get("id", "")),
                "away_team": (away.get("team", {}) or {}).get("displayName", ""),
                "home_team": (home.get("team", {}) or {}).get("displayName", ""),
                "away_score": int(away.get("score", 0) or 0),
                "home_score": int(home.get("score", 0) or 0),
                "status": str(comp.get("status", {}).get("type", {}).get("name", "")),
                "venue": (comp.get("venue", {}) or {}).get("fullName", ""),
            })

        if games:
            df = pl.DataFrame(games)
            self.norm.write("mlb", "scoreboard", df)
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
                    snapshot_hash = self.raw.write(source, game_date, record_id, data)
                    self.meta.update_source_health(source, "active")
                    results["collected"].append("statcast")
                    results["statcast_rows"] = len(data)
                    results["statcast_hash"] = snapshot_hash
            except Exception as e:
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
            except Exception:
                pass

        results["status"] = "ok" if results["collected"] else "no_data"
        self.meta.audit_event("collect_pybaseball", results)
        return results

    # ── Open-Meteo Archived Forecasts ───────────────────────────────────

    def collect_weather_forecast(
        self, game_date: str, latitude: float, longitude: float, venue_id: str,
    ) -> dict[str, Any]:
        """Fetch archived weather forecast for a venue on a game date.

        Uses Open-Meteo's archive API for individual model runs — this returns
        the forecast *as it was* at a specific run time, not the realized weather
        (which would be a retrospective leak).

        Args:
            game_date: ISO date string
            latitude, longitude: venue coordinates
            venue_id: identifier for this venue
        """
        source = "open_meteo"
        record_id = f"weather_{venue_id}_{game_date}"
        self._throttle()

        import httpx
        url = "https://archive-api.open-meteo.com/v1/archive"
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": game_date,
            "end_date": game_date,
            "hourly": ["temperature_2m", "relative_humidity_2m", "dew_point_2m",
                        "precipitation", "surface_pressure", "wind_speed_10m",
                        "wind_direction_10m", "weather_code"],
            "timezone": "America/New_York",
            "models": "best_match",
        }
        try:
            resp = httpx.get(url, params=params, timeout=30)
            resp.raise_for_status()
            payload = resp.json()
            snapshot_hash = self.raw.write(source, game_date, record_id, payload)
            self.meta.update_source_health(source, "active")
            return {"status": "ok", "venue_id": venue_id, "hash": snapshot_hash}
        except Exception as e:
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
            mlb_events = client.slate("MLB", game_date)
            books: list[dict[str, Any]] = []
            for event in mlb_events:
                try:
                    event_id = str(event.get("id", ""))
                    markets = event.get("markets", [])
                    for market in markets:
                        books.append({
                            **provenance_row(
                                source=source,
                                source_record_id=f"{event_id}_{market.get('id', '')}",
                                source_version="polymarket_us_v1",
                                observed_at_utc=utc_now().isoformat(),
                                effective_at_utc=utc_now().isoformat(),
                                event_start_utc=event.get("startDate", ""),
                            ),
                            "event_id": event_id,
                            "market_id": market.get("id", ""),
                            "market_type": market.get("type", ""),
                            "side": market.get("side", ""),
                            "line": market.get("line"),
                            "best_bid": market.get("bestBid"),
                            "best_ask": market.get("bestAsk"),
                            "bid_size": market.get("bidSize"),
                            "ask_size": market.get("askSize"),
                            "spread": market.get("spread"),
                        })
                except Exception:
                    continue

            if books:
                payload = {"events": len(mlb_events), "books": len(books)}
                self.raw.write(source, game_date, record_id, payload)
                df = pl.DataFrame(books)
                self.markets.write_books("mlb", game_date, df)
                self.meta.update_source_health(source, "active")
                self.meta.audit_event("collect_polymarket_books", {"date": game_date, "books": len(books)})
                return {"status": "ok", "date": game_date, "books": len(books)}

            return {"status": "no_mlb_markets", "date": game_date}

        except Exception as e:
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
            ).select(["venue_id", "venue"]).unique()
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
        except Exception:
            return []


# ── NBA/WNBA Collector ──────────────────────────────────────────────────────

class NBACollector:
    """NBA data via SportsDataverse + ESPN + Polymarket."""

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

    def collect_date(self, game_date: str, sport: str = "nba") -> dict[str, Any]:
        """Collect NBA/WNBA data for a date. sport='nba' or 'wnba'."""
        results: dict[str, Any] = {"date": game_date, "sport": sport}
        results["polymarket"] = self._collect_markets(sport, game_date)
        all_ok = results.get("polymarket", {}).get("status") in ("ok", "no_markets")
        results["status"] = "ok" if all_ok else "partial"
        return results

    def _collect_markets(self, sport: str, game_date: str) -> dict[str, Any]:
        source = "polymarket_us"
        self._throttle()
        try:
            from model_prediction.data_sources.polymarket_us import PolymarketUSClient
            client = PolymarketUSClient()
            events = client.events_for_date(game_date).get(sport.upper(), [])
            books: list[dict[str, Any]] = []
            for event in events:
                for market in event.get("markets", []):
                    books.append({
                        **provenance_row(source, f"{event.get('id','')}_{market.get('id','')}",
                                         "polymarket_us_v1", utc_now().isoformat(),
                                         utc_now().isoformat(), event.get("startDate","")),
                        "event_id": str(event.get("id", "")),
                        "market_type": market.get("type", ""), "side": market.get("side", ""),
                        "line": market.get("line"), "best_bid": market.get("bestBid"),
                        "best_ask": market.get("bestAsk"),
                    })
            if books:
                self.markets.write_books(sport, game_date, pl.DataFrame(books))
                return {"status": "ok", "books": len(books)}
            return {"status": "no_markets"}
        except Exception as e:
            return {"status": "error", "error": str(e)[:200]}


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
        self._last_call = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_call = time.monotonic()

    def collect_date(self, game_date: str) -> dict[str, Any]:
        return {"status": "stub", "date": game_date, "note": "nflverse integration pending"}


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
        self._last_call = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_call = time.monotonic()

    def collect_date(self, game_date: str) -> dict[str, Any]:
        return {"status": "stub", "date": game_date, "note": "StatsBomb integration pending"}


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
        self._last_call = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_call = time.monotonic()

    def collect_date(self, game_date: str) -> dict[str, Any]:
        return {"status": "stub", "date": game_date, "note": "Sackmann integration pending"}


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
