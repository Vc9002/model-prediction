"""Read-only NCAAF settlement / grading / CLV audit.

Inputs: ledgers.db (read-only), ESPN summary cache (fetch_espn.py), Polymarket snapshot archives.
Output: settlement_rows.csv (one line per non-superseded NCAAF row with all checks) and
settlement_findings.json (aggregates). Nothing in the repo or runtime is written.
"""

from __future__ import annotations

import csv
import json
import math
import pathlib
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import timedelta

sys.path.insert(0, "E:/MP/src")
from model_prediction.cli.settle import _extract_market_slug  # noqa: E402
from model_prediction.domain import EASTERN, parse_utc  # noqa: E402
from model_prediction.ncaaf_prekickoff_capture import ncaaf_settlement_slug  # noqa: E402

HERE = pathlib.Path("E:/MP/outputs/ncaaf/bug_hunt")
CACHE = HERE / "espn_summary_cache"
ODDS = pathlib.Path("E:/model-prediction-runtime/odds/ncaaf")
DB = "file:E:/model-prediction-runtime/ledgers/ledgers.db?mode=ro"
PM_BOOKS = {"kalshi", "polymarket", "polymarket_us", "prediction_market"}


def norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split())


def espn_truth(eid: str) -> dict | None:
    p = CACHE / f"{eid}.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    comp = (d.get("header") or {}).get("competitions", [{}])[0]
    out = {
        "status": comp.get("status", {}).get("type", {}).get("name"),
        "completed": comp.get("status", {}).get("type", {}).get("completed"),
        "neutral": comp.get("neutralSite"),
        "date": comp.get("date"),
    }
    for t in comp.get("competitors", []):
        side = t.get("homeAway")
        team = t.get("team", {})
        out[f"{side}_name"] = team.get("displayName")
        out[f"{side}_loc"] = team.get("location")
        out[f"{side}_short"] = team.get("shortDisplayName")
        try:
            out[f"{side}_score"] = int(t.get("score"))
        except (TypeError, ValueError):
            out[f"{side}_score"] = None
        out[f"{side}_periods"] = len(t.get("linescores", []) or [])
    return out


def grade(mt: str, sel: str, line, away: int, home: int) -> str:
    sel = sel.lower()
    if mt == "moneyline":
        m = home - away if sel == "home" else away - home
    elif mt == "spread":
        m = (home - away if sel == "home" else away - home) + float(line)
    else:
        t = away + home
        m = t - float(line) if sel == "over" else float(line) - t
    return "win" if m > 0 else "loss" if m < 0 else "push"


# ---------------------------------------------------------------- snapshot index
_SNAP: dict[str, dict[str, list[dict]]] = {}


def snaps_for(date: str) -> dict[str, list[dict]]:
    if date in _SNAP:
        return _SNAP[date]
    idx: dict[str, list[dict]] = defaultdict(list)
    p = ODDS / date / "polymarket_snapshots.jsonl"
    if p.exists():
        with p.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if item.get("market_slug"):
                    idx[item["market_slug"]].append(item)
    _SNAP[date] = idx
    return idx


def slug_records(slug: str, start_utc: str) -> list[tuple[str, dict]]:
    start = parse_utc(start_utc)
    dates = []
    for d in (
        start.astimezone(EASTERN).date().isoformat(),
        start.date().isoformat(),
        (start - timedelta(days=1)).date().isoformat(),
        (start + timedelta(days=1)).date().isoformat(),
        (start - timedelta(days=2)).date().isoformat(),
    ):
        if d not in dates:
            dates.append(d)
    out = []
    for d in dates:
        for rec in snaps_for(d).get(slug, []):
            out.append((d, rec))
    return out


def selected_side(rec: dict, row: dict) -> tuple[str | None, str]:
    """Independent side resolution: which of long/short is the row's own bet."""
    mt = row["market_type"]
    sel = row["selection"].lower()
    ld = str((rec.get("long") or {}).get("description", ""))
    sd = str((rec.get("short") or {}).get("description", ""))
    if mt == "total":
        if sel == "over" and ld.lower().startswith("over"):
            return "long", "over-word"
        if sel == "under" and sd.lower().startswith("under"):
            return "short", "under-word"
        return None, f"total-words {ld}/{sd}"
    if mt == "spread":
        # The row's line is from its own side's perspective; exactly one side carries that sign.
        try:
            rl = float(row["line"])
            lv, sv = float(ld), float(sd)
        except (TypeError, ValueError):
            return None, "spread-unparsable"
        cands = [s for s, v in (("long", lv), ("short", sv)) if abs(v - rl) < 1e-6]
        anchor = norm(str(rec.get("team") or ""))
        team = norm(row["home_team"] if sel == "home" else row["away_team"])
        other = norm(row["away_team"] if sel == "home" else row["home_team"])
        anchor_is_sel = bool(anchor) and (team.startswith(anchor + " ") or team == anchor)
        anchor_is_other = bool(anchor) and (other.startswith(anchor + " ") or other == anchor)
        by_team = None
        if anchor_is_sel and not anchor_is_other:
            by_team = "long"
        elif anchor_is_other and not anchor_is_sel:
            by_team = "short"
        if len(cands) == 1 and by_team == cands[0]:
            return cands[0], "line-sign+anchor"
        if len(cands) == 1 and by_team is None:
            return cands[0], "line-sign-only"
        return None, f"spread-conflict cands={cands} by_team={by_team} anchor={anchor!r}"
    # moneyline: descriptions are nicknames or names
    team_full = norm(row["home_team"] if sel == "home" else row["away_team"])
    other_full = norm(row["away_team"] if sel == "home" else row["home_team"])
    lnorm, snorm = norm(ld), norm(sd)

    def hit(full: str, desc: str) -> bool:
        return bool(desc) and (full == desc or full.endswith(" " + desc) or full.startswith(desc + " "))

    l_sel, s_sel = hit(team_full, lnorm), hit(team_full, snorm)
    l_oth, s_oth = hit(other_full, lnorm), hit(other_full, snorm)
    if l_sel and not s_sel and not l_oth:
        return "long", "ml-name"
    if s_sel and not l_sel and not s_oth:
        return "short", "ml-name"
    anchor = norm(str(rec.get("team") or ""))
    if anchor:
        a_sel = team_full.startswith(anchor + " ") or team_full == anchor
        a_oth = other_full.startswith(anchor + " ") or other_full == anchor
        if a_sel and not a_oth:
            return "long", "ml-anchor"
        if a_oth and not a_sel:
            return "short", "ml-anchor"
    return None, f"ml-ambiguous {ld}/{sd} anchor={anchor!r}"


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def main() -> None:
    con = sqlite3.connect(DB, uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "select * from ledger_records where sport='ncaaf' and status in ('settled','archived','open','voided')"
    ).fetchall()
    out_rows = []
    agg = Counter()
    for r in rows:
        p = json.loads(r["decision_payload_json"] or "{}")
        row = {**p, **{k: r[k] for k in r.keys() if k not in ("decision_payload_json", "feature_payload_json")}}
        row["market_type"] = str(row["market_type"])
        rec = {
            "pick_id": r["pick_id"],
            "tier": r["ledger_tier"],
            "status": r["status"],
            "event_id": r["event_id"],
            "start": r["event_start_utc"],
            "model": r["model_id"],
            "mt": row["market_type"],
            "sel": row["selection"],
            "line": row.get("decision_line") or row.get("line"),
            "units": r["units"],
            "book": p.get("sportsbook"),
            "away": p.get("away_team"),
            "home": p.get("home_team"),
            "ledger_away_score": p.get("away_score"),
            "ledger_home_score": p.get("home_score"),
            "result": r["result"],
            "pnl": r["pnl_units"],
            "void_reason": p.get("void_reason"),
            "settled_at": r["settled_at_utc"],
            "flags": [],
        }
        flags = rec["flags"]
        t = espn_truth(str(r["event_id"]))
        if t is None:
            flags.append("NO_ESPN_TRUTH")
        else:
            rec.update(
                espn_status=t["status"],
                espn_away=t.get("away_name"),
                espn_home=t.get("home_name"),
                espn_away_score=t.get("away_score"),
                espn_home_score=t.get("home_score"),
                espn_neutral=t["neutral"],
                espn_date=t["date"],
                espn_periods=max(t.get("away_periods") or 0, t.get("home_periods") or 0),
            )
            la, lh = norm(p.get("away_team", "")), norm(p.get("home_team", ""))
            ea, eh = norm(t.get("away_name") or ""), norm(t.get("home_name") or "")
            if la == ea and lh == eh:
                orient = "same"
            elif la == eh and lh == ea:
                orient = "swapped"
            else:
                # fall back to location prefix
                eal, ehl = norm(t.get("away_loc") or ""), norm(t.get("home_loc") or "")
                if la.startswith(eal) and lh.startswith(ehl):
                    orient = "same~"
                elif la.startswith(ehl) and lh.startswith(eal):
                    orient = "swapped~"
                else:
                    orient = "names_differ"
            rec["orient"] = orient
            if orient.startswith("swapped"):
                flags.append("HOME_AWAY_SWAPPED_VS_ESPN")
            if orient == "names_differ":
                flags.append("TEAM_NAMES_DIFFER_FROM_ESPN")
            # kickoff drift
            try:
                drift = (parse_utc(t["date"]) - parse_utc(r["event_start_utc"])).total_seconds() / 60
                rec["kickoff_drift_min"] = round(drift)
                if abs(drift) > 30:
                    flags.append("KICKOFF_DRIFT>30m")
            except (TypeError, ValueError):
                pass
            if r["status"] in ("settled", "archived") and t["completed"] and t.get("away_score") is not None:
                ta, th = t["away_score"], t["home_score"]
                if orient.startswith("swapped"):
                    ta, th = th, ta
                rec["truth_away"], rec["truth_home"] = ta, th
                is_void = bool(p.get("void_reason"))
                if is_void:
                    flags.append("VOIDED_BUT_GAME_COMPLETED")
                else:
                    want = grade(row["market_type"], row["selection"], rec["line"], ta, th)
                    rec["regrade"] = want
                    if want != r["result"]:
                        flags.append("GRADE_MISMATCH")
                    try:
                        if (int(p.get("away_score")), int(p.get("home_score"))) != (ta, th):
                            flags.append("LEDGER_SCORES_DIFFER")
                    except (TypeError, ValueError):
                        flags.append("LEDGER_SCORES_MISSING")
                    if (
                        row["market_type"] == "spread"
                        and rec["line"] not in (None, "")
                        and float(rec["line"]) == int(float(rec["line"]))
                    ):
                        flags.append("WHOLE_LINE_SPREAD")
            elif r["status"] in ("settled", "archived") and not t["completed"]:
                if not p.get("void_reason"):
                    flags.append("SETTLED_BUT_ESPN_NOT_COMPLETED")
            if r["status"] == "open":
                try:
                    from datetime import UTC, datetime

                    age_h = (datetime.now(UTC) - parse_utc(r["event_start_utc"])).total_seconds() / 3600
                    rec["open_age_h"] = round(age_h, 1)
                    if age_h > 36:
                        flags.append("OPEN_PAST_KICKOFF+36h")
                    elif age_h > 6 and t["completed"]:
                        flags.append("OPEN_GAME_FINAL>6h")
                except ValueError:
                    pass

        # ------------------------------------------------------------ PnL
        if r["status"] in ("settled", "archived"):
            units = f(r["units"]) or 0.0
            entry = f(p.get("market_probability_at_decision")) or f(p.get("decision_raw_implied_probability")) or f(
                p.get("market_implied_probability")
            )
            dec = f(p.get("decision_decimal_odds")) or f(p.get("decimal_odds"))
            rec["entry"] = entry
            rec["dec"] = dec
            pm = str(p.get("sportsbook") or "").lower() in PM_BOOKS
            res = r["result"]
            if res == "win":
                exp = units * (1 / entry - 1) if pm else units * (dec - 1)
                exp_entry_based = units * (1 / entry - 1) if entry else None
            elif res == "loss":
                exp = exp_entry_based = -units
            else:
                exp = exp_entry_based = 0.0
            rec["pnl_expected"] = round(exp, 4)
            pnl = f(r["pnl_units"])
            if pnl is None or abs(pnl - exp) > 6e-4:
                flags.append("PNL_FORMULA_MISMATCH")
            if exp_entry_based is not None and abs(exp_entry_based - exp) > 0.01:
                flags.append("PNL_NOT_FROM_ENTRY_PROB")
            if entry and dec and abs(1 / entry - dec) > 0.01:
                flags.append("DEC_ODDS!=1/ENTRY")

        # ------------------------------------------------------------ CLV
        if r["status"] in ("settled", "archived") and not p.get("void_reason"):
            close = f(p.get("closing_implied_probability"))
            rec["close"] = close
            rec["clv"] = p.get("probability_clv")
            slug0 = _extract_market_slug(str(p.get("rationale", "")), p)
            slug = ncaaf_settlement_slug(slug0, str(p.get("rationale", ""))) if slug0 else None
            rec["slug"] = slug
            dq = p.get("market_quote_observed_at_utc") or p.get("observed_at_utc")
            pm = str(p.get("sportsbook") or "").lower() in PM_BOOKS
            espn_start = parse_utc(t["date"]) if t and t.get("date") else None
            if slug:
                recs = slug_records(slug, r["event_start_utc"])
                valid = []
                for d, s in recs:
                    obs = parse_utc(s["observed_at_utc"])
                    bounds = [parse_utc(r["event_start_utc"])]
                    if s.get("event_start_utc"):
                        bounds.append(parse_utc(s["event_start_utc"]))
                    if espn_start:
                        bounds.append(espn_start)
                    if obs >= min(bounds):
                        continue
                    if dq and obs <= parse_utc(dq):
                        continue
                    if s.get("market_state") not in (None, "MARKET_STATE_OPEN"):
                        continue
                    valid.append((obs, d, s))
                rec["n_valid_close_quotes"] = len(valid)
                if valid:
                    obs, d, s = max(valid, key=lambda x: x[0])
                    side, how = selected_side(s, row)
                    rec["indep_close_side"] = side
                    rec["indep_close_how"] = how
                    rec["indep_close_obs"] = s["observed_at_utc"]
                    rec["indep_close_line"] = s.get("line")
                    if side:
                        ask = (s.get(side) or {}).get("ask")
                        rec["indep_close"] = ask
                        if (
                            row["market_type"] in ("spread", "total")
                            and s.get("line") is not None
                            and rec["line"] not in (None, "")
                            and abs(abs(float(s["line"])) - abs(float(rec["line"]))) > 1e-9
                        ):
                            rec["indep_close"] = None
                            rec["indep_close_how"] += ";line-mismatch"
                # where did the recorded close come from?
                if close is not None:
                    matches = []
                    for d, s in recs:
                        side, how = selected_side(s, row)
                        for sname in ("long", "short"):
                            ask = (s.get(sname) or {}).get("ask")
                            if ask is not None and abs(float(ask) - close) < 1e-6:
                                matches.append((s["observed_at_utc"], sname, side, s.get("market_state")))
                    ok = [
                        m
                        for m in matches
                        if m[1] == m[2]
                        and (not dq or parse_utc(m[0]) > parse_utc(dq))
                        and (espn_start is None or parse_utc(m[0]) < espn_start)
                    ]
                    rec["close_source_matches"] = len(matches)
                    if not matches:
                        flags.append("CLOSE_NOT_FOUND_IN_ARCHIVE")
                    elif not ok:
                        wrong_side = [m for m in matches if m[2] is not None and m[1] != m[2]]
                        if wrong_side:
                            flags.append("CLOSE_FROM_WRONG_SIDE")
                        else:
                            flags.append("CLOSE_NOT_VALID_TIME_OR_SIDE")
                    if rec.get("indep_close") is not None and abs(float(rec["indep_close"]) - close) > 1e-6:
                        flags.append("CLOSE_DIFFERS_FROM_INDEPENDENT")
                    if entry is not None and abs(close - entry) < 1e-9:
                        agg["close_equals_entry"] += 1
                        flags.append("CLOSE_EQUALS_DECISION_PRICE")
                    clv = f(p.get("probability_clv"))
                    if clv is None or entry is None or abs(clv - (close - entry)) > 2e-6:
                        flags.append("CLV_ARITHMETIC")
                else:
                    if rec.get("indep_close") is not None and pm:
                        flags.append("CLOSE_MISSING_BUT_AVAILABLE")
            else:
                rec["n_valid_close_quotes"] = None
                if pm:
                    flags.append("NO_SLUG_FOR_CLOSE")
            if close is None:
                agg[f"no_close::{p.get('sportsbook')}::{row['market_type']}"] += 1
            else:
                agg[f"close::{p.get('sportsbook')}::{row['market_type']}"] += 1

        for fl in flags:
            agg[f"flag::{fl}"] += 1
        rec["flags"] = ";".join(flags)
        out_rows.append(rec)

    keys = sorted({k for rr in out_rows for k in rr})
    front = ["pick_id", "tier", "status", "flags", "mt", "sel", "line", "away", "home", "result", "regrade"]
    keys = front + [k for k in keys if k not in front]
    with (HERE / "settlement_rows.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        for rr in out_rows:
            w.writerow(rr)
    (HERE / "settlement_findings.json").write_text(json.dumps(dict(sorted(agg.items())), indent=1))
    print(json.dumps(dict(sorted(agg.items())), indent=1))
    print("rows", len(out_rows), Counter(r["status"] for r in out_rows))


if __name__ == "__main__":
    main()
