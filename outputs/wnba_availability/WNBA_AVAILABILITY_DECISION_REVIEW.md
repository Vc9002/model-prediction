# WNBA Player-Availability Challenger: July 17–20, 2026

## Decision

**Probability-quality positive, winner-accuracy neutral, not promotable.** The challenger preserved winner accuracy and improved Brier in this tiny window.

## What was actually tested

- Incumbent: the frozen ledger probability produced by `wnba-elo-trend-lr-v3` at decision time; current recomputation is fallback only when no ledger row exists.
- Addition: official WNBA status × pregame projected minutes × a heavily shrunk 10-game box plus/minus proxy above replacement.
- Status sources: official WNBA PDFs plus timestamp-filtered ESPN event injuries. ESPN fills official omissions; contradictory explicit statuses fail closed in production.
- Conflict sensitivity: the diagnostic table also shows a clearly labeled most-conservative resolution (the lower active probability). It is not production-authorized.
- Probability bridge: availability points are added in probit-margin space using the pre-window empirical WNBA margin standard deviation.
- Window: ten settled games on July 17–19 plus four unsettled July 20 scenarios. July 20 is not included in winner accuracy.
- Provenance warning: the official PDFs carry historical publication timestamps but were downloaded retrospectively. This is diagnostic, not a locked point-in-time promotion test.

## Data validation

- Official PDFs parsed: **10**.
- Official player-status rows parsed, including repeated report updates: **224**.
- Settled games with a complete submitted report and mapped priors: **9 / 10**.
- Settled games evaluable only after conservative conflict resolution: **10 / 10**.
- Empirical pre-window home-margin sigma: **14.618 points**.
- Parser/status counts: `{"Available": 25, "Doubtful": 13, "Out": 157, "Probable": 22, "Questionable": 7}`.

### Source reports

| Report time (UTC) | Rows | Submitted teams | Not submitted | SHA-256 |
|---|---:|---:|---:|---|
| 2026-07-17T23:00:00+00:00 | 31 | 11 | 2 | `478a848cdaeb977cc2333e1714a81fcb8b8506208bd5112b6de5814e9fd26bb7` |
| 2026-07-18T01:30:00+00:00 | 34 | 13 | 0 | `53001b0f844b3376201bcb47ba0a513ee2703b4bd576e198118de9e9838d1340` |
| 2026-07-18T23:00:00+00:00 | 18 | 9 | 2 | `ed86550042bffc884e7f02f1dfb48c349f0641cb2409674d1ccd110d3894b0cb` |
| 2026-07-18T23:30:00+00:00 | 18 | 9 | 2 | `3690b7839c328625ec8462b6c2cd1451178a81994d5f6ecf86ed1081ad92de22` |
| 2026-07-19T00:00:00+00:00 | 26 | 12 | 0 | `5720c38d5ab252705107d399c5fed60629199a158c30f03212b0ab308d94e28c` |
| 2026-07-19T16:30:00+00:00 | 19 | 7 | 6 | `47f61a1108de2858827250c4c99823dbacad9e7bfb922a29e8e03ad100ca27ae` |
| 2026-07-19T19:30:00+00:00 | 22 | 7 | 6 | `9ff267270a184ddb22084c13a72b56e30ceb5cf5d5a7adf291d33e21ea14152c` |
| 2026-07-19T22:30:00+00:00 | 30 | 9 | 4 | `09679852c2bec17edc8ebb762c263f20514657bd7b9e7dc59bdcc99202d8b916` |
| 2026-07-20T11:30:00+00:00 | 13 | 6 | 0 | `2422ccc3c88520ff3992c530dac5ebe0797b047c164431e07f2a7d4d557f52b9` |
| 2026-07-20T12:15:00+00:00 | 13 | 6 | 0 | `673c8d0f5042874c61fc3b9e323dd0c1f5cbb6b35a7fac536fb4434c5570fd89` |

## Game-by-game model impact

`Gap` is positive when availability favors the home team. `Adjusted` uses the 1.0× availability scale.

| Date | Matchup | Score | Incumbent home P | Gap (pts) | Adjusted home P | Δ pp | Incumbent pick | Adjusted pick | Correct? | Baseline source | Availability status | Source conflicts | Report |
|---|---|---|---:|---:|---:|---:|---|---|---|---|---|---|---|
| 2026-07-17 | Seattle Storm @ Indiana Fever | 107-110 | 62.070% | -2.058 | 56.613% | -5.46% | home | home | yes | flat_picks.xlsx:607e61329ed3424c | complete | — | 2026-07-17T23:00:00+00:00 |
| 2026-07-17 | Atlanta Dream @ Toronto Tempo | 111-92 | 46.549% | -1.252 | 43.162% | -3.39% | away | away | yes | flat_picks.xlsx:deb68aa7d25042de | complete | — | 2026-07-17T23:00:00+00:00 |
| 2026-07-17 | Los Angeles Sparks @ Chicago Sky | 82-96 | 50.536% | +0.852 | 52.857% | +2.32% | home | home | yes | flat_picks.xlsx:9d42b77cda3d44a7 | complete | — | 2026-07-17T23:00:00+00:00 |
| 2026-07-17 | Connecticut Sun @ Phoenix Mercury | 96-83 | 63.794% | -0.932 | 61.379% | -2.41% | home | home | no | flat_picks.xlsx:33934bb67697420b | complete | — | 2026-07-18T01:30:00+00:00 |
| 2026-07-18 | New York Liberty @ Indiana Fever | 88-108 | 59.331% | +2.164 | 64.956% | +5.63% | home | home | yes | flat_picks.xlsx:ef7830f025d54963 | complete | — | 2026-07-18T23:30:00+00:00 |
| 2026-07-18 | Portland Fire @ Minnesota Lynx | 93-101 | 74.170% | +0.847 | 76.007% | +1.84% | home | home | yes | flat_picks.xlsx:5c32fca0897f4465 | complete | — | 2026-07-18T23:30:00+00:00 |
| 2026-07-18 | Washington Mystics @ Golden State Valkyries | 69-74 | 74.389% | +0.029 | 74.452% | +0.06% | home | home | yes | flat_picks.xlsx:413a2bc86aab48de | complete | — | 2026-07-19T00:00:00+00:00 |
| 2026-07-19 | Los Angeles Sparks @ Dallas Wings | 82-90 | 75.097% | +2.110 | 79.443% | +4.35% | home | home | yes | flat_picks.xlsx:660d896695654e99 | diagnostic_conflict_resolution | Smith, Alanna: official Doubtful / ESPN Out | 2026-07-19T16:30:00+00:00 |
| 2026-07-19 | Chicago Sky @ Atlanta Dream | 91-93 | 67.571% | -0.106 | 67.310% | -0.26% | home | home | yes | flat_picks.xlsx:9f52810e7f374e1c | complete | — | 2026-07-19T19:30:00+00:00 |
| 2026-07-19 | Connecticut Sun @ Phoenix Mercury | 63-72 | 56.577% | -0.423 | 55.437% | -1.14% | home | home | yes | flat_picks.xlsx:44eda71121684cc1 | complete | — | 2026-07-19T22:30:00+00:00 |
| 2026-07-20 | Las Vegas Aces @ Toronto Tempo | unsettled | 34.271% | +0.522 | 35.592% | +1.32% | away | away | — | flat_picks.xlsx:e35a217d64aa4265 | complete | — | 2026-07-20T12:15:00+00:00 |
| 2026-07-20 | New York Liberty @ Dallas Wings | unsettled | 67.878% | -0.718 | 66.100% | -1.78% | home | home | — | flat_picks.xlsx:5ddbae8d805944cb | diagnostic_conflict_resolution | Smith, Alanna: official Doubtful / ESPN Out | 2026-07-20T12:15:00+00:00 |
| 2026-07-20 | Washington Mystics @ Golden State Valkyries | unsettled | 75.044% | +0.000 | 75.044% | +0.00% | home | home | — | flat_picks.xlsx:0da50dc88c9649b8 | fail_closed | — | 2026-07-19T22:30:00+00:00 |
| 2026-07-20 | Minnesota Lynx @ Seattle Storm | unsettled | 26.096% | -0.488 | 25.024% | -1.07% | away | away | — | flat_picks.xlsx:2d184773492d4263 | complete | — | 2026-07-20T12:15:00+00:00 |

## Confidence-gate and feature-strength sensitivity

Accuracy is conditional on calls. Strict rows exclude source conflicts; diagnostic rows include the labeled conservative resolution.

| Cohort | Availability scale | Confidence gate | Settled | Calls | Accuracy | Brier |
|---|---:|---:|---:|---:|---:|---:|
| strict | 0.0× | 50% | 9 | 9 | 88.9% | 0.1782 |
| strict | 0.0× | 55% | 9 | 7 | 85.7% | 0.1782 |
| strict | 0.0× | 60% | 9 | 5 | 80.0% | 0.1782 |
| strict | 0.0× | 65% | 9 | 3 | 100.0% | 0.1782 |
| strict | 0.0× | 70% | 9 | 2 | 100.0% | 0.1782 |
| strict | 0.0× | 75% | 9 | 0 | — | 0.1782 |
| strict | 0.5× | 50% | 9 | 9 | 88.9% | 0.1735 |
| strict | 0.5× | 55% | 9 | 8 | 87.5% | 0.1735 |
| strict | 0.5× | 60% | 9 | 5 | 80.0% | 0.1735 |
| strict | 0.5× | 65% | 9 | 3 | 100.0% | 0.1735 |
| strict | 0.5× | 70% | 9 | 2 | 100.0% | 0.1735 |
| strict | 0.5× | 75% | 9 | 1 | 100.0% | 0.1735 |
| strict | 1.0× | 50% | 9 | 9 | 88.9% | 0.1694 |
| strict | 1.0× | 55% | 9 | 8 | 87.5% | 0.1694 |
| strict | 1.0× | 60% | 9 | 5 | 80.0% | 0.1694 |
| strict | 1.0× | 65% | 9 | 3 | 100.0% | 0.1694 |
| strict | 1.0× | 70% | 9 | 2 | 100.0% | 0.1694 |
| strict | 1.0× | 75% | 9 | 1 | 100.0% | 0.1694 |
| strict | 1.5× | 50% | 9 | 9 | 88.9% | 0.1659 |
| strict | 1.5× | 55% | 9 | 6 | 83.3% | 0.1659 |
| strict | 1.5× | 60% | 9 | 5 | 80.0% | 0.1659 |
| strict | 1.5× | 65% | 9 | 4 | 100.0% | 0.1659 |
| strict | 1.5× | 70% | 9 | 2 | 100.0% | 0.1659 |
| strict | 1.5× | 75% | 9 | 1 | 100.0% | 0.1659 |
| strict | 2.0× | 50% | 9 | 9 | 88.9% | 0.1630 |
| strict | 2.0× | 55% | 9 | 7 | 85.7% | 0.1630 |
| strict | 2.0× | 60% | 9 | 5 | 100.0% | 0.1630 |
| strict | 2.0× | 65% | 9 | 4 | 100.0% | 0.1630 |
| strict | 2.0× | 70% | 9 | 3 | 100.0% | 0.1630 |
| strict | 2.0× | 75% | 9 | 1 | 100.0% | 0.1630 |
| diagnostic_including_conflicts | 0.0× | 50% | 10 | 10 | 90.0% | 0.1666 |
| diagnostic_including_conflicts | 0.0× | 55% | 10 | 8 | 87.5% | 0.1666 |
| diagnostic_including_conflicts | 0.0× | 60% | 10 | 6 | 83.3% | 0.1666 |
| diagnostic_including_conflicts | 0.0× | 65% | 10 | 4 | 100.0% | 0.1666 |
| diagnostic_including_conflicts | 0.0× | 70% | 10 | 3 | 100.0% | 0.1666 |
| diagnostic_including_conflicts | 0.0× | 75% | 10 | 1 | 100.0% | 0.1666 |
| diagnostic_including_conflicts | 0.5× | 50% | 10 | 10 | 90.0% | 0.1613 |
| diagnostic_including_conflicts | 0.5× | 55% | 10 | 9 | 88.9% | 0.1613 |
| diagnostic_including_conflicts | 0.5× | 60% | 10 | 6 | 83.3% | 0.1613 |
| diagnostic_including_conflicts | 0.5× | 65% | 10 | 4 | 100.0% | 0.1613 |
| diagnostic_including_conflicts | 0.5× | 70% | 10 | 3 | 100.0% | 0.1613 |
| diagnostic_including_conflicts | 0.5× | 75% | 10 | 2 | 100.0% | 0.1613 |
| diagnostic_including_conflicts | 1.0× | 50% | 10 | 10 | 90.0% | 0.1567 |
| diagnostic_including_conflicts | 1.0× | 55% | 10 | 9 | 88.9% | 0.1567 |
| diagnostic_including_conflicts | 1.0× | 60% | 10 | 6 | 83.3% | 0.1567 |
| diagnostic_including_conflicts | 1.0× | 65% | 10 | 4 | 100.0% | 0.1567 |
| diagnostic_including_conflicts | 1.0× | 70% | 10 | 3 | 100.0% | 0.1567 |
| diagnostic_including_conflicts | 1.0× | 75% | 10 | 2 | 100.0% | 0.1567 |
| diagnostic_including_conflicts | 1.5× | 50% | 10 | 10 | 90.0% | 0.1528 |
| diagnostic_including_conflicts | 1.5× | 55% | 10 | 7 | 85.7% | 0.1528 |
| diagnostic_including_conflicts | 1.5× | 60% | 10 | 6 | 83.3% | 0.1528 |
| diagnostic_including_conflicts | 1.5× | 65% | 10 | 5 | 100.0% | 0.1528 |
| diagnostic_including_conflicts | 1.5× | 70% | 10 | 3 | 100.0% | 0.1528 |
| diagnostic_including_conflicts | 1.5× | 75% | 10 | 2 | 100.0% | 0.1528 |
| diagnostic_including_conflicts | 2.0× | 50% | 10 | 10 | 90.0% | 0.1495 |
| diagnostic_including_conflicts | 2.0× | 55% | 10 | 8 | 87.5% | 0.1495 |
| diagnostic_including_conflicts | 2.0× | 60% | 10 | 6 | 100.0% | 0.1495 |
| diagnostic_including_conflicts | 2.0× | 65% | 10 | 5 | 100.0% | 0.1495 |
| diagnostic_including_conflicts | 2.0× | 70% | 10 | 4 | 100.0% | 0.1495 |
| diagnostic_including_conflicts | 2.0× | 75% | 10 | 2 | 100.0% | 0.1495 |

## Dallas / Paige Bueckers audit

**The status bug is fixed; the Dallas edge is not.** ESPN identifies Paige Bueckers as Out even though the official WNBA PDF omits her. Isolated, her absence moves Dallas from 67.878% to 59.313%. After all listed absences are combined, Dallas remains 66.100% because New York is also missing material players. That is still a Dallas pick, and the Alanna Smith status conflict makes the production result a no-call.

| Date | Merged Bueckers status | Dallas baseline | Paige-only Dallas P | Net availability gap | Net adjusted Dallas P | Production disposition | Outcome |
|---|---|---:|---:|---:|---:|---|---|
| 2026-07-19 | Not listed (treated Available) | 75.097% | 75.097% | +2.110 | 79.443% | NO CALL: explicit source conflict | Dallas win |
| 2026-07-20 | Out | 67.878% | 59.313% | -0.718 | 66.100% | NO CALL: explicit source conflict | unsettled |

## Largest player-level adjustments

These are challenger inputs, not causal player values. A negative expected-loss number means the noisy proxy rated the named player below the team replacement prior.

| Date | Matchup | Team | Player | Status | Proj min | Impact above repl /100 | Expected points lost |
|---|---|---|---|---|---:|---:|---:|
| 2026-07-20 | New York Liberty @ Dallas Wings | Dallas Wings | Paige Bueckers | Out | 32.3 | +5.17 | +3.343 |
| 2026-07-17 | Seattle Storm @ Indiana Fever | Indiana Fever | Aliyah Boston | Out | 25.7 | +5.45 | +2.796 |
| 2026-07-19 | Los Angeles Sparks @ Dallas Wings | Los Angeles Sparks | Kelsey Plum | Out | 25.8 | +4.21 | +2.170 |
| 2026-07-20 | New York Liberty @ Dallas Wings | New York Liberty | Leonie Fiebich | Out | 24.3 | +4.21 | +2.047 |
| 2026-07-18 | New York Liberty @ Indiana Fever | New York Liberty | Leonie Fiebich | Out | 25.9 | +2.54 | +1.314 |
| 2026-07-17 | Atlanta Dream @ Toronto Tempo | Toronto Tempo | Brittney Sykes | Out | 14.9 | +3.62 | +1.075 |
| 2026-07-17 | Connecticut Sun @ Phoenix Mercury | Phoenix Mercury | Natasha Mack | Out | 16.3 | +2.80 | +0.913 |
| 2026-07-17 | Los Angeles Sparks @ Chicago Sky | Los Angeles Sparks | Kelsey Plum | Out | 24.4 | +1.79 | +0.874 |
| 2026-07-18 | New York Liberty @ Indiana Fever | New York Liberty | Satou Sabally | Out | 12.4 | +3.43 | +0.850 |
| 2026-07-20 | New York Liberty @ Dallas Wings | New York Liberty | Satou Sabally | Out | 10.5 | +4.00 | +0.842 |
| 2026-07-17 | Seattle Storm @ Indiana Fever | Seattle Storm | Ezi Magbegor | Out | 11.1 | +3.33 | +0.738 |
| 2026-07-19 | Los Angeles Sparks @ Dallas Wings | Dallas Wings | Alanna Smith | Out | 9.6 | +3.14 | +0.601 |
| 2026-07-17 | Connecticut Sun @ Phoenix Mercury | Phoenix Mercury | Jovana Nogic | Out | 15.6 | +1.79 | +0.558 |
| 2026-07-19 | Connecticut Sun @ Phoenix Mercury | Phoenix Mercury | Quionche Carter | Out | 6.1 | +4.27 | +0.522 |
| 2026-07-20 | Las Vegas Aces @ Toronto Tempo | Las Vegas Aces | Kierstan Bell | Out | 10.3 | +2.54 | +0.522 |
| 2026-07-18 | Portland Fire @ Minnesota Lynx | Portland Fire | Megan Gustafson | Out | 25.0 | +1.00 | +0.502 |
| 2026-07-17 | Connecticut Sun @ Phoenix Mercury | Connecticut Sun | Aneesah Morrow | Out | 9.6 | +2.57 | +0.491 |
| 2026-07-19 | Connecticut Sun @ Phoenix Mercury | Phoenix Mercury | Shay Ciezki | Out | 4.9 | +4.48 | +0.437 |
| 2026-07-20 | Minnesota Lynx @ Seattle Storm | Seattle Storm | Ezi Magbegor | Out | 10.9 | +1.76 | +0.385 |
| 2026-07-19 | Los Angeles Sparks @ Dallas Wings | Los Angeles Sparks | Kiana Williams | Out | 8.5 | +2.08 | +0.353 |
| 2026-07-20 | New York Liberty @ Dallas Wings | Dallas Wings | Alanna Smith | Out | 9.5 | +1.61 | +0.308 |
| 2026-07-19 | Connecticut Sun @ Phoenix Mercury | Phoenix Mercury | Sami Whitcomb | Out | 11.5 | -1.24 | -0.285 |
| 2026-07-18 | Portland Fire @ Minnesota Lynx | Portland Fire | Holly Winterburn | Out | 5.4 | +2.40 | +0.258 |
| 2026-07-19 | Connecticut Sun @ Phoenix Mercury | Connecticut Sun | Aneesah Morrow | Out | 8.6 | +1.49 | +0.257 |
| 2026-07-17 | Los Angeles Sparks @ Chicago Sky | Chicago Sky | Skylar Diggins | Out | 20.8 | +0.51 | +0.212 |
| 2026-07-17 | Los Angeles Sparks @ Chicago Sky | Los Angeles Sparks | Alissa Pili | Out | 3.1 | +3.01 | +0.189 |
| 2026-07-19 | Los Angeles Sparks @ Dallas Wings | Los Angeles Sparks | Alissa Pili | Out | 3.1 | +3.03 | +0.188 |
| 2026-07-18 | Portland Fire @ Minnesota Lynx | Minnesota Lynx | Liatu King | Out | 4.3 | -1.85 | -0.158 |
| 2026-07-19 | Chicago Sky @ Atlanta Dream | Chicago Sky | Maddy Westbeld | Out | 7.0 | -1.13 | -0.158 |
| 2026-07-19 | Chicago Sky @ Atlanta Dream | Atlanta Dream | Te-Hina Paopao | Out | 5.6 | -1.38 | -0.153 |
| 2026-07-17 | Atlanta Dream @ Toronto Tempo | Toronto Tempo | Ornella Bankole | Out | 3.1 | +2.42 | +0.151 |
| 2026-07-17 | Atlanta Dream @ Toronto Tempo | Atlanta Dream | Te-Hina Paopao | Out | 6.0 | -1.15 | -0.136 |
| 2026-07-17 | Atlanta Dream @ Toronto Tempo | Atlanta Dream | Indya Nivar | Out | 2.3 | +2.40 | +0.111 |
| 2026-07-20 | Minnesota Lynx @ Seattle Storm | Seattle Storm | Taina Mair | Out | 7.2 | +0.72 | +0.103 |
| 2026-07-19 | Chicago Sky @ Atlanta Dream | Atlanta Dream | Indya Nivar | Out | 2.2 | +2.35 | +0.102 |
| 2026-07-18 | Portland Fire @ Minnesota Lynx | Portland Fire | Karlie Samuelson | Out | 10.8 | -0.33 | -0.070 |
| 2026-07-17 | Connecticut Sun @ Phoenix Mercury | Connecticut Sun | Hailey Van Lith | Out | 7.2 | +0.38 | +0.054 |
| 2026-07-20 | New York Liberty @ Dallas Wings | New York Liberty | Marine Johannes | Questionable | 14.9 | +0.30 | +0.044 |
| 2026-07-19 | Connecticut Sun @ Phoenix Mercury | Connecticut Sun | Ashlon Jackson | Out | 4.1 | -0.38 | -0.031 |
| 2026-07-18 | Washington Mystics @ Golden State Valkyries | Golden State Valkyries | Juste Jocyte | Out | 2.6 | -0.56 | -0.029 |

## What this test cannot establish

1. Ten settled games cannot estimate a reliable new coefficient or prove lift.
2. The report PDFs were recovered after the games, so their embedded publication times are useful diagnostics but not equivalent to prospectively observed snapshots.
3. The impact prior is heavily shrunk raw box plus/minus, not WNBA RAPM or lineup-adjusted causal impact.
4. Current rosters were queried during reconstruction; transactions effective between the game and retrieval can create entity risk.
5. The incumbent already went 9-1 in this window, leaving almost no honest room for accuracy improvement. Brier and probability movement are more informative here.

## Keep/remove recommendation

Keep the official report parser, immutable snapshots, player mapping, projected-minute contract, and fail-closed model hook. Do **not** replace the active WNBA artifact or assign a production coefficient from these ten games. Run the collector prospectively, replace shrunk raw plus/minus with a WNBA-specific regularized lineup-impact prior, then re-run a preregistered fresh cohort.
