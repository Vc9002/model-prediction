"""Follow-up checks: v9 breakout, attribution union, fair_line validation, void/archive check, CIs."""

import glob
import sqlite3

import numpy as np
import pandas as pd

rng = np.random.default_rng(11)
Q = pd.read_pickle("quoted_final.pkl")
m = pd.read_pickle("enriched.pkl")


def rec(g):
    n = len(g)
    w = int(g.y.sum())
    if not n:
        return "n=0"
    return (
        f"{w}-{n - w} ({w / n:.1%}), pnl {g.pnl.sum():+.2f}u, ROI {g.pnl.sum() / g.units.sum():+.1%}, "
        f"model {g.p_model.mean():.1%}, ask {g.p_mkt.mean():.1%}, n={n}"
    )


def brier_diff(g):
    g = g[g.dec_mid.notna()]
    d = ((g.p_model - g.y) ** 2 - (g.dec_mid - g.y) ** 2).values
    bs = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(4000)]
    return f"{d.mean():+.4f} [{np.percentile(bs, 2.5):+.4f},{np.percentile(bs, 97.5):+.4f}] n={len(d)}"


print("## v9 only")
v9 = Q[Q.mshort.str.endswith("v9")]
for mk, g in v9.groupby("mkt"):
    print(f"- {mk}: {rec(g)}; Brier(model)-Brier(mid) {brier_diff(g)}")
    print(f"    QUAL: {rec(g[g.regime == 'QUAL'])}")
print(f"- v9 all markets: {rec(v9)}")
print(f"- all models, events since 11-08: {rec(Q[Q.start >= pd.Timestamp('2026-11-08', tz='UTC')])}")
print("v9 sides:")
for lab, g in [
    ("spread home", v9[(v9.mt == "spread") & (v9.selection == "home")]),
    ("spread away", v9[(v9.mt == "spread") & (v9.selection == "away")]),
    ("spread fav", v9[(v9.mt == "spread") & (v9.line < 0)]),
    ("spread dog", v9[(v9.mt == "spread") & (v9.line > 0)]),
    ("spread |L|<=3.5", v9[(v9.mt == "spread") & (v9.line.abs() <= 3.5)]),
    ("spread |L| 4-9.5", v9[(v9.mt == "spread") & (v9.line.abs().between(4, 9.5))]),
    ("spread |L| 10+", v9[(v9.mt == "spread") & (v9.line.abs() >= 10)]),
    ("total over", v9[(v9.mt == "total") & (v9.selection == "over")]),
    ("total under", v9[(v9.mt == "total") & (v9.selection == "under")]),
    ("total line 235+", v9[(v9.mt == "total") & (v9.line >= 235)]),
    ("total line <225", v9[(v9.mt == "total") & (v9.line < 225)]),
]:
    print(f"- {lab}: {rec(g)}")

print("\n## Spread loss attribution (union)")
sp = Q[Q.mt == "spread"]
u = sp[(sp.regime == "FLAT") | (sp.mshort == "spread-margin-v5")]
print(f"- all quoted spreads: {rec(sp)}")
print(f"- FLAT or v5 (union): {rec(u)}")
print(f"- complement: {rec(sp.drop(u.index))}")
print(f"- FLAT and v5 overlap n={len(sp[(sp.regime == 'FLAT') & (sp.mshort == 'spread-margin-v5')])}")
print(
    f"- all settled spread/total rows quoted? unquoted spread/total in deduped settled: "
    f"{int(((m.status == 'settled') & m.mt.isin(['spread', 'total']) & (m.provenance != 'decision_time_executable_quote')).sum())}"
)

print("\n## fair_line validation (ask 47-53c)")
f = Q[Q.fair_dec.notna() & Q.p_mkt.between(0.47, 0.53)].copy()
f["d"] = np.where(f.mt == "total", f.fair_dec - f.line, f.fair_dec - f.line)
print(
    f"n={len(f)}; |fair_dec - pick line|: median {f.d.abs().median():.2f}, p95 {f.d.abs().quantile(0.95):.2f}, "
    f"max {f.d.abs().max():.2f}; >1.5 pts: {(f.d.abs() > 1.5).sum()}"
)
print(f[f.d.abs() > 1.5][["mshort", "selection", "line", "fair_dec", "p_mkt", "slug"]].head(10).to_string())
fc = Q[Q.fair_close.notna() & Q.fair_dec.notna()]
print(
    f"|fair_close - fair_dec| max {(fc.fair_close - fc.fair_dec).abs().max():.2f}, "
    f">3 pts: {((fc.fair_close - fc.fair_dec).abs() > 3).sum()}"
)
t9 = Q[(Q.mshort == "total-margin-v9")]
act = t9.hs + t9.as_
print(
    f"v9 totals: actual - pick line mean {(act - t9.line).mean():+.2f} (n={len(t9)}), "
    f"actual - fair_dec {(act - t9.fair_dec).mean():+.2f}, actual - exp_total {(act - t9.exp_total).mean():+.2f}, "
    f"SE {(act - t9.exp_total).std() / np.sqrt(len(t9)):.2f}"
)

print("\n## residual corr CIs (one row per event x model, settled)")
res = m[(m.status == "settled") & m.hs.notna()].copy()
res = res.sort_values("tier", key=lambda s: (s != "main").astype(int)).drop_duplicates(["event_id", "model"])
for mt, mcol in [("spread", "exp_margin"), ("total", "exp_total")]:
    g = res[(res.mt == mt) & res.fair_dec.notna()]
    if mt == "total":
        mk = g.fair_dec.values
        act = (g.hs + g.as_).values
    else:
        mk = np.where(g.selection == "home", -g.fair_dec, g.fair_dec)
        act = (g.hs - g.as_).values
    x = g[mcol].values - mk
    yv = act - mk
    r = np.corrcoef(x, yv)[0, 1]
    n = len(x)
    z = np.arctanh(r)
    lo, hi = np.tanh(z - 1.96 / np.sqrt(n - 3)), np.tanh(z + 1.96 / np.sqrt(n - 3))
    b, a = np.polyfit(x, yv, 1)
    resid = yv - (a + b * x)
    se = np.sqrt(resid.var(ddof=2) / ((x - x.mean()) ** 2).sum())
    print(
        f"- {mt}: r={r:+.3f} [{lo:+.3f},{hi:+.3f}], slope {b:+.2f} (SE {se:.2f}), n={n}; "
        f"v9 only r={np.corrcoef(x[g.model.str.endswith('v9').values], yv[g.model.str.endswith('v9').values])[0, 1]:+.3f}"
        f" n={int(g.model.str.endswith('v9').sum())}"
    )

print("\n## void/archive check")
c = sqlite3.connect("file:E:/model-prediction-runtime/ledgers/ledgers.db?mode=ro", uri=True)
print([r[1] for r in c.execute("pragma table_info(ledger_events)")])
try:
    print(
        c.execute(
            "select e.event_type, count(*) from ledger_events e join ledger_records r on r.pick_id=e.pick_id "
            "where r.sport='nba' group by 1"
        ).fetchall()
    )
except Exception as ex:  # noqa: BLE001
    print("join failed", ex)
    print(c.execute("select * from ledger_events limit 2").fetchall())
arch = [
    p for p in glob.glob("E:/model-prediction-runtime/**/archive*/**/*", recursive=True) if "nba" in p.lower()
][:10]
print("runtime archive files mentioning nba:", arch)
arch2 = [p for p in glob.glob("e:/MP/data/archive/**/*", recursive=True) if "nba" in p.lower()][:10]
print("repo data/archive files mentioning nba:", arch2)

print("\n## schedule coverage for B2B derivation")
sched = m.drop_duplicates("event_id")
days = sched.start.dt.tz_convert("America/New_York").dt.date.value_counts().sort_index()
alld = pd.date_range(days.index.min(), days.index.max()).date
print("dates with 0 ledger games:", [str(d) for d in alld if d not in days.index])
print("games per date:", {str(k): int(v) for k, v in days.items()})
