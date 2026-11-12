---
status: complete
branch: refactor/cleanup-2026-11-11
---
# Refactor / cleanup / optimize pass (2026-11-11)

Done on `refactor/cleanup-2026-11-11` (base: checkpoint `0af19ecd`):

- `58994447` removed 75 functions/classes with zero references anywhere in the repo (679 lines, 30 files).
- `08d0e9d9` safe ruff simplifications (RET505, UP015, C420, FURB110, SIM300) in 28 files.
- `7b0438f9` extracted the duplicated moneyline-supersede block shared by the WNBA and NBA spread forecasts.

Decided against:

- Splitting `cli/forecast.py` across modules. It is a single module by design: about 100 test patches
  (57 on `utc_now`) only reach call sites in the module they patch.
- Unifying the NBA/WNBA/NFL `_forecast_*_sport` wrappers. Their differences are sport-specific Main-ledger
  rules (edge gates, model state, record type).
- Performance changes. The 2026-11-11 daily log shows the cycle is bound by throttled network I/O:
  Polymarket BBO capture 540s (4 workers, 429 guard), auto-buyer cycle 272s, NCAAB settlement 73s.
  CLI import is 2.4s (scipy, sklearn, pandas, polars), all needed by the forecast path.

Not touched: `rebuild/` (39 unreferenced definitions listed by the scan), interface and decorated methods.

Failing before this work (same failures at `0af19ecd`): 9 in `tests/test_cli.py`, 1 in
`tests/test_five_blind_spot_guards.py`, 1 in `tests/test_forecast_runtime_regressions.py`,
1 in `tests/test_tennis_markov.py`.

## Pick-tag stages (2026-11-11/12)

- `PickTag` (research/shadow/qualified) derives from destination: Main/Flat ledger -> research, paper buyer -> shadow, live buyer -> qualified. Buyer jsonl rows store `pick_tag`; old rows are backfilled on read.
- Stage 1 (`3fbbf16a`): `ModelState` -> `PickRequest.model_enabled` (`lifecycle.model_enabled` reads `enabled:` then legacy `status:`; only retired/suspended mean off).
- Stage 2 (`0080f5bd`): `RecordType` -> `EligibilityResult.qualified`; stored `record_type` column spelled only in `domain.py` (`legacy_record_type`, `is_qualified_row`, `is_research_row`).
- Deliberately NOT done: config `status: shadow_qualified` -> `enabled: true`. `dashboard/evidence.py` reads config `status` as "config_qualified" and compares it with the artifact's locked-holdout result; migrating the yaml would raise mismatch warnings on every qualified artifact. Needs a decision on that evidence check first.
