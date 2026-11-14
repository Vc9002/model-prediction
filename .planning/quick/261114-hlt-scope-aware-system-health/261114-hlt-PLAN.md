---
quick_id: 261114-hlt
slug: scope-aware-system-health
date: 2026-11-14
status: complete
---

# Make system_health scope-aware

Found while verifying the merged scope cut live: `system_health` was DEGRADED with seven
reasons, every one of them expected (MLB/WNBA off-season capture and snapshots, KBO/NPB
switched-off snapshots, WNBA production canary with no games to predict). A verdict that is
always DEGRADED hides real findings.

## Tasks

1. `_inactive_sports()` reads `config/model.yaml` through `is_sport_daily_enabled`, the same
   gate the daily run uses; capture/market staleness on those sports is recorded
   (`stale: false`, `inactive: true`) but never degrades; the primary's prediction age becomes
   a note when its sport is inactive. No `model.yaml` (test repos) means nothing is inactive.
2. Two tests: switched-off and off-season sports do not degrade while a still-active quiet
   sport is flagged; an off-season primary makes prediction age informational.
3. Verify live against the runtime root; commit; PR 37; merge on operator instruction.
