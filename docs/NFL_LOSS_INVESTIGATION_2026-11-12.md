# NFL loss investigation and improvement options — 2026-11-12

Follow-up to `docs/MODEL_REVIEW_2026-11-11.md` and the NFL v7 replay
(`outputs/nfl/nfl_v7_replay.md`). Question asked: where do the NFL losses come
from, and what would actually improve the NFL models? Evidence is the runtime
ledger (288 settled NFL rows, all tiers) and a per-game dump of the 1,489-game
point-in-time replay with every engine component broken out
(`scripts/nfl_replay_dump_rows.py` / `scripts/nfl_replay_residuals.py`, built on
`scripts/replay_nfl_v7_all_settled.py`'s loaders). No code was changed for this
document; every item below is an operator decision or a research task.

## 1. Where the settled losses are

Settled NFL rows by market (Main and Flat both count a pick, so a Main pick
appears twice):

| market | rows | hit | units | note |
|---|---|---|---|---|
| spread | 88 | 43.2% | −29.3 | 64 rows are the retired v5 engine; 23 are v7 |
| total | 76 | 53.9% | +10.0 | 51 v5, 25 v7 |
| moneyline (`nfl-elo-trend-lr-v4`) | 124 | 63.7% | +12.6 | see §4: the plus is an artefact |

Grading was audited: the ledger's `line` is the selection's own line, and all 88
spread rows regrade correctly from the stored scores. The losses are real, not
settlement errors.

### Spread losses: underdogs, especially home underdogs

| selection | rows | hit | units |
|---|---|---|---|
| home | 46 | 26.1% | −36.6 |
| away | 42 | 61.9% | +7.3 |
| underdog (any side) | 65 | 43.1% | −22.8 |
| favourite | 23 | 43.5% | −6.5 |

Two separate causes:

- **Retired v5 engine (64 rows, −11.8u).** It assigned 68–77% cover
  probabilities to home underdogs (TEN +8.5 at 77.2%, MIA +6.5 at 72.6%, ATL
  +1.5 at 75.9%, TB +2.5 at 74.6%, ARI +5.5 at 70.2%, NO +3.5 at 68.1%); all
  six lost. Home dogs under v5: 9 of 24. v5 is no longer served; these rows
  are history, not a live problem.
- **v7 defaulted-input window (23 rows, −16.2u, 10-27 → 11-12).** With every
  team at league-average efficiency the blend reduces to
  `0.8 × market + 0.2 × (HFA 1.35 + QB tier gap)`, which always shrinks the
  line toward zero, so the engine took the dog in 17 of 23 picks, 7+ point
  dogs went 1 of 8. Fixed 2026-11-11 (`03faf30d`, fail-closed inputs); the
  00:30 ET run on 11-12 built a full 20-column snapshot and priced Thursday's
  game on real inputs (spread 50.0%, total 50.5%, weather 12 mph / 50 °F).

Edge was anti-informative in both engines: picks with recorded edge ≥5% hit
31.8% (44 rows, −28.8u); picks under 3% edge hit 54.8%.

### Total losses: none systematic

Totals are +10.0u overall. The one losing pocket is lines ≥48 (0 of 9) and the
"edge ≥12%" bucket (5 of 17); both are v5 rows. v7 totals are 15 of 25.

## 2. What the replay says about the v7 engines (1,489 games, 2021–2026)

The replay regresses the market residual (actual margin minus the closing
line, home-positive) on each engine component, so each component's coefficient
is the weight the data supports; the engine's own weight is 0.20 for the
structural terms and 1.0 for EDSR / turnover-luck.

### Spread: the structural signal carries no information at the closing line

| regressor | coef | t | engine weight |
|---|---|---|---|
| engine margin − market margin | −0.13 | −0.35 | 1.00 |
| structural margin − market margin | −0.06 | −0.77 | 0.20 |
| pass-EPA points | −0.08 | −0.81 | 0.20 |
| rush-EPA points | +0.61 | +1.35 | 0.20 |
| pressure disparity | +2.20 | +0.87 | 0.20 |
| QB tier gap | −0.02 | −0.13 | 0.20 |
| rest | +0.27 | +0.74 | 0.20 |
| EDSR | +1.67 | +0.99 | 1.00 |
| turnover luck | +2.75 | +0.93 | 1.00 |

R² of the full component model is 0.5%. No component is significant; the
engine's disagreement with the market points, if anything, the wrong way. The
engine favours the underdog in 64.9% of games (its side hits 52.7% vs 51.1% for
"always take the dog"). Brier variants:

| variant | Brier |
|---|---|
| engine as served | 0.2496 |
| market only (50% everywhere) | 0.2500 |
| 90/10 market/structural blend | 0.2498 |
| structural only | 0.2618 |

The 16 edge ≥5% fires are driven by the two components with the largest
dispersion, pass-EPA (sd 4.0 pts) and QB tier gap (sd 2.6 pts); both have zero
correlation with the residual (+0.003 and −0.007). Fires hit 37.5%.

Two parameter facts the data contradicts:

- **Margin sd.** Empirical residual sd is 12.8 points flat; the engine uses
  13.8, and 17.25 for |line| ≥ 8. Heavy lines have residual sd 12.6 and dogs
  cover 49.6% of them, so the 1.25× "blowout variance" inflation is
  unfounded. It only shrinks probabilities toward 50% (Brier-neutral), but it
  is a fiction in the artifact.
- **Home bias.** Mean residual is +0.48 points home (t = 1.4). Not bettable.

### Total: weather is the only component with information

| regressor | coef | t | engine weight |
|---|---|---|---|
| weather penalty (recorded wind/temp) | +1.72 | +2.54 | 1.0 |
| dome boost | +1.97 | +2.02 | 1.0 |
| rush EPA | +10.1 | +1.72 | 1.0 |
| pass EPA | +1.15 | +0.90 | 1.0 |
| red-zone | +1.31 | +1.12 | 1.0 |
| explosive | −2.78 | −1.18 | 1.0 |
| pace | −0.84 | −1.04 | 1.0 |
| QB | −0.41 | −0.98 | 1.0 |
| high-total drag | +0.57 | +1.04 | 1.0 |

Engine with recorded weather: Brier 0.2506; without weather 0.2517; the
no-weather variant's disagreement with the market has coefficient 0.01. The
weather term is under-weighted: relative to the +0.97 baseline for calm
games, 12–14 mph games run −2.5 points (engine −0.5), 14–18 mph −3.5 (engine
−1.2), 18+ mph −3.3 (engine −2.5). Unders in recorded wind ≥12 mph hit 60.7%
(206 games, +32.6u at the 52.38% standard lay), but the effect is entirely
2021–2023; 2024 and 2025 are 47%. Two caveats before anyone trades it: the
replay's wind is nflverse's recorded game-time wind, not a pregame forecast,
and the market may have repriced wind since 2023. Domes run +1.6 points over
the line but the over rate is 50.9% — not an edge. Temperature below 32 °F
shows no under effect (46.9%). The engine takes the over in 78% of dome games
and 62% of all games; its over side hits 49.6%, its under side 53.3%.

## 3. Infrastructure gaps found while looking

- **No closing line for any NFL row, so CLV is unmeasurable.** Of 113
  Polymarket-priced spread/total rows, `closing_line` is populated in 0,
  `decision_consensus_probability` in 0, and `probability_clv` is "0.000000"
  where present. Settlement's `market_store.closing_quote` is the MLB odds
  store (`data_sources/mlb_market_odds.py`), which has no NFL rows. For a
  market-anchored engine CLV is the only fast diagnostic; it is missing.
- **v7 rationale strings still say "NFL Structural v6"**
  (`cli/forecast.py:2876`, `:2978`, `:3498`, `:3594`). Cosmetic, but it is
  what the dashboard shows.

## 4. Moneyline champion `nfl-elo-trend-lr-v4`

- **Its +12.6u is an artefact of placeholder pricing.** 50 settled rows carry
  `market_probability = 0.52381` (the standard lay) with sportsbook
  `model_opinion_no_executable_quote`, no closing price, and no consensus
  price; they are 34 of 50 and +22.6u *because favourites were paid as if they
  were coin flips*. At real quotes (74 rows) the model is −10.1u: Main 7 of 14
  (−4.3u), Flat 38 of 60 (−5.8u).
- **Its edge is anti-informative at real prices.** Flat picks with model edge
  ≥5% went 4 of 15 (−9.4u); picks where the model was *below* the market went
  28 of 36 (+3.1u). A joint logit of outcome on model and market probability
  gives model −0.27 (se 1.40) and market +2.44 (se 0.77). The model adds
  nothing the price does not already say.
- **Its "predictively_qualified" evidence was never market-priced.** The
  artifact's qualification block scores 62 of 87 holdout calls at a flat
  52.38% lay (`market_inputs_used: false`, `every_called_month_positive_at_
  minus_110`). That is the same fiction as the 50 placeholder rows.
- **Governance leak, closed 11-01.** Eight Main rows (10-25, 11-01) reached
  Main as `QUALIFIED`, 1.0 unit, at the placeholder price because the
  quote-unavailable downgrade kept `decision="CALL"` and `ledger.append_
  evaluated`'s Main branch relabels every CALL to QUALIFIED (`ccc842b6`,
  2026-10-23). `787b8e36` (2026-11-01 19:05 ET) made the downgrade a NO_CALL
  for Main; no placeholder-priced Main row exists after it. Since then there
  have been zero Main moneyline rows at all: Polymarket NFL moneyline quotes
  are rarely matched, and the fallback is ESPN DraftKings consensus, which
  only Flat accepts.

## 5. What would actually improve NFL

Ordered by evidence, not effort. Each is an operator decision.

1. **Stop treating the structural spread signal as edge.** At the closing
   line it has none (§2). Options: (a) demote `nfl-spread-structural-v7`
   from `predictively_qualified`; (b) keep it serving but raise the Main edge
   hurdle so it effectively never fires on structural disagreement, and let
   Main NFL spread picks come only from *price* edge, i.e. a Polymarket ask
   that is cheaper than the sharp consensus for the same line; (c) test the
   signal where inefficiency can exist, against opening or early-week lines,
   which requires a line-history source nflverse does not provide.
2. **Build the NFL closing-line capture and CLV** before any further model
   work. Store the consensus line and Polymarket ask at kickoff per event and
   market, populate `closing_line` / `probability_clv` at settlement. Without
   it there is no way to tell variance from a bad price.
3. **Re-fit the total engine around weather and drop the dead terms.** Weather
   is the only term with information and it is under-weighted roughly 2–3×.
   Validate on the Open-Meteo *forecast* captures (live since 11-11), not
   recorded wind, and walk-forward with 2024–2025 as the test block because
   the raw effect weakened there. Pace, explosive, red-zone, QB and the
   high-total drag have no support and widen the probabilities for nothing.
4. **Fix the spread artifact's sd.** 12.8 flat; remove the heavy-line 1.25×.
   Brier-neutral but it stops the engine claiming a variance it cannot show.
5. **Moneyline: re-qualify `nfl-elo-trend-lr-v4` against real prices or
   retire it.** Its only market-priced record is −10u on 74 picks with
   inverted edge. Its `evidence_status` in `config/production.yaml` is
   unsupported. If it stays, the 50 placeholder-priced rows should be
   excluded from any performance view.
6. **Cosmetic:** rename the "v6" rationale strings.

## 6. Reproduction

```
python scripts/nfl_replay_dump_rows.py <out.json>   # per-game rows + components
python scripts/nfl_replay_residuals.py <out.json>   # regressions and buckets
```

Both scripts import `scripts/replay_nfl_v7_all_settled.py`'s loaders, so the
cohort, PIT inputs and sign convention are identical to the committed replay.
Ledger queries read `E:\model-prediction-runtime\ledgers\ledgers.db`
(`ledger_records`, `lower(sport)='nfl' and status='settled'`).
