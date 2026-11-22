"""Join settled+superseded NBA rows to Polymarket snapshot history.

Adds decision-time midpoint, last-pre-tip ('close') midpoint/ask, fair main line at decision and
close (pick-team / total terms), and the gap from last capture to tip. Writes enriched.pkl.
"""

from datetime import timedelta

import numpy as np
import pandas as pd

df = pd.read_pickle("nba_rows.pkl")
snaps = pd.read_pickle("snaps.pkl")
snaps = snaps[snaps.obs.notna()].sort_values("obs")
by_slug = {k: g for k, g in snaps.groupby("slug")}
by_event_mt = {k: g for k, g in snaps.groupby(["event_slug", "mt"])}


def norm_team(s):
    return (s or "").lower().replace(".", "").strip()


def team_match(a, b):
    a, b = norm_team(a), norm_team(b)
    if not a or not b:
        return False
    return a == b or a.split()[-1] == b.split()[-1]


def mid(b, a):
    if a is None or b is None or np.isnan(a) or np.isnan(b):
        return np.nan
    return (a + b) / 2


def side_of(r, s):
    """Return ('long'|'short') for the pick's side in snapshot row s."""
    if r.mt == "total":
        return "long" if r.selection == "over" else "short"
    pick_team = r.home if r.selection == "home" else r.away
    if team_match(pick_team, s.team):
        return "long"
    other = r.away if r.selection == "home" else r.home
    if team_match(other, s.team):
        return "short"
    return None


def side_px(s, side):
    if side == "long":
        return s.l_ask, mid(s.l_bid, s.l_ask)
    return s.s_ask, mid(s.s_bid, s.s_ask)


def fair_line(r, t):
    """Fair 50c line in pick terms at time t: pick-team spread line, or total line."""
    if r.mt == "moneyline" or not r.slug:
        return np.nan
    ev = r.slug.split("-", 1)[1]
    ev = ev.rsplit("-pos-", 1)[0].rsplit("-neg-", 1)[0]
    if r.mt == "total":
        ev = ev.rsplit("-", 1)[0]
    g = by_event_mt.get((ev, r.mt))
    if g is None:
        return np.nan
    g = g[(g.obs <= t) & (g.obs >= t - timedelta(minutes=30))]
    if g.empty:
        return np.nan
    g = g.sort_values("obs").drop_duplicates("slug", keep="last")
    pts = []
    for s in g.itertuples():
        if s.state and s.state != "MARKET_STATE_OPEN":
            continue
        lm = mid(s.l_bid, s.l_ask)
        if np.isnan(lm):
            continue
        if r.mt == "total":
            pts.append((float(s.line), 1 - lm))  # (line, P(under)) increasing in line
        else:
            try:
                desc = float(str(s.l_desc).replace("+", ""))
            except ValueError:
                continue
            side = side_of(r, s)
            if side == "long":
                pts.append((desc, lm))
            elif side == "short":
                pts.append((-desc, 1 - lm))
    if len(pts) < 2:
        return np.nan
    pts.sort()
    for (x0, p0), (x1, p1) in zip(pts, pts[1:]):
        if p0 <= 0.5 <= p1 and p1 > p0:
            return x0 + (0.5 - p0) * (x1 - x0) / (p1 - p0)
    return np.nan


out = []
for r in df.itertuples():
    rec = dict(pick_id=r.pick_id, tier=r.tier)
    g = by_slug.get(r.slug) if r.slug else None
    if g is not None and r.quote_obs is not None:
        pre = g[g.obs <= r.quote_obs + timedelta(seconds=60)]
        if not pre.empty:
            s = pre.iloc[-1]
            side = side_of(r, s)
            if side:
                a, m = side_px(s, side)
                rec.update(dec_ask=a, dec_mid=m, dec_snap_obs=s.obs, side=side)
        pt = g[g.obs < r.start]
        if not pt.empty:
            s = pt.iloc[-1]
            side = side_of(r, s)
            if side:
                a, m = side_px(s, side)
                rec.update(
                    close_ask=a,
                    close_mid=m,
                    close_obs=s.obs,
                    close_gap_min=(r.start - s.obs).total_seconds() / 60,
                )
    if r.mt != "moneyline" and r.quote_obs is not None:
        rec["fair_dec"] = fair_line(r, r.quote_obs + timedelta(seconds=60))
        g2 = None
        if r.slug:
            # last capture batch before tip
            gg = by_slug.get(r.slug)
            if gg is not None:
                pt = gg[gg.obs < r.start]
                if not pt.empty:
                    g2 = pt.obs.iloc[-1]
        rec["fair_close"] = fair_line(r, g2 + timedelta(seconds=60)) if g2 is not None else np.nan
    out.append(rec)

e = pd.DataFrame(out)
m = df.merge(e, on=["pick_id", "tier"], how="left")
m.to_pickle("enriched.pkl")
q = m[(m.status == "settled") & (m.provenance == "decision_time_executable_quote")]
print(
    "settled quoted rows",
    len(q),
    "with dec_ask",
    q.dec_ask.notna().sum(),
    "with close",
    q.close_mid.notna().sum(),
)
d = (q.dec_ask - q.p_mkt).abs()
print("dec_ask vs ledger p_mkt: n", d.notna().sum(), "within 0.006:", (d <= 0.006).sum(), "max", d.max())
print(q[d > 0.006][["model", "mt", "selection", "line", "p_mkt", "dec_ask", "slug"]].head(15).to_string())
print("fair_dec non-null", q.fair_dec.notna().sum(), "fair_close", q.fair_close.notna().sum())
