# Production-model deep audit — 2026-08-15 (burn-in day 0)

Tooling: `scripts/production_model_audit.py` (research branch, read-only
against live state). Per model: artifact self-hash, config wiring
(filename ↔ model_version), serving-feature coverage, champion
resolution, ledger evidence (SQLite canonical), input-data freshness,
qualification honesty. Machine-readable result:
`outputs/research/production_model_audit.json`.

## Mechanical layer: 13/13 clean

All 13 configured models pass the mechanical checks (hash self-verify,
wiring, serving coverage, champion map). Champion = primary for every
sport; no challenger has displaced an incumbent.

## Real finding — FIXED same day

### WNBA: zero ledger rows since the sqlite-authority cutover (thread-affinity)

`RuntimeLedgerStore` held ONE sqlite3 connection; the forecast fan-out
runs sports in ThreadPoolExecutor workers, and sqlite3 forbids using a
connection from another thread. Every WNBA ledger write failed with
"SQLite objects created in a thread can only be used in that same
thread" — 212 occurrences in the 08-14 daily log, 0 on 08-13 (the
cutover day), `"logged": 0`, `"status": "error"` every cycle since.

Fixed on main (`6028d00`): one connection per thread (WAL +
busy_timeout serialize writes), `close()` closes every thread's
connection, revert-verified regression test
(`test_store_usable_from_multiple_threads`). WNBA rows for 08-14/15
remain a documented data gap.

### MLB zero-open-rows — investigated, NOT a defect

MLB logged 0 rows on 08-15 while producing 15 candidates. Chased to
the confidence gate: all 15 were below v8's learned threshold
(`NO_CALL_BELOW_LEARNED_CONFIDENCE` → research-only, main-skip is the
documented "show everything, human decides" contract working as
written), and the flat pass found 7 games with no matched Polymarket
quote overnight. The 151 `removed` rows on 08-14 are the designed
clear-and-replace of same-day re-forecasts. No bug.

## Gaps flagged (not bugs; decisions/documentation)

1. **MLB v8 carries `qualified: false`** — promoted by operator override
   (documented in the artifact's promotion_rationale). v9 must earn
   formal qualification; no promotion decisions during burn-in.
2. **Esports (×5) and KBO/NPB artifacts carry no qualification or
   training block** — their lineage schema records ratings state but no
   holdout metrics or promotion rationale. Champion evidence for these
   seven models lives outside the artifact (in docs/ledgers), unlike
   the LR artifacts. Recommend: v7/v2-next builds embed the same
   qualification/training structure v8 carries.
3. **NBA and NFL have zero ledger rows ever** — research-only models,
   and NBA is deep offseason (last event 62 days ago). Expected, but
   note the NFL model will start producing rows only when the regular
   season begins; until then "NFL v4 keep" has no live evidence stream.
4. **Soccer and tennis are code-backed** (no artifact file) — no
   artifact hash to self-verify; their serving contracts live in code
   constants. Expected by design; a hash-pinned code-backed contract is
   a possible hardening.
5. **Soccer Odds API** remains a documented known-DEGRADED external
   dependency (credential) — does not block burn-in.

## Status

| Model | Mechanical | Live evidence | Note |
|---|---|---|---|
| MLB v8 | clean | 150 rows (122 settled) | qualified:false by override — documented |
| WNBA v4 | clean | 89 rows (81 settled) | **logging fixed 08-15**; 08-14/15 gap |
| NBA v4 | clean | 0 rows | offseason |
| NFL v4 | clean | 0 rows | preseason |
| Soccer pooled | clean | 161 rows (147 settled) | code-backed |
| Tennis v1 | clean | 379 rows (366 settled) | code-backed |
| CS2/Dota2/LoL/Valorant/R6 v6 | clean | 465/99/291/220/7 rows | no qualification block — gap #2 |
| KBO/NPB v2 | clean | 34/55 rows | no qualification block — gap #2 |

No promotion decisions made. Champions unchanged.
