# NBA / NCAAB / NFL / NCAAF model review — 2026-11-11

Code, feature and wiring review of the four champions, grounded in the settled
runtime ledger. Reviewed at HEAD `75a2498e` (then `5e2212ef`). Every finding was
verified against the live tree before being listed; ledger numbers come from
`E:\model-prediction-runtime\ledgers\ledgers.db` read-only.

**Status (2026-11-12):** the NFL findings (#2, #3 NFL part, #4, #5, #8d NFL, #9 NFL,
#15) are **fixed** in commits `03faf30d` → `a2d9a43c` (see `docs/DEBUG.md`
2026-11-11 entry and `outputs/nfl/nfl_v7_replay.md`). The NBA findings (#3 moneyline
path, #6, #8d NBA, #14) are **fixed** in `quick-261112-11j` (DEBUG.md 2026-11-12 entry), which
also fixed three NBA defects found while re-verifying this file — see the **NBA
addendum** at the end. Everything else below is **open**. Open items belong in
`docs/ROADMAP.md` when picked up; this file is the evidence record.

**NCAAB status (2026-11-12):** #7, #9 (NCAAB part), #10 (NCAAB part), #11 and the
NCAAB push-mass item are **fixed** in `quick-261112-nc6` — `docs/DEBUG.md` 2026-11-12
NCAAB entry, `docs/NCAAB_V6_REVIEW_FIXES_2026-11-12.md`,
`outputs/ncaab/ncaab_v6_review_ablation.md` (validation-selected; control reproduced
68/68 live rows). The t-scale half of #10 was tested and rejected (coverage got worse).
Champions per `config/production.yaml`: NBA = `nba-elo-trend-lr-v4` ML /
`nba-spread-margin-v9` / `nba-total-margin-v9`; NCAAB = `ncaab-spread-v6` (ML+spread)
/ `ncaab-total-v6`; NFL = `nfl-elo-trend-lr-v4` ML / `nfl-*-structural-v7`;
NCAAF = `cfb-structural-v4` all three.

## What's verified working

- **Tests & lint**: 67 tests across the 13 directly relevant files pass; ruff clean
  on `models/`, `features/`, `sports/`, `college_basketball_forward.py`. One pytest
  invocation hard-crashed during collection; two reruns passed (transient, watch).
- **PIT ordering** is correct in all four production paths: `observed_at = utc_now()`
  is captured *before* the slow ESPN/Polymarket/snapshot builds,
  availability/injury/efficiency loaders filter `<= observed_at`, and a second
  `utc_now()` feeds `validate(now=)`. No leak found.
- **NCAAB governance fixes landed and work**: Main-totals pause (`93aa093c`,
  06:08Z 11-10) — last Main total created 05:38Z; current-season sample gate
  (`37c9212d`, 00:03Z 11-11) — last Main NCAAB row 23:43Z 11-10, zero since.
  Side effect: **Main NCAAB is dark until teams hit 8 current-season games
  (~late Nov)**, by design.
- **CFB replay snapshots** are exactly reproducible (RNG state captured before the
  draw; 2e-6 tolerance).

## Ranked findings

### HIGH

**1. NCAAF production never runs `cfb-structural-v4`'s validated code. (FIXED `quick-261112-0xd` — `CollegeFootballModel` is now the v4 class built from the artifact; serving-entry-point parity test added)**
`models/college_football.py:690` serves `CollegeFootballModel()` (`margin_sd=16.2`,
`:87`) under the label `"cfb-structural-v4"` (`:36`) and stamps the v4 artifact
hash on every pick. `CFBStructuralV4Model` (14.5 — the 11-05 walk-forward retune —
plus conference 0.9135 scaling and total-SD widening) is imported only under
`sports/ncaaf/`, which nothing in `cli/` imports. Live evidence: the newest Main
spread pick (22:25Z 11-11) recorded `margin_sd=18.306` = 16.2 × uncertainty
widening for a ±1.5 line. `tests/test_cfb_structural_v4.py:25` pins 14.5 on a class
production never calls. **Fix**: route `build_cfb_slate` through
`CFBStructuralV4Model` (or delete the duplicate) and add a test that imports the
*serving* entry point and asserts its `margin_sd` equals the artifact's.

**2. NFL team efficiency silently collapsed to league-average defaults. (FIXED
`03faf30d`)** `TEAM_COLUMNS` lacked `sacks_suffered`; `_team_efficiency` read it,
the `ColumnNotFoundError` was swallowed, and every team came back as the defaults
tuple from the 10-27 snapshot cutover until 11-12. Reproduced on the live 11-11
snapshot. Now fails closed (`NFLEfficiencyInputError` → NO_CALL); defect window
annotated in `docs/DEBUG.md`; Main rows left in place (operator decision).

**3. Research rows relabelled `QUALIFIED` on Main. (NFL FIXED `e44b7117`; NBA moneyline
path FIXED `quick-261112-11j`; NBA/WNBA spread helper OPEN-BY-DESIGN)** `_downgrade_unserved` keeps `decision="CALL"` with
`record_type=RESEARCH_OBSERVATION`; spread Main gates checked only `decision`;
`ledger.append_evaluated`'s Main branch relabels whatever arrives as QUALIFIED.
21 Main NFL v7 rows carried `model_state_at_decision='research'`. NFL now gates on
`record_type`. The shared basketball spread helper was deliberately left alone:
`test_unqualified_wnba_spread_reaches_main_as_research_only_call` pins that
downgraded WNBA/NBA spreads *do* reach Main as research-only paper calls.

**4. NFL heavy-line spreads silently dropped by replay validation. (FIXED
`9cea0b73`/`e44b7117`)** `inference_inputs.sd` stored the artifact's 13.8 while the
engine priced |line|≥8 at 1.25×; `scalar_artifact_replay` raised and the candidate
vanished into `skipped`. Now stores `fc.effective_sd`; round-trip test added.

**5. NFL weather was always the 5 mph / 65 °F fallback. (FIXED `37084668`)** ESPN
carries no NFL wind/temp; all 69 outdoor v7 total rows showed the placeholder and
the engine's wind/cold terms never fired. Open-Meteo PIT capture for 32 venues now
runs in the daily capture pool; unavailable ⇒ no weather term, flagged.

**6. `nba-elo-trend-lr-v4` is served on different features than it was fit on.
(FIXED `quick-261112-11j` — serving overrides removed; refit still an operator option)** `learned_forward.py:671-698` injects NBA-only overrides (Elo HCA 38,
offseason gap 60, retention 0.40, `isolate_postseason=True`; added in `a9315de4`
10-24); training (`validation.py:393-394`) used `build_elo(history, sport)` (HCA 70,
regression 0.35, gap 90) and `TrendEngine(history)` defaults. `elo_probability` for
even teams shifts ~0.60→0.55 against coefficient 3.56 (~4–5 pp on output). The
artifact's Brier 0.185 / 73.7% no longer describes the served model. Mitigated:
`evidence_status: historical_only` keeps it out of Main as qualified. **Fix**: refit
v4 with the new construction (and put those keys in the artifact) or drop the
overrides; extend `test_train_serve_parity` beyond MLB.

**7. NCAAB total v6 compares a league-shrunk level against a *conference*
baseline. (FIXED `quick-261112-nc6`)** `ncaab_total_v6.py:270-275`: `hl10` was shrunk toward
`TrendEngine.league_baseline` (prior 12 games) but `pf_*` divides by the conference
baseline (SEC 83.4, Northeast 69.0), so high-scoring conferences look "slow" and
low-scoring ones "fast". Repro with the real class: identical 85-ppg teams project
raw total **148.9 labelled SEC vs 162.7 labelled Northeast** (true 170). Also
`w_league = 1-(0.85+0.15) = 0`, so the advertised conference baselines never enter
the total directly. Live: 74 settled picks, 44.6% hit at avg 59% model
probability; Main −27.9u in 3 days (now paused). **Fix**: shrink toward the
conference baseline (team priors for `TrendEngine`, mirroring
`build_elo(team_priors=…)`) or compute `pf` against the league baseline; give
`w_league` a non-zero, validated weight.

### MEDIUM

**8. Executable-ask integrity in CFB/NFL.** (a) CFB FIXED `quick-261112-0xd` (newest capture per slug, both asks required): `college_football.py:426-473`
`_match_cfb_moneyline` returns the *first* (oldest) capture of the day; spread/total
pick the ask closest to 50% across all captures *and* alt lines — the 18:00 ET run
then trips `STALE_DATA` (>12 h) and can never emit Main ML. (b) CFB FIXED `quick-261112-0xd`: missing-side
asks fabricated as `1 − ask` (`:740-741`), overstating edge by the full spread.
(c) OPEN DECISION: **229 of 238 Main NCAAF rows and 31 of 52 Main NFL v7 rows are
`espn_consensus`**, not Polymarket. NFL rows are now labelled (`5ffc2b40`) and stay
Main-eligible by operator decision 2026-11-11; NCAAF rows are now labelled the same way (`quick-261112-0xd`), eligibility unchanged pending decision. (d) NFL total
selector FIXED (`e44b7117`); NBA total selector FIXED `quick-261112-11j` (spread selectors were fixed
in `6c078b08`). Fix pattern: `PolymarketSnapshotStore.latest()`, require both asks,
tag non-Polymarket rows.

**9. Artifact ↔ code drift on code-backed models.** NFL FIXED (`4dd3034e`: dead keys
stripped, recording-dict test). NCAAB FIXED (`quick-261112-nc6`: hash-verified
`from_artifact` factories, consumed-keys + serving-parity tests): `ncaab-spread-v6.json` said
`offseason_retention: 0.2`, served default is 0.35 (`ncaab_spread_v6.py:159`) and
the factory ignores the artifact; CFB `gates.block_fcs/max_spread_line/
max_moneyline_dog_odds` FIXED `quick-261112-0xd` (enforced on Main via `main_gates_from_artifact`; consumed-keys test covers the v4 artifact).
**Fix**: generalise the consumed-keys test to every champion in `production.yaml`.

**10. "Coherent" moneylines aren't coherent with their own margin distributions.
(NCAAB FIXED `quick-261112-nc6`: moneyline read off the t margin distribution; the
scale change was tested and rejected. NBA OPEN)** NCAAB logistic s=9.5 vs its sd-10.5
margin: at +8 → 87.4% vs 77.7%; NBA v9
s=11.8 vs sd 14: +8 → 82.7% vs 71.6%. NCAAB's Student-t(df=8) with *scale* 10.5 has
true SD 12.1, so spread cover probabilities are ~15% flatter than v5's. Needs one
ablation, not three constants.

**11. Early-season margin compression in NCAAB spread v6's trend half. (FIXED
`quick-261112-nc6`: tiered trend priors + additive matchup; Elo ramp implemented but off)** A
team beating mid-majors by 20 hosting one losing to them by 20 (true ≈ +40) gets
`trend_margin` 11.7 vs `elo_margin` 21.5 → `expected_margin` 16.6, 17% to cover
−28.5. Same mechanism as the −64.7u road-dog bleed under tempo-v3. **Fix**:
sample-dependent `elo_weight` (lean on tiered Elo until `current_season_sample` ≥ ~8)
or conference priors for the trend engine.

**12. CFB hardcoded total gates with no evidence. (FIXED `quick-261112-0xd` — moved to `config/model.yaml` `main_total_gates`, same values, documented unvalidated; dead `edge>0.10` removed)** `forecast.py` CFB total
gates: p>62%, under ≥56.5, |proj−line|<1.5; `edge>0.10` is dead given `max_edge`
0.08. Added in a "refactor" and an NBA commit; absent from
`CFB_PREDICTION_SYSTEM.md`.

**13. CFB neutral-site/postseason inputs. (FIXED `quick-261112-0xd` — venue-based weather, postseason week 15 verified on the live 2025-12-31 payload, QB channel wired to the injuries snapshot + scoreboard passing leader; the feed is sparse)** Weather resolves on the home
stadium even for neutral sites; a dome-home team playing an outdoor bowl gets
"verified" 70 °F/0 mph. ESPN postseason `week.number` likely → `week=1` → 90%
preseason-prior weight for bowl games (verify against a live payload).
`qb_starter_prob_*` are never populated in production — the QB channel is inert.

**14. NBA `neutral_site` never set for spread/total `UpcomingGame`. (FIXED `quick-261112-11j`)** NBA Cup
Vegas games, Paris, Mexico City get full HCA; the ML path does use it.

**15. NFL `input_qualified` keyed on calendar-date equality. (FIXED `e44b7117`)**
Now ordering/age (`_nfl_inputs_fresh`).

### LOW (quick wins, all OPEN except NFL rationale strings and the CFB items — MC count 200k and source-hash `code_revision` FIXED `quick-261112-0xd`)

Integer-line push mass credited to one side in NBA/NFL (NCAAB FIXED `quick-261112-nc6`;
`home+away=1`; CFB
handles it). Monte-Carlo noise: 15k sims → ±0.8 pp 95% CI on CFB spread
probabilities vs a 2 pp `min_edge`. NFL `_qb_adjustment` + injury penalty applied on
the listed starter's tier, not the backup's. `code_revision` for CFB is a hash of
the version *string*. `BasketballModel` fully constructed then discarded per NBA
slate; availability wiring hardcodes `("v7","v9")`. NBA v9 `uncertainty` hits its
0.03 floor at ≥34 lifetime history games.

## NFL replay result (2026-11-12, `scripts/replay_nfl_v7_all_settled.py`)

Point-in-time over 1,489 completed games with nflverse closing lines (2021→) the
fixed v7 engines sit at the closing line: spread Brier 0.2496 (market 0.2500, slope
0.79), total 0.2506 (slope 0.43 with weather, 0.19 without; >60% tail inverted).
Walk-forward tournament → identity for both markets. At the live 5% hurdle the
spread engine fires 16 times in 1,451 games (37.5% hit). Flat replay (110 picks):
fixed-model probabilities average below the recorded asks; 8 fires, 6 wins.
`config/production.yaml` still says `predictively_qualified` for both — contradicted
by this evidence; operator decision.

## Suggested additions (model-side)

- **NCAAB**: conference-tiered priors for the *trend* engine (the Elo half already
  has them); sample-scheduled `elo_weight`; validate the Student-t scale
  (`sd·√((df−2)/df)`) and one logistic scale against the t distribution;
  `w_league` > 0. Then re-run the HANDOFF §0 ablation (`elo_weight` ×
  `offseason_regression` × HCA) in a harness that reproduces v6's live numbers first.
- **NBA**: v9 ML engine (`moneyline_margin_weight` blend) as the registered challenger
  to LR-v4, with `nba-moneyline-v9`'s true features documented (coefficients
  0.60/0.10/0.10 vs v4's 3.56 suggest different feature scaling — check before
  trusting its 0.1945 Brier). Pace from real possessions (`nba_player_logs`) rather
  than the points-ratio proxy. Wire `neutral_site`.
- **NFL**: the structural inputs add nothing measurable over the market anchor at the
  close; the levers are early Polymarket prices vs the close (CLV) and the moneyline
  model, which was not re-priced. Explicit push handling or half-point-only lines.
- **NCAAF**: ~~ship the v4 class to production (#1)~~ done `quick-261112-0xd`; then replay v4's PIT predictions
  against the 363-game no-vig closing dataset (HANDOFF §7) — the first real
  market-relative number for the champion. Add FCS blocking (declared in the
  artifact, enforced nowhere). Pass `season.type`/week and `is_neutral_site` into
  weather resolution.

## Cross-cutting test gaps

1. Serving-entry-point parity: for each champion in `production.yaml`, import the
   object production actually calls and assert its parameters equal the artifact's
   (would have caught #1, #9). Done for NCAAF (`tests/test_cfb_serving_parity.py`).
2. `record_type` Main gate test for every sport path (#3) — done for NFL.
3. Train/serve parity for NBA/NFL/WNBA learned models, not just MLB v9 (#6).
4. Generalised consumed-keys artifact test (#9) — done for NFL v7 and NCAAF v4.

## NCAAF replay result (2026-11-12, `scripts/replay_cfb_v4_all_settled.py`)

The fixed champion (v4 class from the artifact: margin_sd 14.5, 200k sims) replayed
point-in-time over **all 7,960 completed games 2016→2026-11-12** (history strictly before
kickoff; no market lines exist for this cohort) and over **all 1,610 settled Flat+Main
ledger rows** re-priced at their recorded line and ask with history as of decision time.
Report: `outputs/ncaaf/cfb_v4_replay.md` / `.json`. Nothing written to artifacts or ledgers.

- **Moneyline adds nothing over its own Elo channel.** Brier 0.1893 vs 0.1894 for the bare
  `elo_home_win_prob` feature (home-rate baseline 0.2424); 70.8% accuracy; reliability
  slope 1.20 (underconfident: 80-90% bucket hits 89.9%). Season-by-season the two are
  within ±0.003 every year.
- **The served margin SD is too narrow where it matters.** Realized margin residual SD
  17.15 vs mean effective 15.94 overall; on established FBS-vs-FBS (sample ≥ 4) 16.84 vs
  14.76 (−12%); conference matchups 16.61 vs 14.43, so the 0.9135 conference scale widens
  the gap. The uncertainty widening points the wrong way at the bottom: the lowest-uncertainty
  bucket realizes 17.24 against an effective 14.27; only the 0.14-0.19 bucket matches
  (17.64 vs 17.77). FBS-vs-FCS is over-widened (18.98 vs 21.12). Totals: realized 16.91 vs
  effective 18.42 (the deviation widening inflates it; base 16.0 is about right).
- **Home margin bias +0.84 overall, +2.0 in 2025 and 2026** — the 2.5-point HFA is
  understated recently. Neutral-site games (580) score worse (Brier 0.2264).
- **Settled rows: the recorded ask beats both models.** Flat (559 rows): Brier ask 0.2289,
  old model 0.2448, fixed model 0.2449; the fixed model would have fired 98 times under the
  artifact gates: 51.0%, −6.14u flat. By market: moneyline 20 fires 75.0% +4.90u; spread 34
  fires 50.0% −1.53u; **total 44 fires 40.9% −9.52u**. Main (1,051 rows): 241 fires 47.3%
  −28.44u, totals 126 fires 44.4% −18.45u. On the 143 Flat rows priced against Polymarket
  the fixed model does beat the ask (0.2264 vs 0.2370); on the 416 consensus-priced rows it
  does not (0.2513 vs 0.2261).
- Caveats: no weather term in either cohort (placeholder history fields, no PIT forecast in
  old snapshots); QB channel inert before 2026-11; history not vintaged; consensus asks
  were never executable.

Implications (operator decisions, promotion-gate class): pause Main NCAAF totals as was
done for NCAAB; retune `margin_sd` against realized residual variance (≈16.8 on
established matchups) with the conference scale and the low-uncertainty widening
re-examined, as a versioned v5 challenger through the walk-forward contract; demote or
re-evidence the `predictively_qualified` label, since the structural layer does not beat
its own Elo channel on 7,960 games.

## Open operator decisions

Supersede or leave Main rows priced on defaulted NFL inputs (left, annotated) and
on consensus asks (left); whether ESPN-consensus should ever reach Main for NCAAF;
refit `nba-elo-trend-lr-v4` or revert its serving overrides; demote the NFL v7
`predictively_qualified` label. All are promotion-gate-class changes per
`docs/ROADMAP.md`.

## NBA addendum — 2026-11-12 re-verification (`quick-261112-11j`)

Re-read against HEAD `020767d8` and the runtime ledger. The four NBA findings above
reproduced exactly. Three more surfaced that model code alone cannot show; all three
are fixed in `quick-261112-11j` with regression tests (details and hashes: `docs/DEBUG.md`
2026-11-12).

**A1. A totals serving calibrator reshaped every served NBA total, then silently
reverted.** The committed `nba-total-margin-v9.total_calibrator.json` was a Beta
transform (a 1.2827, b 1.2591, c −0.6334) fitted 2026-11-08 on 125 settled picks made
by v2–v7, labelled v9. `_forecast_nba_total_slate` loaded it from 2026-11-11T01:32Z
(working-tree code, committed later in `0af19ecd`); rows carry
`calibration_method=beta`. Served probabilities on the model's own side fell to
35–46%, so every total had negative edge and Main totals went dark from 11-09. The
nightly pipeline overwrote the file with identity at 2026-11-12T05:32Z (n = 28).
Flat, v9 totals settled: raw era 9-6 +2.97u (avg served 56.3%); beta era 3-10 −7.08u
(avg served 41.9%) on the same raw selections. **Fix:** one shared serving guard
(`calibration_pipeline.serving_guard_reason`) for the buyer, NFL slates and the NBA
total slate; artifacts must declare `fit_model_versions == [model_version]` and a
`fit_source` of `settled_live_rows` or `point_in_time_replay`; the pipeline stamps
both; the slate records the decision. Operator decision still open: the pipeline
can still change a (now own-model) serving transform unattended.

**A2. `nba-moneyline-v9` claimed a locked-holdout qualification it never earned.**
Rolled back 2026-11-10 (`4d5624ed`, placeholder coefficients) but the artifact said
`qualified: true`, Brier 0.1945; 8 of its 11 settled flat rows priced the model
below the market ask (54% vs 87%). **Fix:** qualification block rewritten as an
unverified placeholder with the rollback note, hash recomputed, dropped from the
auto-buyer whitelist (lr-v4 deliberately not added — operator decision), registry
test that `unverified` entries cannot carry a qualified artifact claim.

**A3. #3 on the NBA moneyline path, with ledger evidence.** Main lr-v4 row created
2026-11-11T05:51Z: `reason_code QUALIFIED`, `record_type QUALIFIED_SHADOW_CALL`,
although lr-v4 had been `historical_only` since 11-10 17:42Z and the forecast path
had downgraded it to `PAPER_CALL_MODEL_EVIDENCE_UNVERIFIED`. `ledger.py`'s Main
branch (`ccc842b6`) relabelled every row it received. **Fix:** Main still takes every
CALL (operator directive 2026-10-11) but a non-qualified eligibility keeps its
`PAPER_CALL_*` reason and research `record_type`; only a qualified one is promoted.

Still open for NBA: #10 (latent until the v9 moneyline engine is a challenger) and
the two operator decisions above. LOW items, checked 2026-11-12: integer-line push
mass is moot for NBA -- all 341 settled/open NBA spread and total rows in the runtime
ledger sit on half-point lines, so no change was made to the pricing/replay contract;
the discarded `BasketballModel` construction in both NBA slates is now lazy
(`quick-261112-11j`); the availability wiring still names v7/v9 explicitly (correct,
brittle, left); the v9 `uncertainty` floor feeds sizing and needs its own evidence.
