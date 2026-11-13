---
quick_id: 261113-smp
slug: simplify-project-scope-six-sports
date: 2026-11-13
status: complete
---

# Simplify project scope: six sports, paper + gated real money, research off the critical path, lean CLAUDE.md

Operator directive 2026-11-13 after a complexity review (157k source lines, 91 enabled
models, 4 ledger tiers, 23 research modules, 130 docs for one operator): focus on NBA,
NCAAF, NFL, MLB, WNBA and NCAAB; keep both paper trading and the gated real-money path
and say so once; freeze research/rebuild out of the test path; trim CLAUDE.md.

## Tasks

1. **Scope.** Set `daily_forecast_enabled: false` on SOCCER, TENNIS, LOL, CS2, DOTA2,
   VALORANT, RAINBOW_SIX, KBO, NPB, HOCKEY, DARTS, EUROLEAGUE, NBL in `config/model.yaml`
   (code, artifacts, calibrators untouched). Finding: those sports' daily tasks ran
   unconditionally; only MLB/WNBA and the learned pool consulted `is_sport_daily_enabled`.
   Gate the soccer, tennis, esports, international-baseball and additional-sport task
   submissions in `cli/daily.py` with the same function. Pin with
   `tests/test_focus_sports_config.py`.
2. **Paper vs real money, once.** Rewrite "What this project is" in the root `CLAUDE.md`
   as the single statement (paper is the operating mode; real-money path exists, gated,
   never scheduled). Fix `.claude/CLAUDE.md`, which said "traded with real money … live
   production trading system", to defer to it.
3. **Research off the critical path.** Move pure research-tooling tests to
   `tests/research/` and add `research` to `norecursedirs`; keep `tests/rebuild/` where
   CI runs it. Do not move modules: `champion_challenger`, the `*_replay` modules and
   the `research_*` pricing/ledger modules are imported by the live serving path (import
   graph checked, not names). Disable the `ModelPrediction-RebuildShadow` scheduled task
   in place and comment out its registration.
4. **Trim CLAUDE.md.** Keep durable rules; move the five dated 2026-08 sections verbatim
   into `docs/DEBUG.md`; restate their still-binding contracts as a short list.
5. ruff; targeted tests only (new scope test, the six path-edited research tests,
   daily-integrity tests); default-collection check proves research is excluded.

Not in scope: deleting any sport's code, changing ledger tiers, touching the auto-buyer,
merging the `refactor/cleanup-2026-11-11` branch, committing other sessions' unstaged
hunks in `config/model.yaml`, `scripts/register_windows_tasks.ps1`, `run_daily.ps1`,
`dash.ps1`.
