# Project status and source of truth

**Last verified**: 2026-07-26 against the dirty `deepseek-phase5` checkout at
`697d765`.

This document is the operational status entry point. `DEBUG.md` contains the
full audit evidence and reproduction commands. Historical metrics in old
reports, changelog entries, model cards, and rollback artifacts are not current
operational truth.

## Release verdict

The checkout is **not release-ready**, and the real-money execution surface
should not be used.

The blunt reason: the project currently has a plausible forecasting layer on
top of an execution and evidence chain that is not yet trustworthy enough for
capital. Model hit rates do not offset an order-ticket binding flaw,
non-point-in-time MLB validation, non-atomic ledger/audit writes, failing tests,
and stale release evidence.

## Source-of-truth order

When files disagree, use this order:

1. Runnable tests and the current source path.
2. `config/model.yaml` plus the exact artifact it names.
3. A freshly reproduced report for the same checkout and artifact version.
4. Point-in-time source records and executable-price snapshots.
5. League contracts and architecture documentation.
6. README tables, changelogs, prompt files, and historical model cards.

Fail closed when higher-ranked evidence disagrees. A config override, dashboard
badge, or headline hit rate cannot repair missing provenance or inconsistent
execution semantics.

## Current health

| Check | Verified result | Consequence |
|---|---|---|
| Test suite | **410 passed, 4 failed** | Four dashboard order-preview tests drifted from the current `$5.00` unit cap. |
| Focused critical tests | **84 passed** | Audit, CLI, domain, forward, and XLSX modules now have direct tests. |
| Ruff | **117 findings** | Not lint-clean; 79 are executable-bit/shebang findings. |
| Critical imports | Pass | Core modules and feature/data-source packages import. |
| Python/package | Python 3.14.5; editable install resolves here | Packaging and console entry point work. |
| Artifact hashes | **31 valid, 2 mismatched, 33 total** | NBA and NFL spread baselines fail canonical hash verification. |
| Audit chain | **16,387 events, 0 breaks, 0 hash mismatches** | Cryptographic chain is intact. |
| Ledger/audit reconciliation | **False** | 1,150 historical creation events lack audited removal events; current rows all have creation events. |
| Config artifacts | One missing; one wrong semantic reference | Market-residual artifact is absent; MLB total research points to the spread artifact. |
| Latest learned report | Stale | It names an old worktree and MLB v5, not active MLB v6/current checkout. |
| Dashboard runtime | Healthy process, inconsistent governance state | `/api/status` reports `promotion_allowed=true` while warning MLB is below its qualification gate. |
| Working tree | Heavily dirty | A release cannot be attributed to one stable, reviewable source state. |

The four failing tests are:

- `test_resting_order_preview_and_submit_persist_exchange_id`
- `test_submit_parses_success_after_interactive_prompt`
- `test_buy_at_current_ask_submits_marketable_ioc_limit`
- `test_manual_control_can_buy_at_ask_when_positive_edge_gate_is_disabled`

Their fixed share counts now exceed the current config-derived dollar cap. The
tests must explicitly pin their intended unit value or use cap-compliant sizes.

## P0 blockers

### Real-money execution binding

`PolymarketExecutor.execute()` does not bind the submitted market, side, price,
quantity, and action to the exact qualified ledger row. It also trusts
caller-supplied `estimated_cost_usd` instead of recomputing cost. Submission
precedes audit append. Until those invariants are repaired and directly tested,
do not use the execution surface.

### Ledger and audit are separate commits

Ledger mutations commit before audit events are appended. A crash or audit-lock
failure can leave a created, settled, voided, or removed ledger row without its
matching event, and idempotent retry may not repair the gap.

### MLB v6 provenance

Historical validation now accepts probable-starter inputs only from the
append-only archive when `observed_at_utc` predates both the decision and first
pitch. The real prospective archive began on 2026-07-26. The existing MLB v6
artifact remains unqualified because its old validation did not have that PIT
evidence; new capture does not retroactively validate it.

### WNBA availability diagnostics

Per operator policy, missing/conflicting availability no longer suppresses the
WNBA model opinion. The affected inputs default neutral and the Today row
records the conflict/error rationale and unavailable-feature code.

### Qualification and quote semantics

The learned forward layer labels a confidence-threshold call
`QUALIFIED_SHADOW_CALL` even when the artifact is unqualified. Missing or
timestamp-invalid quotes now retain the MLB/WNBA model opinion in Today as a
zero-unit `NO_CALL_MARKET_UNAVAILABLE`; they cannot become executable calls.

## Active learned artifacts

These are artifact-level predictor diagnostics. Synthetic `-110` units are not
executable profitability.

| Sport | Active artifact | Artifact qualified | Locked calls | Hit rate | Operational reading |
|---|---|---:|---:|---:|---|
| MLB | `mlb-elo-trend-lr-v6` | **No** | 135 | 71.11% | Partial 90-day experiment with no complete qualifying month and invalid historical starter provenance; config override only. |
| NBA | `nba-elo-trend-lr-v4` | Yes | 577 | 73.66% | Predictor gate pass; executable economics remain separate. |
| WNBA | `wnba-elo-trend-lr-v4` | Yes | 163 | 67.48% | Predictor gate pass; availability fail-open path remains a blocker. |
| NFL | `nfl-elo-trend-lr-v4` | Yes | 87 | 71.26% | Predictor gate pass; spread research artifact hash is corrupt. |
| Soccer | `soccer-poisson-dc-v1` | No production artifact | — | — | Draw-aware full-game 2.5 total research; executable-BBO matched and research-only. |

The active esports, KBO, and NPB config entries use qualification overrides,
while the dashboard evidence surface still labels those markets
`research_only`/`qualified_for_betting=false`. KBO/NPB filenames remain `v1`
even though the artifact and config version strings say `v2`. Treat them as
research until separate promotion evidence exists. Preview, daily routing,
gated-subset selection, and half-value tie settlement are now wired.

## Release alignment

`outputs/latest/learned-model-validation.json` is not a current release report:

- its embedded paths point to an old
  `Documents/Poly & Kalshi/.../.claude/worktrees/fix-waves-1-4` checkout;
- it names MLB v5 while config activates MLB v6;
- it predates the current KBO/NPB artifacts and the latest source changes;
- the current checkout has four failing tests and 117 Ruff findings.

Two research artifacts contain invalid canonical hashes:

- `config/models/nba-spread-baseline-v1.json`
- `config/models/nfl-spread-baseline-v1.json`

The config also points `models.MLB.total_research_artifact` to the MLB spread
artifact and points `models.market_residual.artifact` to a nonexistent file.

## Current decision policy

The code no longer enforces the policy described in older documentation.

The CLI can apply a sport-specific minimum edge against an executable ask before
logging. After that, `eligibility.py` enforces bans, lifecycle state,
staleness, and provenance, but it intentionally ignores market disagreement,
exposure, and post-uncertainty edge when deciding `CALL` versus `NO_CALL`.
Sizing uses `edge_scaled_units`, not the exposure-aware `recommend_units`
decision.

Therefore:

- do not claim exposure caps or disagreement review are active eligibility
  gates;
- do not interpret a `QUALIFIED_SHADOW_CALL` label as proof of a qualified
  artifact;
- do not interpret config `status: shadow_qualified` as real-money approval.

## Runtime snapshot

The read-only `summary` command reported:

- 4 open picks and 7.75 open units;
- 4 picks logged on 2026-07-26;
- 7.0931 qualified shadow P&L units all time;
- no mean probability CLV;
- an explicit shadow-research accounting note.

The MLB dry forecast for 2026-07-26 found 15 games and 9
confidence-threshold calls, wrote zero rows, and executed zero orders. Its
aggregate correctly reported zero qualified calls because the active artifact
is unqualified.

## Additional correctness blockers

- Exposure is checked outside the append transaction; concurrent writers can
  approve from the same stale snapshot.
- The Odds API error path can return a URL containing the API key.
- Polymarket aggregate capture can falsely report global timestamp validity and
  silently truncate discovery at 50 events.
- Future timestamps can pass the guaranteed-signal freshness check.
- Soccer head-to-head records draws as away wins.
- MLB weather payload/hour semantics are wrong.
- The economic ROI confidence-interval gate can pass an interval spanning
  zero.

Exact evidence and line references are in `DEBUG.md`.

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

## Repair order

1. Disable or hard-block real-money execution until tickets are bound to the
   exact ledger row and all economics are recomputed server-side.
2. Make ledger mutation and audit append recoverable as one transaction; add
   failure-injection and retry tests.
3. Remove non-PIT probable-starter inputs from validation and keep MLB v6
   unqualified until prospective evidence exists.
4. Enforce artifact qualification, quote `timestamp_valid`, and fail-closed
   WNBA availability semantics.
5. Restore a green suite and correct the two artifact hashes plus config
   references without overwriting rollback artifacts.
6. Repair KBO/NPB preview, routing, half-settlement P&L, and settlement
   visibility.
7. Make exposure-check-plus-append transactional and enforce one writer across
   all ledgers.
8. Fix secret redaction, timestamp age, soccer draw, weather, discovery, and
   economic-CI semantics.
9. Reproduce one versioned report from a stable, green, reviewed checkout.
