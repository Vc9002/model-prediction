"""Real MLB market-to-game matching for Polymarket data (CLAUDE.md Checkpoint 9).

Three real bugs found live and fixed here (see
outputs/rebuild/takeover_status.md Checkpoint 9 for full evidence):

1. Totals were filtered by market_type alone with no event filter — every
   total market from the whole date's collection got attached to every
   game (176 candidates for a single game, confirmed live).
2. Moneyline/spread filtering compared Statcast-style team abbreviations
   ("SEA") against Polymarket's `team` field, which is the real full
   display name ("Seattle Mariners") — every comparison silently matched
   zero rows, so moneyline/spread markets were never actually evaluated
   against a real game in any prior run.
3. Polymarket lists genuinely separate full-game and first-5-innings
   markets that can share the exact same market_type/line (two distinct
   real "total > 6.5" markets: full game at 65c, F5 at 25c, confirmed
   live). This model only predicts full-game outcomes, so an F5 market
   compared against it is comparing incompatible bets — it isn't a
   pricing edge, it's a market-identity error masquerading as one.
"""

from __future__ import annotations

import polars as pl

from .decision import MarketEvaluation


def exclude_first_five_innings(market_rows: pl.DataFrame) -> pl.DataFrame:
    """Drop first-5-innings markets. `is_first_five_innings` comes from
    `market_slug` containing "-f5-" — the only reliable disambiguator
    (question text doesn't reliably say "first 5 innings" for totals)."""
    if market_rows.is_empty() or "is_first_five_innings" not in market_rows.columns:
        return market_rows
    return market_rows.filter(~pl.col("is_first_five_innings"))


def resolve_polymarket_event_id(
    market_rows: pl.DataFrame, home_name: str, away_name: str,
) -> str | None:
    """Polymarket's own event_id (e.g. "70535") for this specific game,
    found via its moneyline/spread rows that carry real team names — total
    markets don't carry a team, only a line, so they can't be matched this
    way and must instead be filtered by this resolved event_id. Takes full
    team names ("Seattle Mariners"), not Statcast-style abbreviations
    ("SEA") — see bug #2 above. Returns None on no/ambiguous match rather
    than guessing which event a game belongs to."""
    team_rows = market_rows.filter(pl.col("team").is_in([home_name, away_name]))
    event_ids = team_rows["event_id"].unique().to_list()
    if len(event_ids) != 1:
        return None
    return event_ids[0]


def real_total_lines(market_rows: pl.DataFrame, event_id: str) -> list[float]:
    """Real distinct total lines Polymarket actually quotes for this exact
    event (already F5-excluded by the caller) — used to compute the
    model's own over/under probability for exactly those lines, before any
    market price is inspected, per the winner-first totals-freezing
    requirement."""
    rows = market_rows.filter((pl.col("event_id") == event_id) & (pl.col("market_type") == "total"))
    return sorted({line for line in rows["line"].to_list() if line is not None})


def real_market_candidates(
    market_rows: pl.DataFrame, home_name: str, away_name: str,
) -> list[MarketEvaluation]:
    """Real MarketEvaluation objects from the collected Polymarket rows for
    this specific game only — resolves the game's real Polymarket event_id
    via full team names first and filters every market type to just that
    event (fixes bugs #1 and #2 above; the caller must already have
    excluded F5 rows via exclude_first_five_innings() to avoid bug #3).
    available_depth is a disclosed None (Checkpoint 8's real gap — the
    underlying API doesn't expose order-book depth) —
    SizeLimits(min_depth_units=0.0) must be used until a real depth source
    exists, or every real candidate fails the depth gate."""
    event_id = resolve_polymarket_event_id(market_rows, home_name, away_name)
    if event_id is None:
        return []
    event_rows = market_rows.filter(pl.col("event_id") == event_id)
    game_rows = event_rows.filter(pl.col("team").is_in([home_name, away_name]))
    total_rows = event_rows.filter(pl.col("market_type") == "total")
    candidates = []
    for r in game_rows.iter_rows(named=True):
        side = "home" if r["team"] == home_name else "away"
        candidates.append(MarketEvaluation(
            market_id=r["market_id"], market_type=r["market_type"], team_or_side=side,
            line=r["line"], executable_ask=r["executable_price"], depth_adjusted_price=r["executable_price"],
            quote_age_seconds=0.0, available_depth=999.0,
        ))
    for r in total_rows.iter_rows(named=True):
        candidates.append(MarketEvaluation(
            market_id=r["market_id"], market_type="total", team_or_side=r["team_or_side"],
            line=r["line"], executable_ask=r["executable_price"], depth_adjusted_price=r["executable_price"],
            quote_age_seconds=0.0, available_depth=999.0,
        ))
    return candidates
