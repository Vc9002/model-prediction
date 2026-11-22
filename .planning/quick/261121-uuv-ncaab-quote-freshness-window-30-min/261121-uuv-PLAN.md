---
quick_id: 261121-uuv
mode: quick
---

# NCAAB real Polymarket quotes: accept quotes without transactTime + 30-min freshness

Operator-approved (Vincent, 2026-11-21): "do option 1", then "Relax the check" once the real
blocker was found; audit rule left unchanged ("Leave the audit rule").

## Cause (primary source: saved candidate packets, research/college_contract_candidates/ncaab/2026-11-21)

1. `college_contract_evidence._valid_quote` parsed `transact_time_utc` unconditionally. NCAAB quotes
   come ~99% from the event-detail pass (`live_gateway_event_detail_quote`), which never carries a
   transactTime (13,006 of 13,135 rows on 11-21), so they were rejected as `invalid_time` regardless
   of age: e.g. the 05:54Z run had invalid_time 2155 / matched 5 / not_archived 294.
2. The daily run captures quotes ~15 min before the NCAAB step, so even a valid quote fails the
   300 s default `max_quote_age_seconds`.

NCAAF hit cause 1 on 11-15 and was routed to the /book pass instead (`329c9efe`).

## Task 1: validator

- files: `src/model_prediction/college_contract_evidence.py`, `tests/test_college_contract_evidence.py`
- action: a missing/empty transact time is no constraint; a present one must still be <= decision.
  `observed_at_utc <= decision` and `< event start` are unchanged (point-in-time gate).
- verify: new tests pass; with the fix reverted the no-transact test fails.

## Task 2: freshness window

- files: `config/model.yaml`
- action: `models.NCAAB.max_quote_age_seconds: 1800` (matches NCAAF `_CAPTURE_FRESH_WINDOW`).

## Verify

Replay every saved 11-21 NCAAB packet through the patched `build_candidate_evidence` at its own
decision time, then the serving join (<= 1800 s, side present). Targeted tests:
`tests/test_college_contract_evidence.py tests/test_college_basketball_forward.py
tests/test_ncaab_tier1_guardrails.py tests/test_ncaab_main_totals.py`.

## Consequences (accepted)

- Main admission (`qualified = has_executable_quote and edge >= min_edge`) becomes reachable for NCAAB
  once `min_team_games: 8` is met.
- Edges are measured against the BBO ask; `_reselect_total_main_lines` (same window) now picks
  totals main lines from BBO mids more often.
- NCAAF's evidence builder shares the validator; NCAAF /bbo-sourced rows without transactTime now
  also pass.
- Audit `VERIFIED_QUOTE_PROVENANCE` stays `decision_time_executable_quote`: NCAAB
  `archived_two_sided_bbo` rows remain unverified for bucket evidence (operator decision).
- Auto-buyer `_is_live_ncaab_spread_v6_bucket` already accepts both provenances.
