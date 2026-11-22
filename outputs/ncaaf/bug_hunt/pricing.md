# NCAAF pricing and ledger-row bug hunt, 2026 season (read-only)

Run 2026-11-21 against `E:/model-prediction-runtime/ledgers/ledgers.db` (opened read-only) and the
`odds/ncaaf/*/polymarket_snapshots.jsonl` archives: 170,557 quote records, every one parsed and hashed with the
serving code's lineage digest (`college_football.py:320-323`). Prices are given in cents or %.

## Scope and method

- **Rows.** 7,986 NCAAF rows in the ledger. 2,287 are live (status not superseded or removed). Of those, 37 are
  archived or carry a `void_reason`: the 34 rows archived on 11-21, and the three Arizona @ Kansas State 11-21
  rows. That leaves 2,250 rows checked. Of these, 732 are bound to a Polymarket quote: 722 by digest
  (`market_snapshot_record_id`) and 10 by rationale slug, taking the newest capture at or before `created_at_utc`.
  1,518 are priced off ESPN consensus.
- **Side derivation.** The side was derived two independent ways:
  1. the project resolver plus two-team title matching;
  2. the moneyline long-side mascot of the same Polymarket event, matched as a suffix of the ESPN display name.

  The two never disagreed.
- **Replay.** The current `_match_cfb_moneyline`, `_match_cfb_spread` and `_match_cfb_total` selectors were
  re-run on each structural-model row's decision-time archive window (`replay.py`). This separates bugs still live
  in the current code from legacy ones.
- **Scripts and outputs.** Everything is in `E:/MP/outputs/ncaaf/bug_hunt/`:
  - scripts: `load_rows.py`, `build_index.py`, `analyze.py`, `replay.py`, `latent.py`, `reconcile.py`, `espn_check.py`;
  - `rows.pkl`, `quotes.pkl` and `findings.pkl` are caches; rerunning the scripts regenerates them;
  - per-check CSVs: `f_<check>.csv`, plus `replay_diff.csv` and `findings_summary.json`.

## Findings, ranked

### 1. HIGH: 12 live settled rows priced on another game's quote (2 Main, 10 Flat). Legacy, missed by the 11-21 archive

The 11-21 integrity archive (DEBUG.md line 18) removed 23 rows priced on another game's quote. These 12 rows are
the same defect and are still live. Each quote's title names two different teams, and the ledger's event is
graded on the real game at the other game's price and line:

| pick_id | tier | model | created | row | quote used |
|---|---|---|---|---|---|
| cd201aa13391404d | **main** | v4 | 11-08 01:38Z | Texas State @ Oregon State spread away +7.5 @ 50c, +1.4U win | asc-cfb-librty-nmxst-2026-11-07-neg-7pt5 (Liberty vs. NM State) |
| 766fafab4f2946dc | **main** | v4 | 11-08 01:38Z | Texas State @ Oregon State over 60.5 @ 50c, +1.0U win | tsc-cfb-sill-indst-2026-11-07-total-60pt5 (S. Illinois vs. Indiana State) |
| 84a7435ae73447f8 | flat | v4 | 11-08 01:38Z | same as cd201aa1 | Liberty vs. NM State |
| 861618c1943a403a | flat | v4 | 11-08 01:38Z | same as 766fafab | S. Illinois vs. Indiana State |
| 1e8c93a5f4ce4a5a | flat | v4 | 11-08 01:38Z | Fresno State @ Utah State spread away +7.5 @ 50c, 2U | Liberty vs. NM State |
| aad4db471cab4f71 | flat | v4 | 11-08 01:38Z | Fresno State @ Utah State under 60.5 @ 99c | S. Illinois vs. Indiana State |
| 463e7d62b1c64de5 | flat | v4 | 11-07 21:11Z | Boise State @ Colorado State spread away +2.5 @ 94.5c | Morehead State vs. Marist |
| f8d737b1b8644e61 | flat | v4 | 11-07 21:11Z | Boise State @ Colorado State over 55.5 @ 49c | Central Connecticut State vs. Stonehill |
| 103bc4f68005402b | flat | v4 | 11-07 23:39Z | Sam Houston @ Jacksonville State spread home -2.5 @ 53.5c | Samford vs. East Tennessee State |
| 8321c28ba0ca456e | flat | v4 | 10-31 17:02Z | Purdue @ Penn State spread home +1.5 @ 50c, 2U | asc-cfb-penn-brwn (Penn vs. Brown) |
| e5a035615a50414e | flat | v3 | 10-24 23:02Z | Iowa State @ Arizona spread home +7.5 @ 51c, 2U | asc-cfb-iowa-minnst (Iowa vs. Minnesota) |
| 07fa23ae52954ada | flat | v3 | 10-24 14:28Z | Tennessee @ South Carolina spread away +5.5 @ 68c | asc-cfb-chsou-tenst (Charleston Southern vs. Tennessee State) |

- **Already reported.** The sibling settlement audit (`settlement.md`) lists all 12 of these pick_ids as
  collided-contract rows. They are cross-referenced here; this report adds only the pricing-side root cause.
- **PnL on these rows.** Grading was verified from the payload scores. Each row is graded against its own ESPN
  game's final score, but at a line and price taken from the other game. All 12 are wins. Archiving them would
  remove +2.40U from Main (2.4U staked) and +11.54U from Flat (14.4U staked).
- **How they were found.** Six independent checks flagged them, the first five each on one or more of the 12:
  - I1, title does not resolve to the row's teams: 12 rows;
  - I3, the quote's event start differs from the ledger's by 3.5 to 6 hours: 11 rows;
  - B5, `polymarket_us` row with no lineage: 9 rows;
  - T3, the quote's `timestamp_valid` is false: 9 rows;
  - Q1, ask sum 1.115 to 1.525: 8 rows;
  - one slug priced two different event_ids: Liberty/NM State and S. Illinois/Indiana State.
- **Root cause.** Before 32528f57 (2026-11-08 16:35 ET), `_match_cfb_spread` and `_match_cfb_total` accepted any
  quote that met either of two conditions:
  - its label matched one team; or
  - any word of 3 or more characters from the away name, and any from the home name, appeared in the title or slug.

  Common words such as "state", "southern" and "tennessee" therefore collided
  (`git show 32528f57^:src/model_prediction/models/college_football.py`, lines 499-523). The anchor side was then
  guessed from the label (lines 532-541).
- **Why the 11-07 rows reached Main.** The quote was captured after the other game's kickoff, so it had
  `timestamp_valid` false. The lineage builder returned None (`college_football.py:312-319`), and the contract's
  `observed_at_utc` fell back to the decision time (`:1297-1301`). The 12-hour staleness gate therefore saw a fresh
  quote.
- **Current code is clean on this.** Replaying the current selectors matches none of the 12 quotes, and 553 of
  553 v5/v6.2/v7 Polymarket rows reproduce exactly.
- **Fix.** Archive the 12 rows through `archive_settled_rows` with operator approval, as was done for the other 23.

### 2. MEDIUM, current code: stale rungs priced over fresh ones (33 rows on 11-21: 19 v5 including 4 Main, 14 v6.2)

At each decision, the selected spread or total rung's last capture was 2.0 to 8.4 hours older than the newest
capture of a sibling full-game rung on the same event. The capture had stopped re-polling the old rung after the
line moved.

- **Main rows:**
  - 2f9631bf1c074991: Wake Forest @ Georgia Tech spread -20.5 at 52c. Quote observed 05:45Z, decision 14:21Z. The
    -21.5 rung was quoted fresh at 14:08Z at 48c/52.5c.
  - 4ffe67d450974030: BYU @ Kansas over 47.5 at 49c. Quote observed 11:09Z; the 46.5 rung was fresh at 14:08Z.
  - ce415b9fe9ee40c0: Iowa @ Illinois over 40.5 at 51c. Quote observed 17:07Z; the 41.5 and 42.5 rungs were fresh
    at 19:04-19:10Z.
  - ae9b476ec5d34302 (open): ODU @ UConn over 48.5 at 49c. Quote observed 17:09Z; the 46.5 and 47.5 rungs were fresh
    at 19:06-19:11Z, both at 53c on the over.
- **Flat rows:**
  - v5: a58de82467cb4aef, 7c71b016ca1e4bea, b717ffb7314948aa, 42a52cb129334bff, 1694bbb6fdc04b9e,
    c9a8e3724b4f4ff1, 8c43019ce76040c4, fba7b51e7cc345cd, 28a72010f1894974, ddaa64fccb9a4bf8, f246cfb068624247,
    0e2c0f5cf626458f, 9a290e9d7d624a2c, ec430f9047d447e1, bebb81df21ae422f;
  - v6.2: 3fc62a7fd8be422c, 9a803e31934845d4, c0c100dc8a8f42a1, 04bf1aa638ee4fee, 2a4e387852a84747,
    c8dc1fb3f32b413a, d05537abd3604d77, 312c29484cc447e2, ec5a5ec5a37b40fa, f8cc730d8dad4036, 71ac52c884c54aa6,
    1d394e1f03494b20, e6b59d08855c4871, b2bacd26c9884fb2.
- **Root cause.** Three pieces combine:
  - `_load_decision_snapshot_rows` (`college_football.py:658-700`) bounds the window only below, at 00:00 ET of
    the decision day;
  - `_latest_capture_per_market` (`:636-655`) keeps the last capture of every slug ever seen that day;
  - `_match_cfb_spread` (`:497`) and `_match_cfb_total` (`:561-564`) rank rungs only by |long ask - 50c|.

  Ties are common, because asks sit on a half-cent grid and 49c ties 51c. In float, abs(0.52-0.5) == abs(0.48-0.5)
  and abs(0.51-0.5) == abs(0.49-0.5) are both exactly True. `min()` resolves a tie to the first
  slug in dict insertion order, which is the earliest-seen and so the oldest rung. Wake/GT, Vanderbilt/Florida,
  Colorado/Cincinnati and Iowa/Illinois are such ties. In the other cases the old rung was strictly the more
  balanced one.
- **Timing.** All 33 rows are from 11-21, the first slate after b87d6452 (the day-ahead capture and the
  decision-day floor) landed. No v5 row from 11-13 to 11-20 shows the pattern.
- **Impact.** Usually 1 to 3c of price, but the price is several hours old, which violates point-in-time freshness.
  The 12-hour staleness gate does not catch it.
- **Fix direction.**
  - Restrict candidates to rungs whose last capture is within a few minutes of the newest capture for that
    event and market, or break ties by recency.
  - Add an age bound relative to `decision_at`.

### 3. MEDIUM, current code, known pattern: Main has no executable-quote requirement

`genuinely_eligible` and `is_qualified_call` (`cli/forecast.py:5853-5857, 5910-5915`) never check the contract's
`sportsbook` or `market_quote_provenance`. A row priced off ESPN consensus therefore enters Main whenever its
edge passes.

- **Affected Main rows by model:**

  | model | Main rows on ESPN consensus |
  |---|---|
  | v5 | 15 (14 on 11-14, plus 7a2f37d4553245f6 on 11-20) |
  | v4 | 57 |
  | v3 | 11 |
  | v2 | 103 |
  | cfb-spread-v1, cfb-total-v1, college-football-v1 | all 830 Main rows |

  - The 11-20 v5 row, 7a2f37d4553245f6, is Oregon @ Michigan State over 42.5 at 52.8%. Its provenance is `espn_consensus_indicative`, so it is labelled correctly and still admitted.
  - The 11-20 row was written after 79487b2c (11-14 20:27Z, the event-list indicative fallback): that fallback
    found no price, and the row was still admitted on ESPN consensus. The 14 rows from 11-14 predate 79487b2c.
  - The 14 v5 rows from 11-14 are listed in `f_G1_main_gate_violation.csv`, for example 201eae62f32f432f,
    216e280204964a59, 098ac7c363e941ec, 9da8348e0cec4e15 and 27172bceca114b65.
- **The staleness gate is also blind here.** Consensus lineage (`college_football.py:621-633`) has no
  `market_quote_observed_at_utc`, so `observed_at_utc` falls back to the decision time (`:1297-1301`).
- **The indicative path would pass too.** The event-list path (`polymarket_us_indicative`, `timestamp_valid`
  false) would also reach Main. No such rows exist yet.

### 4. LOW-MEDIUM, latent in current code: selectors ignore `market_state` and `timestamp_valid`

`_match_cfb_moneyline`, `_match_cfb_spread` and `_match_cfb_total` (`college_football.py:375-418, 442-492, 517-556`)
never test `market_state` or `timestamp_valid`.

- **Archive exposure.**
  - 177 full-game quotes were HALTED before kickoff (292 more partial).
  - 23,188 records have `timestamp_valid` false; every one is post-kickoff for its own event.
- **Rows affected.** 0 live rows used a non-OPEN quote. A `timestamp_valid` false quote, when selected, produces a
  `polymarket_us` row with no lineage whose observed time is the decision time, which is the mechanism in finding
  1 (B5/T3, 9 rows).
- **Fix.** Skip non-OPEN quotes, and skip quotes whose `timestamp_valid` is not true.

### 5. LOW-MEDIUM, latent: the balance metric ignores book width

The |long ask - 50c| ranking treats a resting 50c ask with a 10c bid as perfectly balanced.

- **Archive exposure.**
  - 17,003 of 84,641 full-game pre-kickoff quotes have an ask sum above 1.10.
  - 448 have a 50c long ask with a long bid below 40c (an example is the Furman -28.5 rung on 11-21).
- **Rows affected.** 8 legacy v3/v4 rows (ask sum 1.115 to 1.525, `f_Q1_wide_book_overround_gt_110.csv`); all 8
  are finding 1 rows. 0 v5+ rows: every v5/v6.2/v7 chosen side has ask minus bid of 1c or less.
- **Fix.** Rank by ask sum, or by |midpoint - 50c| among tight books.

### 6. LOW: 7 rows cannot be verified because their quote records are missing from the archive

Digest misses (`f_B1_digest_not_in_archive.csv`):

- **Notre Dame @ Navy (v3 Flat, 10-31 05:04Z):** d3bda226f5674c40, 6af5e571e4354c0f and 399cecbac0914a12.
  6af5e571e4354c0f is Navy +30.5 at 49.5c with a 73.7% model probability, a 24% edge, 2U, win.
- **Bowling Green @ Western Michigan (same run):** 90ad1825056a49cb, 9761ceedca744896 and f5a3ce91c3ea4e97.
- **Purdue @ Penn State:** 8321c28ba0ca456e, which is a finding 1 row anyway.

Every record in the 10-31 file is observed at 05:xxZ or later. The directory's mtime is 02:02 ET (06:02Z), and
the `aec/asc/tsc-cfb-nd-navy-2026-10-31` slugs appear nowhere in the archive. The file appears to have been
recreated after those decisions; DEBUG.md line 18 notes the same loss for two other 10-31 records. The side and
line are internally consistent, but the price cannot be re-derived.

### 7. LOW: ledger price fields are the integer-odds round trip, not the ask

`PickRequest.american_odds = probability_to_american(ask)` (`cli/forecast.py:5816`) stores, as
`market_probability`, edge and PnL basis, the implied probability of the rounded integer odds. 24 of 758 rows
differ from the rationale ask by more than 0.1c (maximum 0.107c). The gate uses the ask-based edge; the ledger
shows the rounded value. Example: d32e388a65d14a23 (Main, Rice @ Temple under 52.5 at a 52c ask) passed the 8%
edge cap at 7.98% but is stored as 8.06%. This is cosmetic.

### 8. LOW, legacy: 21 pre-season Flat rows (08-29 15:45:00Z) with self-inconsistent price fields

The sibling settlement audit already reported these as F2 in `settlement.md` (for example ea381bef94d645b0). They
are cross-referenced here, not re-derived.

All 7 rows each of college-football-v1, cfb-spread-v1 and cfb-total-v1 store an edge computed against a 50c even
price, while `market_probability` shows 52.4 to 55.6% (`f_X2_edge_not_prob_minus_ask.csv`, for example
5d9019677d97400f and c6960e4228f24bb9). This is the same class as the three rows voided on 08-30 for "priced
against a fabricated 0.50 ask", but these 21 were never voided. All are settled, net -2.97U.

### 9. Legacy: stale or missed quotes before the current window logic (Flat only, no Main exposure)

- **Quotes 12 hours or more old (`f_T6_quote_older_than_12h.csv`): 35 rows.**
  - v2: 16 rows, 10-20 to 10-23.
  - v4: 19 rows, 11-05 to 11-13; examples are ff4fadbf8d3c41d6 (20 hours old) and the 11-05 UTSA/FAU and
    JMU/Southern Miss rows (24 hours old).
  - On Flat, the STALE_DATA research reason is overwritten to FLAT_CALL at `ledger.py:797`, so the staleness is
    invisible in v4 rows. There are 0 such rows on Main, and 0 in the v5+ era.
- **Older selectors picked a different rung than current code would (`replay_diff.csv`, status `different`): 11 rows.**
- **The older matchers missed an in-window book and fell back to ESPN:** 100 v4 rows and 82 v2 rows. Across all
  models, 849 rows are in `f_M1_*.csv`.

### 10. Design notes, not bugs

- **Moneyline side selection.** v5 moneylines take the higher-probability side regardless of edge (5e134c0b,
  `college_football.py:1125-1129`). This produces 64 negative-edge v5 Flat moneyline rows.
- **Flat sizing bump.** The bump `1 + min(1, edge/0.08)` (`ledger.py:789-790, 834-835`) uses raw edge, ignores
  `model_uncertainty` and the 0.25U increment (1.32U, 1.73U and so on), and NCAAF overs are forced to 1U.
- **Challenger uncertainty.** v6.2 and v7 send `model_uncertainty` 0.0, which the `or 0.05` and `or 0.02`
  fallbacks replace.
- **No exposure caps on Main.** Per-event Main exposure can exceed the 2U event cap, because `_call_result` skips
  exposure caps by operator directive (2026-07-26).
- **Extremes.** 542 extreme rows; 74 are v5+ (69 v5, 3 v6.2, 2 v7).
  - Every Polymarket-priced v5+ extreme (57 rows; the other 17 are v5 ESPN-consensus rows) re-derives exactly from a tight book, with ask minus bid of 1c or less.
  - The causes are real model disagreement:
    - FBS-vs-FCS spreads, such as Samford +48.5 @ Auburn and Chattanooga +48.5 @ Alabama, at an 83-85% model
      probability against roughly 51c;
    - higher-probability-side moneylines;
    - North Dakota State @ Hawai'i moneyline;
    - Tulane @ Rice moneyline.
  - Price- and probability-extreme v5+ rows, each re-derived exactly from its book:
    - cff48f6f6ce24ba9 (v5) and ff198fbd8e0d4f6b (v6.2): Samford @ Auburn moneyline at 99.5c. v6.2's model
      probability is exactly 1.000, a degenerate pmf tail; 1 row, 0.5c edge.
    - 0e3f4d1035204d51 (v5) and 0948a8126c524226 (v6.2): Rutgers @ Penn State, at 96c and 4.5c on opposite sides.
    - c53cc85074334bb6 (v5) and d03d5b5497c64c0f (v6.2): Northern Illinois @ North Dakota State, at 96c and 4.5c.
    - f9c346ccbfc94f93: Boston College @ Notre Dame at 98%, ESPN consensus.
  - Negative-edge v5 moneylines come from the higher-probability-side rule:
    - on a book: f16c4670a44d4de1, 535c23fc173346f0, d4578576916a4179, 7f0f4bd500aa4b19;
    - on ESPN consensus: 1f21203430e84121, 5c474e6f38194136, 1cc1d1bbd74b444d, ea47cbdd5a5a46f5.
  - None is a pricing error.
  - The 468 pre-v5 extremes are mostly v1 and v2 Main rows on ESPN consensus. For those, `espn_check.py` found the
    lines consistent with the archived books, so the extremes are model overconfidence. Realised record of the
    rows with edge of 15% or more:
    - cfb-spread-v1 Main: 69-86-1, -40.30U;
    - college-football-v1 Main: 88-42, -37.91U;
    - cfb-total-v1 Main: 9-9, -0.89U.
- **Pre-v5 Main gate breaches are all from before the gate existed.**
  - v3, 10-24: the 9 edge-band rows predate d4ea8e60 (10-25), and the 14 weather rows predate 02d904b4 (10-26).
  - v4: the 6 projection-gap rows and all 57 ESPN-priced rows predate f6fa91d1 (11-12), which added the FCS,
    moneyline-floor and projection gates.
  - v1 and v2: gated under older, different rules; not adjudicated.
- **Latent name collisions.** `_team_matches` has two: "Miami" matches "Miami (OH) RedHawks", because "(oh)" is not
  a disqualifying suffix, and "Georgia" matches "Georgia Southern Eagles", because "southern" is only a prefix
  disqualifier. `resolve_team` returns None for "Miami (OH) RedHawks" once parentheses are stripped, for "Missouri
  State Bears" and for "Penn". Two-team matching neutralises all of these today; 0 rows are affected.

## Checked and clean

- **Price, side and line re-derivation:** 718 rows re-derived (241 moneyline, 240 spread, 237 total). 1 mismatch:
  the already-known NM State 10-21 v2 flipped row, 881b2c77229146ee. Eight spreads could not be re-derived because
  they are finding 1 wrong-game rows.
- **v5, v6.2 and v7 rows:** for 553 of 553, the current selectors choose the same contract (slug).
- **Known flipped rows, reconciled.** Running the re-derivation over every digest-bound row, superseded and
  archived included (`reconcile.py`), finds 9 flips: NM State 10-21 (2 rows) and Arizona @ Kansas State 11-21
  (7 rows, of which 3 voided and 4 superseded). It finds no others. The coordinator's count of 13 includes 4 rows
  this method cannot see: they are not bound to a digest, or they sit in another ledger.
- **ESPN-consensus rows (1,518).** They were compared with the book the current selectors find in each row's
  decision window (`espn_check.py`, `espn_vs_book.csv`).
  - 647 have no book and cannot be verified.
  - 768 are consistent (spread gap of 3 points or less, total gap of 4 or less, no moneyline side swap).
  - 103 are flagged, all v1-era, from September to 10-03:
    - 91 are gaps where the archived book held only off-market rungs (asks of 6 to 94c);
    - 10 are sign "flips": 8 against placeholder books (a 51c ask over a 1c bid, which supports finding 5) and 2
      within a few points of the archived book;
    - 0 moneyline swaps.
  - The cross-model check of moneyline favourite against spread-line sign covered 412 same-run pairs. It flags only
    Arizona @ Kansas State and one 08-29 seeded row.
- **Totals over/under:** all 237 totals rows map long to Over. In the archive, 73,381 of 73,381 total quotes are
  long=Over and short=Under.
- **Spread long description equals line:** 94,105 of 94,105 spread quotes.
- **Partial and alternate contracts:** 0 rows used a 1H, quarter or team-total contract. All 170,557 slugs parse
  as `prefix-cfb-a-b-date-rest`. The substring filter neither excludes a full-game slug nor lets a partial one
  through.
- **Market type:** 0 rows priced off a quote of a different market type.
- **Duplicates:**
  - 0 groups with more than one live row per model x event x market x tier;
  - 0 matchups under two event_ids;
  - 0 event_ids carrying two matchups;
  - 127 Main/Flat twin pairs, 3 of which drifted (v4 11-10, from different runs, same side and line).
- **Timing:**
  - 0 rows created at or after kickoff;
  - 0 quotes observed after the decision;
  - 0 quotes observed after kickoff;
  - 0 non-OPEN quotes used;
  - 0 Main rows on a quote 12 hours or more old.
  - Age at decision for bound rows: 522 under 1 hour, 135 at 1-6 hours, 34 at 6-12 hours, 35 at 12 hours or more
    (all legacy Flat).
- **Identity:**
  - The resolver side and the mascot side disagree on 0 bound rows.
  - The same-mascot pairs (LSU/Auburn, Rice/FAU, Rice/Temple, Arizona/Kansas State) are handled by title order.
  - Moneyline favourite against spread-line sign was checked on 379 model x event x run pairs; only finding 1 rows
    and the voided Arizona @ Kansas State rows fail.
- **Sizing:** all 915 live rows created since 11-01 match the current rule exactly (eligibility
  `edge_scaled_units`, then the `ledger.py:765-844` override). All 434 mismatches predate the 28143606 rule change
  of 11-01. No row exceeds 2U, and none is at or below 0U.
- **v5 Main gates:**
  - 0 FBS-vs-FCS;
  - 0 FCS-vs-FCS;
  - 0 moneyline asks below 33.3%;
  - 0 spreads above 21.5;
  - 0 model probabilities below 50%;
  - 0 total-gate breaches;
  - 0 unverified-weather totals;
  - 1 edge-band breach, which is the rounding case in finding 7.
