"""Unified MarketQuote Warehouse & Snapshot Ingestion Layer.

Provides immutable persistence, high-performance querying, and Point-in-Time (PIT)
quote retrieval across all upstream sportsbook and prediction market feeds.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..domain import MarketQuote, utc_now
from ..runtime_paths import RuntimePaths

_WAREHOUSE_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_quotes (
    quote_id                INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id                TEXT NOT NULL,
    sport                   TEXT NOT NULL,
    market_type             TEXT NOT NULL,
    selection               TEXT NOT NULL,
    source                  TEXT NOT NULL,
    observed_at_utc         TEXT NOT NULL,
    best_bid                REAL,
    best_ask                REAL,
    last_price              REAL,
    american_odds           INTEGER,
    decimal_odds            REAL,
    raw_implied_probability REAL,
    no_vig_probability      REAL,
    depth_liquidity_usd     REAL,
    line                    REAL,
    is_executable           INTEGER NOT NULL DEFAULT 1,
    created_at_utc          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_market_quotes_pit
    ON market_quotes (event_id, market_type, selection, observed_at_utc DESC);

CREATE INDEX IF NOT EXISTS idx_market_quotes_sport_time
    ON market_quotes (sport, observed_at_utc DESC);
"""


class MarketQuoteWarehouse:
    """Persistent SQLite warehouse for multi-exchange market quotes."""

    def __init__(self, db_path: Path | str | None = None, paths: RuntimePaths | None = None) -> None:
        if db_path is not None:
            self.db_path = Path(db_path)
        elif paths is not None:
            self.db_path = paths.runtime_root / "market_quotes.db"
        else:
            runtime_paths = RuntimePaths.resolve()
            self.db_path = runtime_paths.runtime_root / "market_quotes.db"

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_WAREHOUSE_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def record_quote(self, quote: MarketQuote) -> int:
        """Insert a single market quote."""
        now_iso = utc_now().isoformat()
        with self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO market_quotes (
                    event_id, sport, market_type, selection, source, observed_at_utc,
                    best_bid, best_ask, last_price, american_odds, decimal_odds,
                    raw_implied_probability, no_vig_probability, depth_liquidity_usd,
                    line, is_executable, created_at_utc
                ) VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?
                )
                """,
                (
                    quote.event_id,
                    quote.sport,
                    quote.market_type,
                    quote.selection,
                    quote.source,
                    quote.observed_at_utc,
                    quote.best_bid,
                    quote.best_ask,
                    quote.last_price,
                    quote.american_odds,
                    quote.decimal_odds,
                    quote.raw_implied_probability,
                    quote.no_vig_probability,
                    quote.depth_liquidity_usd,
                    quote.line,
                    1 if quote.is_executable else 0,
                    now_iso,
                ),
            )
            return cursor.lastrowid or 0

    def record_quotes_batch(self, quotes: Sequence[MarketQuote]) -> int:
        """Bulk insert a sequence of market quotes in a single transaction."""
        if not quotes:
            return 0
        now_iso = utc_now().isoformat()
        rows = [
            (
                q.event_id,
                q.sport,
                q.market_type,
                q.selection,
                q.source,
                q.observed_at_utc,
                q.best_bid,
                q.best_ask,
                q.last_price,
                q.american_odds,
                q.decimal_odds,
                q.raw_implied_probability,
                q.no_vig_probability,
                q.depth_liquidity_usd,
                q.line,
                1 if q.is_executable else 0,
                now_iso,
            )
            for q in quotes
        ]
        with self._conn:
            self._conn.executemany(
                """
                INSERT INTO market_quotes (
                    event_id, sport, market_type, selection, source, observed_at_utc,
                    best_bid, best_ask, last_price, american_odds, decimal_odds,
                    raw_implied_probability, no_vig_probability, depth_liquidity_usd,
                    line, is_executable, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(quotes)

    def get_latest_quote(
        self,
        event_id: str,
        market_type: str,
        selection: str,
        *,
        source: str | None = None,
        as_of_utc: str | None = None,
    ) -> MarketQuote | None:
        """Retrieve the most recent quote strictly on or before as_of_utc (PIT safe)."""
        query = "SELECT * FROM market_quotes WHERE event_id = ? AND market_type = ? AND selection = ?"
        params: list[Any] = [event_id, market_type, selection]
        if source is not None:
            query += " AND source = ?"
            params.append(source)
        if as_of_utc is not None:
            query += " AND observed_at_utc <= ?"
            params.append(as_of_utc)
        query += " ORDER BY observed_at_utc DESC LIMIT 1"

        row = self._conn.execute(query, params).fetchone()
        return self._row_to_quote(row) if row is not None else None

    def get_quotes_for_event(
        self,
        event_id: str,
        *,
        market_type: str | None = None,
        selection: str | None = None,
        as_of_utc: str | None = None,
    ) -> list[MarketQuote]:
        """Get all market quotes for an event across all markets/selections as of a timestamp."""
        query = "SELECT * FROM market_quotes WHERE event_id = ?"
        params: list[Any] = [event_id]
        if market_type is not None:
            query += " AND market_type = ?"
            params.append(market_type)
        if selection is not None:
            query += " AND selection = ?"
            params.append(selection)
        if as_of_utc is not None:
            query += " AND observed_at_utc <= ?"
            params.append(as_of_utc)
        query += " ORDER BY observed_at_utc DESC"

        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_quote(r) for r in rows]

    def count_quotes(self, *, sport: str | None = None) -> int:
        query = "SELECT COUNT(*) as n FROM market_quotes"
        params: list[Any] = []
        if sport is not None:
            query += " WHERE sport = ?"
            params.append(sport)
        row = self._conn.execute(query, params).fetchone()
        return int(row["n"]) if row else 0

    @staticmethod
    def _row_to_quote(row: sqlite3.Row) -> MarketQuote:
        return MarketQuote(
            event_id=str(row["event_id"]),
            sport=str(row["sport"]),
            market_type=str(row["market_type"]),
            selection=str(row["selection"]),
            source=str(row["source"]),
            observed_at_utc=str(row["observed_at_utc"]),
            best_bid=float(row["best_bid"]) if row["best_bid"] is not None else None,
            best_ask=float(row["best_ask"]) if row["best_ask"] is not None else None,
            last_price=float(row["last_price"]) if row["last_price"] is not None else None,
            american_odds=int(row["american_odds"]) if row["american_odds"] is not None else None,
            decimal_odds=float(row["decimal_odds"]) if row["decimal_odds"] is not None else None,
            raw_implied_probability=float(row["raw_implied_probability"])
            if row["raw_implied_probability"] is not None
            else None,
            no_vig_probability=float(row["no_vig_probability"])
            if row["no_vig_probability"] is not None
            else None,
            depth_liquidity_usd=float(row["depth_liquidity_usd"])
            if row["depth_liquidity_usd"] is not None
            else None,
            line=float(row["line"]) if row["line"] is not None else None,
            is_executable=bool(row["is_executable"]),
        )
