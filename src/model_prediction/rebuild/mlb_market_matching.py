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

import json
from dataclasses import asdict
from datetime import datetime

import polars as pl

from .decision import MarketEvaluation
from .storage import sha256_hex, utc_now


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


def real_spread_line_side_pairs(market_rows: pl.DataFrame, event_id: str) -> list[tuple[float, str]]:
    """Real (line, side) pairs Polymarket actually quotes for this event's
    spread markets. Each side of a real spread market carries its own
    signed line — a home favorite's side is quoted at e.g. -1.5 while the
    away side of that same market is quoted at +1.5, not a single shared
    line like totals' over/under. `probability_for_market(pred, "spread",
    side, line)` needs that side's own signed line to compute the right
    cover probability, so callers must compute one probability per real
    (line, side) pair, not one per distinct line value."""
    rows = market_rows.filter((pl.col("event_id") == event_id) & (pl.col("market_type") == "spread"))
    pairs = {
        (r["line"], r["team_or_side"])
        for r in rows.select(["line", "team_or_side"]).iter_rows(named=True)
        if r["line"] is not None and r["team_or_side"] in ("home", "away")
    }
    return sorted(pairs)


def real_quote_age_seconds(observed_at_utc: str | None, *, now: datetime | None = None) -> float:
    """Real elapsed time since a quote was actually observed — every market
    row already carries a real `observed_at_utc` (see provenance_row in
    storage.py), so this was never blocked on a missing data source the way
    depth is. The previous hardcoded `quote_age_seconds=0.0` silently
    claimed every quote was captured at the exact instant of evaluation,
    which was never true and made the staleness gate in decision.py's
    `_quote_gate_reason()` unable to ever fire for a real reason. Returns a
    large sentinel (so the staleness gate fails closed) rather than 0.0 for
    a missing/unparseable timestamp — a quote with unknown age must not be
    treated as fresh.
    """
    if not observed_at_utc:
        return float("inf")
    try:
        observed = datetime.fromisoformat(observed_at_utc)
    except ValueError:
        return float("inf")
    reference = now or utc_now()
    return max(0.0, (reference - observed).total_seconds())


def real_market_candidates(
    market_rows: pl.DataFrame, home_name: str, away_name: str,
) -> list[MarketEvaluation]:
    """Real MarketEvaluation objects from the collected Polymarket rows for
    this specific game only — resolves the game's real Polymarket event_id
    via full team names first and filters every market type to just that
    event (fixes bugs #1 and #2 above; the caller must already have
    excluded F5 rows via exclude_first_five_innings() to avoid bug #3).

    quote_age_seconds is now real (see real_quote_age_seconds() above).
    available_depth is honestly unavailable (Checkpoint 8 / reviewer-flagged
    gap: the underlying Polymarket source doesn't expose order-book depth,
    unlike age, which was always computable from data already collected).
    Real bug fixed here: this previously set available_depth=999.0, a
    fabricated value that trivially cleared decision.py's default
    min_depth_units=1.0 gate on every single candidate — the INSUFFICIENT_
    DEPTH reason code existed but could never actually fire. Per CLAUDE.md
    Part 3 §2 ("mark depth_available=false; do not fabricate depth; ...
    fail economic qualification"), every real candidate now sets
    depth_available=False, which makes decide_team_market()/decide_total()
    correctly return NO_BET/INSUFFICIENT_DEPTH for every real market until
    a genuine depth-providing source is integrated — that is the honest,
    correct behavior given real data scarcity, not a bug to work around
    with SizeLimits(min_depth_units=0.0)."""
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
            quote_age_seconds=real_quote_age_seconds(r.get("observed_at_utc")), available_depth=0.0,
            depth_available=False,
        ))
    for r in total_rows.iter_rows(named=True):
        candidates.append(MarketEvaluation(
            market_id=r["market_id"], market_type="total", team_or_side=r["team_or_side"],
            line=r["line"], executable_ask=r["executable_price"], depth_adjusted_price=r["executable_price"],
            quote_age_seconds=real_quote_age_seconds(r.get("observed_at_utc")), available_depth=0.0,
            depth_available=False,
        ))
    return candidates


def real_market_snapshot_hash(event_id: str, candidates: list[MarketEvaluation]) -> str:
    """A real content hash of exactly the market evidence a decision was
    made from, for use as the shadow ledger's trade_decisions idempotency
    key. Real bug found and fixed here: `quote_age_seconds` is deliberately
    excluded -- it's `now - observed_at_utc` (see real_quote_age_seconds()
    above), which increases every single second purely from wall-clock time
    passing, even when the underlying book hasn't moved at all. Hashing it
    in meant an immediate rerun against byte-identical market data always
    produced a different hash, defeating the idempotency guarantee: a real
    2-game rerun with unchanged books produced 32 duplicate trade_decisions
    rows instead of 0 before this fix."""
    payload = {
        "event_id": event_id,
        "candidates": [
            {k: v for k, v in asdict(c).items() if k != "quote_age_seconds"}
            for c in candidates
        ],
    }
    return sha256_hex(json.dumps(payload, sort_keys=True, default=str).encode("utf-8"))
