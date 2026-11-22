"""Extra cuts: pooled headline, availability sign split, back-to-back from ledger schedule, v9 total calibrator rows."""

from datetime import timedelta

import numpy as np
import pandas as pd

Q = pd.read_pickle("quoted_final.pkl")
m = pd.read_pickle("enriched.pkl")


def rec(g):
    n = len(g)
    w = int(g.y.sum())
    return (
        f"{w}-{n - w} ({w / n:.1%}), pnl {g.pnl.sum():+.2f}u, ROI {g.pnl.sum() / g.units.sum():+.1%}, n={n}"
        if n
        else "n=0"
    )


print("## Pooled headline (quoted)")
for mk, g in Q.groupby("mkt"):
    print(
        f"- {mk}: all {rec(g)}; QUAL {rec(g[g.regime == 'QUAL'])}; mean model {g.p_model.mean():.1%} "
        f"vs ask {g.p_mkt.mean():.1%}"
    )
print(f"- ALL: {rec(Q)}")
print(f"- since v9 start (event >= 11-08): {rec(Q[Q.start >= pd.Timestamp('2026-11-08', tz='UTC')])}")

print("\n## Availability sign (v2-v5 spreads)")
a = Q[(Q.mt == "spread") & Q.avail_gap.notna()].copy()
a["dir"] = np.where(
    a.avail_gap.abs() <= 0.5,
    "|gap|<=0.5 (none)",
    np.where((a.selection == "home") == (a.avail_gap > 0), "toward pick", "against pick"),
)
for k, g in a.groupby("dir"):
    print(f"- {k}: {rec(g)}")
print("avail_gap distribution:", a.avail_gap.describe().round(2).to_dict())
t = Q[(Q.mt == "total") & Q.avail_loss.notna()]
print("avail_total_loss distribution:", t.avail_loss.describe().round(2).to_dict())

print("\n## Back-to-back (derived from all ledger NBA game start times, any status)")
sched = m.drop_duplicates("event_id")[["event_id", "start", "home", "away"]]
long = pd.concat([sched.assign(team=sched.home), sched.assign(team=sched.away)])[
    ["event_id", "start", "team"]
]
long = long.sort_values(["team", "start"])
long["prev"] = long.groupby("team").start.shift(1)
long["b2b"] = (long.start - long.prev) <= timedelta(hours=30)
b2b = long.set_index(["event_id", "team"]).b2b
print(
    f"schedule games: {sched.event_id.nunique()}, first {sched.start.min():%m-%d}, last {sched.start.max():%m-%d}"
)
sm = Q[Q.mt != "total"].copy()
sm["pick_team"] = np.where(sm.selection == "home", sm.home, sm.away)
sm["opp_team"] = np.where(sm.selection == "home", sm.away, sm.home)
sm["pick_b2b"] = [b2b.get((e, t), False) for e, t in zip(sm.event_id, sm.pick_team)]
sm["opp_b2b"] = [b2b.get((e, t), False) for e, t in zip(sm.event_id, sm.opp_team)]
sm["b2b"] = np.select(
    [sm.pick_b2b & ~sm.opp_b2b, ~sm.pick_b2b & sm.opp_b2b, sm.pick_b2b & sm.opp_b2b],
    ["our team on B2B", "opponent on B2B", "both B2B"],
    "neither",
)
for (mk, k), g in sm.groupby(["mkt", "b2b"]):
    print(f"- {mk} {k}: {rec(g)}")
tq = Q[Q.mt == "total"].copy()
tq["anyb2b"] = [
    b2b.get((e, h), False) or b2b.get((e, a_), False) for e, h, a_ in zip(tq.event_id, tq.home, tq.away)
]
for (sel, k), g in tq.groupby(["selection", "anyb2b"]):
    print(f"- total {sel} any-team-B2B={k}: {rec(g)}")

print("\n## v9 totals rows with the 'Calibrated X -> Y' layer (11-10..11-12)")
cal = Q[Q.rationale.str.contains("Calibrated", na=False)]
print(f"n={len(cal)}; regimes {cal.regime.value_counts().to_dict()}; {rec(cal) if len(cal) else ''}")
print(f"of which calibrated model prob < 50% on the chosen side: {(cal.p_model < 0.5).sum()}")
allcal = m[m.rationale.str.contains("Calibrated", na=False)]
print(
    f"all statuses with calibrator text: {len(allcal)}; models {allcal.model.value_counts().to_dict()}; "
    f"dates {allcal.start.min():%m-%d}..{allcal.start.max():%m-%d}"
)

print("\n## Model-vs-market gap direction for totals (v9): picks vs model below/above market")
v9 = Q[(Q.mshort == "total-margin-v9") & Q.fair_dec.notna()].copy()
v9["gap"] = v9.exp_total - v9.fair_dec
for k, g in v9.groupby(pd.cut(v9.gap, [-50, -6, -3, 0, 3, 50])):
    print(f"- model-market {k}: {rec(g)}; overs {int((g.selection == 'over').sum())}")
print("\n## Totals: actual minus market fair line, all v9 settled games")
print(f"mean {(v9.hs + v9.as_ - v9.fair_dec).mean():+.2f}, n={len(v9)}")

print(
    "\n## Market-quoted ROI if we had simply bet every v9 total/spread at the decision ask in the model's direction,"
    " by month"
)
for mk, g in Q[Q.mshort.str.endswith("v9")].groupby("mkt"):
    for wk, h in g.groupby(g.start.dt.strftime("%m-%d").str[:4] + "x"):
        pass
Q["wk"] = (Q.start - pd.Timestamp("2026-10-20", tz="UTC")).dt.days // 7
for (mk, wk), g in Q.groupby(["mkt", "wk"]):
    print(f"- {mk} week{wk} (from {Q[Q.wk == wk].start.min():%m-%d}): {rec(g)}")
