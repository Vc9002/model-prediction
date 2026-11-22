"""Read-only loader for NBA ledger rows + Polymarket snapshot history.

Outputs nba_rows.pkl (all NBA ledger rows, parsed) and snaps.pkl (snapshot history).
All DBs opened read-only.
"""

import glob
import json
import re
from datetime import UTC, datetime

import pandas as pd

LEDGER = "file:E:/model-prediction-runtime/ledgers/ledgers.db?mode=ro"
SNAP_GLOB = "E:/model-prediction-runtime/odds/nba/*/polymarket_snapshots.jsonl"
OUT = __file__.rsplit("\\", 1)[0] if "\\" in __file__ else "."

PARSE_FAIL = []


def ts(s):
    if s in (None, ""):
        return None
    try:
        d = datetime.fromisoformat(str(s).strip())
    except ValueError:
        PARSE_FAIL.append(s)
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.astimezone(UTC)


def fnum(x):
    try:
        if x in (None, ""):
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def load_ledger():
    import sqlite3

    c = sqlite3.connect(LEDGER, uri=True)
    c.row_factory = sqlite3.Row
    out = []
    for r in c.execute("select * from ledger_records where sport='nba'"):
        d = json.loads(r["decision_payload_json"] or "{}")
        f = json.loads(r["feature_payload_json"] or "{}")
        feats = f.get("features") or {}
        mis = f.get("model_input_snapshot") or {}
        inf = mis.get("inference_inputs") or {}
        rat = d.get("rationale", "") or ""
        m = re.search(r"\(((?:asc|tsc|aec)-[a-z0-9\-]+)\)", rat)
        out.append(
            dict(
                pick_id=r["pick_id"],
                tier=r["ledger_tier"],
                model=r["model_id"],
                mt=r["market_type"],
                event_id=r["event_id"],
                start=ts(r["event_start_utc"]),
                created=ts(r["created_at_utc"]),
                quote_obs=ts(d.get("market_quote_observed_at_utc")),
                selection=r["selection"],
                line=r["line"],
                p_model=r["model_probability"],
                p_mkt=r["market_probability"],
                edge=r["edge"],
                units=r["units"],
                pnl=r["pnl_units"],
                status=r["status"],
                result=r["result"],
                reason=r["reason_code"],
                record_type=d.get("record_type"),
                provenance=d.get("market_quote_provenance"),
                qsource=d.get("market_quote_source"),
                reconstructed=d.get("market_quote_reconstructed"),
                no_vig=fnum(d.get("decision_no_vig_probability")),
                close_p_ledger=fnum(d.get("closing_implied_probability")),
                clv_ledger=fnum(d.get("probability_clv")),
                home=d.get("canonical_home_team_name") or d.get("home_team"),
                away=d.get("canonical_away_team_name") or d.get("away_team"),
                hs=fnum(d.get("home_score")),
                as_=fnum(d.get("away_score")),
                slug=m.group(1) if m else None,
                rationale=rat,
                exp_margin=fnum(feats.get("expected_margin")),
                exp_total=fnum(feats.get("expected_total")),
                avail_gap=fnum(feats.get("availability_points_gap")),
                avail_loss=fnum(feats.get("availability_total_loss")),
                rest_diff=fnum(feats.get("rest_diff")),
                inf_mean=fnum(inf.get("mean")),
                inf_sd=fnum(inf.get("sd")),
                inf_line=fnum(inf.get("line")),
                base_home_p=fnum(mis.get("base_home_probability")),
                served_home_p=fnum(mis.get("served_home_probability")),
                elo_p=fnum(feats.get("elo_probability")),
            )
        )
    return pd.DataFrame(out)


def load_snaps():
    rows = []
    for p in glob.glob(SNAP_GLOB):
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                try:
                    j = json.loads(line)
                except json.JSONDecodeError:
                    continue
                lg, sh = j.get("long") or {}, j.get("short") or {}
                rows.append(
                    dict(
                        slug=j.get("market_slug"),
                        event_slug=j.get("event_slug"),
                        mt=j.get("market_type"),
                        line=j.get("line"),
                        team=j.get("team"),
                        obs=ts(j.get("observed_at_utc")),
                        start=ts(j.get("event_start_utc")),
                        l_ask=fnum(lg.get("ask")),
                        l_bid=fnum(lg.get("bid")),
                        l_desc=lg.get("description"),
                        s_ask=fnum(sh.get("ask")),
                        s_bid=fnum(sh.get("bid")),
                        s_desc=sh.get("description"),
                        state=j.get("market_state"),
                        recon=j.get("reconstructed"),
                    )
                )
    s = pd.DataFrame(rows).drop_duplicates(subset=["slug", "obs"])
    return s


if __name__ == "__main__":
    df = load_ledger()
    df.to_pickle(OUT + "/nba_rows.pkl")
    s = load_snaps()
    s.to_pickle(OUT + "/snaps.pkl")
    print("ledger rows", len(df), "snap rows", len(s), "parse failures", len(PARSE_FAIL), PARSE_FAIL[:5])
    print(df.status.value_counts().to_dict())
    print("null start", df.start.isna().sum(), "null created", df.created.isna().sum())
