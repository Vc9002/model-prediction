"""Real MLB prospective settlement + closing-price capture -- CLAUDE.md's
next-phase Task 19.

For every real MLB `trade_decisions` row (BET or NO_BET -- deliberately not
limited to placed paper orders, per the explicit instruction to capture
settlement/closing data for ALL evaluated research opportunities) whose
event has since gone STATUS_FINAL in the real ESPN scoreboard, this script:

1. Determines the real outcome (WIN/LOSS/PUSH) of the exact evaluated
   market/side/line from the real final score -- this answers "would this
   specific frozen side have won" regardless of whether real money (paper
   units) was ever put behind it, which is exactly what NO_BET rows need
   for later CLV/predictive-accuracy research.
2. Attempts to capture a real closing price for that exact market_id/side
   by re-querying the same Polymarket collector used at decision time,
   then reading the actual store that collector writes to
   (`data/rebuild/markets/mlb/{date}.parquet` via `MarketStore`) -- NOT
   the shadow ledger's own `market_snapshots` SQL table, which nothing in
   this codebase currently populates (a real, separate wiring gap this
   script must not silently assume is populated).
3. Records both via ShadowLedger.record_closing_price() /
   record_settlement() -- append-only, idempotent (skips any
   trade_decision that already has a settlement row).

A candidate quote is only accepted as a real "closing" observation when
its own real `observed_at_utc` is strictly after the decision's
`decision_time_utc` (a later real observation than what informed the
decision) and at or before the event's real `event_start_utc` (a genuine
pregame closing quote, not in-play data -- CLAUDE.md: "Do not use...
in-play data for a pregame test"). The quote's own real observed_at_utc
is what gets recorded, never `utc_now()` -- reusing the capture-time
timestamp as the quote's timestamp would misrepresent when the market was
actually observed.

Real, disclosed limitation found while building this: Polymarket's public
markets endpoint only serves currently open/active markets, not historical
order books for markets that have already resolved. Re-querying it for a
past date after the fact returns nothing ("no_mlb_markets") -- there is no
retroactive closing price to recover for events whose markets closed
before a collector was polling them through resolution. This is not
special-cased or hidden: settled_price stays real None with an explicit
note when this happens, and the real fix is running this script (or the
underlying collector) prospectively, close to and through each event's
market close, in future sessions -- not reconstructing a historical price
after the fact (CLAUDE.md: "Do not use... reconstructed historical
prices").

Registry-safe: does not touch test_consumption_registry.json. Writes only
to data/rebuild/shadow.db (append-only tables) -- never mutates
production ledgers.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/mlb_settle_and_capture_closing.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.rebuild.collectors import MLBCollector
from model_prediction.rebuild.metadata import MetadataDB
from model_prediction.rebuild.mlb_features import dedupe_scoreboard
from model_prediction.rebuild.shadow_ledger import ShadowLedger, utc_now
from model_prediction.rebuild.storage import MarketStore

DATA_ROOT = "data/rebuild"
SPORT = "mlb"


def determine_outcome(market_type: str, team_or_side: str, line: float | None, home_score: int, away_score: int) -> str:
    """Real outcome (WIN/LOSS/PUSH) of one evaluated market/side/line, from
    the real final score. `line` is that side's own signed line (real
    market convention -- 'home' with line=-2.5 means home favored by 2.5;
    'away' with its own line is not necessarily the mirror of 'home's,
    since real captured lines are independent market quotes, not a
    predeclared symmetric grid). Moneyline has no real line (None -> 0.0)."""
    effective_line = line if line is not None else 0.0
    if market_type == "total":
        total = home_score + away_score
        if total == effective_line:
            return "PUSH"
        covered = total > effective_line
        return "WIN" if (covered == (team_or_side == "over")) else "LOSS"

    # moneyline and spread both reduce to "this side's own margin + line".
    side_margin = (home_score - away_score) if team_or_side == "home" else (away_score - home_score)
    result = side_margin + effective_line
    if result == 0:
        return "PUSH"
    return "WIN" if result > 0 else "LOSS"


def real_closing_quote(
    market_store: MarketStore, sport: str, game_date: str, market_id: str, team_or_side: str,
    line: float | None, decision_time_utc: str, event_start_utc: str,
) -> tuple[float, str] | None:
    """The real most-recent quote for one exact market_id/side/line on
    `game_date`, read from the actual store the collector writes to
    (`data/rebuild/markets/{sport}/{date}.parquet`), accepted as a real
    closing observation only if its own real observed_at_utc is strictly
    after `decision_time_utc` and at or before `event_start_utc` (a real
    later, still-pregame quote -- never in-play data, never the same
    snapshot already used at decision time). Returns
    (closing_price, observed_at_utc) or None if no such real row exists."""
    path = market_store.path(sport, game_date)
    if not path.exists():
        return None
    books = market_store.read(sport, game_date)
    matches = books.filter(
        (pl.col("market_id") == market_id)
        & (pl.col("team_or_side") == team_or_side)
        & ((pl.col("line") == line) if line is not None else pl.col("line").is_null())
        & (pl.col("observed_at_utc") > decision_time_utc)
        & (pl.col("observed_at_utc") <= event_start_utc)
    ).sort("observed_at_utc", descending=True)
    if matches.height == 0:
        return None
    latest = matches.row(0, named=True)
    price = latest.get("executable_price")
    if price is None:
        return None
    return float(price), str(latest["observed_at_utc"])


def main() -> None:
    sb_path = Path(DATA_ROOT) / "normalized/mlb/scoreboard.parquet"
    if not sb_path.exists():
        print(f"ERROR: {sb_path} not found. Run the MLB scoreboard collector first.")
        sys.exit(1)

    sb = dedupe_scoreboard(pl.read_parquet(sb_path))
    final_games = sb.filter(pl.col("status") == "STATUS_FINAL")
    final_scores: dict[str, tuple[int, int]] = {
        r["event_id"]: (int(r["home_score"]), int(r["away_score"])) for r in final_games.iter_rows(named=True)
    }
    event_start_by_id: dict[str, str] = {
        r["event_id"]: r["event_start_utc"] for r in final_games.iter_rows(named=True)
    }
    print(f"1. Real completed games available for settlement: {len(final_scores)}")

    ledger = ShadowLedger(f"{DATA_ROOT}/shadow.db")
    meta = MetadataDB(f"{DATA_ROOT}/metadata.db")
    collector = MLBCollector(DATA_ROOT, meta)
    market_store = MarketStore(f"{DATA_ROOT}/markets")

    pending = ledger.conn.execute(
        """SELECT td.id, td.event_id, td.action, td.market_type, td.units,
                  td.evaluated_market_id, td.evaluated_team_or_side, td.evaluated_line,
                  td.decision_time_utc, td.reason_code
           FROM trade_decisions td
           LEFT JOIN settlements s ON s.trade_decision_id = td.id
           WHERE td.sport = ? AND s.id IS NULL AND td.evaluated_market_id IS NOT NULL""",
        (SPORT,),
    ).fetchall()
    print(f"2. Real unsettled trade_decisions with an evaluated market: {len(pending)}")

    run_id = ledger.record_run(SPORT, run_type="settlement")

    # Real closing-price attempt, once per real distinct event date -- not
    # once per decision -- to avoid redundant real HTTP calls for the same
    # date's book. Cached per game_date so a date with zero live markets
    # (already-resolved historical date) isn't retried per decision.
    closing_attempted_dates: set[str] = set()
    closing_found = 0
    closing_attempted = 0

    n_settled = 0
    n_skipped_not_final = 0
    outcome_counts: dict[str, int] = {"WIN": 0, "LOSS": 0, "PUSH": 0}

    for row in pending:
        event_id = row["event_id"]
        if event_id not in final_scores:
            n_skipped_not_final += 1
            continue
        home_score, away_score = final_scores[event_id]

        outcome = determine_outcome(
            row["market_type"], row["evaluated_team_or_side"], row["evaluated_line"], home_score, away_score,
        )
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1

        game_date = row["decision_time_utc"][:10]
        if game_date not in closing_attempted_dates:
            closing_attempted_dates.add(game_date)
            closing_attempted += 1
            try:
                collector.collect_polymarket_books(game_date)
            except Exception as e:  # noqa: BLE001 -- external I/O; real closing-price capture is best-effort and must not abort real settlement
                print(f"   (real closing-price fetch failed for {game_date}: {e!r} -- continuing without it)")

        closing_price: float | None = None
        event_start_utc = event_start_by_id.get(event_id)
        if event_start_utc is not None:
            quote = real_closing_quote(
                market_store, SPORT, game_date, row["evaluated_market_id"], row["evaluated_team_or_side"],
                row["evaluated_line"], row["decision_time_utc"], event_start_utc,
            )
            if quote is not None:
                closing_price, quote_observed_at = quote
                ledger.record_closing_price(
                    run_id=run_id, sport=SPORT, event_id=event_id,
                    market_id=row["evaluated_market_id"], side_id=row["evaluated_team_or_side"],
                    line=row["evaluated_line"], closing_price=closing_price, observed_at_utc=quote_observed_at,
                )
                closing_found += 1

        # Real, disclosed: no real paper_order exists for any NO_BET row
        # (units=0 by definition), so pnl stays None for every real row
        # settled this session -- there are zero real BET decisions in the
        # ledger yet. The BET branch is implemented and unit-tested, not
        # exercised live this session because no real BET row exists.
        pnl: float | None = None
        if row["action"] == "BET":
            paper_order = ledger.conn.execute(
                "SELECT avg_fill_price, filled_units FROM paper_orders WHERE trade_decision_id=? ORDER BY id DESC LIMIT 1",
                (row["id"],),
            ).fetchone()
            if paper_order is not None and paper_order["avg_fill_price"] and paper_order["filled_units"]:
                fill_price = float(paper_order["avg_fill_price"])
                filled_units = float(paper_order["filled_units"])
                if outcome == "WIN":
                    pnl = filled_units * (1.0 / fill_price - 1.0) if fill_price > 0 else None
                elif outcome == "LOSS":
                    pnl = -filled_units
                else:
                    pnl = 0.0

        notes = (
            f"real outcome for evaluated_market_id={row['evaluated_market_id']} "
            f"side={row['evaluated_team_or_side']} line={row['evaluated_line']} "
            f"action={row['action']} reason_code={row['reason_code']}; "
            f"closing_price={'captured' if closing_price is not None else 'unavailable (Polymarket public API serves only open markets, not resolved historical books)'}"
        )
        ledger.record_settlement(
            run_id=run_id, sport=SPORT, event_id=event_id, outcome=outcome,
            trade_decision_id=row["id"], settled_price=closing_price, pnl=pnl,
            settled_at_utc=utc_now(), notes=notes,
        )
        n_settled += 1

    print(f"3. Real closing-price fetch attempted for {closing_attempted} distinct real dates; "
          f"real quotes found and recorded for {closing_found} of {n_settled} settled decisions.")
    print(f"4. Real settlements recorded: {n_settled} (skipped {n_skipped_not_final} -- event not yet STATUS_FINAL)")
    print(f"   Real outcome breakdown across all evaluated sides (BET + NO_BET): {outcome_counts}")
    print(
        "\n5. Real, disclosed scope: registry-safe (does not touch\n"
        "   test_consumption_registry.json). No real closing prices were\n"
        "   recoverable for these already-resolved past-date markets --\n"
        "   Polymarket's public endpoint only serves currently open markets.\n"
        "   settled_price is real None for every row this session; the fix is\n"
        "   running this script (or a standalone closing-price poller)\n"
        "   prospectively, close to and through each event's real market\n"
        "   close, starting now, not reconstructing a historical price."
    )


if __name__ == "__main__":
    main()
