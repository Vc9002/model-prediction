# Pre-registration: ncaab-spread-v7 afternoon vs evening tips

Registered 2026-11-25, before any before/after-5 PM split of this cohort was computed. Nothing in
this document may change after the first look at the split; a change needs a new, dated
registration that says what changed and why.

## Question

Do `ncaab-spread-v7` picks in the live cohort's saved-edge band (5% to under 12%) perform worse
when the game tips before 5 PM ET than when it tips at or after 5 PM ET?

The live auto-buyer cohort `ncaab-spread-v7:spread:[0.05,0.12):live` buys these picks with real
money, so a real time-of-day effect would change which games it buys.

## Population (fixed)

- **Model:** `model_id` exactly `ncaab-spread-v7`. Rows carrying any other id, including the
  `ncaab-spread-v6` id written 11-16 to 11-21, are excluded and never pooled.
- **Date:** games whose tip, in US Eastern time, falls on 2026-11-21 or later.
- **Ledger:** the flat ledger (`ledger_records.ledger_tier = 'flat'`), market `spread`,
  `decision = 'CALL'`.
- **Band:** the pick's saved edge (`ledger_records.edge`, the edge the forecast saved at decision
  time) satisfies `0.05 <= edge < 0.12`. The live-ask edge at buy time is not used to define the
  band. It is reported as a secondary column only.
- **Executable quotes:** `market_quote_provenance` is `archived_two_sided_bbo` or
  `decision_time_executable_quote`, and `market_quote_timestamp_valid` is true. `indicative_slate`
  rows and sportsbook-priced rows are excluded.
- **One row per game:** if a game has several surviving settled rows, keep the one with the
  latest `created_at_utc` that is still before tip. Voided rows are excluded.

## Split (fixed)

- One cut at 17:00:00 US Eastern time, applied to the game's scheduled tip (`event_start_utc`
  converted with the `America/New_York` zone, so DST is handled).
- **Afternoon:** tip strictly before 17:00:00 ET.
- **Evening:** tip at or after 17:00:00 ET. A 17:00:00 tip is evening.
- Every tracking record carries this as `tip_window` (`before_5pm_et` or `5pm_or_later_et`), with
  `tip_et` and `edge_bucket`, so the split can be recomputed from the records alone.

## Metrics (fixed)

1. **W/L:** a win is a covered spread for the selected side. Pushes are counted and reported but
   left out of the win rate, which is `wins / (wins + losses)`.
2. **Cover margin, in points:** the selected side's final margin plus the line it was picked at.
   Positive means it covered by that many points, so a push is 0.

Prices in every report are cents or percentages, never American odds.

## Sample size and looks

- **Target:** 150 afternoon picks meeting every rule above. No decision is made before the target
  is reached. Interim reports may show the counts, but are labelled "interim, no decision".
- If the target is not reached by 2027-02-28, report what exists as inconclusive and decide
  whether to extend, in a new registration.

## Decision rule (fixed)

At the target, compute:

- the afternoon win rate minus the evening win rate, with a two-sided two-proportion z-test;
- the afternoon mean cover margin minus the evening mean cover margin, with a two-sided Welch
  t-test.

**Conclude that afternoon tips are worse** only if all three of these hold:

1. both differences are negative;
2. both p-values are below 0.05;
3. the afternoon win rate is below the cohort's break-even win rate. That rate is the average
   entry price of the afternoon picks' executable decision asks, about 51%.

If all three hold, propose to the operator, as a separate, governed decision, that the live cohort
skip pre-5 PM tips. The paper twin keeps buying them so the effect can keep being measured.

Any other outcome is "no time-of-day effect shown", and the live cohort is unchanged. A result in
the opposite direction (afternoon better) is reported but changes nothing.

## Secondary (reported, never decisive)

- The same split on the live book's actual buys (`auto_buyer_ledger.jsonl`, cohort rows), with
  fill prices and fee-inclusive dollar P&L.
- The same split on the paper twin's buys.
- CLV at the pre-tip close (`clv_cents`, `clv_mid_cents`), restricted to closes observed at least
  5 minutes after the buy and before tip.
- The live-ask edge band (5% to under 12% at buy time).

## Data sources

- Picks and results: `ledgers/ledgers.db` (`ledger_records`), read-only.
- Buys: `auto_buyer_ledger.jsonl` (live) and `paper_auto_buyer/auto_buyer_ledger.jsonl` (paper).
- Tracking records: `logs/auto_buyer_live_fills.jsonl` and one skip-reason file per tip date,
  `logs/auto_buyer_skip_reasons/<ET date>.jsonl`, plus `python -m model_prediction.cli auto-buyer-report --date <ET date>` (or the
  `model-prediction auto-buyer-report` console script). All paths are under the runtime root.
