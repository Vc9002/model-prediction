# Addendum v5 to NCAAB research protocol 261120: event source for the player-data confirmation

Machine-readable pin: `protocol_261120_addendum_v5.json`. Canonical sha256 (as `grader.canonical_sha256`):

**`c11e1910cec9fbc1cc1c5ea7b9ef9422d4eb8b8eafae48e96f65c255b5df7b57`**

Pinned 2026-11-21 19:35 ET, before any 2014-2021 player box was fetched and before any confirmation feature, line
or outcome was joined to a P1 prediction. The JSON is binding; this page summarises it.

## What it amends

v4's `windows.confirmation.player_data`, and the confirmation seasons. The confirmation line source has spread
lines only for 2018, 2019, 2020 and 2022, so the read is pooled over those four (at least 3 must pass the 85% box
rule). P1 uses same-season inputs only, so only 2018-2020 boxes are fetched (2022 is on disk). Everything else in
v4 is unchanged, including P1's form, K, the 85% season rule, the pass rule and the unavailable rule. The fetch
options and the 2014-2021 event list below were drafted before this narrowing; the `f_confirmation_seasons` clause
in the JSON governs.

## Why

v4 says to fetch 2014-2021 boxes "with the pinned scripts, season list extended, nothing else". Both scripts read
events and team schedules from `data/research/ncaab_market/scoreboard.jsonl`, which starts 2021-11-01, so a longer
season list finds no 2014-2021 events.

## The changes

- **(a) Event source:** `replay_261120.load_confirmation_history()`, the same history the A_ml_w0 confirmation
  used.
  - 2014-15 is CBBD-converted from the local cache, with no API call.
  - 2016-22 is the ESPN scoreboard backfill.
  - Non-D1 stubs are demoted.
  - The new `export_player_events_v5.py` writes it in scoreboard row format to
    `data/research/ncaab_external/player_events_v5/events_2014_2022.jsonl`.
  - Its 2022 events match `scoreboard.jsonl` exactly (same ids and dates), so the 2022 boxes on disk are reused.
- **Code change:** both scripts take an `--events PATH` option, and the builder also takes `--seasons` and
  `--out-dir`.
  - The builder ignores box records outside `--seasons`, and the audit adds the D1 pass rate.
  - Every feature function is byte-identical; the diff touches only `audit()` and `main()`.
  - With default arguments the builder reproduces the v4 table. Every non-float value is equal, and two
    transfer-in fields differ by at most 2.2e-16.
- **(b) Re-pinned sha256 values:** the two changed scripts, the export script, the export file,
  `replay_261120.py`, the backfill scoreboard and the v4 table that (d) is fitted on.
  - Scripts are listed in both line-ending forms. With `core.autocrlf=true`, tracked scripts are stored LF and
    checked out CRLF, and v4's own pins mixed the forms.
  - The verifier should hash LF-normalised script content.
  - The confirmation feature table is hashed after it is built and before the read.
- **(c) Season usability:** the 85% rule is unchanged. Its input is the audit's
  `d1_team_games_box_pass_rate_by_season`.
- **(d) Coefficients:** one OLS fit of P1's form (no intercept, `dA` and `dL`) on all of 2023-2026 from the v4
  table, recorded before the read and applied to the replay's served-constant v7 mean.
- **(e) Date quirk:** disclosed and kept.
  - About 15 late West Coast and Hawaii games in development carry the scoreboard date, one day before their
    Eastern tip date.
  - 2 dev team-rows (verified) use a box from a game that tipped at 00:43 ET on the same Eastern day as the
    row's game: event 401484367, 2022-11-25. Their games tipped at 21:00 ET and 23:41 ET, about 20 hours later.
  - The confirmation export has the same quirk for 2016-22 (44 games) and none for 2014-15 (its dates are
    Eastern).
  - Fixing the quirk would change the pinned builder, so confirmation applies the same tolerance.

K is unchanged.

## Notes for the reviewer (not pinned clauses)

1. **Lines limit the effective confirmation seasons.** `spread_corr_delta_vs_v7` needs a closing line.
   - `espn_backfill_261120` has spread lines only for 2018 (4,447), 2019 (5,625), 2020 (5,331) and 2022, and CBBD
     lines are still refused.
   - So the read can only pool 2018, 2019, 2020 and 2022. 2015-2017 add nothing, whatever their box quality.
   - 2022 passes the 85% gate (D1 pass rate 0.9107, from the changed audit run through the export), so at
     least 2 of 2018-2020 must pass to reach the 3-season minimum.
2. **The fetch could be smaller.** P1 uses only same-season availability features (`avail_top7_prev1`,
   `top7_minutes_share_out_3plus`), which need no prior season.
   - Every season, full plan as drafted (v4's list): 45,899 events, about 8.7 h.
   - P1 plus line-bearing seasons only (2018, 2019, 2020): 17,819 events, about 3.4 h.
   - Adding 2017 as the prior season for 2018 (needed only if P2-type features ever matter): 23,783 events,
     about 4.5 h.
   - The draft keeps v4's season list. Narrowing it is your call; it would be a change to v4's season list.
   - Rates: 1.46 events/s, single worker, 0.6 s pause. The 2022-2026 run took 21,179 s for 30,991 events with no
     403/429.
   - Timing: an 8.7 h fetch started in the evening runs through 00:15-01:15 ET. The fetch itself is allowed then,
     but the audit and build must wait until after 01:15. The 3.4 h option finishes before that window.
3. **The v4 table is not byte-reproducible.** The pinned builder sums transfer shares over a Python set, so
   rebuilds differ in the last bit unless `PYTHONHASHSEED` is fixed. The draft runs the confirmation build with
   `PYTHONHASHSEED=0`, which was verified byte-deterministic.
4. **grader_v4 refuses development calls from this working tree** while the changed builder is checked out,
   because its hash no longer matches v4's pin. The v4 dev reads are done.
   - Restore with `git checkout 3d71b135 -- scripts/ncaab_research/player_features_261121.py`, which gives the
     CRLF form `a6efbde1` that v4 pinned.
   - `git show` prints the LF blob (`0855b5a1`), which v4's raw-byte check refuses.
