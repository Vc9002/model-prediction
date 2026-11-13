# Deferred: docs/ROADMAP.md item 5 close-out text

## Why this is deferred

The plan's Task 2 instructed editing `docs/ROADMAP.md` to mark item 5
("Edge scanner cross-date p_model fallback") as fixed. At execution time,
`git status` / `git diff --stat -- docs/ROADMAP.md` showed `docs/ROADMAP.md`
already has substantial unrelated uncommitted edits from another concurrent
session (72 insertions / 21 deletions across the file, unrelated to this
item). Per the operator's explicit deviation instruction for this task, this
executor did not touch `docs/ROADMAP.md` at all, to avoid co-mingling this
plan's change with that other session's in-flight, uncommitted edit -- which
could stage/commit content this plan never reviewed, or conflict with
whatever that session is about to commit.

This file records the exact text that would have closed out item 5, so a
future session (or the operator) can apply it once `docs/ROADMAP.md`'s
current uncommitted edits are resolved (committed or reconciled).

## Current state (as of this task, 2026-11-12)

`docs/ROADMAP.md` line 182 (at HEAD, pre-other-session's-edits) reads:

```
#### 5. Edge scanner cross-date p_model fallback (OPEN)
```

followed by its three existing bullet lines.

## Replacement text to apply

Replace that heading and its three bullet lines with:

```markdown
#### 5. Edge scanner cross-date p_model fallback — FIXED 2026-11-12

- `_lookup_model_prob` now requires the Main/Flat pick to be for the exact
  same real-world UTC calendar date as the market (parsed via
  `datetime.fromisoformat` and converted to UTC, not a raw `[:10]` string
  slice) before it can supply `p_model` -- a pick for a different date is
  never used, even when it is the only candidate (fails closed instead).
- No multi-day tolerance window. A live-data audit (quick task 261112-2s6)
  scanned the full historical Polymarket snapshot corpus against Main+Flat
  and found every date gap greater than zero days was a genuinely different
  real-world event (1 to 113+ days apart, including esports rematches of the
  same two teams weeks or months later); 1,070 of 6,037 ever-matched markets
  (about 17.7%) relied on this fallback and now correctly return no match.
- Some Main/Flat rows store `event_start_utc` with a local-offset notation
  (e.g. `-0400`) for the same real instant a market snapshot stores in `Z`
  notation, which a raw string slice misreads as a different calendar day;
  117 markets hit this. The fix compares true UTC calendar dates rather than
  string prefixes, so these still match correctly.
- Found during quick task 261112-01r and fixed here; see
  `tests/test_scan_polymarket.py` for the regression tests.
```

## Verification this text satisfies once applied

- `grep -c "Edge scanner cross-date p_model fallback — FIXED 2026-11-12" docs/ROADMAP.md` should print 1.
- `grep -c "Edge scanner cross-date p_model fallback (OPEN)" docs/ROADMAP.md` should print 0.
- The replacement cites both the 1,070-of-6,037 (~17.7%) genuinely-cross-date
  figure and the 117-market offset-notation finding.

## Source commit for the actual fix

`src/model_prediction/portfolio/polymarket_scanner.py` and
`tests/test_scan_polymarket.py` were already fixed and committed in this same
quick task (Task 1), independent of this deferred doc edit -- see
`261112-2s6-SUMMARY.md` for the commit hash.
