# Project status and source of truth

**Last verified**: 2026-08-05 (rebuild E2E: 53-game MLB training, 3-fold CV, Brier 0.243, ECE 0.02). 752 tests pass (699 legacy + 49 rebuild + 4 pipeline).

This document is the operational status entry point. `MASTER.md` (repo root)
is now the most current, most detailed running log of real bugs found/fixed
with full evidence — trust it over this file when they disagree on specifics;
this file exists to be the short, current summary someone can read first.
`DEBUG.md` contains older audit evidence and reproduction commands.
Historical metrics in old reports, changelog entries, model cards, and
rollback artifacts are not current operational truth.

**Operating note**: day-to-day work on this project prioritizes *wiring and
features* over validation metrics — is a model actually running in `daily`,
and on what data, not its hit rate or promotion-gate status. The release
verdict below is a separate, narrower claim about real-money execution safety
specifically, and remains unchanged by wiring work.

## Active model versions (2026-08-04)

| Sport | Active artifact | Status | Hit rate | Qualification |
|---|---|---|---|---|
| MLB moneyline | `mlb-elo-trend-lr-v8` | shadow_qualified (override) | 58.5% locked-holdout at the operator-lowered threshold (target_hit_rate=0.60) | `qualified=false` — validation Brier regressed vs. v7's retired feature set, and holdout no longer clears the 60% bar at this looser, coverage-optimized threshold either. Both honestly listed in the artifact's own `qualification.failures`. Real per-starter `starter_era_gap` feature (`features/starter_history.py`), replacing v7's team-level `pitcher_era_gap`. |
| MLB spread | `measured-edge-margin-v3` | active_research | — | Real, sized Main-ledger rows (gated on both confirmed starters, matching moneyline). Real Poisson-GLM elasticity refit promoted 2026-08-04 (F-62): diagnostic correlation 0.2057→0.208, hit rate 59.5%→60.0% |
| MLB totals | `measured-edge-totals-v3` | active_research | — | Real, sized Main-ledger rows. **Still not fixed** — same elasticity refit promoted 2026-08-04 (shared Trend Engine with spread), but totals specifically got marginally *worse* (correlation 0.0585→0.0414, hit rate 55.3%→52.9%). The previously-reported 71% over-pick figure could not be reproduced against the full diagnostic dataset in either formula version; confirms rather than resolves the standing diagnosis that totals needs an absolute-run-environment signal, not better relative elasticities (P1-17/F-62 in `MASTER.md`) |
| NBA moneyline | `nba-elo-trend-lr-v4` | shadow_qualified | 73.66% | `qualified=true` |
| WNBA moneyline | `wnba-elo-trend-lr-v4` | shadow_qualified | 67.48% | `qualified=true` |
| NFL moneyline | `nfl-elo-trend-lr-v4` | shadow_qualified | 71.26% | `qualified=true` (offseason) |
| Soccer | `soccer-poisson-dc-v1` | shadow_qualified (operator override) | 62.5% locked-holdout | No walk-forward artifact exists; override not genuine promotion |
| LOL | `lol-tiered-elo-v6` | shadow_qualified (override) | — | v6 Platt-scaled. **Fixed 2026-08-04 (F-63)**: added inactivity decay + thin-data confidence discount — real ~33% reduction in mean predicted edge for thin-data matchups on held-out data, at a disclosed locked-test accuracy cost (70.6%→69.2%) |
| CS2 | `cs2-tiered-elo-v6` | shadow_qualified (override) | — | Same v6 fix as LOL (F-63); this title's locked-test accuracy improved slightly (65.8%→66.0%) |
| Dota 2 | `dota2-tiered-elo-v5` | shadow_qualified (override) | — | v5 Platt-scaled |
| Valorant | `valorant-tiered-elo-v5` | shadow_qualified (override) | — | v5 Platt-scaled |
| Rainbow Six | `rainbow_six-tiered-elo-v5` | research | — | v5 Platt-scaled |
| KBO | `kbo-tie-aware-elo-v2` | shadow_qualified (override) | — | Tie-aware, zero-unit research only |
| NPB | `npb-tie-aware-elo-v2` | shadow_qualified (override) | — | Tie-aware, zero-unit research only |
| Tennis | `tennis-surface-elo-v1` | research | — | WTA + ATP (ATP added 2026-08-03; ITF still unbuildable — no ESPN data source) |

## Ledger routing (definitive: `docs/LEDGER_ROUTING.md` — itself stale, verify against `main_ledgers.py`/`research_ledgers.py` directly)

Restructured 2026-08-03/04: Main and Flat are now **per-sport files**, not one shared workbook.

- **Main** (`data/main/{sport}.xlsx`: mlb, wnba, soccer, tennis): MLB (moneyline/spread/total, no edge gate — trust/provenance only, separate confidence-threshold gate in `cli.py`), WNBA moneyline (same), soccer/tennis (real edge+confidence gate) — real-sized calls
- **Flat** (`data/flat/{sport}.xlsx`: mlb, nba, nfl, wnba, soccer, tennis): every candidate, every one of those sports, no gate at all
- **Research** (`data/research/{sport}.xlsx`): Esports (5 titles), KBO, NPB — all candidates
- **Gated Research** (`data/gated_research/{sport}.xlsx`): Curated subset clearing per-sport edge/confidence bars
- **Model Ledgers** (`data/model_ledgers/`): per-model-identity architecture (additive; existing pipeline unchanged)

## Runtime snapshot (2026-08-04)

- **Tests**: 686 passed, 0 failed
- **Ruff**: 126 findings in `src/ tests/` (baseline; not chased — mostly `EXE002` shebang-on-test-files and pre-existing type-adjacent findings)
- **Audit chain**: `verify-chain` reports `chain_intact: true`, 0 breaks
- **Git**: `main`, HEAD `31d3b7c`, pushed to `origin/main`. Working tree has ongoing daily-pipeline data drift (normal; ledgers/odds/availability snapshots are tracked in this repo by design)
- **CI**: `.github/workflows/ci.yml` — ruff + pytest on push/PR
- **Dashboard**: Live at `127.0.0.1:8765`, launchd-managed, per-session token-based auth on orders. `_pick_quote` (order-readiness) now correctly resolves spread/total, not just moneyline (F-53, 2026-08-04) — previously every real MLB spread/total order was unconditionally refused
- **Daily pipeline**: Running through 2026-08-04, real `--log` runs verified end-to-end today, logs in `data/logs/`. New capture step (`cli.py::_capture_mlb_starter_snapshots`) keeps `data/mlb_statsapi/game_snapshots.jsonl` current — was previously a one-time static dump with no live-update path
- **Console entry point**: `.venv/bin/model-prediction` works
- **BBO capture**: Active across 8 sports (`data/odds/`)
- **Known, unresolved, non-code issue**: The Odds API key appears genuinely invalid — all 12 configured soccer leagues on that provider return `401 Unauthorized` (verified live 2026-08-04). Soccer's ESPN-sourced leagues are unaffected. Needs a real key rotation, not a code fix.
- **Repo hygiene**: `data/mlb_statsapi/game_snapshots.jsonl` (85MB) and `data/events.jsonl` (61MB) both exceeded GitHub's 50MB recommended file size and were growing toward the 100MB hard cap. **Fixed 2026-08-05**: both now tracked via Git LFS (forward-only, per explicit operator choice — existing git history untouched, every commit from this point forward stores these two paths as LFS pointers instead of full blobs). Verified: `git lfs status` shows both objects successfully pushed, full test suite green with LFS active, `.git/hooks/pre-push`'s existing pytest/mypy gate merged with (not overwritten by) the LFS pre-push hook.

## Release verdict

**The 6 originally-identified P0 real-money-execution defects are resolved or confirmed non-issues** (verified 2026-08-03/04, full evidence in `MASTER.md`'s P0 section and Fixed Bugs log):

1. Execution-ticket binding — resolved 2026-08-03, extended to spread/total/btts (F-49), and the dashboard-side gap that made that fix unreachable from the real order flow is also now fixed (F-53, 2026-08-04)
2. Ledger/audit atomicity — confirmed the original claim was backwards (audit is appended *before* the ledger write); true cross-file atomicity across separate files still doesn't exist as a lower-severity, real architectural gap
3. Artifact qualification / quote `timestamp_valid` enforcement — resolved as a deliberate operator decision (qualification no longer gates classification) plus re-verified `timestamp_valid` handling is correct everywhere it applies
4. `market-residual-v1.json` — resolved 2026-08-03 (F-50), real artifact trained, wired as diagnostic-only
5. MLB spread artifact reused for totals — resolved 2026-08-03 (F-51), both now point at their own real, live Measured Edge artifacts
6. Two "mismatched" artifact hashes — confirmed never a real bug, an artifact of the verification script's own wrong JSON convention

**That does not mean real-money execution should be turned on.** Separate from the 6 original defects:

- MLB v8 (the active moneyline artifact) is honestly `qualified: false` — real, positive signal (58.5% holdout hit rate, well above the 50% coin-flip line) but does not clear this project's own 60% promotion bar, on top of a validation-set Brier regression vs. the feature set it replaced. It is live via the same operator-override mechanism v7 used, not because it passed cleanly.
- MLB totals still has a known, unfixed accuracy gap — a real elasticity refit was attempted and promoted 2026-08-04 but honestly did not improve it (P1-17/F-62); needs an absolute-run-environment-specific model change, not another elasticity refit.
- Esports Elo's thin/stale-data overconfidence gap has a real fix now (F-63, 2026-08-04): inactivity decay + thin-data shrink reduced mean predicted edge on genuinely thin-data matchups by ~30-35% across all 5 titles on real held-out data, at a modest, disclosed locked-test accuracy cost in 4 of 5 titles.
- The general cross-file ledger/audit atomicity gap (item 2 above) is real, if lower-severity than originally described.

Do not infer executable profitability from artifact hit rates, synthetic
`-110` units, shadow-ledger P&L, or a dashboard qualification badge.

## Bugs found and fixed since last verification (2026-08-02 → 2026-08-04)

See `MASTER.md`'s Fixed Bugs log (F-47 through F-63) for full evidence on each. Highlights: dual-ledger duplicate-row gap for soccer/tennis re-runs (F-47); two active model coefficients (`bullpen_weakness_gap`, `defensive_trend_gap`, then `starter_era_gap`) silently missing from the audit ledger despite scoring correctly — the same recurring bug class, three times (F-48, F-55); dashboard order-readiness moneyline-only gate blocking every real MLB spread/total order (F-53); MLB v8 promotion with a real starter-identity feature and its own live infrastructure (F-54); a redaction fix that crashed instead of redacting, breaking soccer score collection (F-56); registry-free team-ban support completely non-functional in two independent ways (F-58); a WNBA availability fail-closed fix silently defeated by a pre-existing exception wrapper written for a different purpose (F-59); an unbounded pagination loop hardened with a page cap (F-60); MLB totals elasticity refit promoted with an honest (partially negative) result, plus a real bug in the calibration script that would have broken it (F-62); esports inactivity decay + thin-data confidence discount promoted for all 5 titles, real ~30-35% edge reduction on thin-data matchups verified against held-out data (F-63).

## Safe command forms

```bash
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check src/ tests/
.venv/bin/model-prediction --help
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli verify-chain
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli summary
```

Commands with `--write-artifacts`, `--log`, settlement, ledger cleanup, ban
mutation, dashboard POST routes, `daily`, `execute`, or `sell-position` change
state. They require separate authorization appropriate to the risk.

## Repair order (remaining, real, lower-severity than the original P0 list)

1. ~~Give cross-file ledger-mutation-plus-audit-append real transactional recovery (retry/failure-injection tests)~~ — turned out to already exist (`tests/test_ledger_hardening.py::test_ledger_write_crash_leaves_a_recoverable_audit_event_not_a_silent_gap`, `test_audit_append_happens_while_the_ledger_lock_is_still_held`), this doc's own claim was stale; verified 2026-08-04 and extended to also confirm `_verify_chain` itself (not just raw data inspection) detects the orphaned-audit-event case. True cross-file atomicity across separate files (ledger + audit as one physical transaction) still doesn't exist — that's a real, distinct, lower-severity architectural gap from "no recovery tests," which is now closed.
2. Build a real absolute-run-environment signal for MLB totals (P1-17/F-62's own next step: `totals_specific_market_residual` or `branched_absolute_run_intensity_head`, per `config/model.yaml`'s `problem_cohorts.totals`) — a relative-elasticity refit was tried 2026-08-04 and honestly did not help.
3. ~~Add confidence discount / inactivity decay to the esports `NeutralElo` model.~~ Done 2026-08-04, see F-63.
4. Rotate The Odds API key.
5. ~~Move `data/mlb_statsapi/game_snapshots.jsonl` and `data/events.jsonl` to Git LFS before either crosses GitHub's 100MB hard cap.~~ Done 2026-08-05, forward-only (see F-65).
6. Split `cli.py` and `dashboard_server.py` into packages (both remain large, growing files).
7. Migrate ledger storage to SQLite for ACID guarantees (long-standing item, unchanged).
