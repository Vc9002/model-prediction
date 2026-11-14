---
quick_id: 261114-reg
slug: disable-out-of-scope-registry-entries-prune-scripts
date: 2026-11-14
status: complete
---

# Disable the out-of-scope registry entries and prune orphaned scripts

Operator: "do everything" (steps 3 and 4 of the simplification plan after 261113-smp and
261114-tfx). Make the registry say what the scope says, and remove scripts nothing uses.

## Tasks

1. **Registry.** In `config/production.yaml` set `enabled: false` on every model entry whose
   sport is outside NBA/NCAAF/NFL/MLB/WNBA/NCAAB and remove those sports from `champions:`
   and `challengers:` (textual edit; comments preserved). Trim
   `model_lifecycle.SUPPORTED_MARKETS` to the six sports. Pre-check: settlement never
   consults the registry, so the four open Euroleague/hockey Flat rows still grade.
2. **Scripts.** Build a reference index over src, tests, docs, ops, scheduler files and
   intra-scripts imports; delete only git-tracked scripts referenced nowhere that are either
   for a switched-off sport or one-off research runners. Keep operator tools, backfills,
   artifact freezers and shared helpers. Refuse any deletion that a kept script imports.
3. Fix the stale test pins this exposes and the ones already failing at HEAD (verified by
   running them against a `git archive HEAD` tree): entry counts, champion sets, NCAAF v5
   evidence status, nine missing catalog descriptors, a network-dependent settlement test.
4. ruff; registry-adjacent targeted runs; compileall scripts (the CI step); commit; push.
