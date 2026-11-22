---
quick_id: 261121-uuv
status: complete
---

# Summary: NCAAB real Polymarket quotes

## Changed

- `college_contract_evidence._valid_quote`: missing transact time no longer fails as `invalid_time`;
  a present transact time after the decision is still rejected.
- `config/model.yaml`: `models.NCAAB.max_quote_age_seconds: 1800`.
- Two regression tests in `tests/test_college_contract_evidence.py`; the no-transact test fails with
  the validator change reverted and passes with it restored.

## Evidence (replay of the 8 saved 2026-11-21 NCAAB packets)

| run (UTC) | matched before | matched after | serving decisions joined (<= 30 min) |
|---|---|---|---|
| 05:54 | 5 / 2454 | 2160 | 127 / 129 |
| 11:16 | 3 / 2510 | 2371 | 149 / 149 |
| 14:15 | 4 / 2576 | 2486 | 150 / 151 |
| 17:16 | 10 / 2564 | 2555 | 138 / 140 |
| 19:17 | 27 / 2307 | 2300 | 98 / 98 |
| 21:21 | 14 / 1632 | 1630 | 68 / 68 |
| 23:51 | 11 / 940 | 939 | 42 / 42 |
| 01:51 | 11 / 512 | 511 | 6 / 6 |

672 of 683 decisions would carry `archived_two_sided_bbo`; none would at the old 300 s window
(matched quotes were 11-17 min old). Remaining misses are post-start or never-archived markets.

Tests: ruff clean; 52 passed across contract-evidence, college-basketball-forward, NCAAB tier-1
guardrails and NCAAB main-totals files.

## Not changed

Audit verified-provenance rule (operator: leave). Capture path unchanged (event-detail pass stays).
