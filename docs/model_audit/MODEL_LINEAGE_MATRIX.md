# Model Lineage Matrix

**Date**: 2026-08-11. Tracks, per sport, the incumbent operational model, the archived
research source it descends from (if any), the rebuild's current serving fallback, and
the rebuild-native curated candidate's status. See `docs/model_audit/ARCHITECTURE_CORRECTION.md`
for the binding rule this matrix enforces: incumbent and rebuild-native entries are always
independently trained/served — a rebuild-native artifact never loads, aliases, or shares
state with its incumbent counterpart.

| Sport | Incumbent (operational control) | Archived source | Rebuild fallback (current primary) | Rebuild-native curated candidate |
|---|---|---|---|---|
| WNBA | `wnba-elo-trend-lr-v4` — shadow_qualified, 67.48% hit rate | `origin/rebuild/wnba-v1` — `features.py`/`horizon_builder.py` recovered and ported (PR #24); `baselines.py` rights-blocked, reference only | `_BasicEloAdapter` (generic binary Elo) | Data foundation real and audited (2022-2025, PR #24). Model fitting (`wnba-elo-trend-lr-rebuild-v1`) in progress — see `docs/model_audit/models/WNBA_ELO_TREND_LR_REBUILD_V1.md` once landed. |
| Soccer | `soccer-poisson-dc-v1` — shadow_qualified (operator override), 62.5% locked-holdout; no persisted qualification artifact (`config/model.yaml` comment only) | — | `_BasicEloAdapter` (binary, semantically inadequate for a 3-way sport — deliberate foundation stage, not a bug, per `ARCHITECTURE_CORRECTION.md`) | Data foundation audited (PR #22): 395 matches, 6 ESPN leagues, `NO-GO` for fitting yet — provider only covers 6 of the incumbent's 19 leagues, and all rows are capture-time-only provenance (blocks chronological validation). Model fitting not started. |
| Tennis | `tennis-surface-elo-v1` — shadow_qualified, 65.5% hit rate, 4,269 locked-holdout calls; no persisted qualification artifact | `origin/rebuild/tennis-v1` predates the TennisMyLife/ESPN provider replacement — not a curated transplant source, tennis was built new against real providers | `_BasicEloAdapter` | Data foundation audited (PR #20): 27,949 real ATP+WTA matches 2021-2025, `HEALTHY` per `rebuild-data audit`. Two real normalizer bugs found and fixed (schema-inference crash blocking all backfills; silent match-collision data loss, recovered 480 matches). `GO` for fitting on TennisMyLife history now; cross-provider (TennisMyLife↔ESPN) identity resolution still needed before live serving. Model fitting not started — queued after WNBA. |
| NFL | `nfl-elo-trend-lr-v4` — shadow_qualified (offseason), 71.26% hit rate; artifact's own `expected_calibration_error: 0.1009`, confirmed worst-calibrated of the tracked models | — | `_BasicEloAdapter` | Data foundation audited (PR #23): `rebuild/nfl/*` was already substantial real infrastructure (nflverse client, PIT filtering, content-addressed storage) — the gap was exercising it. One real bug fixed (schema-inference crash on nullable columns). Schedule 2021-2025 clean (1,424 games). `weekly_rosters` blocked (real GSIS-identity gap, doesn't affect Elo/trend). EPA/CPOE/pressure features remain blocked pending calendar-time daily capture — not to be worked around. `GO` for `elo_probability`+`trend_gap`; calibration should be the first priority once trained, per the audit's confirmed ECE finding. Model fitting not started — queued after Soccer/Tennis. |
| NBA | `nba-elo-trend-lr-v4` — shadow_qualified, 73.66% hit rate; Elo leakage question formally resolved (67 games traced, zero invariant violations, `docs/model_audit/models/NBA_ELO_TREND_LR_V4.md`) | None — no `origin/rebuild/nba-v1` branch has ever existed in this repo | `_BasicEloAdapter` | **Blocked** (PR #21): no rebuild data-ingestion layer exists at all — `rebuild-data backfill/audit --sport nba` returns `NOT_IMPLEMENTED`, no `src/model_prediction/rebuild/nba/` package. WNBA's 8-module package is a near-direct architectural template (shared ESPN/basketball schema) for whoever builds this. Not attempted in this pass per "document blockers, don't fabricate capability." |
| MLB (moneyline) | `mlb-elo-trend-lr-v8` — `qualified: false` on its own artifact (validation Brier regressed vs. holdout hit rate); live threshold (0.619665) does not match what `MASTER.md`/`docs/PROJECT_STATUS.md` describe as current (0.587335) — that commit exists in history but was never merged to main, unresolved, needs an operator decision | `origin/rebuild/mlb-v3-research` — already substantially incorporated into main's real MLB v3 data foundation | MLB already has a real, independent rebuild-native stack (not `_BasicEloAdapter`) | `mlb_moneyline_v2_frozen_v1` (clean-slate XGBoost two-head + negative-binomial) exists, code-complete, fail-closed, but `sealing_required` — 0 of 100 required real games evaluated. `KEEP_CHALLENGER` per the original audit; corrected benchmark too close to naive baseline to justify replacing v8. Feature refresh queued last, after WNBA/Soccer/Tennis/NFL/NBA. |
| MLB (spread/total) | `mlb-analyst-poisson-trend-v0.3` via `measured-edge-margin-v3`/`measured-edge-totals-v3` — real, sized Main-ledger rows historically; totals has a known, unfixed absolute-run-environment accuracy gap | — | Part of the existing MLB rebuild stack | Not yet separately curated; queued with the MLB feature refresh. |

## Reading this table

- **"Incumbent"** columns describe the live, operational system — untouched by any rebuild
  work, serving real predictions today via the existing `daily` pipeline.
- **"Rebuild fallback"** is what `rebuild-shadow` actually serves for that sport *right now*
  if you run it today. For everything except MLB and (once landed) WNBA, that's the
  generic `_BasicEloAdapter` — a deliberate, disclosed foundation stage, not a defect.
- **"Rebuild-native curated candidate"** status reflects real, verified work — a data
  foundation audit that actually ran backfill commands against live providers, not a plan.
  "Blocked" and "NO-GO" verdicts are honest findings, not failures to hide.

## Update this file

Every sport's data-foundation or model-fitting PR should update its row here. Do not let
this drift from `config/model.yaml`/`config/rebuild.yaml`'s real state — if this table and
the config disagree, the config wins and this file needs fixing, not the other way around.
