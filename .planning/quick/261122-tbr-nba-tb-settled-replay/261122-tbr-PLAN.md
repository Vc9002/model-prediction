---
quick_id: 261122-tbr
slug: nba-tb-settled-replay
date: 2026-11-22
mode: quick (inline, ralph round 1)
---

# NBA T-B replay over every settled game (read-only)

Goal context: ralph loop "NBA positive PnL on all settled games, gated but not cherry-picked,
profitable each week".

## Task

Add `scripts/nba_v10/tb_settled_replay.py`, read-only:

1. Harness proof: rebuild every settled v9 / lr-v5 Flat row's executable ask from the archived
   Polymarket snapshots (`odds/nba/<date>/polymarket_snapshots.jsonl`, matched on market slug and
   quote time), and regrade it from the stored scores. Report the match rate; stop if it is low.
2. Apply the pinned T-B rule (plan section 9: devigged DraftKings probability minus Polymarket ask
   >= 3.0 points, gross of fee) to every settled game, with these rules fixed before any outcome
   is looked at:
   - Quote: the last Polymarket snapshot per market observed in [tip - 120 min, tip - 5 min].
   - DraftKings: the live ESPN capture (PIT) when present, else the backfilled close
     (look-ahead: labelled an optimistic upper bound).
   - Primary cut: moneyline (direct two-way devig) and spread/total contracts at the same line as
     DraftKings (fair 50/50 when no juice is stored). Translated lines reported separately.
   - One contract per game/market, the side with the largest gap.
3. Output: n, stake, PnL gross and net of the 0.06*p*(1-p) fee, game-cluster bootstrap CI, and
   PnL per ISO week, to `outputs/nba/TB_SETTLED_REPLAY.md`.

No `src/` changes. No threshold scan.
