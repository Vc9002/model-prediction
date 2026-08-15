# Tennis rebuild data foundation

Groundwork pass, 2026-08-11, branch `rebuild/tennis-model-curation-v1` (from
`origin/main`). **Data audit and architecture prep only — no model fitting.**
Builds on the confirmed findings in
`docs/model_audit/models/TENNIS_SURFACE_ELO_V1.md` and
`docs/model_audit/features/TENNIS.md` (not re-derived here): the incumbent
`tennis-surface-elo-v1` stays exactly as-is, is fed by ESPN alone today, and
the feature registry's old "no active tennis model uses surface" claim was
wrong. This document covers only the separate, unpromoted `rebuild/tennis/`
data track and whether it is a safe foundation for a future rebuild-native
Surface Elo candidate.

Raw output: `outputs/rebuild/tennis/data_audit.json`. Runtime data itself
lives under `data/rebuild/normalized/tennis/` (gitignored, not committed, per
`docs/rebuild/ARCHITECTURE.md`).

## What exists before this pass

Nothing. `data/rebuild/` did not exist at all in this checkout before this
session — the rebuild tennis track (`src/model_prediction/rebuild/tennis/`,
`providers/tennis_mylife.py`, `providers/tennis_espn.py`) is real, tested
code, but had never actually been run against live data in this checkout.
This pass is the first real backfill.

## What was backfilled

Real network calls against the live TennisMyLife (`stats.tennismylife.org`)
and ESPN (`site.api.espn.com`) endpoints, via
`rebuild-data backfill --sport tennis`:

- **Historical match data (TennisMyLife, `--kind main`)**: ATP and WTA,
  seasons 2021–2025 (5 seasons × 2 tours = 10 season-files).
- **Current-window scoreboard (ESPN, `--current`)**: ATP and WTA, one
  capture each, 2026-08-11.
- 2026 (the current, still-in-progress TennisMyLife season) was attempted
  and explicitly **not** included — see Known Issues below.

### Coverage numbers (real, from `rebuild-data audit --sport tennis`)

| tour | seasons | matches | current_events |
|---|---|---|---|
| ATP | 2021–2025 | 14,668 | 548 |
| WTA | 2021–2025 | 13,281 | 548 |
| **combined** | | **27,949** | **1,096** |

Per-season ATP: 2021=2,735, 2022=2,918, 2023=2,995, 2024=3,159, 2025=2,861.
Per-season WTA: 2021=2,597, 2022=2,612, 2023=2,801, 2024=2,752, 2025=2,519.
Date range: `tourney_date` 2021-01-04 to 2025-12-22. Zero duplicate rows,
zero missing-winner-identity rows, `rebuild-data audit` reports `HEALTHY` for
both tours.

Surface breakdown (from TennisMyLife's own `surface` column, not ESPN's
tournament-name keyword heuristic the incumbent model relies on):

| tour | Hard | Clay | Grass | missing |
|---|---|---|---|---|
| ATP | 8,710 | 4,318 | 1,587 | 53 (0.39%) |
| WTA | 8,154 | 3,528 | 1,542 | 57 (0.43%) |

Result-type classification (retirement/walkover/default never counted as an
ordinary win/loss): 26,903 `completed`, 830 `retirement`, 211 `walkover`, 5
`default` — internally consistent, `completed` flag matches `result_type` in
every row.

### Player identity (provider-scoped, per `docs/rebuild/OPERATIONS.md`)

Cross-provider resolution (TennisMyLife ↔ ESPN) is real, separate,
not-yet-built work, per `docs/rebuild/OPERATIONS.md` — not attempted here.
Within TennisMyLife's own id space over this window: 1,545 unique
provider-scoped player ids (823 ATP, 722 WTA), zero empty provider-player-id
rows, zero player names mapping to more than one provider id. This is a
reasonable signal of within-provider key stability over 27,949 matches, not
proof of full identity correctness, and says nothing about matching a
TennisMyLife player to their ESPN counterpart.

### PIT check

Zero rows in the `matches` table have a `tourney_date` after their own
capture date — no PIT violation found. **Structural caveat, not a bug**:
TennisMyLife's `tourney_date` is date-only, one value per tournament (not
per match), so `matches` rows carry `availability_basis: "capture_time_only"`
— they prove what TennisMyLife published and when this repo captured it,
never a fine-grained historical match-start vintage. `current_events` (ESPN)
does carry a real `event_start_utc`, but the capture window itself spans
2026-08-01 through 2026-08-23 (a rolling ~3-week schedule, not literally
"today"), and includes `STATUS_SCHEDULED` (not-yet-played) rows. The
foundation module keeps `matches` and `current_events` in physically
separate tables/partitions for exactly this reason — a future model must
never let a `current_events` scheduled-match row leak into training history.

## Real bugs found and fixed this pass

Both fixes are narrow, in `src/model_prediction/rebuild/tennis/normalize.py`
— data normalization/ingestion code, not model-fitting code, so in scope for
this groundwork pass. Both were required to get *any* real backfill to
succeed or to be complete; neither touches `TennisModel`, the incumbent, or
any model-fitting logic.

1. **Schema-inference hard crash (blocked every tennis backfill outright).**
   `normalize_tennismylife_matches`/`normalize_espn_scoreboard` built
   `pl.DataFrame(rows)` from a list of dicts using Polars' default 100-row
   schema sniff. `winner_seed`/`loser_seed` are null for most of a season's
   first 100 rows (unseeded early-round players sort first in TennisMyLife's
   file order), so Polars inferred those columns as all-null, then hard-
   crashed the instant a later row supplied a real seed string
   (`could not append value: "4" of type: str to the builder`). This made
   every tennis backfill call in this repo fail deterministically before the
   fix — reproduced live on ATP 2025 pre-fix. **Fix**: pass
   `infer_schema_length=None` (full scan) at both call sites. Verified: all
   10 season backfills plus both `--current` captures succeeded after the
   fix.

2. **`match_num`-null canonical-id collision (silent data loss, more severe
   than the crash above because it produced no error at all).**
   `canonical_match_id` was built as
   `f"tennis_mylife:{tourney_id}:{match_num or 'na'}"`. TennisMyLife leaves
   `match_num` null for some marquee events — confirmed for the **entire
   2025 ATP US Open singles draw** (127 matches, `tourney_level='G'`), plus
   Shanghai/Cincinnati Masters, Winston-Salem/Beijing/Tokyo/Hangzhou/Chengdu,
   and the Laver Cup (489 rows total across the 10 backfilled season-files —
   concentrated entirely in the ATP 2025 file; no other season/tour in this
   range had any null `match_num`). Every match inside one such tournament
   collapsed onto the *same* `canonical_match_id`
   (`tennis_mylife:2025-560:na` for all 127 US Open matches), and
   `TennisNormalizedStore.read_matches()`'s keep-last primary-key dedup then
   silently kept exactly 1 of every group — the 2025 US Open effectively
   vanished from the normalized store except for one arbitrary match, with
   zero error or warning surfaced anywhere. **Fix**: when `match_num` is
   null, `canonical_match_id` now falls back to
   `f"na-{round}-{winner_id}-{loser_id}"` instead of a bare `"na"`. Verified
   collision-free against all 10 real backfilled files (a pairing plays at
   most once per round within one tournament, so round+winner+loser is
   unique in practice). **Impact recovered**: ATP 2025's stored match count
   went from 2,381 (silently missing 480 real matches) to the correct 2,861
   — confirmed to exactly equal the raw TennisMyLife file's own row count
   for 2025-dated matches.

Both fixes are covered in the raw findings in
`outputs/rebuild/tennis/data_audit.json` under `known_issues`.

## Known issue found and deliberately NOT fixed

**2026 (current partial season) backfill fails closed on 3 dirty upstream
rows.** `rebuild-data backfill --sport tennis --tour atp --season 2026`
raises `ValueError("missing tennis loser_id")` and aborts the whole season.
Root cause verified directly in the raw 2026.csv: 3 of 1,895 rows (Miami
Masters R64, Bastad R16, Kitzbuhel R32) have a real `loser_name` and score
but a null `loser_id` — a genuine upstream identity-lookup gap on
TennisMyLife's side for the still-in-progress season, not a parsing bug in
this repo. `normalize.py`'s `_required()` fail-closed behavior (raise rather
than fabricate a player id) is the correct, deliberate posture here,
consistent with this project's stated philosophy of declining to answer
rather than guessing on missing data. The open question is scope, not
correctness — whether a future change should let one dirty row fail closed
without aborting its *entire* season file (skip-and-count, logged) versus
today's all-or-nothing behavior. That is a policy decision for the next
phase, not something to change silently in a data-foundation-only pass.
Consequence: 2026 is excluded from `seasons_backfilled`; near-term coverage
instead comes from the ESPN `--current` capture, a materially different
source with its own limitations (rolling ~3-week window, no true historical
vintage, singles-only).

## Rights posture (unchanged, verified consistent with existing code)

Both providers already carry conservative rights profiles wired into
`SourceRightsProfile`/`TENNIS_MYLIFE_RIGHTS`/`TENNIS_ESPN_RIGHTS`:
`upstream_rights_status: unresolved`, `commercial_use_status: unresolved`,
`use_scope: research_shadow_only`, `production_allowed: False` for both. No
new evidence surfaced during this pass changes that posture in either
direction — this backfill checked out clean per the task's safety
instruction: nothing found suggests TennisMyLife match-result *ingestion*
itself is rights-blocked, only that production *serving* from it remains
gated pending a real licensing resolution (unchanged from
`docs/rebuild/OPERATIONS.md`'s existing framing). Every normalized row
carries its own rights/provenance columns (`license_id`,
`upstream_rights_status`, `commercial_use_status`, `production_allowed`),
so any future consumer can check gating per-row rather than trusting a
global assumption.

## What tour/season/surface range is safe to eventually train on

- **Tours**: ATP and WTA both have real, healthy, PIT-clean coverage.
  Challenger/qualifying `--kind` values exist in the CLI but were not
  backfilled this pass (out of scope — "a handful of recent ATP and WTA
  seasons" per the task, and the incumbent model itself is main-draw only).
- **Seasons**: 2021–2025 is a safe, verified-clean 5-season window per tour
  (10,929+ combined matches per tour is a substantial sample; the incumbent
  `tennis-surface-elo-v1`'s own locked-holdout qualification ran on 4,269
  calls, for comparison). 2026 is not safe yet — excluded for the documented
  fail-closed reason above, not silently included with dirty rows.
  Extending further back (TennisMyLife has ATP files to 1967, WTA to 1990)
  is plausible for Elo cold-start depth but untested in this pass — every
  season doubles verification cost, and 5 recent seasons is enough for
  groundwork purposes.
- **Surfaces**: Hard/Clay/Grass all have solid depth per tour; missingness
  is low (0.39–0.43%) and comes directly from TennisMyLife's own `surface`
  column rather than the incumbent's tournament-name keyword heuristic —
  this is a genuine structural improvement path (no fail-open
  default-to-Hard behavior to inherit) *if* a rebuild-native Surface Elo
  eventually replays the incumbent's architecture on this data.
- **Player identity**: usable at the provider-scoped level described above
  for an Elo-style rating book keyed by TennisMyLife's own player id — but a
  rebuild-native model cannot yet be validated against ESPN's live
  scoreboard identity space (needed for eventual PIT-safe serving/matching
  to real market events) until the documented cross-provider identity
  resolution work is actually built. That is a real, sequenced dependency
  for the *next* phase, not a defect in this phase.

## What's still missing before model fitting could start (next phase, not this one)

1. Cross-provider (TennisMyLife ↔ ESPN) player identity resolution — needed
   to connect historical Elo-book identities to live-served matches.
2. A real historical match-start timestamp is fundamentally unavailable
   from TennisMyLife (date-only, tournament-level) — any future model's PIT
   contract has to be built around tournament-date granularity, not
   match-instant granularity, or accept ESPN's narrower, forward-only
   `event_start_utc` window as the only source of finer-grained timing.
2026-season backfill needs a scoped decision (skip-row vs. abort-season)
   before it can be included cleanly.
3. No decision made in this pass on Challenger/qualifying-level match kinds,
   Carpet-surface handling (none present in this window), or how far back to
   extend historical seasons for Elo cold-start depth.
4. This pass produced zero model code, zero feature-engineering code, and
   zero fitted parameters — by design (task scope).

## Go/no-go recommendation

**GO, with one blocking follow-up before the next phase begins**: the data
foundation itself (ATP/WTA, 2021–2025, main-draw, TennisMyLife-sourced) is
now real, verified, PIT-clean, and free of the two ingestion bugs that
previously made it either non-functional or silently incomplete. It is a
credible base for a curated rebuild-native Surface Elo candidate.

The blocking follow-up is **not** a data-quality gap in the backfilled
range — it's the still-unbuilt cross-provider player identity resolution
(`docs/rebuild/OPERATIONS.md`'s own stated "real, separate, not-yet-built
work"). Model *fitting* against TennisMyLife-only history can proceed on
today's data without it (an Elo book keyed by TennisMyLife's own player id
is self-consistent). But *serving* — matching a fitted model's players to
live ESPN-sourced events/markets — cannot be validated end-to-end until that
resolution work exists. Recommend sequencing the next phase as: (a) fit and
backtest against this TennisMyLife-only foundation first (self-contained,
unblocked today), (b) build cross-provider identity resolution as a
parallel or immediately-following track, (c) only then attempt a rebuild
`--current`/live-serving smoke test. Do not attempt to skip straight to (c).

No new evidence in this pass argues for expanding scope, relaxing the
`production_allowed: False` gate, or touching the incumbent
`tennis-surface-elo-v1` in any way — it remains untouched, as required.
