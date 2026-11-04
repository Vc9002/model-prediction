# College contract evidence and NCAAF calibration, 2026-11-04

## Theory, evidence, test, failure, rollback

The previous NCAAB decision path used indicative summary prices, and its
archived executable BBO was separate. NCAAF preserved quote hashes for some
selected calls but did not retain probabilities for rejected sides and all
alternate lines. A path split also made the football slate look for BBO under
the checkout while the actual archive was under the runtime root. The new
research packet records every normalized listed contract, both sides, exact
line probabilities, quote join or explicit missingness, model input hash and
content hash. It does not change NCAAB's indicative-price gate or stake rule.

The falsifiable tests are exact event/market ID, slug, line, predecision and
pregame times, open state, two-sided BBO, and content-addressed packet
integrity. A mismatch fails closed in the research join. The rollback is to
remove the `college_contract_evidence` capture and restore the prior slate
builder; existing ledgers and model artifacts are untouched.

## First prospective capture

| Sport and slate | Listed contracts | Model scored | Valid exact BBO joined | Both model and BBO | Primary missingness |
|---|---:|---:|---:|---:|---|
| NCAAB, Nov 4 | 543 | 543 | 44 (8.1%) | 44 | 499 not archived |
| NCAAF, Nov 5 | 135 | 134 | 7 (5.2%) | 6 | 128 alternate lines not archived; one FCS moneyline has no matched ESPN model game |

The daily BBO capturer retains a bounded main-line shortlist, so low
all-listed-contract quote coverage is expected and is not a source failure.
It must remain visible as a denominator. For NCAAF's two matched ESPN games,
all six selected main-line candidate asks and content hashes equal the
packet's joined quote. The seventh quoted contract is an unmatched FCS game.
NCAAB research packets keep the indicative ask alongside the executable ask;
the ledger still labels its price indicative. No orders were placed.

Packet location: runtime `research/college_contract_candidates/{sport}/{date}/{hash}.json`.
The filename hashes the summary and every contract record hash. Quote hashes
are computed from the exact archived JSON row, and each row retains the
archive path and observed/transact times. The packet includes each serving
candidate's event, market slug, side, indicative or selected ask and quote
hash for reconciliation.

## NCAAF calibration audit

The legacy settled Main ledger still contains 1,014 calls, of which 51 have
verified executable-quote lineage. Those 51 are v2/v3 selected calls (5/46),
almost all in season week 8 (50) and one in week 9. By market, model versus
recorded-ask Brier: moneyline 0.1988 vs 0.1948 (9 calls); spread 0.2427 vs
0.2517 (25); total 0.2677 vs 0.2512 (17). The moneyline and total model
scores are worse than the ask baseline on this *selected, tiny* sample; the
spread score is better. Favorite status and line-size breakdowns are in the
machine-readable audit. Many bins have single-digit counts and cannot define
a calibration map or eligibility change.

The new prospective common-contract packet has **zero settled contracts** as
of this report. No new calibration was fitted, no alternative gate was
tested, and sizing remains unchanged. When later packets settle, evaluate
both sides on the same contract and timestamp, with chronological date
splits and proper probability scores against the market before testing a
frozen gate. An outcome-selected QUALIFIED versus LOW_EDGE reversal is not
an admissible gate experiment.

Reproduce the audit with `python scripts/audit_ncaaf_market_calibration.py`.
The JSON result is `outputs/research/ncaaf_market_calibration_2026-11-04.json`.
