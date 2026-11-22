"""Compact index of every archived NCAAF Polymarket quote, keyed by the lineage digest the serving code writes."""
import glob
import hashlib
import json
import os
import pickle

root = "E:/model-prediction-runtime/odds/ncaaf"
recs = []  # compact dicts
for path in sorted(glob.glob(f"{root}/*/polymarket_snapshots.jsonl")):
    day = os.path.basename(os.path.dirname(path))
    for i, line in enumerate(open(path, encoding="utf-8")):
        if not line.strip():
            continue
        r = json.loads(line)
        payload = {k: v for k, v in r.items() if not str(k).startswith("_")}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        lo, so = r.get("long") or {}, r.get("short") or {}
        recs.append(
            dict(
                digest=digest,
                day=day,
                lineno=i + 1,
                pm_event_id=str(r.get("event_id") or ""),
                event_slug=r.get("event_slug"),
                title=r.get("event_title"),
                pm_start=r.get("event_start_utc"),
                team=r.get("team"),
                line=r.get("line"),
                mt=r.get("market_type"),
                slug=r.get("market_slug"),
                state=r.get("market_state"),
                obs=r.get("observed_at_utc"),
                tv=r.get("timestamp_valid"),
                la=lo.get("ask"),
                lb=lo.get("bid"),
                ld=lo.get("description"),
                lsz=lo.get("ask_size"),
                sa=so.get("ask"),
                sb=so.get("bid"),
                sd=so.get("description"),
                ssz=so.get("ask_size"),
                cache_age=r.get("quote_cache_age_seconds"),
                src=r.get("reconstruction_status_source"),
            )
        )
pickle.dump(recs, open("E:/MP/outputs/ncaaf/bug_hunt/quotes.pkl", "wb"))
print(len(recs), len({r["digest"] for r in recs}))
