# NCAAF and NCAAB research, 2026-11-04

## NCAAF: market evidence and legacy gate

The settled Main ledger has 1,014 NCAAF calls for −58.41 units. The quote audit
verifies content hashes, contract identity, open status, pregame timing, two
asks, and recorded selection price for all 51 calls with a snapshot hash. None
of the 830 legacy v1 calls has executable quote lineage. The current v4 has 19
settled Main calls and none has a snapshot hash. A simultaneous, timestamped
v1–v4 comparison on common contracts is therefore **not identifiable** from
the archive. The 51 verified calls span v2 (5) and v3 (46), on different
dates and selections. Do not rank versions using their pooled P&L.

Within v1, 486 QUALIFIED calls lost 91.35 units, while 344 PAPER_CALL_LOW_EDGE
calls gained 20.50 units. QUALIFIED moneyline: 83 calls, 32.5% wins against
66.5% mean model probability, −48.34 units. QUALIFIED spread: 242 calls (239
non-push), 48.1% wins against 76.5% mean model probability, −36.65 units.
QUALIFIED total: 161 calls, 50.3% wins against 60.8% mean model probability,
−6.36 units. The indicative recorded prices predicted outcomes better by
Brier score in all three markets. This is strong evidence of legacy
miscalibration or selection defects, not evidence that reversing the gate
would work. The winning low-edge moneylines were largely favorites with a
mean indicative ask of 83.9%, so their win rate alone does not prove edge.

Keep sizing unchanged. Collect *same-contract, same-time* pregame BBO and
shadow probabilities from each candidate version, with no orders. Require
quote validity, calibration, Brier/log loss, and later-date net results before
revising eligibility or stake rules.

Reproduction: `python scripts/audit_ncaaf_market_calibration.py`; detailed
output: `outputs/research/ncaaf_market_calibration_2026-11-04.json`.

## NCAAB: data and possession challenger

First-seen hashed box-score capture covers all 210 completed November 2–3
games (182 + 28). The live forecast now stores a hashed feature packet and
serves v3 as recorded in `config/model.yaml` (prior commit `fd474e04`). The
November 4 BBO capture adds 45 timestamp-valid pregame, two-sided main-line
contracts across 26 events: 25 moneylines, 10 spreads, and 10 totals. Daily
slate capture now includes a bounded NCAAB main-line selection. Existing
November 2–3 quote coverage is sparse, and the live summary price remains
indicative; those settled calls cannot support an executable-edge claim.

Research challenger v4 uses estimated possessions (FGA − ORB + turnovers +
0.475 FTA), points per possession, 20-iteration opponent-adjusted offense and
defense, 12-game shrinkage, and pace. Features use only **previous dates**.
The source is the [sportsdataverse 2025–26 team box-score release](https://github.com/sportsdataverse/sportsdataverse-data/releases/tag/espn_mens_college_basketball_team_boxscores),
asset `team_box_2026.parquet`, SHA-256
`db58650c1a9b2a1b6c8735507b8e922580ea514bb9f2ccbc1d91ae6588a27f42`.
It matches 6,280 of 6,281 v3 evaluation games. Individual box-score
first-seen times for that historical season are unknown, so this is
retrospective development evidence.

| Period | Games | Margin MAE v3 → v4 | Total MAE v3 → v4 | Brier v3 → v4 | Log loss v3 → v4 |
|---|---:|---:|---:|---:|---:|
| Train, before Feb 2 | 4,307 | 13.84 → 11.86 | 14.61 → 13.70 | .193 → .158 | .574 → .481 |
| Validation, Feb 2–Mar 4 | 1,448 | 8.86 → 10.94 | 13.94 → 12.94 | .204 → .221 | .597 → .629 |
| Later, Mar 5 onward | 525 | 9.03 → 12.04 | 14.04 → 12.88 | .208 → .245 | .605 → .685 |

The challenger fails the required later-date margin, calibration, Brier, and
log-loss gates. Its total improvement is insufficient for promotion. V3 stays
in service. No v4 bet selection or P&L is reported as actionable because the
prediction gate failed before the recorded-price comparison, and archival
executable quote coverage is insufficient.

Reproduction: download the pinned asset to `scratch/team_box_2026.parquet`,
verify its SHA-256, then run `python scripts/test_ncaab_possession_v4.py`.
Detailed output: `outputs/research/ncaab_possession_v4_2026-11-04.json`.
