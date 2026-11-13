---
quick_id: 261113-j3m
status: complete
date: 2026-11-13
commits:
  - ec3210a2
  - docs commit (HEAD of quick task)
---

# Summary — promote nba-elo-trend-lr-v5 to NBA moneyline champion

Promotion id `promo-20261113T184744Z-6b0b74` (active) in the runtime promotions table; copy at
`outputs/nba/nba_lr_v5_promotion.json`. Evidence: `docs/NBA_LR_V5_REFIT_2026-11-12.md`.

- Preflight (model-governance): artifact qualification qualified on a locked holdout; no market-relative
  replay exists for v5 or v4, recorded verbatim as `market_evidence_unavailable_reason`. Wiring traced:
  champions map + model.yaml serving pointer + serving_status flips all moved together.
- Champion `nba-elo-trend-lr-v5` (production, historical_only, rollback v4); v4 `rollback`; challenger cleared.
- Verified: registry loads with 0 problem entries; champion/rollback resolve; the learned slate's
  production artifact and the champion agree (`_is_registered_serving_model` True); registry, v9,
  champion-challenger and NBA blind-spot/cli tests green; ruff clean.
- Left open (operator): buyer whitelist for the serving NBA moneyline model; whether historical_only
  should become predictively_qualified under the direct-wiring policy. model.yaml staged by NBA hunk
  only (two foreign uncommitted hunks left in place); HANDOFF updated in the working tree only.
