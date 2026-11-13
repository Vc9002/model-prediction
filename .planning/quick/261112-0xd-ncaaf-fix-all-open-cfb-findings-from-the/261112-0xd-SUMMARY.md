---
status: complete
quick_id: 261112-0xd
---

# NCAAF: close every open CFB finding of the 2026-11-11 model review

**Note:** planned and executed inline by the orchestrating session (no planner/executor
subagents). Three other sessions were editing this working tree concurrently (NBA review
fixes, ledger/audit work); shared files were staged as HEAD + this task's hunks only, and
the code commit was verified in an isolated worktree at that commit.

## What was wrong

- **#1 (HIGH)** `build_cfb_slate` served a second `CollegeFootballModel` implementation
  (margin_sd 16.2, no conference scaling, no total-SD widening) under the
  `cfb-structural-v4` label and artifact hash, from the v4 promotion (2026-10-26) until this
  fix. The validated `CFBStructuralV4Model` (14.5) was never on the production path. 243 Main
  rows carry the mislabelled arithmetic (identifiable from snapshot `parameters.margin_sd`).
- **#8a/b** moneyline matcher returned the oldest capture of the day; spread/total selectors
  compared stale and fresh captures; one-sided books were completed as `1 - ask`.
- **#8c** 229 of 238 Main NCAAF rows were ESPN-consensus priced with no label.
- **#9** the artifact's `gates.block_fcs / max_spread_line / max_moneyline_dog_odds` were read
  nowhere; eight artifact-declared feature names were not emitted by the feature dict.
- **#12** Main total gates were hardcoded with no evidence; one clause was dead.
- **#13** neutral-site weather resolved on the home stadium; bowls ran as week 1 (90% prior
  weight); `qb_starter_prob_*` was never populated.
- **LOW** 15k Monte-Carlo draws (±0.8 pp CI vs a 2 pp gate); `code_revision` hashed the
  version string.

## What was done (commit `f6fa91d1`)

- `CollegeFootballModel` is a thin subclass of `CFBStructuralV4Model`; `cfb_model()` builds it
  with `from_artifact(load_cfb_structural_v4_artifact())`, which fails closed on identity,
  hash, undeclared features and unread keys. The v4 engine records replay inputs from one
  advancing RNG stream (six-decimal probabilities; `cfb_distribution.py` untouched so open
  snapshots still replay).
- Newest-capture-per-slug matching, both asks required, no fabricated side.
- `feature_basis.quote_provenance` + rationale note on every contract (NFL precedent).
- `main_gates_from_artifact` enforces every artifact gate on Main; total gates live in
  `config/model.yaml` `models.NCAAF.main_total_gates` (same values, documented unvalidated).
- Venue-based neutral-site weather (resolver + daily capture); postseason → model week 15;
  verified on the live 2025-12-31 ESPN payload.
- `features/cfb_qb_availability.py`: PIT join of the daily ESPN injuries snapshot to the
  scoreboard passing leader. Live feed is sparse (3 entries on 11-12), so the channel is
  correct but will rarely fire.
- 200k simulations; `cfb_serving_code_hash()`.

## Verification

- `tests/test_cfb_serving_parity.py`: 26 tests. Mutation checks: the parity test fails when
  the legacy 16.2 model is injected; the matcher test fails when newest→oldest is flipped;
  the FCS gate test fails when the reject is removed.
- 82 CFB-related tests pass in a clean worktree at `f6fa91d1`; `ruff check src/ tests/` clean.
- Live ESPN slate 2026-11-14: 56 games, 167 contracts, all `cfb-structural-v4`, margin SDs
  13.78–19.58, 200k sims, 3 FCS contracts now blocked, 5.9 s.
- Pre-existing failures also red at committed HEAD (not this task): `tests/test_cli.py`
  tennis/soccer/MLB/WNBA/NFL ledger tests, `test_model_naming::test_catalog_covers_production_registry`,
  `test_ci_contracts::test_ci_imports_of_checked_in_config_resolve`. One further
  `test_cli` learned-sport failure comes from another session's uncommitted hunks.

## Open decisions (operator)

- Supersede or keep the 243 Main `cfb-structural-v4` rows priced on the 16.2 arithmetic.
- Whether ESPN-consensus-priced rows may reach Main for NCAAF (now labelled).
- Validate or delete `main_total_gates`.

## Still owed (research)

Replay v4's PIT predictions against the 363-game no-vig closing set; refresh the artifact's
`calibration` block; a denser college QB availability source.
