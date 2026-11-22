"""Side/price/line re-derivation over EVERY digest-bound NCAAF row, any status, to reconcile known flipped rows."""
import collections
import pickle
import re
import sys

sys.path.insert(0, "E:/MP/src")
from model_prediction.data_sources.cfb_data import resolve_team  # noqa: E402
from model_prediction.learned_forward import _team_matches  # noqa: E402

OUT = "E:/MP/outputs/ncaaf/bug_hunt"
rows = pickle.load(open(f"{OUT}/rows.pkl", "rb"))
Q = pickle.load(open(f"{OUT}/quotes.pkl", "rb"))
byd = {q["digest"]: q for q in Q}
ml = {}
for q in Q:
    if q["mt"] == "moneyline" and q["ld"]:
        ml.setdefault(q["pm_event_id"], (q["ld"], q["sd"]))


def clean(n):
    return (n or "").replace("(", "").replace(")", "").strip()


def title_side(title, away, home):
    parts = None
    for sep in (" vs. ", " vs ", " @ ", " at "):
        if sep in (title or ""):
            parts = title.split(sep, 1)
            break
    if not parts:
        return None
    p1, p2 = parts
    ar, hr = resolve_team(clean(away)), resolve_team(clean(home))
    ac, hc = (ar.canonical_name if ar else away), (hr.canonical_name if hr else home)
    r1, r2 = resolve_team(clean(p1)), resolve_team(clean(p2))
    c1, c2 = (r1.canonical_name if r1 else p1), (r2.canonical_name if r2 else p2)
    a1 = c1 == ac or _team_matches(away, p1)
    h2 = c2 == hc or _team_matches(home, p2)
    h1 = c1 == hc or _team_matches(home, p1)
    a2 = c2 == ac or _team_matches(away, p2)
    if a1 and h2 and not (h1 and a2):
        return "away"
    if h1 and a2 and not (a1 and h2):
        return "home"
    return None


def mascot_side(ev, away, home):
    m = ml.get(ev)
    if not m:
        return None
    ld, sd = (x.casefold() for x in m)
    if ld == sd:
        return None
    a, h = away.casefold(), home.casefold()
    if a.endswith(" " + ld) and h.endswith(" " + sd):
        return "away"
    if h.endswith(" " + ld) and a.endswith(" " + sd):
        return "home"
    return None


c = collections.Counter()
bad = []
for r in rows:
    q = byd.get(r.get("market_snapshot_record_id") or "")
    if q is None:
        continue
    p = r["p"]
    away = p.get("canonical_away_team_name") or p.get("away_team") or ""
    home = p.get("canonical_home_team_name") or p.get("home_team") or ""
    mt, sel = r["market_type"], r["selection"]
    side = mascot_side(q["pm_event_id"], away, home) or title_side(q["title"], away, home)
    ask = r["rat_ask"] if r["rat_ask"] is not None else float(r["market_probability"])
    if mt == "total":
        ea, el = (q["la"] if sel == "over" else q["sa"]), q["line"]
    elif side is None:
        c[("no_side", r["status"])] += 1
        continue
    else:
        ea = q["la"] if sel == side else q["sa"]
        el = None if mt == "moneyline" else (q["line"] if sel == side else -q["line"])
    ok_p = abs(ea - ask) <= 0.0025
    ok_l = el is None or (r["line"] is not None and abs(float(r["line"]) - el) < 1e-6)
    c[(r["status"], ok_p and ok_l)] += 1
    if not (ok_p and ok_l):
        bad.append((r["pick_id"], r["status"], r["ledger_tier"], r["model_id"], r["created_at_utc"][:16], away, home,
                    mt, sel, r["line"], "ask", ask, "exp", ea, el, q["title"], q["team"], "voidreason" if p.get("void_reason") else ""))
for k, v in sorted(c.items(), key=str):
    print(v, k)
print(len(bad))
for b in bad:
    print(b)
