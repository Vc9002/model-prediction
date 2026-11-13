# NCAAB v6 settled-ledger replay -- fixed model vs pre-fix control vs as logged

Generated 2026-11-12T07:06:29.448540+00:00. Spread artifact `7704c5800b88`, total artifact `cbff479dbdb5`.

1167 of 1167 settled Flat rows re-priced (0 skipped: ). Neutral flag unavailable for 144 rows (treated as home court).
Quote provenance: {'archived_two_sided_bbo': 10, 'indicative_slate': 112, 'unknown': 1045}. Units are paper, 1u per row at the recorded ask; indicative asks are not executable.

`forward rule` = rows the live forward would have called: the model favours the recorded side (p >= 0.5) AND its edge over the recorded ask clears the hurdle (1u each). `any-edge` ignores the p >= 0.5 rule. `favours recorded side` = share of rows with p >= 0.5 for the recorded side.

## moneyline

- **as_logged**: n 305, Brier 0.1862, log loss 0.5590, ECE 0.202, mean p 0.440 vs ask 0.250, hit 0.239, favours recorded side 0.33; forward rule >=3.5%: 76b 34W -1.27u (ROI -0.017); >=5%: 66b 28W -1.57u (ROI -0.024); >=8%: 56b 26W +4.84u (ROI 0.086); any-edge >=3.5%: 262b 51W -79.57u (ROI -0.304)
- **control_pre_fix_v6**: n 305, Brier 0.1529, log loss 0.4663, ECE 0.106, mean p 0.332 vs ask 0.250, hit 0.239, favours recorded side 0.31; forward rule >=3.5%: 71b 30W -0.90u (ROI -0.013); >=5%: 65b 26W -1.34u (ROI -0.021); >=8%: 58b 24W +1.70u (ROI 0.029); any-edge >=3.5%: 215b 37W -77.13u (ROI -0.359)
- **fixed_v6**: n 305, Brier 0.1616, log loss 0.4978, ECE 0.130, mean p 0.369 vs ask 0.250, hit 0.239, favours recorded side 0.28; forward rule >=3.5%: 46b 19W +1.60u (ROI 0.035); >=5%: 46b 19W +1.60u (ROI 0.035); >=8%: 42b 17W +1.77u (ROI 0.042); any-edge >=3.5%: 235b 35W -85.21u (ROI -0.363)

by selection:
- away: control n 224, Brier 0.1138, log loss 0.3703, ECE 0.091, mean p 0.208 vs ask 0.136, hit 0.125, favours recorded side 0.08; forward rule >=3.5%: 13b 1W -7.12u (ROI -0.548); >=5%: 13b 1W -7.12u (ROI -0.548); >=8%: 13b 1W -7.12u (ROI -0.548); any-edge >=3.5%: 154b 8W -80.35u (ROI -0.522)
- away: fixed   n 224, Brier 0.1305, log loss 0.4273, ECE 0.164, mean p 0.289 vs ask 0.136, hit 0.125, favours recorded side 0.11; forward rule >=3.5%: 19b 3W -8.61u (ROI -0.453); >=5%: 19b 3W -8.61u (ROI -0.453); >=8%: 18b 2W -9.74u (ROI -0.541); any-edge >=3.5%: 198b 16W -95.74u (ROI -0.483)
- home: control n 81, Brier 0.2613, log loss 0.7320, ECE 0.197, mean p 0.676 vs ask 0.564, hit 0.556, favours recorded side 0.95; forward rule >=3.5%: 58b 29W +6.22u (ROI 0.107); >=5%: 52b 25W +5.78u (ROI 0.111); >=8%: 45b 23W +8.82u (ROI 0.196); any-edge >=3.5%: 61b 29W +3.22u (ROI 0.053)
- home: fixed   n 81, Brier 0.2476, log loss 0.6926, ECE 0.137, mean p 0.589 vs ask 0.564, hit 0.556, favours recorded side 0.77; forward rule >=3.5%: 27b 16W +10.21u (ROI 0.378); >=5%: 27b 16W +10.21u (ROI 0.378); >=8%: 24b 15W +11.51u (ROI 0.480); any-edge >=3.5%: 37b 19W +10.53u (ROI 0.285)

by original model (fixed v6 re-price):
- ncaab-spread-v5: as logged n 5, Brier n/a, log loss n/a, ECE n/a, mean p 0.652 vs ask 0.404, hit 0.600, favours recorded side 1.00; forward rule >=3.5%: 5b 3W +0.89u (ROI 0.178); >=5%: 4b 2W +0.17u (ROI 0.042); >=8%: 4b 2W +0.17u (ROI 0.042); any-edge >=3.5%: 5b 3W +0.89u (ROI 0.178)
- ncaab-spread-v5: fixed     n 5, Brier n/a, log loss n/a, ECE n/a, mean p 0.505 vs ask 0.404, hit 0.600, favours recorded side 0.60; forward rule >=3.5%: 1b 1W +1.38u (ROI 1.380); >=5%: 1b 1W +1.38u (ROI 1.380); >=8%: 1b 1W +1.38u (ROI 1.380); any-edge >=3.5%: 3b 1W -0.62u (ROI -0.207)
- ncaab-spread-v6: as logged n 50, Brier 0.2360, log loss 0.6637, ECE 0.127, mean p 0.681 vs ask 0.639, hit 0.600, favours recorded side 1.00; forward rule >=3.5%: 30b 15W +3.89u (ROI 0.130); >=5%: 23b 11W +4.44u (ROI 0.193); >=8%: 19b 11W +8.44u (ROI 0.444); any-edge >=3.5%: 30b 15W +3.89u (ROI 0.130)
- ncaab-spread-v6: fixed     n 50, Brier 0.2307, log loss 0.6492, ECE 0.094, mean p 0.616 vs ask 0.639, hit 0.600, favours recorded side 0.82; forward rule >=3.5%: 11b 5W +1.58u (ROI 0.144); >=5%: 11b 5W +1.58u (ROI 0.144); >=8%: 8b 4W +2.89u (ROI 0.361); any-edge >=3.5%: 14b 8W +8.91u (ROI 0.636)
- ncaab-tempo-v2: as logged n 70, Brier 0.1836, log loss 0.5531, ECE 0.222, mean p 0.414 vs ask 0.208, hit 0.200, favours recorded side 0.24; forward rule >=3.5%: 15b 9W +5.12u (ROI 0.342); >=5%: 15b 9W +5.12u (ROI 0.342); >=8%: 14b 9W +6.12u (ROI 0.437); any-edge >=3.5%: 60b 13W -14.49u (ROI -0.241)
- ncaab-tempo-v2: fixed     n 70, Brier 0.1559, log loss 0.4882, ECE 0.165, mean p 0.346 vs ask 0.208, hit 0.200, favours recorded side 0.21; forward rule >=3.5%: 13b 7W +3.04u (ROI 0.234); >=5%: 13b 7W +3.04u (ROI 0.234); >=8%: 12b 6W +1.91u (ROI 0.159); any-edge >=3.5%: 61b 11W -19.57u (ROI -0.321)
- ncaab-tempo-v3: as logged n 180, Brier 0.1725, log loss 0.5304, ECE 0.233, mean p 0.378 vs ask 0.154, hit 0.144, favours recorded side 0.16; forward rule >=3.5%: 26b 7W -11.17u (ROI -0.429); >=5%: 24b 6W -11.30u (ROI -0.471); >=8%: 19b 4W -9.89u (ROI -0.520); any-edge >=3.5%: 167b 20W -69.86u (ROI -0.418)
- ncaab-tempo-v3: fixed     n 180, Brier 0.1440, log loss 0.4577, ECE 0.162, mean p 0.305 vs ask 0.154, hit 0.144, favours recorded side 0.15; forward rule >=3.5%: 21b 6W -4.40u (ROI -0.210); >=5%: 21b 6W -4.40u (ROI -0.210); >=8%: 21b 6W -4.40u (ROI -0.210); any-edge >=3.5%: 157b 15W -73.92u (ROI -0.471)

main_tier_rows: control n 249, Brier 0.1350, log loss 0.4233, ECE 0.115, mean p 0.281 vs ask 0.180, hit 0.177, favours recorded side 0.22; forward rule >=3.5%: 54b 23W -1.27u (ROI -0.023); >=5%: 50b 21W -1.07u (ROI -0.021); >=8%: 45b 19W -0.03u (ROI -0.001); any-edge >=3.5%: 196b 30W -75.50u (ROI -0.385)
main_tier_rows: fixed   n 249, Brier 0.1476, log loss 0.4667, ECE 0.153, mean p 0.330 vs ask 0.180, hit 0.177, favours recorded side 0.21; forward rule >=3.5%: 38b 16W +1.68u (ROI 0.044); >=5%: 38b 16W +1.68u (ROI 0.044); >=8%: 36b 15W +1.55u (ROI 0.043); any-edge >=3.5%: 215b 28W -91.26u (ROI -0.424)
executable_quote_rows: control n=0
executable_quote_rows: fixed   n=0
sample_ge_8: control n=0
sample_ge_8: fixed   n=0

## spread

- **as_logged**: n 408, Brier 0.3556, log loss 1.2404, ECE 0.271, mean p 0.759 vs ask 0.503, hit 0.500, favours recorded side 0.97; forward rule >=3.5%: 367b 180W -11.66u (ROI -0.032); >=5%: 352b 174W -8.10u (ROI -0.023); >=8%: 315b 150W -16.85u (ROI -0.053); any-edge >=3.5%: 368b 180W -12.66u (ROI -0.034)
- **control_pre_fix_v6**: n 408, Brier 0.3008, log loss 0.8357, ECE 0.204, mean p 0.666 vs ask 0.503, hit 0.500, favours recorded side 0.90; forward rule >=3.5%: 329b 158W -18.59u (ROI -0.057); >=5%: 311b 152W -12.33u (ROI -0.040); >=8%: 282b 132W -22.23u (ROI -0.079); any-edge >=3.5%: 339b 159W -21.92u (ROI -0.065)
- **fixed_v6**: n 408, Brier 0.2911, log loss 0.8053, ECE 0.179, mean p 0.644 vs ask 0.503, hit 0.500, favours recorded side 0.88; forward rule >=3.5%: 303b 147W -13.91u (ROI -0.046); >=5%: 285b 138W -13.49u (ROI -0.047); >=8%: 252b 127W -2.24u (ROI -0.009); any-edge >=3.5%: 316b 152W -6.79u (ROI -0.021)

by selection:
- away: control n 303, Brier 0.3155, log loss 0.8792, ECE 0.229, mean p 0.696 vs ask 0.504, hit 0.492, favours recorded side 0.91; forward rule >=3.5%: 248b 116W -20.32u (ROI -0.082); >=5%: 238b 113W -16.25u (ROI -0.068); >=8%: 222b 102W -21.64u (ROI -0.098); any-edge >=3.5%: 257b 116W -29.32u (ROI -0.114)
- away: fixed   n 303, Brier 0.3029, log loss 0.8382, ECE 0.205, mean p 0.680 vs ask 0.504, hit 0.492, favours recorded side 0.93; forward rule >=3.5%: 245b 117W -15.28u (ROI -0.062); >=5%: 233b 110W -16.98u (ROI -0.073); >=8%: 214b 103W -11.72u (ROI -0.055); any-edge >=3.5%: 257b 121W -13.83u (ROI -0.054)
- home: control n 105, Brier 0.2583, log loss 0.7100, ECE 0.151, mean p 0.577 vs ask 0.501, hit 0.524, favours recorded side 0.87; forward rule >=3.5%: 81b 42W +1.73u (ROI 0.021); >=5%: 73b 39W +3.92u (ROI 0.054); >=8%: 60b 30W -0.59u (ROI -0.010); any-edge >=3.5%: 82b 43W +7.40u (ROI 0.090)
- home: fixed   n 105, Brier 0.2571, log loss 0.7103, ECE 0.148, mean p 0.541 vs ask 0.501, hit 0.524, favours recorded side 0.75; forward rule >=3.5%: 58b 30W +1.37u (ROI 0.024); >=5%: 52b 28W +3.49u (ROI 0.067); >=8%: 38b 24W +9.48u (ROI 0.249); any-edge >=3.5%: 59b 31W +7.04u (ROI 0.119)

by original model (fixed v6 re-price):
- ncaab-spread-v5: as logged n 9, Brier n/a, log loss n/a, ECE n/a, mean p 0.699 vs ask 0.517, hit 0.444, favours recorded side 1.00; forward rule >=3.5%: 9b 4W -1.38u (ROI -0.153); >=5%: 9b 4W -1.38u (ROI -0.153); >=8%: 9b 4W -1.38u (ROI -0.153); any-edge >=3.5%: 9b 4W -1.38u (ROI -0.153)
- ncaab-spread-v5: fixed     n 9, Brier n/a, log loss n/a, ECE n/a, mean p 0.645 vs ask 0.517, hit 0.444, favours recorded side 1.00; forward rule >=3.5%: 8b 3W -2.26u (ROI -0.283); >=5%: 8b 3W -2.26u (ROI -0.283); >=8%: 8b 3W -2.26u (ROI -0.283); any-edge >=3.5%: 8b 3W -2.26u (ROI -0.283)
- ncaab-spread-v6: as logged n 90, Brier 0.2691, log loss 0.7614, ECE 0.094, mean p 0.638 vs ask 0.507, hit 0.544, favours recorded side 1.00; forward rule >=3.5%: 86b 47W +6.44u (ROI 0.075); >=5%: 80b 46W +10.44u (ROI 0.131); >=8%: 61b 31W +0.19u (ROI 0.003); any-edge >=3.5%: 86b 47W +6.44u (ROI 0.075)
- ncaab-spread-v6: fixed     n 90, Brier 0.2639, log loss 0.7364, ECE 0.125, mean p 0.602 vs ask 0.507, hit 0.544, favours recorded side 0.92; forward rule >=3.5%: 68b 37W +4.81u (ROI 0.071); >=5%: 59b 32W +4.11u (ROI 0.070); >=8%: 45b 27W +8.18u (ROI 0.182); any-edge >=3.5%: 68b 37W +4.81u (ROI 0.071)
- ncaab-tempo-v2: as logged n 130, Brier 0.3802, log loss 1.4542, ECE 0.342, mean p 0.789 vs ask 0.493, hit 0.485, favours recorded side 0.91; forward rule >=3.5%: 107b 49W -9.44u (ROI -0.088); >=5%: 103b 47W -8.76u (ROI -0.085); >=8%: 97b 44W -7.39u (ROI -0.076); any-edge >=3.5%: 108b 49W -10.44u (ROI -0.097)
- ncaab-tempo-v2: fixed     n 130, Brier 0.2827, log loss 0.7784, ECE 0.211, mean p 0.616 vs ask 0.493, hit 0.485, favours recorded side 0.81; forward rule >=3.5%: 79b 34W -12.47u (ROI -0.158); >=5%: 76b 33W -11.51u (ROI -0.151); >=8%: 70b 33W -5.51u (ROI -0.079); any-edge >=3.5%: 92b 39W -5.35u (ROI -0.058)
- ncaab-tempo-v3: as logged n 179, Brier 0.3843, log loss 1.3488, ECE 0.310, mean p 0.802 vs ask 0.508, hit 0.492, favours recorded side 0.99; forward rule >=3.5%: 165b 80W -7.28u (ROI -0.044); >=5%: 160b 77W -8.40u (ROI -0.052); >=8%: 148b 71W -8.27u (ROI -0.056); any-edge >=3.5%: 165b 80W -7.28u (ROI -0.044)
- ncaab-tempo-v3: fixed     n 179, Brier 0.3118, log loss 0.8626, ECE 0.228, mean p 0.686 vs ask 0.508, hit 0.492, favours recorded side 0.91; forward rule >=3.5%: 148b 73W -3.98u (ROI -0.027); >=5%: 142b 70W -3.83u (ROI -0.027); >=8%: 129b 64W -2.65u (ROI -0.021); any-edge >=3.5%: 148b 73W -3.98u (ROI -0.027)

main_tier_rows: control n 336, Brier 0.3099, log loss 0.8563, ECE 0.212, mean p 0.680 vs ask 0.499, hit 0.488, favours recorded side 0.93; forward rule >=3.5%: 289b 139W -15.92u (ROI -0.055); >=5%: 277b 134W -13.73u (ROI -0.050); >=8%: 254b 118W -21.87u (ROI -0.086); any-edge >=3.5%: 298b 140W -18.25u (ROI -0.061)
main_tier_rows: fixed   n 336, Brier 0.3003, log loss 0.8277, ECE 0.194, mean p 0.658 vs ask 0.499, hit 0.488, favours recorded side 0.92; forward rule >=3.5%: 269b 131W -11.39u (ROI -0.042); >=5%: 258b 124W -14.13u (ROI -0.055); >=8%: 228b 115W -1.79u (ROI -0.008); any-edge >=3.5%: 279b 134W -9.47u (ROI -0.034)
executable_quote_rows: control n 5, Brier n/a, log loss n/a, ECE n/a, mean p 0.606 vs ask 0.510, hit 0.800, favours recorded side 1.00; forward rule >=3.5%: 5b 4W +2.86u (ROI 0.571); >=5%: 5b 4W +2.86u (ROI 0.571); >=8%: 3b 2W +0.96u (ROI 0.321); any-edge >=3.5%: 5b 4W +2.86u (ROI 0.571)
executable_quote_rows: fixed   n 5, Brier n/a, log loss n/a, ECE n/a, mean p 0.576 vs ask 0.510, hit 0.800, favours recorded side 0.80; forward rule >=3.5%: 4b 3W +1.82u (ROI 0.454); >=5%: 3b 2W +0.96u (ROI 0.321); >=8%: 2b 2W +1.96u (ROI 0.982); any-edge >=3.5%: 4b 3W +1.82u (ROI 0.454)
sample_ge_8: control n=0
sample_ge_8: fixed   n=0

## total

- **as_logged**: n 454, Brier 0.2502, log loss 0.7019, ECE 0.111, mean p 0.636 vs ask 0.518, hit 0.529, favours recorded side 0.95; forward rule >=3.5%: 347b 181W +7.48u (ROI 0.022); >=5%: 313b 161W +2.30u (ROI 0.007); >=8%: 238b 128W +12.19u (ROI 0.051); any-edge >=3.5%: 359b 186W +10.51u (ROI 0.029)
- **control_pre_fix_v6**: n 454, Brier 0.2535, log loss 0.7002, ECE 0.016, mean p 0.523 vs ask 0.518, hit 0.529, favours recorded side 0.99; forward rule >=3.5%: 142b 60W -16.92u (ROI -0.119); >=5%: 103b 39W -19.58u (ROI -0.190); >=8%: 54b 18W -12.93u (ROI -0.239); any-edge >=3.5%: 142b 60W -16.92u (ROI -0.119)
- **fixed_v6**: n 454, Brier 0.2512, log loss 0.6956, ECE 0.019, mean p 0.510 vs ask 0.518, hit 0.529, favours recorded side 0.87; forward rule >=3.5%: 79b 32W -9.36u (ROI -0.118); >=5%: 50b 19W -6.52u (ROI -0.130); >=8%: 27b 9W -3.86u (ROI -0.143); any-edge >=3.5%: 79b 32W -9.36u (ROI -0.118)

by selection:
- over: control n 181, Brier 0.2557, log loss 0.7048, ECE 0.031, mean p 0.527 vs ask 0.520, hit 0.525, favours recorded side 1.00; forward rule >=3.5%: 62b 25W -8.53u (ROI -0.138); >=5%: 52b 21W -6.27u (ROI -0.121); >=8%: 25b 7W -7.57u (ROI -0.303); any-edge >=3.5%: 62b 25W -8.53u (ROI -0.138)
- over: fixed   n 181, Brier 0.2507, log loss 0.6945, ECE 0.035, mean p 0.515 vs ask 0.520, hit 0.525, favours recorded side 0.92; forward rule >=3.5%: 40b 17W -2.27u (ROI -0.057); >=5%: 26b 11W -0.16u (ROI -0.006); >=8%: 15b 5W -1.46u (ROI -0.097); any-edge >=3.5%: 40b 17W -2.27u (ROI -0.057)
- under: control n 273, Brier 0.2520, log loss 0.6972, ECE 0.014, mean p 0.521 vs ask 0.516, hit 0.531, favours recorded side 0.98; forward rule >=3.5%: 80b 35W -8.39u (ROI -0.105); >=5%: 51b 18W -13.31u (ROI -0.261); >=8%: 29b 11W -5.36u (ROI -0.185); any-edge >=3.5%: 80b 35W -8.39u (ROI -0.105)
- under: fixed   n 273, Brier 0.2516, log loss 0.6964, ECE 0.024, mean p 0.507 vs ask 0.516, hit 0.531, favours recorded side 0.83; forward rule >=3.5%: 39b 15W -7.09u (ROI -0.182); >=5%: 24b 8W -6.36u (ROI -0.265); >=8%: 12b 4W -2.40u (ROI -0.200); any-edge >=3.5%: 39b 15W -7.09u (ROI -0.182)

by original model (fixed v6 re-price):
- ncaab-tempo-v2: as logged n 155, Brier 0.2070, log loss 0.6020, ECE 0.093, mean p 0.669 vs ask 0.541, hit 0.587, favours recorded side 0.88; forward rule >=3.5%: 94b 55W +11.61u (ROI 0.123); >=5%: 89b 51W +8.84u (ROI 0.099); >=8%: 72b 45W +14.18u (ROI 0.197); any-edge >=3.5%: 106b 60W +14.64u (ROI 0.138)
- ncaab-tempo-v2: fixed     n 155, Brier 0.2500, log loss 0.6931, ECE 0.087, mean p 0.500 vs ask 0.541, hit 0.587, favours recorded side 1.00; forward rule >=3.5%: 26b 7W -6.31u (ROI -0.243); >=5%: 24b 6W -6.53u (ROI -0.272); >=8%: 21b 5W -5.86u (ROI -0.279); any-edge >=3.5%: 26b 7W -6.31u (ROI -0.243)
- ncaab-tempo-v3: as logged n 177, Brier 0.2770, log loss 0.7720, ECE 0.137, mean p 0.645 vs ask 0.506, hit 0.520, favours recorded side 0.99; forward rule >=3.5%: 150b 79W +5.79u (ROI 0.039); >=5%: 141b 74W +4.98u (ROI 0.035); >=8%: 120b 63W +4.39u (ROI 0.037); any-edge >=3.5%: 150b 79W +5.79u (ROI 0.039)
- ncaab-tempo-v3: fixed     n 177, Brier 0.2495, log loss 0.6922, ECE 0.026, mean p 0.506 vs ask 0.506, hit 0.520, favours recorded side 0.77; forward rule >=3.5%: 12b 7W +2.01u (ROI 0.168); >=5%: 4b 3W +2.16u (ROI 0.540); >=8%: 0b 0W +0.00u (ROI n/a); any-edge >=3.5%: 12b 7W +2.01u (ROI 0.168)
- ncaab-total-v5: as logged n 12, Brier 0.2184, log loss 0.6251, ECE 0.048, mean p 0.619 vs ask 0.507, hit 0.667, favours recorded side 1.00; forward rule >=3.5%: 12b 8W +3.78u (ROI 0.315); >=5%: 10b 7W +3.85u (ROI 0.385); >=8%: 7b 4W +0.85u (ROI 0.122); any-edge >=3.5%: 12b 8W +3.78u (ROI 0.315)
- ncaab-total-v5: fixed     n 12, Brier 0.2443, log loss 0.6818, ECE 0.148, mean p 0.519 vs ask 0.507, hit 0.667, favours recorded side 0.83; forward rule >=3.5%: 1b 1W +1.04u (ROI 1.040); >=5%: 0b 0W +0.00u (ROI n/a); >=8%: 0b 0W +0.00u (ROI n/a); any-edge >=3.5%: 1b 1W +1.04u (ROI 1.040)
- ncaab-total-v6: as logged n 110, Brier 0.2716, log loss 0.7383, ECE 0.134, mean p 0.580 vs ask 0.504, hit 0.446, favours recorded side 1.00; forward rule >=3.5%: 91b 39W -13.70u (ROI -0.150); >=5%: 73b 29W -15.36u (ROI -0.210); >=8%: 39b 16W -7.22u (ROI -0.185); any-edge >=3.5%: 91b 39W -13.70u (ROI -0.150)
- ncaab-total-v6: fixed     n 110, Brier 0.2564, log loss 0.7060, ECE 0.135, mean p 0.530 vs ask 0.504, hit 0.446, favours recorded side 0.85; forward rule >=3.5%: 40b 17W -6.11u (ROI -0.153); >=5%: 22b 10W -2.15u (ROI -0.098); >=8%: 6b 4W +2.00u (ROI 0.334); any-edge >=3.5%: 40b 17W -6.11u (ROI -0.153)

main_tier_rows: control n 323, Brier 0.2537, log loss 0.7007, ECE 0.021, mean p 0.521 vs ask 0.494, hit 0.520, favours recorded side 0.99; forward rule >=3.5%: 97b 41W -9.63u (ROI -0.099); >=5%: 68b 24W -14.48u (ROI -0.213); >=8%: 39b 12W -9.86u (ROI -0.253); any-edge >=3.5%: 97b 41W -9.63u (ROI -0.099)
main_tier_rows: fixed   n 323, Brier 0.2514, log loss 0.6959, ECE 0.013, mean p 0.508 vs ask 0.494, hit 0.520, favours recorded side 0.87; forward rule >=3.5%: 52b 20W -6.25u (ROI -0.120); >=5%: 33b 11W -5.41u (ROI -0.164); >=8%: 21b 6W -3.90u (ROI -0.186); any-edge >=3.5%: 52b 20W -6.25u (ROI -0.120)
executable_quote_rows: control n 5, Brier n/a, log loss n/a, ECE n/a, mean p 0.564 vs ask 0.500, hit 0.800, favours recorded side 1.00; forward rule >=3.5%: 5b 4W +2.96u (ROI 0.592); >=5%: 3b 2W +0.96u (ROI 0.320); >=8%: 1b 1W +1.00u (ROI 1.000); any-edge >=3.5%: 5b 4W +2.96u (ROI 0.592)
executable_quote_rows: fixed   n 5, Brier n/a, log loss n/a, ECE n/a, mean p 0.556 vs ask 0.500, hit 0.800, favours recorded side 1.00; forward rule >=3.5%: 4b 3W +1.96u (ROI 0.490); >=5%: 3b 2W +1.00u (ROI 0.333); >=8%: 0b 0W +0.00u (ROI n/a); any-edge >=3.5%: 4b 3W +1.96u (ROI 0.490)
sample_ge_8: control n=0
sample_ge_8: fixed   n=0
