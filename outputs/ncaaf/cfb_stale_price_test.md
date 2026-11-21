# NCAAF stale-price test (quick-261121-spt, overhaul step 1)

- Verdict (ESPN-close arm, pooled, UPPER BOUND): **INCONCLUSIVE**
- Rule `cfb-stale-price-rule-1` sha256 `5f72f6daf3340d22c30bc968853c0cea2d79636eba4f81040389da1adf568389`
- Script sha256 `b4238c321ef925d629a84a825d45843f5123a12c0f1be88e9a380872f3eb9f0a` at commit `ee49c5d611942f7b83cec20b8ed30a883f8d4288`
- Run at 2026-11-21T04:35:02Z; data cutoff 2026-11-21T04:00:00Z; fee 0; max ask 48c; ask size >= 1.0

## Match rates

- Polymarket spread/total events matched to a 2026 game: 1033 of 1046
- Matched with an ESPN 2026 close: 569 of 1046; ESPN lined events covered: 569 of 731

## Results

| Arm | Market | n | W-L | Hit | Break-even (mean ask) | Hit - BE [95% CI] | ROI [95% CI] | PnL U |
|---|---|---|---|---|---|---|---|---|
| espn_close | pooled | 222 | 118-104 | 53.1% | 46.9% | +6.3 pts [-1.7%, +13.1%] | +13.3% [-3.9%, +27.8%] | +29.49 |
| espn_close | spread | 99 | 50-49 | 50.5% | 47.0% | +3.5 pts [-3.8%, +13.4%] | +7.3% [-8.5%, +28.9%] | +7.20 |
| espn_close | total | 123 | 68-55 | 55.3% | 46.7% | +8.6 pts [-5.7%, +16.8%] | +18.1% [-11.9%, +35.5%] | +22.29 |
| dk_asof | pooled | 4 | 2-2 | 50.0% | 48.0% | +2.0 pts [-48.0%, +52.0%] | +4.2% [-100.0%, +108.3%] | +0.17 |
| dk_asof | spread | 0 | - | - | - | - | - | - |
| dk_asof | total | 4 | 2-2 | 50.0% | 48.0% | +2.0 pts [-48.0%, +52.0%] | +4.2% [-100.0%, +108.3%] | +0.17 |

## Selection funnel (outcome-free)

- espn_close: {"spread:integer_anchor_no_pick": 93, "spread:no_anchor": 384, "spread:no_rung_at_anchor": 181, "spread:picked": 99, "spread:rung_but_no_qualifying_ask": 150, "total:no_anchor": 356, "total:no_rung_at_anchor": 231, "total:picked": 123, "total:rung_but_no_qualifying_ask": 166}; ungraded 0; pushes 0
- dk_asof: {"spread:no_anchor": 901, "spread:no_rung_at_anchor": 1, "spread:rung_but_no_qualifying_ask": 5, "total:no_anchor": 870, "total:picked": 4, "total:rung_but_no_qualifying_ask": 2}; ungraded 0; pushes 0

## Side-mapping check (outcome-free)

- espn_close: latest pre-kickoff midpoint of P(home covers / over) at the anchor rung: {"spread": {"n_abs_dev_gt_0p15": 0, "n_events": 249, "quartiles": [0.485, 0.4975, 0.5125]}, "total": {"n_abs_dev_gt_0p15": 0, "n_events": 289, "quartiles": [0.485, 0.495, 0.515]}}
- dk_asof: latest pre-kickoff midpoint of P(home covers / over) at the anchor rung: {"spread": {"n_abs_dev_gt_0p15": 0, "n_events": 5, "quartiles": [0.4975, 0.5125, 0.5175]}, "total": {"n_abs_dev_gt_0p15": 0, "n_events": 6, "quartiles": [0.48, 0.5, 0.505]}}

## Volume

- ESPN-close arm picks per captured ET Saturday: mean 20.44; by date {"2026-09-05": 42, "2026-09-12": 52, "2026-09-19": 12, "2026-09-26": 3, "2026-10-03": 13, "2026-10-10": 1, "2026-10-24": 27, "2026-10-31": 31, "2026-11-14": 3}
- Total picks, all days: ESPN-close 222, DraftKings as-of 4. the espn_close anchor exists only for games before the 2026-11-15 ESPN fetch, and Saturdays whose full-game rows are all timestamp_valid false have no kept rung.

## Caveats

- The ESPN close postdates every quote, so the ESPN arm is an upper bound on a real-time rule.
- The DraftKings arm is decision-time clean but tiny and carries no verdict.
- Ask sizes are top-of-book; capacity beyond the quoted size is not tested.
- No platform fee is modelled.
