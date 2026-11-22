# quick-261121-qsd: NCAAF Polymarket quote side from the title's first team

## Bug
`_match_cfb_spread` decided which team a spread quote's line belongs to by resolving the quote's `team`
label, and fell back to **home** when the label did not resolve. Polymarket labels Arizona "University of
Arizona" (also "NM State", "U. of Idaho", and bare mascots early in the season). On 2026-11-21 (Arizona at
Kansas State, both "Wildcats") the contract `asc-cfb-arz-kanst-2026-11-21-neg-10pt5` (Arizona -10.5) was read
as Kansas State -10.5, so v5, v6.2 and v7 all priced "Arizona +10.5" at the Kansas State +10.5 ask (52c):
edges of 31% / 42% / 39%. `_match_cfb_moneyline` had the same fallback (rescued that night only because the
mascot "Wildcats" matched the away team; it matches both).

## Evidence (all captured NCAAF Polymarket rows, 2026-09-04..11-21)
- Quote `team` = title first team: spreads 22,256 of 22,256 resolvable labels, moneylines 1,165 of 1,165.
- Spread long description equals the quote line on 94,072 of 94,072 rows.
- 71,799 spread rows carry a label that does not resolve.
- Ledger rows with a recoverable slug: 828 consistent, 13 with a flipped line (11 Arizona 11-21: 3 open
  Flat v5/v6.2/v7 + 8 superseded; 2 NM State 10-21 v2 Flat, one settled win +0.83U). 1,856 older rows carry
  no slug and could not be checked this way.

## Fix
Both matchers take the side from the title position already matched to the ESPN away/home teams; the label
is only a cross-check (`_quote_label_side`), and a label that names the other team fails closed for that
quote. Over history the cross-check contradicts once (West Georgia "Wolves" vs Arkansas State Red Wolves,
an artifact of short names; ambiguous, so not dropped, with ESPN display names).

## Tests
`tests/test_cfb_serving_parity.py`: three new tests (spread + moneyline side when the label does not
resolve, both orientations; label naming the other team fails closed). Mutation-proven: all three fail on
the old matcher, pass on the fix.

## Not done (operator decision)
The 3 open Flat rows for the Arizona game (v5, v6.2, v7) are mispriced and were left in place; removing or
archiving them is a ledger mutation that needs operator approval.
