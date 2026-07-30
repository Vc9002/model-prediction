# Ledger routing: which sport/market goes into which ledger

This is the fixed, operator-directed convention (2026-07-31) for which of
this project's four ledger types a given sport/market ends up in. Verified
directly against the real routing code in `cli.py` (`_forecast_learned_sport`,
`_forecast_mlb_totals_flat`, `_forecast_soccer_sport`, `_log_esports_forecast`,
`_forecast_international_sport`) and `domain.py`'s `PRODUCTION_SPORTS`/
`LEARNED_PRODUCTION_SPORTS` constants, not just described from memory --
re-check those if this file and the code ever disagree, and fix whichever is
wrong.

## The four ledgers

| Ledger | File(s) | Real position sizing? |
|---|---|---|
| **Main** | `data/picks.xlsx` | Yes -- the only ledger real execution ever reads from |
| **Flat** | `data/flat_picks.xlsx` | No -- zero-unit diagnostic sizing, real games only, no edge gate |
| **Research** | `data/research/{sport}.xlsx` (one file per sport/title) | No -- zero-unit, every priced candidate |
| **Gated Research** | `data/gated_research/{sport}.xlsx` (one file per sport/title) | No -- zero-unit, but only the subset of Research that clears that sport's own edge/confidence bar |

## Main (`picks.xlsx`)

**MLB and WNBA moneyline only** (`domain.py`'s `PRODUCTION_SPORTS = ("mlb",
"wnba")`) -- the only two sports promoted to "production" status by explicit
operator decision. NBA and NFL use the identical model/feature pipeline
(`learned_forward.py`'s `build_learned_moneyline_slate`, shared by all four
`DAILY_LEARNED_SPORTS`) and are genuinely strong (NBA ~74%, NFL ~71% real
holdout hit rate), but have not been promoted to Main -- they route to Flat
only (below).

As of 2026-07-30/31: **no gate hides a candidate from Main.** The model's own
learned confidence threshold and the min-edge-vs-executable-ask check both
used to silently skip a candidate before it ever reached the ledger; both
were removed per operator direction (every real forecasted game becomes a
real, sized Main-ledger call; both numbers are still recorded in `reason`/
rationale for a human to review before deciding whether to actually place
the bet). This is a deliberate, different choice from Gated Research's
philosophy below.

MLB spread/total (Measured Edge) is **never** logged to Main -- see Flat.

## Flat (`flat_picks.xlsx`)

Two independent sources, both zero-unit, both logged unconditionally (no
edge gate, ever -- that is the entire point of Flat: every model opinion for
every real game, regardless of confidence or price):

1. **Moneyline for all four `DAILY_LEARNED_SPORTS`** (MLB, NBA, WNBA, NFL) --
   re-logs the same computed candidates from the Main-ledger forecast pass,
   in `flat_mode=True`, which bypasses the `PRODUCTION_SPORTS` restriction.
   This is why NBA/NFL rows can appear in Flat but never in Main.
2. **MLB spread and totals** (Measured Edge margin-v1/totals-v1 Monte Carlo
   models) -- `_forecast_mlb_totals_flat`, paired output from `build_mlb_slate`
   filtered to `SPREAD`/`TOTAL` only (the moneyline third of that same call is
   discarded here since MLB moneyline is already served live by
   `learned_forward.py` above -- not duplicated).

Nothing in Flat is ever staked; `record_type`/`units` reflect that regardless
of how confident or well-priced a given row looks.

## Research (`data/research/{sport}.xlsx`)

Every other model this project runs, one workbook per sport/title
(`cli.py`'s `RESEARCH_ONLY_DAILY_SPORTS`):

- **Soccer** (Poisson-Dixon-Coles: moneyline, totals, BTTS)
- **Tennis** (WTA surface Elo -- Polymarket US has no ATP market, ESPN has
  no ITF scoreboard, so WTA is the only real coverage)
- **Esports**: LOL, CS2, Dota 2, Valorant, Rainbow Six (neutral series Elo)
- **KBO, NPB** (tie-aware Elo) -- currently priced zero real events: Polymarket
  does not list KBO/NPB markets at all (confirmed repeatedly across many real
  days; a platform coverage gap, not a wiring bug)

Every priced candidate is logged here regardless of edge or confidence --
Research is the "everything the model looked at" record, analogous to what
Flat is for the four learned-moneyline sports above.

## Gated Research (`data/gated_research/{sport}.xlsx`)

The curated subset of Research: only rows that clear that sport's own
`min_edge` and `research_confidence_gate` (checked in
`evaluate_gated_research_eligibility`) get mirrored here. This is the "the
model says this pick is actually valid" tier -- deliberately still tightened
by design, unlike Main above.

Tightened 2026-07-31 for esports specifically, since real settled Gated
picks were performing *worse* than unfiltered Research in every title
(`research_confidence_gate` had been left at `0.0`, barely filtering
anything). Now set per title to that title's own already-validated
`confidence_threshold` from its artifact:

| Title | `research_confidence_gate` |
|---|---|
| LOL | 0.05 |
| CS2 | 0.03 |
| Dota 2 | 0.05 |
| Valorant | 0.05 |
| Rainbow Six | 0.03 |

Soccer and tennis's Gated Research is often empty on a given day -- checked
directly, this is real: `min_edge` (0.05 for soccer) is a genuinely hard bar
against an efficiently-priced market (e.g. a full-game 2.5 total), not a
wiring bug. KBO/NPB's Gated Research is always empty for the same reason
Research is: no real market data exists to price against.

## Why Main and Gated Research take opposite philosophies

This is intentional, not an inconsistency -- confirmed explicitly by the
operator (2026-07-31):

- **Main (MLB/WNBA)**: "show me everything, I decide." No automated gate
  hides a real pick from you; you are the final filter.
- **Gated Research (esports/soccer/tennis/KBO/NPB)**: "Research shows
  everything, Gated should mean something." The curated tier is deliberately
  tightened so it stays a meaningful signal on its own, since these sports
  don't have a Main-ledger equivalent a human is expected to manually
  re-filter the same way.
