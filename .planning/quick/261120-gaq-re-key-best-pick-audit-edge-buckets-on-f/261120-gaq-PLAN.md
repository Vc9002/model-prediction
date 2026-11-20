---
phase: quick-261120-gaq
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - scripts/audit_best_picks.py
  - tests/test_audit_best_picks.py
  - docs/BEST_PICKS_AUDIT_FRAMEWORK.md
  - docs/DEBUG.md
  - .codex/skills/forecast-model-picks/SKILL.md
autonomous: true
requirements:
  - QUICK-261120-gaq

estimate:
  tokens: 55000
  raw_tokens: 55000
  tasks: 3
  confidence: low

must_haves:
  truths:
    - "A decision (key = sport, event_id, market_type, selection, model_id, lowercased) is bucketed on the edge of its EARLIEST row with status open, superseded or settled (all_rows), and on its EARLIEST row whose own market_quote_provenance is decision_time_executable_quote (verified). The last pre-game settled row no longer decides the bucket."
    - "Each first-sight row is graded at its own line and price: its own result and pnl_units when it is itself settled WIN/LOSS/PUSH; else from final home_score/away_score on any settled row of the same (sport, event_id); else from a same-key settled row with the same line; else the key is skipped as ungraded. A later row is never substituted for an ungradeable first row."
    - "Rows with status archived or removed are never counted in any bucket, first-sight or last-run."
    - "audit_candidate keeps every existing return key, verdict threshold, MIN_GRADED, and both basis strings; it additionally returns last_run_evidence (the old settled-row basis, chosen with the same use_model decision as evidence) and appends a 'Context, last pre-game run (old basis): verified XW-YL, Z% ROI.' flag when that record has a verified graded decision."
    - "On the live ledger the new last_run_by_* maps match HEAD's stats_by_* maps: identical key sets and identical wins/losses/pushes, pnl and staked within 1e-6 (proves the old basis is preserved as context)."
    - "The new first-sight regression test fails against HEAD's engine and passes against the new one (proved by revert); the 12 existing tests still pass."
    - "docs/BEST_PICKS_AUDIT_FRAMEWORK.md and docs/DEBUG.md state the first-sight basis, why, and the measured NBA under 5-8% numbers on both bases."
  artifacts:
    - path: "scripts/audit_best_picks.py"
      provides: "HistoricalAuditEngine with first-sight stats_by_* maps, last_run_by_* maps, ungraded_first_sight_keys counter, and audit_candidate returning last_run_evidence"
    - path: "tests/test_audit_best_picks.py"
      provides: "regression tests for first-sight bucketing, score grading (total and spread), same-line fallback, archived/removed exclusion, last-run context basis"
    - path: "docs/BEST_PICKS_AUDIT_FRAMEWORK.md"
      provides: "first-sight bucket population section; Section 3 snapshot marked old basis"
    - path: "docs/DEBUG.md"
      provides: "2026-11-20 entry with the evidence and the live before/after numbers"
  key_links:
    - from: "scripts/audit_best_picks.py::HistoricalAuditEngine._load_historical_settlements"
      to: "ledger_records (status IN open, superseded, settled)"
      via: "one read-only query through _connect_ro, scores via json_extract under json_valid"
      pattern: "status IN"
    - from: "scripts/audit_best_picks.py::audit_candidate"
      to: "external Rainmeter widget AutoLog.py (outside the repo, not edited)"
      via: "unchanged keys evidence/basis/quote_verified/verdict and EdgeTierStats fields wins/losses/win_rate/roi/total_graded"
      pattern: "\"evidence\": evidence"
---

<objective>
Re-key the best-pick audit's edge buckets in `scripts/audit_best_picks.py` on the edge a pick had when it was FIRST offered (its first sighting in the ledger), graded at that row's own line and price, instead of on the last pre-game settled row. Keep the old settled-row record as context only.

Purpose: the forecast job reruns up to 4x a day and supersedes earlier rows, so the settled row is the LAST pre-game rerun. Bucketing on its edge conditions on line movement that happened after the pick was offered: a pick offered at +6% whose final run shows +12% (or +1%) leaves the 5-8% bucket. Measured by the orchestrator on the live ledger: NBA total v9 under 5-8% reads 8W-1L, +77.4% ROI; the 8 unders a manual tracker logged in that band at first sight went 3-5, and all 5 losers had left the band by the final run (first-in-band record 11W-10L, +3.5% ROI). NBA moneyline pooled 5-8% reads 8W-1L, +47.6% vs about +11% at first sighting. The pnl arithmetic is correct; the defect is population selection.

Locked design (orchestrator, implement exactly): population and keys per decision 1; grading per decision 2; last-run context per decision 3; why-comment per decision 4; tests a-d per decision 5; docs per decision 6; verification per decision 7. Planner discretion choices are stated inline and marked "(discretion)".

Output: the re-keyed engine, its regression tests (revert-proved), a live read-only comparison, updated docs, a SUMMARY.
</objective>

<execution_context>
@~/.claude/gsd-core/workflows/execute-plan.md
@~/.claude/gsd-core/templates/summary.md
</execution_context>

<context>
@CLAUDE.md
@.claude/CLAUDE.md
@scripts/audit_best_picks.py
@tests/test_audit_best_picks.py
@docs/BEST_PICKS_AUDIT_FRAMEWORK.md

Facts measured by the planner (read-only, 2026-11-20) that the executor can rely on:
- Live ledger `E:/model-prediction-runtime/ledgers/ledgers.db` status counts: settled 25,040; superseded 29,973; open 141; archived 30; removed 10. Tiers: flat, main, gated_research, research. 0 rows have an empty event_id.
- `home_score`/`away_score` in `decision_payload_json` are TEXT (for example '126', '98'). They are non-empty on 24,106 of 25,040 settled rows. Many superseded rows carry the key with an empty string, so "non-empty" must be checked, not "not null".
- Selections in scope: moneyline home/away, spread home/away, total over/under, nrfi nrfi/yrfi. Spread `line` is from the selected side's perspective. Many rows have a NULL line.
- `created_at_utc` formats are MIXED: 55,079 rows like `2026-11-19T23:47:42.589330Z`, 73 rows without fractional seconds (`...42Z`), 2 rows ending `+00:00`. String sort mis-orders same-second rows, so first-sight ordering must parse timestamps.
- Baselines at HEAD: `ruff check` and `ruff format --check` clean on both files; `python -m pytest tests/test_audit_best_picks.py -o addopts='' -q` gives 12 passed; the engine loads the live ledger in about 0.5 s.
- `scripts/audit_best_picks.py`, `tests/test_audit_best_picks.py`, `docs/BEST_PICKS_AUDIT_FRAMEWORK.md` and `docs/DEBUG.md` are clean at HEAD. `.codex/skills/forecast-model-picks/SKILL.md` has 66 lines of SOMEONE ELSE's uncommitted work, including the whole "Verify best-pick claims" section; it must NOT be staged.
- The shared tree has many unrelated uncommitted files (calibration JSONs, config/model.yaml, .planning/STATE.md). Stage only by explicit path. Never `git add -A`, `git add .`, `git stash`, or `git checkout .`.
- The external widget `C:/Users/Vince/Documents/Rainmeter/Skins/BestPickAudit/@Resources/AutoLog.py` imports this script. Do not edit it and do not run it (it may write the manual tracker CSV).

SCRATCH below means the executor's own session scratchpad directory (absolute path from its environment). Shell variables do not persist between Bash calls, so every command that uses `$SCRATCH` (including the `<automated>` verify lines) must assign it in the same call, e.g. prefix `SCRATCH='<absolute scratchpad path>' && ...`.
</context>

<tasks>

<task type="tracer" tdd="true">
  <name>Task 1: First-sight engine, last-run context maps, regression tests with revert proof, live CLI run</name>
  <files>scripts/audit_best_picks.py, tests/test_audit_best_picks.py</files>
  <read_first>scripts/audit_best_picks.py (all 494 lines), tests/test_audit_best_picks.py (all; fixture schema and the `_add` helper at the top)</read_first>
  <behavior>
    - (a) test_first_sight_bucket_not_last_rerun: one nba total under key for model m1 (event "evt-a"): a superseded verified row at edge 0.065, line 230.5, created 2026-01-01T14:00:00Z; then a settled verified row at edge 0.13, line 233.5, result win, created 2026-01-01T22:00:00Z, payload home_score "120", away_score "112" (total 232). Under 230.5 loses at first sight; under 233.5 wins at the last run. Candidate (nba, total, under, edge 0.065, m1, verified): `pooled.verified` is 0W-1L, total_graded 1, pnl -1.0, and `evidence.all_rows.losses == 1`. Candidate at edge 0.13: pooled all_rows and verified both total_graded 0. Use 0.13, not 0.12, because 0.5 + 0.12 - 0.5 sits on the bucket boundary in floats.
    - (b) test_first_sight_graded_by_same_line_settled_result_without_scores: key "evt-b": superseded verified edge 0.065 line 230.5, then settled verified edge 0.13 line 230.5 result win (helper pnl 0.9), default payload without scores. 5-8% under pooled verified is 1W-0L with pnl == pytest.approx(1.0), the price-derived pnl from the first-sight row's 0.5 price, not the settled row's 0.9. A second key "evt-b2" (superseded 0.065 line 230.5, settled 0.13 line 233.5 win, no scores) is ungraded: it adds nothing to either bucket, and `engine.ungraded_first_sight_keys == 1`.
    - (c) test_archived_and_removed_rows_never_counted: mlb moneyline home key "evt-c": an archived verified row at edge 0.065 result loss created first, then a settled verified row at edge 0.09 result win created later. A removed verified row on another event at edge 0.065 result loss. The 5.0-7.9% moneyline bucket has all_rows total_graded 0; the 8.0-11.9% bucket has verified 1W-0L (the archived row never becomes the first sighting).
    - (d) test_last_run_evidence_keeps_settled_row_basis: four m1 nba total under keys, each a superseded verified row at edge 0.065 line 230.5 then a settled verified row at edge 0.13 line 233.5 result win (scores home "120" away "112"). One m2 key: superseded verified edge 0.065 line 230.5, settled verified edge 0.13 line 233.5 result loss (scores home "120" away "115"). Candidate (nba, total, under, edge 0.13, m1, verified): `basis == "all models pooled"`; `evidence.verified.total_graded == 0`; `last_run_evidence.verified` is 4W-1L (pooled). This pins the basis rule: if the last-run model record (4 verified) chose the basis, it would read 4W-0L. A flag contains "Context, last pre-game run (old basis): verified 4W-1L".
    - (e) test_first_sight_spread_graded_from_selected_side (discretion, guards the line-perspective rule): nba spread away key: superseded verified edge 0.065 line +2.5, then settled verified edge 0.13 line +1.5 result loss, scores home "122" away "120". Away margin -2: +2.5 covers, +1.5 does not. The 5.0-7.9% spread bucket is verified 1W-0L with pnl == pytest.approx(1.0).
    - All 12 existing tests pass unchanged. They insert only settled rows, and a lone settled row is its own first sighting graded by its own result and pnl.
  </behavior>
  <action>
RED FIRST. Save HEAD's engine for the revert proof and for Task 2: `git show HEAD:scripts/audit_best_picks.py > SCRATCH/gaq_head_audit_best_picks.py`.

Test fixture changes:
- Extend `_add` in tests/test_audit_best_picks.py with a keyword `payload_extra: dict | None = None`. It is merged into the JSON payload only when `raw_payload` is None. `status`, `created_at`, `line` and `event_id` already exist.
- Pass `result=None` for superseded rows. The helper then stores pnl 0.0.
- Add a small helper `_superseded_then_settled(db_path, *, event_id, model_id, sport, market_type, selection, first_edge, first_line, last_edge, last_line, last_result, scores=None, provenance=VERIFIED)`. It inserts the two rows with created_at 14:00Z then 22:00Z.

Write tests (a) to (e) per `<behavior>`. Run them against the HEAD engine and confirm (a) fails because the 5-8% bucket has 0 graded. That is the red.

Engine changes in scripts/audit_best_picks.py (per locked D1-D4):
1. In `__init__`, keep the four existing attribute names `stats_by_sport_mkt_bucket`, `stats_by_sport_dir_bucket`, `stats_by_model_mkt_bucket` and `stats_by_model_dir_bucket`. They now hold the FIRST-SIGHT basis.
2. Add four parallel defaultdict(BucketStats) maps with the same key shapes. Use exactly these names, because Task 2 compares them to HEAD: `last_run_by_sport_mkt_bucket`, `last_run_by_sport_dir_bucket`, `last_run_by_model_mkt_bucket`, `last_run_by_model_dir_bucket`.
3. Add `self.ungraded_first_sight_keys: int = 0`.
4. The constructor still calls `_load_historical_settlements`.

Loading, in one query (per D1):
- Rewrite `_load_historical_settlements` around ONE read-only query through `_connect_ro`, filtered on `status IN ('open','superseded','settled')`. Archived and removed rows are operator-removed bad rows and are never read.
- Select today's columns plus `status`, `created_at_utc`, `ledger_tier`, and `home_score` / `away_score`. Pull the scores with `json_extract` under the same `CASE WHEN json_valid(decision_payload_json)` guard as `quote_provenance`, so a malformed payload still never raises.
- Order is applied in Python, as described in the next two sections.

Last-run pass (per D3; reproduces HEAD's numbers):
- Take only the settled rows.
- Sort them with HEAD's SQL ordering semantics: key `(0 if ledger_tier == 'main' else 1, created_at_utc or "")`, using a stable sort.
- Run HEAD's exact loop body into the `last_run_by_*` maps: WIN/LOSS/PUSH filter, the `seen` dedup only when event_id is non-empty, own `pnl_units`, staked `units or 1.0`, and all_rows plus verified-when-verified.
- Factor the per-row "which BucketStats entries" logic (2 entries, plus the 2 direction entries for total over/under) into one helper that both passes use.

First-sight pass (per D1, D2):
- Sort ALL loaded rows by `(parsed created_at_utc, tier rank main=0 else 1)`, stable sort.
- Parse with `datetime.fromisoformat`, which accepts both the `Z` and `+00:00` forms on Python 3.11. Treat a naive parse (no offset) as UTC, so mixed aware/naive values can never raise TypeError during the sort and take down engine load (and the widget with it). An unparseable or missing timestamp gets an aware `datetime.max` sentinel so it can never claim first sighting.
- Put this why-comment at the selection, per D4: the settled row is the last pre-game rerun (the daily job reruns up to 4x and supersedes earlier rows), so keying the bucket on its edge conditions on line movement after the pick was first offered; winners whose edge grew and losers whose edge shrank migrate between buckets.
- Walk the sorted rows with setdefault into two dicts keyed by the lowercased 5-tuple: `first_any` (any row) and `first_verified` (only rows whose `quote_provenance == VERIFIED_QUOTE_PROVENANCE`).
- Rows with an empty event_id keep today's behaviour, per D1: skip them in the dicts, and if settled with WIN/LOSS/PUSH, count each one individually from its own result and pnl into all_rows (and verified when verified).
- Each `first_any` row, once graded, adds to `.all_rows` only, using that row's own edge for the bucket.
- Each `first_verified` row, once graded, adds to `.verified` only, using its own edge for its bucket.
- The two dicts are NOT coupled. A key can sit in one bucket's all_rows and another bucket's verified, so "N verified of M graded" may show N > M. That is accepted per the locked design.
- Each `first_any` key whose row is ungradeable increments `ungraded_first_sight_keys` and is skipped.
- Never fall forward to a later row of the same key: substituting the last run reintroduces the bias.

Grading helper(s) (per D2). Suggested split: `_parse_score`, `_grade_from_scores`, `_price_pnl`, and a method `_grade_first_sight(row, scores_by_event, settled_by_key)` returning `(result, pnl, staked)` or None. Steps, in order:
1. `p = market_probability`. If it is missing, `<= 0`, or `>= 1`, return None. Apply this to every first-sight row, including the own-settled path, because a missing price makes the edge and bucket meaningless. The last-run pass stays exactly as HEAD (discretion).
2. `staked = float(units or 1.0)`.
3. If the row itself is settled with WIN/LOSS/PUSH, return its own upper-cased result and `float(pnl_units or 0.0)`, unchanged from today.
4. Else grade from scores. Before the loop, build `scores_by_event[(sport, event_id)]` from settled rows whose home_score and away_score both parse as float (non-empty strings; the first parseable pair wins). Then:
   - moneyline home/away: selected-side score minus other-side score.
   - spread home/away: (selected minus other) + line; requires a non-NULL line.
   - total over: total minus line. Total under: line minus total. Both require a non-NULL line.
   - Greater than 0 is WIN, less than 0 is LOSS, equal is PUSH.
   - Any other market or selection (nrfi/yrfi, draw) falls through to step 5.
5. Else use the same-key settled fallback. `settled_by_key` holds same-key settled rows with WIN/LOSS/PUSH, in last-run order. Take the first whose line equals the first-sight row's line: both NULL counts as equal (moneyline and nrfi lines are NULL); otherwise both numeric with `abs(diff) < 1e-9`. Never call `abs()` on None.
6. Else return None.
7. Price-derived pnl for steps 4 and 5: WIN is `staked * (1 - p) / p`, LOSS is `-staked`, PUSH is `0.0`. This matches ledger pnl_units on every row the orchestrator checked.

`audit_candidate` (per D3):
- Leave the verdict logic, thresholds, MIN_GRADED, both basis strings and every existing return key exactly as they are.
- After computing `use_model`, build `last_run_pooled` and `last_run_model` with the same direction-vs-market family selection from the `last_run_by_*` maps (`.get(..., BucketStats())`).
- Set `last_run_evidence = last_run_model if use_model else last_run_pooled`. This reuses the FIRST-SIGHT `use_model` decision so the context line is on the same basis as the `basis` string (discretion; pinned by test d).
- After the if/elif flag chain and before the CONFIRMED_BEST-withheld check, append the flag `Context, last pre-game run (old basis): verified {w}W-{l}L, {roi:+.1f}% ROI.` whenever `last_run_evidence.verified.total_graded > 0`. Percentages only, never American odds.
- Add `"last_run_evidence": last_run_evidence` to the returned dict.
- `collect_open_pick_audits` needs no change; the flag reaches the printed report through `flags`.

Update the module docstring point 1 and the `BucketStats` docstring so they describe first-sight rows rather than settled rows.

Then run the tests green. REVERT PROOF (CLAUDE.md rule):
1. `cp scripts/audit_best_picks.py SCRATCH/gaq_new_audit_best_picks.py`.
2. `cp SCRATCH/gaq_head_audit_best_picks.py scripts/audit_best_picks.py`.
3. Run `python -m pytest "tests/test_audit_best_picks.py::test_first_sight_bucket_not_last_rerun" -o addopts='' -q` and record the assertion failure (losses 0 != 1).
4. `cp SCRATCH/gaq_new_audit_best_picks.py scripts/audit_best_picks.py`.
5. Confirm with `sha256sum` that the restored file matches the saved new copy, then rerun the whole test file green.

Run the live end-to-end path: `python scripts/audit_best_picks.py` against the real ledger (read-only via `_connect_ro`). It must exit 0. Record its wall time next to the 0.5 s engine baseline.

Commit:
- Stage EXACTLY `scripts/audit_best_picks.py` and `tests/test_audit_best_picks.py` by path.
- Check that `git diff --cached --name-only` lists only those two.
- Commit `fix(quick-261120-gaq): best-pick audit buckets keyed on first sighting, graded at that row's line and price`, ending with the attribution line from the session.
- Afterwards, check that `git show --stat --format= HEAD` lists only those two files.
  </action>
  <verify>
    <automated>ruff check scripts/audit_best_picks.py tests/test_audit_best_picks.py && ruff format --check scripts/audit_best_picks.py tests/test_audit_best_picks.py && python -m pytest tests/test_audit_best_picks.py -o addopts='' -q && python scripts/audit_best_picks.py > /dev/null && echo E2E_OK</automated>
  </verify>
  <done>
    - Ruff check and format are clean.
    - 17 tests pass: the 12 existing plus (a) to (e).
    - Test (a) was observed failing with HEAD's engine restored and passing after the restore, with matching sha256.
    - `python scripts/audit_best_picks.py` exits 0 on the live ledger; wall time recorded.
    - One commit holds exactly the two files.
  </done>
</task>

<task type="auto">
  <name>Task 2: Live read-only comparison: last-run maps equal HEAD; first-sight vs last-run NBA numbers; widget contract</name>
  <files>SCRATCH/gaq_compare.py (scratch only, not committed)</files>
  <read_first>SCRATCH/gaq_head_audit_best_picks.py (saved in Task 1); scripts/audit_best_picks.py (already in context from Task 1, do not re-read)</read_first>
  <action>
Write `SCRATCH/gaq_compare.py`. It loads two modules with `importlib.util.spec_from_file_location`, registering each in `sys.modules` under its own name before `exec_module` (the dataclasses under `from __future__ import annotations` need this, exactly as tests/test_audit_best_picks.py does):
- `abp_head` from SCRATCH/gaq_head_audit_best_picks.py.
- `abp_new` from E:/MP/scripts/audit_best_picks.py.

It builds `old = abp_head.HistoricalAuditEngine()` and `new = abp_new.HistoricalAuditEngine()`, both on the live default `DB_PATH`. Both open read-only through `_connect_ro`; nothing writes.

1. Equality gate, the decisive check that the old basis survives as context:
   - Compare old.stats_by_sport_mkt_bucket with new.last_run_by_sport_mkt_bucket, and likewise for sport_dir, model_mkt and model_dir.
   - Convert each BucketStats with `dataclasses.astuple`. Direct `==` fails across two module copies of the dataclass.
   - Require identical key sets and identical wins/losses/pushes for both all_rows and verified. Compare pnl and staked within an absolute tolerance of 1e-6: rows that tie on tier and created_at_utc can be summed in a different order, which changes only the last float digits.
   - Print `LAST_RUN_EQUALS_HEAD True` or `False`. Exit 1 on any mismatch, printing the first differing key and both tuples.
2. Report numbers (report-only, not a gate). Print W-L-P, win rate %, ROI %, and total graded for both all_rows and verified, on both bases:
   - NBA total under, bucket `5.0% - 7.9%`, model `nba-total-margin-v9`: first-sight `new.stats_by_model_dir_bucket[('nba','under','5.0% - 7.9%','nba-total-margin-v9')]` vs `new.last_run_by_model_dir_bucket[same key]`.
   - The pooled `('nba','under','5.0% - 7.9%')` direction bucket on both bases.
   - The NBA moneyline pooled `('nba','moneyline','5.0% - 7.9%')` on both bases.
   - `new.ungraded_first_sight_keys`.
   - The orchestrator expects roughly 11W-10L-ish first-sight vs 8W-1L last-run for the NBA under bucket. Its "first-in-band" tracker population is not the same population, so differences are expected and are reported, not gated.
3. Verdict change and widget contract: call `audit_candidate(sport='nba', market_type='total', selection='under', edge=0.065, model_prob=0.565, market_prob=0.5, model_id='nba-total-margin-v9', quote_provenance=VERIFIED_QUOTE_PROVENANCE)` on both engines.
   - Print old verdict, new verdict, new basis, and every new flag.
   - Print the new result's `evidence.verified` and `last_run_evidence.verified` records explicitly (W-L, win rate %, ROI %), since locked D7 names both.
   - Assert the widget-facing contract on the new result: `a["evidence"].verified.wins/.losses/.win_rate/.roi/.total_graded`, `a["evidence"].all_rows`, `a["basis"]`, `a["quote_verified"]`, `a["verdict"]` and `a["last_run_evidence"]` all resolve.
   - Assert module attributes `_connect_ro`, `DB_PATH` and `ET_TZ` exist on `abp_new`.
   - Do not run the Rainmeter AutoLog.py.

Run it and paste its full output into the SUMMARY. If the equality gate fails, fix the last-run pass in scripts/audit_best_picks.py (its ordering or loop body drifted from HEAD), rerun Task 1's verify, amend nothing: make a follow-up commit with the same explicit-path staging.
  </action>
  <verify>
    <automated>python "$SCRATCH/gaq_compare.py" > "$SCRATCH/gaq_compare.out" && grep -q "LAST_RUN_EQUALS_HEAD True" "$SCRATCH/gaq_compare.out" && cat "$SCRATCH/gaq_compare.out" && echo COMPARE_OK</automated>
  </verify>
  <done>
    - The script exits 0 and prints `LAST_RUN_EQUALS_HEAD True`.
    - The first-sight and last-run records for NBA total v9 under 5-8% (model-only and pooled) and NBA moneyline pooled 5-8% are printed and captured, along with the ungraded-key count, the old and new verdicts, and a passing widget-contract check.
  </done>
</task>

<task type="auto">
  <name>Task 3: Docs: framework section, Section 3 old-basis marking, DEBUG entry, skill line (working tree only); commit docs</name>
  <files>docs/BEST_PICKS_AUDIT_FRAMEWORK.md, docs/DEBUG.md, .codex/skills/forecast-model-picks/SKILL.md</files>
  <read_first>docs/BEST_PICKS_AUDIT_FRAMEWORK.md (already in context; edit with Edit, scoped), docs/DEBUG.md lines 1-10 only, .codex/skills/forecast-model-picks/SKILL.md lines 85-100 only, SCRATCH/gaq_compare.out</read_first>
  <action>
Per locked D6. Use the Edit tool for every change (scoped replacements, no whole-file Write). Percentages only, never American odds. Comments and prose explain why.

1. docs/BEST_PICKS_AUDIT_FRAMEWORK.md:
   - In Pillar 1, change the "Only verified-quote rows count" bullet so it says a decision counts by its FIRST verified-quote row, not "a settled row".
   - Directly after Pillar 1's bullet list, add a short subsection: `#### Bucket population: first sighting, not the last rerun (2026-11-20, quick 261120-gaq)`. In a few sentences it must cover:
     - Buckets are keyed on each decision's earliest open/superseded/settled row (verified evidence: earliest verified row) and graded at that row's own line and price: own settled result, else final scores from the event's settled rows, else a same-key same-line settled result, else ungraded.
     - Archived/removed rows are never counted.
     - The last pre-game settled-row record is shown only as a "Context, last pre-game run (old basis)" flag and never moves a verdict.
     - Why: the settled row is the last of up to four daily reruns, so bucketing on it conditions on later line movement. Cite the NBA total v9 under 5-8% before/after numbers from SCRATCH/gaq_compare.out and the orchestrator's tracker evidence (8 first-sight unders went 3-5, all 5 losers left the band).
     - Because all-rows and verified evidence each take their own first row, a bucket line can read more verified than graded decisions.
   - At the top of Section 3, under the existing dated-snapshot paragraph, add one sentence: the 2026-11-11 table and the NCAAB 5-8% snapshot were computed on the OLD last-pre-game-run basis and must be recomputed on the first-sight basis before any figure is cited.
2. docs/DEBUG.md: insert a new entry at the TOP, immediately above `## 2026-11-18 — health audit`. Heading: `## 2026-11-20 — Best-pick audit bucketed on the last pre-game rerun, not the first sighting (quick-261120-gaq)`. Use 3-5 bullets covering:
   - the defect: population selection, not arithmetic; pnl and results verified correct;
   - the evidence: NBA total v9 under 5-8% read 8W-1L +77.4%; tracker first-sight 3-5; first-in-band 11W-10L +3.5%; NBA moneyline pooled 5-8% +47.6% vs about +11%;
   - the fix and the commit hash from Task 1;
   - the measured first-sight vs last-run numbers and the ungraded-key count from SCRATCH/gaq_compare.out;
   - that last-run maps reproduce HEAD (equality gate: identical counts, pnl/staked within 1e-6).
3. .codex/skills/forecast-model-picks/SKILL.md: in the working tree only, change item 1 under "Before naming any pick best" (lines about 92-94, "Recompute the cited bucket from settled ledger rows with the tool's own dedup") so it reads that the bucket is recomputed from each decision's first-sighting row (earliest ledger row, graded at its own line and price) with the tool's own dedup, keeping the rest of the item. That section is someone else's uncommitted work, so do NOT stage this file. Leave it modified in the working tree and say so in the SUMMARY.
4. Commit:
   - Stage EXACTLY `docs/BEST_PICKS_AUDIT_FRAMEWORK.md` and `docs/DEBUG.md` by path.
   - Check that `git diff --cached --name-only` lists only those two.
   - Commit `docs(quick-261120-gaq): best-pick audit first-sight bucket basis; DEBUG entry`, with the session attribution line.
   - Immediately after committing, save the commit's file list: `git show --name-only --format= HEAD > "$SCRATCH/gaq_docs_commit.txt"` (same Bash call assigns SCRATCH). The verify reads only this file, so it stays valid after later commits move HEAD.
   - Do not edit .planning/STATE.md or ROADMAP.md; the orchestrator owns them.
   - Write the SUMMARY to the path in `<output>`, including the Task 2 output, the revert-proof transcript (failing assertion line), the live CLI wall time, and the note that SKILL.md is edited but uncommitted.
  </action>
  <verify>
    <automated>grep -c "first sighting" docs/BEST_PICKS_AUDIT_FRAMEWORK.md && sed -n 3p docs/DEBUG.md | grep -q "2026-11-20" && grep -q "first-sighting row" .codex/skills/forecast-model-picks/SKILL.md && grep -qx "docs/DEBUG.md" "$SCRATCH/gaq_docs_commit.txt" && grep -qx "docs/BEST_PICKS_AUDIT_FRAMEWORK.md" "$SCRATCH/gaq_docs_commit.txt" && ! grep -qvx -e "docs/DEBUG.md" -e "docs/BEST_PICKS_AUDIT_FRAMEWORK.md" -e "" "$SCRATCH/gaq_docs_commit.txt" && echo DOCS_OK</automated>
  </verify>
  <done>
    - The framework doc has the first-sight subsection, and Pillar 1 plus Section 3 are marked for the basis change.
    - The DEBUG.md top entry is dated 2026-11-20 with the evidence and the measured numbers.
    - The SKILL.md line is updated in the working tree and is not staged.
    - The docs commit holds exactly the two doc files.
    - The SUMMARY is written.
  </done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| audit script -> live ledger | the script reads the canonical SQLite ledger; any write there would corrupt the evidence store |
| audit verdict -> operator/widget | a mis-bucketed record presents noise as a CONFIRMED_BEST pick to the operator and the Rainmeter widget |
| executor -> shared working tree | unrelated uncommitted work from other sessions sits in the same tree |

## STRIDE Threat Register

| Threat ID | Category | Component | Severity | Disposition | Mitigation Plan |
|-----------|----------|-----------|----------|-------------|-----------------|
| T-gaq-01 | Tampering | live ledgers.db | high | mitigate | all reads go through `_connect_ro` (sqlite `mode=ro`); Task 2 builds engines only; no INSERT/UPDATE anywhere; archived/removed rows filtered in SQL, never mutated |
| T-gaq-02 | Tampering | best-pick evidence integrity (grading at the first-sight line) | high | mitigate | line-perspective and direction rules pinned by tests (a), (b), (e); ungradeable first rows are skipped, never filled from a later row; revert proof shows test (a) catches the old population |
| T-gaq-03 | Denial of service | external Rainmeter widget contract | medium | mitigate | return keys, basis strings, thresholds and `_connect_ro`/`DB_PATH`/`ET_TZ` unchanged; Task 2 asserts the widget-facing fields resolve; engine load time recorded vs the 0.5 s baseline |
| T-gaq-04 | Repudiation | shared working tree commits | medium | mitigate | explicit-path staging only, `git diff --cached --name-only` checked before each commit, `git show --stat` after; SKILL.md (foreign uncommitted section) left unstaged; no stash/checkout of the tree |
| T-gaq-05 | Elevation of privilege | auto-buyer / real-money path | low | accept | the script is report-only and nothing under src/, config/, the auto-buyer state, or the runtime root is edited; verdict labels do not feed the buyer |
</threat_model>

<verification>
- `ruff check scripts/audit_best_picks.py tests/test_audit_best_picks.py` and `ruff format --check` on both files are clean (baseline clean).
- `python -m pytest tests/test_audit_best_picks.py -o addopts='' -q` gives 17 passed. This is the only pytest; never the whole suite.
- Revert proof: test (a) fails with HEAD's engine and passes after the restore, with matching sha256.
- `python scripts/audit_best_picks.py` exits 0 on the live ledger (read-only).
- SCRATCH/gaq_compare.py prints `LAST_RUN_EQUALS_HEAD True` and reports the NBA under 5-8% records on both bases.
- Two commits, holding exactly {scripts/audit_best_picks.py, tests/test_audit_best_picks.py} and {docs/BEST_PICKS_AUDIT_FRAMEWORK.md, docs/DEBUG.md}. Nothing pushed. STATE.md and ROADMAP.md are untouched by the executor.
</verification>

<success_criteria>
- Bucket evidence comes from each decision's first sighting, graded at that row's own line and price, with archived/removed rows excluded and no fall-forward to later rows.
- The old settled-row record survives (identical counts, pnl/staked within 1e-6 of HEAD) as `last_run_by_*` maps and as `last_run_evidence` plus one context flag. Every external-facing key, basis string and threshold is unchanged.
- The regression test is proved to catch the old behaviour, and the live before/after numbers for NBA total v9 under 5-8% are reported in the SUMMARY and DEBUG.md.
</success_criteria>

<output>
Create `.planning/quick/261120-gaq-re-key-best-pick-audit-edge-buckets-on-f/261120-gaq-SUMMARY.md` when done.
</output>
