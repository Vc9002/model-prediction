"""NCAAF pricing / ledger-row bug hunt (read-only). Writes findings.json + per-check CSVs next to this file."""
from __future__ import annotations

import bisect
import collections
import csv
import json
import pickle
import re
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, "E:/MP/src")
from model_prediction.data_sources.cfb_data import resolve_team  # noqa: E402
from model_prediction.learned_forward import _team_matches  # noqa: E402
from model_prediction.pricing import implied_probability  # noqa: E402
from model_prediction.units import UnitPolicy, edge_scaled_units  # noqa: E402

OUT = "E:/MP/outputs/ncaaf/bug_hunt"
rows = pickle.load(open(f"{OUT}/rows.pkl", "rb"))
Q = pickle.load(open(f"{OUT}/quotes.pkl", "rb"))

con = sqlite3.connect("file:E:/model-prediction-runtime/ledgers/ledgers.db?mode=ro", uri=True)
FEATS = {}
for pid, fp in con.execute("select pick_id, feature_payload_json from ledger_records where sport='ncaaf'"):
    try:
        FEATS[pid] = json.loads(fp or "{}").get("features") or {}
    except Exception:
        FEATS[pid] = {}


def ts(s):
    if s in (None, ""):
        return None
    s = str(s).strip().replace("Z", "+00:00")
    if re.match(r".*T\d\d:\d\d\+00:00$", s):
        s = s.replace("+00:00", ":00+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def fnum(x):
    try:
        if x in (None, ""):
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


SLUG_RE = re.compile(r"^(aec|asc|tsc)-cfb-(.+?)-(\d{4}-\d{2}-\d{2})(?:-(.*))?$")


def slug_kind(slug):
    m = SLUG_RE.match(slug or "")
    if not m:
        return "unparsed"
    rest = m.group(4) or ""
    if m.group(1) == "aec" and rest == "":
        return "full"
    if m.group(1) == "asc" and re.fullmatch(r"(pos|neg)-\d+pt\d+|(pos|neg)-\d+", rest):
        return "full"
    if m.group(1) == "tsc" and re.fullmatch(r"total-\d+pt\d+|total-\d+", rest):
        return "full"
    return "partial:" + rest


SP_TOK = ("-1h-", "-2h-", "-1q-", "-2q-", "-3q-", "-4q-")
TOT_TOK = SP_TOK + ("-tt-", "-tt1h-", "-tt2h-")

# ---------------- quote indexes
by_digest = {q["digest"]: q for q in Q}
by_slug = collections.defaultdict(list)
by_pm_event = collections.defaultdict(list)
for q in Q:
    q["obs_dt"] = ts(q["obs"])
    by_slug[q["slug"]].append(q)
    by_pm_event[q["pm_event_id"]].append(q)
for v in by_slug.values():
    v.sort(key=lambda q: q["obs_dt"] or datetime.min.replace(tzinfo=timezone.utc))
ml_mascots = {}
for q in Q:
    if q["mt"] == "moneyline" and q["ld"] and q["sd"]:
        ml_mascots.setdefault(q["pm_event_id"], (q["ld"], q["sd"], q["title"]))

# filter-direction audit of the code's substring partial filter
filter_audit = collections.Counter()
for slug, lst in by_slug.items():
    mt = lst[0]["mt"]
    kind = slug_kind(slug)
    toks = SP_TOK if mt == "spread" else TOT_TOK if mt == "total" else ()
    excluded = any(t in slug.lower() for t in toks)
    if kind == "full" and excluded:
        filter_audit[("full_wrongly_excluded", mt)] += 1
    if kind.startswith("partial") and not excluded and mt != "moneyline":
        filter_audit[("partial_let_through", mt, kind)] += 1


def split_title(t):
    t = t or ""
    for sep in (" vs. ", " vs ", " @ ", " at "):
        if sep in t:
            return t.split(sep, 1)
    return None


def clean(n):
    return (n or "").replace("(", "").replace(")", "").strip()


def resolver_side(p1, p2, away, home):
    ar, hr = resolve_team(clean(away)), resolve_team(clean(home))
    ac = ar.canonical_name if ar else away
    hc = hr.canonical_name if hr else home
    r1, r2 = resolve_team(clean(p1)), resolve_team(clean(p2))
    c1 = r1.canonical_name if r1 else p1
    c2 = r2.canonical_name if r2 else p2
    a1 = c1 == ac or _team_matches(away, p1)
    h2 = c2 == hc or _team_matches(home, p2)
    h1 = c1 == hc or _team_matches(home, p1)
    a2 = c2 == ac or _team_matches(away, p2)
    if a1 and h2 and not (h1 and a2):
        return "away"
    if h1 and a2 and not (a1 and h2):
        return "home"
    return None


def mascot_side(pm_event_id, away, home):
    """Independent: the ML long description is the first team's mascot; it must end exactly one ESPN name."""
    m = ml_mascots.get(pm_event_id)
    if not m:
        return None, "no_ml_record"
    ld, sd, _ = m
    ldc, sdc = ld.casefold().strip(), sd.casefold().strip()
    if ldc == sdc:
        return None, "same_mascot"
    a, h = away.casefold(), home.casefold()
    la, lh = a.endswith(" " + ldc), h.endswith(" " + ldc)
    sa, sh = a.endswith(" " + sdc), h.endswith(" " + sdc)
    if la and sh and not (lh or sa):
        return "away", "ok"
    if lh and sa and not (la or sh):
        return "home", "ok"
    return None, f"unmatched({ld}/{sd})"


def tier_of(name):
    r = resolve_team(clean(name))
    return r.tier if r else None


policy = UnitPolicy()


def expected_units(r):
    p = r["p"]
    mt = r["market_type"]
    prob = float(r["model_probability"])
    odds = p.get("decision_american_odds") or p.get("american_odds")
    try:
        odds = int(float(odds))
    except (TypeError, ValueError):
        return None
    if mt == "moneyline" or (mt == "total" and r["selection"] == "over"):
        return 1.0
    unc = fnum(p.get("model_uncertainty"))
    elig = edge_scaled_units(prob, unc or 0.05, odds, policy, market_type=mt)
    dyn = elig if (elig > 0 and elig != 1.0) else edge_scaled_units(prob, unc or 0.02, odds, market_type=mt)
    calc_edge = max(0.0, prob - implied_probability(odds))
    if dyn <= 1.0 and calc_edge > 0.01:
        dyn = round(1.0 + min(1.0, calc_edge / 0.08), 2)
    return dyn


LIVE = [r for r in rows if r["status"] not in ("superseded", "removed")]
F = collections.defaultdict(list)  # finding -> list of dicts
stats = collections.Counter()


def add(key, r, **kw):
    F[key].append(
        dict(
            pick_id=r["pick_id"],
            tier=r["ledger_tier"],
            model=r["model_id"],
            status=r["status"],
            created=r["created_at_utc"],
            event_id=r["event_id"],
            start=r["event_start_utc"],
            away=r["p"].get("canonical_away_team_name") or r["p"].get("away_team"),
            home=r["p"].get("canonical_home_team_name") or r["p"].get("home_team"),
            mt=r["market_type"],
            sel=r["selection"],
            line=r["line"],
            prob=r["model_probability"],
            mkt=r["market_probability"],
            edge=r["edge"],
            units=r["units"],
            result=r["result"],
            pnl=r["pnl_units"],
            **kw,
        )
    )


for r in LIVE:
    p = r["p"]
    stats["live"] += 1
    stats[("live_tier_status", r["ledger_tier"], r["status"])] += 1
    created = ts(r["created_at_utc"])
    start = ts(r["event_start_utc"])
    away = p.get("canonical_away_team_name") or p.get("away_team") or ""
    home = p.get("canonical_home_team_name") or p.get("home_team") or ""
    sb = p.get("sportsbook")
    prov = p.get("market_quote_provenance")
    mt = r["market_type"]
    sel = r["selection"]
    archived = r["status"] == "archived" or bool(p.get("void_reason")) or bool(p.get("corrective_action"))
    if archived:
        stats["archived_or_voided_skipped"] += 1
        continue
    # ---- timing
    if created and start and created >= start:
        add("T1_created_after_start", r, minutes=round((created - start).total_seconds() / 60, 1))
    # ---- quote binding
    q = None
    how = None
    rid = r.get("market_snapshot_record_id") or p.get("market_snapshot_record_id")
    if rid:
        q = by_digest.get(rid)
        how = "digest"
        if q is None:
            add("B1_digest_not_in_archive", r, record_id=rid)
    slug = r.get("rat_slug2") or r.get("rat_slug")
    if q is None and slug and sb == "polymarket_us":
        s2 = slug
        if mt == "moneyline" and re.search(r"-(home|away)$", s2):
            s2 = s2.rsplit("-", 1)[0]
        lst = by_slug.get(s2) or []
        cands = [x for x in lst if x["obs_dt"] and created and x["obs_dt"] <= created]
        if cands:
            q = cands[-1]
            how = "slug_latest_before_created"
        else:
            add("B2_slug_unresolvable", r, slug=slug)
    if sb == "polymarket_us":
        stats[("pm_rows_bound", how)] += 1
    else:
        stats[("non_pm_rows", sb, prov)] += 1
    ask_row = r.get("rat_ask")
    if ask_row is None:
        ask_row = fnum(r["market_probability"])
    if q is not None:
        # slug sanity
        kind = slug_kind(q["slug"])
        if kind != "full":
            add("P1_partial_contract_used", r, slug=q["slug"], kind=kind)
        if slug:
            s2 = re.sub(r"-(home|away)$", "", slug) if mt == "moneyline" else slug
            if s2 != q["slug"]:
                add("B3_rationale_slug_differs_from_bound_quote", r, rat_slug=slug, quote_slug=q["slug"])
        qmt = q["mt"]
        if qmt != mt:
            add("P2_market_type_mismatch", r, quote_mt=qmt, slug=q["slug"])
        # identity
        parts = split_title(q["title"])
        rs = ms = None
        mstat = None
        if parts:
            rs = resolver_side(parts[0], parts[1], away, home)
            ms, mstat = mascot_side(q["pm_event_id"], away, home)
            if rs is None:
                add("I1_title_does_not_resolve_to_row_teams", r, title=q["title"], team=q["team"])
            if ms is not None and rs is not None and ms != rs:
                add("I2_resolver_vs_mascot_side_disagree", r, title=q["title"], resolver=rs, mascot=ms,
                    mascots=ml_mascots.get(q["pm_event_id"]))
            if ms is None and mstat != "no_ml_record":
                stats[("mascot_check_unavailable", mstat[:9])] += 1
            if q["team"] and parts and clean(q["team"]) != clean(parts[0]):
                stats["team_label_not_title_first"] += 1
        p1 = ms or rs
        qstart = ts(q["pm_start"])
        if qstart and start and abs((qstart - start).total_seconds()) > 3 * 3600:
            add("I3_quote_event_start_differs", r, quote_start=q["pm_start"], title=q["title"],
                hours=round((qstart - start).total_seconds() / 3600, 1))
        # re-derive price / line
        exp_ask = exp_line = None
        if p1 is not None:
            if mt == "moneyline":
                exp_ask = q["la"] if sel == p1 else q["sa"]
            elif mt == "spread":
                exp_ask = q["la"] if sel == p1 else q["sa"]
                exp_line = q["line"] if sel == p1 else -q["line"]
        if mt == "total":
            if (q["ld"], q["sd"]) != ("Over", "Under"):
                add("P3_total_desc_not_over_under", r, ld=q["ld"], sd=q["sd"])
            exp_ask = q["la"] if sel == "over" else q["sa"] if sel == "under" else None
            exp_line = q["line"]
        if exp_ask is None:
            stats[("price_not_rederivable", mt)] += 1
        else:
            stats[("price_rederived", mt)] += 1
            if ask_row is None or abs(float(exp_ask) - float(ask_row)) > 0.0025:
                other = q["sa"] if exp_ask == q["la"] else q["la"]
                add("P4_price_mismatch", r, row_ask=ask_row, expected=exp_ask, other_side=other,
                    slug=q["slug"], title=q["title"], p1=p1, la=q["la"], sa=q["sa"],
                    side_unverifiable_by_price=abs((q["la"] or 0) - (q["sa"] or 0)) < 0.0025)
            elif abs((q["la"] or 0) - (q["sa"] or 0)) < 0.0025 and mt != "total":
                stats[("price_match_but_side_unverifiable", mt)] += 1
            if fnum(r["market_probability"]) is not None and abs(float(exp_ask) - float(r["market_probability"])) > 0.004:
                add("P5_market_probability_vs_ask_rounding", r, ask=exp_ask)
        if exp_line is not None:
            rl = fnum(r["line"])
            if rl is None or abs(rl - float(exp_line)) > 1e-6:
                add("P6_line_mismatch", r, row_line=r["line"], expected=exp_line, title=q["title"],
                    p1=p1, slug=q["slug"])
            dl = fnum(p.get("decision_line"))
            if dl is not None and rl is not None and abs(dl - rl) > 1e-6:
                add("P7_decision_line_differs_from_line", r, decision_line=dl)
        # quote state / timing
        if q["state"] != "MARKET_STATE_OPEN":
            add("T2_quote_not_open", r, state=q["state"], slug=q["slug"], obs=q["obs"])
        if q["tv"] is not True:
            add("T3_quote_timestamp_invalid", r, obs=q["obs"], slug=q["slug"], how=how)
        if q["obs_dt"] and created and q["obs_dt"] > created:
            add("T4_quote_after_decision", r, obs=q["obs"])
        if q["obs_dt"] and start and q["obs_dt"] >= start:
            add("T5_quote_after_kickoff", r, obs=q["obs"])
        if q["obs_dt"] and created:
            age_h = (created - q["obs_dt"]).total_seconds() / 3600
            stats[("age_bucket", "<1h" if age_h < 1 else "1-6h" if age_h < 6 else "6-12h" if age_h < 12 else ">=12h")] += 1
            if age_h >= 12:
                add("T6_quote_older_than_12h", r, obs=q["obs"], age_h=round(age_h, 1), reason=r["reason_code"])
        lineage_obs = p.get("market_quote_observed_at_utc")
        if lineage_obs and q["obs"] and ts(lineage_obs) != q["obs_dt"]:
            add("B4_lineage_obs_differs_from_quote", r, lineage_obs=lineage_obs, quote_obs=q["obs"])
        # book quality / rung choice
        if q["la"] is not None and q["sa"] is not None:
            over = q["la"] + q["sa"]
            if over > 1.10:
                # was a tighter full-game rung of the same market available at the decision?
                alts = []
                for x in by_pm_event.get(q["pm_event_id"], []):
                    if x["mt"] != q["mt"] or slug_kind(x["slug"]) != "full" or x["la"] is None or x["sa"] is None:
                        continue
                    if x["obs_dt"] is None or created is None or x["obs_dt"] > created:
                        continue
                    if (created - x["obs_dt"]).total_seconds() > 6 * 3600:
                        continue
                    alts.append(x)
                latest = {}
                for x in alts:
                    if x["slug"] not in latest or x["obs_dt"] > latest[x["slug"]]["obs_dt"]:
                        latest[x["slug"]] = x
                best = min(latest.values(), key=lambda x: x["la"] + x["sa"], default=None)
                add("Q1_wide_book_overround_gt_110", r, slug=q["slug"], la=q["la"], lb=q["lb"], sa=q["sa"],
                    sb_=q["sb"], overround=round(over, 3),
                    tighter=None if best is None or best["la"] + best["sa"] > 1.05 else
                    f"{best['slug']} {best['la']}/{best['sa']}")
        if sb == "polymarket_us" and not prov:
            add("B5_pm_row_without_lineage", r, slug=q["slug"], tv=q["tv"], state=q["state"], obs=q["obs"])
    elif sb == "polymarket_us":
        stats["pm_row_unbound"] += 1

    # ---- extremes
    e = fnum(r["edge"])
    mp = fnum(r["model_probability"])
    ap = ask_row
    ext = []
    if e is not None and abs(e) >= 0.15:
        ext.append("abs_edge>=15%")
    if mp is not None and not (0.02 <= mp <= 0.98):
        ext.append("model_p_outside_2-98%")
    if ap is not None and (ap < 0.05 or ap > 0.95):
        ext.append("price<5c_or>95c")
    if ext:
        add("X1_extreme", r, flags="|".join(ext), sportsbook=sb, ask=ap,
            slug=q["slug"] if q else None, la=q["la"] if q else None, sa=q["sa"] if q else None,
            lb=q["lb"] if q else None, sbid=q["sb"] if q else None)
    # ---- edge consistency
    if e is not None and mp is not None and ap is not None and abs((mp - ap) - e) > 0.004:
        add("X2_edge_not_prob_minus_ask", r, ask=ap, recomputed=round(mp - ap, 4))

    # ---- sizing
    eu = expected_units(r)
    if eu is not None and r["units"] is not None and abs(float(r["units"]) - eu) > 0.011:
        add("S1_units_differ_from_current_rule", r, expected=eu, unc=p.get("model_uncertainty"),
            odds_prob=p.get("decision_raw_implied_probability"))
    if r["units"] is not None and float(r["units"]) > 2.0 + 1e-9:
        add("S2_units_above_2U", r)
    if r["units"] is not None and float(r["units"]) <= 0:
        add("S3_units_nonpositive", r)

    # ---- Main gates (structural models)
    if r["ledger_tier"] == "main":
        feats = FEATS.get(r["pick_id"], {})
        gates = []
        if e is not None and not (0.02 - 1e-9 <= e <= 0.08 + 1e-9):
            gates.append(f"edge_outside_2-8%({e:.3f})")
        if mp is not None and mp < 0.5:
            gates.append("model_p<50%")
        if str(feats.get("is_fbs_vs_fcs")) in ("True", "true", "1"):
            gates.append("fbs_vs_fcs")
        ta, th = tier_of(away), tier_of(home)
        if ta == "FCS" and th == "FCS":
            gates.append("fcs_vs_fcs")
        if (ta is None or th is None):
            gates.append(f"unresolved_tier({ta},{th})")
        if mt == "spread" and fnum(r["line"]) is not None and abs(float(r["line"])) > 21.5:
            gates.append("spread_line>21.5")
        if mt == "moneyline" and ap is not None and ap < 0.333333 - 1e-6:
            gates.append("ml_ask<33.3%")
        if mt == "total":
            if str(feats.get("weather_inputs_verified")) not in ("True", "true"):
                gates.append("weather_unverified")
            if mp is not None and mp > 0.62:
                gates.append("total_p>62%")
            if sel == "under" and fnum(r["line"]) is not None and float(r["line"]) >= 56.5:
                gates.append("under_line>=56.5")
            pt = fnum(feats.get("projected_total"))
            if pt is not None and fnum(r["line"]) is not None and abs(pt - float(r["line"])) < 1.5:
                gates.append("projection_gap<1.5")
        if sb != "polymarket_us":
            gates.append(f"not_executable_quote({sb})")
        if gates:
            add("G1_main_gate_violation", r, gates="|".join(gates), sportsbook=sb)

# ---------------- duplicates
grp = collections.defaultdict(list)
for r in LIVE:
    if r["status"] == "archived":
        continue
    grp[(r["model_id"], r["event_id"], r["market_type"], r["ledger_tier"])].append(r)
for k, lst in grp.items():
    if len(lst) > 1:
        for r in lst:
            add("D1_duplicate_live_rows", r, group="/".join(k), n=len(lst))
# same matchup under two event ids
mg = collections.defaultdict(set)
for r in LIVE:
    p = r["p"]
    d = (r["event_start_utc"] or "")[:10]
    mg[(frozenset([p.get("canonical_away_team_name"), p.get("canonical_home_team_name")]), d)].add(r["event_id"])
for k, ids in mg.items():
    if len(ids) > 1:
        stats[("same_matchup_two_event_ids", "/".join(sorted(ids)))] += 1
# one event id with two different matchups / starts
ev = collections.defaultdict(set)
for r in LIVE:
    p = r["p"]
    ev[r["event_id"]].add((p.get("canonical_away_team_name"), p.get("canonical_home_team_name")))
for k, s in ev.items():
    if len(s) > 1:
        stats[("event_id_two_matchups", k, str(sorted(map(str, s))))] += 1

# ---------------- ESPN-priced rows that had a usable PM quote at decision
title_idx = collections.defaultdict(list)
for q in Q:
    if q["obs_dt"] is None:
        continue
    title_idx[q["day"]].append(q)
espn_with_pm = []
for r in LIVE:
    p = r["p"]
    if p.get("sportsbook") == "polymarket_us" or r["status"] == "archived":
        continue
    created = ts(r["created_at_utc"])
    away = p.get("canonical_away_team_name") or ""
    home = p.get("canonical_home_team_name") or ""
    found = None
    days = {r["event_start_utc"][:10]}
    if created:
        days.add(created.strftime("%Y-%m-%d"))
    for d in days:
        for q in title_idx.get(d, []):
            if q["mt"] != r["market_type"] or q["la"] is None or q["sa"] is None:
                continue
            if slug_kind(q["slug"]) != "full":
                continue
            if not (q["obs_dt"] <= created and (created - q["obs_dt"]).total_seconds() < 12 * 3600):
                continue
            parts = split_title(q["title"])
            if not parts:
                continue
            if resolver_side(parts[0], parts[1], away, home) is None:
                # try loose: any title team shares a token-prefix with ESPN names
                continue
            found = q
            break
        if found:
            break
    if found:
        add("M1_espn_priced_but_pm_quote_existed", r, sportsbook=p.get("sportsbook"),
            slug=found["slug"], obs=found["obs"], state=found["state"])

# ---------------- write
summary = {k: len(v) for k, v in sorted(F.items())}
json.dump({"summary": summary, "stats": {str(k): v for k, v in stats.items()},
           "filter_audit": {str(k): v for k, v in filter_audit.items()}},
          open(f"{OUT}/findings_summary.json", "w"), indent=1)
pickle.dump(dict(F), open(f"{OUT}/findings.pkl", "wb"))
for k, v in F.items():
    keys = sorted({kk for d in v for kk in d})
    with open(f"{OUT}/f_{k}.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(v)
print(json.dumps(summary, indent=1))
for k, v in sorted(stats.items(), key=lambda kv: str(kv[0])):
    print(k, v)
print("filter_audit", dict(filter_audit))
